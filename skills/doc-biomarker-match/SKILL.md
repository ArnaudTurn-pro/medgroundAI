---
name: doc-biomarker-match
description: Biomarker → therapy matcher over the medground corpus's curated CIViC evidence layer. Use when the user has a specific molecular alteration and wants the therapies it implies, each with an evidence level — "what's actionable for EGFR L858R", "therapies matched to BRAF V600E in melanoma", "is KRAS G12C targetable", "what does CIViC say about this variant", "leveled evidence for ERBB2 amplification", "resistance markers for osimertinib", "actionable mutations for this patient", "which drugs is this mutation sensitive/resistant to". Calls match_therapies(gene, disease, variant) and variant_evidence(variant) to pull CIViC's curated biomarker→drug associations, each carrying an A–E evidence level (A validated · B clinical · C case study · D preclinical · E inferential) and a clinical significance (sensitivity/response · resistance · adverse response). Renders a leveled match table (gene/variant → therapy → significance → level → civic:eid), separates sensitivity/predictive from resistance matches, scopes by disease (off-target diseases flagged), and flags the strength of each. Every match is grounded to a civic:eid id that passes check_grounding. Resolves fuzzy gene/drug/disease names via doc-find-concept first. Routes onward to doc-treatment-map (rank pathways), doc-evidence (deepen one match in the literature), doc-paper-appraisal. Decision-support — reports what the curated evidence SAYS, never dosing or a prescription. NOT medical advice.
---

# doc-biomarker-match

The **biomarker → therapy matcher**. You give it a molecular alteration (gene + variant, optionally a
disease); it returns the therapies that alteration *predicts a response or resistance to*, each
stamped with a **curated CIViC evidence level (A–E)** and grounded to a real `civic:eid` id. It is the
profile's fast, leveled answer to *"what's actionable for this mutation?"* — the curated counterpart
to `/doc-evidence`'s free-text literature synthesis.

It exists because biomarker→therapy questions have a *curated, leveled* source that raw retrieval
doesn't expose: CIViC. `match_therapies` / `variant_evidence` return human-curated associations with
an evidence level attached — a more rigorous strength signal than a hand-rolled "Strong/Moderate/Weak"
read of search hits. Distinct from:

- `/doc-find-concept` — resolves a term to a `mesh:` id; this maps a *variant* to *therapies*.
- `/doc-evidence` — free-text literature synthesis across facets; this is the curated biomarker layer (use both for a full picture).
- `/doc-treatment-map` — ranks whole treatment *pathways* for a patient; this answers the narrower *"what does this one variant imply?"* and feeds that ranker.
- `/doc-case` — builds the patient profile; it already calls these tools in Step 2b. This skill is the standalone, deep version for a single biomarker.

## When to invoke

Trigger phrases (exact or paraphrased):
- *"What's actionable for EGFR L858R?"* / *"What therapies match BRAF V600E?"*
- *"Is KRAS G12C targetable?"* / *"What is this variant sensitive / resistant to?"*
- *"What does CIViC say about ERBB2 amplification?"* / *"Leveled evidence for this mutation."*
- *"Resistance markers for osimertinib / this drug."*
- *"Actionable mutations for this patient."* (run per variant)

If the user wants the *whole patient's* pathways ranked → `/doc-treatment-map`. If they want a
free-text literature answer rather than the curated layer → `/doc-evidence`. If they only want a
concept id → `/doc-find-concept`.

## Inputs

| Input | Default | Notes |
|---|---|---|
| `gene` | — (required) | The gene, e.g. `EGFR`, `BRAF`, `KRAS`. Resolve brand/alias ambiguity via `/doc-find-concept` if unsure. |
| `variant` | unset | The specific alteration, e.g. `L858R`, `V600E`, `G12C`, `amplification`, `exon 19 deletion`. If unset, `variant_evidence`/`match_therapies` returns gene-level matches. |
| `disease` | unset (strongly preferred) | The cancer type to scope to, e.g. `lung`, `melanoma`. A match in a *different* disease is `off-target` and flagged — don't silently borrow it. |
| `significance` | all | Optionally filter to `sensitivity` / `resistance` / `adverse`. Default surfaces all and groups them. |

## The grounding contract (non-negotiable)

1. **Retrieve before you reason.** Never state a biomarker→therapy association from model memory.
   Pull it from CIViC first (`match_therapies` / `variant_evidence`), or the literature
   (`search_papers` / `summarize_evidence`).
2. **Write the answer as discrete claims.** One match per claim. Each claim cites the `civic:eid…`
   (or `pubmed:…`) id it came from — drawn ONLY from what you actually retrieved.
