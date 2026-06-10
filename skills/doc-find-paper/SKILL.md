---
name: doc-find-paper
description: Resolve a partial or fuzzy paper reference into one or more canonical `paper_id`s (e.g. `pubmed:39281234`). Use when the user names a paper by a title fragment, an author name, a journal+year, or a topic-ish handle like "that olaparib maintenance trial", "the KEYNOTE-189 paper", "Sung 2021 on HCC", "the BRCA PARP inhibitor study". Call this BEFORE any op that needs a paper id — `get_paper`, `get_paper_chunks`, `/doc-paper-appraisal`, or any skill handed a specific paper. Triggers: "find the paper", "which paper said", "look up that study", "the trial about X", "paper by <author>", "that <year> <journal> paper".
---

# doc-find-paper

Resolve a fuzzy paper reference → canonical `paper_id`. Surgical resolver; runs before any paper-id-consuming skill.

## When to invoke

- "Find the paper on olaparib maintenance in ovarian cancer."
- "That 2021 NEJM pembrolizumab trial — what's its id?"
- "Look up the study by Robson on talazoparib."
- Any handoff where a downstream skill (`/doc-paper-appraisal`, `get_paper`) needs a `paper_id` but the user gave prose.

## Inputs

| Input | Default | Notes |
|---|---|---|
| `ref` (required) | — | Anything the user said: title fragment, author, topic, journal+year, partial id. |
| `k` | 10 | Candidates to retrieve (8–15). Hard cap 50. |

## Flow

1. **Already a paper id?** If `ref` matches `^pubmed:\d+$` (or a bare PMID `^\d{6,9}$` → prefix `pubmed:`), skip retrieval. Confirm with `get_paper(paper_id)`; if it resolves, return it.

2. **Retrieve candidates.** Call `search_papers(ref, k=10)`. If the ref is really a *topic* ("PARP inhibitors in BRCA-mutant breast cancer") more than a specific paper, also resolve the concept first via `/doc-find-concept` and pull `concept_papers(<concept name>, limit=15)` to widen the net.

3. **Rank candidates** by: title-fragment overlap → author match → journal+year match → retrieval score. A title the user half-quoted beats a high vector score on a tangential paper.

4. **One clear match?** Return its `paper_id` and a one-line confirmation. Optionally `get_paper(paper_id)` to confirm title/authors/year before handing off.

5. **Several plausible?** Present a short numbered table and ask which:

   | # | paper_id | title | year | journal | why matched |
   |---|---|---|---|---|---|
   | 1 | pubmed:30345884 | Olaparib maintenance … ovarian | 2019 | NEJM | title + drug match |
   | 2 | pubmed:28578601 | Niraparib maintenance … | 2017 | NEJM | adjacent drug, same setting |

6. **Nothing matches?** The corpus is finite. Say so plainly — *"No match in the local corpus."* — and offer `ingest_pubmed(<query>, max_results=20)` to pull it from PubMed (note: cost/time; ask first).

## Output

When invoked by another skill, just return the resolved id(s): `pubmed:30345884`. When invoked directly, print the one-liner:

> Resolved to **pubmed:30345884** — *"Maintenance Olaparib in Patients with Newly Diagnosed Advanced Ovarian Cancer"* (NEJM, 2019).

Or, on ambiguity, the numbered table above + "Which one?"

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

- **Never invent a `paper_id`.** Every id you return must come from a real `search_papers` / `concept_papers` hit. If it didn't appear in a tool result, it doesn't exist for our purposes.
- **Resolve, don't synthesize.** This skill returns an id, not an answer about the paper's findings. Hand off to `/doc-evidence` or `/doc-paper-appraisal` for content.
- **Read-only.** No watches, no ingestion — except offering `ingest_pubmed` when the corpus genuinely lacks the paper (ask before running it).
- **Prefer a confirmed match over a guess.** When two candidates are close, ask rather than pick.
- **Surface MCP errors verbatim.** Don't swallow a failed `search_papers` / `get_paper`.

## Examples

| User says | What the skill does |
|---|---|
| "the olaparib maintenance ovarian trial" | `search_papers("olaparib maintenance ovarian cancer", k=10)` → one dominant NEJM hit → returns `pubmed:30345884` with one-line confirmation. |
| "Robson's talazoparib paper" | `search_papers("Robson talazoparib breast cancer", k=10)` → ranks by author+drug → confirms via `get_paper` → returns the id. |
| "that pembrolizumab lung study from 2018-ish" | `search_papers("pembrolizumab non-small cell lung cancer", k=12)` → 3 close candidates → presents numbered table (id \| title \| year \| journal \| why) → asks which. |
| "the CRISPR base-editing leukemia paper" | `search_papers(...)` returns nothing on-topic → "No match in the local corpus. Want me to `ingest_pubmed('CRISPR base editing leukemia')`?" |

---

*Research synthesis over a finite local corpus — not medical advice. Verify against primary sources and a treating clinician.*
