"""Hybrid retrieval. v2: three channels fused with RRF.

  - vector  (DuckDB VSS)  — dense semantic similarity
  - lexical (DuckDB FTS)  — BM25 exact-token match (drug/gene/trial IDs, dosages)
  - graph   (KuzuDB)      — 1-hop MeSH co-occurrence expansion

Design contract: every result carries enough provenance to render a citation without further
lookups — paper title, year, journal, URL.

Fusion: the per-channel ranked lists are merged with Reciprocal Rank Fusion (RRF), the standard
library-grade fusion algorithm (`score = sum(1 / (k + rank))`). No tuning weights — RRF is
intentionally simple and well-behaved, and it only reads *rank*, so the channels' incomparable
raw scores (cosine distance vs BM25 vs co-occurrence count) need no normalisation.

Every channel is consulted opportunistically and degrades independently: a missing embedding
key, an empty vector table, or a sparse graph drops that channel to nothing rather than failing
the query. As long as one channel answers, `search` returns grounded hits. In particular the
lexical channel needs no API key, so retrieval works the moment papers are ingested.
"""

from __future__ import annotations

import logging

from medground import runtime
from medground.models import ChunkSection, RetrievalHit
from medground.nlp.embeddings import get_embedder
from medground.store.docs import DocStore  # used in type annotations
from medground.store.lexical import LexicalStore
from medground.store.vectors import VectorStore

log = logging.getLogger("medground.retrieve")

RRF_K = 60  # standard RRF constant — robust across corpora


def _row_to_hit(row: dict, score: float) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=row["id"],
        paper_id=row["paper_id"],
        score=score,
        section=ChunkSection(row.get("section", "abstract")),
        text=row.get("text", ""),
        title=row.get("title", ""),
        year=row.get("year"),
        journal=row.get("journal"),
        url=row.get("url"),
    )


def vector_search(
    query: str,
    *,
    k: int = 8,
    docs: DocStore | None = None,
    vectors: VectorStore | None = None,
) -> list[RetrievalHit]:
    docs = docs or runtime.get_docs()
    vectors = vectors or VectorStore(docs=docs)
    embedder = get_embedder()
    qvec = embedder.embed_query(query)
    rows = vectors.search(qvec, k=k)
    if not rows:
        return []
    # Cosine distance ∈ [0, 2]; turn into similarity-ish score in [0, 1] for human readability.
    return [_row_to_hit(r, max(0.0, 1.0 - float(r.get("_distance", 0.0)))) for r in rows]


def lexical_search(
    query: str,
    *,
    k: int = 8,
    docs: DocStore | None = None,
    lexical: LexicalStore | None = None,
) -> list[RetrievalHit]:
    """BM25 over chunk text (DuckDB FTS). No API key needed — pure lexical.

    Catches the exact-token matches dense vectors miss: gene symbols, drug names, trial NCT
    ids, dosages. The displayed score is the BM25 value normalised to [0, 1] within this list
    for readability only — RRF fusion uses rank, so the raw magnitude is irrelevant downstream.
    """
    docs = docs or runtime.get_docs()
    lexical = lexical or LexicalStore(docs=docs)
    rows = lexical.search(query, k=k)
    if not rows:
        return []
    top = float(rows[0].get("_bm25", 0.0)) or 1.0
    return [
        _row_to_hit(r, max(0.0, min(1.0, float(r.get("_bm25", 0.0)) / top))) for r in rows
    ]


def graph_search(
    query: str,
    *,
    k: int = 8,
    docs: DocStore | None = None,
) -> list[RetrievalHit]:
    """1-hop MeSH expansion: anchor on concepts appearing in the query, return their papers.

    Cheap and explainable: if "BRCA1" appears in the query and is a MeSH concept, we surface
    chunks from papers tagged with BRCA1 or any concept that co-occurs strongly with it. The
    fusion step (`search`) is what decides whether to trust this signal vs vector retrieval.
    """
    docs = docs or runtime.get_docs()
    try:
        graph = runtime.get_graph()
    except Exception:
        return []
    chunk_rows = graph.chunks_for_query(query, k=k * 3, docs=docs)
    if not chunk_rows:
        return []
    # Graph-derived score: best chunk per paper gets the highest score; cap to k results.
    seen: set[str] = set()
    hits: list[RetrievalHit] = []
    for row in chunk_rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        # Graph-side score in [0, 1] from the anchor strength the graph store computed.
        hits.append(_row_to_hit(row, float(row.get("anchor_score", 0.5))))
        if len(hits) >= k:
            break
    return hits


