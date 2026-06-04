"""Groundedness verification — the architectural enforcement behind ADR-0007.

The contract is "no claim without a source." This module makes that *checkable* instead of a
polite request buried in a prompt. Given an agent's drafted claims — each with the paper_ids it
cites — it verifies, deterministically and with no LLM call, that:

  1. every claim cites at least one paper_id          → otherwise `uncited` (contract violation)
  2. every cited paper_id exists in the local corpus  → otherwise `phantom_citation` (a fabricated
                                                         or mistyped id; the citation is not real)
  3. [optional] every cited id was inside the evidence envelope the agent was handed
                                                       → otherwise `off_envelope` (the agent cited
                                                         something it did not actually retrieve)

What this deliberately does NOT check: semantic entailment — whether the cited paper actually
*supports* the claim. That judgement is the agent's (Opus's) job. This module guarantees the
floor: the provenance is real and reachable. An answer that fails here is not grounded at all,
regardless of how convincing the prose is.

Intended loop:
    pack = summarize_evidence(question)            # retrieve; note pack["allowed_paper_ids"]
    draft = <Opus writes claims, each citing paper_ids from the pack>
    report = check_grounding(draft, pack["allowed_paper_ids"])
    if not report["grounded"]: <Opus repairs the flagged claims and re-checks>
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from medground.store.docs import DocStore

# Status values, worst-first so a caller can treat "anything but grounded" as a failure.
STATUS_UNCITED = "uncited"
STATUS_PHANTOM = "phantom_citation"
STATUS_OFF_ENVELOPE = "off_envelope"
STATUS_GROUNDED = "grounded"


def _normalize_claim(raw: Any) -> dict[str, Any]:
    """Accept a few shapes for a claim and return {text, citations:[...]}.

    Tolerated inputs:
      - {"text": "...", "citations": ["pubmed:1", ...]}
      - {"text": "...", "paper_ids": [...]}          (alias)
      - {"claim": "...", "citations": [...]}          (alias)
      - "a bare string"                                (→ no citations)
    """
    if isinstance(raw, str):
        return {"text": raw.strip(), "citations": []}
    if not isinstance(raw, dict):
        return {"text": str(raw), "citations": []}
    text = str(raw.get("text") or raw.get("claim") or "").strip()
    cites = raw.get("citations")
    if cites is None:
        cites = raw.get("paper_ids") or raw.get("paper_id") or []
    if isinstance(cites, str):
        cites = [cites]
    citations = [str(c).strip() for c in cites if str(c).strip()]
    return {"text": text, "citations": citations}


def verify_claims(
    claims: Iterable[Any],
    *,
    docs: DocStore | None = None,
    allowed_paper_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Verify provenance of every claim. Pure function over the corpus — no LLM, no network.

    Returns:
      {
        "grounded": bool,                  # True iff every claim is `grounded`
        "grounded_ratio": float,           # fraction of claims fully grounded
        "n_claims": int,
        "claims": [{text, citations, status, problems:[...]}],
        "violations": [ ...subset of claims that are not grounded... ],
      }
    """
    docs = docs or DocStore()
    norm = [_normalize_claim(c) for c in claims]
    norm = [c for c in norm if c["text"]]  # drop empty lines

    # One round trip resolves which cited ids are real.
    all_cited = sorted({cid for c in norm for cid in c["citations"]})
    existing = docs.known_paper_ids(all_cited) if all_cited else set()
    allowed = {str(a).strip() for a in allowed_paper_ids} if allowed_paper_ids else None

    results: list[dict[str, Any]] = []
    for c in norm:
        problems: list[str] = []
        citations = c["citations"]
        if not citations:
            status = STATUS_UNCITED
            problems.append("no paper_id cited")
        else:
            phantom = [cid for cid in citations if cid not in existing]
            off_env = (
                [cid for cid in citations if cid in existing and cid not in allowed]
                if allowed is not None
                else []
            )
            if phantom:
                status = STATUS_PHANTOM
                problems.append(f"cited id(s) not in corpus: {', '.join(phantom)}")
            elif off_env:
                status = STATUS_OFF_ENVELOPE
                problems.append(
                    f"cited id(s) outside the retrieved evidence: {', '.join(off_env)}"
                )
            else:
                status = STATUS_GROUNDED
        results.append(
            {"text": c["text"], "citations": citations, "status": status, "problems": problems}
        )

    n = len(results)
    n_grounded = sum(1 for r in results if r["status"] == STATUS_GROUNDED)
    violations = [r for r in results if r["status"] != STATUS_GROUNDED]
    return {
        "grounded": n > 0 and not violations,
        "grounded_ratio": round(n_grounded / n, 3) if n else 0.0,
        "n_claims": n,
        "claims": results,
        "violations": violations,
        "checked_against": "evidence envelope + corpus" if allowed is not None else "corpus",
    }
