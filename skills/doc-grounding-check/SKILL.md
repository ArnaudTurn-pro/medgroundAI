---
name: doc-grounding-check
description: The audit skill of the doc profile — fact-checks an EXTERNAL draft, claim, statement, or treatment plan against the medground oncology-literature corpus and tells you, claim by claim, whether the literature actually supports it. Use when the user pastes text and asks "is this supported by the literature", "fact-check this", "check this claim / draft / paragraph / treatment plan", "verify these statements", "is this grounded", "ground-truth this", "are these citations real", "did I make this up", "does the evidence back this up". Parses the text into atomic claims, runs evaluate_plan for treatment plans or search_papers per claim to find candidate citations, then runs the deterministic check_grounding gate and classifies each claim — grounded / uncited / phantom_citation (fabricated reference) / off_envelope, and for plans supported / contested / unsupported / unknown. Emits a verdict TABLE, an overall grounded_ratio, and a corrected grounded rewrite. Does NOT rubber-stamp — confident unsupported claims and fabricated citations are the headline risk it exists to catch. NOT medical advice. Drill into /doc-evidence to build a properly grounded answer, or /doc-find-paper.
---

# doc-grounding-check

The audit skill. You paste a draft, a claim, a paragraph, or a treatment plan; this skill tells you — **claim by claim** — whether the corpus actually supports it, catches fabricated citations, and hands back a corrected, grounded rewrite.

Its job is to **not rubber-stamp.** The headline risk in oncology synthesis is a confident, fluent, *unsupported* claim — or a citation that looks real but isn't. This skill is the adversary that finds them. Distinct from:

- `/doc-evidence` — *builds* a grounded answer from a question. This skill *audits* text you already have.
- `/doc-triage` — vague "where's the evidence" scans. This skill verifies specific assertions.
- `/doc-paper-appraisal` — grades one paper's internal quality. This skill checks whether your claims map to papers at all.

## When to invoke

Trigger phrases (exact or paraphrased):
- *"Is this supported by the literature?"* / *"Does the evidence back this up?"*
- *"Fact-check this."* / *"Ground-truth this."* / *"Verify these statements."*
- *"Check this claim / draft / paragraph."*
- *"Check this treatment plan."* → plan mode (uses `evaluate_plan`).
- *"Are these citations real?"* / *"Did I make any of this up?"*
- *"Is this grounded?"*

If the user wants you to *write* the answer rather than check theirs → `/doc-evidence`.
If they want a single paper's quality graded → `/doc-paper-appraisal`.

**Detect plan vs claims:** if the pasted text reads as a clinical plan (a sequence of intended interventions / regimen / management steps for a patient), use **plan mode** (`evaluate_plan`). Otherwise treat it as **loose claims** and search per claim.

## Inputs

| Input | Default | Notes |
|---|---|---|
| `text` | — (required) | The pasted draft / claim set / treatment plan to audit. |
| `mode` | inferred | `claims` (default) or `plan`. Plan mode when the text is a management/treatment sequence. |
| `k_per_claim` | 5 | Candidate hits retrieved per claim (`evaluate_plan` / `search_papers`). |
| `strictness` | `standard` | `standard` = flag uncited + phantom + off-envelope. `strict` = also flag claims supported only by weak/indirect hits as `contested`. |
| `existing_citations` | parsed from text | If the draft already carries `paper_id`s, audit those *as given* first — phantom citations are the highest-value catch. |

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

### Step 1 — Parse into atomic claims

Break the pasted text into **one assertion per claim**. Split compound sentences ("X improves PFS and reduces grade-3 AEs" → two claims). Preserve any `paper_id`s the author already attached — those get audited *as given* (a citation the author wrote that turns out fake is the single highest-value finding). Echo the parsed claim list back so the user can confirm the split.

State your interpretation in one line:
> *"Parsed your paragraph into 6 atomic claims; 2 carry citations I'll verify as-given. Mode: claims. Auditing against the local corpus."*

### Step 2 — Find candidate citations

**Plan mode** (treatment plan):
```
evaluate_plan(plan_text="<the pasted plan>", k_per_claim=5)
```
Returns `{plan, claims:[{claim, hits:[...]}], verdict_schema}`. Each claim arrives with retrieved candidate hits; you will judge each as **supported / contested / unsupported / unknown** per the verdict schema.

