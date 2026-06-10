---
name: doc-treatment-map
description: Evidence-weighted treatment-pathway mapper & ranker for the medground oncology-literature desk. Use when the user wants to work out the best-supported treatment PROCESS / SEQUENCE for a patient and wants the options ranked by outcome — "map the treatment options", "what's the best pathway", "sort out the treatment process", "which sequence gives the best outcome", "rank the treatment strategies", "immunotherapy-first or surgery-first?", "what should the plan be", "compare the treatment options for this patient", "best chance of recovery". Consumes a Patient Context Block (from /doc-case — builds one first if absent). Enumerates the candidate treatment pathways actually in play, identifies the DECISION-GATING UNKNOWNS and renders them as a decision tree, anchors biomarker-matched arms on curated CIViC evidence (match_therapies / variant_evidence, leveled A–E), builds a per-arm comparative evidence map of grounded outcomes (response / survival where reported) + evidence strength + risks, then produces a RANKED pathway recommendation — the ranking is [INFERENCE] over grounded outcome facts, every outcome claim is [GROUNDED] and passes check_grounding. Recommends a STRATEGY/SEQUENCE, never dosing/schedules; flags recommendations conditional on unconfirmed facts as provisional; defers the final decision to the treating team / multidisciplinary tumor board (RCP). Decision-SUPPORT to inform a clinical discussion — NOT a prescription, NOT a treatment order, NOT a substitute for clinical judgment. NOT medical advice.
---

# doc-treatment-map

The **treatment-pathway mapper & ranker**. You give it a patient (or a Patient Context Block from
`/doc-case`); it works out which treatment *strategies / sequences* are actually in play, maps the
**decision-gating unknowns** as a tree, builds a **comparative evidence map** of the grounded
outcomes for each live pathway, and ends with a **ranked recommendation** of which pathway the
literature most strongly supports — and *why*.

It exists because the other skills answer *one* question well (`/doc-evidence`), appraise *one*
paper (`/doc-paper-appraisal`), or map a *topic* (`/doc-landscape`) — but none of them put the
*candidate pathways side by side and rank them by outcome*. This one does, scoped to a specific
patient.

## The line this skill walks (read this first)

This is the one `doc-*` skill that **ranks and recommends** a pathway. That is deliberate — but it
stays decision-*support*, not prescription, and it holds these lines without exception:

- ✅ **Recommends a STRATEGY / SEQUENCE** — e.g. "immunotherapy-first → surgical consolidation,
  ranked #1." A *sequence of modalities* is a thinking aid for the tumor board.
- 🚫 **Never emits dosing, schedules, drug-administration instructions, or a written order.** A
  recommended sequence is decision-support; "pembrolizumab 200 mg IV q3w ×N" is a prescription —
  out of scope, always. If the user asks for dosing, redirect: *"the sequence is what I can rank;
  the regimen is the treating team's to write."*
- ⚠️ **The ranking is `[INFERENCE]`** — reasoning over grounded outcome facts. Every *outcome*
  claim under it is `[GROUNDED]` and passes `check_grounding`. The recommendation never wears the
  costume of a cited fact.
- ⚠️ **The final decision belongs to the treating team / multidisciplinary tumor board (RCP).** The
  output is framed to *inform that discussion*, never to override it.
- ⚠️ **A recommendation built on unconfirmed facts is PROVISIONAL** and labelled so. If the case
  has pending decision-gating facts (MSI status, stage, resectability), the ranking is conditional
  and the decision tree is the real deliverable until those resolve.

If you cannot keep these lines for a given request, say so and fall back to `/doc-evidence`
(facts only, no ranking).

## When to invoke

Trigger phrases (exact or paraphrased):
- *"Sort out the treatment process."* / *"Map the treatment options."* / *"What's the best pathway?"*
- *"Which sequence gives the best outcome?"* / *"Rank the treatment strategies."*
- *"Immunotherapy-first or surgery-first?"* / *"Neoadjuvant or adjuvant?"* / *"What should the plan be?"*
- *"Compare the treatment options for this patient."* / *"Best chance of recovery."*

