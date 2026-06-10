---
name: doc-landscape
description: Map the research landscape of a topic via the MeSH knowledge graph — concept clusters, hub concepts, bridge concepts, key papers per cluster, and the temporal trend. Trigger phrases — "map the landscape", "state of the field", "state of play", "what's the research landscape for X", "concept map", "who connects to X", "what connects to X", "research clusters", "key papers in X", "overview of the field", "give me the lay of the land", "what's adjacent to X", "neighborhood of X". Resolves an anchor concept, pulls its MeSH neighbors with co-occurrence weights, clusters them into named themes, identifies high-weight HUBS and cross-theme BRIDGES, and surfaces the key papers per cluster with publication years to read the trend over time. Read-only over a finite local oncology corpus. Edges are CO-OCCURRENCE, not causation — stated explicitly. Drill down with /doc-gems (rare edges), /doc-synthesis (bridge paths), /doc-evidence (deep-dive a cluster).
---

# doc-landscape

The "give me the map of this field" skill. You point it at a topic; it returns a **landscape map** of the surrounding MeSH concept graph — the themes a field decomposes into, the concepts that anchor it (hubs), the concepts that stitch themes together (bridges), the key papers in each theme, and how the literature has moved over time.

It is the bird's-eye view. Distinct from:

- `/doc-evidence` — drills DOWN into one clinical question with full grounded synthesis; landscape goes WIDE first.
- `/doc-find-concept` — resolves a single fuzzy term to a `mesh:` id; landscape consumes that and maps outward.
- `/doc-gems` — hunts the RARE / low-co-occurrence edges; landscape shows the dominant structure first.
- `/doc-triage` — ranks a reading list for a question; landscape draws the concept topology.

## When to invoke

Trigger phrases (exact or paraphrased):
- *"Map the landscape of HER2 breast cancer."* / *"What's the research landscape for KRAS?"*
- *"What's the state of the field on immune checkpoint inhibitors?"* / *"State of play on CAR-T?"*
- *"Give me a concept map for PARP inhibitors."* / *"Lay of the land in glioblastoma."*
- *"What connects to BRCA1?"* / *"What's adjacent to the EGFR pathway?"*
- *"What are the research clusters around pancreatic cancer?"*
- *"What are the key papers in tumor microenvironment research?"*

If the user names a specific clinical QUESTION ("does X improve survival") rather than a FIELD, prefer `/doc-evidence`. If they want the *weird* edges rather than the dominant structure, prefer `/doc-gems`.

## Inputs

| Input | Default | Notes |
|---|---|---|
| `topic` | required | A concept/term. Resolved to a canonical `mesh:` anchor via `/doc-find-concept` semantics (`find_concepts`). |
| `neighbor_limit` | 15 | Neighbors pulled around the anchor (`graph_neighbors` limit). |
| `hops` | 1 | Keep at 1 — `hops=2` is noisy per corpus guidance. Only widen if explicitly asked. |
| `papers_per_cluster` | 5 | Key papers surfaced per named cluster (`concept_papers` limit slice). |
| `corpus_frame` | off | If on, prepend `corpus_stats()` to frame coverage of the whole field. |

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

For landscape mapping the grounding contract bites in two specific places:
- **The neighbor table and weights are GROUNDED structure** — they come straight from `graph_neighbors` (weight = paper-co-occurrence count). State them as-is.
- **The clustering, the hub/bridge labels, and the trend reading are `[INFERENCE]`** — they are YOUR interpretation of the graph, not facts the corpus asserts. Flag them. Any specific *finding* you attribute to a key paper must be `[GROUNDED]` with a `paper_id` and must pass `check_grounding`.

## Flow

### Step 1 — Resolve the anchor concept

State your interpretation in one line, then resolve:

> *"Mapping the landscape around 'HER2 breast cancer'. Anchor resolution first, then 1-hop MeSH neighborhood."*

```
find_concepts("<topic fragment>", limit=15)
```

