"""Process-wide store ownership and serialization.

The embedded stores (DuckDB, KuzuDB) are single-writer, and their connection objects are not
thread-safe. FastMCP runs sync tools in a worker-thread pool, and — when enabled — a watch loop
runs in the background. So several threads can reach the stores at once, and two OS processes
opening the same files at once fail outright (the file lock is exclusive; verified).

This module makes the process the single, safe owner of the data dir:

  - **one connection per store**, opened once and reused (no per-call open / extension reload);
  - **one re-entrant lock** (`DB_LOCK`) serializing every store touch.

Serializing reads as well as writes is cheap at our scale (queries are milliseconds, one user)
and removes a whole class of races. Work that must NOT hold the lock — network fetches, embedding
API calls — stays outside it by construction: callers acquire the lock only around the DB op.

Because there is exactly one owner process, the watch loop runs INSIDE the MCP server (see the
lifespan in `mcp/server.py`) instead of as a separate `medground watch daemon` process that
would fail to acquire the file lock. See ADR-0014.
"""

from __future__ import annotations

import contextlib
import functools
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from medground.config import CONFIG
from medground.store.docs import DocStore
from medground.store.graph import GraphStore
from medground.store.lexical import LexicalStore
from medground.store.vectors import VectorStore

# Re-entrant so a locked entry point can call another without self-deadlock.
DB_LOCK = threading.RLock()

_init_lock = threading.Lock()  # guards lazy singleton construction only
_docs: DocStore | None = None
_vectors: VectorStore | None = None
_lexical: LexicalStore | None = None
_graph: GraphStore | None = None


def get_docs() -> DocStore:
    global _docs
    if _docs is None:
        with _init_lock:
            if _docs is None:
                _docs = DocStore()
    return _docs


def get_vectors() -> VectorStore:
    global _vectors
    if _vectors is None:
        with _init_lock:
            if _vectors is None:
                _vectors = VectorStore(docs=get_docs())
    return _vectors


def get_lexical() -> LexicalStore:
    global _lexical
    if _lexical is None:
        with _init_lock:
            if _lexical is None:
                _lexical = LexicalStore(docs=get_docs())
    return _lexical


def get_graph() -> GraphStore:
    global _graph
    if _graph is None:
        with _init_lock:
            if _graph is None:
                _graph = GraphStore()
    return _graph


F = TypeVar("F", bound=Callable[..., object])


def locked(fn: F) -> F:
    """Serialize a callable's body under `DB_LOCK`. Use on sync entry points that touch the DB.

    Preserves the wrapped signature (so FastMCP still derives the right tool schema)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with DB_LOCK:
            return fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def reset() -> None:
    """Close and drop all singletons. For tests and CLI teardown; not used in steady state."""
    global _docs, _vectors, _lexical, _graph
    with _init_lock:
        for store in (_docs, _graph):
            if store is not None:
                with contextlib.suppress(Exception):
                    store.close()
        _docs = _vectors = _lexical = _graph = None


# ----- single-owner cross-process lock (ADR-0018) -------------------------------------------
# DuckDB/KuzuDB are single-writer: two OS processes opening the same data dir fail. We make
# ownership explicit and fail-fast — the first medground process takes an exclusive OS lock on
# the data dir; a second is refused *cleanly* instead of dying on a confusing DuckDB lock error.
# The lock auto-releases on process exit. No-op where `fcntl` is unavailable (non-Unix), where
# we fall back to the store's own exclusive file lock.

_owner_lock_fd: int | None = None


class DataDirLocked(RuntimeError):
    """Raised when another medground process already owns the data dir."""

    def __init__(self, holder_pid: str, path: Path) -> None:
        self.holder_pid = holder_pid
        super().__init__(
            f"another medground process (PID {holder_pid}) already owns {path.parent} — "
            f"stop it first. The embedded stores are single-writer (ADR-0018)."
        )


def acquire_owner_lock(data_dir: Path | None = None) -> None:
    """Take an exclusive, cross-process lock on the data dir (idempotent within a process).

    Call once at process startup, before touching the stores. Raises `DataDirLocked` if a
    different process holds it. No-op on platforms without `fcntl`.
    """
    global _owner_lock_fd
    if _owner_lock_fd is not None:
        return
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-Unix
        return
    base = Path(data_dir) if data_dir is not None else CONFIG.data_dir
    base.mkdir(parents=True, exist_ok=True)
    lock_path = base / ".medground.lock"
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        holder = "unknown"
        with contextlib.suppress(Exception):
            holder = os.pread(fd, 32, 0).decode().strip() or "unknown"
        os.close(fd)
        raise DataDirLocked(holder, lock_path) from exc
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    os.fsync(fd)
    _owner_lock_fd = fd


def release_owner_lock() -> None:
    """Release the owner lock. Process exit also releases it; this is for tests / teardown."""
    global _owner_lock_fd
    if _owner_lock_fd is None:
        return
    with contextlib.suppress(Exception):
        import fcntl

        fcntl.flock(_owner_lock_fd, fcntl.LOCK_UN)
    with contextlib.suppress(Exception):
        os.close(_owner_lock_fd)
    _owner_lock_fd = None
