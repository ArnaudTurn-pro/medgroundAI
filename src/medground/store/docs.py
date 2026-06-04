"""DuckDB-backed document store. Holds Papers and Chunks with idempotent upserts.

Why DuckDB:
  - Single embedded file, zero ops, ACID.
  - Columnar storage + vectorized execution → handles millions of rows on a laptop.
  - SQL is the lingua franca for analytics & ad-hoc reports.

Concurrency note: DuckDB is single-writer. We keep one process holding the connection at a time
(the CLI ingest / the MCP server). Connection is created lazily so import is cheap.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import duckdb

from medground.config import CONFIG
from medground.models import Chunk, Paper

log = logging.getLogger("medground.docs")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    native_id TEXT NOT NULL,
    title TEXT,
    abstract TEXT,
    authors TEXT[],
    journal TEXT,
    year INTEGER,
    publication_date DATE,
    doi TEXT,
    pmid TEXT,
    pmcid TEXT,
    url TEXT,
    mesh_terms TEXT[],
    keywords TEXT[],
    publication_types TEXT[],
    language TEXT,
    ingested_at TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS papers_pmid_idx ON papers(pmid);
CREATE INDEX IF NOT EXISTS papers_doi_idx ON papers(doi);
CREATE INDEX IF NOT EXISTS papers_year_idx ON papers(year);

-- No FK on paper_id: DuckDB can't DELETE, nor UPDATE list-typed columns of, a row referenced by
-- a FK, and replays FK drops without CASCADE — which broke idempotent re-ingest, FTS-rebuild WAL
-- replay, and the compaction rebuild. Integrity is enforced by the pipeline (chunks are always
-- written with a valid, just-upserted paper_id). See ADR-0016.
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL,
    index INTEGER NOT NULL,
    section TEXT NOT NULL,
    text TEXT NOT NULL,
    char_start INTEGER,
    char_end INTEGER
);

CREATE INDEX IF NOT EXISTS chunks_paper_idx ON chunks(paper_id);
CREATE INDEX IF NOT EXISTS chunks_section_idx ON chunks(section);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    query TEXT,
    started_at TIMESTAMP DEFAULT now(),
    finished_at TIMESTAMP,
    n_papers INTEGER DEFAULT 0,
    n_chunks INTEGER DEFAULT 0,
    notes TEXT
);
CREATE SEQUENCE IF NOT EXISTS ingestion_runs_id_seq;

CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    query TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'pubmed',
    cadence_seconds INTEGER NOT NULL DEFAULT 86400,
    max_per_run INTEGER NOT NULL DEFAULT 50,
    last_run_at TIMESTAMP,
    last_cursor_date DATE,
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT now(),
    notes TEXT
);
CREATE SEQUENCE IF NOT EXISTS watches_id_seq;

CREATE TABLE IF NOT EXISTS watch_runs (
    id INTEGER PRIMARY KEY,
    watch_id INTEGER NOT NULL,
    started_at TIMESTAMP DEFAULT now(),
    finished_at TIMESTAMP,
    papers_added INTEGER DEFAULT 0,
    chunks_added INTEGER DEFAULT 0,
    error TEXT
);
CREATE SEQUENCE IF NOT EXISTS watch_runs_id_seq;

-- CIViC structured biomarker->therapy evidence (the precise-matching layer; the same items also
-- live as groundable documents in papers/chunks via paper_id = "civic:eid<eid>"). See ADR-0017.
CREATE TABLE IF NOT EXISTS civic_evidence (
    eid INTEGER PRIMARY KEY,
    paper_id TEXT,
    gene TEXT,
    variant TEXT,
    disease TEXT,
    doid TEXT,
    therapies TEXT[],
    evidence_level TEXT,
    evidence_type TEXT,
    direction TEXT,
    significance TEXT,
    pmid TEXT,
    year INTEGER,
    url TEXT,
    description TEXT
);
CREATE INDEX IF NOT EXISTS civic_gene_idx ON civic_evidence(gene);
CREATE INDEX IF NOT EXISTS civic_disease_idx ON civic_evidence(disease);
CREATE INDEX IF NOT EXISTS civic_pmid_idx ON civic_evidence(pmid);
"""