3. **Gate before you present.** Call `check_grounding(claims, allowed_paper_ids)` on the draft. The
   `civic:eid…` ids are in the corpus and pass the gate. Repair every `violation`
   (`uncited` → add the real id; `phantom_citation` → fix it; `off_envelope` → retrieve it or drop
   the claim) and re-run until `grounded=true`.
4. **No claim ships without an id the gate accepts.** If CIViC has no curated match, say so plainly —
   *"no CIViC match for `<variant>`"* — and offer the literature route (`/doc-evidence`) or
   `ingest_pubmed`. Do NOT backfill a "known" drug association from general knowledge.
5. **Label every line.** `[GROUNDED]` = a cited CIViC/corpus match · `[INFERENCE]` = your reading of
   the matches (e.g. "the A-level sensitivity match is the strongest lead") · `[GAP]` = no curated
   match. Inference must never wear the costume of a curated association.
6. **Cite legibly.** First mention of a CIViC item: `civic:eid…` + the gene/variant → therapy +
   level. Thereafter the id.

## CIViC evidence legend (state it in the output)

- **Level A — Validated** association (guideline / consensus / validated in a clinical setting).
- **Level B — Clinical** evidence (clinical trial or other primary patient data).
- **Level C — Case study** (individual case reports).
- **Level D — Preclinical** (in-vivo / in-vitro model evidence).
- **Level E — Inferential** (indirect association).

Clinical significance for a predictive match: **Sensitivity/Response** · **Resistance** ·
**Reduced Sensitivity** · **Adverse Response**. (CIViC also carries Prognostic / Diagnostic /
Predisposing / Oncogenic items — surface them if returned, but the therapy matches are the headline.)

## Flow

### Step 1 — State interpretation + resolve the biomarker

> *"Matching therapies for **`<gene> <variant>`** in **`<disease>`** against the curated CIViC layer,
> then grounding each match. Read-only."*

If the gene/drug/disease is fuzzy or aliased (brand name, gene-vs-protein), resolve it first via
`/doc-find-concept` so the query terms are canonical.

### Step 2 — Query the curated CIViC layer (parallel)

```
match_therapies(gene="<gene>", disease="<disease>", variant="<variant>")   # biomarker → therapy matches, A–E leveled
variant_evidence(variant="<gene variant>")                                  # all CIViC evidence for the molecular profile
```

`match_therapies` returns the therapy matches with their significance + level + `civic:eid`.
`variant_evidence` widens to every evidence item on the variant (including prognostic/diagnostic).
Capture every `civic:eid` — it is the citation envelope for the gate.

If both return nothing for the variant, retry at the gene level (drop `variant`), and note that the
*specific* alteration has no curated match even if the gene does.

### Step 3 — Organize the matches

Bucket by clinical significance, and within each by descending level (A → E):
- **Sensitivity / Response** — therapies the alteration predicts benefit from.
- **Resistance / Reduced sensitivity** — therapies it predicts *won't* work (just as important — a
  resistance match is a reason to *avoid* a drug, never conflate it with a sensitivity match).
- **Adverse response** — pharmacogenomic toxicity signals.
- **Other** (prognostic / diagnostic / predisposing) — surface briefly if returned.

**Disease scoping is the honesty axis.** A match curated in a *different* cancer type than the
patient's is `off-target` — surface it, but flag it explicitly (the same discipline as
`/doc-treatment-map`'s population-match column). Never silently apply a melanoma BRAF match to a
lung-cancer patient without saying so.

### Step 4 — Gate, then render

Assemble each match as a claim citing its `civic:eid` (plus any `pubmed:` you pulled), run
`check_grounding(claims, allowed_paper_ids)`, repair every violation, re-run until `grounded=true`.
Then render the table below. Report `grounded_ratio`.

---

## Output template

