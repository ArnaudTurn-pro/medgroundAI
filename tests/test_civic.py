"""Tests for the CIViC source adapter — normalization, gene derivation, Paper mapping (ADR-0017).

Hermetic: operates on a captured GraphQL node shape (no network, no DB).
"""

from __future__ import annotations

from medground.sources import civic

# Shape captured from the live CIViC GraphQL API (evidenceItems node).
SAMPLE = {
    "id": 238,
    "evidenceLevel": "A",
    "evidenceType": "PREDICTIVE",
    "evidenceDirection": "SUPPORTS",
    "significance": "RESISTANCE",
    "status": "ACCEPTED",
    "description": "EGFR T790M confers resistance to first-generation EGFR TKIs.",
    "molecularProfile": {"name": "EGFR T790M"},
    "disease": {"name": "Lung Non-small Cell Carcinoma", "doid": "3908"},
    "therapies": [{"name": "Erlotinib", "ncitId": "C65530"}],
    "source": {"citationId": "25668228", "sourceType": "PUBMED", "publicationYear": 2015},
}


def test_normalize_extracts_structured_fields():
    ev = civic.normalize(SAMPLE)
    assert ev["eid"] == 238
    assert ev["gene"] == "EGFR"
    assert ev["variant"] == "EGFR T790M"
    assert ev["disease"] == "Lung Non-small Cell Carcinoma"
    assert ev["doid"] == "3908"
    assert ev["therapies"] == ["Erlotinib"]
    assert ev["evidence_level"] == "A"
    assert ev["significance"] == "RESISTANCE"
    assert ev["pmid"] == "25668228"  # the join key into the paper corpus
    assert ev["year"] == 2015


def test_to_paper_maps_for_pipeline_and_grounding():
    p = civic.to_paper(civic.normalize(SAMPLE))
    assert p.id == "civic:eid238"
    assert p.source == "civic"
    assert p.pmid == "25668228"
    # entities become graph concepts; gene + therapy present
    assert "EGFR" in p.mesh_terms
    assert "Erlotinib" in p.mesh_terms
    # title is a readable, groundable citation
    assert "resistance" in p.title.lower()
    assert "Erlotinib" in p.title
    # the structured record rides along for the civic_evidence table
    assert isinstance(p.raw, dict) and p.raw["eid"] == 238


def test_non_pubmed_source_yields_no_pmid():
    node = {**SAMPLE, "source": {"citationId": "9999", "sourceType": "ASCO", "publicationYear": 2021}}
    assert civic.normalize(node)["pmid"] is None


def test_gene_derivation_keeps_fusions_whole():
    node = {**SAMPLE, "molecularProfile": {"name": "BCR::ABL1 Fusion"}}
    assert civic.normalize(node)["gene"] == "BCR::ABL1"


def test_significance_prettified_in_title():
    node = {**SAMPLE, "significance": "SENSITIVITYRESPONSE"}
    title = civic.to_paper(civic.normalize(node)).title
    assert "sensitivity/response" in title  # not the raw "SENSITIVITYRESPONSE"
