"""Embedding service — provider-agnostic facade. Off-the-shelf SDKs, swap by config.

Providers (set via env `MG_EMBED_PROVIDER`):
  - "openai" (DEFAULT) → text-embedding-3-large (3072d) via the official OpenAI SDK. Top-tier
                quality; needs OPENAI_API_KEY.
  - "fastembed" → local ONNX model (bge-small-en-v1.5, 384d). No API key, no quota, offline after
                a one-time download; small and CPU-fast. The zero-cost option. See ADR-0015.
  - "voyage"    → voyage-3-large (1024d) via voyageai SDK (Anthropic's partner; biomedical-strong).
                Needs VOYAGE_API_KEY.

Switching provider means switching model + dim together (see config.py) — the vector index is
dim-specific, so `medground reembed` recreates it on a dim change. We always L2-normalize the
output so cosine == dot product, regardless of provider.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from functools import cached_property
from typing import Protocol

import numpy as np

from medground.config import CONFIG

# Voyage's recommended query/document prefixes (improves retrieval on their models).
_VOYAGE_QUERY_INPUT_TYPE = "query"
_VOYAGE_DOC_INPUT_TYPE = "document"


class _Provider(Protocol):
    dim: int

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...
    def embed_queries(self, texts: Sequence[str]) -> np.ndarray: ...


def _l2_normalize(arr: np.ndarray) -> np.ndarray:
    arr = arr.astype(np.float32, copy=False)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


def _batched(seq: Sequence[str], n: int) -> Iterable[Sequence[str]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


class FastEmbedEmbedder:
    """Local ONNX embeddings via fastembed. No API key, no quota; offline after first download.

    The model (default bge-small-en-v1.5, 384d) downloads once to a local cache, then every
    embed call runs on-device. bge uses an asymmetric query prefix, so queries go through
    `query_embed` and passages through `embed`.
    """

    def __init__(self, model: str, dim: int, batch_size: int) -> None:
        self.model = model
        self.dim = dim
        self.batch_size = batch_size

    @cached_property
    def _impl(self):
        try:
            from fastembed import TextEmbedding
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "fastembed is not installed. `uv pip install fastembed`, or switch provider "
                "via MG_EMBED_PROVIDER=openai|voyage."
            ) from e
        return TextEmbedding(model_name=self.model)

    def _to_array(self, vecs) -> np.ndarray:
        return _l2_normalize(np.asarray(list(vecs), dtype=np.float32))

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return self._to_array(self._impl.embed(list(texts), batch_size=self.batch_size))

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_queries([text])[0]

    def embed_queries(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        query_embed = getattr(self._impl, "query_embed", None)
        vecs = query_embed(list(texts)) if query_embed else self._impl.embed(list(texts))
        return self._to_array(vecs)


class OpenAIEmbedder:
    """OpenAI text-embedding-3-* family."""

    def __init__(self, model: str, dim: int, batch_size: int) -> None:
        self.model = model
        self.dim = dim
        self.batch_size = batch_size

    @cached_property
    def _client(self):
        from openai import OpenAI

        if not CONFIG.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Export it or switch provider via MG_EMBED_PROVIDER."
            )
        return OpenAI(api_key=CONFIG.openai_api_key)

    def _embed(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        out: list[list[float]] = []
        for batch in _batched(texts, self.batch_size):
            # `dimensions` is supported by text-embedding-3-* and lets us match the LanceDB schema.
            resp = self._client.embeddings.create(
                model=self.model, input=list(batch), dimensions=self.dim
            )
            out.extend(d.embedding for d in resp.data)
        return _l2_normalize(np.asarray(out, dtype=np.float32))

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        return self._embed(texts)

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([text])[0]

    def embed_queries(self, texts: Sequence[str]) -> np.ndarray:
        return self._embed(texts)  # text-embedding-3-* is symmetric: query == document encoding


class VoyageEmbedder:
    """Voyage AI — Anthropic's recommended embedding partner. Strong on biomedical & code."""

    def __init__(self, model: str, dim: int, batch_size: int) -> None:
        self.model = model
        self.dim = dim
        self.batch_size = batch_size

    @cached_property
    def _client(self):
        import voyageai

        if not CONFIG.voyage_api_key:
            raise RuntimeError(
                "VOYAGE_API_KEY is not set. Export it or switch provider via MG_EMBED_PROVIDER."
            )
        return voyageai.Client(api_key=CONFIG.voyage_api_key)

    def _embed(self, texts: Sequence[str], input_type: str) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        out: list[list[float]] = []
        # Voyage caps payloads at ~120 inputs and ~120k tokens/request; our batch_size is safe.
        for batch in _batched(texts, self.batch_size):
            resp = self._client.embed(list(batch), model=self.model, input_type=input_type)
            out.extend(resp.embeddings)
        return _l2_normalize(np.asarray(out, dtype=np.float32))

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        return self._embed(texts, _VOYAGE_DOC_INPUT_TYPE)

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([text], _VOYAGE_QUERY_INPUT_TYPE)[0]

    def embed_queries(self, texts: Sequence[str]) -> np.ndarray:
        return self._embed(texts, _VOYAGE_QUERY_INPUT_TYPE)


def _build_provider() -> _Provider:
    provider = CONFIG.embedding_provider.lower()
    args = (CONFIG.embedding_model, CONFIG.embedding_dim, CONFIG.embedding_batch_size)
    if provider in ("fastembed", "local", "bge"):
        return FastEmbedEmbedder(*args)
    if provider == "openai":
        return OpenAIEmbedder(*args)
    if provider == "voyage":
        return VoyageEmbedder(*args)
    raise ValueError(
        f"unknown embedding provider: {provider!r}. "
        "Set MG_EMBED_PROVIDER to 'fastembed', 'openai', or 'voyage'."
    )


class Embedder:
    """Public facade. Keeps the rest of the codebase provider-agnostic."""

    def __init__(self) -> None:
        self._impl = _build_provider()

    @property
    def dim(self) -> int:
        return self._impl.dim

    def embed_passages(self, texts: Iterable[str], batch_size: int | None = None) -> np.ndarray:
        # batch_size arg kept for back-compat; provider already batches internally.
        return self._impl.embed_documents(list(texts))

    def embed_query(self, text: str) -> np.ndarray:
        return self._impl.embed_query(text)

    def embed_queries(self, texts: Iterable[str]) -> np.ndarray:
        """Batch-embed several queries in ONE provider call (multi-facet / multi-claim retrieval)."""
        return self._impl.embed_queries(list(texts))


_EMBEDDER: Embedder | None = None


def get_embedder() -> Embedder:
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = Embedder()
    return _EMBEDDER
