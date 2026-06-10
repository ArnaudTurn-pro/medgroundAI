---
name: doc-synthesis
description: The combination / coupling SIMULATOR — couple two or more research findings (drugs, targets, mechanisms, pathways) into a grounded combination HYPOTHESIS. Trigger phrases — "could X and Y be combined", "combination therapy hypothesis", "synergy between X and Y", "couple these findings", "what if we combined", "cross-paper synthesis", "connect these mechanisms", "simulate combining", "would A plus B work", "is there a rationale for X with Y", "combination opportunities in <topic>", "what could pair with X". For each component it gathers the GROUNDED mechanism (cited paper_ids), uses graph_neighbors to find SHARED pathways/concepts that form the coupling rationale, checks whether the combination is ALREADY studied (search_papers "A and B combination/synergy"), then builds a hypothesis card — grounded premises → inferential coupling FLAGGED [INFERENCE]/[GAP] → testable prediction → confirming/refuting evidence → risks (toxicity overlap, antagonism). Every premise passes check_grounding; the coupling itself is NEVER [GROUNDED] unless a paper literally reports the combination. Read-only over a finite local oncology corpus. Output is a hypothesis to TEST, not a treatment recommendation. Drill into /doc-paper-appraisal, /doc-contradictions, /doc-evidence.
---

# doc-synthesis

The **combination / coupling simulator**. You give it two-or-more components — drugs, molecular targets, mechanisms, pathways — or a topic to mine for combinations; it returns a **hypothesis card** that couples them: each component's grounded mechanism, the shared biology that makes the coupling plausible, whether anyone has already studied the pair, a testable prediction, and the evidence that would confirm or refute it.

This is generative research reasoning under a hard grounding leash. The premises are corpus-cited; the *coupling* is explicitly flagged as inference. Distinct from:

- `/doc-evidence` — answers a single question that already exists in the literature; synthesis *manufactures* a new hypothesis by joining findings.
- `/doc-landscape` — maps the concept graph; synthesis takes specific concepts and proposes coupling them into a therapy/mechanism hypothesis.
- `/doc-contradictions` — surfaces where the corpus disagrees; synthesis builds a forward-looking combination.
- `/doc-gems` — finds individual novel papers; synthesis joins multiple findings into one hypothesis.

## When to invoke

Trigger phrases (exact or paraphrased):
- *"Could olaparib and a checkpoint inhibitor be combined?"* / *"Would drug A plus drug B work?"*
- *"Combination therapy hypothesis for BRCA-mutant tumors."*
- *"Is there synergy between PARP inhibition and immunotherapy?"*
- *"Couple these two findings into a rationale."* / *"Connect these mechanisms."*
- *"What if we combined a MEK inhibitor with autophagy blockade?"*
- *"Find combination opportunities in KRAS-mutant lung cancer."* (topic-mining mode)
- *"What could pair with trastuzumab?"* (single-anchor → find partners)

If the user wants the evidence for a *single* established intervention, route to `/doc-evidence`. If they want to know where the literature *disagrees*, route to `/doc-contradictions`.

## Inputs

| Input | Default | Notes |
|---|---|---|
| `components` | required (≥1) | 2+ drugs/targets/mechanisms to couple. If only 1 given → **partner-finding mode**: mine the graph for candidate partners. If a topic given → **opportunity-mining mode**. |
| `mode` | inferred | `couple` (explicit 2+) · `partner` (1 anchor → find partners) · `mine` (topic → surface combinations). |
| `k_per_component` | 5 | Hits retrieved per component when gathering its mechanism. |
| `neighbor_limit` | 15 | `graph_neighbors` limit when hunting shared pathways. |
| `already_studied_k` | 8 | Hits for the "is this combination already in the corpus?" search. |

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

### The coupling rule (this skill's hard line)

> **The coupling is NEVER `[GROUNDED]` unless a paper in the corpus literally reports the combination.**

Each component's *mechanism* can and must be `[GROUNDED]`. The *joining* of two mechanisms into a synergy claim is `[INFERENCE]` — full stop — unless `search_papers` actually returns a paper testing A+B together, in which case that specific combination finding becomes `[GROUNDED]` and the hypothesis upgrades to "already studied". Never let an inferential leap wear a citation that only supports one half of it. This is the single most dangerous failure mode of a combination simulator and the gate exists to catch it.

## Flow

State your interpretation in one line:

