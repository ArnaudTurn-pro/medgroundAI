---
name: doc-case
description: Patient-case intake & retrieval-steering skill for the medground oncology-literature desk. Use when the user hands over a clinical case and wants the literature search shaped around it — "here's a patient case", "given this patient", "patient with X", "case: …", "tailor the search to this patient", "build a patient profile", "what should I look up for this patient", "65yo with EGFR-mutant lung adeno, prior osimertinib…", "BRCA1 ovarian, platinum-resistant", "HER2+ breast, progressed on trastuzumab". Parses the free-text case into a structured clinical profile (cancer type / histology / stage / grade / key biomarkers & mutations / prior lines of therapy & response / comorbidities / performance status / age & sex / patient goals), resolves each diagnosis and biomarker to canonical `mesh:` concepts via find_concepts (noting any absent from the corpus graph), pulls curated CIViC biomarker→therapy evidence (match_therapies / variant_evidence, leveled A–E) for each actionable variant, and emits a reusable **Patient Context Block** — a compact, copy-pasteable structured block (Profile + resolved concept ids + a set of STEERED query seeds) that scopes and steers every downstream doc query so retrieval goes in the right direction and stays in context. It then hands off to /doc-evidence, /doc-synthesis, /doc-gems, /doc-landscape, /doc-triage — each scoped to the block. NOT advice and does NOT prescribe — it structures intake and steers grounded evidence retrieval; it never outputs dosing or a treatment recommendation, only what to READ and what the evidence SAYS, grounded. NOT medical advice.
---

# doc-case

The **patient-case intake & retrieval-steering** skill. You hand it a clinical case; it hands you back a structured **Patient Context Block** that every other `doc-*` skill can use to steer retrieval in the right direction and keep the patient in context across a whole session.

It exists because a raw clinical vignette is a poor query. "65yo with EGFR-mutant lung adeno, prior osimertinib, now progressing" needs to become resolved MeSH concepts + targeted query seeds before the corpus can answer well. This skill does that normalization once, up front, so the downstream evidence skills don't each re-guess the scope.

**This is intake and steering — not advice.** It captures and structures the case, resolves it to concepts, and points retrieval in the right direction. It does **not** prescribe, does **not** output dosing, and does **not** state a treatment plan. Its outputs are *what to READ* and *what the evidence SAYS* (grounded), never *what to DO*.

Distinct from:
- `/doc-find-concept` — resolves ONE term to a `mesh:` id; doesn't build a patient profile.
- `/doc-evidence` — answers ONE grounded question; doesn't capture or steer a case (but is the natural handoff).
- `/doc-triage` — maps the literature terrain; consumes the Patient Context Block this skill emits.

## When to invoke

Trigger phrases (exact or paraphrased):
- *"Here's a patient case…"* / *"Given this patient…"* / *"Case: …"*
- *"Patient with EGFR-mutant lung adeno, prior osimertinib…"* / a clinical vignette pasted in.
- *"Tailor the search to this patient."* / *"Build a patient profile."* / *"What should I look up for this patient?"*
- Any message that reads as a clinical vignette (age + cancer type + biomarkers + prior therapy).

If the case is too thin to steer retrieval (e.g. just "lung cancer patient"), ask 2–3 targeted clarifying questions (histology? biomarkers? prior therapy? stage?) before emitting a block — a vague block steers nothing.

## Inputs

| Input | Default | Notes |
|---|---|---|
| `case_text` | — (required) | Free-text clinical vignette. Anything from a one-liner to a full history. |
| `question` | unset | If the user attaches a specific clinical question, build the grounded answer (Step 5) scoped to the block. |

No assumptions are invented. If a field isn't in the case, mark it `not stated` — never fabricate a stage, a biomarker, or a prior line of therapy.

## Flow

### Step 1 — Parse the case into a structured profile

Extract, verbatim where possible, into these fields. Mark anything absent as `not stated` (do NOT infer):

| Field | Example |
|---|---|
| **Cancer type** | Non-small-cell lung cancer (NSCLC) |
| **Histology** | Adenocarcinoma |
| **Stage** | IV (metastatic) |
| **Grade** | not stated |
| **Key biomarkers & mutations** | EGFR L858R; T790M (acquired); PD-L1 30% |
| **Prior lines of therapy & response** | 1L osimertinib → PR, then progression at 14 mo |
| **Relevant comorbidities** | CKD stage 3 |
| **Performance status** | ECOG 1 |
| **Age & sex** | 65, female |
| **Patient goals (if stated)** | prioritize quality of life |

### Step 2 — Resolve diagnosis + biomarkers to canonical concepts

For each diagnosis and each biomarker/mutation, call `find_concepts(<term>)` to map it to a canonical `mesh:` id. Run these in one parallel batch.

