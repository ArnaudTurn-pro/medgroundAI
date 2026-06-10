---
name: doc-evidence
description: The core grounded evidence-synthesis workflow over the medground oncology-literature corpus. Use this for any clinical or research question that wants a sourced, fact-checked answer — "what's the evidence for X", "what does the literature say about Y", "evidence summary on Z", "is X effective in Y", "summarize the research on …", "how well does drug A work in disease B", "what's known about biomarker C", PICO-style questions (population / intervention / comparator / outcome), and "give me a grounded answer on …". Decomposes the question into clinical facets (efficacy / safety / biomarkers / mechanism / comparators) via summarize_evidence — plus curated CIViC match_therapies / variant_evidence (leveled A–E) for biomarker-driven questions — drafts the answer as discrete labelled claims each citing only retrieved paper_ids, then runs check_grounding as a deterministic gate and repairs every violation until grounded=true before presenting. Every claim is tagged [GROUNDED] / [INFERENCE] / [GAP], carries inline paper_ids, and is backed by a Citations block. NOT medical advice — grounded research synthesis only. Drill into /doc-paper-appraisal, /doc-contradictions, /doc-find-paper for follow-ups.
---

# doc-evidence

The grounded evidence engine of the `doc` profile. Turn a clinical/research question into a **sourced, gate-checked answer** — organized by facet, every line labelled, no claim shipped without a `paper_id` the grounding gate accepts.

This is the workhorse. Distinct from:

- `/doc` (router) — picks ONE specialist; doesn't synthesize the full evidence pack.
- `/doc-triage` — vague "where's the evidence / what should I read" scans → ranked reading list; descriptive, not a fully gated answer.
- `/doc-grounding-check` — audits an *external* draft/plan you paste in; this skill *builds* a grounded answer from scratch.
- `/doc-contradictions` — surfaces disagreement on a point; this skill answers the whole question and routes there when facets conflict.

## When to invoke

Trigger phrases (exact or paraphrased):
- *"What's the evidence for <intervention> in <disease>?"*
- *"What does the literature say about <topic>?"* / *"Summarize the research on <X>."*
- *"Is <drug> effective in <population>?"* / *"How well does <X> work for <Y>?"*
- *"Evidence summary on <biomarker / pathway / regimen>."*
- *"What's known about <gene / mutation / mechanism>?"*
- A **PICO** question — Population, Intervention, Comparator, Outcome — phrased clinically.

If the user pastes a draft and asks *"is this supported?"* → that's `/doc-grounding-check`, not this.
If the user wants a single paper graded → `/doc-paper-appraisal`.
If the question is vague ("what should I read about X?") → `/doc-triage`.

If a **Patient Context Block** from `/doc-case` is present in the conversation, thread its diagnosis / histology / stage / biomarkers / prior-therapy terms into every facet query — don't answer in the abstract when a patient context exists.

## Inputs

| Input | Default | Notes |
|---|---|---|
| `question` | — (required) | The clinical/research question. Restate it as your interpretation before retrieving. |
| `facets` | `efficacy, safety, biomarkers, mechanism, comparators` | Override only if the question is clearly single-facet (e.g. pure mechanism question → `mechanism` only). |
| `k_per_facet` | 5 | Hits retrieved per facet by `summarize_evidence`. Raise for a broad question, cap sensibly. |
| `patient_context` | unset | If a `/doc-case` Patient Context Block is in scope, fold its terms into every facet query. |
| `depth` | `standard` | `standard` = one summarize_evidence pass. `deep` = also deepen thin facets with targeted `search_papers` / `get_paper_chunks`. |

## The grounding contract (non-negotiable)

```
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
```

## Flow

### Step 1 — State your interpretation (one line)

Restate the question so the user can catch a misread before any retrieval:

> *"Reading this as: efficacy + safety of PARP inhibitors in BRCA-mutated ovarian cancer, with biomarker and comparator context. Synthesizing across 5 facets, then gating before I answer."*

If a Patient Context Block is in scope, name what you're folding in:
> *"Threading the case context (high-grade serous ovarian, BRCA1+, platinum-sensitive, 2 prior lines) into every facet."*

### Step 2 — Retrieve the evidence pack

```
summarize_evidence(question="<the question, with patient-context terms folded in>",
                   k_per_facet=5,
                   facets=null)   # null = default 5 facets; pass a list to override
```

This returns `{question, facets:[{name, query, hits:[...]}], suggested_structure:[...]}` plus an **`allowed_paper_ids`** envelope. Capture that envelope verbatim — it is the citation whitelist the gate enforces in Step 5. Do **not** synthesize from any paper_id outside it.

### Step 3 — Deepen thin facets (depth=deep, or any facet that came back empty/weak)

