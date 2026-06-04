# Safety & Responsible Use

**medground is a research and decision-*support* tool, not medical advice.** It tells you
what the published literature *says*, grounded in citations. It is **not clinically validated**,
has **no regulatory clearance**, and must **never** be the thing that makes a clinical decision.

Read this before you use it, deploy it, or build on it. If you only read one line:

> A grounded citation means the source is *real and retrievable* — **not** that it is correct,
> high-quality, current, or relevant to your patient. That judgment is still a human's.

---

## Intended use

- Literature retrieval and synthesis for researchers, clinicians, informaticists, and students.
- Hypothesis generation and evidence mapping over a finite, citable corpus.
- Biomarker → therapy *evidence lookup* (curated CIViC predictive evidence with A–E levels).
- As a **reference implementation** of grounded medical RAG — a hallucination-resistant evidence
  server that others can study, audit, and build on.

## Out-of-scope — do **not** use this for

- Direct patient care: diagnosis, dosing, treatment orders, or any clinical action.
- A substitute for a qualified clinician, pharmacist, or multidisciplinary tumor board (RCP).
- Autonomous or closed-loop clinical decision-making.
- Emergency, population-scale, or point-of-care use without expert review in the loop.
- Anything where a wrong or missing answer could reach a patient unchecked.

Outputs are a **starting point for expert review, never an endpoint.**

---

## What "grounded" guarantees — and what it does **not**

The grounding gate (`check_grounding`) is **deliberately narrow and deterministic** (no LLM, no
network). It classifies each drafted claim:

| Status | Meaning |
|---|---|
| `grounded` | Cites ≥1 `paper_id` that exists in the corpus (and, if an envelope was given, was retrieved for this question). |
| `uncited` | No citation at all. |
| `phantom_citation` | Cites an id not in the corpus — fabricated or mistyped. |
| `off_envelope` | Cites a real corpus paper that wasn't retrieved for this question. |

It **guarantees provenance is real and reachable.** It does **not**:

- **Verify entailment** — whether the cited paper actually *supports* the claim. A claim can be
  `grounded` and still misrepresent its source. That judgment is the LLM's, and the LLM can be wrong.
- **Assess quality** — a grounded citation may be a small, preclinical, retracted, industry-funded,
  or already-contradicted paper. Use `/doc-paper-appraisal` and primary sources to weigh it.
- **Enforce itself** — enforcement is **contractual, not sandboxed.** The LLM is the MCP *client*;
  the server cannot intercept the model's output. A client that ignores the retrieve → cite → gate →
  repair loop can present ungrounded text. The gate is a **tool the model must choose to use**, not a
  barrier it is physically forced through. Treat any output that didn't pass the gate as ungrounded.

---

## Corpus limitations

- **Finite and partial.** The corpus is a curated slice (order ~10⁴ documents), not all of PubMed
  (~37M). **Absence of evidence in the corpus is not evidence of absence in the literature.**
- **Only as fresh as the last ingest.** Standard of care changes faster than any static corpus.
  Always cross-check current guidelines (e.g. NCCN) and recent literature.
- **Abstract-level, source-limited.** Today: PubMed abstracts + CIViC. No full text; no
  ClinicalTrials.gov / EuropePMC / preprints yet. In-text detail beyond the abstract is not retrieved.
- **Inherited bias.** English-language bias, indexing/MeSH-tagging bias, and publication bias all
  carry through retrieval.

---

## Data & privacy

- **Do not enter patient-identifiable information (PHI/PII).** This tool answers *literature*
  questions. De-identify any case detail before using it to steer a search.
- **Local-first, but queries may leave the machine.** Stored data lives in local files
  (DuckDB / KuzuDB) with no telemetry. However, with the default embedding provider, your **search
  queries and ingested text are sent to your configured embedding API** (OpenAI / Voyage). If queries
  must never leave the machine, use the local provider: `MG_EMBED_PROVIDER=fastembed` (offline).
- You are responsible for compliance with your institution's data-handling and any applicable
  regulation (GDPR, HIPAA, etc.) in how you use it.

---

## Known limitations (honest gaps)

- Grounding is **paper-level**, not chunk-level or entailment-level (planned).
- **No evaluation harness yet** — retrieval and grounding quality are not formally measured.
- The MeSH graph is **co-occurrence, not causal or typed** relations (no "treats"/"inhibits").
- **Single-user, single-writer** — not a hardened multi-tenant service.

---

## If you build on this

- **Preserve the grounding loop.** Stripping the gate, or presenting model output that bypassed it,
  defeats the entire purpose of the project. The discipline is the contribution.
- Keep the disclaimers and this file with any derivative.
- Do not represent outputs as medical advice, clinical validation, or a regulated medical device.

---

## Reporting

- **Bugs and safety concerns:** open an issue at
  <https://github.com/ArnaudTurn-pro/medground/issues>.
- **Security-sensitive reports:** please use a private GitHub security advisory on the repository
  rather than a public issue.

---

## No warranty

The software is provided **"as is", without warranty of any kind**, as set out in the project
`LICENSE`. The authors and contributors accept **no liability** for any clinical, research, or other
decision made using this software. Always defer clinical decisions to the treating team.
