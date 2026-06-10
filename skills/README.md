# medground skills — the `/doc` profile

A set of **[Claude Code](https://docs.claude.com/en/docs/claude-code) skills** that turn medground
into ready-made slash commands. They're optional — plain-English questions already work once the
[MCP server is connected](../HOWTOUSE.md) — but the skills make the grounded *retrieve → cite →
check* flow first-class, consistent, and discoverable.

Every skill enforces the same rule: **no clinical claim ships without a real, retrievable,
gate-approved citation.** Research synthesis, not medical advice.

## Install

From the repo root:

```bash
./install-skills.sh
```

That copies these folders into `~/.claude/skills/`. Restart Claude Code, then type `/doc`. To
install somewhere else: `CLAUDE_SKILLS_DIR=/path ./install-skills.sh`. (To remove them later, delete
the `doc*` folders from `~/.claude/skills/`.)

> Skills are a **Claude Code** feature. For Claude Desktop or other MCP clients, the server still
> drives the grounded workflow automatically — you just don't get the slash commands.

## Start here

| Skill | Use it when… |
|---|---|
| **`/doc`** | You're not sure which to use — describe the task and it routes for you. |
| **`/doc-help`** | You want the full menu and a few example prompts. |
| **`/doc-evidence`** | You have a clear clinical/research question and want a sourced, gate-checked answer. |
| **`/doc-triage`** | The question is vague — "what should I read", "state of the field". |

## The full suite

| Skill | What it does |
|---|---|
| `/doc` | Router — reads your intent and dispatches to the right skill below. |
| `/doc-triage` | Vague/exploratory questions → a ranked, grounded reading list. |
| `/doc-evidence` | The core workflow: a sourced, gate-checked answer to a clinical/research question. |
| `/doc-case` | Turn a patient case into a reusable context that steers every later search. |
| `/doc-treatment-map` | Rank treatment **pathways** for a case by grounded outcome (strategy/sequence, never dosing). |
| `/doc-biomarker-match` | A gene/variant → curated CIViC therapy matches, each with an A–E evidence level. |
| `/doc-grounding-check` | Fact-check an external draft, claim, or treatment plan against the corpus. |
| `/doc-paper-appraisal` | Grade one paper — study design, risk of bias, GRADE. |
| `/doc-gems` | Surface uncommon, novel, or contrarian papers. |
| `/doc-landscape` | Map a topic via the MeSH knowledge graph — clusters, hubs, key papers. |
| `/doc-synthesis` | Couple findings into a grounded combination hypothesis. |
| `/doc-contradictions` | Surface where the corpus disagrees with itself. |
| `/doc-find-paper` | Resolve a fuzzy paper reference → canonical `paper_id`. |
| `/doc-find-concept` | Resolve a fuzzy term (gene/drug/disease) → canonical `mesh:` concept. |
| `/doc-watch` | Standing literature watches + fresh PubMed ingestion (write-capable; confirms first). |

See [`../EXAMPLES.md`](../EXAMPLES.md) for copy-paste prompts that show each of these in action.