For any facet whose `hits` are sparse, off-target, or thin on a sub-question:

- **Biomarker question? Call CIViC.** If the question turns on a gene/variant (e.g. "is osimertinib active against EGFR T790M?", "what's actionable for BRAF V600E?"), call `match_therapies(gene, disease, variant)` / `variant_evidence(variant)` — curated, **A–E-leveled** biomarker→therapy evidence whose `civic:eid…` ids are in the corpus and pass the gate. These feed the Biomarkers + Efficacy facets with a leveled signal; add their ids to your working envelope.
- `search_papers(query="<sharper, facet-specific query>", k=8)` — pull more targeted hits. Every new hit's `paper_id` is **added to your working envelope** (you retrieved it, so the gate will accept it).
- `get_paper_chunks(paper_id)` — when a single hit is clearly central and you need its full section-by-section detail (e.g. the exact HR, CI, or AE rate).
- `get_paper(paper_id)` — for the full record (abstract, MeSH terms, journal, year) when you need to cite legibly or judge relevance.

Keep the envelope as the union of every `paper_id` you actually retrieved across Steps 2-3. Nothing else may be cited.

### Step 4 — Draft the answer as discrete claims

Decompose your answer into **one assertion per claim**, grouped by facet. Each claim object:

```json
{"text": "PARP-inhibitor maintenance extended PFS vs placebo in BRCA-mutated platinum-sensitive ovarian cancer.",
 "citations": ["pubmed:30345884"]}
```

Rules while drafting:
- Each claim cites ≥1 `paper_id` **from the envelope only**.
- Mark each as `[GROUNDED]` (corpus-cited fact), `[INFERENCE]` (your reasoning over grounded facts — allowed but flagged), or `[GAP]` (not in corpus — no citation, says so plainly).
- `[INFERENCE]` claims are NOT sent to the gate as grounded; they're presented separately and clearly labelled. Never let inference wear the costume of evidence.
- If a facet has no support in the corpus, that's a `[GAP]` line — don't backfill from general knowledge.

### Step 5 — Gate before you present (mandatory, loop until clean)

Send every `[GROUNDED]` claim through the deterministic gate with the envelope:

```
check_grounding(claims=[{text, citations}, ...], allowed_paper_ids=<envelope from Step 2-3>)
```

Returns `{grounded, grounded_ratio, n_claims, claims:[{text,citations,status,problems}], violations}`.

Repair **every** violation, then **re-run** `check_grounding` until `grounded=true`:

| `status` | Meaning | Repair |
|---|---|---|
| `grounded` | citation valid & inside envelope | keep |
| `uncited` | no citation | add a real `paper_id` from the envelope, or relabel as `[INFERENCE]`/`[GAP]` |
| `phantom_citation` | cites a paper_id not in the corpus | fix the id (you likely transcribed it wrong) or drop the claim |
| `off_envelope` | cites a real corpus paper you did NOT retrieve for this question | retrieve it (`search_papers`) to bring it into the envelope, or drop the claim |

Do not present a single grounded line until the gate returns `grounded=true`. Report the final `grounded_ratio`.

### Step 6 — Render

Organize by facet, label every line, attach the Citations block and the grounded_ratio. Template below.

---

## Output template

```
# Evidence — <question> · <date>
Corpus: local store · Facets: Efficacy · Safety · Biomarkers · Mechanism · Comparators
Grounding gate: PASSED · grounded_ratio <0.00–1.00>

## TL;DR
<2-3 sentences naming the headline grounded finding and the single biggest caveat/gap.>

## Efficacy
- [GROUNDED] <claim>. (pubmed:30345884 — "Maintenance Olaparib in Ovarian Cancer", 2018)
- [GROUNDED] <claim>. (pubmed:30345884)
- [INFERENCE] <reasoning that combines the two grounded facts above — flagged, not evidence>.

## Safety
- [GROUNDED] <claim>. (pubmed:31562799 — "<short title>", 2019)
- [GAP] <sub-question the corpus does not answer> — not found in the local corpus.

## Biomarkers
- [GROUNDED] <claim>. (pubmed:...)

## Mechanism
- [GROUNDED] <claim>. (pubmed:...)

## Comparators
- [GROUNDED] <claim>. (pubmed:...)
- [INFERENCE] <head-to-head inference, flagged as not directly tested in the corpus>.

## Citations
| paper_id | title | year | journal |
|---|---|---|---|
| pubmed:30345884 | Maintenance Olaparib in Newly Diagnosed Ovarian Cancer | 2018 | NEJM |
| pubmed:31562799 | <title> | 2019 | <journal> |

## Not covered by the corpus
- <facet/sub-question the corpus is silent on>.
- <another gap>.
→ To extend coverage, ingest fresh PubMed papers: `ingest_pubmed("<targeted query>")` (via /doc-watch).

## Drill down
- Grade the strength of the key paper → /doc-paper-appraisal pubmed:30345884
- The Efficacy and Comparators facets disagree — surface the controversy → /doc-contradictions
- Track down a specific paper by fragment/author → /doc-find-paper

---
*Research synthesis over a finite local corpus — not medical advice. Verify against primary sources and a treating clinician.*
```

