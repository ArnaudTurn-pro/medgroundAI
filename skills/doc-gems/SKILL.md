---
name: doc-gems
description: Surface uncommon, high-novelty, innovative papers in the medground corpus — answers "find the gems", "hidden gems", "underappreciated papers", "overlooked studies", "novel findings", "innovative results", "what's surprising", "what's contrarian", "first-of-kind", "off the beaten path", "what should I read that others miss", "anything unexpected about X", "surprising findings about resistance". Given a topic or seed concept (resolved via doc-find-concept), it hunts novelty through the MeSH graph — graph_neighbors to find LOW-weight (rare) edges and BRIDGE concepts that link otherwise-distant clusters — then concept_papers on those rare neighbors plus search_papers with contrarian/novelty phrasings ("novel mechanism", "unexpected", "first report", "contrary to", "paradoxical") to assemble candidate gems. Each candidate is scored on Rarity + Bridging + Recency + Potential-impact, then put through an HONESTY pass that separates "genuinely novel" from "merely obscure / low quality" and flags any gem whose quality is unverified (routing to doc-paper-appraisal). Emits a ranked gem list with gem-type, novelty signal, and a quality flag. Every paper claim is grounded (paper_id) and passes check_grounding. Research synthesis, not medical advice.
---

# doc-gems

The "find the hidden gems / what should I read that everyone misses / anything surprising about X"
skill.

Most retrieval rewards the *central, well-connected, oft-cited* papers. This skill deliberately
inverts that — it hunts the **periphery**: rare concept co-occurrences, **bridge** papers that
wire together distant clusters, and contrarian/first-of-kind findings. Then it does the honest
thing most novelty-hunters skip: it separates a genuine gem from a paper that is merely *obscure
because it's weak*. Distinct from:

- `/doc-landscape` — maps the *central* structure of a topic (hubs, clusters, trend); this hunts the *edges*.
- `/doc-evidence` — synthesizes the *consensus* answer to a question; this surfaces the *outliers*.
- `/doc-triage` — builds a balanced reading list; this builds a deliberately *contrarian* one.
- `/doc-paper-appraisal` — judges a *single* paper's quality; `/doc-gems` *routes to it* to verify any gem whose quality is unproven.

## When to invoke

Trigger phrases (exact or paraphrased):
- *"Find the gems"* / *"Hidden gems"* / *"Any hidden gems on X?"*
- *"Underappreciated / overlooked / neglected papers"*
- *"Novel / innovative findings"* / *"What's new and weird here?"*
- *"What's surprising / contrarian / counterintuitive?"*
- *"First-of-kind"* / *"First report of"* / *"Anything paradoxical?"*
- *"Off the beaten path"* / *"What should I read that others miss?"*
- *"Surprising findings about resistance / metastasis / a specific mechanism"*
- *"Gems bridging X and Y"* / *"Unexpected connections between two things"*

If the user gives a fuzzy topic ("KRAS resistance", "the immunotherapy stuff"), resolve the anchor
with **`/doc-find-concept`** FIRST. If they give two concepts and ask for *bridges*, run the
bridging path (Step 3) between both anchors.

## Inputs

| Input | Default | Notes |
|---|---|---|
| `topic` | — (required) | A topic or seed concept. Resolve fuzzy → `mesh:` id via `/doc-find-concept`. |
| `second_anchor` | unset | A second concept — switches to **bridge-hunting** mode (gems linking the two). |
| `top_n` | 8 | Gems to surface. |
| `recency_floor` | unset | Optional year cutoff (e.g. 2020) to bias toward recent novelty. |
| `gem_types` | all | Subset of `rare-edge` / `bridge` / `contrarian` / `first-of-kind` if the user wants one flavour. |

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

> **Gems nuance.** "This is a gem" is an `[INFERENCE]` — a judgement about novelty/impact. The
> *facts* it rests on (what the paper claims, what year, which concepts it bridges, how rare the
> edge is) are `[GROUNDED]` to a `paper_id` and a graph weight. Novelty-hunting is the one place the
> temptation to embellish ("groundbreaking!", "paradigm-shifting!") is strongest — resist it. A gem
> is a *grounded* finding that happens to be rare/bridging/contrarian, never a hyped one.

## Flow

### Step 1 — State your interpretation + resolve the anchor

> *"Hunting gems around <topic>. Resolving the anchor concept, then probing the MeSH periphery for rare edges, bridges, and contrarian findings. Read-only."*

Resolve the seed with `find_concepts(fragment, limit=15)` → pick the canonical `mesh:` id (or
defer to `/doc-find-concept` if ambiguous). For bridge mode, resolve BOTH anchors.

### Step 2 — Map the neighbourhood (find the rare edges)