**Claims mode** (loose statements):
```
search_papers(query="<claim restated as a search query>", k=5)   # one call per claim, in parallel
```
Collect the candidate hits per claim. The union of all retrieved `paper_id`s becomes the **envelope** for the gate. If the draft already cited a `paper_id`, also confirm it appears in the corpus at all — if `search_papers` / `get_paper` cannot find it, it's a phantom citation before the gate even runs.

### Step 3 — Run the deterministic gate

Assemble each claim with its best candidate citation(s) and gate the lot:
```
check_grounding(claims=[{text, citations}, ...], allowed_paper_ids=<retrieved envelope>)
```
Returns `{grounded, grounded_ratio, n_claims, claims:[{text,citations,status,problems}], violations}`.

The author's original citations are checked **exactly as written** — this is how `phantom_citation` (fabricated/garbled id) and `off_envelope` (real paper, but doesn't actually cover this claim) get caught.

### Step 4 — Classify every claim

Map each claim to a verdict. Do **not** soften — an unsupported confident claim is the thing this skill exists to surface.

| `check_grounding` status | Verdict | Meaning |
|---|---|---|
| `grounded` | **Grounded** | A corpus paper in the envelope supports it. |
| `uncited` | **Uncited** | Assertion with no citation. Found a real one? attach it. Couldn't? → cut or soften. |
| `phantom_citation` | **Phantom citation** | Cites a paper_id not in the corpus — likely fabricated or garbled. The headline catch. |
| `off_envelope` | **Off-envelope** | Cites a real corpus paper that does NOT cover this claim — citation/claim mismatch. |

For **plan mode**, additionally render the `evaluate_plan` verdict per step:

| Plan verdict | Meaning |
|---|---|
| **supported** | corpus evidence backs this step |
| **contested** | corpus has evidence on both sides — surface it, route to `/doc-contradictions` |
| **unsupported** | no corpus evidence supports this step — flag loudly |
| **unknown** | corpus is silent — not endorsed, not refuted; says so |

### Step 5 — Repair: find real support or recommend the cut

For every failing claim, **attempt a rescue** before condemning it:
- Re-query with a sharper `search_papers` to find a genuinely supporting paper. If found, attach it and re-gate.
- If no real support exists in the corpus → recommend **cutting** the claim, or **softening** it to a hedged/`[GAP]` statement. Never invent a citation to make it pass.
- Re-run `check_grounding` after repairs so the final `grounded_ratio` reflects the rescued state.

### Step 6 — Render the verdict table + corrected rewrite

