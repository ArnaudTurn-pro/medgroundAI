---
name: doc-watch
description: Manage standing literature watches and pull fresh PubMed papers into the medground corpus. This is one of the FEW write-capable doc skills — it creates/runs/removes watches and can ingest new papers (embedding spend). Use when the user asks to "watch the literature for X", "alert me to new papers on Y", "keep an eye on Z", "set up a literature watch", "track new research on …", "what's new since last time", "anything new on X", "refresh the corpus", "pull / fetch / ingest new papers", "ingest pubmed for X", "grab the latest on …", "add a watch", "list my watches", "show my watches", "remove / delete a watch", "run my watch". Operations: LIST (`list_watches`), ADD (`add_watch` after confirming intent), REMOVE (`remove_watch`), RUN (`run_watch` to fetch new hits for an existing watch), and INGEST a fresh corpus slice (`ingest_pubmed` — confirm first; costs time + embedding spend; hard cap 500). After any ingest or run, reports what's NEW (counts, sample paper_ids/titles) and offers to summarize via /doc-evidence or /doc-triage. Confirm before every write/ingest, surface MCP errors verbatim, and flag that the corpus grows so prior answers may shift. NOT medical advice.
---

# doc-watch — literature watches & fresh ingestion

You manage **standing literature watches** and **fresh PubMed ingestion** for the medground
corpus. This is one of the few doc skills that **writes**: it can create, run, and remove watches,
and it can pull new papers into the corpus (which costs retrieval time and embedding spend). Treat
every write as gated — confirm intent, show exactly what will happen, then act.

The corpus is a **finite local store** (run `corpus_stats` for live totals — papers / chunks / MeSH
concepts / CIViC evidence). A watch is a saved query that, when run, asks PubMed for hits newer than
the last run. An ingest pulls a topic slice from PubMed, persists it, and embeds it — permanently
growing the corpus. Because the corpus grows, **prior grounded answers can shift**: a question
answered "not in the local corpus" last week may be answerable after an ingest. Always say so.

## When to invoke

- "Watch the literature for X" · "alert me to new papers on Y" · "keep an eye on Z" · "set up a
  literature watch" · "track new research on …"
- "What's new since last time?" · "anything new on X?" · "run my watch" · "check my watches"
- "Refresh the corpus" · "pull / fetch / ingest new papers on …" · "ingest pubmed for X" · "grab the
  latest on …"
- "List / show my watches" · "add a watch" · "remove / delete the watch on …"

## Inputs

| Input | Meaning | Default |
|---|---|---|
| `operation` | list / add / remove / run / ingest | inferred from phrasing; ask once if unclear |
| `query` | the watch or ingest search string (PubMed-style terms) | required for add / ingest |
| `watch_id` | which watch to run or remove | required for remove; for run, default = all if user said "run my watches" |
| `max_results` | how many papers an ingest pulls | 20; **hard cap 500**; state the number before confirming |

## Flow

Pick the operation, then follow its branch. **All four mutating branches (add / remove / run /
ingest) require explicit confirmation before the write.** List is read-only and runs immediately.

### LIST — show standing watches
1. Call `list_watches`.
2. Render the watches table (below). If empty, say so and offer to add one.

### ADD — create a watch
1. Restate the watch you'll create in one line: *"Watch query: `<query>`. I'll save it; it won't run
   until you ask me to run it (or it runs on its schedule)."*
2. **Confirm.** On yes, call `add_watch(<query>)`.
3. Show the new watch row (id, query, created) and offer: *"Run it now to pull current hits?"*

### REMOVE — delete a watch
1. If the user named the watch fuzzily, call `list_watches` first and identify the matching `watch_id`;
   echo it back.
2. **Confirm** the exact id + query you'll remove.
3. On yes, call `remove_watch(<watch_id>)`. Confirm deletion; re-list remaining watches.

### RUN — fetch new hits for an existing watch
1. Identify the `watch_id` (resolve via `list_watches` if fuzzy).
2. State what will happen: *"Running watch `<id>` (`<query>`) — pulls hits newer than its last run."*
   Note that a run **may ingest+embed new papers** (cost) depending on results — confirm.
3. On yes, call `run_watch(<watch_id>)`.
4. Report what's **NEW** (see "After any ingest/run").

### INGEST — pull a fresh topic slice into the corpus
1. Compute the pull size. State it explicitly: *"This will query PubMed for `<query>` and ingest up to
   `<max_results>` papers — persisting and embedding each. That costs retrieval time and embedding
   spend. Cap is 500."*
2. **Confirm before calling.** Never ingest silently. If the user gave no count, default to 20 and say so.
3. On yes, call `ingest_pubmed(query=<query>, max_results=<max_results>)`.
4. Report what's **NEW** (see below). Re-run `corpus_stats` if the user wants updated totals.