Do **not** invoke for: a single factual question (→ `/doc-evidence`), appraising one study
(→ `/doc-paper-appraisal`), or building the patient profile itself (→ `/doc-case`).

If there is **no Patient Context Block** in the conversation and the request is patient-specific,
run `/doc-case` FIRST to build one, then map. A pathway ranking with no patient scope is meaningless.

## Inputs

| Input | Default | Notes |
|---|---|---|
| `patient_context` | — (strongly preferred) | A Patient Context Block from `/doc-case`. If absent, build one first. The block's profile + resolved concepts + gating unknowns drive everything. |
| `pathways` | inferred | Candidate pathways to compare. If unset, enumerate them from the case (the plan(s) under discussion + the obvious standard-of-care alternatives the evidence surfaces). |
| `objective` | `overall recovery` | What "best outcome" means here — default is long-term recovery/survival; can be set to QoL-weighted, organ-preservation, etc. if the patient goals say so. Name it explicitly in the header. |

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

> **Ranking nuance.** A pathway ranking is *mostly* `[INFERENCE]` — it is *your judgement* weighing
> grounded outcomes. That is allowed and is the point of the skill. But each outcome the judgement
> rests on (a response rate, a survival figure, a toxicity signal) is `[GROUNDED]` to a real
> `paper_id`. You may not invent an outcome to justify a ranking. Inference operates ON grounded
> facts, never instead of them.

## Flow

### Step 1 — State interpretation + lock the scope (one line)

> *"Mapping treatment pathways for <case handle>, objective = <overall recovery>. Candidate
> pathways: A <…>, B <…>, C <…>. Decision-gating unknowns: <MSI status, stage>. Read-only."*

If you built the block via `/doc-case` this turn, say so. Repeat the patient scope so the user sees
it's honored. If `objective` differs from default, name it.

### Step 2 — Enumerate candidate pathways + gating unknowns

From the Patient Context Block:
- **Pathways** = the plan(s) under discussion in the case + the standard-of-care alternatives the
  evidence will surface. Phrase each as a *sequence of modalities*, e.g.
  "neoadjuvant ICI → surgery → surveillance" / "upfront surgery → adjuvant chemo" /
  "first-line ICI (metastatic) ± chemo". Keep them mutually distinguishable.
- **Decision-gating unknowns** = the pending facts that *flip which pathway applies*
  (e.g. MSI/MMR status, stage III vs IV, resectability of metastases, germline status). These are
  the branch points of the decision tree in Step 4.

### Step 3 — Retrieve grounded outcome evidence per pathway (parallel)

For each candidate pathway, in ONE parallel batch where possible:
- **CIViC biomarker matches FIRST** (whenever the patient has an actionable variant):
  `match_therapies(gene, disease, variant)` / `variant_evidence(variant)` — curated biomarker→therapy
  associations, each with an **A–E evidence level** and a clinical significance (sensitivity/response
  · resistance). The `civic:eid…` ids pass `check_grounding`. These anchor *which* targeted pathways
  are even live for this patient, and the A–E level is a first-class strength input to Step 5. If the
  Patient Context Block already carries the CIViC matches (from `/doc-case` Step 2b), **reuse them —
  don't re-query.**
- `summarize_evidence(question="<pathway> outcomes in <patient profile>", k_per_facet=5)` — capture
  each pathway's hits + `allowed_paper_ids`. Union all envelopes into the master `allowed_paper_ids`.
- Optionally `evaluate_plan(plan_text="<the pathway as a plain-language plan>")` to get per-claim
  supported / contested / unsupported / unknown verdicts on that pathway — a fast strength signal.
- `search_papers` with outcome/comparator phrasings ("vs chemotherapy", "survival", "recurrence",
  "head-to-head") to fill comparator gaps.

