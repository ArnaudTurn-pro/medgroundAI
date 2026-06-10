---
name: doc-contradictions
description: Surface where the medground oncology corpus DISAGREES with itself — conflicting findings on a question, both sides cited, controversy made visible rather than smoothed into false consensus. Trigger phrases — "conflicting evidence", "where does the literature disagree", "contradictions", "is this settled or contested", "both sides", "controversy", "mixed results", "is there debate about X", "do studies agree on X", "is the evidence consistent", "what's disputed", "settled science or not". Searches BOTH a claim AND its negation (e.g. "X improves survival" AND "X shows no benefit / X worsens outcomes"), buckets hits into Supports / Contradicts / Nuanced, grounds each position in specific paper_ids, then characterizes WHY the studies differ (population, dose, study design, endpoint, era, preclinical-vs-clinical). Both sides pass check_grounding. Renders a two-column controversy view + a "why they differ" analysis + a "what would resolve it" note. NEVER manufactures a disagreement that isn't there — if the corpus is genuinely consistent, it says so. Read-only over a finite local corpus. NOT medical advice. Drill into /doc-paper-appraisal (weigh conflicting papers by quality), /doc-evidence.
---

# doc-contradictions

The "where does the literature actually disagree?" skill. You give it a question, claim, or topic; it deliberately searches for **both sides** — the supporting evidence *and* the refuting/null evidence — buckets the hits, grounds each camp in specific papers, and explains *why* the studies diverge. The headline value is honesty: it refuses to collapse a real controversy into a tidy consensus, and refuses to invent a controversy where the corpus agrees.

Distinct from:

- `/doc-evidence` — synthesizes the *answer* to a question; contradictions instead foregrounds the *disagreement* inside that answer.
- `/doc-paper-appraisal` — judges ONE paper's quality; contradictions sets two camps of papers against each other (then hands off to appraisal to break the tie).
- `/doc-synthesis` — couples findings into new hypotheses; contradictions pulls findings apart to expose conflict.
- `/doc-gems` — finds novel/contrarian individual papers; contradictions maps the two-sided structure of a debate.

## When to invoke

Trigger phrases (exact or paraphrased):
- *"Is there conflicting evidence on <X>?"* / *"Where does the literature disagree about <X>?"*
- *"Is the benefit of <drug> in <disease> settled or contested?"*
- *"Give me both sides on <X>."* / *"What's the controversy around <X>?"*
- *"Mixed results for <intervention>?"* / *"Do the studies agree on <X>?"*
- *"Is the evidence on <biomarker> consistent?"* / *"What's disputed about <X>?"*

If the user just wants the consensus answer, route to `/doc-evidence`. If they want to weigh two specific papers head-to-head, do contradictions first then hand the two camps to `/doc-paper-appraisal`.

## Inputs

| Input | Default | Notes |
|---|---|---|
| `question` | required | A claim / question / topic. The skill auto-derives its negation. |
| `k_per_side` | 8 | Hits retrieved per side (claim and counter-claim). |
| `use_facets` | on | Run `summarize_evidence` to get the facet decomposition + `allowed_paper_ids` envelope before the directional searches. |
| `dimensions` | auto | The "why they differ" axes to test: population, dose, design, endpoint, era, preclinical-vs-clinical. |

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

For controversy mapping the contract has a special edge: **both camps must be equally grounded**. It is not acceptable to cite the side you find more convincing and hand-wave the other. Each position — Supports and Contradicts — carries its own `paper_id`s and passes `check_grounding`. The "why they differ" analysis is `[INFERENCE]` and must be labelled as such.

## Flow

State your interpretation in one line, including the negation you'll search:

> *"Controversy scan on 'does adjuvant chemo improve OS in stage II colon cancer'. I'll search the claim AND its negation (no benefit / harm), bucket both sides, and explain why they differ."*

### Step 1 — Establish the evidence envelope

```
summarize_evidence("<question>", k_per_facet=5)
```

Capture `allowed_paper_ids`. This gives the facet decomposition and a balanced first pull. (Skip only if `use_facets=off`.)

### Step 2 — Search BOTH directions explicitly

This is the core move. Derive the negation of the claim and search each direction separately so neither side is starved:

```
search_papers("<claim — the positive direction, e.g. 'X improves survival / X effective'>", k=8)
search_papers("<negation — 'X no benefit / X no difference / X worsens / X failed'>", k=8)
```

Add direction-specific phrasings where useful (e.g. "negative trial", "did not meet endpoint", "no significant difference", "inferior to"). The goal is to actively *recruit* the dissenting evidence, not wait for it to show up.

### Step 3 — Bucket the hits

Sort every retrieved hit into one of three buckets, by what the paper actually concludes:

- **Supports** — the paper's finding backs the claim.
- **Contradicts** — the paper's finding opposes the claim (null or reversed).
- **Nuanced** — conditional, subgroup-only, or "it depends" findings that belong to neither camp cleanly.

If a hit is ambiguous, open it (`get_paper` / `get_paper_chunks`) before bucketing — do not guess its direction.

### Step 4 — Ground each position

For each camp, write the position as a discrete claim with its `paper_id`(s):

- Position A (Supports): `[GROUNDED]` claim + citations.
- Position B (Contradicts): `[GROUNDED]` claim + citations.
- Nuanced findings: `[GROUNDED]` claims + citations.

### Step 5 — Characterize WHY they differ `[INFERENCE]`

For the conflicting camps, test the standard divergence axes and name the most likely driver(s):