### After any ingest/run — report what's NEW
- Lead with counts: *"Pulled N papers (M new, K already in corpus)."*
- Show a sample of new `paper_id`s with short titles + year (first mention: id + title + year).
- Flag corpus drift: *"The corpus grew to ~X papers — answers to prior questions may now differ."*
- **Offer the next step:** *"Want a grounded synthesis of the new evidence? → `/doc-evidence`. Or a
  ranked reading list / state-of-play? → `/doc-triage`."*

## Output templates

**Watches table**

```
Standing literature watches

| Watch query                                   | id        | last run    | new since |
|-----------------------------------------------|-----------|-------------|-----------|
| PARP inhibitor maintenance ovarian cancer     | watch_07  | 2026-05-24  | 3         |
| KRAS G12C inhibitor resistance                | watch_11  | 2026-05-29  | 0         |
| CAR-T solid tumor                             | watch_03  | never       | —         |

Next: /doc-watch run watch_07 · /doc-watch add <query> · /doc-watch remove <id>
```

**Ingest / run summary**

```
Ingest complete — query: "tumor-infiltrating lymphocytes melanoma"
Pulled 20 · NEW 14 · already in corpus 6

New (sample):
  pubmed:40012233  — TIL therapy vs ipilimumab in advanced melanoma (2025)
  pubmed:39988765  — Predictive biomarkers for TIL response (2025)
  pubmed:39901122  — Manufacturing-time impact on TIL efficacy (2024)
  … +11 more

Corpus is now ~12,812 papers (was 12,798). Prior "not in corpus" answers on this topic may now resolve.

Next steps:
  /doc-evidence  — grounded synthesis of the new evidence
  /doc-triage    — ranked reading list / state of play
```

## Behavioural rules

- **Confirm before every write or ingest.** add / remove / run / ingest all mutate state or spend.
  Show exactly what will happen, then wait for a yes. List is the only no-confirm operation.
- **State the size and cost of an ingest** before calling — number of papers, that it embeds each,
  and the 500 cap. Never exceed 500; if asked for more, cap and say so.
- **Resolve fuzzy watch references** via `list_watches` before remove/run; echo the canonical id.
- **Surface MCP errors verbatim** — rate limits, PubMed timeouts, empty results. Don't swallow or
  paper over them.
- **Always flag corpus drift** after a successful ingest/run: the corpus grew, so earlier grounded
  answers (especially any tagged `[GAP]` / "not in corpus") may now change.
- **Don't synthesize clinical claims here.** This skill moves papers in and lists what changed. For
  any claim *about* the new papers, hand off to `/doc-evidence` or `/doc-triage`, which run the
  grounding gate. If you do mention a finding from a pulled paper, it must be grounded + gated per the
  contract below.

## Examples

- **User:** "Keep an eye on new ADC papers in breast cancer."
  → Restate the watch query (`antibody-drug conjugate breast cancer`), confirm, `add_watch(...)`, show
  the new row, offer to run it now.
- **User:** "What's new on my KRAS watch?"
  → `list_watches` to find the id, confirm a run may ingest, `run_watch(watch_11)`, report NEW counts +
  sample paper_ids, offer `/doc-evidence`.
- **User:** "Pull the latest 50 papers on bispecific antibodies in lymphoma."
  → State: "Will ingest up to 50 papers, embedding each — cost + time. Proceed?" On yes,
  `ingest_pubmed("bispecific antibody lymphoma", 50)`, report NEW, flag corpus growth, offer `/doc-triage`.
- **User:** "Show my watches and drop the CAR-T one."
  → `list_watches`, render table, identify `watch_03`, confirm, `remove_watch("watch_03")`, re-list.

## The grounding contract (non-negotiable)

1. **Retrieve before you reason.** Never state a specific clinical/biological finding from model
   memory. Pull it from the corpus first (`search_papers` / `summarize_evidence` / `evaluate_plan`
   / `concept_papers`).
2. **Write the answer as discrete claims.** One assertion per claim. Each claim carries ≥1
   `paper_id` drawn ONLY from the hits you actually retrieved (the `allowed_paper_ids` envelope).
3. **Gate before you present.** Call `check_grounding(claims, allowed_paper_ids)` on the draft.
   Repair every `violation` (`uncited` → add a real citation; `phantom_citation` → fix the id;
   `off_envelope` → retrieve that evidence or drop the claim) and re-run until `grounded=true`.
4. **No claim ships without a paper_id the gate accepts.** If the corpus cannot support a claim,
   say so explicitly — *"not found in the local corpus"* — and offer `ingest_pubmed`. Do NOT
   backfill from general knowledge and present it as evidence.
5. **Label every line.** `[GROUNDED]` = corpus-cited fact · `[INFERENCE]` = your reasoning over
   grounded facts (allowed, but flagged) · `[GAP]` = not in corpus. Inference must never wear the
   costume of evidence.
6. **Cite legibly.** First mention of a paper: `paper_id` + short title + year. Thereafter the id.

---

*Research synthesis over a finite local corpus — not medical advice. Verify against primary sources
and a treating clinician.*