Template below. Lead with the failures (that's the value), give the overall `grounded_ratio`, and hand back a clean grounded rewrite.

---

## Output template

```
# Grounding audit — <date>
Mode: <claims | plan> · Claims parsed: <n> · Corpus: local store
Overall grounded_ratio: <0.00–1.00>  ·  Phantom citations found: <n>  ·  Unsupported: <n>

## TL;DR
<1-2 sentences. Lead with the worst finding — e.g. "2 of 6 claims are unsupported and one
citation (pubmed:99999999) does not exist in the corpus.">

## Verdict table
| # | Claim (as written) | Status | Best citation found | Verdict / fix |
|---|---|---|---|---|
| 1 | "Olaparib maintenance extends PFS in BRCA-mut ovarian ca" | Grounded | pubmed:30345884 (2018) | Keep as-is. |
| 2 | "It improves overall survival by 40%" | Off-envelope | pubmed:30345884 covers PFS not OS | Mismatch — paper shows PFS benefit, not a 40% OS figure. Soften or cite an OS-specific source (none in corpus). |
| 3 | "per Smith 2021 (pubmed:99999999)" | Phantom citation | — | pubmed:99999999 is NOT in the corpus. Citation appears fabricated. CUT or replace. |
| 4 | "Grade-3 anemia is the main toxicity" | Grounded | pubmed:31562799 (2019) | Keep. |
| 5 | "This regimen is curative" | Uncited | none found | No corpus support for "curative". CUT — overclaim. |
| 6 | "Combine with bevacizumab for synergy" | Unsupported (plan) | none found | Corpus silent on this combination. Flag as speculative → /doc-synthesis to model the hypothesis. |

## Plan verdicts (plan mode only)
| Step | Verdict | Note |
|---|---|---|
| Platinum-based induction | supported | pubmed:... |
| PARPi maintenance | supported | pubmed:30345884 |
| Add anti-angiogenic | contested | conflicting corpus evidence → /doc-contradictions |
| Off-label kinase inhibitor | unknown | not found in the local corpus |

## Corrected, grounded rewrite
<The pasted text rewritten so every retained claim is grounded: overclaims softened, phantom
citations removed, unsupported lines cut or marked [GAP]. Each surviving claim carries a real
paper_id the gate accepts. Inference clearly flagged [INFERENCE].>

## Not supported by the corpus
- <claim that had to be cut/softened> — not found in the local corpus.
→ To check fresh literature: `ingest_pubmed("<query>")` (via /doc-watch), then re-audit.

## Drill down
- Build a properly grounded answer for the topic instead → /doc-evidence
- Resolve a fuzzy/garbled paper reference → /doc-find-paper
- Conflicting evidence on a contested step → /doc-contradictions

---
*Research synthesis over a finite local corpus — not medical advice. Verify against primary sources and a treating clinician.*
```

---

## Behavioural rules

- **Do NOT rubber-stamp.** The default posture is skeptical. A fluent, confident, unsupported claim is exactly what this skill exists to catch — surface it loudly, don't normalize it.
- **Phantom citations are the top priority.** Always verify author-supplied `paper_id`s *as written* against the corpus. A citation that doesn't resolve is the highest-value finding — flag it explicitly with the dead id.
- **Read-only on the corpus.** Never mutate. The only write is an explicit `ingest_pubmed` the user requests (via `/doc-watch`).
- **Atomic claims only.** Split compounds; one assertion per row. Echo the parse so the user can correct the split.
- **The gate is the arbiter, not your intuition.** Run `check_grounding` and report its verdicts; don't override `grounded`/`uncited`/`phantom`/`off_envelope` with a hunch.
- **Rescue before condemning.** For each failure, try one sharper `search_papers` to find genuine support before recommending a cut. But never fabricate a citation to force a pass.
- **Never backfill from model memory.** If the corpus can't support a claim, the verdict is *unsupported / not in the local corpus* — full stop. Offer `ingest_pubmed`; don't paper over the gap with general knowledge.
- **Surface contested evidence.** Plan steps with corpus evidence on both sides are `contested`, not `supported` — route to `/doc-contradictions`.
- **Always report the grounded_ratio** before and after repair — it's the honesty metric.
- **Surface MCP errors verbatim.** Never swallow a tool error.

## Composition with other skills

- The natural follow-up to a failed audit is `/doc-evidence` — build the answer correctly from scratch instead of patching a broken draft.
- This skill is what the `grounding-auditor` agent runs as its core loop.
- Garbled/fuzzy references → `/doc-find-paper`; contested plan steps → `/doc-contradictions`; speculative combinations flagged in a plan → `/doc-synthesis`.

## Examples

**User**: *"Fact-check this: 'Olaparib maintenance extends PFS in BRCA-mutated ovarian cancer (pubmed:30345884) and improves overall survival by 40%.'"*
→ claims mode; parse into 2 claims; gate. Claim 1 → Grounded. Claim 2 → Off-envelope (the cited paper shows PFS, not a 40% OS figure). Verdict table + softened rewrite.

**User**: *"Are the citations in this paragraph real? <paragraph with pubmed:99999999>"*
→ Verify each author-supplied id against the corpus first; `pubmed:99999999` doesn't resolve → **phantom citation**, headline finding. Recommend cut/replace; rebuild via `/doc-evidence`.

**User**: *"Check this treatment plan: platinum induction → PARPi maintenance → add bevacizumab → off-label kinase inhibitor."*
→ plan mode; `evaluate_plan`; per-step verdicts: induction *supported*, PARPi *supported*, bevacizumab *contested* (→ /doc-contradictions), kinase inhibitor *unknown* (not in corpus). Disclaimer prominent.

**User**: *"Is this paragraph grounded, or did I overclaim anywhere?"*
→ claims mode; parse; per-claim `search_papers` → `check_grounding`; flag the "curative"/overclaim lines as Uncited, attempt rescue, hand back a hedged grounded rewrite with the grounded_ratio.
