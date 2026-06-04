"""Regression: a crash-poisoned DuckDB WAL must not brick the corpus forever.

A native crash (SIGTRAP/SIGKILL) mid-checkpoint can leave a write-ahead log DuckDB cannot replay.
The classic case: `create_fts_index(overwrite=1)` drops `fts_main_chunks` into the WAL, the process
dies before the follow-up CHECKPOINT lands, and WAL replay then fails with "Cannot drop entry
fts_main_chunks ... terms depends on it" (replay has no CASCADE). Before the fix EVERY subsequent
open failed and the whole corpus was unreachable. `DocStore._open_with_recovery` now quarantines the
unreplayable WAL and reopens from the last checkpoint, trading the un-checkpointed tail (which is
additive and re-ingestable) for an openable database.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import duckdb
import pytest

from medground.store.docs import DocStore

# Child process that builds a checkpointed FTS index, then writes an un-checkpointed FTS drop to the
# WAL and hangs — so the parent can SIGKILL it mid-WAL, reproducing the crash exactly.
_POISON_CHILD = textwrap.dedent(
    """
    import sys, time, duckdb
    from medground.store.docs import _SCHEMA
    path = sys.argv[1]
    c = duckdb.connect(path, config={
        "autoinstall_known_extensions": True, "autoload_known_extensions": True})
    c.execute("INSTALL fts"); c.execute("LOAD fts")
    c.execute(_SCHEMA)                       # the real chunks schema, so reopen is a clean no-op
    c.executemany(
        "INSERT INTO chunks (id, paper_id, index, section, text) VALUES (?, ?, ?, ?, ?)",
        [("a", "p", 0, "title", "hello world"), ("b", "p", 1, "abstract", "goodbye world")],
    )
    c.execute("PRAGMA create_fts_index('chunks', 'id', 'text', overwrite=1)")
    c.execute("CHECKPOINT")                 # main file now holds fts_main_chunks + its terms table
    c.execute("PRAGMA drop_fts_index('chunks')")   # drop goes to the WAL only — NOT checkpointed
    sys.stdout.write("READY\\n"); sys.stdout.flush()
    time.sleep(60)                          # wait to be killed mid-WAL
    """
)


def test_is_wal_replay_failure_matches_only_replay_errors():
    assert DocStore._is_wal_replay_failure(
        Exception("Failure while replaying WAL file: Cannot drop entry fts_main_chunks")
    )
    assert DocStore._is_wal_replay_failure(
        Exception("table terms depends on schema fts_main_chunks in the WAL")
    )
    # Unrelated errors must pass through untouched (we only auto-quarantine genuine replay failures).
    assert not DocStore._is_wal_replay_failure(Exception("disk full"))
    assert not DocStore._is_wal_replay_failure(Exception("syntax error near SELECT"))


def test_quarantine_wal_moves_file_aside(tmp_path):
    db = tmp_path / "x.duckdb"
    wal = tmp_path / "x.duckdb.wal"
    wal.write_bytes(b"not a real wal")
    docs = DocStore(path=db)

    moved = docs._quarantine_wal()

    assert moved is not None and moved.exists()
    assert not wal.exists()  # original WAL is gone (renamed aside)
    assert moved.name.startswith("x.duckdb.wal.corrupt-")


def test_quarantine_wal_is_noop_without_wal(tmp_path):
    assert DocStore(path=tmp_path / "x.duckdb")._quarantine_wal() is None


@pytest.mark.skipif(os.name != "posix", reason="needs SIGKILL to reproduce a mid-WAL crash")
def test_self_heals_poisoned_fts_wal(tmp_path):
    db = tmp_path / "poison.duckdb"
    proc = subprocess.Popen(
        [sys.executable, "-c", _POISON_CHILD, str(db)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "READY"  # FTS dropped into the WAL, not flushed
    finally:
        proc.kill()  # SIGKILL — no clean close, no checkpoint: the WAL stays unreplayable
        proc.wait(timeout=10)

    wal = db.with_name(db.name + ".wal")
    assert wal.exists(), "the kill should have left an un-checkpointed WAL"

    # A naive open is bricked: replay can't drop fts_main_chunks (no CASCADE).
    with pytest.raises(duckdb.Error):
        duckdb.connect(
            str(db),
            config={"autoinstall_known_extensions": True, "autoload_known_extensions": True},
        ).execute("SELECT 1")

    # The store self-heals: quarantines the WAL, reopens from the checkpoint, stays usable.
    docs = DocStore(path=db)
    assert docs.counts()["chunks"] == 2  # the checkpointed rows survived
    assert not wal.exists()  # the poisoned WAL was moved aside
    assert list(db.parent.glob("poison.duckdb.wal.corrupt-*")), "WAL should be quarantined, not lost"
    docs.close()