When facets **agree cleanly**, drop the `/doc-contradictions` drill-down. When the answer rests on one or two pivotal papers, always offer `/doc-paper-appraisal` on them.

---

## Behavioural rules

- **Read-only on the corpus.** This skill never mutates. The only writes are an explicit `ingest_pubmed` the user asks for (route via `/doc-watch`).
- **The gate is mandatory and non-skippable.** No grounded line is presented until `check_grounding` returns `grounded=true`. If you cannot reach `grounded=true` for a claim, the claim does not ship — it becomes a `[GAP]` line.
- **Envelope discipline.** Cite only `paper_id`s you actually retrieved this turn. An `off_envelope` flag means "you're citing real corpus evidence you didn't pull for this question" — retrieve it or drop it.
- **Never backfill from model memory.** A confident-sounding clinical fact with no corpus support is the headline failure mode. Say *"not found in the local corpus"* and offer `ingest_pubmed`.
- **Use CIViC for biomarker-driven questions.** A gene+variant question is best answered by `match_therapies` / `variant_evidence` (curated, A–E leveled, `civic:eid` pre-grounded) alongside `summarize_evidence` — not literature search alone.
- **Inference is allowed but quarantined.** `[INFERENCE]` lines reason over grounded facts and are clearly flagged; they are not sent to the gate as evidence and never presented as `[GROUNDED]`.
- **Surface contradictions, don't smooth them.** If two facets (or two papers) conflict, say so and route to `/doc-contradictions`. Do not average disagreement into false consensus.
- **Cite legibly.** First mention: `paper_id` + short title + year. Thereafter the bare id.
- **Surface MCP errors verbatim.** Never swallow a tool error; show it and stop.
- **Report the grounded_ratio.** It's the honesty metric — always print it.
- **Finite corpus honesty.** When the question clearly exceeds the corpus domain, say so up front and offer `ingest_pubmed` rather than answering thinly.

## Composition with other skills

- Built on the same MCP primitives the `evidence-synthesist` agent lives by.
- If the user pastes their *own* draft to be checked → hand to `/doc-grounding-check` (audit), not this (build).
- If a single paper needs quality grading → `/doc-paper-appraisal`.
- If facets conflict → `/doc-contradictions`.
- If a paper reference is fuzzy → `/doc-find-paper`; if a biomedical term is fuzzy → `/doc-find-concept` (resolve to `mesh:` id, then re-query).
- For fresh literature beyond the corpus → `/doc-watch` (`ingest_pubmed`).

## Examples

**User**: *"What's the evidence for PARP inhibitors in BRCA-mutated ovarian cancer?"*
→ Restate interpretation; `summarize_evidence` across all 5 facets; draft claims per facet citing only the envelope; `check_grounding` → repair → re-check to `grounded=true`; render the faceted answer + Citations + grounded_ratio + gaps. Offer `/doc-paper-appraisal` on the pivotal trial.

**User**: *"Is pembrolizumab effective in PD-L1-high non-small-cell lung cancer, and what's the comparator picture?"*
→ Same flow, emphasis on Efficacy + Comparators facets. Any head-to-head not directly tested in corpus → `[INFERENCE]` line, flagged. Route to `/doc-contradictions` if response-rate findings disagree.

**User**: *"Summarize the research on tumor mutational burden as a biomarker for immunotherapy response."*
→ `summarize_evidence` with biomarker/mechanism weighting; deepen the Biomarkers facet with targeted `search_papers`; gate; render. Likely several `[GAP]` lines if corpus is thin → offer `ingest_pubmed`.

**User** (PICO): *"In HER2-low metastatic breast cancer (P), does trastuzumab deruxtecan (I) vs physician's-choice chemo (C) improve PFS (O)?"*
→ Map P/I/C/O onto facets; retrieve; build claims with exact PFS/HR figures pulled via `get_paper_chunks` from the pivotal paper; gate; render with the Comparators facet front and centre.

**User** (with a `/doc-case` block in scope): *"What are the treatment-relevant findings for this patient?"*
→ Fold the Patient Context Block (histology / stage / biomarkers / prior lines) into every facet query; otherwise the standard gated flow. End with the not-medical-advice disclaimer prominently.
