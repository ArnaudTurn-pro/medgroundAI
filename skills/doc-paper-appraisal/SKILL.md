---
name: doc-paper-appraisal
description: Critical appraisal / quality scorecard of a single research paper from the medground corpus — answers "how good is this paper", "appraise this study", "critique this study", "is this study reliable", "can I trust this finding", "how much weight should I give this paper", "quality of the evidence", "grade this evidence", "risk of bias", "study design", "sample size", "methodology", "is this preclinical finding trustworthy", "is this just a small trial". Reads the paper end-to-end (get_paper + get_paper_chunks), classifies the STUDY DESIGN (meta-analysis / RCT / cohort / case-control / case series / preclinical in-vivo / in-vitro / review / editorial), assigns an EVIDENCE LEVEL (GRADE-style High/Moderate/Low/Very-Low + Oxford CEBM level), scores RISK OF BIAS by domain (selection / performance / detection / attrition / reporting) with flags (small-n, single-arm, no control, surrogate endpoint, short follow-up, retrospective, industry funding), runs a STATISTICAL sanity pass (effect size, confidence intervals, multiplicity, power, p-hacking smells), and checks CORROBORATION via the MeSH graph (concept_papers — is the finding replicated or isolated?). Emits a quality scorecard + evidence-level badge + a "how much weight to give this" verdict. Every statement about the paper is grounded (paper_id) and passes check_grounding. Appraisal judges QUALITY — it is still research synthesis, not medical advice.
---

# doc-paper-appraisal

The "how good is this paper / can I trust this finding / grade this evidence" skill.

Given a single paper, it produces a **critical-appraisal scorecard**: study design, evidence
level (GRADE + Oxford CEBM), risk-of-bias by domain, a statistical sanity pass, and a
corroboration check against the rest of the corpus — ending in a one-paragraph verdict on **how
much weight to give the paper**. Distinct from:

- `/doc-evidence` — synthesizes *many* papers to answer a clinical question; this appraises *one*.
- `/doc-find-paper` — resolves a fuzzy reference to a `paper_id`; this is the step *after* you have the id.
- `/doc-grounding-check` — audits an external *claim/draft* against the corpus; this audits a *paper's own quality*.
- `/doc-contradictions` — maps disagreement across the corpus; this appraises a single study (and points to `/doc-contradictions` when the finding turns out to be isolated/contested).

## When to invoke

Trigger phrases (exact or paraphrased):
- *"How good is this paper?"* / *"Appraise this study"* / *"Critique this paper"*
- *"Is this study reliable?"* / *"Can I trust this finding?"* / *"Should I believe this?"*
- *"What's the quality of the evidence?"* / *"Grade this evidence"* / *"GRADE this"*
- *"What's the risk of bias?"* / *"Is this biased?"*
- *"What study design is this?"* / *"Is this an RCT or just a case series?"*
- *"Is the sample size big enough?"* / *"Is this underpowered?"*
- *"Is this preclinical finding trustworthy?"* / *"This is just a mouse study, right?"*
- *"How much weight should I give this paper?"*