```
# Biomarker → therapy matches — <gene> <variant> · <disease | pan-cancer> · <date>
Source: curated CIViC layer (match_therapies / variant_evidence) · grounding gate: PASSED · grounded_ratio <0.00–1.00>

## TL;DR
[INFERENCE] The strongest lead is <therapy> (<significance>, Level <A–E>); <one line on the resistance/caveat>.

## Sensitivity / response  [GROUNDED]
| Gene · variant | Therapy | Significance | Level | Disease match | civic:eid |
|---|---|---|---|---|---|
| EGFR L858R | osimertinib | Sensitivity/Response | B | exact (lung) | civic:eid… |
| … | … | … | … | off-target (<other disease>) | civic:eid… |

## Resistance / reduced sensitivity  [GROUNDED]
| Gene · variant | Therapy | Significance | Level | Disease match | civic:eid |
|---|---|---|---|---|---|
| EGFR T790M | gefitinib | Resistance | B | exact (lung) | civic:eid… |

## Adverse response / other  (if any)
- [GROUNDED] <pharmacogenomic / prognostic item> (Level <X>, civic:eid…).

## Strength read  [INFERENCE]
<Which matches are practice-relevant (A/B, exact disease) vs hypothesis-grade (C/D/E or off-target).
Name the single strongest sensitivity match and the single most important resistance flag.>

## Gaps
- [GAP] No CIViC match for <variant / significance> — the curated layer is silent here.
  → free-text literature instead: /doc-evidence · or ingest fresh PubMed: /doc-watch.

## Drill-down
- Rank this into the patient's whole pathway → /doc-treatment-map
- Deepen one match in the primary literature → /doc-evidence
- Weigh the trial behind an A/B match → /doc-paper-appraisal (resolve it with /doc-find-paper first)

---
*Decision-support over a curated evidence layer — reports what the evidence SAYS, never dosing or a
prescription. Research synthesis, not medical advice. Verify against primary sources and a treating clinician.*
```

## Behavioural rules

- **Curated, not exhaustive.** CIViC is a curated database — a *no match* means "not curated here",
  NOT "no evidence anywhere". Always offer the literature route (`/doc-evidence`) when CIViC is thin.
- **Level is the strength signal.** Report the A–E level on every match and lead with the highest. An
  E-level inferential match is a hypothesis, not an actionable result — say so.
- **Sensitivity ≠ resistance — never conflate them.** A resistance match means *avoid that drug for
  this variant*. Bucket the two separately and never let a resistance item read as an endorsement.
- **Disease scoping is mandatory honesty.** A match curated in a different cancer type is
  `off-target` — surface it flagged, never silently borrowed.
- **Resolve fuzzy names first.** Brand→generic, gene↔protein, abbreviation→expansion via
  `/doc-find-concept` before querying, so the curated lookup hits.
- **Gate before render.** Every match is a claim citing its `civic:eid`; nothing ships before
  `check_grounding` returns `grounded=true`. Report the `grounded_ratio`.
- **Read-only.** `match_therapies`, `variant_evidence`, `search_papers`, `check_grounding`,
  `/doc-find-concept` only. The sole write escape hatch is *offering* `/doc-watch` when the curated +
  literature layers are both thin.
- **Decision-support, never a prescription.** Report what the curated evidence SAYS about a variant.
  Never output dosing, a schedule, or "give drug X". Recommending the *whole* sequence is
  `/doc-treatment-map`'s job (still strategy, not dosing); the final call is the treating team's.
- **Surface MCP errors verbatim.** Don't swallow a tool error.

## Composition with other skills

- Upstream: `/doc-find-concept` (resolve a fuzzy gene/drug/disease) · `/doc-case` (which already runs
  these tools in Step 2b and can hand you the variants).
- Downstream: `/doc-treatment-map` (rank the patient's whole pathway, folding these leveled matches
  into the evidence-strength column) · `/doc-evidence` (free-text literature depth on one match) ·
  `/doc-paper-appraisal` (grade the trial behind an A/B match).

The drill-down links are an offer, not a chained call.

## Examples

**User**: *"What's actionable for EGFR L858R in lung adenocarcinoma?"*
→ Resolve EGFR; `match_therapies(gene="EGFR", disease="lung", variant="L858R")` +
`variant_evidence("EGFR L858R")`; bucket sensitivity (EGFR-TKIs, A/B level) vs resistance markers;
gate against the `civic:eid` ids; render the leveled table; offer `/doc-treatment-map` to rank the
whole pathway. No dosing.

**User**: *"Is BRAF V600E targetable, and does the disease matter?"*
→ `match_therapies(gene="BRAF", variant="V600E")` across diseases; surface the melanoma A/B matches as
`exact` for a melanoma patient but **off-target** for a lung-cancer patient, with the lung-specific
matches flagged separately; strength read names the disease dependence explicitly.

**User**: *"What is osimertinib resistance associated with?"*
→ Lead with the **Resistance** bucket: variants whose CIViC significance is resistance/reduced
sensitivity to osimertinib (e.g. acquired alterations), each leveled + `civic:eid` grounded; note
this is a *reason to avoid/expect failure*, not a therapy recommendation.

**User**: *"Any actionable mutations here? KRAS G12C, TP53 R175H."*
→ Run the matcher per variant; KRAS G12C → sotorasib/adagrasib sensitivity matches (leveled); TP53
R175H → likely prognostic/oncogenic rather than a therapy match → say so and route to `/doc-evidence`
for the literature. Gate all; disclaimer.

---

*Decision-support over a curated evidence layer — not medical advice. Verify against primary sources
and a treating clinician.*