Pull, per pathway: **efficacy outcomes** (response, pCR, DFS, OS where reported), **comparative**
data vs the alternative, **risks/toxicity**, and **the population caveat** (does the evidence's
population match THIS patient — stage, MSI, line of therapy?).

**Survival horizon is mandatory.** For each pathway, explicitly retrieve **time-anchored survival**
— 2-year and 5-year DFS/OS/RFS where the corpus reports it (also capture any 3-year landmark and
median PFS/OS). Run targeted `search_papers` for "<pathway> 2-year / 5-year survival / disease-free
survival / overall survival rate". Response rate and pathological response are **surrogates** — a
pathway map that ranks on surrogates alone is incomplete. Where the corpus gives no time-anchored
figure, that is a `[GAP]` to state outright (and a `/doc-watch` ingestion offer), **never** smoothed
over by leaning on the surrogate. Distinguish **relative** survival benefit (HR, % recurrence
reduction) from **absolute** time-point rates — report both when available, and don't let a relative
benefit masquerade as an absolute survival figure.

### Step 4 — Build the DECISION TREE on the gating unknowns

Render the gating unknowns as a branch structure. Each leaf names the pathway that becomes live
under that combination of facts, with the grounded evidence attached. This is the part that is
robust even when the case is unresolved — it says *"here is what to do once you know X."*

```
<Unknown 1: MSI/MMR status?>
 ├─ dMMR / MSI-H ──> <Unknown 2: stage?>
 │     ├─ non-metastatic (III) ─> Pathway A: neoadjuvant ICI → surgery   [grounded evidence]
 │     └─ metastatic (IV)      ─> Pathway B: first-line ICI ± chemo       [grounded evidence]
 └─ pMMR / MSS ─────────────────> Pathway C: chemo-based ± surgery        [grounded evidence]
```

Mark which leaf the patient is *currently* on (or "undetermined — pending <X>").

### Step 5 — Build the COMPARATIVE EVIDENCE MAP (live branches)

For the pathways that are currently live (or the 2–3 most plausible given the case), a side-by-side:

| Pathway | Grounded outcome(s) | Evidence strength | Population match | Key risks | Corroboration |
|---|---|---|---|---|---|
| A … | [GROUNDED] … (`paper_id`) | Strong/Mod/Weak/Contested | exact / partial / off-target | … | replicated / isolated |

"Evidence strength" uses the same heuristic as `/doc-triage` (multiple recent convergent papers =
Strong; single/old/preclinical = Weak; conflicting = Contested → route detail to
`/doc-contradictions`). **Where a pathway turns on a CIViC biomarker→therapy match, fold its
curated A–E level into the rating** (Level A/B ≈ Strong, C ≈ Moderate, D/E ≈ Weak/preclinical) — a
leveled, curated signal beats the hand-rolled heuristic. "Population match" is the honesty column — a 100% response rate in
*early-stage* disease is **off-target** if this patient is metastatic.

Then render a **Survival-horizon table** (its own block — this is mandatory, not optional):

| Pathway | 2-year | 3-year | 5-year | Notes (median PFS/OS, relative benefit) |
|---|---|---|---|---|
| A … | <rate / `[GAP]`> | … | <rate / `[GAP]`> | [GROUNDED] … (`paper_id`) |

Fill each cell with a grounded time-point figure (`paper_id`) or an explicit `[GAP]` — corpus
silent. A column of `[GAP]`s is a finding, not a failure: it says the durable-survival case for
that pathway isn't yet in the corpus, which directly bounds how much weight the ranking can carry.
Offer `/doc-watch` to ingest the missing long-term survival datasets.

### Step 6 — RANK the pathways (the recommendation)

Order the live pathways by evidence-weighted expected outcome for THIS patient and THIS objective.
The ranking is `[INFERENCE]`. For each rank, give the one-line *why*, naming the grounded outcomes
and the single biggest caveat. If the top pathway depends on an unconfirmed fact, mark the whole
ranking **PROVISIONAL — conditional on <X>**.