> *"Coupling simulator: olaparib × pembrolizumab. I'll ground each mechanism, find shared pathways, check if the combo is already studied, then build a flagged hypothesis card."*

### Step 1 — Ground each component's mechanism

For every component, retrieve its mechanism — do NOT recall it from memory:

```
summarize_evidence("mechanism of action of <component> in cancer", k_per_facet=5)
# or, for a tighter pull:
search_papers("<component> mechanism / pathway", k=5)
```

Capture the `allowed_paper_ids` envelope and the specific `paper_id`s. Write each mechanism as a discrete `[GROUNDED]` claim with its citation. If a component's mechanism is not in the corpus, say so explicitly and offer `ingest_pubmed` — do not fabricate it.

### Step 2 — Find the shared biology (the coupling rationale)

Resolve each component to a `mesh:` concept (`find_concepts`) and pull neighborhoods:

```
find_concepts("<component>", limit=15)        # per component → canonical id
graph_neighbors("<component A concept>", hops=1, limit=15)
graph_neighbors("<component B concept>", hops=1, limit=15)
```

Intersect the two neighbor sets. **Shared concepts/pathways are the coupling rationale** — e.g. both touch *DNA repair* or *PD-L1 expression*. Remember the contract: a shared neighbor is **co-occurrence, not mechanism** — it is a *lead* for a coupling, labelled `[INFERENCE]`, not proof of synergy. If the intersection is empty, that itself is a finding: the components are biologically distant in this corpus (`[GAP]`), which weakens the hypothesis.

### Step 3 — Is the combination ALREADY studied?

Before proposing a novel hypothesis, check the corpus actually for the pair:

```
search_papers("<A> and <B> combination", k=8)
search_papers("<A> <B> synergy / combined therapy", k=8)
```

- If a paper genuinely tests A+B → the combination is **already studied**. Report it as `[GROUNDED]`, summarize the result, and reframe the card as "what's known + open questions" rather than a fresh hypothesis.
- If nothing returns → the combination is **not in the local corpus** (`[GAP]`). Say so; this is the genuinely novel case the simulator is for.

### Step 4 — Build the hypothesis

Assemble the chain, labelling every link:
- **Grounded premises** — each component's mechanism, cited `[GROUNDED]`.
- **Inferential coupling** — why joining them *might* produce synergy, `[INFERENCE]`, resting on the shared-pathway evidence from Step 2.
- **Testable prediction** — a falsifiable statement ("A+B reduces tumor volume more than either alone in BRCA-mutant models").
- **Confirming evidence** — any corpus hits that lean toward the hypothesis (cited).
- **Refuting evidence** — actively search for it (`search_papers("<A> <B> antagonism / no benefit")`); never omit it.
- **Risks** — toxicity overlap, antagonism, resistance — pulled from the corpus where possible (`summarize_evidence("<A> toxicity")`, `("<B> toxicity")`), flagged `[GAP]` where the corpus is silent.

### Step 5 — Gate every premise

```
check_grounding(claims, allowed_paper_ids)
```

Every `[GROUNDED]` line is a claim in this call. Repair each `violation` and re-run until `grounded=true`. The `[INFERENCE]` coupling is NOT submitted as a grounded claim — it is presented as inference and must not carry a citation that only supports one component.

## Output template

