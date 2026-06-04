"""CIViC source — Clinical Interpretation of Variants in Cancer (civicdb.org).

Curated, expert-moderated **variant → disease → therapy** clinical evidence. Each evidence item
carries an evidence LEVEL (A = validated … E = inferential), a clinical direction + significance
(e.g. SUPPORTS / RESISTANCE / SENSITIVITY), an evidence type (PREDICTIVE / DIAGNOSTIC / …), and a
source citation that is usually a **PubMed id** — the join key that links CIViC into our PubMed
corpus.

The read-only GraphQL API is open (no key needed). `MG_CIVIC_API_KEY`, if set, is sent as a bearer
token (raises limits / enables writes) but is not required for ingestion.

Design: each evidence item is mapped to a `Paper`-shaped record so it flows through the SAME ingest
pipeline as literature (chunk → embed → BM25 → graph → grounding). The structured fields (gene,
variant, disease, therapy, evidence level, direction) are carried in `Paper.raw` for the precise
biomarker→therapy matching layer. See ADR-0017 / docs/CIVIC_INTEGRATION_PLAN.md.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from medground.config import CONFIG
from medground.models import Paper

GRAPHQL_URL = "https://civicdb.org/api/graphql"

_EVIDENCE_QUERY = """
query($after: String, $size: Int!) {
  evidenceItems(first: $size, after: $after) {
    totalCount
    pageInfo { endCursor hasNextPage }
    nodes {
      id
      evidenceLevel
      evidenceType
      evidenceDirection
      significance
      status
      description
      molecularProfile { name }
      disease { name doid }
      therapies { name ncitId }
      source { citationId sourceType publicationYear }
    }
  }
}
"""

_RETRY = AsyncRetrying(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    retry=retry_if_exception_type((httpx.HTTPError,)),
    reraise=True,
)


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if CONFIG.civic_api_key:
        h["Authorization"] = f"Bearer {CONFIG.civic_api_key}"
    return h


def _gene_from_profile(mp_name: str) -> str:
    """Derive the gene symbol from a molecular-profile name.

    Pragmatic v1: the MP name reliably leads with the gene — "EGFR T790M" → EGFR, "KRAS G12C" →
    KRAS, "NPM1 EXON 11 MUTATION" → NPM1. Fusions ("BCR::ABL1") are kept whole. Canonical
    gene linkage (CIViC features) is deferred — see the entity-resolution note in ADR-0017.
    """
    return mp_name.split()[0] if mp_name else ""


def normalize(node: dict[str, Any]) -> dict[str, Any]:
    """CIViC GraphQL evidence node → flat, stable dict used by `to_paper` and the matching table."""
    mp = (node.get("molecularProfile") or {}).get("name", "") or ""
    disease = node.get("disease") or {}
    source = node.get("source") or {}
    therapies = [t.get("name", "") for t in (node.get("therapies") or []) if t.get("name")]
    pmid = source.get("citationId") if source.get("sourceType") == "PUBMED" else None
    return {
        "eid": node["id"],
        "variant": mp,
        "gene": _gene_from_profile(mp),
        "disease": disease.get("name", "") or "",
        "doid": disease.get("doid"),
        "therapies": therapies,
        "ncit_ids": [t.get("ncitId") for t in (node.get("therapies") or []) if t.get("ncitId")],
        "evidence_level": node.get("evidenceLevel"),
        "evidence_type": node.get("evidenceType"),
        "direction": node.get("evidenceDirection"),
        "significance": node.get("significance"),
        "status": node.get("status"),
        "description": node.get("description") or "",
        "pmid": pmid,
        "year": source.get("publicationYear"),
        "url": f"https://civicdb.org/evidence/{node['id']}",
    }


_SIGNIFICANCE = {
    "SENSITIVITYRESPONSE": "sensitivity/response",
    "RESISTANCE": "resistance",
    "ADVERSE_RESPONSE": "adverse response",
    "REDUCED_SENSITIVITY": "reduced sensitivity",
    "BETTER_OUTCOME": "better outcome",
    "POOR_OUTCOME": "poor outcome",
}


def _pretty_significance(s: str | None) -> str:
    return _SIGNIFICANCE.get(s or "", (s or "").replace("_", " ").lower())


def _title(ev: dict[str, Any]) -> str:
    level = ev.get("evidence_level") or "?"
    if ev["therapies"] and ev.get("evidence_type") == "PREDICTIVE":
        sig = _pretty_significance(ev.get("significance"))
        return (
            f"CIViC EID{ev['eid']} — {ev['variant']} → {sig} to "
            f"{', '.join(ev['therapies'])} in {ev['disease']} (Level {level})"
        )
    etype = (ev.get("evidence_type") or "evidence").lower()
    return f"CIViC EID{ev['eid']} — {ev['variant']} in {ev['disease']} ({etype}, Level {level})"


def to_paper(ev: dict[str, Any]) -> Paper:
    """Map a normalized CIViC evidence item to a `Paper` so it reuses the literature pipeline.

    `mesh_terms` carries the entities (gene / variant / disease / therapies) so they become graph
    concepts — linking CIViC items to each other and, by name, to PubMed MeSH concepts. `pmid` is
    the link to the underlying paper. The full structured record rides in `raw`.
    """
    concepts = [c for c in (ev["gene"], ev["variant"], ev["disease"], *ev["therapies"]) if c]
    keywords = [x for x in (ev.get("evidence_level"), ev.get("evidence_type"), ev.get("significance")) if x]
    return Paper(
        id=f"civic:eid{ev['eid']}",
        source="civic",
        native_id=str(ev["eid"]),
        title=_title(ev),
        abstract=ev["description"],
        year=ev.get("year"),
        pmid=ev.get("pmid"),
        url=ev["url"],
        mesh_terms=concepts,
        keywords=keywords,
        raw=ev,
    )


async def fetch(*, max_results: int | None = None, page_size: int = 200) -> AsyncIterator[dict[str, Any]]:
    """Yield normalized CIViC evidence dicts, paginating the GraphQL API (Relay cursor).

    `max_results` caps the number yielded (None = the whole knowledgebase, ~11k items).
    """
    timeout = httpx.Timeout(CONFIG.http_timeout_s)
    after: str | None = None
    yielded = 0
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        while True:
            size = page_size if max_results is None else min(page_size, max_results - yielded)
            if size <= 0:
                return
            variables = {"after": after, "size": size}
            async for attempt in _RETRY:
                with attempt:
                    r = await client.post(
                        GRAPHQL_URL,
                        json={"query": _EVIDENCE_QUERY, "variables": variables},
                        headers=_headers(),
                    )
                    r.raise_for_status()
                    body = r.json()
            if "errors" in body:
                raise RuntimeError(f"CIViC GraphQL errors: {body['errors']}")
            block = body["data"]["evidenceItems"]
            for node in block["nodes"]:
                yield normalize(node)
                yielded += 1
                if max_results is not None and yielded >= max_results:
                    return
            page = block["pageInfo"]
            if not page.get("hasNextPage"):
                return
            after = page.get("endCursor")
