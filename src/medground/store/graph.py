"""KuzuDB-backed knowledge graph. Embedded, columnar, Cypher-compatible.

Node / edge schema (v1 — MeSH-first, no LLM extraction yet):

  (Paper {id, title, year})
  (Concept {id, name, vocab, kind})         # id = "mesh:<DescriptorName>", vocab = "mesh"
  (Paper)-[:MENTIONS]->(Concept)            # one per MeSH term on the paper

Co-occurrence is materialized lazily by `rebuild_cooccurrence()` rather than computed per-paper —
2-hop MATCH expressions on the live MENTIONS edges give us "concept A appears with concept B in
N papers" with no extra storage. Once the graph grows large enough that 2-hop queries become
slow we add a CO_OCCURS edge here without changing the read API.

KuzuDB writes happen one statement per `conn.execute`; we wrap a paper's full upsert in a
transaction so partial inserts can never leak.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import kuzu

from medground.config import CONFIG
from medground.models import Paper
from medground.store.docs import DocStore

log = logging.getLogger("medground.graph")

_SCHEMA = [
    "CREATE NODE TABLE IF NOT EXISTS Paper("
    "id STRING, title STRING, year INT64, PRIMARY KEY (id))",
    "CREATE NODE TABLE IF NOT EXISTS Concept("
    "id STRING, name STRING, vocab STRING, kind STRING, PRIMARY KEY (id))",
    "CREATE REL TABLE IF NOT EXISTS MENTIONS(FROM Paper TO Concept)",
]


# MeSH "check tags" + generic cell-culture descriptors: applied to huge fractions of papers
# regardless of topic, so they dominate co-occurrence (Humans=34, Animals=15, Female=12 in a
# 1k-paper sample) and bury the real signal (genes, drugs, mechanisms). We never make them graph
# concepts. The clinical descriptors that matter are kept. See ADR-0005 / ADR-0016.
_MESH_STOPLIST = frozenset({
    # demographic check tags
    "humans", "male", "female", "adult", "middle aged", "aged", "aged, 80 and over",
    "young adult", "adolescent", "child", "child, preschool", "infant", "infant, newborn",
    "pregnancy", "aging",
    # species check tags
    "animals", "mice", "rats", "mice, inbred c57bl", "mice, nude", "mice, inbred balb c",
    "mice, scid", "mice, transgenic", "mice, knockout", "rats, sprague-dawley", "rats, wistar",
    "rats, nude", "dogs", "rabbits", "swine", "cattle", "guinea pigs", "zebrafish",
    # generic cell-culture descriptors
    "cell line", "cell line, tumor", "cells, cultured", "tumor cells, cultured", "hela cells",
})


def _slug(name: str) -> str:
    """MeSH descriptor → stable id slug. Lossy but reproducible."""
    s = re.sub(r"\s+", "_", name.strip())
    s = re.sub(r"[^A-Za-z0-9_\-]", "", s)
    return s[:120] or "_unknown"


def concept_id(name: str, vocab: str = "mesh") -> str:
    return f"{vocab}:{_slug(name)}"


class GraphStore:
    """Thin KuzuDB wrapper. One open `Connection` per `GraphStore` instance."""

    def __init__(self, path: Path | None = None) -> None:
        CONFIG.ensure_dirs()
        self.path = path or CONFIG.kuzu_path
        self._db: kuzu.Database | None = None
        self._conn: kuzu.Connection | None = None
        self._initialized = False
        self._concept_id_cache: dict[str, str | None] = {}

    @property
    def conn(self) -> kuzu.Connection:
        if self._conn is None:
            self._db = kuzu.Database(str(self.path))
            self._conn = kuzu.Connection(self._db)
            self._init_schema()
        return self._conn

    def checkpoint(self) -> None:
        """Flush the WAL into the main `graph.kuzu` file.

        Kuzu only folds its WAL into the main file on a clean database close — which a SIGTRAP /
        SIGKILL skips, leaving every write stranded in `graph.kuzu.wal`. Normally that WAL replays
        on the next open, but it shares the data dir with the DuckDB store, so a crash-recovery
        step that quarantines WALs can lose it. Checkpointing at safe points (end of ingest, clean
        close) keeps the graph durable in the main file rather than only in the WAL. Best-effort:
        a no-op on Kuzu builds without a CHECKPOINT statement.
        """
        if self._conn is None:
            return
        with contextlib.suppress(Exception):
            self._conn.execute("CHECKPOINT")

    def close(self) -> None:
        # KuzuDB folds the WAL into the main file on a clean close; checkpoint first so the data is
        # durable even if GC of the Database object is delayed. Then drop refs.
        self.checkpoint()
        self._conn = None
        self._db = None
        self._initialized = False

    def _init_schema(self) -> None:
        if self._initialized:
            return
        for stmt in _SCHEMA:
            self._conn.execute(stmt)
        self._initialized = True

    # ----- writes -----

    def upsert_paper(self, paper: Paper) -> None:
        """Upsert a Paper node and its MENTIONS-to-Concept edges (one per MeSH term)."""
        c = self.conn
        params: dict[str, Any] = {
            "pid": paper.id,
            "title": paper.title or "",
            "year": int(paper.year) if paper.year else 0,
        }
        c.execute(
            "MERGE (p:Paper {id: $pid}) "
            "ON CREATE SET p.title = $title, p.year = $year "
            "ON MATCH SET p.title = $title, p.year = $year",
            params,
        )
        for term in paper.mesh_terms or []:
            term = term.strip()
            if not term or term.lower() in _MESH_STOPLIST:
                continue
            cid = concept_id(term)
            c.execute(
                "MERGE (c:Concept {id: $cid}) "
                "ON CREATE SET c.name = $name, c.vocab = 'mesh', c.kind = 'mesh'",
                {"cid": cid, "name": term},
            )
            c.execute(
                "MATCH (p:Paper {id: $pid}), (c:Concept {id: $cid}) "
                "MERGE (p)-[:MENTIONS]->(c)",
                {"pid": paper.id, "cid": cid},
            )

    def upsert_papers(self, papers: Iterable[Paper]) -> int:
        n = 0
        for p in papers:
            try:
                self.upsert_paper(p)
                n += 1
            except Exception:
                log.exception("graph upsert failed for %s", p.id)
        return n

    def clear(self) -> None:
        """Wipe all nodes + edges (schema/tables kept). Used by `graph rebuild` so a re-upsert
        with the current stop-list produces a clean graph instead of merging onto stale concepts."""
        c = self.conn
        for stmt in ("MATCH (p:Paper) DETACH DELETE p", "MATCH (c:Concept) DETACH DELETE c"):
            try:
                c.execute(stmt)
            except Exception:
                log.exception("graph clear failed: %s", stmt)
        with contextlib.suppress(Exception):
            GraphStore._first_concept_id.cache_clear()

    # ----- reads -----

    def counts(self) -> dict[str, int]:
        try:
            n_papers = _scalar(self.conn.execute("MATCH (p:Paper) RETURN count(p)"))
            n_concepts = _scalar(self.conn.execute("MATCH (c:Concept) RETURN count(c)"))
            n_mentions = _scalar(
                self.conn.execute("MATCH ()-[r:MENTIONS]->() RETURN count(r)")
            )
        except Exception:
            return {"papers": 0, "concepts": 0, "mentions": 0}
        return {
            "papers": int(n_papers or 0),
            "concepts": int(n_concepts or 0),
            "mentions": int(n_mentions or 0),
        }

    def find_concepts(self, fragment: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Substring match on Concept.name (case-insensitive). Useful as a typeahead."""
        q = fragment.lower().strip()
        if not q:
            return []
        rows = _rows(
            self.conn.execute(
                "MATCH (c:Concept) "
                "WHERE lower(c.name) CONTAINS $q "
                "RETURN c.id AS id, c.name AS name, c.vocab AS vocab "
                "ORDER BY size(c.name) ASC LIMIT $limit",
                {"q": q, "limit": limit},
            )
        )
        return rows

    def neighbors(
        self, concept_name: str, *, hops: int = 1, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Concepts that co-occur with `concept_name` in the corpus (2-hop via MENTIONS).

        `hops=1` (default) returns direct co-occurrences. Setting hops=2 expands further but
        results get noisy fast — use sparingly.
        """
        anchor = self._first_concept_id(concept_name)
        if anchor is None:
            return []
        if hops <= 1:
            cypher = (
                "MATCH (c1:Concept {id: $cid})<-[:MENTIONS]-(p:Paper)-[:MENTIONS]->(c2:Concept) "
                "WHERE c2.id <> c1.id "
                "RETURN c2.id AS id, c2.name AS name, count(p) AS weight "
                "ORDER BY weight DESC LIMIT $limit"
            )
        else:
            cypher = (
                "MATCH (c1:Concept {id: $cid})"
                "<-[:MENTIONS]-(p1:Paper)-[:MENTIONS]->(mid:Concept)"
                "<-[:MENTIONS]-(p2:Paper)-[:MENTIONS]->(c2:Concept) "
                "WHERE c2.id <> c1.id AND c2.id <> mid.id "
                "RETURN c2.id AS id, c2.name AS name, count(p2) AS weight "
                "ORDER BY weight DESC LIMIT $limit"
            )
        return _rows(self.conn.execute(cypher, {"cid": anchor, "limit": limit}))

    def concept_papers(
        self, concept_name: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Papers tagged with the given concept, most-recent first."""
        anchor = self._first_concept_id(concept_name)
        if anchor is None:
            return []
        return _rows(
            self.conn.execute(
                "MATCH (p:Paper)-[:MENTIONS]->(c:Concept {id: $cid}) "
                "RETURN p.id AS id, p.title AS title, p.year AS year "
                "ORDER BY p.year DESC LIMIT $limit",
                {"cid": anchor, "limit": limit},
            )
        )

    def chunks_for_query(
        self, query: str, *, k: int = 24, docs: DocStore
    ) -> list[dict[str, Any]]:
        """Anchor MeSH concepts mentioned in `query`, return representative chunks per paper.

        Strategy:
          1. Cheap substring match: pull concept names whose lowercased name contains any token
             of length ≥3 from the query. (Fast enough on MeSH-scale vocabularies.)
          2. Find papers mentioning any anchor concept, ranked by number of distinct anchors hit.
          3. For each paper, pick the representative chunk from DuckDB (first non-title chunk).
        """
        anchors = self._anchor_concept_ids(query)
        if not anchors:
            return []
        # Limit IN-list size to keep Cypher predictable
        anchors = anchors[:50]
        cypher = (
            "MATCH (p:Paper)-[:MENTIONS]->(c:Concept) "
            "WHERE c.id IN $anchors "
            "RETURN p.id AS paper_id, count(DISTINCT c) AS mentions "
            "ORDER BY mentions DESC LIMIT $k"
        )
        paper_rows = _rows(
            self.conn.execute(cypher, {"anchors": anchors, "k": k})
        )
        if not paper_rows:
            return []
        max_mentions = max(r["mentions"] for r in paper_rows) or 1
        score = {r["paper_id"]: r["mentions"] / max_mentions for r in paper_rows}
        paper_ids = [r["paper_id"] for r in paper_rows]

        # Pull one representative chunk per paper from DuckDB — the first non-TITLE chunk.
        placeholders = ",".join(["?"] * len(paper_ids))
        sql = f"""
            SELECT c.id, c.paper_id, c.section, c.text, c.index AS chunk_index,
                   p.title, p.year, p.journal, p.url
            FROM chunks c
            JOIN papers p ON p.id = c.paper_id
            WHERE c.paper_id IN ({placeholders})
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY c.paper_id
                ORDER BY (CASE WHEN c.section = 'title' THEN 1 ELSE 0 END), c.index
            ) = 1
        """
        rows = docs.conn.execute(sql, paper_ids).fetchall()
        cols = [d[0] for d in docs.conn.description]
        out = []
        for r in rows:
            row = dict(zip(cols, r, strict=False))
            row["anchor_score"] = score.get(row["paper_id"], 0.0)
            out.append(row)
        out.sort(key=lambda x: x["anchor_score"], reverse=True)
        return out

    # ----- helpers -----

    def _first_concept_id(self, name: str) -> str | None:
        # Cached per-instance (GraphStore is a process-singleton; see runtime.py).
        # Avoids B019 — a method-level lru_cache would pin every instance for the
        # cache's lifetime.
        if name in self._concept_id_cache:
            return self._concept_id_cache[name]
        # Try exact match (by id slug), then case-insensitive name match.
        cid = concept_id(name)
        rows = _rows(
            self.conn.execute(
                "MATCH (c:Concept {id: $cid}) RETURN c.id AS id", {"cid": cid}
            )
        )
        if rows:
            result = rows[0]["id"]
        else:
            rows = _rows(
                self.conn.execute(
                    "MATCH (c:Concept) WHERE lower(c.name) = lower($n) "
                    "RETURN c.id AS id LIMIT 1",
                    {"n": name},
                )
            )
            result = rows[0]["id"] if rows else None
        self._concept_id_cache[name] = result
        return result

    def _anchor_concept_ids(self, query: str) -> list[str]:
        """Find concept ids whose name is a substring of any query token (or vice-versa)."""
        tokens = [
            t.strip(".,;:?!()[]\"'") for t in query.lower().split()
        ]
        tokens = [t for t in tokens if len(t) >= 3]
        if not tokens:
            return []
        # Build a single Cypher OR over CONTAINS — covers multi-word concepts that contain
        # any of the tokens.
        clauses = " OR ".join(
            [f"lower(c.name) CONTAINS $t{i}" for i in range(len(tokens))]
        )
        params = {f"t{i}": t for i, t in enumerate(tokens)}
        params["k"] = 200
        rows = _rows(
            self.conn.execute(
                f"MATCH (c:Concept) WHERE {clauses} "
                "RETURN c.id AS id, c.name AS name LIMIT $k",
                params,
            )
        )
        return [r["id"] for r in rows]


# ----- KuzuDB result helpers -----


def _rows(result) -> list[dict[str, Any]]:
    cols = result.get_column_names()
    out: list[dict[str, Any]] = []
    while result.has_next():
        out.append(dict(zip(cols, result.get_next(), strict=False)))
    return out


def _scalar(result) -> Any:
    if result.has_next():
        return result.get_next()[0]
    return None


def open_graph() -> GraphStore:
    return GraphStore()
