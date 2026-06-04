"""Tests for the groundedness verifier — the enforcement floor behind ADR-0007/0013.

`verify_claims` is a pure function whose only dependency is `docs.known_paper_ids(ids)`, so we
stub that with an in-memory set: no DuckDB, no network, fast and deterministic. These lock down
the contract that matters most — a fabricated or missing citation must never read as grounded.
"""

from __future__ import annotations

from medground.retrieve.grounding import (
    STATUS_GROUNDED,
    STATUS_OFF_ENVELOPE,
    STATUS_PHANTOM,
    STATUS_UNCITED,
    verify_claims,
)


class FakeDocs:
    """Minimal stand-in for DocStore: only `known_paper_ids` is used by verify_claims."""

    def __init__(self, existing: set[str]) -> None:
        self._existing = set(existing)

    def known_paper_ids(self, ids: list[str]) -> set[str]:
        return {i for i in ids if i in self._existing}


CORPUS = {"pubmed:1", "pubmed:2", "pubmed:3"}


def _statuses(report: dict) -> list[str]:
    return [c["status"] for c in report["claims"]]


def test_grounded_claim_with_real_in_envelope_citation():
    docs = FakeDocs(CORPUS)
    report = verify_claims(
        [{"text": "A real claim.", "citations": ["pubmed:1"]}],
        docs=docs,
        allowed_paper_ids=["pubmed:1", "pubmed:2"],
    )
    assert report["grounded"] is True
    assert report["grounded_ratio"] == 1.0
    assert _statuses(report) == [STATUS_GROUNDED]
    assert report["violations"] == []


def test_uncited_claim_is_a_violation():
    docs = FakeDocs(CORPUS)
    report = verify_claims([{"text": "No source here.", "citations": []}], docs=docs)
    assert report["grounded"] is False
    assert _statuses(report) == [STATUS_UNCITED]


def test_phantom_citation_not_in_corpus():
    docs = FakeDocs(CORPUS)
    report = verify_claims(
        [{"text": "Cites a fabricated id.", "citations": ["pubmed:99999999"]}], docs=docs
    )
    assert report["grounded"] is False
    assert _statuses(report) == [STATUS_PHANTOM]
    # the offending id is surfaced for the agent to repair
    assert "pubmed:99999999" in report["violations"][0]["problems"][0]


def test_off_envelope_real_paper_but_not_retrieved():
    docs = FakeDocs(CORPUS)
    report = verify_claims(
        [{"text": "Real paper, but not one I retrieved.", "citations": ["pubmed:3"]}],
        docs=docs,
        allowed_paper_ids=["pubmed:1", "pubmed:2"],
    )
    assert report["grounded"] is False
    assert _statuses(report) == [STATUS_OFF_ENVELOPE]


def test_off_envelope_only_applies_when_envelope_given():
    """Without an envelope, any real-in-corpus citation is grounded (existence check only)."""
    docs = FakeDocs(CORPUS)
    report = verify_claims(
        [{"text": "Real paper, no envelope passed.", "citations": ["pubmed:3"]}], docs=docs
    )
    assert report["grounded"] is True
    assert _statuses(report) == [STATUS_GROUNDED]
    assert report["checked_against"] == "corpus"


def test_phantom_takes_precedence_over_off_envelope():
    docs = FakeDocs(CORPUS)
    report = verify_claims(
        [{"text": "One real-but-off-envelope and one fabricated.", "citations": ["pubmed:3", "pubmed:nope"]}],
        docs=docs,
        allowed_paper_ids=["pubmed:1"],
    )
    # a fabricated id is the more severe problem and wins the status
    assert _statuses(report) == [STATUS_PHANTOM]


def test_mixed_batch_ratio_and_violations():
    docs = FakeDocs(CORPUS)
    report = verify_claims(
        [
            {"text": "Good.", "citations": ["pubmed:1"]},
            {"text": "Bad.", "citations": ["pubmed:404"]},
            {"text": "Orphan.", "citations": []},
            {"text": "Also good.", "citations": ["pubmed:2"]},
        ],
        docs=docs,
    )
    assert report["n_claims"] == 4
    assert report["grounded"] is False
    assert report["grounded_ratio"] == 0.5
    assert len(report["violations"]) == 2


def test_claim_shape_aliases_and_bare_strings_are_tolerated():
    docs = FakeDocs(CORPUS)
    report = verify_claims(
        [
            {"claim": "alias for text", "paper_ids": "pubmed:1"},  # claim+paper_ids+scalar
            "a bare string with no citation",
        ],
        docs=docs,
    )
    assert _statuses(report) == [STATUS_GROUNDED, STATUS_UNCITED]


def test_empty_input_is_not_grounded():
    docs = FakeDocs(CORPUS)
    report = verify_claims([], docs=docs)
    assert report["grounded"] is False
    assert report["n_claims"] == 0
    assert report["grounded_ratio"] == 0.0