`graph_neighbors(concept_name, hops=1, limit=15)`

The returned `neighbors` carry a **weight** = paper-co-occurrence count. Read it inversely:
- **LOW-weight neighbors** (weight 1-3) = rare co-occurrences = candidate novelty edges.
- **HIGH-weight neighbors** = the well-trodden core — *deprioritize* these (they're `/doc-landscape` territory).

Keep `hops=1` (hops=2 is noisy). Record the low-weight neighbor concepts; these are your rare-edge probes.

### Step 3 — Find the bridges (distant-cluster connectors)

A **bridge** concept connects two clusters that don't otherwise talk. To find them:
- Single-anchor mode: among the low-weight neighbors, run `graph_neighbors` on a couple of them and
  look for a concept that appears in *both* the anchor's and the neighbor's lists but with low
  weight on each side — that intermediary is a bridge.
- `second_anchor` mode: run `graph_neighbors` on both anchors and intersect — concepts (or papers)
  appearing on both sides, especially at low weight, are the bridge candidates linking the two topics.

### Step 4 — Pull candidate gem papers

For each rare-edge / bridge concept from Steps 2-3:

`concept_papers(concept_name, limit=20)`  → papers tagged with that rare concept (recent-first).

Then widen with contrarian/novelty phrasings via lexical+vector retrieval — issue these as a
parallel batch, scoped to the topic:

`search_papers("<topic> novel mechanism", k=8)`
`search_papers("<topic> unexpected OR paradoxical", k=8)`
`search_papers("<topic> first report OR first-in-class", k=8)`
`search_papers("<topic> contrary to OR challenges the view", k=8)`

Pool the hits. De-duplicate by `paper_id`. This pool is the candidate-gem set and defines the
`allowed_paper_ids` envelope.

### Step 5 — Score each candidate

For every candidate, score four axes from grounded signals (each 0-3):

| Axis | Signal | Source |
|---|---|---|
| **Rarity** | low co-occurrence weight of the concept edge it sits on | `graph_neighbors` weight |
| **Bridging** | does it connect two otherwise-distant concepts/clusters? | Step 3 intersection |
| **Recency** | how new is it? (recent novelty > old novelty) | `year` from hit metadata |
| **Potential-impact** | does the abstract claim an outsized/mechanistic/first result? | `search_papers` text / `get_paper` abstract |

`gem_score = Rarity + Bridging + Recency + Potential-impact` (0-12). Tag each gem's **type**:
`rare-edge` · `bridge` · `contrarian` · `first-of-kind`.

### Step 6 — HONESTY pass (genuine gem vs merely obscure)

This is the step that makes the skill trustworthy. For the top candidates, ask: **is this rare
because it's novel, or rare because it's weak/wrong/ignored-for-good-reason?**

- Skim the candidate's design from its abstract/metadata (`get_paper` if needed). A "novel
  mechanism" from a single in-vitro assay is *interesting but unverified*, not a validated gem.
- If a gem's quality is **unverified or shaky**, do NOT drop it — surface it WITH a quality flag
  (`⚠️ quality unverified`) and route the user to **`/doc-paper-appraisal`** to grade it properly.
- If a candidate is rare merely because it's an editorial/opinion/duplicate, demote or drop it and say why.
- Distinguish `[INFERENCE] genuinely novel` from `[INFERENCE] merely obscure` explicitly in the why-line.

Never inflate. A gem you flag honestly is worth more than a hyped one the user can't trust.

### Step 7 — Gate, then render

Assemble every paper claim as a citation-bearing claim list and run
`check_grounding(claims, allowed_paper_ids)` over the pooled envelope from Steps 4-6. Repair every
violation; re-run until `grounded=true`. Then render the ranked gem list.

---

## Output templates

### Ranked gem list (default)

```
# Hidden gems — <topic> · <date>
Anchor: `<mesh:id>` <concept name> · corpus: local store · read-only

## TL;DR
[INFERENCE] <one or two sentences: the single most interesting gem and the through-line —
e.g. "The strongest signal is a low-weight bridge between <A> and <B> via <paper>; two further
contrarian reports challenge the consensus on <X>.">

## Gems (ranked by novelty signal)
| # | paper_id | Title (year) | Gem type | Novelty signal | Quality flag |
|---|---|---|---|---|---|
| 1 | `pubmed:…` | <short title> (2023) | bridge | links <A>↔<B>, edge weight 2 | ✅ RCT-grade / ⚠️ unverified |
| 2 | `pubmed:…` | <short title> (2022) | contrarian | contradicts consensus on <X> | ⚠️ single in-vitro |
| 3 | `pubmed:…` | <short title> (2024) | first-of-kind | "first report of <Y>" | ⚠️ unverified → appraise |
| … | | | | | |

## Why each matters (grounded)
1. **`pubmed:…`** — [GROUNDED] reports <finding> (`pubmed:…`). [INFERENCE] gem because <rarity/bridge/contrarian rationale>. Quality: <flag + one line>.
2. …

## Honesty notes
- [INFERENCE] <which "gems" are genuinely novel vs merely obscure, and why>
- <any candidate demoted/dropped and the reason — editorial, duplicate, too weak>

## Drill-down
- Verify a gem's quality before you trust it → /doc-paper-appraisal
- Couple two gems into a combination hypothesis → /doc-synthesis
- See the central structure these gems sit at the edge of → /doc-landscape
- Check whether a contrarian gem is genuinely contested → /doc-contradictions

## Coverage note
Bounded by the finite local corpus — "rare here" can mean "well-known elsewhere". To widen the net, run `ingest_pubmed` on <topic> and re-run.

*Research synthesis over a finite local corpus — not medical advice. Verify against primary sources and a treating clinician.*
```

### Bridge mode (`second_anchor` set)

```
# Bridge gems — <A> ✕ <B> · <date>
Anchors: `<mesh:A>` <name A> · `<mesh:B>` <name B>

## The bridge
[INFERENCE] <concept(s) / paper(s) that wire these two topics together, with the low edge weights that make the link non-obvious.>

| # | paper_id | Title (year) | Bridges via | Edge weight | Quality flag |
|---|---|---|---|---|---|
| 1 | `pubmed:…` | <short title> (year) | <intermediary concept> | A:2 / B:1 | ⚠️/✅ |

## Why this connection is interesting
[INFERENCE] <what a combination of these two distant areas might imply — clearly tagged speculative, hand off to /doc-synthesis for a grounded hypothesis.>

*Research synthesis over a finite local corpus — not medical advice. Verify against primary sources and a treating clinician.*
```

---

## Behavioural rules

- **Read-only.** `find_concepts`, `graph_neighbors`, `concept_papers`, `search_papers`, `get_paper`, `check_grounding` only. Don't ingest unless the user asks.
- **Invert the weight.** Gems live at LOW co-occurrence weight. If you find yourself surfacing the obvious hub papers, you're doing `/doc-landscape`, not this.
- **hops=1.** Don't go to hops=2 — the periphery gets noisy and the bridges stop being real.
- **Honesty over hype.** Every gem gets a quality flag. "Rare" is not "good". Never present an unverified single-assay finding as a validated breakthrough.
- **Flag, don't hide.** A gem with shaky quality is still surfaced — with `⚠️` and a route to `/doc-paper-appraisal`. Don't silently drop it, and don't silently trust it.
- **Label novelty as inference.** "This is a gem" is `[INFERENCE]`; the underlying finding/year/edge-weight is `[GROUNDED]`.
- **Gate before render.** No gem list ships before `check_grounding` returns `grounded=true`.
- **Speculative connections → /doc-synthesis.** If a bridge implies a combination idea, hand it off; don't manufacture a mechanism here.
- **Corpus is finite.** "Rare here" ≠ "rare in the literature". Always include the coverage note and offer `ingest_pubmed`.

## Composition with other skills

- Upstream: `/doc-find-concept` resolves the anchor(s).
- Downstream: `/doc-paper-appraisal` (verify a gem's quality), `/doc-synthesis` (couple gems into a hypothesis), `/doc-landscape` (see the centre these edges hang off), `/doc-contradictions` (is a contrarian gem genuinely contested?).

The drill-down links are offers, not chained calls. If the user says "appraise gem #2", route that to `/doc-paper-appraisal` with that `paper_id`.

## Examples

**User**: *"Find the hidden gems on KRAS-inhibitor resistance."*
→ resolve `mesh:` anchor for KRAS-inhibitor resistance, map low-weight neighbors, pull concept_papers + contrarian search, score, honesty-pass, render ranked gem list with quality flags.

**User**: *"Any surprising / contrarian findings about immunotherapy resistance?"*
→ emphasize `contrarian` gem-type; run the "contrary to / challenges the view / paradoxical" search probes; surface both the surprising claim and its quality flag.

**User**: *"What gems bridge ferroptosis and the tumor microenvironment?"*
→ `second_anchor` bridge mode; resolve both, intersect neighbours, surface low-weight connector papers, hand the combination idea to `/doc-synthesis`.

**User**: *"What should I read on metastasis that everyone overlooks?"*
→ rare-edge + first-of-kind types, recency-aware; render the gem list, honestly separating genuinely-novel from merely-obscure.

**User**: *"Is that 'first report' gem actually any good?"*
→ route to `/doc-paper-appraisal` with the gem's `paper_id` — this skill finds gems, that one grades them.
