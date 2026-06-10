# Examples — what you can do with medground

Copy-paste prompts to try once medground is [connected to Claude](HOWTOUSE.md). Each one shows the
plain-English version (always works) and, where useful, the `/doc` **skill** version (if you ran
`./install-skills.sh`). They assume you've imported some evidence — at minimum
`uv run medground ingest civic`, ideally a few PubMed searches too.

> **How to read the answers.** medground labels every line: **`[GROUNDED]`** = a fact with a real
> `paper_id` · **`[INFERENCE]`** = Claude reasoning over those facts · **`[GAP]`** = not in the
> corpus. It reports a **`grounded_ratio`** (1.00 = every fact is sourced). If it can't support a
> claim, it says *"not found in the corpus"* and offers to fetch papers — **it does not bluff.**
> Research synthesis, **not medical advice.**

---

## First 5 minutes

Paste these one at a time:

1. **Check the connection**
   > Using medground, how many papers and CIViC items are in the corpus?

   *(Claude calls `corpus_stats` and reports the counts. If you see numbers, you're live.)*

2. **Ask a real question**
   > Using medground, what's the evidence for PARP inhibitors in BRCA-mutated ovarian cancer? Cite everything and give me the grounded_ratio.

3. **Look up a biomarker**
   > Using medground, what therapies are actionable for BRAF V600E in melanoma, with evidence levels?

That's the whole loop: ask → grounded, cited answer → drill in with the `paper_id`.

---

## Ask a grounded question  ·  `/doc-evidence`

> What's the evidence for osimertinib in first-line EGFR-mutant NSCLC?

> Is pembrolizumab effective first-line in PD-L1-high non-small-cell lung cancer? Sourced, with a grounded_ratio.

> `/doc-evidence` second-line options after osimertinib resistance in EGFR-mutant lung cancer

**You get:** an answer organized by facet (efficacy / safety / biomarkers), every fact tagged and
carrying a `paper_id`, gaps named out loud, and a citations table.

---

## Biomarker → therapy (curated CIViC, A–E levels)  ·  `/doc-biomarker-match`

> What's actionable for EGFR L858R in lung cancer? Show the evidence level for each therapy.

> `/doc-biomarker-match` KRAS G12C in non-small-cell lung cancer

> Is the ERBB2 (HER2) amplification targetable? What does CIViC say, and how strong is it?

**You get:** therapies tagged *sensitivity* / *resistance*, each with a CIViC **Level A–E** and a
`civic:eid…` source that passes the grounding check. Level A (validated) is visibly different from
Level D (preclinical) — the antidote to hype.

---

## Research a patient case  ·  `/doc-case` → `/doc-treatment-map`

> `/doc-case` 64-year-old, stage IV lung adenocarcinoma, EGFR exon 19 deletion, never-smoker, progressed on osimertinib. What should I look into?

Then, in the same thread:

> `/doc-treatment-map` rank the next-line strategies for this case by grounded outcome

**You get:** a reusable "patient context" that steers every later search, then treatment
**pathways ranked by the evidence** — strategy and sequence only, **never dosing**, and it defers to
the tumor board.

> ⚠️ **De-identify first.** This is a *literature* tool — never paste a real name, MRN, or
> identifiable detail. See [`SAFETY.md`](SAFETY.md).

---

## Find, then judge, a paper  ·  `/doc-find-paper` · `/doc-paper-appraisal`

> Find that FLAURA osimertinib first-line trial.

> `/doc-paper-appraisal` how good is pubmed:31751012? Study design, risk of bias, GRADE.

**You get:** the canonical `paper_id`, then a quality scorecard — so "it's cited" never gets
confused with "it's trustworthy."

---

## Map a field & find the surprises  ·  `/doc-landscape` · `/doc-gems`

> `/doc-landscape` map the research landscape around tumor mutational burden as a biomarker

> `/doc-gems` find uncommon or contrarian papers on immunotherapy in hepatocellular carcinoma

**You get:** for landscape, the concept clusters and hub/bridge papers from the MeSH graph; for
gems, the underappreciated, novel, or against-the-grain findings most summaries skip.

---

## Generate a hypothesis  ·  `/doc-synthesis`

> `/doc-synthesis` could a PARP inhibitor and an anti-PD-1 be combined in ovarian cancer? Give a grounded rationale and flag what's speculative.

**You get:** a combination hypothesis built by coupling grounded findings, with the supported parts
and the speculative leaps clearly separated.

---

## Stress-test claims  ·  `/doc-contradictions` · `/doc-grounding-check`

> `/doc-contradictions` is the benefit of adjuvant immunotherapy in resected NSCLC settled or contested?

> `/doc-grounding-check` fact-check this draft against the corpus:
> "Atezolizumab is standard first-line for all EGFR-mutant NSCLC (PMID 99999999)."

**You get:** for contradictions, both sides cited rather than smoothed over; for grounding-check, a
per-claim verdict that catches unsupported statements and fake/mismatched citations (that PMID is
invented — the check will flag it).

---

## Stay current  ·  `/doc-watch`

> `/doc-watch` add a watch for "HER2-low breast cancer trastuzumab deruxtecan", check it daily

> Using medground, ingest recent PubMed papers on antibody-drug conjugates in bladder cancer.

**You get:** standing literature watches that pull only what's new, and on-demand fresh imports.
*(These write to the corpus and cost a little to embed — Claude confirms before running.)*

---

## When it's not in the corpus

> Using medground, what's the evidence for a drug you almost certainly haven't imported yet?

**You get:** *"not found in the local corpus"* — plus an offer to `ingest_pubmed`. That honesty is
the point: absence in this finite corpus is **not** absence in the literature, and medground won't
fill the gap with unsourced guesses.

---

## Housekeeping (the CLI, not Claude)

```bash
uv run medground stats                                   # what's imported
uv run medground ingest pubmed -q "BRCA1 olaparib ovarian" -n 50
uv run medground ingest civic                            # ~11k curated biomarker items
uv run medground search "PARP inhibitor resistance" -k 8 # quick hybrid search
```

---

*medground — grounded research synthesis over a finite local corpus. **Not medical advice.** Always
defer clinical decisions to the treating team. See [`SAFETY.md`](SAFETY.md).*
