"""Core domain types. Pydantic v2 everywhere — schemas double as serialization.

Design notes:
  - `Paper.id` is the canonical identifier across sources, formatted "<source>:<native_id>"
    (e.g. "pubmed:39281234"). The native id stays parseable and uniqueness is global.
  - `Chunk.id` is "<paper_id>#<index>"; ordering preserved by `index`.
  - `Entity` is a typed node (gene, drug, disease, ...). `source` records provenance so we can
    later reconcile MeSH vs UMLS vs ad-hoc LLM extractions without losing the trail.
  - `Edge` carries `evidence_chunk_ids`: every relation must point at the chunks that warrant it.
    Groundedness is structural, not aspirational.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Source = Literal[
    "pubmed", "europepmc", "clinicaltrials", "openalex", "biorxiv", "medrxiv", "civic"
]


class Paper(BaseModel):
    """A research article record."""

    model_config = ConfigDict(extra="ignore")

    id: str  # canonical, e.g. "pubmed:39281234"
    source: Source
    native_id: str  # PMID, DOI, NCT, OpenAlex W-id...
    title: str
    abstract: str = ""
    authors: list[str] = Field(default_factory=list)
    journal: str | None = None
    year: int | None = None
    publication_date: date | None = None
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    url: str | None = None
    mesh_terms: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    publication_types: list[str] = Field(default_factory=list)
    language: str | None = None
    raw: dict | None = None  # source-specific payload, kept for reprocessing


class ChunkSection(StrEnum):
    TITLE = "title"
    ABSTRACT = "abstract"
    BACKGROUND = "background"
    METHODS = "methods"
    RESULTS = "results"
    CONCLUSIONS = "conclusions"
    BODY = "body"
    OTHER = "other"


class Chunk(BaseModel):
    """A retrievable text span with full provenance."""

    model_config = ConfigDict(extra="ignore")

    id: str  # "<paper_id>#<index>"
    paper_id: str
    index: int
    section: ChunkSection
    text: str
    char_start: int = 0
    char_end: int = 0


class EntityKind(StrEnum):
    GENE = "gene"
    PROTEIN = "protein"
    DRUG = "drug"
    DISEASE = "disease"
    CONDITION = "condition"
    BIOMARKER = "biomarker"
    PROCEDURE = "procedure"
    PHENOTYPE = "phenotype"
    CHEMICAL = "chemical"
    ANATOMY = "anatomy"
    OTHER = "other"


class Entity(BaseModel):
    """A KG node. `id` is "<vocab>:<code>" when linked (e.g. "mesh:D001943"); otherwise a slug."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    kind: EntityKind = EntityKind.OTHER
    aliases: list[str] = Field(default_factory=list)
    vocab: str | None = None  # mesh, umls, ncbi-gene, drugbank...


class Edge(BaseModel):
    """A typed, evidenced relation between two entities."""

    model_config = ConfigDict(extra="ignore")

    src: str  # Entity.id
    dst: str  # Entity.id
    relation: str  # e.g. "treats", "inhibits", "co_mentioned_with"
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    weight: float = 1.0


class RetrievalHit(BaseModel):
    """One chunk returned from retrieval, with score and provenance for citation."""

    model_config = ConfigDict(extra="ignore")

    chunk_id: str
    paper_id: str
    score: float
    section: ChunkSection
    text: str
    title: str
    year: int | None = None
    journal: str | None = None
    url: str | None = None