- Record the resolved id (e.g. `EGFR` → `mesh:Receptor,_Epidermal_Growth_Factor`; `BRCA1` → `mesh:BRCA1_Protein`).
- If a term has no match, mark it **`not in corpus graph`** explicitly — this tells downstream skills that retrieval for it will lean on lexical/vector channels, not the MeSH graph, and may be thin.
- Resolve drugs mentioned in prior therapy too (e.g. `osimertinib`), since resistance/sequencing queries hang off them.

### Step 2b — Pull curated CIViC biomarker evidence (for each actionable variant)

For every gene+variant the case names (e.g. `EGFR L858R`, `BRAF V600E`, a `BRCA1` mutation), query the
curated CIViC layer BEFORE any literature trawl:

```
match_therapies(gene="<gene>", disease="<cancer type>", variant="<variant>")   # biomarker → therapy matches
variant_evidence(variant="<gene variant>")                                      # all evidence for the molecular profile
```

These return CIViC's curated biomarker→therapy associations, each carrying an **evidence level A–E**
(A = validated · B = clinical · C = case study · D = preclinical · E = inferential) and a clinical
significance (sensitivity/response · resistance · adverse response). Each match's `civic:eid…` id is
already in the corpus and passes `check_grounding`. Fold the leveled matches into the block (Step 3) —
they are the highest-signal, pre-grounded steer for a biomarker-driven case. If a variant returns
nothing, note it as `no CIViC match` (the lexical/vector/graph channels still apply).

### Step 3 — Emit the Patient Context Block

Render the block below (fenced, copy-pasteable). It is the durable artifact: it carries the profile, the resolved concept ids, and a set of **steered query seeds** — concrete, retrieval-ready phrasings derived from the case that point downstream queries in the right direction (resistance mechanisms after the prior line, biomarker-matched options to explore, comorbidity-aware constraints to read about).

The seeds are *search directions*, not recommendations. "osimertinib resistance EGFR T790M" is a thing to READ ABOUT, not a thing to DO.

### Step 4 — Offer scoped next steps

Hand the block to the right specialist, each scoped to the block:
- `/doc-evidence` — a grounded answer to this patient's specific question.
- `/doc-synthesis` — combination/coupling options to *explore* (supported-vs-speculative tagged).
- `/doc-gems` — uncommon / novel leads relevant to this profile.
- `/doc-landscape` — map the research landscape around the resolved concepts.
- `/doc-triage` — multi-facet "what does the literature say / what should I read" scan, scoped to the block.

### Step 5 — If the user attached a clinical question, answer it grounded (scoped to the block)

Run the `/doc-evidence` workflow, scoped to the block:
1. `summarize_evidence(question=<the patient's question, seeded by the block>)` → capture `allowed_paper_ids`.
2. Draft the answer as discrete claims, each citing `paper_id`s ONLY from `allowed_paper_ids`.
3. `check_grounding(claims, allowed_paper_ids)` → repair every violation, re-run until `grounded=true`.
4. Label every line `[GROUNDED]` / `[INFERENCE]` / `[GAP]`. Present what the evidence SAYS — never a recommendation, never dosing.
5. Anything the corpus can't support → `[GAP] — not found in the local corpus`; offer `/doc-watch` to ingest fresh PubMed.

---

## Output template

