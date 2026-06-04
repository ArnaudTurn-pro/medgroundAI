"""End-to-end ingestion pipeline.

Source-agnostic shape:
  fetch (async generator of Paper)
    → chunk
    → persist Paper + Chunks in DuckDB
    → embed chunks → upsert vectors (DuckDB VSS)
    → upsert MeSH concepts + MENTIONS edges (KuzuDB)
  then, once: rebuild the lexical FTS index (DuckDB BM25 snapshot)

The pipeline is streaming: papers are processed in micro-batches as they arrive, so memory stays
flat regardless of corpus size. Failures on a single paper are logged and skipped — never abort
a long ingest because one record was malformed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from medground import runtime
from medground.ingest.chunker import chunk_paper
from medground.models import Chunk, Paper
from medground.nlp.embeddings import get_embedder
from medground.sources import civic, pubmed
from medground.store.docs import DocStore
from medground.store.graph import GraphStore
from medground.store.lexical import LexicalStore
from medground.store.vectors import VectorStore

log = logging.getLogger("medground.ingest")


@dataclass
class IngestStats:
    papers: int = 0
    chunks: int = 0
    errors: int = 0


def _locked(fn, *args):
    """Run a single DB op under the process lock (ADR-0014). Call inside asyncio.to_thread so the
    lock is acquired off the event loop — network/embedding work stays outside it by construction.
    """
    with runtime.DB_LOCK:
        return fn(*args)


def _chunk_and_store(docs: DocStore, batch: list[Paper]) -> list[Chunk]:
    """Chunk a batch and persist the chunks, holding the lock once for the whole batch."""
    out: list[Chunk] = []
    with runtime.DB_LOCK:
        for p in batch:
            chunks = chunk_paper(p)
            if not chunks:
                continue
            docs.replace_chunks(p.id, chunks)
            out.extend(chunks)
    return out


async def _drain_in_batches(stream: AsyncIterator[Paper], batch_size: int):
    batch: list[Paper] = []
    async for paper in stream:
        batch.append(paper)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


async def _ingest_stream(
    stream: AsyncIterator[Paper],
    *,
    source: str,
    query: str,
    docs: DocStore,
    vectors: VectorStore | None,
    graph: GraphStore | None,
    lexical: LexicalStore | None,
    embed: bool,
    populate_graph: bool,
    build_lexical: bool,
    batch_size: int,
    on_batch: Callable[[list[Paper]], None] | None = None,
) -> IngestStats:
    """Source-agnostic core: drain a `Paper` stream into all stores (the lake).

    One pipeline, many rivers — PubMed, CIViC, … each builds a `Paper` stream and feeds it here.
    Re-ingest is additive (already-present ids are skipped). `on_batch(batch)` is an optional
    per-batch side-write for a source's structured layer (e.g. CIViC's `civic_evidence` table);
    it runs under the process lock. See docs/data-pipelines.html.
    """
    embedder = get_embedder() if embed else None
    stats = IngestStats()
    with runtime.DB_LOCK:
        run_id = docs.start_run(source, query)
    try:
        async for batch in _drain_in_batches(stream, batch_size):
            # Additive: drop ids already in the corpus (no re-chunk/re-embed churn).
            known = await asyncio.to_thread(_locked, docs.known_paper_ids, [p.id for p in batch])
            batch = [p for p in batch if p.id not in known]
            if not batch:
                continue

            # 1. Persist papers (DB write — serialized via the process lock, ADR-0014)
            try:
                await asyncio.to_thread(_locked, docs.upsert_papers, batch)
            except Exception:
                log.exception("upsert_papers failed for batch of %d", len(batch))
                stats.errors += len(batch)
                continue

            # 2. Chunk (CPU) + persist chunks (DB write), under the lock once for the batch.
            all_chunks = await asyncio.to_thread(_chunk_and_store, docs, batch)

            # 3. Embed (network — deliberately NOT under the lock) then upsert vectors (locked).
            if embed and all_chunks and embedder and vectors:
                texts = [c.text for c in all_chunks]
                vecs = await asyncio.to_thread(embedder.embed_passages, texts)
                await asyncio.to_thread(_locked, vectors.upsert, all_chunks, vecs)

            # 4. Graph: concepts + MENTIONS edges. Best-effort, won't abort the batch.
            if populate_graph and graph is not None:
                try:
                    await asyncio.to_thread(_locked, graph.upsert_papers, batch)
                except Exception:
                    log.exception("graph upsert failed for batch")

            # 5. Source-specific structured side-write (e.g. CIViC civic_evidence). Best-effort.
            if on_batch is not None:
                try:
                    await asyncio.to_thread(_locked, on_batch, batch)
                except Exception:
                    log.exception("on_batch side-write failed for source %s", source)

            stats.papers += len(batch)
            stats.chunks += len(all_chunks)
            log.info(
                "ingested batch [%s]: +%d papers (+%d chunks), totals %d / %d",
                source, len(batch), len(all_chunks), stats.papers, stats.chunks,
            )
    finally:
        await asyncio.to_thread(_locked, docs.finish_run, run_id, stats.papers, stats.chunks)
        # Lexical index is a snapshot — refresh it once now that new chunks have landed.
        if build_lexical and lexical is not None and stats.chunks:
            try:
                await asyncio.to_thread(_locked, lexical.rebuild)
            except Exception:
                log.exception("lexical FTS index rebuild failed")
        # Fold the graph WAL into graph.kuzu so a later crash/kill can't strand the MeSH graph in
        # its WAL (Kuzu only auto-checkpoints on a clean close). Best-effort.
        if populate_graph and graph is not None and stats.papers:
            try:
                await asyncio.to_thread(_locked, graph.checkpoint)
            except Exception:
                log.exception("graph checkpoint failed")
        # Reclaim HNSW index churn (DuckDB rewrites it on checkpoint). `medground compact` is
        # the deep reclaim.
        if embed and vectors is not None and stats.chunks:
            try:
                await asyncio.to_thread(_locked, vectors.compact)
            except Exception:
                log.exception("hnsw index compaction failed")
    return stats


def _resolve_stores(docs, vectors, graph, lexical, *, embed, populate_graph, build_lexical):
    docs = docs or runtime.get_docs()
    vectors = vectors or (VectorStore(docs=docs) if embed else None)
    graph = graph or (runtime.get_graph() if populate_graph else None)
    lexical = lexical or (LexicalStore(docs=docs) if build_lexical else None)
    return docs, vectors, graph, lexical


async def ingest_pubmed(
    query: str,
    *,
    max_results: int = 50,
    batch_size: int = 16,
    docs: DocStore | None = None,
    vectors: VectorStore | None = None,
    graph: GraphStore | None = None,
    lexical: LexicalStore | None = None,
    embed: bool = True,
    populate_graph: bool = True,
    build_lexical: bool = True,
    mindate: str | None = None,
    datetype: str = "pdat",
    sort: str = "relevance",
    skip_known: bool = False,
) -> IngestStats:
    """River: PubMed literature. `mindate`/`datetype` enable delta pulls (watches); `skip_known`
    filters PMIDs already in the corpus before efetch (saves bandwidth)."""
    docs, vectors, graph, lexical = _resolve_stores(
        docs, vectors, graph, lexical, embed=embed, populate_graph=populate_graph,
        build_lexical=build_lexical,
    )
    skip: set[str] | None = None
    if skip_known:
        with runtime.DB_LOCK:
            rows = docs.conn.execute(
                "SELECT pmid FROM papers WHERE source = 'pubmed' AND pmid IS NOT NULL"
            ).fetchall()
        skip = {r[0] for r in rows}
    stream = pubmed.fetch(
        query, max_results=max_results, mindate=mindate, datetype=datetype, sort=sort,
        skip_pmids=skip,
    )
    return await _ingest_stream(
        stream, source="pubmed", query=query, docs=docs, vectors=vectors, graph=graph,
        lexical=lexical, embed=embed, populate_graph=populate_graph, build_lexical=build_lexical,
        batch_size=batch_size,
    )


async def ingest_civic(
    *,
    max_results: int | None = None,
    batch_size: int = 64,
    docs: DocStore | None = None,
    vectors: VectorStore | None = None,
    graph: GraphStore | None = None,
    lexical: LexicalStore | None = None,
    embed: bool = True,
    populate_graph: bool = True,
    build_lexical: bool = True,
) -> IngestStats:
    """River: CIViC. Ingests curated variant→therapy evidence as groundable documents (reusing the
    literature pipeline) AND into the structured `civic_evidence` table for precise biomarker→
    therapy matching. `max_results=None` ingests the whole knowledgebase (~11k items). See
    ADR-0017 + docs/CIVIC_INTEGRATION_PLAN.md."""
    docs, vectors, graph, lexical = _resolve_stores(
        docs, vectors, graph, lexical, embed=embed, populate_graph=populate_graph,
        build_lexical=build_lexical,
    )

    async def _stream() -> AsyncIterator[Paper]:
        async for ev in civic.fetch(max_results=max_results):
            yield civic.to_paper(ev)

    def _write_civic(batch: list[Paper]) -> None:
        docs.upsert_civic_evidence([p.raw for p in batch if isinstance(p.raw, dict)])

    return await _ingest_stream(
        _stream(), source="civic", query=f"civic(max={max_results})", docs=docs, vectors=vectors,
        graph=graph, lexical=lexical, embed=embed, populate_graph=populate_graph,
        build_lexical=build_lexical, batch_size=batch_size, on_batch=_write_civic,
    )
