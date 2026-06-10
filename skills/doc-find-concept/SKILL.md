---
name: doc-find-concept
description: Resolve a fuzzy biomedical term — gene, protein, drug (brand or generic), disease, pathway, or MeSH descriptor — into a canonical `mesh:` concept id (e.g. `mesh:BRCA1_Protein`). Use BEFORE any MeSH-graph op that needs a canonical concept: `graph_neighbors`, `concept_papers`, `/doc-landscape`, `/doc-gems`. Handles synonyms, aliases, brand-vs-generic drug names, and gene-vs-protein ambiguity. Triggers: "what's the concept id for X", "find the MeSH term", "the gene/drug/disease X", "map <term> to the graph", "is <term> in the knowledge graph", "neighbors of <term>", "papers tagged <term>".
---

# doc-find-concept

Resolve a fuzzy biomedical term → canonical `mesh:` concept id. Surgical resolver; runs before any MeSH-graph skill.

## When to invoke

- "What's the concept id for BRCA1?" / "map olaparib to the graph"
- Before `/doc-landscape`, `/doc-gems`, or a direct `graph_neighbors` / `concept_papers` call that needs the canonical concept name/id.
- When a user names a gene, protein, drug, disease, or pathway in prose and a downstream op needs the exact `mesh:` handle.

## Inputs

| Input | Default | Notes |
|---|---|---|
| `term` (required) | — | Gene/protein/drug/disease/pathway/descriptor, any spelling or alias. |
| `limit` | 15 | Candidates from `find_concepts`. |

## Flow

1. **Typeahead the graph.** Call `find_concepts(term, limit=15)` → `[{id, name}]`. This maps the fragment to canonical MeSH nodes.

2. **One obvious canonical match?** Return its id (e.g. `BRCA1` → `mesh:BRCA1_Protein`). Done.

3. **Several candidates?** Present a numbered table and pick the most canonical, or ask:

   | # | id | name |
   |---|---|---|
   | 1 | mesh:BRCA1_Protein | BRCA1 Protein |
   | 2 | mesh:Genes_BRCA1 | Genes, BRCA1 |

   Prefer the descriptor that matches the user's *intent* — gene-as-locus (`Genes,_BRCA1`) vs the protein product (`BRCA1_Protein`). If the downstream skill traverses co-occurrence, the protein/descriptor node is usually the populated one.

4. **Handle synonyms / aliases.** If the first fragment returns nothing or only weak hits, try a second:
   - **Brand → generic** (and vice versa): "Lynparza" → try "olaparib"; "Keytruda" → "pembrolizumab".
   - **Gene ↔ protein**: "HER2" → try "ERBB2", "Receptor, ErbB-2".
   - **Abbreviation → expansion**: "NSCLC" → "non-small-cell lung carcinoma".

5. **Confirm it's populated (optional).** Call `concept_papers(<name>, limit=3)` to show the node actually has papers behind it before handing off — a canonical id with zero papers is a dead end for `/doc-landscape`.

6. **Absent from the graph?** Say so plainly — *"Not in the corpus MeSH graph."* — and suggest the closest neighbor that *is* present, or `/doc-find-paper` if the user is really after a specific paper rather than a concept.

## Output

When invoked by another skill, just return the canonical id(s): `mesh:BRCA1_Protein`. When invoked directly, print the one-liner:

> Resolved **BRCA1** → `mesh:BRCA1_Protein` (BRCA1 Protein) — **34** papers tagged.

Or, on ambiguity, the numbered table above + "Which sense did you mean?"

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

- **Never invent a `mesh:` id.** Every id you return must come from a real `find_concepts` hit.
- **Resolve, don't analyze.** This skill returns a concept id, not a landscape or a finding. Hand off to `/doc-landscape`, `/doc-gems`, or `graph_neighbors` for traversal.
- **Try aliases before declaring absence.** Brand/generic and gene/protein swaps catch most "not found" misses — exhaust them first.
- **Pick the populated node.** When two ids are equally canonical, prefer the one `concept_papers` shows has more papers behind it.
- **Read-only.** Surface MCP errors verbatim; never swallow them.

## Examples

| User says | What the skill does |
|---|---|
| "the BRCA1 gene" | `find_concepts("BRCA1", limit=15)` → returns `mesh:BRCA1_Protein` and `mesh:Genes_BRCA1`; picks the populated protein node, notes the alternative. |
| "Lynparza" (brand) | `find_concepts("Lynparza")` → empty → retries `find_concepts("olaparib")` → returns `mesh:Olaparib` (or closest descriptor). |
| "HER2" | `find_concepts("HER2")` → weak → retries `"ERBB2"` → `mesh:Receptor,_ErbB-2`; confirms with `concept_papers`. |
| "PARP" (ambiguous) | `find_concepts("PARP")` returns enzyme + inhibitor-class nodes → numbered table → asks "the enzyme `Poly(ADP-ribose) Polymerases` or the drug class?". |
| "some pathway I half-remember about ferroptosis" | `find_concepts("ferroptosis")` → empty → "Not in the corpus MeSH graph; closest present node is `mesh:Iron`. Want a paper search instead via /doc-find-paper?" |

---

*Research synthesis over a finite local corpus — not medical advice. Verify against primary sources and a treating clinician.*