class DocStore:
    """Thin DuckDB wrapper. Idempotent upserts; explicit transactions on bulk writes."""

    def __init__(self, path: Path | None = None) -> None:
        CONFIG.ensure_dirs()
        self.path = path or CONFIG.duckdb_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self._conn = self._open_with_recovery()
        return self._conn

    def _open(self) -> duckdb.DuckDBPyConnection:
        """Open the DuckDB file and load the index extensions. May raise on WAL replay."""
        # The persistent DB embeds an HNSW (vss) index AND an FTS index. Every connection must
        # load BOTH: any write or CHECKPOINT touches them, and a checkpoint that can't see an
        # index type fails. `autoload`/`autoinstall` also let WAL replay on open pull the
        # extensions in for their custom index types, so the DB stays openable after a crash.
        conn = duckdb.connect(
            str(self.path),
            config={
                "autoinstall_known_extensions": True,
                "autoload_known_extensions": True,
            },
        )
        for ext in ("vss", "fts"):
            with contextlib.suppress(Exception):
                conn.execute(f"INSTALL {ext}")
                conn.execute(f"LOAD {ext}")
        conn.execute("SET hnsw_enable_experimental_persistence = true")
        conn.execute(_SCHEMA)
        return conn

    def _open_with_recovery(self) -> duckdb.DuckDBPyConnection:
        """Open the store, self-healing a poisoned WAL instead of staying bricked forever.

        A native crash (SIGTRAP/SIGKILL) mid-checkpoint can leave a write-ahead log DuckDB cannot
        replay — most often the FTS-rebuild case ("Cannot drop fts_main_chunks ... terms depends on
        it"; replay has no CASCADE), but the experimental HNSW index can poison it too. Once that
        happens EVERY open fails and the whole corpus is unreachable, with no `atexit` flush to save
        it (the crash already happened). We trade the unreplayable tail for an openable database:
        quarantine the `.wal` beside the file and reopen from the last good checkpoint. The dropped
        writes are the un-checkpointed tail of the last ingest, which is additive and re-runnable.
        """
        try:
            return self._open()
        except Exception as exc:
            if not self._is_wal_replay_failure(exc):
                raise
            quarantined = self._quarantine_wal()
            if quarantined is None:
                raise
            log.warning(
                "DuckDB WAL replay failed (%s). Quarantined the unreplayable WAL to %s and "
                "reopened from the last checkpoint; un-checkpointed writes from the last ingest "
                "were dropped (re-run the ingest — it is additive). See ADR-0018.",
                exc,
                quarantined.name,
            )
            return self._open()

    @staticmethod
    def _is_wal_replay_failure(exc: Exception) -> bool:
        msg = str(exc).lower()
        return "wal" in msg and ("replay" in msg or "fts_main" in msg or "depends on" in msg)

    def _quarantine_wal(self) -> Path | None:
        """Move the `.wal` aside so the next open replays nothing. Returns the new path, or None."""
        wal = self.path.with_name(self.path.name + ".wal")
        if not wal.exists():
            return None
        target = wal.with_name(f"{wal.name}.corrupt-{os.getpid()}")
        with contextlib.suppress(Exception):
            target.unlink()
        try:
            wal.rename(target)
        except OSError:
            return None
        return target

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextlib.contextmanager
    def transaction(self) -> Iterator[duckdb.DuckDBPyConnection]:
        c = self.conn
        c.execute("BEGIN")
        try:
            yield c
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise

    # ----- writes -----

    def upsert_papers(self, papers: Iterable[Paper]) -> int:
        rows = [
            (
                p.id, p.source, p.native_id, p.title, p.abstract, p.authors,
                p.journal, p.year, p.publication_date, p.doi, p.pmid, p.pmcid, p.url,
                p.mesh_terms, p.keywords, p.publication_types, p.language,
            )
            for p in papers
        ]
        if not rows:
            return 0
        ids = [r[0] for r in rows]
        with self.transaction() as c:
            # Insert-or-ignore. We never DELETE or UPDATE an existing paper row: DuckDB cannot
            # delete — nor update the list-typed columns of (authors/mesh_terms/...) — a row that
            # is referenced by the chunks.paper_id foreign key, which is exactly what an
            # overlapping re-ingest across topic queries would do. A PubMed record is immutable by
            # PMID, so skipping an already-present paper loses nothing; a deliberate refresh would
            # rebuild the paper together with its chunks. Returns the number of NEW papers.
            placeholders = ",".join(["?"] * len(ids))
            existing = {
                r[0]
                for r in c.execute(
                    f"SELECT id FROM papers WHERE id IN ({placeholders})", ids
                ).fetchall()
            }
            new_rows = [r for r in rows if r[0] not in existing]
            if new_rows:
                c.executemany(
                    """
                    INSERT INTO papers
                    (id, source, native_id, title, abstract, authors, journal, year,
                     publication_date, doi, pmid, pmcid, url, mesh_terms, keywords,
                     publication_types, language)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    new_rows,
                )
        return len(new_rows)

    def replace_chunks(self, paper_id: str, chunks: Iterable[Chunk]) -> int:
        rows = [
            (c.id, c.paper_id, c.index, c.section.value, c.text, c.char_start, c.char_end)
            for c in chunks
        ]
        with self.transaction() as conn:
            conn.execute("DELETE FROM chunks WHERE paper_id = ?", [paper_id])
            if rows:
                conn.executemany(
                    """
                    INSERT INTO chunks (id, paper_id, index, section, text, char_start, char_end)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
        return len(rows)

    def start_run(self, source: str, query: str | None) -> int:
        c = self.conn
        run_id = c.execute(
            "SELECT nextval('ingestion_runs_id_seq')"
        ).fetchone()[0]
        c.execute(
            "INSERT INTO ingestion_runs (id, source, query) VALUES (?, ?, ?)",
            [run_id, source, query],
        )
        return int(run_id)

    def finish_run(self, run_id: int, n_papers: int, n_chunks: int, notes: str = "") -> None:
        self.conn.execute(
            """
            UPDATE ingestion_runs
            SET finished_at = now(), n_papers = ?, n_chunks = ?, notes = ?
            WHERE id = ?
            """,
            [n_papers, n_chunks, notes, run_id],
        )

    # ----- reads -----

    def get_paper(self, paper_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM papers WHERE id = ?", [paper_id]
        ).fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self.conn.description]
        return dict(zip(cols, row, strict=False))

    def get_chunks(self, paper_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM chunks WHERE paper_id = ? ORDER BY index", [paper_id]
        ).fetchall()
        cols = [d[0] for d in self.conn.description]
        return [dict(zip(cols, r, strict=False)) for r in rows]

    def get_chunks_by_ids(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        placeholders = ",".join(["?"] * len(ids))
        q = f"""
            SELECT c.id, c.paper_id, c.index, c.section, c.text,
                   p.title, p.year, p.journal, p.url
            FROM chunks c
            JOIN papers p ON p.id = c.paper_id
            WHERE c.id IN ({placeholders})
        """
        rows = self.conn.execute(q, ids).fetchall()
        cols = [d[0] for d in self.conn.description]
        return {r[0]: dict(zip(cols, r, strict=False)) for r in rows}

    def counts(self) -> dict[str, int]:
        n_papers = self.conn.execute("SELECT count(*) FROM papers").fetchone()[0]
        n_chunks = self.conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        return {"papers": int(n_papers), "chunks": int(n_chunks)}

    # ----- watches -----

    def add_watch(
        self,
        label: str,
        query: str,
        *,
        source: str = "pubmed",
        cadence_seconds: int = 86400,
        max_per_run: int = 50,
        notes: str = "",
    ) -> int:
        c = self.conn
        watch_id = c.execute("SELECT nextval('watches_id_seq')").fetchone()[0]
        c.execute(
            """
            INSERT INTO watches (id, label, query, source, cadence_seconds, max_per_run, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [int(watch_id), label, query, source, cadence_seconds, max_per_run, notes],
        )
        return int(watch_id)

    def list_watches(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM watches"
        if enabled_only:
            sql += " WHERE enabled = true"
        sql += " ORDER BY id"
        rows = self.conn.execute(sql).fetchall()
        cols = [d[0] for d in self.conn.description]
        return [dict(zip(cols, r, strict=False)) for r in rows]

    def get_watch(self, ident: str | int) -> dict[str, Any] | None:
        # Resolve by id (numeric) or label.
        if isinstance(ident, int) or (isinstance(ident, str) and ident.isdigit()):
            row = self.conn.execute(
                "SELECT * FROM watches WHERE id = ?", [int(ident)]
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM watches WHERE label = ?", [ident]
            ).fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self.conn.description]
        return dict(zip(cols, row, strict=False))

    def remove_watch(self, ident: str | int) -> bool:
        w = self.get_watch(ident)
        if w is None:
            return False
        self.conn.execute("DELETE FROM watch_runs WHERE watch_id = ?", [w["id"]])
        self.conn.execute("DELETE FROM watches WHERE id = ?", [w["id"]])
        return True

    def set_watch_enabled(self, ident: str | int, enabled: bool) -> bool:
        w = self.get_watch(ident)
        if w is None:
            return False
        self.conn.execute(
            "UPDATE watches SET enabled = ? WHERE id = ?", [enabled, w["id"]]
        )
        return True

    def update_watch_cursor(self, watch_id: int, last_run_at, last_cursor_date) -> None:
        self.conn.execute(
            "UPDATE watches SET last_run_at = ?, last_cursor_date = ? WHERE id = ?",
            [last_run_at, last_cursor_date, watch_id],
        )

    def start_watch_run(self, watch_id: int) -> int:
        c = self.conn
        rid = c.execute("SELECT nextval('watch_runs_id_seq')").fetchone()[0]
        c.execute(
            "INSERT INTO watch_runs (id, watch_id) VALUES (?, ?)", [int(rid), watch_id]
        )
        return int(rid)

    def finish_watch_run(
        self, run_id: int, papers_added: int, chunks_added: int, error: str = ""
    ) -> None:
        self.conn.execute(
            """
            UPDATE watch_runs
            SET finished_at = now(), papers_added = ?, chunks_added = ?, error = ?
            WHERE id = ?
            """,
            [papers_added, chunks_added, error or None, run_id],
        )

    def known_paper_ids(self, ids: list[str]) -> set[str]:
        if not ids:
            return set()
        placeholders = ",".join(["?"] * len(ids))
        rows = self.conn.execute(
            f"SELECT id FROM papers WHERE id IN ({placeholders})", ids
        ).fetchall()
        return {r[0] for r in rows}

    # ----- CIViC structured evidence -----

    def upsert_civic_evidence(self, rows: Iterable[dict[str, Any]]) -> int:
        """Insert CIViC evidence rows (insert-or-ignore by eid). No FK references it, so this is
        a plain upsert. Mirrors the groundable document stored at paper_id = civic:eid<eid>."""
        tuples = [
            (
                int(r["eid"]), f"civic:eid{r['eid']}", r.get("gene"), r.get("variant"),
                r.get("disease"), r.get("doid"), list(r.get("therapies") or []),
                r.get("evidence_level"), r.get("evidence_type"), r.get("direction"),
                r.get("significance"), r.get("pmid"),
                int(r["year"]) if r.get("year") else None, r.get("url"), r.get("description"),
            )
            for r in rows
            if r.get("eid") is not None
        ]
        if not tuples:
            return 0
        with self.transaction() as c:
            c.executemany(
                """
                INSERT INTO civic_evidence
                (eid, paper_id, gene, variant, disease, doid, therapies, evidence_level,
                 evidence_type, direction, significance, pmid, year, url, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (eid) DO NOTHING
                """,
                tuples,
            )
        return len(tuples)

    def civic_match(
        self,
        *,
        gene: str | None = None,
        disease: str | None = None,
        variant: str | None = None,
        evidence_type: str | None = "PREDICTIVE",
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Structured biomarker→therapy lookup over civic_evidence, ranked by evidence level
        (A best) then recency. Powers the `match_therapies` MCP tool. Returns leveled rows.

        `variant` is a substring match on the molecular-profile name and is applied in SQL (before
        LIMIT), so narrowing by variant never silently drops matches that sort past the limit."""
        where, params = [], []
        if gene:
            where.append("lower(gene) = lower(?)")
            params.append(gene)
        if disease:
            where.append("lower(disease) LIKE lower(?)")
            params.append(f"%{disease}%")
        if variant:
            where.append("lower(variant) LIKE lower(?)")
            params.append(f"%{variant}%")
        if evidence_type:
            where.append("evidence_type = ?")
            params.append(evidence_type)
        where.append("len(therapies) > 0")  # only treatment-bearing evidence
        sql = "SELECT * FROM civic_evidence WHERE " + " AND ".join(where)
        sql += " ORDER BY evidence_level ASC, year DESC NULLS LAST LIMIT ?"
        params.append(int(limit))
        rows = self.conn.execute(sql, params).fetchall()
        cols = [d[0] for d in self.conn.description]
        return [dict(zip(cols, r, strict=False)) for r in rows]

    def civic_for_variant(self, variant: str, *, limit: int = 25) -> list[dict[str, Any]]:
        """All CIViC evidence for a variant / molecular profile (any evidence type), level-ranked."""
        if not variant:
            return []
        rows = self.conn.execute(
            "SELECT * FROM civic_evidence WHERE lower(variant) LIKE lower(?) "
            "ORDER BY evidence_level ASC, year DESC NULLS LAST LIMIT ?",
            [f"%{variant}%", int(limit)],
        ).fetchall()
        cols = [d[0] for d in self.conn.description]
        return [dict(zip(cols, r, strict=False)) for r in rows]

    def civic_counts(self) -> int:
        try:
            return int(self.conn.execute("SELECT count(*) FROM civic_evidence").fetchone()[0])
        except Exception:
            return 0


def open_store() -> DocStore:
    return DocStore()
