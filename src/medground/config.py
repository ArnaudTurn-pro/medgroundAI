"""Runtime configuration. One source of truth for paths, models, and tuning knobs.

Resolution order: real env var > `.env` file > defaults. Override anything with `MG_<NAME>`
(the legacy `CK_<NAME>` prefix is still accepted as a fallback). The standard `OPENAI_API_KEY` /
`VOYAGE_API_KEY` are read under their conventional names.

Secrets and local settings live in a `.env` at the project root. It is loaded once, here, before
any field is read — so a single `.env` feeds BOTH the CLI and the MCP server (which Claude may
spawn from a different working directory). Real environment variables always win over the file.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _load_env_files() -> None:
    """Populate os.environ from `.env` before the Config fields are evaluated.

    Searched, in order (first existing file per location loaded; never overrides a real env var):
      1. `$MG_ENV_FILE` (or legacy `$CK_ENV_FILE`) if set,
      2. the project root (two levels above this package — where pyproject.toml lives),
      3. the current working directory.
    """
    candidates: list[Path] = []
    explicit = os.environ.get("MG_ENV_FILE") or os.environ.get("CK_ENV_FILE")
    if explicit:
        candidates.append(Path(explicit))
    with contextlib.suppress(IndexError):  # defensive; the package is always nested
        candidates.append(Path(__file__).resolve().parents[2] / ".env")  # project root
    candidates.append(Path.cwd() / ".env")

    seen: set[Path] = set()
    for path in candidates:
        path = path.expanduser()
        if path in seen:
            continue
        seen.add(path)
        if path.is_file():
            load_dotenv(path, override=False)


# Load .env BEFORE the dataclass body runs (its field defaults read os.environ at import time).
_load_env_files()


def _raw(name: str) -> str | None:
    """Read an env var by MedGround's `MG_` prefix, falling back to the legacy `CK_` prefix."""
    v = os.environ.get(f"MG_{name}")
    if v is None:
        v = os.environ.get(f"CK_{name}")
    return v


def _env(name: str, default: str) -> str:
    v = _raw(name)
    return default if v is None else v


def _env_int(name: str, default: int) -> int:
    v = _raw(name)
    return default if v is None else int(v)


def _env_bool(name: str, default: bool) -> bool:
    v = _raw(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    # Storage roots
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "./data")).resolve())

    # Embeddings. Default is OpenAI text-embedding-3-large (3072-d) — top-tier quality; needs
    # OPENAI_API_KEY. See ADR-0010/0015. To switch, set provider + model + dim TOGETHER:
    #   local : MG_EMBED_PROVIDER=fastembed MG_EMBED_MODEL=BAAI/bge-small-en-v1.5 MG_EMBED_DIM=384  (no key, offline)
    #   voyage: MG_EMBED_PROVIDER=voyage    MG_EMBED_MODEL=voyage-3-large         MG_EMBED_DIM=1024
    embedding_provider: str = _env("EMBED_PROVIDER", "openai")
    embedding_model: str = _env("EMBED_MODEL", "text-embedding-3-large")
    embedding_dim: int = _env_int("EMBED_DIM", 3072)
    embedding_batch_size: int = _env_int("EMBED_BATCH", 128)

    # API keys — read from standard env names so users don't have to learn new ones.
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    voyage_api_key: str = os.environ.get("VOYAGE_API_KEY", "")

    # PubMed (NCBI E-utilities). Setting an email is requested by NCBI; tool name is a courtesy.
    ncbi_email: str = _env("NCBI_EMAIL", "")
    ncbi_api_key: str = _env("NCBI_API_KEY", "")
    ncbi_tool: str = _env("NCBI_TOOL", "medground")

    # CIViC (Clinical Interpretation of Variants in Cancer). Reads are open; the key (if set) is
    # sent as a bearer token for higher limits / writes — not required for ingestion.
    civic_api_key: str = _env("CIVIC_API_KEY", "")

    # Chunking
    chunk_chars: int = _env_int("CHUNK_CHARS", 1200)
    chunk_overlap: int = _env_int("CHUNK_OVERLAP", 150)

    # Retrieval
    default_top_k: int = _env_int("TOP_K", 8)

    # Watch loop hosted inside the MCP server (single-process owner; see ADR-0014).
    # Off by default — background ingestion embeds, which costs money. Opt in explicitly.
    watch_in_server: bool = _env_bool("WATCH_IN_SERVER", False)
    watch_tick_seconds: int = _env_int("WATCH_TICK", 300)

    # I/O
    http_timeout_s: float = float(_env("HTTP_TIMEOUT", "30"))
    http_concurrency: int = _env_int("HTTP_CONCURRENCY", 4)

    # MCP HTTP transport (`medground serve`). One shared server that many clients connect to by
    # URL, so multiple terminals / agents share a single DuckDB/KuzuDB owner. Localhost-only by
    # default — do not expose this to a network without adding auth. See ADR-0018.
    http_host: str = _env("HTTP_HOST", "127.0.0.1")
    http_port: int = _env_int("HTTP_PORT", 8765)

    @property
    def duckdb_path(self) -> Path:
        return self.data_dir / "medground.duckdb"

    @property
    def kuzu_path(self) -> Path:
        return self.data_dir / "graph.kuzu"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


CONFIG = Config()