```
# Hypothesis card — <Component A> × <Component B>  ·  <date>
Mode: <couple / partner / mine>  ·  corpus: local store

> ⚠ This is a HYPOTHESIS TO TEST, not a treatment recommendation.
> Combinations here are research directions only — unproven until trialled.

## Components
- A: <name> (<mesh:id>)
- B: <name> (<mesh:id>)

## Grounded mechanisms  [GROUNDED]
- A: [GROUNDED] pubmed:30345884 — "<short title>" (2018) — <A's mechanism, one line>.
- B: [GROUNDED] pubmed:39281234 — "<short title>" (2024) — <B's mechanism, one line>.

## Proposed coupling  [INFERENCE]
[INFERENCE] If A does <X> and B does <Y>, joining them could <synergy rationale>, because both
converge on <shared pathway>. This is reasoning over grounded facts — NOT an observed result.

## Shared-pathway evidence (co-occurrence, not causation)
Shared MeSH neighbors of A and B: <concept> (weight N), <concept> (weight M).
[INFERENCE] These shared concepts are the coupling lead — overlap ≠ proven synergy.

## Already studied?
- [GROUNDED] pubmed:... — "<short title>" (2022) reports A+B in <setting>: <result>. (if found)
  — OR —
- [GAP] No paper in the local corpus tests A+B together. This combination is unstudied here.
  → ingest_pubmed("<A> <B> combination") to check the broader literature.

## Testable prediction
[INFERENCE] <falsifiable statement, e.g. "A+B yields greater response than monotherapy in <population>">.

## Refuting evidence
- [GROUNDED] pubmed:... — "<title>" (2020) suggests <antagonism / no benefit / overlapping resistance>. (if found)
- [GAP] No refuting evidence located in the corpus. (if none)

## Risks
- Toxicity overlap: [GROUNDED/GAP] <both cause myelosuppression? cite or flag gap>.
- Antagonism / resistance: [GROUNDED/GAP] <cite or flag>.

## Plausibility (my read)  [INFERENCE]
<Low / Moderate / High> — <one-line justification grounded in the strength of shared pathways and
any confirming evidence>.

## Drill down
- Weigh the cited papers by quality → /doc-paper-appraisal
- Check whether the mechanism evidence is contested → /doc-contradictions
- Full grounded synthesis of one component → /doc-evidence

---
*Research synthesis over a finite local corpus — not medical advice. Verify against primary sources and a treating clinician.*
**Combinations proposed here are research directions to test, never treatment recommendations. No combination is safe or effective until trialled. Do not act on a hypothesis card clinically.**
```

## Behavioural rules

- **Read-only.** Never invoke write tools (other than an explicit user-requested `ingest_pubmed`).
- **The coupling is inference, always.** Joining two grounded mechanisms into a synergy claim is `[INFERENCE]` unless a paper literally reports the combination. This is the load-bearing rule — the gate exists to enforce it. Never attach a one-component citation to a two-component claim.
- **Ground each premise independently.** Every component mechanism is its own `[GROUNDED]` claim with its own `paper_id`, passing `check_grounding`. A hypothesis built on an ungrounded premise is worthless.
- **Always hunt for refuting evidence.** Actively search antagonism / no-benefit / overlapping-resistance. Omitting the downside is how a simulator becomes a hype machine. If none is found, say `[GAP] none located` — don't imply the combo is clean.
- **Always surface risk.** Toxicity overlap and antagonism are mandatory sections even if the answer is `[GAP]`.
- **Empty intersection is a finding.** If the components share no MeSH neighbors, report the biological distance as `[GAP]` and lower the plausibility — don't manufacture a rationale.
- **Combinations are research directions only.** The strong disclaimer is mandatory and non-negotiable on every output. Never imply a hypothesis is actionable in the clinic.
- **Name the corpus boundary.** "Not studied in the local corpus" ≠ "novel to science". Offer `ingest_pubmed` to check the broader literature before claiming novelty.
- **Legible citations.** First mention: `paper_id` + short title + year. Thereafter the id.

## Examples

**User**: *"Could olaparib and pembrolizumab be combined for BRCA-mutant breast cancer?"*
→ `couple` mode. Ground olaparib's PARP-inhibition mechanism and pembrolizumab's PD-1 blockade (separate cited claims); intersect MeSH neighborhoods (shared: DNA-damage → neoantigen load); `search_papers` for the actual combination; build the card with the coupling flagged `[INFERENCE]`; surface overlapping toxicity risk; gate; render with the strong disclaimer.

**User**: *"Is there a rationale for pairing a MEK inhibitor with autophagy blockade?"*
→ `couple` mode, target+mechanism. Ground each arm; find shared pathway (RAS/MAPK survival signaling ↔ autophagy as resistance escape); check if studied; testable prediction; refuting search.

**User**: *"What could pair well with trastuzumab?"* 
→ `partner` mode. Anchor on `mesh:Trastuzumab`; `graph_neighbors` to surface candidate partners by shared biology; rank 2-3 candidates; build a brief card for the strongest, flagging all couplings `[INFERENCE]`.

**User**: *"Find combination opportunities in KRAS-mutant lung cancer."*
→ `mine` mode. `/doc-landscape`-style neighborhood around KRAS; identify bridge concepts that connect distinct therapeutic clusters; propose the 1-2 most plausible couplings as hypothesis cards, each grounded + flagged.

**User**: *"Couple these two papers I'm looking at into a hypothesis."*
→ Resolve both via `/doc-find-paper` → `get_paper` for each mechanism (grounded); intersect their MeSH terms for the coupling rationale; build the card.
