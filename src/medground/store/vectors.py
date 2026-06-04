"""Vector store — DuckDB VSS (HNSW). Same file, same connection as `DocStore`.

Why DuckDB instead of LanceDB:
  - Docs, chunks, MeSH terms, AND vectors all live in one file (one backup, one transaction).
  - SQL composes filters + vector search natively: `WHERE p.year >= 2020 ORDER BY distance`.
  - DuckDB's `vss` community extension ships an HNSW index that uses `array_cosine_distance`.

Schema is a separate table from `chunks` so a dim change (e.g. swapping embedding provider)
only recreates the vector table — the textual chunks and their citation metadata remain intact.

For VSS HNSW persistence across sessions, DuckDB requires the experimental flag — set on every
new connection. Cost: nothing in practice; the flag is silently ignored if already enabled.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from medground.config import CONFIG
from medground.models import Chunk
from medground.store.docs import DocStore

TABLE_NAME = "chunk_vectors"
INDEX_NAME = "chunk_vectors_hnsw"


class VectorStore:
    """DuckDB-backed vector store. Shares connection with `DocStore` — single DB file."""

    def __init__(self, docs: DocStore | None = None, dim: int | None = None) -> None:
        self.docs = docs or DocStore()
        self.dim = dim or CONFIG.embedding_dim
        self._ensured = False

    def _ensure(self) -> None:
        """Idempotent: install/load VSS, create table+HNSW at current dim, migrate on mismatch."""
        if self._ensured:
            return
        c = self.docs.conn
        # The `vss` extension is a community DuckDB extension; INSTALL is a one-time download,
        # LOAD is per-connection. Both are idempotent.
        c.execute("INSTALL vss")
        c.execute("LOAD vss")
        # HNSW on persistent storage is gated behind an experimental flag at the time of writing.
        c.execute("SET hnsw_enable_experimental_persistence = true")

        existing_dim = self._existing_dim()
        if existing_dim is not None and existing_dim != self.dim:
            # Provider/model swap. Schema is FLOAT[N] which is fixed; drop the vector table
            # only — chunks/papers remain. Re-populate via `medground reembed`.
            c.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
            c.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
            existing_dim = None

        if existing_dim is None:
            c.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                    chunk_id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL,
                    section TEXT,
                    vector FLOAT[{self.dim}]
                )
                """
            )
            c.execute(
                f"""
                CREATE INDEX IF NOT EXISTS {INDEX_NAME}
                ON {TABLE_NAME} USING HNSW (vector) WITH (metric = 'cosine')
                """
            )
        self._ensured = True

    def _existing_dim(self) -> int | None:
        c = self.docs.conn
        row = c.execute(
            """
            SELECT data_type FROM information_schema.columns
            WHERE table_name = ? AND column_name = 'vector'
            """,
            [TABLE_NAME],
        ).fetchone()
        if row is None:
            return None
        # data_type comes back as e.g. "FLOAT[3072]"
        dtype = str(row[0])
        if "[" in dtype and dtype.endswith("]"):
            try:
                return int(dtype.split("[")[1].rstrip("]"))
            except ValueError:
                return None
        return None

    # ----- writes -----

    def upsert(self, chunks: Iterable[Chunk], vectors: np.ndarray) -> int:
        chunks = list(chunks)
        if not chunks:
            return 0
        if vectors.shape != (len(chunks), self.dim):
            raise ValueError(
                f"vectors shape {vectors.shape} != expected ({len(chunks)}, {self.dim})"
            )
        self._ensure()
        ids = [c.id for c in chunks]
        rows = [
            (c.id, c.paper_id, c.section.value, vectors[i].astype(np.float32).tolist())
            for i, c in enumerate(chunks)
        ]
        with self.docs.transaction() as conn:
            placeholders = ",".join(["?"] * len(ids))
            conn.execute(
                f"DELETE FROM {TABLE_NAME} WHERE chunk_id IN ({placeholders})", ids
            )
            conn.executemany(
                f"INSERT INTO {TABLE_NAME} (chunk_id, paper_id, section, vector) "
                f"VALUES (?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    # ----- reads -----

    def search(self, query_vector: np.ndarray, k: int = 8) -> list[dict]:
        """Return top-k by cosine distance, joined with paper+chunk metadata for grounding.

        The probe vector is inlined as a LITERAL, not a bound `?` parameter: DuckDB's VSS only
        rewrites `ORDER BY array_cosine_distance(col, <constant>) LIMIT k` into an HNSW_INDEX_SCAN
        when the probe is a constant. With a parameter it silently falls back to a full brute-force
        scan (~20x slower — measured 200ms vs 11ms at 3.7k vectors). The values are our own
        float32 embeddings (numeric), so inlining is injection-safe. The index scan runs in a CTE
        so the metadata joins can't disturb the rewrite.
        """
        self._ensure()
        qvec = query_vector.astype(np.float32).tolist()
        vec_lit = "[" + ",".join(repr(x) for x in qvec) + "]"
        k = int(k)
        rows = self.docs.conn.execute(
            f"""
            WITH topk AS (
                SELECT chunk_id, paper_id, section,
                       array_cosine_distance(vector, {vec_lit}::FLOAT[{self.dim}]) AS _distance
                FROM {TABLE_NAME}
                ORDER BY array_cosine_distance(vector, {vec_lit}::FLOAT[{self.dim}])
                LIMIT {k}
            )
            SELECT t.chunk_id AS id, t.paper_id, t.section, c.text, c.index AS chunk_index,
                   p.title, p.year, p.journal, p.url, t._distance
            FROM topk t
            JOIN chunks c ON c.id = t.chunk_id
            JOIN papers p ON p.id = t.paper_id
            ORDER BY t._distance
            """
        ).fetchall()
        cols = [d[0] for d in self.docs.conn.description]
        return [dict(zip(cols, r, strict=False)) for r in rows]

    def compact(self) -> None:
        """Reclaim HNSW index churn (DuckDB rewrites the persistent index on every checkpoint, and
        the freed blocks are not auto-reclaimed). Bounds on-disk growth between full `medground
        compact` rebuilds. Best-effort — caller should not abort an ingest if this fails.
        """
        self._ensure()
        self.docs.conn.execute(f"PRAGMA hnsw_compact_index('{INDEX_NAME}')")
        self.docs.conn.execute("CHECKPOINT")

    def count(self) -> int:
        if not self._ensured:
            try:
                self._ensure()
            except Exception:
                return 0
        return int(
            self.docs.conn.execute(f"SELECT count(*) FROM {TABLE_NAME}").fetchone()[0]
        )


def open_vectors(docs: DocStore | None = None) -> VectorStore:
    return VectorStore(docs=docs)