Pick the best-matching canonical id (e.g. `mesh:Receptor_ErbB_2`). If several candidates are plausible, name the one you chose and list the runners-up so the user can redirect. If nothing resolves, say so — *"'<topic>' did not resolve to a MeSH concept in the corpus graph"* — and offer `search_papers` as a lexical fallback or `ingest_pubmed` to grow coverage.

Optionally frame coverage:

```
corpus_stats()    # only if corpus_frame=on — returns live papers / concepts to size the map
```

### Step 2 — Pull the 1-hop neighborhood

```
graph_neighbors(<anchor concept_name>, hops=1, limit=15)
```

Returns `{anchor, neighbors:[{id, name, weight}]}`. The `weight` is the co-occurrence count (how many papers tag both the anchor and the neighbor). Render the raw table first — this is grounded structure:

| Neighbor concept | id | Weight (co-occurrence) |
|---|---|---|

Sort by weight descending.

### Step 3 — Cluster the neighbors into named themes `[INFERENCE]`

Group the neighbors into 2-5 coherent themes (e.g. *targeted therapy*, *resistance mechanisms*, *biomarkers*, *toxicity*). Name each cluster in plain language. This grouping is YOUR inference over concept names — label it `[INFERENCE]`. Do NOT pretend the graph asserts these clusters.

### Step 4 — Identify HUBS and BRIDGES `[INFERENCE]`

- **HUBS** — the highest-weight neighbors. These are the concepts the field organizes around; they co-occur with the anchor across many papers.
- **BRIDGES** — concepts that plausibly connect *two different clusters*. To confirm a bridge, run `graph_neighbors` on the candidate and check it neighbors concepts from more than one cluster:

```
graph_neighbors(<candidate bridge concept>, hops=1, limit=15)
```

A real bridge is a drill-down lead for `/doc-synthesis` (the coupling/combination simulator) — flag it as such.

### Step 5 — Key papers per cluster, with years → temporal trend

For the anchor and the top concept(s) in each cluster:

```
concept_papers(<concept_name>, limit=20)
```

Returns papers tagged with that MeSH concept, most-recent-first. For each cluster pick the top `papers_per_cluster`. Capture `paper_id`, title, year. Read the **trend over time** from the year distribution — is the cluster's literature recent (active front) or old (mature/settled)? That reading is `[INFERENCE]`.

If you attribute a specific *finding* to any of these papers (not just "this paper exists"), retrieve it (`search_papers` / `get_paper`) and ground it.

### Step 6 — Gate and assemble

Write every attributed finding as a claim and gate it:

```
check_grounding(claims, allowed_paper_ids)
```

Repair every violation and re-run until `grounded=true`. Then render the landscape map.

## Output template

```
# Landscape — <topic>  ·  <date>
Anchor: <mesh:id> "<canonical name>"  ·  1-hop neighbors: <N>  ·  corpus: local store

## TL;DR
<topic> decomposes into <k> themes: <A>, <B>, <C>. The field organizes around <hub concept(s)>;
<bridge concept> stitches <A> to <B> and is the most interesting coupling lead. Literature trend:
<recent surge / mature / sparse>.  [INFERENCE]

## Neighborhood (grounded structure — weight = paper co-occurrence)
| Neighbor concept | id | Weight |
|---|---|---|
| Trastuzumab | mesh:Trastuzumab | 41 |
| Drug Resistance, Neoplasm | mesh:Drug_Resistance_Neoplasm | 28 |
| ... | ... | ... |

## Clusters  [INFERENCE — my grouping of the above]
**Cluster 1 — Targeted therapy** (concepts: Trastuzumab, Pertuzumab, …)
**Cluster 2 — Resistance mechanisms** (concepts: Drug Resistance Neoplasm, PIK3CA, …)
**Cluster 3 — Biomarkers** (concepts: …)

## Hubs & bridges  [INFERENCE]
- **HUB**: <concept> (weight N) — the field's center of gravity.
- **BRIDGE**: <concept> — links Cluster 1 ↔ Cluster 2. → couple these with /doc-synthesis.

## Key papers per cluster
**Cluster 1 — Targeted therapy**
- [GROUNDED] pubmed:30345884 — "<short title>" (2018) — <one-line finding>
- [GROUNDED] pubmed:39281234 — "<short title>" (2024) — <one-line finding>

**Cluster 2 — Resistance mechanisms**
- [GROUNDED] pubmed:... — "<short title>" (2021) — <one-line finding>

## Trend over time  [INFERENCE]
Cluster 1 papers span 2014-2024 with most in the last 3 years → active front.
Cluster 3 is older (2009-2016) → comparatively settled. <one sentence on direction of travel>.

## Where the corpus is thin  [GAP]
- <neighbor with weight=1, or theme you'd expect but didn't see> — not well represented in the corpus.
- To widen: ingest_pubmed("<query>").

## Drill down
- Rare / contrarian edges in this neighborhood → /doc-gems
- Couple a bridge into a combination hypothesis → /doc-synthesis
- Deep grounded synthesis of one cluster → /doc-evidence
- Where clusters disagree → /doc-contradictions

---
*Research synthesis over a finite local corpus — not medical advice. Verify against primary sources and a treating clinician.*
Note: graph edges are **co-occurrence** (two concepts tagged on the same paper), NOT causation or mechanism.
```

