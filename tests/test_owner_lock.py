"""Single-owner cross-process data-dir lock (ADR-0018).

Verifies that one medground process can take the lock, a second is refused, and releasing it
frees the data dir again. Uses separate file descriptors to stand in for a "second process"
(flock treats independent open() handles as distinct contenders, even within one process).
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from medground import runtime


def _denied_to_other(lock_path) -> bool:
    """True if a *separate* fd cannot take the exclusive lock (i.e. it is held)."""
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    return False


def test_acquire_records_pid_and_blocks_others(tmp_path):
    runtime.release_owner_lock()  # clean slate (module-global fd)
    lock = tmp_path / ".medground.lock"

    runtime.acquire_owner_lock(tmp_path)
    try:
        assert lock.exists()
        assert lock.read_text().strip() == str(os.getpid())
        assert _denied_to_other(lock) is True
    finally:
        runtime.release_owner_lock()

    assert _denied_to_other(lock) is False  # freed after release


def test_acquire_is_idempotent(tmp_path):
    runtime.release_owner_lock()
    runtime.acquire_owner_lock(tmp_path)
    fd_before = runtime._owner_lock_fd
    runtime.acquire_owner_lock(tmp_path)  # second call is a no-op, same fd
    try:
        assert runtime._owner_lock_fd == fd_before
    finally:
        runtime.release_owner_lock()


def test_acquire_raises_when_held_by_another(tmp_path):
    runtime.release_owner_lock()
    lock = tmp_path / ".medground.lock"
    other = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.write(other, b"99999")  # stand-in holder pid
    try:
        with pytest.raises(runtime.DataDirLocked) as excinfo:
            runtime.acquire_owner_lock(tmp_path)
        assert "99999" in str(excinfo.value)
        assert runtime._owner_lock_fd is None  # failed acquire leaves no dangling fd
    finally:
        fcntl.flock(other, fcntl.LOCK_UN)
        os.close(other)
        runtime.release_owner_lock()


def test_guard_exits_zero_when_locked(monkeypatch):
    """server._guard_single_owner() maps DataDirLocked -> a clean SystemExit(0).

    This is the headline UX promise (a stray second server disconnects cleanly, no crash spam) and
    was previously untested.
    """
    from medground.mcp import server

    def _locked(*_a, **_k):
        raise runtime.DataDirLocked("4242", Path("/tmp/x/.medground.lock"))

    monkeypatch.setattr(runtime, "acquire_owner_lock", _locked)
    with pytest.raises(SystemExit) as excinfo:
        server._guard_single_owner()
    assert excinfo.value.code == 0


def test_lock_blocks_a_real_second_process(tmp_path):
    """The guarantee that actually matters: a separate OS *process* is refused.

    The same-process two-fd tests above would still pass under POSIX byte-range locks (which do NOT
    contend within one process); this asserts real cross-process exclusion via `flock`.
    """
    runtime.release_owner_lock()
    lock = tmp_path / ".medground.lock"
    child = textwrap.dedent(
        f"""
        import os, fcntl, sys
        fd = os.open({str(lock)!r}, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(fd, str(os.getpid()).encode())
        print("READY", flush=True)
        sys.stdin.read()  # hold the lock until the parent closes our stdin
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", child], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout.readline().strip() == "READY"  # child now holds the lock
        with pytest.raises(runtime.DataDirLocked):
            runtime.acquire_owner_lock(tmp_path)
    finally:
        proc.stdin.close()
        proc.wait(timeout=5)
        runtime.release_owner_lock()


def test_checkpoint_registered_once(monkeypatch):
    """The WAL-flush CHECKPOINT is registered with atexit exactly once, however many sessions."""
    from medground.mcp import server

    server._checkpoint_registered = False
    registered = []
    monkeypatch.setattr(server.atexit, "register", lambda fn: registered.append(fn))
    server._register_checkpoint_once()
    server._register_checkpoint_once()
    server._register_checkpoint_once()
    assert len(registered) == 1


def test_lifespan_starts_watch_loop_at_most_once(monkeypatch):
    """Under streamable-HTTP the lifespan runs per session — the watch loop must start only once
    (the B1 regression: N clients must not spawn N watch loops). See ADR-0018."""
    import asyncio
    import contextlib
    import types

    from medground.mcp import server

    server._watch_task = None
    server._checkpoint_registered = True  # don't touch atexit in this test
    starts = {"n": 0}

    async def _fake_daemon(**_kw):
        starts["n"] += 1
        await asyncio.sleep(3600)

    monkeypatch.setattr(
        server, "CONFIG", types.SimpleNamespace(watch_in_server=True, watch_tick_seconds=300)
    )
    monkeypatch.setattr(server._watch, "daemon", _fake_daemon)

    async def _run():
        for _ in range(3):  # three "client sessions"
            async with server._lifespan(server.mcp):
                pass
        await asyncio.sleep(0)  # let the created task run its first line
        task = server._watch_task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(_run())
    server._watch_task = None
    assert starts["n"] == 1