| Axis | Question |
|---|---|
| **Population** | Different stage / line of therapy / biomarker status / age? |
| **Dose / schedule** | Different dosing, duration, or regimen? |
| **Design** | RCT vs observational? Powered vs underpowered? Single-arm vs controlled? |
| **Endpoint** | OS vs PFS vs ORR vs a surrogate? |
| **Era** | Older vs newer studies (standard-of-care shifted)? |
| **Translation** | Preclinical / in-vitro vs clinical? |

This analysis is `[INFERENCE]` — your reasoning over grounded facts. Flag it. Where you can ground a specific design/population fact about a paper, cite it.

### Step 6 — Gate both sides

```
check_grounding(claims, allowed_paper_ids)
```

Every camp's claims go into this call. Repair all violations and re-run until `grounded=true`. If one side has no real grounding, that is itself the answer — the "controversy" may be illusory (see honesty rule below).

## Output template

```
# Controversy — <question>  ·  <date>
Corpus: local store  ·  searched claim + negation  ·  envelope: <N> allowed papers

## Verdict
<CONTESTED — genuine disagreement> / <NUANCED — agreement with conditions> /
<SETTLED — corpus is consistent, no real conflict found>.  [INFERENCE]

## Position A — <the claim>  [GROUNDED]
- pubmed:30345884 — "<short title>" (2018) — <finding supporting the claim>.
- pubmed:39281234 — "<short title>" (2024) — <finding supporting the claim>.

## Position B — <the counter-claim>  [GROUNDED]
- pubmed:31122334 — "<short title>" (2019) — <null / reversed finding>.
- pubmed:... — "<title>" (2021) — <null / reversed finding>.

## Nuanced / conditional
- pubmed:... — "<title>" (2022) — <benefit only in subgroup X / depends on Y>.

## Why they differ  [INFERENCE]
- **Population**: A studied <X> patients; B studied <Y>. <one line>.
- **Endpoint**: A reported <OS>; B reported <PFS / surrogate>. <one line>.
- **Era / design**: <retrospective vs RCT, pre- vs post- standard-of-care shift>.
The most likely driver of the disagreement is <…>.

## What would resolve it
<The study/data that would settle this — e.g. "a head-to-head RCT in the <X> population powered for OS">.
[GAP] Not present in the local corpus → ingest_pubmed("<query>") to check the broader literature.

## Drill down
- Weigh the conflicting papers by quality (which camp is methodologically stronger?) → /doc-paper-appraisal
- Build the consensus answer with all caveats → /doc-evidence

---
*Research synthesis over a finite local corpus — not medical advice. Verify against primary sources and a treating clinician.*
```

### When the corpus is actually consistent

If both-directional searching turns up no genuine dissent, say so plainly — do NOT manufacture a controversy:

```
# Controversy — <question>  ·  <date>
## Verdict: SETTLED (within this corpus)  [INFERENCE]
I searched both the claim and its negation. The corpus is consistent: <N> papers support
<position>, none materially contradict it.
- pubmed:... — "<title>" (year) — <finding>.
Caveat: this is a finite local corpus. Absence of contradiction here ≠ settled science globally.
→ ingest_pubmed("<negation query>") if you want to stress-test against fresh papers.
```

## Behavioural rules

- **Read-only.** Never invoke write tools (other than an explicit user-requested `ingest_pubmed`).
- **Search the negation — always.** The defining behavior. Never rely on a single positive-direction query; explicitly construct and search the counter-claim. A controversy skill that only searches one direction is broken.
- **Both camps grounded equally.** Each side carries its own `paper_id`s and passes the gate. Never cite one side richly and the other thinly.
- **Never smooth a real disagreement.** If the corpus genuinely conflicts, present both columns with equal weight. Do not pick a winner by fiat — that's `/doc-paper-appraisal`'s job, offered as a drill-down.
- **Never manufacture a fake one.** If the corpus agrees, say SETTLED. Inventing controversy is as dishonest as hiding it.
- **"Why they differ" is inference.** The divergence analysis is `[INFERENCE]`, labelled. Ground individual design/population facts where you can.
- **Distinguish "no conflict" from "no evidence".** A `[GAP]` (nothing found either way) is not the same as SETTLED (consistent supporting evidence, no dissent). Say which.
- **Name the corpus boundary.** "Settled within this corpus" ≠ "settled science". Always offer `ingest_pubmed` to test against fresh literature.
- **Legible citations.** First mention: `paper_id` + short title + year. Thereafter the id.

## Examples

**User**: *"Is there conflicting evidence on adjuvant chemotherapy for stage II colon cancer?"*
→ `summarize_evidence` for the envelope; search "adjuvant chemo improves OS stage II colon" AND "adjuvant chemo no benefit / no survival difference stage II"; bucket; ground both camps; attribute the split to MSI status / risk stratification; render two-column controversy view.

**User**: *"Both sides on PD-L1 as a predictive biomarker for checkpoint inhibitors."*
→ Search "PD-L1 predicts response" AND "PD-L1 does not predict response / PD-L1-negative responders"; bucket Supports/Contradicts/Nuanced; characterize by assay/threshold/tumor-type heterogeneity; flag the nuance camp heavily.

**User**: *"Is the survival benefit of <drug> in <disease> settled or contested?"*
→ Directional search both ways; if consistent, return SETTLED with the supporting papers and the finite-corpus caveat; if not, full controversy view + handoff to `/doc-paper-appraisal` to weigh the camps.

**User**: *"Mixed results for antioxidants and cancer risk?"*
→ Search benefit AND harm/no-effect directions; expect a genuine split; bucket; attribute to study design (observational vs RCT) and era; "what would resolve it" = a powered RCT.