State explicitly when two pathways are **evidence-tied** (don't manufacture a separation the corpus
doesn't support), and when the deciding factor is a *non-literature* fact (staging, resectability,
patient preference, performance status) rather than the evidence.

### Step 7 — Gate, then render

Assemble every *outcome* statement as a claim with `paper_id` citations from the master
`allowed_paper_ids`, run `check_grounding`, repair every violation, re-run until `grounded=true`.
The ranking sentences themselves are `[INFERENCE]` and are not gated, but each must point at
gated outcome claims. Render the template below. Report `grounded_ratio`.

---

## Output template

```
# Treatment-pathway map — <case handle> · <date>
Scope: <patient profile one-liner> · Objective: <overall recovery / QoL / …> · local corpus

## TL;DR
Best-supported pathway (PROVISIONAL, pending <X>): **<Pathway A>**, because <one line>.
The decision currently hinges on: <gating unknowns>.

## Decision tree (what flips the choice)
<the branch structure from Step 4, gating unknowns → live pathway + evidence per leaf>
Patient is currently on: <leaf / undetermined pending X>.

## Comparative evidence map (live branches)
| Pathway | Grounded outcome(s) | Strength | Population match | Key risks | Corroboration |
|---|---|---|---|---|---|
| A … | … (`paper_id`) | … | … | … | … |
| B … | … (`paper_id`) | … | … | … | … |

## Survival horizon (2-yr / 5-yr — mandatory)
| Pathway | 2-year | 3-year | 5-year | Notes (median PFS/OS, relative benefit) |
|---|---|---|---|---|
| A … | <rate / [GAP]> | … | <rate / [GAP]> | [GROUNDED] … (`paper_id`) |
| B … | … | … | … | … |
(A column of [GAP]s is itself the finding — durable-survival evidence absent from the corpus;
distinguish relative benefit (HR / % reduction) from absolute time-point rates; offer /doc-watch.)

## Ranked recommendation  [INFERENCE over grounded outcomes]
1. **<Pathway A>** — <why: grounded outcomes + biggest caveat>.  [PROVISIONAL if conditional]
2. **<Pathway B>** — <why; when it becomes first choice>.
3. **<Pathway C>** — <why lower / when it applies>.
(Note evidence-ties and non-literature deciders explicitly.)

## Grounded outcome claims (gate-passed, <ratio>)
- [GROUNDED] <outcome> (`paper_id`, short title, year).
- [GROUNDED] <comparative outcome> (`paper_id`).
- [INFERENCE] <the weighing that produced the ranking — over the facts above>.
- [GAP] <outcome the corpus can't supply, e.g. this patient's stage> — not a literature question / not in the local corpus.

## What would change the ranking
- Confirm <gating unknown> → moves patient to <leaf>, making <pathway> first choice.
- <non-literature decider: resectability, PS, patient goals> sits with the treating team.

## Drill-down
- Appraise the load-bearing trial → /doc-paper-appraisal
- A pathway looks contested → /doc-contradictions
- Deepen one pathway's evidence → /doc-evidence
- A combination worth exploring → /doc-synthesis

## Boundaries
- This is a ranked **strategy** map to inform the tumor-board (RCP) discussion — NOT a prescription,
  NOT dosing, NOT a treatment order. The final decision is the treating team's.
- Rankings are [INFERENCE] over grounded outcomes; outcome claims are gated; corpus is finite.

*Research synthesis over a finite local corpus — not medical advice. Verify against primary sources and a treating clinician.*
```

---

## Behavioural rules

- **Patient scope is mandatory.** No Patient Context Block → build one with `/doc-case` first. Never
  rank pathways in the abstract.
