"""Command-line interface. Thin wrapper around the library.

Subcommands:
  ingest pubmed --query "..." [--max N]
  search "..."  [--k 8]      hybrid: vector + BM25 + graph
  reembed                    re-embed all chunks with the current provider
  index                      rebuild the BM25 lexical index
  stats
  graph stats|rebuild|find|neighbors
  watch add|list|remove|enable|disable|run|daemon
  mcp                        start the MCP stdio server (one subprocess per client)
  serve                      run ONE shared HTTP MCP server (multi-client; ADR-0018)
"""

from __future__ import annotations

import asyncio
import logging
import sys

import typer
from rich.console import Console
from rich.table import Table

from medground.config import CONFIG
from medground.ingest.pipeline import ingest_civic, ingest_pubmed
from medground.retrieve.hybrid import search as hybrid_search
from medground.store.docs import DocStore
from medground.store.graph import GraphStore
from medground.store.lexical import LexicalStore
from medground.store.vectors import VectorStore
from medground.watch import service as watch_service

app = typer.Typer(
    no_args_is_help=True,
    help="medground — grounded Graph-RAG over cancer research literature.",
)
ingest_app = typer.Typer(no_args_is_help=True, help="Ingest documents from a source.")
app.add_typer(ingest_app, name="ingest")
watch_app = typer.Typer(
    no_args_is_help=True,
    help="Track new research over time. Each watch pulls only the delta since its last run.",
)
app.add_typer(watch_app, name="watch")
graph_app = typer.Typer(
    no_args_is_help=True,
    help="Knowledge graph (KuzuDB): MeSH concepts, MENTIONS edges, neighbor queries.",
)
app.add_typer(graph_app, name="graph")

console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


