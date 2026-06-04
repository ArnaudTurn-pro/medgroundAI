"""Watch executor and daemon loop.

Concurrency model:
  - Each watch runs as one async task. Tasks share a single semaphore (`_NCBI_SEM`) so the
    total request rate against NCBI stays polite even as the number of watches grows.
  - Within a single watch the existing ingestion pipeline already batches efetch calls; we
    just feed it a delta query (mindate + datetype="edat" + skip_known).

Cursor semantics:
  - `last_cursor_date` is the date of the most recent successful run (UTC).
  - Next run pulls anything indexed in PubMed on or after `cursor - 1 day` (small overlap to
    cover same-day races where NCBI indexes a paper after our query ran). Idempotent upserts
    make the overlap free.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta

from medground import runtime
from medground.ingest.pipeline import IngestStats, ingest_pubmed
from medground.store.docs import DocStore

log = logging.getLogger("medground.watch")

# Polite ceiling on simultaneous watches hitting NCBI. With an API key NCBI allows ~10 req/s;
# our pipeline batches efetch heavily so 4 concurrent watches is well within limits.
_NCBI_SEM = asyncio.Semaphore(4)


_CADENCE_RE = re.compile(r"^\s*(\d+)\s*([smhdw]?)\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_cadence(spec: str | int) -> int:
    """Accept "30m", "1h", "1d", "12h", "604800", 86400 → seconds. Raises ValueError otherwise."""
    if isinstance(spec, int):
        if spec < 60:
            raise ValueError("cadence must be at least 60 seconds")
        return spec
    m = _CADENCE_RE.match(str(spec))
    if not m:
        raise ValueError(f"invalid cadence: {spec!r}")
    n, unit = int(m.group(1)), m.group(2).lower()
    seconds = n * _UNIT_SECONDS[unit]
    if seconds < 60:
        raise ValueError("cadence must be at least 60 seconds")
    return seconds


def _is_due(watch: dict, now: datetime) -> bool:
    if not watch.get("enabled", True):
        return False
    last = watch.get("last_run_at")
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    delta = (now - last).total_seconds()
    return delta >= watch["cadence_seconds"]


def _mindate_for(watch: dict, now: datetime) -> str | None:
    """Pick the PubMed mindate string for this run. Returns None for first run (no cursor)."""
    cursor = watch.get("last_cursor_date")
    if cursor is None:
        return None
    # 1-day overlap absorbs same-day races and minor timezone skew.
    start = cursor - timedelta(days=1)
    return start.strftime("%Y/%m/%d")


async def run_one(watch_id: int, *, docs: DocStore | None = None) -> dict:
    """Run a single watch. Persists run history and updates the cursor on success."""
    docs = docs or runtime.get_docs()
    with runtime.DB_LOCK:
        w = docs.get_watch(watch_id)
    if w is None:
        raise ValueError(f"unknown watch: {watch_id}")
    if w["source"] != "pubmed":
        raise NotImplementedError(f"source not yet supported: {w['source']}")

    now = datetime.now(UTC)
    mindate = _mindate_for(w, now)
    with runtime.DB_LOCK:
        run_id = docs.start_watch_run(w["id"])

    log.info(
        "watch[%s] '%s' running (mindate=%s, max=%d)",
        w["label"], w["query"], mindate or "ALL", w["max_per_run"],
    )

    error = ""
    stats = IngestStats()
    try:
        async with _NCBI_SEM:
            stats = await ingest_pubmed(
                w["query"],
                max_results=w["max_per_run"],
                docs=docs,
                mindate=mindate,
                datetype="edat" if mindate else "pdat",
                sort="date" if mindate else "relevance",
                skip_known=True,
            )
    except Exception as e:
        log.exception("watch[%s] failed", w["label"])
        error = f"{type(e).__name__}: {e}"

    with runtime.DB_LOCK:
        docs.finish_watch_run(run_id, stats.papers, stats.chunks, error)
        if not error:
            docs.update_watch_cursor(w["id"], now, now.date())

    return {
        "watch_id": w["id"],
        "label": w["label"],
        "papers_added": stats.papers,
        "chunks_added": stats.chunks,
        "error": error or None,
    }


async def run_due(*, docs: DocStore | None = None) -> list[dict]:
    """Run every watch whose cadence has elapsed. Concurrent, with shared NCBI semaphore."""
    docs = docs or runtime.get_docs()
    now = datetime.now(UTC)
    with runtime.DB_LOCK:
        enabled = docs.list_watches(enabled_only=True)
    watches = [w for w in enabled if _is_due(w, now)]
    if not watches:
        return []
    log.info("watches due: %d", len(watches))
    return await asyncio.gather(*[run_one(w["id"], docs=docs) for w in watches])


async def run_all_now(*, docs: DocStore | None = None) -> list[dict]:
    """Force-run every enabled watch regardless of cadence."""
    docs = docs or runtime.get_docs()
    with runtime.DB_LOCK:
        watches = docs.list_watches(enabled_only=True)
    if not watches:
        return []
    return await asyncio.gather(*[run_one(w["id"], docs=docs) for w in watches])


async def daemon(*, tick_seconds: int = 60, docs: DocStore | None = None) -> None:
    """Long-running loop. Wakes every `tick_seconds`, runs anything due, sleeps.

    The tick is intentionally short relative to typical cadences (minutes vs hours/days) so
    newly-added watches don't wait an entire cadence for their first run.
    """
    docs = docs or runtime.get_docs()
    log.info("watch daemon started (tick=%ds)", tick_seconds)
    while True:
        try:
            results = await run_due(docs=docs)
            for r in results:
                log.info(
                    "watch[%s] +%d papers, +%d chunks%s",
                    r["label"], r["papers_added"], r["chunks_added"],
                    f" (ERROR: {r['error']})" if r.get("error") else "",
                )
        except asyncio.CancelledError:
            log.info("watch daemon cancelled")
            raise
        except Exception:
            log.exception("watch daemon tick failed")
        await asyncio.sleep(tick_seconds)