def _rrf_fuse(*ranked_lists: list[RetrievalHit], k: int) -> list[RetrievalHit]:
    """Reciprocal Rank Fusion. Aggregates multiple ranked lists into a single ordering."""
    scores: dict[str, float] = {}
    best_seen: dict[str, RetrievalHit] = {}
    for ranked in ranked_lists:
        for rank, hit in enumerate(ranked, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            # Keep the highest-scoring representation of each chunk for citation rendering.
            if hit.chunk_id not in best_seen or hit.score > best_seen[hit.chunk_id].score:
                best_seen[hit.chunk_id] = hit
    fused = sorted(best_seen.values(), key=lambda h: scores[h.chunk_id], reverse=True)
    # Overwrite per-hit score with the fused score so downstream UI sees a coherent value.
    for h in fused:
        h.score = round(scores[h.chunk_id], 6)
    return fused[:k]


def _safe_vector(query, k, docs, vectors) -> list[RetrievalHit]:
    """Vector channel, but never fatal: a missing embedding key or empty table → no hits.

    This is what lets the corpus be searchable (via lexical + graph) before a re-embed."""
    try:
        return vector_search(query, k=k, docs=docs, vectors=vectors)
    except Exception as e:  # missing API key, empty/absent vector table, provider error
        log.info("vector channel unavailable (%s) — degrading to lexical + graph", e)
        return []


def search(query: str, k: int = 8) -> list[RetrievalHit]:
    """Hybrid retrieval: vector + lexical + graph, fused via RRF. Stable public entry.

    Each channel degrades independently; the fusion runs over whichever channels returned hits.
    Over-fetch per channel (k*2) so RRF has room to reward agreement across channels.

    Runs under the process DB lock (ADR-0014) so it can't collide with a background watch write
    on the shared connections. The lock is released between calls, so a multi-facet caller
    (summarize_evidence) lets watch ingests interleave between facets.
    """
    with runtime.DB_LOCK:
        docs = runtime.get_docs()
        vectors = runtime.get_vectors()
        lexical = runtime.get_lexical()
        channels = [
            _safe_vector(query, k * 2, docs, vectors),
            lexical_search(query, k=k * 2, docs=docs, lexical=lexical),
            graph_search(query, k=k * 2, docs=docs),
        ]
    live = [c for c in channels if c]
    if not live:
        return []
    if len(live) == 1:
        return live[0][:k]
    return _rrf_fuse(*live, k=k)


def search_many(queries: list[str], k: int = 8) -> list[list[RetrievalHit]]:
    """Batched multi-query hybrid retrieval. Returns one hit-list per query, aligned by index.

    Embeds ALL query strings in a SINGLE provider call — the per-query embed round trip was the
    dominant latency when a caller runs many sub-queries (summarize_evidence's facets,
    evaluate_plan's claims). The embed happens outside the DB lock; the three channels are then
    fused per query. Degrades exactly like `search`: a missing/erroring embedder drops the vector
    channel and lexical + graph carry each query.
    """
    if not queries:
        return []
    docs = runtime.get_docs()
    vectors = runtime.get_vectors()
    lexical = runtime.get_lexical()
    qvecs = None
    try:
        qvecs = get_embedder().embed_queries(queries)  # one round trip for all queries
    except Exception as e:
        log.info("vector channel unavailable (%s) — degrading to lexical + graph", e)

    out: list[list[RetrievalHit]] = []
    with runtime.DB_LOCK:
        for i, q in enumerate(queries):
            v_hits: list[RetrievalHit] = []
            if qvecs is not None:
                try:
                    rows = vectors.search(qvecs[i], k=k * 2)
                    v_hits = [
                        _row_to_hit(r, max(0.0, 1.0 - float(r.get("_distance", 0.0)))) for r in rows
                    ]
                except Exception:
                    v_hits = []
            channels = [v_hits, lexical_search(q, k=k * 2, docs=docs, lexical=lexical),
                        graph_search(q, k=k * 2, docs=docs)]
            live = [c for c in channels if c]
            out.append(_rrf_fuse(*live, k=k) if len(live) > 1 else (live[0][:k] if live else []))
    return out
