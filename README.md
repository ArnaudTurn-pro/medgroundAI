<p align="center">
  <img src="docs/logo.png" alt="medground logo" width="120" height="120" />
</p>

# medground

**Grounded Graph-RAG over the biomedical research literature. No claim ships without a real, retrievable source.**

medground gives Claude a finite, citable corpus of biomedical research and a **deterministic
grounding gate**, so every answer is backed by a paper you can open. Retrieval is hybrid (dense
vectors, BM25, and a MeSH knowledge graph), and each answer must pass a provenance check before it
reaches you: every claim has to cite a `paper_id` the system can actually find. A confident,
unsupported sentence is treated as a bug, not a stylistic choice.

It runs as an [MCP](https://modelcontextprotocol.io) server, so it plugs straight into Claude
Desktop, Claude Code, or any MCP-aware client as a research tool.

> ⚕️ **This is research synthesis and decision *support*, not medical advice.** It tells you what
> the literature *says*, grounded in citations. It does not prescribe, dose, or replace a
> clinician or tumor board.

- **Website:** [medground.ai](https://medground.ai) · **New here? → [`HOWTOUSE.md`](HOWTOUSE.md):** a 5-minute guide to asking Claude grounded questions (no tool names required). One-command setup: **[`./install.sh`](install.sh)** · copy-paste prompts: **[`EXAMPLES.md`](EXAMPLES.md)**.
- **Status:** early development (`0.0.x`) · Python 3.11+ · 24 tests green · [MIT](LICENSE)-licensed
- **Scale** (snapshot, 2026-06-01, grows continuously; run `medground stats` for live counts):
  ~12,400 grounded documents (~26,900 chunks), of which 11,240 are CIViC biomarker→therapy items;
  two retrieval-ready sources (PubMed + CIViC)
- **Scope:** the retrieval pipeline is domain-agnostic, and grows with your corpus. PubMed ingestion covers any biomedical field; the curated CIViC layer is one structured source (oncology biomarkers) among more to come. The repo ships the *pipeline*, not the corpus: you build your own with `medground ingest`, so no third-party article text is redistributed.
- **Architecture deep-dive:** [`docs/decisions/ARCHITECTURE.md`](docs/decisions/ARCHITECTURE.md) and the
  [ADRs](docs/decisions/) (18 decision records)

---

## Why this exists

Large language models hallucinate, and for research questions a fluent, wrong, *uncited* answer
is worse than no answer. medground's thesis is that an LLM is only trustworthy on these questions
when it is **forced to retrieve, forced to cite, and mechanically checked**. The system provides the
retrieval substrate and the enforcement; the LLM does the reasoning on top.

The contract is non-negotiable and **structural, not aspirational**:

1. **Retrieve** evidence from the local corpus (hybrid search returns citation metadata + an
   `allowed_paper_ids` envelope).
2. **Draft** the answer as discrete claims, each citing only `paper_id`s that were actually
   retrieved.
3. **Gate** the draft through `check_grounding(claims, allowed_paper_ids)`, a deterministic, no-LLM
   verifier that flags every uncited, fabricated, or out-of-envelope citation.
4. **Repair** every violation and re-check. Only a `grounded=true` answer is presented.

See [ADR-0007](docs/decisions/0007-groundedness-and-provenance.md) and
[ADR-0013](docs/decisions/0013-grounding-verifier-tool.md).

---

## How it works

```
                        ┌──────────────────────────┐
                        │  Agent (Claude / client) │
                        └────────────┬─────────────┘
                                     │ MCP (stdio) · 17 tools
                        ┌────────────▼─────────────┐
                        │  medground MCP server │
                        └────────────┬─────────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
       ┌──────▼──────┐        ┌──────▼──────┐        ┌──────▼──────┐
       │ Vector ANN  │        │   BM25 FTS  │        │  Graph hop  │
       │ (DuckDB-VSS)│        │  (DuckDB)   │        │  (KuzuDB)   │
       │ dense /     │        │ exact tokens│        │ MeSH 1-hop  │
       │ semantic    │        │ genes·drugs·│        │ co-occur-   │
       │             │        │ NCT·dosages │        │ rence       │
       └──────┬──────┘        └──────┬──────┘        └──────┬──────┘
              └──────────────────────┼──────────────────────┘
                  each channel degrades independently
                                     │
                          ┌──────────▼──────────┐
                          │ Reciprocal Rank     │   no tuning weights;
                          │ Fusion (RRF)        │   rank-only, library-grade
                          └──────────┬──────────┘
                                     │
                          ┌──────────▼───────────┐
                          │ Cited hits ──────────┼──► check_grounding gate
                          └──────────────────────┘    (deterministic provenance)

   Ingestion (PubMed E-utilities · CIViC GraphQL):
     fetch → chunk → embed → DuckDB (docs + vectors + FTS) + KuzuDB (MeSH graph)
                            ↑ OpenAI text-embedding-3-large (default) / fastembed / Voyage
```

- **Hybrid retrieval, RRF-fused.** Three channels: dense vector (semantic), BM25 (verbatim
  gene/drug/trial/dosage tokens), and a 1-hop MeSH graph expansion, merged by
  [Reciprocal Rank Fusion](docs/decisions/0006-hybrid-retrieval-rrf.md). Each channel degrades
  independently: a missing embedding key or empty vector table just drops that channel; the
  lexical channel needs no API key, so the corpus is searchable the moment papers are ingested.
- **MeSH-first knowledge graph.** Concepts and `MENTIONS` edges come straight from PubMed's MeSH
  terms (free, authoritative); demographic/species "check tags" are stop-listed so real signal
  isn't buried. Co-occurrence is computed on the live edges. ([ADR-0004](docs/decisions/0004-graph-rag-design.md),
  [ADR-0005](docs/decisions/0005-mesh-first-entities.md))
- **Embedded, single-file storage.** Everything (papers, chunks, vectors, FTS index, CIViC
  evidence) lives in **one DuckDB file**; the graph in **one KuzuDB file**. No servers, one backup,
  ACID. ([ADR-0002](docs/decisions/0002-embedded-storage-stack.md),
  [ADR-0011](docs/decisions/0011-collapse-vectors-into-duckdb.md))
- **Single-owner concurrency.** The running process is the sole owner of the data dir: one
  connection per store + one re-entrant lock serialize every touch. Network and embedding work
  stays *outside* the lock by construction. ([ADR-0014](docs/decisions/0014-concurrency-single-owner.md))

---

## Quickstart

> **In a hurry?** One command does everything below, installs medground, writes your `.env` (no
> hand-editing), offers a starter corpus, connects it to Claude, and installs the `/doc` skills:
>
> ```bash
> git clone https://github.com/ArnaudTurn-pro/medgroundAI medground && cd medground
> ./install.sh
> ```
>
> It's safe to re-run anytime to change a setting. The manual steps below are the same thing, broken out.

### 1. Install

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
git clone https://github.com/ArnaudTurn-pro/medgroundAI medground && cd medground
uv sync                      # creates .venv and installs from uv.lock
```

### 2. Configure

```bash
cp .env.example .env
```

Then edit `.env`. The minimum for the default (best-quality) setup is an OpenAI key and an
absolute data path:

```dotenv
MG_DATA_DIR=/absolute/path/to/medground/data
OPENAI_API_KEY=sk-...
MG_NCBI_EMAIL=you@example.com   # courtesy to NCBI; raises nothing but is requested
```

> **No API key?** Switch to the free, offline local embedder, set
> `MG_EMBED_PROVIDER=fastembed`, `MG_EMBED_MODEL=BAAI/bge-small-en-v1.5`, `MG_EMBED_DIM=384`.
> See [Embeddings](#embeddings).

`.env` is loaded from the project root regardless of the working directory, so the same file feeds
both the CLI and an MCP server that Claude spawns from elsewhere. Real environment variables always
win over the file. `.env` is gitignored, never commit secrets.

### 3. Build a corpus

```bash
# Pull literature from PubMed
uv run medground ingest pubmed -q "BRCA1 olaparib maintenance ovarian" -n 50

# Pull curated biomarker→therapy evidence from CIViC (0 = the whole knowledgebase, ~11k items)
uv run medground ingest civic -n 200

uv run medground stats     # what's in the local stores
```

### 4. Search (sanity check)

```bash
uv run medground search "PARP inhibitor resistance mechanisms" -k 8
```

### 5. Wire it into an MCP client

The server speaks MCP over stdio. For **Claude Code**:

```bash
claude mcp add medground -- uv run --directory /absolute/path/to/medground medground-mcp
```

For **Claude Desktop** (`claude_desktop_config.json`) or any JSON-configured client:

```json
{
  "mcpServers": {
    "medground": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/medground", "medground-mcp"]
    }
  }
}
```

Keys and `MG_DATA_DIR` are picked up from the project-root `.env` automatically; alternatively pass
them in an `"env": { ... }` block. The server self-describes its grounded workflow to the client on
connect.

> **Multiple clients / agents at once?** The stdio command above spawns **one server per client**,
> and the embedded stores are **single-writer**, so two terminals (or Warp panes, or an IDE) would
> collide. To share one corpus across many clients, run **one** shared HTTP server and point every
> client at its URL:
>
> ```bash
> # one process owns the DB → http://127.0.0.1:8765/mcp, leave it running
> uv run --directory /absolute/path/to/medground medground serve
> claude mcp add --transport http medground http://127.0.0.1:8765/mcp   # run in each client
> ```
>
> (Drop the `uv run --directory …` prefix only if the venv is active and you're inside the
> project dir. The `serve` process must stay up; clients only connect to it.)
>
> One writer, many clients, no lock conflict; a startup lock makes a stray second server exit
> cleanly. See [ADR-0018](docs/decisions/0018-http-transport-and-single-owner-lock.md).

---

## Skills: the `/doc` profile (optional, recommended)

medground bundles a set of **[Claude Code](https://docs.claude.com/en/docs/claude-code) skills** in
[`skills/`](skills/) that turn the grounded workflow into slash commands, `/doc-evidence`,
`/doc-case`, `/doc-treatment-map`, `/doc-biomarker-match`, and more. They're optional (plain-English
questions already work), but they make the flows first-class and consistent. Install them with one
command from the repo root:

```bash
./install-skills.sh          # copies them into ~/.claude/skills
```

Restart Claude Code and type `/doc`. Full list in [`skills/README.md`](skills/README.md); copy-paste
prompts in [`EXAMPLES.md`](EXAMPLES.md). (`./install.sh` installs these for you at the end.)

---

## The MCP toolset (17 tools)

The agent surface. Source: [`src/medground/mcp/server.py`](src/medground/mcp/server.py).

### Retrieve & ground: the core loop

| Tool | What it does |
|---|---|
| `search_papers(query, k=8)` | Hybrid retrieval (vector + BM25 + graph, RRF-fused). Returns hits with `chunk_id`, `paper_id`, score, section, title, year, journal, url, text. Cap `k`=50. |
| `summarize_evidence(question, k_per_facet=5, facets?)` | Decomposes a clinical question into facets (efficacy / safety / biomarkers / mechanism / comparators) and retrieves per facet. Returns an evidence pack + `allowed_paper_ids` + a suggested answer structure. **No LLM call**, the agent synthesizes. |
| `evaluate_plan(plan_text, k_per_claim=5)` | Splits a treatment plan into claims and retrieves evidence per claim. Returns a per-claim pack + `allowed_paper_ids` + a verdict schema (supported / contested / unsupported / unknown). **No LLM call.** |
| `check_grounding(claims, allowed_paper_ids?)` | **The enforcement gate.** Deterministically classifies each drafted claim: `grounded` / `uncited` / `phantom_citation` / `off_envelope`. Returns `grounded`, `grounded_ratio`, and the violations to repair. No LLM, no network. |
| `get_paper(paper_id)` | Full paper record (title, abstract, authors, MeSH, journal, year, DOI/PMID/URL). |
| `get_paper_chunks(paper_id)` | All chunks for a paper, in order, with section labels. |

### MeSH knowledge graph

| Tool | What it does |
|---|---|
| `find_concepts(fragment, limit=15)` | Resolve a fuzzy term → canonical concept id (e.g. `mesh:BRCA1_Protein`). Typeahead before the graph ops below. |
| `graph_neighbors(concept_name, hops=1, limit=15)` | Concepts that co-occur with an anchor in the corpus, with co-occurrence weights. |
| `concept_papers(concept_name, limit=20)` | Papers tagged with a MeSH concept, most-recent first. |

### Biomarker → therapy (CIViC)

| Tool | What it does |
|---|---|
| `match_therapies(gene, disease?, variant?, limit=20)` | Curated **predictive** evidence, which therapies are indicated/contraindicated for a biomarker, each with a CIViC **evidence level (A=validated … E=inferential)** and a `civic:eid…` `paper_id` that passes `check_grounding`. |
| `variant_evidence(variant, limit=25)` | All CIViC evidence (predictive / diagnostic / prognostic) for a molecular profile (e.g. `BRAF V600E`, `EGFR T790M`), level-ranked. |

### Corpus management & watches

| Tool | What it does |
|---|---|
| `corpus_stats()` | Counts (papers / chunks / vectors / lexical index / CIViC / graph) plus the live embedding config. Sanity-check before reasoning. |
| `ingest_pubmed(query, max_results=20)` | Pull fresh papers from PubMed and persist (embeds + indexes). Cap 500. |
| `add_watch` · `list_watches` · `remove_watch` · `run_watch` | Standing literature watches that track new research over time (delta pulls; see [Watches](#watches)). |

---

## The grounding contract in practice

The intended agent loop (and why the gate matters):

```
pack   = summarize_evidence("first-line therapy for EGFR-mutant NSCLC")
draft  = <LLM writes claims, each citing paper_ids drawn ONLY from pack["allowed_paper_ids"]>
report = check_grounding(draft, pack["allowed_paper_ids"])
# report["grounded"] == false?  → repair every entry in report["violations"], re-check.
# present only when grounded == true.
```

`check_grounding` is intentionally **narrow and deterministic**. It verifies the *floor*,
that provenance is real and reachable, and classifies each claim:

| Status | Meaning |
|---|---|
| `grounded` | Cites ≥1 `paper_id` that exists in the corpus (and, if an envelope was given, was retrieved for this question). |
| `uncited` | No `paper_id` at all, a contract violation. |
| `phantom_citation` | Cites an id that isn't in the corpus, a fabricated or mistyped reference. |
| `off_envelope` | Cites a real corpus paper that wasn't in the retrieved evidence for this question. |

What it deliberately does **not** check: semantic entailment, whether the cited paper actually
*supports* the claim. That judgment is the LLM's job. The gate guarantees the citation is real;
the LLM is responsible for it being relevant. ([`src/medground/retrieve/grounding.py`](src/medground/retrieve/grounding.py))

---

## CLI reference

The `medground` command (prefix with `uv run` unless the venv is active).

```
ingest pubmed -q "<query>" [-n 50] [--batch 16] [--no-embed]   Search PubMed and ingest top results
ingest civic  [-n 0] [--batch 64] [--no-embed]                 Ingest CIViC evidence (0 = all ~11k)
search "<query>" [-k 8]                                         Hybrid retrieval; prints cited hits
stats                                                           Counts across all local stores
index                                                          Rebuild the BM25 lexical index
reembed [--batch 128]                                          Re-embed all chunks (after a provider switch)
compact                                                        Rebuild the DuckDB file, reclaiming bloat
graph stats | rebuild | find <fragment> | neighbors <name>     MeSH graph inspection
watch add | list | remove | enable | disable | run | daemon    Manage research watches
mcp                                                            Start the MCP stdio server (one per client)
serve [--host H] [--port 8765]                                 Run ONE shared HTTP MCP server (multi-client)
```

Maintenance notes:
- **`reembed`**, run after changing `MG_EMBED_PROVIDER`/`MG_EMBED_MODEL`. The vector table is
  recreated at the new dimension; chunks and papers are untouched.
- **`index`**, ingestion rebuilds the BM25 index automatically; run this manually only after a
  migration or if lexical search returns nothing on a corpus you know is populated.
- **`compact`**, DuckDB never shrinks a file in place, and HNSW/FTS churn bloats it (the corpus
  file is multi-GB). This rebuilds into a fresh, compact file. **Stop the MCP server first** (the
  data dir is single-owner). ([ADR-0016](docs/decisions/0016-db-storage-and-vector-search-optimization.md))
- **`graph rebuild`**, wipes and rebuilds the graph from DuckDB so the current MeSH stop-list is
  applied cleanly.

---

## Configuration

All knobs are environment variables (set in `.env` or the real environment). Resolution order:
real env var → `.env` → default. Source of truth:
[`src/medground/config.py`](src/medground/config.py).

| Variable | Default | Purpose |
|---|---|---|
| `MG_DATA_DIR` | `./data` | Storage root (DuckDB + KuzuDB). **Use an absolute path** so the MCP server finds it regardless of CWD. |
| `MG_EMBED_PROVIDER` | `openai` | `openai` · `fastembed` (local) · `voyage`. |
| `MG_EMBED_MODEL` | `text-embedding-3-large` | Model name for the provider. |
| `MG_EMBED_DIM` | `3072` | Embedding dimension, must match the model. |
| `MG_EMBED_BATCH` | `128` | Texts per embedding API call. |
| `OPENAI_API_KEY` | (none) | Required when provider = `openai`. |
| `VOYAGE_API_KEY` | (none) | Required when provider = `voyage`. |
| `MG_NCBI_EMAIL` | (none) | Sent to NCBI as a courtesy (not a secret). |
| `MG_NCBI_API_KEY` | (none) | Raises NCBI rate limit 3 → 10 req/s. |
| `MG_CIVIC_API_KEY` | (none) | Optional CIViC bearer token (reads are open; not required). |
| `MG_CHUNK_CHARS` | `1200` | Target chunk size (characters). |
| `MG_CHUNK_OVERLAP` | `150` | Overlap between long-section windows. |
| `MG_TOP_K` | `8` | Default hit count for `search`. |
| `MG_WATCH_IN_SERVER` | `false` | Run the watch loop *inside* the MCP server (see [Watches](#watches)). |
| `MG_WATCH_TICK` | `300` | In-server scheduler poll interval (seconds). |
| `MG_HTTP_TIMEOUT` | `30` | HTTP timeout (seconds). |
| `MG_HTTP_CONCURRENCY` | `4` | Max concurrent source requests. |
| `MG_ENV_FILE` | (none) | Explicit path to a `.env` to load first. |

---

## Embeddings

The embedding layer is a provider-agnostic facade selected purely by config
([`src/medground/nlp/embeddings.py`](src/medground/nlp/embeddings.py)). Output is always
L2-normalized, so cosine == dot product regardless of provider.

| Provider | Model | Dim | Key | Notes |
|---|---|---|---|---|
| **`openai`** (default) | `text-embedding-3-large` | 3072 | `OPENAI_API_KEY` | Top-tier quality. |
| `fastembed` | `BAAI/bge-small-en-v1.5` | 384 | none | Local ONNX, offline after a one-time download, **zero-cost**. |
| `voyage` | `voyage-3-large` | 1024 | `VOYAGE_API_KEY` | Anthropic's partner; biomedical-strong. |

Switching provider means switching **model + dim together** (the vector index is dim-specific).
Set all three, then run `medground reembed` to rebuild the vector table. See
[ADR-0010](docs/decisions/0010-hosted-embeddings.md) and
[ADR-0015](docs/decisions/0015-local-first-embeddings.md).

---

## Data sources

Each source is an async client that fetches, parses, and yields `Paper` records, it never touches
storage. Everything flows through one shared ingestion pipeline (chunk → embed → vectors → graph →
BM25). ([`src/medground/sources/`](src/medground/sources/))

- **PubMed** (NCBI E-utilities), the literature backbone: `esearch → efetch → parse`. Section-aware
  chunking honors labeled abstract sections; MeSH terms become graph concepts.
- **CIViC** (GraphQL), curated, expert-moderated **variant → disease → therapy** evidence. Each item
  enters twice: as a **groundable document** (so it's searchable and citable like any paper) *and* into
  a structured `civic_evidence` table that powers the `match_therapies` / `variant_evidence` tools with
  A to E evidence levels. PubMed `citationId`s link CIViC items back to the literature.
  ([ADR-0017](docs/decisions/0017-multi-source-civic.md))

*Planned (not yet wired): EuropePMC, ClinicalTrials.gov, OpenAlex, bioRxiv/medRxiv preprints;
see [ADR-0009](docs/decisions/0009-source-priorities.md).*

---

## Watches

A **watch** is a standing subscription `(label, query, source, cadence)` that tracks new research
over time. Each run pulls only the delta since the last run (PubMed `edat` cursor), skips PMIDs
already in the corpus, and advances a cursor on success. Multiple watches run concurrently behind a
shared NCBI rate-limit semaphore.

```bash
medground watch add -l brca-parp -q "BRCA PARP inhibitor resistance" --every 1d
medground watch list
medground watch run brca-parp          # run now, bypassing the cadence
```

Two ways to run the scheduler, pick one (they'd fight for the exclusive file lock otherwise):
- **In-server (recommended):** set `MG_WATCH_IN_SERVER=1` so the loop runs *inside* the MCP server
  process, tracking new research alongside the agent. Off by default, background ingestion embeds,
  which costs money.
- **Standalone daemon:** `medground watch daemon`, only when the MCP server is **not** running.

([ADR-0014](docs/decisions/0014-concurrency-single-owner.md))

---

## Storage & concurrency

- **One DuckDB file** holds `papers`, `chunks`, the HNSW vector index (`vss` extension), the BM25
  index (`fts` extension), `civic_evidence`, and ingestion/watch bookkeeping. Vectors live in a
  separate table from chunk text, so swapping embedding provider only recreates the vector table.
- **One KuzuDB file** holds the MeSH graph: `Paper` and `Concept` nodes, `MENTIONS` edges.
- **No foreign key** on `chunks.paper_id`, a deliberate choice; DuckDB can't delete/update
  FK-referenced rows with list columns, which broke idempotent re-ingest and FTS-rebuild WAL replay.
  Integrity is enforced by the pipeline instead. ([ADR-0016](docs/decisions/0016-db-storage-and-vector-search-optimization.md))
- **Single-owner model:** the process owns the data dir via one connection per store + one
  re-entrant `DB_LOCK`. Reads are serialized too (cheap at this scale, one user), this is what lets
  an in-server watch loop write while tool calls read. ([`src/medground/runtime.py`](src/medground/runtime.py))
- **One writer across processes:** startup takes an exclusive `flock` on `<data_dir>/.medground.lock`,
  so a second `medground` process (server or CLI) is refused cleanly instead of racing for the file.
  To share the corpus among many clients, run one HTTP server (`medground serve`) and connect them by
  URL. ([ADR-0018](docs/decisions/0018-http-transport-and-single-owner-lock.md))

---

## Development

```bash
uv sync                 # install deps (incl. dev group)
uv run pytest           # 24 tests, no network; store tests use isolated temp dirs
uv run ruff check .     # lint (line-length 100, py311 target)
uv run ruff format .    # format
```

> Tests that touch the store use a temp data dir. The singleton test opens the real `MG_DATA_DIR`;
> if the MCP server is holding the lock, run with a throwaway dir:
> `MG_DATA_DIR="$(mktemp -d)" uv run pytest`.

Project layout:

| Path | Concern |
|---|---|
| `src/medground/config.py`, `models.py` | Config + Pydantic domain types |
| `src/medground/store/{docs,vectors,lexical,graph}.py` | DuckDB doc/vector/FTS stores + KuzuDB graph |
| `src/medground/runtime.py` | Single-owner stores + `DB_LOCK` + `@locked` |
| `src/medground/sources/{pubmed,civic}.py` | Source adapters (fetch → `Paper`) |
| `src/medground/ingest/{chunker,pipeline}.py` | Section-aware chunking + the ingestion pipeline |
| `src/medground/nlp/embeddings.py` | Provider-agnostic embedding facade |
| `src/medground/retrieve/{hybrid,grounding}.py` | RRF hybrid retrieval + the grounding gate |
| `src/medground/watch/service.py` | Watch executor + daemon |
| `src/medground/mcp/server.py` | MCP server (17 tools) |
| `src/medground/cli.py` | The `medground` CLI |
| `docs/decisions/` | 18 Architecture Decision Records (Nygard format) |
| `skills/` + `install-skills.sh` | The `/doc` Claude Code skills + their installer |
| `install.sh` | Guided one-command setup (uv sync → `.env` → corpus → MCP → skills) |
| `HOWTOUSE.md` · `EXAMPLES.md` | Plain-English usage guide + copy-paste prompts |

---

## Project status

**Shipped:** PubMed + CIViC ingestion · hybrid RRF retrieval (vector + BM25 + MeSH graph) ·
deterministic grounding gate · biomarker→therapy matching with evidence levels · research watches ·
17-tool MCP server · three embedding providers · single-file embedded storage with compaction.

**Planned:** more sources (EuropePMC, ClinicalTrials.gov, OpenAlex, preprints) · richer entity
extraction beyond MeSH (scispaCy / LLM relation extraction) · an evaluation harness (held-out
questions + grounding/retrieval metrics).

Rationale for every major choice is recorded as an [ADR](docs/decisions/); the running narrative is
in [`docs/decisions/BUILD_LOG.md`](docs/decisions/BUILD_LOG.md).

---

## License

Released under the [MIT License](LICENSE), © 2026 Arnaud Tauveron. Permissive: use, modify, and
build on it freely, including commercially. If you build on it, please **preserve the grounding loop**
and keep the safety notices (see [`SAFETY.md`](SAFETY.md)), the discipline is the point.

---

## Disclaimer

medground is a **research and decision-support** tool that surfaces and grounds what the published
literature says. It does **not** provide medical advice, diagnosis, dosing, or treatment orders, and
it is **not** a substitute for a qualified clinician or a multidisciplinary tumor board. Always defer
clinical decisions to the treating team.

**See [`SAFETY.md`](SAFETY.md)** for intended use, out-of-scope uses, the limits of the grounding gate,
corpus limitations, and responsible-use guidance.
