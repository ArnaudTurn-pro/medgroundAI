"""Regression: re-ingesting a paper that already has chunks must not fail.

`upsert_papers` used to DELETE the paper row then re-INSERT. Once the paper had chunks, the
`chunks.paper_id` foreign key rejected the DELETE (and DuckDB also can't UPDATE the list-typed
columns of an FK-referenced row), failing the whole batch — exactly what overlapping topic
ingests hit (e.g. re-querying "colon cancer chemotherapy" over a corpus that already held those
papers). The fix is insert-or-ignore: an already-present paper is a no-op (PMID records are
immutable; a refresh would be an explicit rebuild).
"""

from __future__ import annotations

from medground.models import Chunk, ChunkSection, Paper
from medground.store.docs import DocStore


def _paper(title: str, pid: str = "pubmed:1") -> Paper:
    return Paper(id=pid, source="pubmed", native_id="1", title=title)


def _chunk(pid: str = "pubmed:1") -> Chunk:
    return Chunk(id=f"{pid}#0", paper_id=pid, index=0, section=ChunkSection.TITLE, text="x")


def test_reingest_existing_paper_with_chunks_is_noop_not_crash(tmp_path):
    docs = DocStore(path=tmp_path / "t.duckdb")
    n_new = docs.upsert_papers([_paper("Original title")])
    docs.replace_chunks("pubmed:1", [_chunk()])  # paper now has a child chunk (FK in play)
    assert n_new == 1

    # Previously raised "Violates foreign key constraint". Now: a clean no-op (0 new).
    n_again = docs.upsert_papers([_paper("Different title, same id")])

    assert n_again == 0  # already present → skipped, not re-written
    assert docs.counts()["papers"] == 1  # no duplicate row
    assert docs.get_paper("pubmed:1")["title"] == "Original title"  # untouched (immutable by id)
    assert len(docs.get_chunks("pubmed:1")) == 1  # child chunk preserved
    docs.close()


def test_upsert_returns_count_of_new_papers_only(tmp_path):
    docs = DocStore(path=tmp_path / "t2.duckdb")
    assert docs.upsert_papers([_paper("A", "pubmed:1"), _paper("B", "pubmed:2")]) == 2
    # one existing + one new → only the new one counts
    assert docs.upsert_papers([_paper("A", "pubmed:1"), _paper("C", "pubmed:3")]) == 1
    assert docs.counts()["papers"] == 3
    docs.close()


def _civic_row(eid, variant, level, therapy, **kw):
    return {
        "eid": eid, "gene": "EGFR", "variant": variant, "disease": "Lung Non-small Cell Carcinoma",
        "therapies": [therapy], "evidence_level": level, "evidence_type": "PREDICTIVE",
        "direction": "SUPPORTS", "significance": "SENSITIVITYRESPONSE", "pmid": str(eid),
        "year": 2020, "url": f"u{eid}", "description": f"d{eid}", **kw,
    }


def test_civic_match_variant_filter_applies_before_limit(tmp_path):
    """Regression (ADR-0017): the variant filter must run in SQL, before LIMIT.

    It used to be a Python post-filter applied to the already-LIMITed rows, so a matching variant
    that sorted past `limit` (here, a Level-B item outranked by a Level-A one) was silently dropped.
    """
    docs = DocStore(path=tmp_path / "civic.duckdb")
    docs.upsert_civic_evidence([
        _civic_row(1, "EGFR L858R", "A", "Osimertinib"),   # outranks the T790M row (Level A < B)
        _civic_row(2, "EGFR T790M", "B", "Erlotinib"),
    ])
    # With limit=1 the unfiltered top row is L858R; the old post-LIMIT filter then returned nothing.
    rows = docs.civic_match(gene="EGFR", variant="T790M", limit=1)
    assert [r["eid"] for r in rows] == [2]
    assert rows[0]["therapies"] == ["Erlotinib"]
    docs.close()
