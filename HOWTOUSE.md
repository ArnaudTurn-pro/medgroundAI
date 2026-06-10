# How to use medground

medground is a **grounded cancer-research assistant** you talk to through Claude. Ask it a
question about the cancer literature and it answers with **real citations** — and when the answer
isn't in its corpus, it says so plainly instead of making something up.

> ⚕️ Research synthesis, **not medical advice.** It tells you what the literature *says*, with
> sources. It does not diagnose, dose, or replace a clinician or tumor board. See
> [`SAFETY.md`](SAFETY.md).

There are two ways in, in order of simplicity:

1. **Just ask Claude** (works the moment the MCP is connected — no commands to learn). ← start here
2. **The `/doc` skill profile** (optional Claude Code add-on that makes the flows first-class).

---

## 1. Connect it (one time, ~1 minute)

> **Easiest path:** from the repo folder, run **`./install.sh`**. It installs medground, writes
> your `.env` for you (no hand-editing), offers to download a starter corpus, connects it to Claude,
> and installs the skills — then tells you what to ask first. The manual steps below do the same
> thing by hand.

medground runs as an **[MCP](https://modelcontextprotocol.io) server**, so Claude uses it as a
tool. If you haven't installed it yet, do the [README Quickstart](README.md#quickstart) first
(`git clone` → `uv sync` → fill in `.env`). Then register the server:

**Claude Code:**
```bash
claude mcp add medground -- uv run --directory /absolute/path/to/medground medground-mcp
```

**Claude Desktop:** add the `mcpServers` block from the README to `claude_desktop_config.json` and
restart.

> **Want medground in several terminals / Warp panes / agents at once?** Don't register the stdio
> command in each — they'd each spawn a server and the database is single-writer. Instead run **one**
> shared server and point every client at its URL:
> ```bash
> # leave this running — it owns the DB; clients only connect to it
> uv run --directory /absolute/path/to/medground medground serve
> claude mcp add --transport http medground http://127.0.0.1:8765/mcp   # in each client
> ```
> (Drop the `uv run --directory …` prefix only if the venv is active and you're in the project dir.)

**Check it worked** — ask Claude:

> *"Using medground, how many papers are in the corpus?"*

Claude should call the `corpus_stats` tool and report the counts. If it does, you're ready.

---

## 2. Use it: just ask (this is the whole thing)

With the MCP connected, **ask your question in plain English.** You don't need to know any tool
names — Claude retrieves from the corpus and answers with citations. Try:

- *"What's the evidence for PARP inhibitors in BRCA-mutated ovarian cancer?"*
- *"Is pembrolizumab effective in MSI-high colorectal cancer?"*
- *"What's known about osimertinib resistance in EGFR-mutant lung cancer?"*
- *"What therapies are indicated for the BRAF V600E mutation in melanoma?"*
- *"Map the research landscape around KRAS G12C inhibitors."*

> **Tip:** name the **drug + cancer type + biomarker** when you can. The more specific the question,
> the sharper the retrieval.

---

## 3. What a good answer looks like

A grounded answer is organized by topic, **every line is labelled**, and **every fact carries a
paper id**. Here's the shape (illustrative):

```
# Evidence — PARP inhibitors in BRCA-mutated ovarian cancer
Grounding gate: PASSED · grounded_ratio 1.00

## TL;DR
PARP-inhibitor maintenance extends progression-free survival in BRCA-mutated,
platinum-sensitive ovarian cancer; long-term safety data are thinner.

## Efficacy
- [GROUNDED] Maintenance olaparib extended PFS vs placebo. (pubmed:30345884 —
  "Maintenance Olaparib in Newly Diagnosed Ovarian Cancer", NEJM 2018)
- [INFERENCE] The benefit likely concentrates in HRD-positive tumors — reasoning over
  the grounded biomarker data, not a direct head-to-head result.

## Safety
- [GROUNDED] Anemia and fatigue were the most common grade ≥3 events. (pubmed:…)
- [GAP] Long-term secondary-malignancy risk — not found in the corpus.

## Citations
| paper_id        | title                                            | year | journal |
|-----------------|--------------------------------------------------|------|---------|
| pubmed:30345884 | Maintenance Olaparib in Newly Diagnosed Ovarian… | 2018 | NEJM    |

*Research synthesis over a finite local corpus — not medical advice.*
```

*(Illustrative. Your real answer will cite real papers actually in the corpus.)*

### Read the labels — this is the trust model

| Label | What it means | How to treat it |
|---|---|---|
| **`[GROUNDED]`** | A fact backed by a real paper in the corpus; the `paper_id` is shown. | Evidence — open the source if it matters. |
| **`[INFERENCE]`** | Claude's reasoning *over* grounded facts, flagged as reasoning. | A hypothesis, not proof. |
| **`[GAP]`** | The corpus doesn't answer this — said out loud. | A known unknown — *not* "no". |

Two more signals to look for:

- **`paper_id`** (e.g. `pubmed:30345884`, `civic:eid12`) — the real source, and your join key.
  Paste it back to go deeper: *"summarize pubmed:30345884"* or *"appraise it."*
- **`grounded_ratio`** — the share of the answer's factual claims that passed the deterministic
  citation check. **`1.00` means every stated fact is sourced.**

> **The core promise:** if the corpus can't support an answer, medground says *"not found in the
> corpus"* and offers to fetch fresh papers — it does **not** bluff with general knowledge. A
> grounded citation means the source is **real**, not that it's high-quality or right for your
> patient; weigh it yourself.

---

## 4. What you can ask it to do

You can just describe what you want — Claude picks the right approach. Common jobs:

| You want… | Say something like… |
|---|---|
| A sourced evidence summary | *"What's the evidence for `<drug>` in `<cancer>`?"* |
| Research shaped around a patient | *"Here's a patient: 47 yo, MSI-high right-colon adenocarcinoma, prior FOLFOX — what should I look up?"* |
| Best-supported treatment **strategy/sequence** | *"Rank the treatment pathways for this patient by outcome."* (strategy, never dosing) |
| Biomarker → therapy evidence | *"What therapies are indicated for EGFR T790M?"* |
| Find a specific paper | *"Find that olaparib maintenance trial."* |
| Judge a paper's quality | *"How good is pubmed:30345884? Risk of bias?"* |
| Map a field | *"Map the landscape around tumor mutational burden."* |
| Novel / contrarian papers | *"Find the hidden gems on immunotherapy in MSS colorectal cancer."* |
| A combination hypothesis | *"Could a PARP inhibitor and an anti-PD-1 be combined? Grounded rationale."* |
| Where the literature disagrees | *"Is the benefit of `<X>` settled or contested?"* |
| Fact-check a draft you wrote | *"Is this claim supported by the literature: '…'?"* |
| Pull in new papers | *"Ingest recent PubMed papers on ADCs in breast cancer."* (writes — Claude confirms first) |

---

## 5. Power-user mode: the `/doc` skill profile (optional)

If you use **Claude Code**, install the bundled **`doc` skill profile** — one command from the repo
root, **`./install-skills.sh`** — and every flow above becomes a first-class slash command with
structured, consistent output:

```
/doc                  router — describe any literature task, it routes for you
/doc-evidence         sourced, gate-checked answer to a clinical/research question
/doc-triage           vague "what should I read / state of the field" → ranked reading list
/doc-case             turn a patient case into a reusable context that steers every search
/doc-treatment-map    rank treatment pathways for a case by grounded outcome (strategy, not dosing)
/doc-grounding-check  fact-check an external draft / treatment plan against the corpus
/doc-paper-appraisal  grade one paper (design, risk of bias, GRADE)
/doc-gems             uncommon / novel / contrarian papers
/doc-landscape        map a topic via the MeSH knowledge graph
/doc-synthesis        couple findings into a combination hypothesis
/doc-contradictions   surface where the corpus disagrees
/doc-watch            standing literature watches + fresh PubMed ingestion
/doc-help             the full menu
```

You **don't need these.** Plain-English questions work out of the box, because the MCP server tells
Claude to follow the grounded *retrieve → cite → check* loop automatically. The skills just make it
smoother and more repeatable. Full list in [`skills/README.md`](skills/README.md); copy-paste prompts
to try in [`EXAMPLES.md`](EXAMPLES.md).

---

## 6. Tips for the best results

- **Be specific.** "drug + cancer type + biomarker + line of therapy" retrieves far better than a
  one-word topic.
- **Ask for the strict format** when you want it: *"answer grounded, with citations and a
  grounded_ratio."*
- **"Not in the corpus"? Extend it.** Ask Claude to *"ingest recent PubMed papers on `<topic>`"*
  (it confirms before writing — embedding costs a little), then re-ask your question.
- **De-identify patient details.** This is a *literature* tool — never paste identifiable patient
  information. (See [`SAFETY.md`](SAFETY.md).)
- **Use the `paper_id` to drill down:** *"show me the chunks of pubmed:30345884"*, *"appraise it"*,
  *"what papers are near it in the graph?"*

---

## 7. Limits (read once)

- **Finite corpus** — a curated slice of the literature, not all of PubMed. *Absence in the corpus
  is not absence in the literature.*
- **Abstract-level**, sourced from **PubMed + CIViC** today, and only as fresh as the last ingest.
- **"Grounded" ≠ "correct for you."** It means the citation is real. Weigh study quality
  (`/doc-paper-appraisal`) and current guidelines yourself.
- **Not a medical device, not clinically validated, not medical advice.** Always defer clinical
  decisions to the treating team. Full detail in [`SAFETY.md`](SAFETY.md).

---

*medground — grounded research synthesis over a finite local corpus. **Not medical advice.***