If the user names a paper by a fuzzy reference (title fragment, author, "that KRAS trial from
2021"), resolve it with **`/doc-find-paper`** FIRST, then appraise the returned `paper_id`. If
they hand you two papers and ask which is stronger, appraise each and compare scorecards.

## Inputs

| Input | Default | Notes |
|---|---|---|
| `paper_id` | — (required) | Canonical id, e.g. `pubmed:39281234`. Resolve fuzzy refs via `/doc-find-paper` first. |
| `lens` | `full` | `full` (whole scorecard) · `design` (just classify) · `bias` (risk-of-bias only) · `stats` (statistical sanity only) |
| `corroborate` | `true` | Run the MeSH corroboration pass (`concept_papers` on key terms). Set false to skip for speed. |
| `compare_to` | unset | A second `paper_id` — appraise both, render side-by-side. |

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

> **Appraisal nuance.** A critical appraisal is *mostly* `[INFERENCE]` — it is *your judgement
> about* a paper. That is allowed and expected. But every judgement must be anchored to a
> `[GROUNDED]` fact the paper actually reports (its design, its n, its endpoints, its CIs). You may
> not invent that the paper "used randomization" or "had 1,200 patients" — those are extracted from
> the chunks and grounded to the `paper_id`. Inference operates ON grounded facts, never instead of them.

## Flow

### Step 1 — State your interpretation (one line)

> *"Appraising `pubmed:39281234` — full scorecard: design → evidence level → risk of bias → stats sanity → corroboration. Read-only."*

If the reference was fuzzy, name the resolution: *"Resolved 'the sotorasib KRAS trial' → `pubmed:34161704`."*

### Step 2 — Read the whole paper

Run BOTH in one parallel batch:

| Call | Yields |
|---|---|
| `get_paper(paper_id)` | title, abstract, authors, **MeSH terms**, journal, year, DOI/PMID/URL |
| `get_paper_chunks(paper_id)` | every chunk in order, with **section labels** (methods/results/etc.) |

Extract and hold (all grounded to `paper_id`):
- The **design signals** (randomized? blinded? control arm? prospective? n? follow-up duration? endpoints?).
- The **numbers** (effect size, hazard/odds/risk ratio, confidence intervals, p-values, n per arm).
- The **funding / conflict** statement if present in the chunks.
- The **stated limitations** the authors themselves admit.

If a chunk does not state something (e.g. no funding disclosure retrieved), record it as `[GAP]` —
"funding not reported in retrieved chunks" — never assume.

### Step 3 — Classify STUDY DESIGN

From the methods/abstract chunks, place the paper in exactly one bucket (highest applicable):

| Design | Tell-tale signals in chunks |
|---|---|
| **Meta-analysis / systematic review** | pooled estimates, forest plot, PRISMA, "we searched databases" |
| **RCT** | "randomized", "double-blind", "placebo/active control", allocation |
| **Cohort (prospective / retrospective)** | followed a group over time, exposure→outcome, no randomization |
| **Case-control** | started from outcome, looked back at exposure, matched controls |
| **Case series / case report** | descriptive, n small, no comparator |
| **Preclinical in-vivo** | animal model (mouse/xenograft/PDX), no human subjects |
| **Preclinical in-vitro** | cell lines, organoids, biochemical assays only |
| **Narrative review / editorial / commentary** | no new data, opinion/synthesis |

State the call as `[INFERENCE]` justified by a `[GROUNDED]` signal: *"[INFERENCE] Phase III RCT —
the methods chunk reports 1:1 randomization to sotorasib vs docetaxel (`pubmed:...`)."*

### Step 4 — Assign EVIDENCE LEVEL (GRADE + Oxford CEBM)

Map design → starting level, then adjust for the risk-of-bias / stats findings below:

| Design | Starting GRADE | Oxford CEBM Level |
|---|---|---|
| Meta-analysis of RCTs / strong RCT | **High** | 1a / 1b |
| Single RCT (some limitations) | **Moderate** | 1b / 2b |
| Cohort / case-control | **Low** | 2b / 3b |
| Case series / mechanistic preclinical | **Very Low** | 4 / 5 |
| Editorial / opinion | **Very Low** | 5 |

Downgrade for: serious risk of bias, imprecision (wide CIs), indirectness (surrogate endpoint,
non-target population), inconsistency (contradicts corroboration pass), publication-bias signals.
Upgrade preclinical only for: large effect, dose-response, mechanism consistency across the corpus.
State the **final** level and **why it moved** from the start.

### Step 5 — RISK OF BIAS by domain

Score each domain **Low / Some-concerns / High / Unclear**, each anchored to a grounded fact (or
an explicit `[GAP]` where the paper is silent):

| Domain | What to look for |
|---|---|
| **Selection** | randomization adequate? allocation concealed? baseline balance? eligibility narrow/broad? |
| **Performance** | blinding of participants/personnel? co-interventions controlled? |
| **Detection** | outcome assessors blinded? objective vs subjective endpoint? |
| **Attrition** | dropout rate, ITT vs per-protocol, missing-data handling? |
| **Reporting** | pre-registered? all stated endpoints reported? selective outcome reporting? |

Then raise explicit **red-flag flags** when present:
`small-n` · `single-arm` · `no control` · `surrogate endpoint` (e.g. ORR/PFS proxying for OS) ·
`short follow-up` · `retrospective` · `industry funding` · `open-label` · `subgroup-driven` ·
`unregistered`. Each flag cites the chunk that triggered it.

### Step 6 — STATISTICAL sanity pass

From what the chunks actually report (do not compute statistics you cannot see):
- **Effect size** — is it clinically meaningful, or statistically-significant-but-tiny?
- **Confidence intervals** — present? do they cross 1 (null)? are they wide (imprecision)?
- **Multiplicity** — many endpoints / subgroups tested without correction? (p-hacking smell)
- **Power** — was a sample-size/power calculation reported? is n plausibly adequate for the effect?
- **p-value hygiene** — primary endpoint pre-specified? or a fished post-hoc subgroup?

Flag what you cannot assess: *"[GAP] No confidence intervals reported in the retrieved chunks."*

### Step 7 — CORROBORATION (is the finding isolated or replicated?)

Pull 1-3 of the paper's **key MeSH terms** from Step 2 and, for each, call:

`concept_papers(concept_name, limit=20)`

Then judge:
- **Replicated** — multiple other corpus papers report a concordant finding → confidence ↑.
- **Isolated** — no corroborating papers in the corpus → confidence ↓, flag it, and point to
  `/doc-contradictions` to check whether it is *contested* (others actively disagree) vs merely *novel*.
- **Contested** — other papers report the opposite → surface BOTH sides; route to `/doc-contradictions`.

Every corroborating/contradicting paper you cite is itself a `paper_id` inside the envelope and is gated.

### Step 8 — Gate, then render

Assemble every factual statement about the paper(s) as a claim list with `paper_id` citations and
run `check_grounding(claims, allowed_paper_ids)` over the union of envelopes from `get_paper`,
`get_paper_chunks`, and the `concept_papers` calls. Repair every violation; re-run until
`grounded=true`. Only then render the scorecard.

---

## Output templates

### `full` — quality scorecard

```
# Paper Appraisal — <short title> (<year>)
`<paper_id>` · <journal> · appraised <date>

## Evidence-level badge
🟩 HIGH  ·  🟨 MODERATE  ·  🟧 LOW  ·  🟥 VERY LOW      ← circle one
**Final: <LEVEL>** (Oxford CEBM <level>) — started <start level>, moved to <final> because <reason>.

## Quality scorecard
| Dimension | Rating | Basis (grounded) |
|---|---|---|
| Study design | <design> | [GROUNDED] methods report <signal> (`<paper_id>`) |
| Selection bias | Low / Some / High / Unclear | <why> |
| Performance bias | … | … |
| Detection bias | … | … |
| Attrition bias | … | … |
| Reporting bias | … | … |
| Effect size | <meaningful / marginal / unclear> | <value + CI if reported> |
| Precision (CIs) | <tight / wide / not reported> | … |
| Power / sample | <n>, <powered? / underpowered? / unstated> | … |
| Corroboration | Replicated / Isolated / Contested | <n corroborating corpus papers> |

## Red flags
- ⚠️ <flag> — [GROUNDED] <chunk basis> (`<paper_id>`)
- ⚠️ <flag> — …
(if none: "No major red flags surfaced in the retrieved chunks.")

## Gaps (not reported / not retrievable)
- [GAP] <e.g. no funding disclosure in retrieved chunks>
- [GAP] <e.g. confidence intervals not reported>

## Verdict — how much weight to give this
[INFERENCE] <one paragraph: synthesize design + bias + stats + corroboration into a single
"trust dial". Be concrete — e.g. "Strong enough to inform practice" vs "Hypothesis-generating
only; do not act on it alone" vs "Preclinical signal — interesting mechanism, zero clinical weight
yet". Name the single biggest reason for the rating.>

## Caveats
- Appraisal is judgement *about* the paper, anchored to grounded facts — not a re-analysis of raw data.
- Corroboration is bounded by the finite local corpus; absence here ≠ absence in the literature.

## Drill-down
- Finding looks isolated/contested? → /doc-contradictions
- Want the full question this paper sits inside? → /doc-evidence
- Comparing candidates? re-run with `compare_to=<other paper_id>`

*Research synthesis over a finite local corpus — not medical advice. Verify against primary sources and a treating clinician.*
```

### `compare_to` — two-paper face-off

```
# Appraisal face-off — <date>
| Dimension | `<paper_id A>` <title A> | `<paper_id B>` <title B> |
|---|---|---|
| Design | <A> | <B> |
| Evidence level | <A badge> | <B badge> |
| Risk of bias (worst domain) | <A> | <B> |
| Effect size / precision | <A> | <B> |
| Corroboration | <A> | <B> |
| **Stronger on this question** | ✅ / — | ✅ / — |

## Verdict
[INFERENCE] <which paper carries more weight and why — name the deciding dimension.>

*Research synthesis over a finite local corpus — not medical advice. Verify against primary sources and a treating clinician.*
```

### `design` / `bias` / `stats` — single-lens

Render only the matching scorecard section, still gated, still with the disclaimer line.

---

## Behavioural rules

- **Read-only.** Only `get_paper`, `get_paper_chunks`, `concept_papers`, `check_grounding`. Never ingest or write unless the user explicitly asks for fresh PubMed pull.
- **One paper is the unit.** Don't drift into answering the broader clinical question — that's `/doc-evidence`. Appraise what's in front of you.
- **Extract, don't assume.** Every design signal, n, CI, and funding line comes from a retrieved chunk. If a chunk doesn't say it, it's a `[GAP]`, not a default.
- **Inference is allowed but labelled.** The verdict is your judgement — mark it `[INFERENCE]`. The facts it rests on are `[GROUNDED]`. Never blur the two.
- **No fabricated statistics.** If the chunks don't report a CI or a power calc, say so — never manufacture one.
- **Preclinical ≠ clinical weight.** A beautiful mechanism in cell lines is `Very Low` for clinical decisions. Say it plainly; don't let elegance inflate the grade.
- **Surface isolation honestly.** If corroboration finds nothing, that lowers confidence — say so and route to `/doc-contradictions`; don't pretend a lone study is settled science.
- **Gate before render.** No scorecard ships before `check_grounding` returns `grounded=true`.
- **Corpus is finite.** If the paper isn't in the local corpus, say so and offer `/doc-find-paper` or `ingest_pubmed`.

## Composition with other skills

This skill **consumes** a `paper_id` (often from `/doc-find-paper`) and **hands off** to:
- `/doc-contradictions` when corroboration shows the finding is isolated or contested,
- `/doc-evidence` when the user wants the whole clinical question, not just this study,
- `/doc-gems` in reverse — `/doc-gems` surfaces candidate "gems", and routes *here* to verify whether a gem's quality is real or just obscure.

It does not invoke those skills automatically; the drill-down links are an offer.

## Examples

**User**: *"How good is this paper — `pubmed:34161704`?"*
→ `full` scorecard: classify design, badge the evidence level, score 5 bias domains, run the stats pass, corroborate via MeSH, render the verdict. Gated.

**User**: *"Is this preclinical KRAS finding actually trustworthy?"* (gives a paper_id)
→ classify as in-vivo/in-vitro, assign **Very Low** GRADE, emphasize the verdict caveat that mechanism ≠ clinical weight, corroborate to see if other corpus papers echo the mechanism.

**User**: *"Which is stronger, the 2019 cohort or the 2022 RCT?"* (two paper_ids)
→ `compare_to` face-off; appraise both, render side-by-side, name the deciding dimension.

**User**: *"Appraise that sotorasib trial."* (fuzzy)
→ resolve via `/doc-find-paper` → `pubmed:...`, then `full` appraisal.

**User**: *"Just tell me the risk of bias."* (gives a paper_id)
→ `lens=bias`: read chunks, score the 5 domains + red flags only, gated, disclaimer.
