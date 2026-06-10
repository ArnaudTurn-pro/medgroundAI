---
name: doc-help
description: Discoverability skill — show the user what the `doc` literature-intelligence system can do. Invoke when the user asks "what can doc do", "/doc help", "doc help", "how do I use doc", "list doc skills", "show me the doc menu", "what are my options", or otherwise wants to explore the surface of the doc profile.
---

# doc-help

Print a concise, one-screen catalog of the `doc` profile so the user can pick a flow. Keep it
scannable — group the 16 skills, name the 7 agents, sketch the MCP tool groups, and finish with
example prompts.

## Mission (one line)

**`doc` is a grounded Graph-RAG literature-intelligence desk over a local cancer-research corpus
(papers + a MeSH knowledge graph + a curated CIViC biomarker-evidence layer).** It answers
clinical/research questions with sourced, fact-checked claims — and it **never hallucinates a
clinical claim**: every assertion is grounded in a retrievable paper and passes a deterministic
grounding gate before it ships.

## The grounding contract (in brief)

Retrieve before you reason → write the answer as discrete claims, each citing only retrieved
`paper_id`s → run `check_grounding` as a deterministic gate and repair every violation until
`grounded=true`. Every line is labelled `[GROUNDED]` (corpus-cited) · `[INFERENCE]` (flagged
reasoning over grounded facts) · `[GAP]` (not in corpus). If the corpus can't support a claim, the
desk says so and offers to ingest fresh papers — it does **not** backfill from general knowledge.

## Output template

```
doc — grounded oncology-literature intelligence (local corpus — run corpus_stats for live counts)

Orchestration:
  /doc                 — router for any literature question; routes to a specialist or runs inline
  /doc-triage          — vague/exec research questions: "what does the literature say / where's the
                         evidence / what should I read / state of play" → multi-facet scan + ranked
                         reading list, prescriptive
  /doc-help            — this menu

Resolvers (fuzzy ref → canonical id):
  /doc-find-paper <hint>     — title fragment / author / year / topic → paper_id (pubmed:NNNN)
  /doc-find-concept <term>   — gene / drug / disease / pathway → mesh: concept id

Patient context:
  /doc-case            — capture diagnosis / histology / stage / biomarkers / prior therapy →
                         reusable Patient Context Block that steers downstream queries (steering, NOT advice)
  /doc-biomarker-match — a gene/variant → curated CIViC therapy matches, each with an A–E evidence
                         level (sensitivity / resistance); civic:eid grounded (decision-support, NOT advice)
  /doc-treatment-map   — rank the candidate treatment pathways / sequences for a patient by grounded
                         outcome + CIViC level; decision tree on gating unknowns (decision-support, NOT a prescription)

Evidence & verification:
  /doc-evidence        — core grounded synthesis for a clinical/research question
                         (summarize_evidence → claims → check_grounding → answer)
  /doc-grounding-check — audit an external draft / claim / treatment plan against the corpus
                         (evaluate_plan + check_grounding): "is this statement supported?"

Quality:
  /doc-paper-appraisal — critical appraisal of one paper: design, evidence level, risk of bias, GRADE

Discovery:
  /doc-gems            — uncommon / high-novelty / bridging / contrarian / first-of-kind papers
  /doc-landscape       — map a topic via the MeSH graph: concept clusters, hubs, key papers, trend

Simulation:
  /doc-synthesis       — couple multiple findings into combination hypotheses, supported-vs-speculative
  /doc-contradictions  — find where the corpus disagrees; both sides cited, controversy surfaced

Freshness (write-capable):
  /doc-watch           — manage standing literature watches + pull fresh PubMed papers (ingest, gated)

Persona agents (Opus 4.8):
  🧫 orchestrator-doc            — research director: decompose, route, synthesize, enforce grounding
  📄 evidence-synthesist        — grounded evidence packs; lives by the grounding gate
  🔬 paper-appraiser            — critical appraisal, study design, risk of bias, biostatistics, GRADE
  💎 gem-scout                  — novelty hunter: uncommon, bridging, contrarian papers
  🧪 hypothesis-simulator       — couples findings → combination hypotheses, stress-tested + grounded
  🕸️ knowledge-graph-navigator  — MeSH graph traversal, landscape mapping, concept resolution
  ✅ grounding-auditor          — enforces the deterministic grounding gate; fact-checks every claim

medground MCP tools:
  Retrieval        search_papers · summarize_evidence · evaluate_plan
  Biomarker (CIViC) match_therapies · variant_evidence   (A–E leveled; civic:eid passes the gate)
  Grounding gate   check_grounding   (deterministic — the enforcement primitive)
  Expand context   get_paper · get_paper_chunks
  MeSH graph       find_concepts · graph_neighbors · concept_papers
  Corpus/freshness corpus_stats · ingest_pubmed
  Watches          add_watch · list_watches · remove_watch · run_watch

Try:
  "What's the evidence for PARP inhibitor maintenance in BRCA-mutated ovarian cancer?"
  "Map the research landscape around KRAS G12C inhibitors"
  "Is this claim supported by the literature: 'osimertinib improves OS in EGFR-mutant NSCLC'?"
  "What therapies are matched to BRAF V600E, and at what evidence level?"
  "Find the uncommon / contrarian papers on immunotherapy in MSS colorectal cancer"
  "Watch the literature for new antibody-drug conjugate papers in breast cancer"
```

## When to use

- User asks "what can doc do?", "/doc help", "doc help", "how do I use doc", "list doc skills",
  "show me the menu", or types `/doc` with no inline question.
- After a skill can't help (out-of-scope or empty corpus) — show what else is available.

## Behavioural rules

- Keep it to one screen. Don't dump every tool signature — group and summarize.
- If the user asks about ONE area ("just the evidence skills", "how do I find papers"), narrow the
  output to that group.
- Always finish with 3-5 copy-paste-modify example prompts.
- Reinforce the framing: grounded, finite corpus, not medical advice.

---

*Research synthesis over a finite local corpus — not medical advice. Verify against primary sources
and a treating clinician.*
