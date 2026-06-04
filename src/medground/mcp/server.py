"""MCP server (stdio) exposing the medground toolset.

Retrieve / ground:
  - search_papers(query, k)        → hybrid hits (vector + BM25 + graph) with citation metadata
  - summarize_evidence(question)   → multi-facet evidence pack + allowed_paper_ids (no LLM)
  - evaluate_plan(plan_text)       → per-claim evidence pack + allowed_paper_ids (no LLM)
  - check_grounding(claims, allowed_paper_ids) → deterministic provenance gate before answering
  - get_paper / get_paper_chunks   → expand context for a paper

Graph (KuzuDB / MeSH):
  - find_concepts / graph_neighbors / concept_papers

Corpus management:
  - ingest_pubmed(query, max)      → pull fresh papers from PubMed and persist
  - add_watch / list_watches / remove_watch / run_watch → track new research over time
  - corpus_stats()                 → counts; sanity check before reasoning over the corpus

Design notes:
  - Tools return plain dicts / lists (auto-serialized by the MCP SDK). Pydantic models are
    converted via `.model_dump(mode='json')` so date fields land as ISO strings.
  - Long operations (ingest) run synchronously *from the client's view* but use asyncio inside
    so we don't block on I/O.
  - Errors are reported as tool exceptions; the SDK translates them into MCP error responses
    with a structured message — clients can show the failure without crashing the session.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import logging
import sys
from collections.abc import AsyncIterator
from typing import Any

from mcp.server.fastmcp import FastMCP

from medground import runtime
from medground.config import CONFIG
from medground.ingest.pipeline import ingest_pubmed as _ingest_pubmed
from medground.retrieve.grounding import verify_claims as _verify_claims
from medground.retrieve.hybrid import search as _search
from medground.retrieve.hybrid import search_many as _search_many
from medground.runtime import get_docs, get_graph, get_lexical, get_vectors, locked
from medground.watch import service as _watch

log = logging.getLogger("medground.mcp")


def _run_coro(coro: Any) -> Any:
    """Run an async coroutine from a synchronous MCP tool body.

    FastMCP executes sync tool callables while its own event loop is already
    running, so a bare ``asyncio.run()`` raises "cannot be called from a running
    event loop". When a loop is active we run the coroutine to completion on a
    dedicated worker thread (with its own loop); otherwise (e.g. under the CLI)
    we fall back to ``asyncio.run()`` directly.
    """
    import concurrent.futures

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# Process-scoped, NOT per-session. Under streamable-HTTP FastMCP enters the lifespan once per
# client session, so anything that must happen once per *process* (the single in-server watch
# loop; the WAL-flush CHECKPOINT) is kept here behind start-once guards rather than in the
# per-session lifespan teardown. See ADR-0018.
_watch_task: asyncio.Task | None = None
_checkpoint_registered = False


def _checkpoint_on_exit() -> None:
    """Best-effort WAL flush at process exit (an unreplayable WAL can make the DB unopenable).

    Registered via `atexit` so it fires once per process regardless of transport — the per-session
    lifespan cannot guarantee this under streamable-HTTP. Flushes only a connection that is ALREADY
    open: never re-open a (possibly multi-GB) store just to checkpoint an idle shutdown. See
    ADR-0014 / ADR-0018.
    """
    with contextlib.suppress(Exception):
        docs = runtime._docs
        if docs is not None and docs._conn is not None:
            docs._conn.execute("CHECKPOINT")


def _register_checkpoint_once() -> None:
    global _checkpoint_registered
    if not _checkpoint_registered:
        atexit.register(_checkpoint_on_exit)
        _checkpoint_registered = True


@contextlib.asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[dict]:
    """Start the in-server watch loop AT MOST ONCE per process (opt-in via MG_WATCH_IN_SERVER).

    Off by default: background ingestion embeds, which costs money. Running it here — rather than a
    separate `medground watch daemon` process — lets a research tracker run alongside Opus without a
    second process fighting for the file lock. FastMCP enters this per client SESSION under
    streamable-HTTP, so the loop is guarded to start a single time and is NOT cancelled on session
    exit (it lives for the process). The CHECKPOINT is process-scoped via `atexit`. See ADR-0018.
    """
    global _watch_task
    _register_checkpoint_once()
    if CONFIG.watch_in_server and _watch_task is None:
        log.info("starting in-server watch loop (tick=%ds)", CONFIG.watch_tick_seconds)
        _watch_task = asyncio.create_task(_watch.daemon(tick_seconds=CONFIG.watch_tick_seconds))
    yield {}


mcp = FastMCP(
    "medground",
    lifespan=_lifespan,
    instructions=(
        "Grounded Graph-RAG over cancer research literature. Retrieval is hybrid: dense vector "
        "+ BM25 lexical + MeSH knowledge graph, fused by Reciprocal Rank Fusion.\n"
        "Workflow:\n"
        "  1. `search_papers` / `summarize_evidence` / `evaluate_plan` to retrieve evidence. "
        "Every hit carries citation metadata; the evidence packs also return `allowed_paper_ids`.\n"
        "  2. Write your answer as discrete claims, each citing paper_id(s) drawn ONLY from the "
        "retrieved hits.\n"
        "  3. Call `check_grounding(claims, allowed_paper_ids)` BEFORE presenting. It deterministically "
        "flags any claim that is uncited, cites a paper not in the corpus, or cites outside the "
        "evidence you retrieved. Repair every flagged claim and re-check.\n"
        "Use `get_paper` / `get_paper_chunks` to expand context; `find_concepts` / `graph_neighbors` "
        "/ `concept_papers` to traverse the MeSH graph; `ingest_pubmed` to pull fresh papers.\n"
        "For biomarker-driven treatment questions use `match_therapies(gene, disease, variant)` "
        "/ `variant_evidence(variant)` — curated CIViC evidence with an A-E level per match; the "
        "paper_id (civic:eid…) is in the corpus and passes check_grounding. Decision support, not "
        "medical advice.\n"
        "Hard rule: no claim ships without a paper_id that `check_grounding` accepts."
    ),
)


def _hit_dict(h) -> dict[str, Any]:
    return {
        "chunk_id": h.chunk_id,
        "paper_id": h.paper_id,
        "score": round(h.score, 4),
        "section": h.section.value if hasattr(h.section, "value") else str(h.section),
        "title": h.title,
        "year": h.year,
        "journal": h.journal,
        "url": h.url,
        "text": h.text,
    }


def _hits_for(query: str, k: int) -> list[dict[str, Any]]:
    return [_hit_dict(h) for h in _search(query, k=k)]


def _hits_for_many(queries: list[str], k: int) -> list[list[dict[str, Any]]]:
    """Retrieve for several queries with the embeddings batched into ONE provider call."""
    return [[_hit_dict(h) for h in hits] for hits in _search_many(queries, k=k)]


def _allowed_ids(*hit_lists: list[dict[str, Any]]) -> list[str]:
    """Distinct paper_ids across one or more hit lists — the evidence envelope for grounding.

    The agent passes this straight into `check_grounding` so a citation is only accepted if it
    points at a paper that was actually retrieved for this question.
    """
    seen: dict[str, None] = {}
    for hits in hit_lists:
        for h in hits:
            pid = h.get("paper_id")
            if pid:
                seen.setdefault(pid, None)
    return list(seen)


@mcp.tool()
def summarize_evidence(
    question: str, k_per_facet: int = 5, facets: list[str] | None = None
) -> dict[str, Any]:
    """Build an evidence pack for a clinical question. No LLM call — Opus does synthesis.

    Decomposes the question into standard clinical facets (efficacy, safety, biomarkers,
    mechanism, comparators) and runs hybrid retrieval for each. Returns structured hits with
    citations the agent can stitch into a grounded answer.

    Args:
      question: the clinical question.
      k_per_facet: hits per facet (1..15).
      facets: override the default facet set if you want narrower / broader coverage.

    Returns: {question, facets: [{name, query, hits: [...]}], suggested_structure: [...]}.
    """
    k_per_facet = max(1, min(15, k_per_facet))
    default_facets = facets or [
        "efficacy outcomes",
        "safety adverse events",
        "biomarkers patient selection",
        "mechanism of action",
        "comparative effectiveness",
    ]
    queries = [f"{question} — {f}" for f in default_facets]
    hit_lists = _hits_for_many(queries, k_per_facet)  # one batched embed call for all facets
    out_facets = [
        {"name": f, "query": q, "hits": hits}
        for f, q, hits in zip(default_facets, queries, hit_lists, strict=True)
    ]
    allowed = _allowed_ids(*[fc["hits"] for fc in out_facets])
    return {
        "question": question,
        "facets": out_facets,
        "allowed_paper_ids": allowed,
        "suggested_structure": [
            "Clinical context (disease + stage + population)",
            "Mechanism — what the intervention does and why",
            "Efficacy — primary endpoints with effect sizes from cited trials",
            "Safety — grade ≥3 AEs, treatment-related deaths",
            "Patient selection — biomarkers and contraindications",
            "Comparators — head-to-head data if available",
            "Open questions / evidence gaps",
        ],
        "grounding_rule": (
            "Every claim in the final summary MUST cite at least one paper_id from these hits. "
            "Before presenting, call check_grounding(claims, allowed_paper_ids) and repair any "
            "claim it flags."
        ),
    }


@mcp.tool()
def evaluate_plan(plan_text: str, k_per_claim: int = 5) -> dict[str, Any]:
    """Build a per-claim evidence pack for a proposed treatment plan. No LLM call.

    Splits `plan_text` into claim-sized statements (line- and sentence-based) and runs hybrid
    retrieval per claim. Opus then judges each claim against its retrieved evidence and assigns
    a verdict: supported / contested / unsupported / unknown.

    Args:
      plan_text: the treatment plan or proposed clinical statement.
      k_per_claim: hits per claim (1..10).

    Returns: {plan, claims: [{claim, hits: [...]}], verdict_schema: {...}}.
    """
    k_per_claim = max(1, min(10, k_per_claim))
    claims = _split_claims(plan_text)
    hit_lists = _hits_for_many(claims, k_per_claim)  # one batched embed call for all claims
    out = [{"claim": c, "hits": hits} for c, hits in zip(claims, hit_lists, strict=True)]
    allowed = _allowed_ids(*[c["hits"] for c in out])
    return {
        "plan": plan_text,
        "claims": out,
        "allowed_paper_ids": allowed,
        "verdict_schema": {
            "verdict": ["supported", "contested", "unsupported", "unknown"],
            "evidence_strength": ["high", "moderate", "low", "very_low"],
            "rationale": "1-2 sentences citing specific paper_id values from the hits.",
            "risks_or_caveats": "list of 0..N concrete clinical concerns.",
        },
        "grounding_rule": (
            "For each claim, the verdict and rationale MUST cite paper_id values from `hits`."
        ),
    }


def _split_claims(text: str) -> list[str]:
    """Very small claim splitter: lines first, then sentence-level if a line is long.

    Kept deliberately tiny — Opus is the judge, this is just slicing. If callers want richer
    decomposition they can pass pre-split claims as one-per-line.
    """
    import re

    lines = [ln.strip(" -•\t") for ln in text.splitlines() if ln.strip(" -•\t")]
    claims: list[str] = []
    for line in lines:
        if len(line) < 220:
            claims.append(line)
            continue
        # split on sentence boundaries to keep claims tractable
        sents = re.split(r"(?<=[.!?])\s+", line)
        claims.extend(s for s in sents if s)
    return claims or [text.strip()]


@mcp.tool()
@locked
def check_grounding(
    claims: list[dict[str, Any]], allowed_paper_ids: list[str] | None = None
) -> dict[str, Any]:
    """Verify a drafted answer is grounded BEFORE presenting it. Deterministic — no LLM.

    This is the enforcement gate for the project's core contract: no claim without a real,
    retrievable source. Call it on your draft after retrieval and before you reply to the user.

    Args:
      claims: your draft as a list of objects, one per claim, each:
              {"text": "<the claim sentence>", "citations": ["pubmed:30345884", ...]}.
      allowed_paper_ids: the `allowed_paper_ids` returned by summarize_evidence / evaluate_plan,
              or the paper_ids from your search_papers hits. If provided, a citation must also be
              inside this evidence envelope (not just anywhere in the corpus). Omit to check
              existence-in-corpus only.

    Returns:
      {grounded, grounded_ratio, n_claims, claims:[{text, citations, status, problems}],
       violations:[...]} where status ∈ {grounded, uncited, phantom_citation, off_envelope}.

    If `grounded` is false, fix every entry in `violations` (add a real citation, correct a
    mistyped paper_id, or retrieve more evidence) and call this again. Do not present an answer
    until it returns grounded=true.
    """
    return _verify_claims(claims, docs=get_docs(), allowed_paper_ids=allowed_paper_ids)


@mcp.tool()
def search_papers(query: str, k: int = 8) -> dict[str, Any]:
    """Hybrid retrieval over the local corpus.

    Args:
      query: free-text query (clinical question, gene/drug/disease term, etc.).
      k: number of grounded hits to return (default 8, hard cap 50).

    Returns: {"hits": [...]} where each hit has chunk_id, paper_id, score, section, title,
    year, journal, url, text. Use these to construct citations.
    """
    k = max(1, min(50, k))
    hits = _search(query, k=k)
    return {"query": query, "k": k, "hits": [_hit_dict(h) for h in hits]}


@mcp.tool()
@locked
def get_paper(paper_id: str) -> dict[str, Any]:
    """Return the full paper record for a canonical id (e.g. "pubmed:39281234").

    Includes title, abstract, authors, MeSH terms, journal, year, DOI/PMID/URL.
    """
    row = get_docs().get_paper(paper_id)
    if row is None:
        raise ValueError(f"paper not found: {paper_id}")
    if row.get("publication_date") is not None:
        row["publication_date"] = row["publication_date"].isoformat()
    if row.get("ingested_at") is not None:
        row["ingested_at"] = row["ingested_at"].isoformat()
    return row


@mcp.tool()
@locked
def get_paper_chunks(paper_id: str) -> dict[str, Any]:
    """Return all chunks for a paper, in original order, with section labels."""
    chunks = get_docs().get_chunks(paper_id)
    if not chunks:
        raise ValueError(f"no chunks for paper: {paper_id}")
    return {"paper_id": paper_id, "chunks": chunks}


@mcp.tool()
def ingest_pubmed(query: str, max_results: int = 20) -> dict[str, Any]:
    """Pull fresh papers from PubMed for `query` and persist them locally.

    This embeds chunks and updates the vector index. Call sparingly — large pulls take time
    and embedding cost. Hard cap: 500.
    """
    max_results = max(1, min(500, max_results))
    stats = _run_coro(_ingest_pubmed(query, max_results=max_results))
    return {
        "query": query,
        "papers_ingested": stats.papers,
        "chunks_indexed": stats.chunks,
        "errors": stats.errors,
    }


@mcp.tool()
@locked
def add_watch(
    label: str,
    query: str,
    cadence: str = "1d",
    max_per_run: int = 50,
    notes: str = "",
) -> dict[str, Any]:
    """Register a watch that tracks new research over time.

    Each watch pulls only the delta since its last run (PubMed `edat` cursor) and skips
    PMIDs already in the corpus. Multiple watches run concurrently inside the daemon.

    Args:
      label: short unique name (used for `run_watch`, `remove_watch`).
      query: PubMed search expression.
      cadence: "30m", "1h", "6h", "1d", "1w" — minimum interval between runs.
      max_per_run: cap on papers ingested per scheduled run.
      notes: optional human-readable note.

    Returns: the new watch record.
    """
    seconds = _watch.parse_cadence(cadence)
    docs = get_docs()
    wid = docs.add_watch(
        label, query, cadence_seconds=seconds, max_per_run=max_per_run, notes=notes
    )
    w = docs.get_watch(wid)
    return _watch_to_dict(w)


@mcp.tool()
@locked
def list_watches() -> dict[str, Any]:
    """List all watches with their cadence and last-run timestamp."""
    return {"watches": [_watch_to_dict(w) for w in get_docs().list_watches()]}


@mcp.tool()
@locked
def remove_watch(ident: str) -> dict[str, Any]:
    """Remove a watch (by numeric id or label). Run history is also deleted."""
    ok = get_docs().remove_watch(ident)
    if not ok:
        raise ValueError(f"no watch found: {ident}")
    return {"removed": ident}


@mcp.tool()
def run_watch(ident: str = "", all_now: bool = False) -> dict[str, Any]:
    """Run one watch immediately (by id/label) or every enabled watch (`all_now=True`).

    Bypasses the cadence check. Returns per-watch counts and any errors. Not lock-wrapped at the
    tool level — the ingest it triggers does long network I/O and serializes its own DB writes
    internally (ADR-0014), so holding the process lock across the whole run would stall reads.
    """
    docs = get_docs()
    if all_now:
        results = _run_coro(_watch.run_all_now(docs=docs))
    else:
        if not ident:
            raise ValueError("pass `ident` or set `all_now=True`")
        with runtime.DB_LOCK:
            w = docs.get_watch(ident)
        if w is None:
            raise ValueError(f"no watch found: {ident}")
        results = [_run_coro(_watch.run_one(w["id"], docs=docs))]
    return {"runs": results}


def _watch_to_dict(w: dict[str, Any] | None) -> dict[str, Any]:
    if w is None:
        return {}
    out = dict(w)
    for k in ("last_run_at", "last_cursor_date", "created_at"):
        v = out.get(k)
        if v is not None and hasattr(v, "isoformat"):
            out[k] = v.isoformat()
    return out


@mcp.tool()
@locked
def corpus_stats() -> dict[str, Any]:
    """Counts of the local corpus. Useful before reasoning over the data."""
    docs = get_docs()
    vectors = get_vectors()
    lexical = get_lexical()
    graph = get_graph()
    c = docs.counts()
    g = graph.counts()
    return {
        "papers": c["papers"],
        "chunks": c["chunks"],
        "vectors": vectors.count(),
        "lexical_index": lexical.count(),
        "civic_evidence": docs.civic_counts(),
        "graph": g,
        "retrieval_channels": ["vector", "lexical", "graph"],
        "data_dir": str(CONFIG.data_dir),
        "embedding_provider": CONFIG.embedding_provider,
        "embedding_model": CONFIG.embedding_model,
        "embedding_dim": CONFIG.embedding_dim,
    }


@mcp.tool()
@locked
def find_concepts(fragment: str, limit: int = 15) -> dict[str, Any]:
    """Search the knowledge graph for MeSH concepts whose name contains `fragment`.

    Useful as a typeahead before calling `graph_neighbors` or `concept_papers`. Returns the
    canonical concept id (e.g. "mesh:BRCA1_Protein") that the other graph tools accept.
    """
    rows = get_graph().find_concepts(fragment, limit=max(1, min(100, limit)))
    return {"fragment": fragment, "concepts": rows}


@mcp.tool()
@locked
def graph_neighbors(concept_name: str, hops: int = 1, limit: int = 15) -> dict[str, Any]:
    """Concepts that co-occur with `concept_name` in the corpus.

    Args:
      concept_name: a MeSH descriptor (e.g. "Olaparib", "BRCA1 Protein").
      hops: 1 = direct co-occurrence (recommended), 2 = expanded (noisy).
      limit: max neighbors to return.

    Returns: {anchor, neighbors: [{id, name, weight}]} where weight is paper-co-occurrence count.
    """
    rows = get_graph().neighbors(concept_name, hops=hops, limit=max(1, min(100, limit)))
    return {"anchor": concept_name, "hops": hops, "neighbors": rows}


@mcp.tool()
@locked
def concept_papers(concept_name: str, limit: int = 20) -> dict[str, Any]:
    """Papers tagged with the given MeSH concept, most-recent first."""
    rows = get_graph().concept_papers(concept_name, limit=max(1, min(100, limit)))
    return {"concept": concept_name, "papers": rows}


@mcp.tool()
@locked
def match_therapies(
    gene: str = "", disease: str = "", variant: str = "", limit: int = 20
) -> dict[str, Any]:
    """Curated biomarker → therapy matches from CIViC, ranked by evidence LEVEL (A best).

    Given a gene (e.g. "EGFR") and optionally a disease ("lung") and/or variant ("T790M"), returns
    PREDICTIVE evidence: which therapies are indicated (sensitivity/response) or contraindicated
    (resistance), each with its CIViC evidence level (A=validated … E=inferential), clinical
    significance, the underlying PubMed id, and a `paper_id` (civic:eid…) that is in the corpus —
    so you can cite it and it passes `check_grounding`.

    Prefer this curated, leveled evidence over raw literature for treatment matching, but still
    ground every claim and surface the evidence level. NOT medical advice — decision SUPPORT only;
    defer to the treating team / tumor board.
    """
    rows = get_docs().civic_match(
        gene=gene or None, disease=disease or None, variant=variant or None,
        limit=max(1, min(100, limit)),
    )
    return {
        "query": {"gene": gene, "disease": disease, "variant": variant},
        "matches": [
            {
                "paper_id": r["paper_id"],
                "variant": r["variant"],
                "disease": r["disease"],
                "therapies": r["therapies"],
                "evidence_level": r["evidence_level"],
                "significance": r["significance"],
                "direction": r["direction"],
                "pmid": r["pmid"],
                "url": r["url"],
                "statement": r["description"],
            }
            for r in rows
        ],
        "note": (
            "CIViC curated evidence; level A=validated … E=inferential. Cite paper_id via "
            "check_grounding. Decision support, not medical advice."
        ),
    }


@mcp.tool()
@locked
def variant_evidence(variant: str, limit: int = 25) -> dict[str, Any]:
    """All CIViC evidence for a variant / molecular profile (any type), ranked by level.

    `variant` matches the molecular-profile name, e.g. "BRAF V600E", "EGFR T790M", "KRAS G12C".
    Returns predictive / diagnostic / prognostic items with their level and cited paper_id.
    """
    rows = get_docs().civic_for_variant(variant, limit=max(1, min(100, limit)))
    return {
        "variant": variant,
        "evidence": [
            {
                "paper_id": r["paper_id"],
                "evidence_type": r["evidence_type"],
                "evidence_level": r["evidence_level"],
                "significance": r["significance"],
                "disease": r["disease"],
                "therapies": r["therapies"],
                "pmid": r["pmid"],
                "statement": r["description"],
            }
            for r in rows
        ],
    }


def _guard_single_owner() -> None:
    """Take the single-writer lock before serving; a second medground exits cleanly (ADR-0018).

    Exiting 0 (rather than crashing) means an MCP client that started a redundant server just
    sees it disconnect, instead of a noisy traceback on every tool call.
    """
    try:
        runtime.acquire_owner_lock()
    except runtime.DataDirLocked as exc:
        print(f"medground: {exc}", file=sys.stderr)
        raise SystemExit(0) from None


def main() -> None:
    """Run the MCP server over stdio (one subprocess per client). Wire into any MCP client."""
    _guard_single_owner()
    _register_checkpoint_once()
    mcp.run()


def serve(host: str | None = None, port: int | None = None) -> None:
    """Run ONE shared MCP server over streamable-HTTP.

    Multiple clients (terminals, Warp panes, IDE, agents) connect to http://HOST:PORT/mcp and
    share this single process — which is the only DuckDB/KuzuDB writer. This is the costless way
    to use medground from many clients at once without the per-client-stdio lock conflict. See
    ADR-0018. Localhost-only by default; do not expose to a network without adding auth.
    """
    _guard_single_owner()
    _register_checkpoint_once()
    mcp.settings.host = host or CONFIG.http_host
    mcp.settings.port = port or CONFIG.http_port
    log.info(
        "medground MCP server (streamable-http) on http://%s:%d%s",
        mcp.settings.host,
        mcp.settings.port,
        mcp.settings.streamable_http_path,
    )
    mcp.run(transport="streamable-http")


if __name__ == "__main__":  # pragma: no cover
    main()
