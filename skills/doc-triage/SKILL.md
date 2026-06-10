---
name: doc-triage
description: Multi-facet triage for vague research-literature questions — "what does the literature say about X", "where's the evidence", "what should I read", "what's the state of play / state of the art", "is this promising", "what's known about X", "give me the lay of the land", "what's strong vs weak evidence", "what's missing / understudied". Runs a parallel scan across several corpus dimensions (evidence backbone via summarize_evidence, concept neighborhood via the MeSH graph, volume/recency via concept_papers, a contrarian search pass for gems/contradictions), ranks findings by strength-of-evidence × relevance × novelty, and prescribes a small grounded reading list — each finding linked to a specialist `doc-*` skill for drill-down. Infers a mode from phrasing — `evidence` (what's known, default) / `discovery` (what's new/promising) / `appraisal` (how strong is the evidence) / `gaps` (what's missing). Threads an optional Patient Context Block (from /doc-case) through every query as the retrieval scope. Use whenever the question is broad, exploratory, or exec-level and no single specialist is an obvious match.
---

# doc-triage

The "what does the literature say / where's the evidence / what should I read / how strong is it / what's missing" skill — the research-literature analogue of an executive triage.

It composes lightweight queries across several corpus dimensions, ranks findings by **strength-of-evidence × relevance × novelty**, and prescribes a small reading list — each finding linked to the specialist `doc-*` skill that owns deeper drill-down. Distinct from:

- `/doc` (router) — picks ONE specialist; doesn't synthesize across dimensions.
- `/doc-evidence` — the deep grounded answer to ONE precise question; not a multi-facet scan.
- `/doc-landscape` — graph-only concept map; descriptive, not prescriptive, and not evidence-strength-aware.
- `/doc-help` — lists capabilities; doesn't run anything.

This is a **read-only** skill. It retrieves, ranks, and recommends — it never ingests, never mutates a watch, and never ships a clinical claim that hasn't passed the grounding gate.

## When to invoke

Trigger phrases (exact or paraphrased), mapped to mode:

- *"What does the literature say about X?"* / *"What's known about X?"* / *"Where's the evidence on X?"* / *"Lay of the land"* / *"State of play"* → **evidence** (default)
- *"What's new / promising / exciting in X?"* / *"Anything cutting-edge?"* / *"State of the art"* / *"Is this promising?"* → **discovery**
- *"How strong is the evidence?"* / *"Strong vs weak evidence"* / *"Is this well-supported?"* / *"How good are these data?"* → **appraisal**
- *"What's missing / understudied / under-researched?"* / *"What don't we know?"* / *"Where are the gaps?"* → **gaps**

Also use when the user asks a broad, exploratory literature question and `/doc-evidence` would be too narrow (it answers one precise question; triage maps the terrain first and tells them where to dig).

If a **Patient Context Block** (from `/doc-case`) is present in the conversation, thread it through EVERY query as the retrieval scope — the topic becomes "this patient's question" and the steered query seeds become the scan inputs. Repeat the scope back so the user sees it's being honored.

## Inputs

| Input | Default | Notes |
|---|---|---|
| `topic` | — (required) | The question or topic. Free text. If a Patient Context Block exists, the block's steered seeds augment it. |
| `mode` | inferred from phrasing | `evidence` / `discovery` / `appraisal` / `gaps`. If unclear, default to **evidence** (most generally useful). |
| `top_n` | 5 | Findings to surface in the ranked list. |
| `patient_context` | unset | A Patient Context Block from `/doc-case`. If present, it scopes every query. Don't ask for it — use it silently if it exists, skip if not. |

## Flow

### Step 1 — State your interpretation (one line)

> *"Triaging the literature on **EGFR-mutant NSCLC resistance** across 4 facets (evidence backbone · concept neighborhood · volume/recency · contrarian gems), plus a CIViC biomarker pass since EGFR is in scope. Mode: evidence. Top 5."*

If a Patient Context Block is in play, repeat the scope: *"Scoped to the patient case: EGFR L858R lung adeno, post-osimertinib."*

If the topic is plainly outside oncology, say so and stop — this corpus is cancer literature only.

### Step 2 — Parallel facet scan (ONE batch)

Issue ALL of these in a single parallel batch. Each is bounded — the goal is fast triage, not exhaustive synthesis. Capture the `allowed_paper_ids` envelope from the evidence backbone; it is the citation universe for the micro-summary in Step 5.

| # | Facet | MCP call | Yields |
|---|---|---|---|
| 1 | **Evidence backbone** | `summarize_evidence(question=<topic>, k_per_facet=5)` | per-facet hits (efficacy / safety / biomarkers / mechanism / comparators) + `allowed_paper_ids` + `suggested_structure` |
| 2 | **Concept neighborhood** | `find_concepts(<key term>)` → then `graph_neighbors(<resolved concept>, hops=1, limit=15)` | adjacent concepts + co-occurrence weights (where the topic sits in the graph) |
| 3 | **Volume / recency** | `concept_papers(<resolved concept>, limit=20)` | how many papers, how recent — a maturity / activity signal |
| 4 | **Contrarian / gems pass** | `search_papers(query="<topic> contradictory OR resistance OR failed OR unexpected OR novel", k=8)` | low-co-occurrence, controversy, first-of-kind signals for `discovery`/`gaps`/contradictions |
| 5 | **CIViC biomarker pass** (only if a gene/variant is in scope) | `match_therapies(gene, disease, variant)` / `variant_evidence(variant)` | curated, A–E-leveled biomarker→therapy matches; `civic:eid` ids pass the gate |

Mode-conditional emphasis (still one batch — just weight what you read):

- `discovery` → lean on facets #2 (bridge concepts, low-weight neighbors) and #4 (gems). A low co-occurrence weight on a real edge is a novelty signal.
- `appraisal` → lean on facet #1; read each hit's `section`/`journal`/`year` for design + recency cues; flag where the backbone is thin (few hits, old papers, one journal).
- `gaps` → cross facet #2 (concepts that *should* connect to the topic) against #3 (which of those have almost no papers). An adjacent concept with high graph weight but few `concept_papers` is an understudied seam.
- `evidence` → balance all four.

### Step 3 — Score and rank findings

A "finding" is a coherent claim-cluster surfaced by the scan (e.g. "osimertinib is first-line standard for EGFR-mutant NSCLC", "MET amplification is a recognized resistance mechanism", "combination X+Y is mechanistically rationalized but clinically unproven"). For each:

- **Evidence strength** — heuristic, made from what the hits show:
  - **Strong** — multiple papers across facets, recent, convergent; design cues suggest trials/meta-analyses.
  - **Moderate** — a few corroborating papers, some recency, no obvious contradiction.
  - **Weak** — single paper, old, or only a mechanistic/preclinical hint.
  - **Contested** — corpus contains conflicting findings (route the detail to `/doc-contradictions`).
- **Relevance** — how central to the asked topic / patient scope (high if it answers the question head-on; lower if adjacent).
- **Novelty** — high if it came from the gems pass or a low-weight graph edge; low if it's textbook backbone.

Rank by `evidence_strength × relevance × novelty`, but **weight differs by mode**:

| Mode | Ranking emphasis |
|---|---|
| `evidence` | strength × relevance (novelty as tiebreak) |
| `discovery` | novelty × relevance (strength as a caveat, not a filter) |
| `appraisal` | strength is the axis; split strong vs weak rather than blend |
| `gaps` | inverse-volume × relevance (least-studied, most-relevant first) |

Surface the top `top_n` (default 5).

### Step 4 — Render (mode-aware)

Pick the matching template below. ALWAYS include:
- **Topic + mode + scope** in the header (name the patient scope if present).
- **TL;DR** — one or two sentences naming the headline finding(s).
- A grounded **micro-summary** (Step 5) — the few load-bearing claims, cited and gated.
- **What I didn't check** — transparency about facets/sections skipped.
- **Drill-down skill per finding** — never end a finding at a count.

### Step 5 — Ground the micro-summary BEFORE presenting

The ranked table is navigational, but any sentence that asserts a clinical/biological *fact* must pass the gate. Write the micro-summary as discrete claims, each citing `paper_id`s drawn ONLY from the `allowed_paper_ids` envelope returned by `summarize_evidence` in Step 2 (CIViC `civic:eid` ids from facet 5 also pass the gate). Call `check_grounding(claims, allowed_paper_ids)`. Repair every violation and re-run until `grounded=true`. Label every line `[GROUNDED]` / `[INFERENCE]` / `[GAP]`. If a finding can't be grounded, present it as `[GAP] — not found in the local corpus` and offer `/doc-watch` to ingest fresh PubMed.

---

## Output templates

### `evidence` — "what does the literature say" (default)

```
# Literature triage — <topic> · <date>
Scope: local cancer corpus<, patient case: …> · Mode: evidence

## TL;DR
The corpus supports <X> strongly and <Y> moderately; <Z> is contested. Start here: [#1].

## Findings (ranked by evidence strength × relevance)
| # | Finding | Evidence strength | # papers | Drill-down |
|---|---|---|---|---|
| 1 | Osimertinib is the first-line standard for EGFR-mutant NSCLC | Strong | 12 | /doc-evidence |
| 2 | T790M is the dominant first-gen-TKI resistance mechanism | Strong | 8 | /doc-landscape |
| 3 | MET amplification drives osimertinib resistance | Moderate | 4 | /doc-synthesis |
| 4 | Efficacy of combo TKI + MET inhibitor | Contested | 3 | /doc-contradictions |
| 5 | CNS penetration advantage of osimertinib | Moderate | 2 | /doc-paper-appraisal |

## Grounded micro-summary
- [GROUNDED] Osimertinib improves PFS vs first-gen EGFR-TKIs in EGFR-mutant NSCLC (pubmed:XXXXXXXX, "FLAURA…", 2018).
- [GROUNDED] T790M emerges in ~50–60% of patients progressing on first-gen TKIs (pubmed:XXXXXXXX, 2015).
- [INFERENCE] Because #2 and #3 are distinct mechanisms, resistance triage likely needs re-biopsy — reasoning over grounded facts, not itself a corpus claim.

## What I didn't check
- Safety/toxicity facet in depth (run /doc-evidence scoped to "osimertinib adverse events").
- Single-paper appraisal / risk of bias (run /doc-paper-appraisal on #1).
- Fresh PubMed beyond the local corpus — it's finite (run /doc-watch to ingest).

Research synthesis over a finite local corpus — not medical advice. Verify against primary sources and a treating clinician.
```

### `discovery` — "what's new / promising"

Lead with novelty, not the textbook backbone.

```
# Promising leads — <topic> · <date>
Scope: local corpus · Mode: discovery

## TL;DR
Three leads stand out for novelty: <A>, <B>, <C>. Strength varies — treat as hypotheses to chase, not settled findings.

## Promising leads (ranked by novelty × relevance)
1. **Bridge finding: <concept A> ↔ <concept B>** — low graph co-occurrence (weight N) but a real edge; an under-explored mechanistic link. Strength: Weak/preclinical. → /doc-gems, then /doc-synthesis to stress-test the coupling.
2. **First-of-kind result: <finding>** — single recent paper, no corroboration yet. → /doc-paper-appraisal to judge whether it's solid or fragile.
3. **Contrarian signal: <finding> contradicts <consensus>** — → /doc-contradictions.

## Grounded micro-summary
- [GROUNDED] <novel finding> reported in (pubmed:XXXXXXXX, "...", 2024).
- [GAP] No corroborating paper in the corpus for <lead B> — single-source. Offer /doc-watch to pull more.

## What I didn't check
- Whether these leads survive critical appraisal (run /doc-paper-appraisal).
- The mature/consensus evidence (that's the /doc-triage `evidence` mode).

Research synthesis over a finite local corpus — not medical advice. Verify against primary sources and a treating clinician.
```

### `appraisal` — "how strong is the evidence"

Split strong vs weak — don't blend.

```
# Evidence-strength triage — <topic> · <date>
Scope: local corpus · Mode: appraisal

## TL;DR
The case for <X> is strong (multiple recent, convergent papers); <Y> rests on thin/old/single-source evidence — treat with caution.

## Strong evidence (corroborated, recent, convergent)
| Claim | Why strong | # papers | Drill-down |
|---|---|---|---|
| <claim> | 3 papers incl. apparent trial-grade design, 2018–2023, convergent | 5 | /doc-paper-appraisal |

## Weak / fragile evidence (single-source, old, preclinical, or contested)
| Claim | Why weak | # papers | Drill-down |
|---|---|---|---|
| <claim> | single 2011 paper, mechanistic only | 1 | /doc-paper-appraisal |
| <claim> | corpus contradicts itself | 2 (conflict) | /doc-contradictions |

## Grounded micro-summary
- [GROUNDED] <strong claim> (pubmed:XXXXXXXX, 2020).
- [INFERENCE] The strength gap means <X> can anchor a synthesis while <Y> needs corroboration before relying on it.

## What I didn't check
- Formal GRADE / risk-of-bias per paper (run /doc-paper-appraisal on each).
- Whether newer PubMed evidence shifts the balance (run /doc-watch).

Research synthesis over a finite local corpus — not medical advice. Verify against primary sources and a treating clinician.
```

### `gaps` — "what's missing / understudied"

```
# Research gaps — <topic> · <date>
Scope: local corpus · Mode: gaps

## TL;DR
The corpus is thin or silent on: <A>, <B>, <C>. These are the seams worth a fresh literature pull.

## Understudied seams (most relevant, least covered first)
| # | Gap | Signal | Drill-down |
|---|---|---|---|
| 1 | <concept> is graph-adjacent to the topic (weight N) but has <2 papers | high-relevance, low-volume | /doc-landscape, then /doc-watch to ingest |
| 2 | No paper couples <finding A> with <finding B> | absent edge | /doc-synthesis (hypothesis) + /doc-watch |
| 3 | Safety/long-term data sparse vs efficacy | facet imbalance | /doc-watch scoped to "<topic> long-term safety" |

## Grounded micro-summary
- [GROUNDED] <what IS established> (pubmed:XXXXXXXX, 2019) — to delimit where the gap begins.
- [GAP] <the missing piece> — not found in the local corpus. Offer /doc-watch: `ingest_pubmed("<topic> …")`.

## What I didn't check
- Whether gaps are real or just absent from THIS finite corpus — ingest fresh PubMed to disambiguate (/doc-watch).

Research synthesis over a finite local corpus — not medical advice. Verify against primary sources and a treating clinician.
```

---

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

## Behavioural rules

- **Read-only.** Never invoke write tools. The only escape hatch is *offering* `/doc-watch` (ingest) when the corpus is too thin — never ingesting silently.
- **Parallel scan is mandatory.** All facet queries go in one batch — don't serialize.
- **CIViC pass when biomarkers are in scope.** If the topic or patient block names a gene/variant, add `match_therapies` / `variant_evidence` to the parallel batch — curated, A–E-leveled, pre-grounded biomarker evidence the literature facets otherwise miss.
- **Bound everything.** `k`/`limit` defaults as in the flow table. Triage is a map, not the territory; the deep dive is the specialist's job.
- **Gate the micro-summary.** The ranked table is navigation; any factual sentence in the micro-summary passes `check_grounding` before it ships. Non-negotiable.
- **Scope is sticky.** If a Patient Context Block is present, thread it through every facet query and name it in the header. Don't widen silently.
- **Mode inference from phrasing**:
  - "say / known / evidence / lay of the land / state of play" → `evidence` (also default)
  - "new / promising / exciting / cutting-edge / state of the art" → `discovery`
  - "strong / weak / how good / well-supported / solid" → `appraisal`
  - "missing / understudied / gaps / don't know / under-researched" → `gaps`
- **Drill-down is mandatory.** Every finding points to a specialist `doc-*` skill — never end at a count.
- **Surface contradictions, don't smooth them.** A `Contested` finding is a feature; route it to `/doc-contradictions`.
- **Finite corpus honesty.** When a facet is empty or thin, say *"not found in the local corpus"* and offer `/doc-watch` — never backfill from general knowledge.
- **Strength is heuristic** — make the scoring methodology visible if the user pushes back.

## Composition with other skills

This skill **reuses MCP query patterns**; the drill-down links are an offer, not a chained call. If the user says "drill into #2", route to the named specialist (`/doc-evidence`, `/doc-landscape`, `/doc-synthesis`, `/doc-contradictions`, `/doc-gems`, `/doc-paper-appraisal`).

If a Patient Context Block does not yet exist and the question is clearly about a specific patient, suggest running `/doc-case` first to build one, then re-run triage scoped to it.

If the user later says *"now do the same but just what's new"*, re-run with `mode=discovery` on the same topic/scope.

## Examples

**User**: *"What does the literature say about EGFR-mutant lung cancer resistance?"*
→ `evidence` mode. Parallel facet scan, ranked findings table, grounded micro-summary, drill-downs. Default scope (whole corpus).

**User**: *"Anything promising in BRCA-mutant ovarian cancer right now?"*
→ `discovery` mode. Lean on graph bridge-concepts + gems pass; promising-leads list with strength caveats; route novel single-source hits to `/doc-paper-appraisal`.

**User**: *"How strong is the evidence that PARP inhibitors help in BRCA-mutated prostate cancer?"*
→ `appraisal` mode. Strong-vs-weak split; design/recency cues per claim; each row → `/doc-paper-appraisal` (or `/doc-contradictions` if conflicting).

**User**: *"What's understudied in immunotherapy for triple-negative breast cancer?"*
→ `gaps` mode. Cross high-graph-weight adjacent concepts against low `concept_papers` volume; understudied-seams table; offer `/doc-watch` to ingest fresh PubMed.

**User** (after running `/doc-case` for a 65yo EGFR L858R lung adeno patient): *"OK, what does the evidence say for this patient?"*
→ `evidence` mode, scoped to the Patient Context Block. Steered seeds ("osimertinib resistance T790M", "MET amplification after osimertinib") seed the scan; header names the patient scope; micro-summary gated against `allowed_paper_ids`.