- **Lead with CIViC for biomarker-matched arms.** When a pathway turns on an actionable variant,
  anchor it on `match_therapies` / `variant_evidence` (A–E leveled, `civic:eid` pre-grounded) before
  raw literature, and map the CIViC level into Evidence strength. Reuse the block's CIViC matches if
  `/doc-case` already pulled them.
- **Strategy, never prescription.** Recommend sequences of modalities; never dosing, schedule, or a
  written order. Redirect dosing requests to the treating team.
- **Ranking is inference, outcomes are grounded.** Every outcome under a rank is `[GROUNDED]` +
  gated; the ranking sentence is `[INFERENCE]`. Never blur them.
- **Population match is non-negotiable honesty.** A huge effect in the wrong population (e.g.
  early-stage data applied to a metastatic patient) is flagged `off-target`, not quietly borrowed.
- **Rank on survival, not just surrogates.** Always retrieve and render the 2-yr/5-yr survival
  horizon. Response rate / pathological response are surrogates; if a pathway leads on surrogates
  but its long-term survival is `[GAP]`, say so plainly — that asymmetry bounds the ranking and
  must be visible, not buried. Never present a relative benefit (HR, % reduction) as an absolute
  survival rate.
- **Provisional when unconfirmed.** If the top pathway depends on a pending fact, label the ranking
  PROVISIONAL and make the decision tree the headline deliverable.
- **Don't manufacture a winner.** Evidence-tied pathways are reported as tied. When the real
  decider is a non-literature fact, say so plainly.
- **Surface disagreement.** A Contested pathway routes to `/doc-contradictions` — don't smooth it.
- **Defer the decision.** Always frame the output as input to the tumor board (RCP), and close with
  the disclaimer.
- **Gate before render.** No outcome claim ships before `check_grounding` returns `grounded=true`.
- **Read-only.** Retrieval + gate only. The sole write escape hatch is *offering* `/doc-watch` when
  the corpus is too thin to rank honestly.
- **Corpus boundary.** Outcomes the corpus can't supply are `[GAP]`; non-literature facts
  (this patient's stage, resectability) are flagged as not-a-literature-question, not as gaps to
  ingest away.

## Composition with other skills

`doc-treatment-map` sits *downstream* of `/doc-case` and *upstream* of the deep-dives:
- `/doc-case` → **`/doc-treatment-map`** → `/doc-paper-appraisal` (weigh the load-bearing trial per
  pathway) → `/doc-contradictions` (stress-test a contested arm).
- `/doc-treatment-map` → `/doc-evidence` to deepen any single pathway's evidence.
- `/doc-treatment-map` → `/doc-synthesis` when a *combination* across pathways is worth a hypothesis.
- For genuinely multi-lens cases, hand the whole thing to `orchestrator-doc`.

It does not invoke those automatically; the drill-down links are an offer.

## Examples

**User** (after `/doc-case` on a dMMR-suspected right-colon patient): *"Sort out the treatment
process for the best outcome."*
→ Enumerate pathways (neoadjuvant ICI → surgery / upfront surgery → adjuvant / first-line
metastatic ICI). Decision tree on MSI status + stage. Comparative evidence map of grounded
outcomes per arm with population-match honesty. Ranked recommendation — PROVISIONAL pending MSI +
staging — naming the immunotherapy-first arm #1 *if* dMMR + non-metastatic, with the surrogate-
endpoint caveat. Gate the outcome claims. Defer to the RCP. Disclaimer.

**User**: *"Immunotherapy-first or surgery-first?"* (patient block present)
→ Two-pathway face-off: grounded outcomes for each, evidence strength, the gating unknown that
decides between them, ranked with the conditional flagged. No dosing.

**User**: *"Just give me the regimen and doses."*
→ Decline the dosing; offer the ranked *sequence* + the grounded evidence, and state the regimen is
the treating team's to write.

**User**: *"Rank the options for my patient."* (no block yet)
→ Run `/doc-case` first to build the block, then map.
