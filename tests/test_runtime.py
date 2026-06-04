"""Tests for the single-owner concurrency model (ADR-0014).

Covers the two guarantees the fix rests on:
  1. one shared store instance per process (singletons);
  2. concurrent multi-thread read+write on a shared connection is safe *when serialized by
     DB_LOCK* — this is what lets the in-server watch loop write while tool calls read.
And the lifespan that hosts the watch loop inside the MCP server process.
"""

from __future__ import annotations

import asyncio
import contextlib
import types
from threading import Thread

from medground import runtime


def test_store_singletons_are_shared():
    assert runtime.get_docs() is runtime.get_docs()
    assert runtime.get_vectors() is runtime.get_vectors()
    assert runtime.get_lexical() is runtime.get_lexical()
    assert runtime.get_graph() is runtime.get_graph()


def test_concurrent_read_write_under_lock(tmp_path):
    """Hammer one shared DuckDB connection from reader + writer threads, all via DB_LOCK.

    DuckDB connection objects are not thread-safe; without the lock this races. With it, the
    serialization must hold and the final state must be exactly the writes we issued."""
    from medground.store.docs import DocStore

    docs = DocStore(path=tmp_path / "t.duckdb")
    errors: list[str] = []
    n_writes = 25

    def writer():
        try:
            for _ in range(n_writes):
                with runtime.DB_LOCK:
                    rid = docs.start_run("test", "q")
                    docs.finish_run(rid, 1, 1)
        except Exception as e:  # pragma: no cover - failure path
            errors.append(repr(e))

    def reader():
        try:
            for _ in range(n_writes):
                with runtime.DB_LOCK:
                    docs.counts()
        except Exception as e:  # pragma: no cover
            errors.append(repr(e))

    writers = [Thread(target=writer) for _ in range(3)]
    readers = [Thread(target=reader) for _ in range(3)]
    for t in (*writers, *readers):
        t.start()
    for t in (*writers, *readers):
        t.join()

    assert not errors, errors
    with runtime.DB_LOCK:
        total = docs.conn.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0]
    assert total == 3 * n_writes  # no lost/duplicated writes
    docs.close()


def test_lifespan_starts_watch_and_keeps_it_running(monkeypatch):
    """The in-server watch loop starts once and is NOT cancelled on session exit — under
    streamable-HTTP a single session ending must not stop the process-wide watcher (ADR-0018)."""
    from medground.mcp import server

    server._watch_task = None
    server._checkpoint_registered = True  # don't register a real atexit handler in this test
    state = {"started": 0, "cancelled": False}

    async def fake_daemon(*, tick_seconds=None, docs=None):
        state["started"] += 1
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    monkeypatch.setattr(server._watch, "daemon", fake_daemon)
    monkeypatch.setattr(
        server, "CONFIG", types.SimpleNamespace(watch_in_server=True, watch_tick_seconds=999)
    )

    async def run():
        async with server._lifespan(server.mcp):
            await asyncio.sleep(0.05)  # let the task spin up
        # the session has ended; the watch loop must still be alive (process-scoped, not cancelled)
        await asyncio.sleep(0.02)
        task = server._watch_task
        assert task is not None and not task.done()
        assert state["cancelled"] is False
        task.cancel()  # explicit cleanup so the task / global don't leak across tests
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())
    server._watch_task = None
    assert state["started"] == 1


def test_lifespan_noop_when_disabled(monkeypatch):
    from medground.mcp import server

    called = {"n": 0}

    async def fake_daemon(**kwargs):
        called["n"] += 1

    monkeypatch.setattr(server._watch, "daemon", fake_daemon)
    monkeypatch.setattr(
        server, "CONFIG", types.SimpleNamespace(watch_in_server=False, watch_tick_seconds=999)
    )

    async def run():
        async with server._lifespan(server.mcp):
            await asyncio.sleep(0.02)

    asyncio.run(run())
    assert called["n"] == 0