## Behavioural rules

- **Read-only.** Never invoke write tools. The only mutation in the whole doc system is `/doc-watch`; this skill stays read-only.
- **Co-occurrence ≠ causation.** State this explicitly every time. A high weight means "appears together often", not "A causes / treats / drives B". This is the single most common misread of a concept graph — pre-empt it.
- **Grounded structure vs inferred map.** The neighbor table and weights are `[GROUNDED]` (straight from `graph_neighbors`). The clustering, hub/bridge labels, and trend reading are `[INFERENCE]` (yours). Never blur the two.
- **hops=1 by default.** `hops=2` is noisy — only widen if the user explicitly asks for a broader sweep, and warn that signal degrades.
- **Gate attributed findings.** "This paper exists and is tagged with concept X" needs no synthesis claim. But the moment you say "paper Y found Z", that's a claim — retrieve it and pass `check_grounding`.
- **Name the corpus boundary.** This is a finite local corpus. A sparse or empty neighborhood means the *corpus* is thin there, not that the field is empty. Say which, and offer `ingest_pubmed`.
- **Surface, don't smooth.** If two clusters look contradictory, flag it and point to `/doc-contradictions` rather than papering over it.
- **Legible citations.** First mention: `paper_id` + short title + year. Thereafter just the id.

## Examples

**User**: *"Map the landscape of HER2-positive breast cancer."*
→ Resolve anchor `mesh:Receptor_ErbB_2`; `graph_neighbors` 1-hop; cluster into targeted-therapy / resistance / biomarkers; surface hubs (Trastuzumab) and a bridge (PIK3CA linking targeted-therapy ↔ resistance); `concept_papers` per cluster with years; read the trend; render the landscape map.

**User**: *"What's the state of the field on immune checkpoint inhibitors?"*
→ Resolve a checkpoint-inhibitor concept; map neighbors (PD-1, PD-L1, CTLA-4, irAEs, biomarkers); cluster; flag the toxicity cluster and bridge to `/doc-contradictions` if response-rate evidence conflicts.

**User**: *"What connects to BRCA1?"*
→ 1-hop neighborhood around `mesh:BRCA1_Protein`; raw weighted neighbor table first; cluster (DNA repair / PARP inhibition / hereditary risk); identify the PARP-inhibitor bridge as a `/doc-synthesis` lead.

**User**: *"Give me a concept map for pancreatic cancer, and tell me where the corpus is thin."*
→ Full landscape map plus an explicit `[GAP]` section listing weight-1 neighbors and expected-but-absent themes, with an `ingest_pubmed` offer.

**User**: *"Lay of the land on KRAS — go two hops out."*
→ Honor `hops=2` but warn signal degrades; render with a clear note that distant neighbors are low-confidence.