```
# Patient Context Block — <short case handle> · <date>

## Profile
- Cancer type:        <…>
- Histology:          <…>
- Stage:              <…>
- Grade:              <… / not stated>
- Biomarkers:         <… / not stated>
- Prior therapy:      <line → response; … / not stated>
- Comorbidities:      <… / not stated>
- Performance status: <… / not stated>
- Age / sex:          <… / not stated>
- Patient goals:      <… / not stated>

## Resolved concepts (MeSH)
- <Diagnosis>  → mesh:<…>
- <Biomarker>  → mesh:<…>
- <Drug>       → mesh:<…>
- <Term>       → not in corpus graph  (lexical/vector retrieval only — may be thin)

## CIViC biomarker matches (curated, leveled — civic:eid ids pass the gate)
- <gene variant> in <disease> → <therapy>: <sensitivity/response | resistance>, **Level <A–E>** (`civic:eid…`)
- <gene variant> → <therapy>: <significance>, **Level <A–E>** (`civic:eid…`)
- <variant with no curated match> → no CIViC match (lean on literature channels)

## Steered query seeds  (search directions — what to READ ABOUT, not what to do)
1. "<e.g. osimertinib resistance EGFR T790M>"
2. "<e.g. MET amplification after osimertinib>"
3. "<e.g. EGFR-mutant NSCLC CNS progression options>"
4. "<comorbidity-aware: e.g. systemic therapy in NSCLC with CKD stage 3>"

## Suggested next skills (each scoped to this block)
- /doc-evidence   — grounded answer to a specific question for this patient
- /doc-synthesis  — combination options to explore (supported vs speculative)
- /doc-gems       — novel / uncommon leads for this profile
- /doc-landscape  — map the research landscape around the resolved concepts
- /doc-triage     — "what does the literature say / what should I read", scoped here

## Caveats
- Fields marked `not stated` were absent from the case — nothing was inferred.
- Concepts marked `not in corpus graph` aren't in the corpus MeSH graph; retrieval for them is lexical/vector only and may be sparse.
- This block STEERS literature search. It is not a treatment plan, not advice.

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

- **Intake & steering only — never advice.** Output what to READ and what the evidence SAYS. Never output dosing, a regimen, or a "you should treat with X" recommendation. If the user asks "what should I give this patient?", redirect to "here's what the evidence SAYS about options for this profile" — grounded, then the disclaimer.
- **Never fabricate clinical fields.** Absent → `not stated`. Don't infer a stage from a vignette, don't assume a biomarker, don't invent a prior line.
- **Resolve, don't guess, concepts.** Use `find_concepts`; if a term has no match, label it `not in corpus graph` rather than forcing a wrong id.
- **Pull curated CIViC evidence for named variants.** For each actionable gene+variant, call `match_therapies` / `variant_evidence` and fold the A–E-leveled matches into the block — curated, pre-grounded (`civic:eid…`), and the strongest biomarker steer. Note variants with no CIViC match rather than omitting them.
- **The block is the durable artifact.** Emit it fenced and copy-pasteable so the user (and downstream skills) can re-use it verbatim across the session.
- **Steered seeds are search directions, not actions.** Phrase them as topics to read about (resistance mechanisms, biomarker-matched options, comorbidity constraints), never as instructions.
- **Scope is sticky downstream.** Every handoff (`/doc-evidence`, `/doc-synthesis`, etc.) carries the block as scope; remind the user the block travels with the question.
- **Gate any clinical answer.** If you answer a question (Step 5), it goes through `check_grounding` — no exceptions.
- **Clarify when thin.** Too little to steer? Ask 2–3 targeted questions before emitting a block.
- **Read-only on the corpus.** The only write escape hatch is *offering* `/doc-watch` when the corpus is too thin for the profile.

## Composition with other skills

`doc-case` sits at the front of a patient-scoped session. Typical chains:
- `doc-case` → `/doc-triage` (scoped) → drill into a specialist on the top finding.
- `doc-case` → `/doc-evidence` (scoped) for a precise question → `/doc-paper-appraisal` on the key paper.
- `doc-case` → `/doc-synthesis` (scoped) to explore combinations → `/doc-contradictions` to stress-test.

Every downstream skill should be passed the **Patient Context Block** as its scope. If a downstream skill is invoked without it during a patient session, re-attach the block first.

## Examples

**User**: *"65yo woman, EGFR L858R lung adenocarcinoma, stage IV, 1L osimertinib with PR then progression at 14 months, now T790M-negative on re-biopsy, ECOG 1, CKD stage 3."*
→ Parse the full profile; resolve EGFR / NSCLC / osimertinib to `mesh:` ids; emit a Patient Context Block whose steered seeds include "osimertinib resistance mechanisms T790M-negative", "MET amplification after osimertinib", "EGFR-mutant NSCLC systemic therapy with renal impairment". Offer `/doc-triage` and `/doc-evidence` scoped to the block. No recommendation — just steering + disclaimer.

**User**: *"Patient with BRCA1-mutant high-grade serous ovarian cancer, platinum-sensitive relapse, prior carboplatin/paclitaxel. What does the evidence say about maintenance options?"*
→ Build the block (resolve `BRCA1` → `mesh:BRCA1_Protein`, ovarian carcinoma, platinum agents); then Step 5: run the grounded `/doc-evidence` flow on "maintenance therapy options after platinum-sensitive relapse in BRCA1-mutant HGSOC" scoped to the block — claims cited from `allowed_paper_ids`, `check_grounding` gated, labelled `[GROUNDED]`/`[INFERENCE]`/`[GAP]`. Present what the evidence SAYS (e.g. PARP-inhibitor maintenance evidence), not a prescription; end with the disclaimer.

**User**: *"I've got a lung cancer patient, what should I look up?"*
→ Too thin to steer. Ask 2–3 clarifying questions first: histology (adeno / squamous / small-cell)? Driver biomarkers (EGFR / ALK / KRAS / PD-L1)? Stage and prior therapy? Then build the block from the answers.