@ingest_app.command("pubmed")
def ingest_pubmed_cmd(
    query: str = typer.Option(..., "--query", "-q", help="PubMed search expression."),
    max_results: int = typer.Option(50, "--max", "-n", help="Max papers to ingest."),
    batch_size: int = typer.Option(16, "--batch", help="Embed/persist batch size."),
    no_embed: bool = typer.Option(False, "--no-embed", help="Skip embedding & vector upsert."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Search PubMed and ingest the top results."""
    _setup_logging(verbose)
    console.print(
        f"[bold cyan]Ingesting[/bold cyan] up to {max_results} papers for query: "
        f"[italic]{query}[/italic]"
    )
    stats = asyncio.run(
        ingest_pubmed(
            query, max_results=max_results, batch_size=batch_size, embed=not no_embed
        )
    )
    console.print(
        f"[bold green]Done.[/bold green] papers=[bold]{stats.papers}[/bold] "
        f"chunks=[bold]{stats.chunks}[/bold] errors={stats.errors}"
    )


@ingest_app.command("civic")
def ingest_civic_cmd(
    limit: int = typer.Option(0, "--limit", "-n", help="Max evidence items (0 = all ~11k)."),
    batch_size: int = typer.Option(64, "--batch", help="Embed/persist batch size."),
    no_embed: bool = typer.Option(False, "--no-embed", help="Skip embedding & vector upsert."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Ingest CIViC variant→therapy evidence: groundable documents + the civic_evidence matching table."""
    _setup_logging(verbose)
    console.print(
        f"[bold cyan]Ingesting CIViC[/bold cyan] "
        f"({limit if limit else 'all (~11k)'} evidence items)"
    )
    stats = asyncio.run(
        ingest_civic(max_results=(limit or None), batch_size=batch_size, embed=not no_embed)
    )
    console.print(
        f"[bold green]Done.[/bold green] evidence=[bold]{stats.papers}[/bold] "
        f"chunks=[bold]{stats.chunks}[/bold] errors={stats.errors}"
    )


@app.command("search")
def search_cmd(
    query: str = typer.Argument(..., help="Free-text query."),
    k: int = typer.Option(CONFIG.default_top_k, "--k", "-k", help="Number of hits."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Hybrid retrieval (vector + BM25 + graph, RRF-fused). Prints grounded, cited hits."""
    _setup_logging(verbose)
    hits = hybrid_search(query, k=k)
    if not hits:
        console.print("[yellow]No hits. Ingest some papers first.[/yellow]")
        raise typer.Exit(code=1)
    table = Table(title=f"Top {len(hits)} for: {query}", show_lines=True)
    table.add_column("#", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Year")
    table.add_column("Citation", overflow="fold")
    table.add_column("Snippet", overflow="fold", max_width=80)
    for i, h in enumerate(hits, 1):
        cite = f"{h.title} — {h.journal or '?'}"
        if h.url:
            cite += f" ({h.url})"
        snippet = h.text[:240] + ("…" if len(h.text) > 240 else "")
        table.add_row(str(i), f"{h.score:.3f}", str(h.year or "?"), cite, snippet)
    console.print(table)


@app.command("reembed")
def reembed_cmd(
    batch_size: int = typer.Option(128, "--batch", help="Chunks per embedding API call."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Re-embed every chunk with the currently-configured provider/model.

    Use after switching `MG_EMBED_PROVIDER` or `MG_EMBED_MODEL`. The DuckDB-VSS vector table is
    dropped and recreated at the new dim; chunks and papers are untouched.
    """
    _setup_logging(verbose)
    from medground.models import Chunk, ChunkSection
    from medground.nlp.embeddings import get_embedder

    docs = DocStore()
    embedder = get_embedder()
    console.print(
        f"[cyan]Re-embedding with[/cyan] {CONFIG.embedding_provider}/{CONFIG.embedding_model} "
        f"(dim={embedder.dim}, batch={batch_size})"
    )

    # Force-recreate the vector table at the new dim (VectorStore handles the mismatch).
    vectors = VectorStore(docs=docs, dim=embedder.dim)
    vectors._ensure()  # eager init / migration to the new dim

    rows = docs.conn.execute(
        "SELECT id, paper_id, index, section, text, char_start, char_end FROM chunks ORDER BY paper_id, index"
    ).fetchall()
    if not rows:
        console.print("[yellow]No chunks to re-embed.[/yellow]")
        return

    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        chunks = [
            Chunk(
                id=r[0], paper_id=r[1], index=r[2], section=ChunkSection(r[3]),
                text=r[4], char_start=r[5] or 0, char_end=r[6] or 0,
            )
            for r in batch
        ]
        vecs = embedder.embed_passages([c.text for c in chunks])
        vectors.upsert(chunks, vecs)
        total += len(chunks)
        console.print(f"  embedded {total}/{len(rows)}")
    console.print(f"[bold green]Done.[/bold green] re-embedded {total} chunks")


@app.command("stats")
def stats_cmd() -> None:
    """Show what's currently in the local stores."""
    docs = DocStore()
    vectors = VectorStore(docs=docs)
    lexical = LexicalStore(docs=docs)
    graph = GraphStore()
    c = docs.counts()
    g = graph.counts()
    table = Table(title="medground — local store stats")
    table.add_column("Store")
    table.add_column("Count", justify="right")
    table.add_row("papers (duckdb)", str(c["papers"]))
    table.add_row("chunks (duckdb)", str(c["chunks"]))
    table.add_row("vectors (duckdb-vss)", str(vectors.count()))
    table.add_row("lexical index (duckdb-fts)", str(lexical.count()))
    table.add_row("graph papers (kuzu)", str(g["papers"]))
    table.add_row("graph concepts (kuzu)", str(g["concepts"]))
    table.add_row("graph mentions (kuzu)", str(g["mentions"]))
    console.print(table)
    console.print(f"[dim]data dir: {CONFIG.data_dir}[/dim]")


@app.command("index")
def index_cmd(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """Rebuild the BM25 lexical index over all chunks (DuckDB FTS).

    Ingest does this automatically; run it manually after a migration or if search returns no
    lexical hits on a corpus you know is populated.
    """
    _setup_logging(verbose)
    n = LexicalStore().rebuild()
    if n == 0:
        console.print("[yellow]No chunks to index. Ingest some papers first.[/yellow]")
        return
    console.print(f"[bold green]Done.[/bold green] indexed {n} chunks for BM25 search")


@app.command("compact")
def compact_cmd(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """Rebuild the DuckDB store into a fresh, compact file (reclaims bloat + drops the legacy FK).

    DuckDB never shrinks a file in place, so churn (re-embeds, per-ingest HNSW rewrites) leaves
    the file far larger than the data. This copies papers/chunks/vectors/watches into a NEW file
    with a fresh HNSW + FTS index, drops the legacy chunks→papers FK, fixes up sequences, and
    swaps it in (old file kept as `medground.duckdb.prebloat`). Stop the MCP server first.
    """
    _setup_logging(verbose)
    import shutil

    import duckdb

    from medground.store.docs import _SCHEMA

    src = CONFIG.duckdb_path
    if not src.exists():
        console.print("[yellow]No DuckDB file to compact.[/yellow]")
        return
    before = src.stat().st_size
    tmp = src.with_name("medground.compact.duckdb")
    for p in (tmp, tmp.with_name(tmp.name + ".wal")):
        if p.exists():
            p.unlink()

    cfg = {"autoinstall_known_extensions": True, "autoload_known_extensions": True}
    con = duckdb.connect(str(tmp), config=cfg)
    try:
        for ext in ("vss", "fts"):
            con.execute(f"INSTALL {ext}")
            con.execute(f"LOAD {ext}")
        con.execute("SET hnsw_enable_experimental_persistence = true")
        con.execute(_SCHEMA)
        con.execute(f"ATTACH '{src}' AS src (READ_ONLY)")
        dim = len(con.execute("SELECT vector FROM src.chunk_vectors LIMIT 1").fetchone()[0])
        for t in ("papers", "chunks", "ingestion_runs", "watches", "watch_runs"):
            con.execute(f"INSERT INTO {t} SELECT * FROM src.{t}")
        con.execute(
            f"CREATE TABLE chunk_vectors (chunk_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, "
            f"section TEXT, vector FLOAT[{dim}])"
        )
        con.execute("INSERT INTO chunk_vectors SELECT * FROM src.chunk_vectors")
        con.execute(
            "CREATE INDEX chunk_vectors_hnsw ON chunk_vectors USING HNSW (vector) "
            "WITH (metric = 'cosine')"
        )
        con.execute(
            r"PRAGMA create_fts_index('chunks','id','text', stemmer='porter', "
            r"stopwords='english', ignore='(\.|[^a-z0-9])+', strip_accents=1, lower=1, overwrite=1)"
        )
        for seq, tbl in (
            ("ingestion_runs_id_seq", "ingestion_runs"),
            ("watches_id_seq", "watches"),
            ("watch_runs_id_seq", "watch_runs"),
        ):
            mx = con.execute(f"SELECT coalesce(max(id), 0) FROM {tbl}").fetchone()[0]
            con.execute(f"CREATE OR REPLACE SEQUENCE {seq} START WITH {int(mx) + 1}")
        npapers = con.execute("SELECT count(*) FROM papers").fetchone()[0]
        nvec = con.execute("SELECT count(*) FROM chunk_vectors").fetchone()[0]
        con.execute("DETACH src")
        con.execute("CHECKPOINT")
    except Exception as e:
        con.close()
        tmp.unlink(missing_ok=True)
        console.print(f"[red]Compaction failed[/red] (is the MCP server still running?): {e}")
        raise typer.Exit(code=1) from e
    con.close()

    bak = src.with_name("medground.duckdb.prebloat")
    bak.unlink(missing_ok=True)
    shutil.move(str(src), str(bak))
    srcwal = src.with_name(src.name + ".wal")
    if srcwal.exists():
        srcwal.unlink()
    shutil.move(str(tmp), str(src))
    after = src.stat().st_size
    console.print(
        f"[bold green]Compacted.[/bold green] papers={npapers} vectors={nvec}  "
        f"{before / 1e6:.0f}MB → {after / 1e6:.0f}MB  (old file kept as {bak.name})"
    )


@graph_app.command("stats")
def graph_stats_cmd() -> None:
    g = GraphStore().counts()
    table = Table(title="graph (KuzuDB)")
    table.add_column("Node/Edge")
    table.add_column("Count", justify="right")
    table.add_row("Paper", str(g["papers"]))
    table.add_row("Concept", str(g["concepts"]))
    table.add_row("MENTIONS", str(g["mentions"]))
    console.print(table)


@graph_app.command("rebuild")
def graph_rebuild_cmd(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """Wipe and rebuild the graph from every paper in DuckDB.

    Clears the graph first so the current MeSH stop-list (check tags / generic cell-culture
    descriptors) is applied — re-upserting onto a stale graph would leave old noise concepts.
    """
    _setup_logging(verbose)
    from medground.models import Paper as PaperModel

    docs = DocStore()
    graph = GraphStore()
    graph.clear()
    rows = docs.conn.execute(
        "SELECT id, source, native_id, title, year, mesh_terms FROM papers"
    ).fetchall()
    if not rows:
        console.print("[yellow]No papers in DuckDB to backfill from.[/yellow]")
        return
    n = 0
    for r in rows:
        paper = PaperModel(
            id=r[0], source=r[1], native_id=r[2], title=r[3] or "", year=r[4],
            mesh_terms=list(r[5] or []),
        )
        try:
            graph.upsert_paper(paper)
            n += 1
        except Exception:
            log_ = console
            log_.print(f"[red]failed[/red]: {paper.id}")
    console.print(f"[bold green]Done.[/bold green] backfilled {n}/{len(rows)} papers")


@graph_app.command("find")
def graph_find_cmd(
    fragment: str = typer.Argument(..., help="Substring of a MeSH concept name."),
    limit: int = typer.Option(15, "--limit", "-n"),
) -> None:
    """Search MeSH concepts by name fragment."""
    rows = GraphStore().find_concepts(fragment, limit=limit)
    if not rows:
        console.print(f"[yellow]No concepts match: {fragment}[/yellow]")
        raise typer.Exit(code=1)
    table = Table(title=f"Concepts matching '{fragment}'")
    table.add_column("id")
    table.add_column("name")
    for r in rows:
        table.add_row(r["id"], r["name"])
    console.print(table)


@graph_app.command("neighbors")
def graph_neighbors_cmd(
    name: str = typer.Argument(..., help="Concept name (e.g. 'BRCA1 Protein')."),
    hops: int = typer.Option(1, "--hops", help="1 (direct) or 2 (expanded). Default 1."),
    limit: int = typer.Option(15, "--limit", "-n"),
) -> None:
    """Show concepts that co-occur with the given anchor in the corpus."""
    rows = GraphStore().neighbors(name, hops=hops, limit=limit)
    if not rows:
        console.print(f"[yellow]No neighbors found for: {name}[/yellow]")
        raise typer.Exit(code=1)
    table = Table(title=f"Neighbors of '{name}' ({hops}-hop)")
    table.add_column("Concept")
    table.add_column("Co-occurrences", justify="right")
    for r in rows:
        table.add_row(r["name"], str(int(r["weight"])))
    console.print(table)


def _fmt_dt(v) -> str:
    if v is None:
        return "—"
    if hasattr(v, "isoformat"):
        return v.isoformat(sep=" ", timespec="seconds") if hasattr(v, "hour") else v.isoformat()
    return str(v)


@watch_app.command("add")
def watch_add_cmd(
    label: str = typer.Option(..., "--label", "-l", help="Unique short name for the watch."),
    query: str = typer.Option(..., "--query", "-q", help="PubMed search expression."),
    every: str = typer.Option("1d", "--every", "-e", help="Cadence: 30m, 1h, 6h, 1d, 1w."),
    max_per_run: int = typer.Option(50, "--max", help="Max papers to ingest per run."),
    source: str = typer.Option("pubmed", "--source", help="Source name (pubmed for now)."),
    notes: str = typer.Option("", "--notes", help="Optional note."),
) -> None:
    """Register a new watch. Cadence is the minimum interval between runs."""
    cadence = watch_service.parse_cadence(every)
    docs = DocStore()
    wid = docs.add_watch(
        label, query, source=source, cadence_seconds=cadence, max_per_run=max_per_run, notes=notes,
    )
    console.print(
        f"[bold green]Added watch #{wid}[/bold green] [cyan]{label}[/cyan] every {every} → "
        f"[italic]{query}[/italic]"
    )


@watch_app.command("list")
def watch_list_cmd() -> None:
    docs = DocStore()
    watches = docs.list_watches()
    if not watches:
        console.print("[yellow]No watches. Use `medground watch add` to create one.[/yellow]")
        return
    table = Table(title="Watches")
    table.add_column("ID", justify="right")
    table.add_column("Label")
    table.add_column("Enabled")
    table.add_column("Source")
    table.add_column("Cadence")
    table.add_column("Query", overflow="fold")
    table.add_column("Last run")
    for w in watches:
        cadence = f"{w['cadence_seconds'] // 3600}h" if w['cadence_seconds'] >= 3600 \
            else f"{w['cadence_seconds'] // 60}m"
        table.add_row(
            str(w["id"]),
            w["label"],
            "yes" if w["enabled"] else "no",
            w["source"],
            cadence,
            w["query"],
            _fmt_dt(w["last_run_at"]),
        )
    console.print(table)


@watch_app.command("remove")
def watch_remove_cmd(ident: str = typer.Argument(..., help="Watch id or label.")) -> None:
    docs = DocStore()
    ok = docs.remove_watch(ident)
    if ok:
        console.print(f"[bold red]Removed watch[/bold red] {ident}")
    else:
        console.print(f"[yellow]No watch found: {ident}[/yellow]")
        raise typer.Exit(code=1)


@watch_app.command("disable")
def watch_disable_cmd(ident: str = typer.Argument(...)) -> None:
    DocStore().set_watch_enabled(ident, False)
    console.print(f"Disabled {ident}")


@watch_app.command("enable")
def watch_enable_cmd(ident: str = typer.Argument(...)) -> None:
    DocStore().set_watch_enabled(ident, True)
    console.print(f"Enabled {ident}")


@watch_app.command("run")
def watch_run_cmd(
    ident: str | None = typer.Argument(None, help="Watch id or label. Omit with --all."),
    all_: bool = typer.Option(False, "--all", help="Force-run every enabled watch."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run a watch immediately, bypassing the cadence check."""
    _setup_logging(verbose)
    docs = DocStore()
    if all_:
        results = asyncio.run(watch_service.run_all_now(docs=docs))
    elif ident:
        w = docs.get_watch(ident)
        if w is None:
            console.print(f"[yellow]No watch found: {ident}[/yellow]")
            raise typer.Exit(code=1)
        results = [asyncio.run(watch_service.run_one(w["id"], docs=docs))]
    else:
        console.print("[red]Pass a watch id/label or --all[/red]")
        raise typer.Exit(code=2)
    for r in results:
        tag = "[green]OK[/green]" if not r.get("error") else f"[red]ERROR[/red]: {r['error']}"
        console.print(
            f"watch[{r['label']}] +{r['papers_added']} papers, "
            f"+{r['chunks_added']} chunks  {tag}"
        )


@watch_app.command("daemon")
def watch_daemon_cmd(
    tick: int = typer.Option(60, "--tick", help="Seconds between scheduler checks."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Long-running loop. Runs each watch when its cadence elapses; Ctrl-C to stop.

    Standalone daemon — use this only when the MCP server is NOT running (they'd both try to
    lock the same files). To track research alongside Opus, run the watcher inside the MCP
    server instead: set MG_WATCH_IN_SERVER=1 (see ADR-0014).
    """
    _setup_logging(verbose)
    console.print(f"[cyan]watch daemon starting[/cyan] (tick={tick}s, Ctrl-C to stop)")
    try:
        asyncio.run(watch_service.daemon(tick_seconds=tick))
    except KeyboardInterrupt:
        console.print("\n[yellow]daemon stopped[/yellow]")


@app.command("mcp")
def mcp_cmd() -> None:
    """Run the MCP server on stdio (one subprocess per client). Wire into your MCP-aware client."""
    from medground.mcp.server import main as mcp_main

    mcp_main()


@app.command("serve")
def serve_cmd(
    host: str = typer.Option(
        CONFIG.http_host, "--host",
        help="Bind address. Localhost-only by default — don't expose without auth.",
    ),
    port: int = typer.Option(CONFIG.http_port, "--port", "-p", help="Port for the HTTP MCP server."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run ONE shared MCP server over HTTP so multiple clients use medground at once.

    Terminals, Warp panes, IDE, and agents all connect to the SAME server (the single
    DuckDB/KuzuDB owner) instead of each spawning a stdio subprocess that would fight over the
    single-writer DB. Register it in every client:

      claude mcp add --transport http medground http://127.0.0.1:8765/mcp

    See ADR-0018.
    """
    _setup_logging(verbose)
    from medground.mcp.server import serve as mcp_serve

    console.print(
        f"[bold cyan]medground[/bold cyan] shared MCP server → "
        f"[green]http://{host}:{port}/mcp[/green]   (Ctrl-C to stop)"
    )
    try:
        mcp_serve(host=host, port=port)
    except KeyboardInterrupt:
        console.print("\n[yellow]server stopped[/yellow]")


if __name__ == "__main__":  # pragma: no cover
    app()
