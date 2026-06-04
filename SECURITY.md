# Security Policy

## Reporting a vulnerability

Please report security vulnerabilities **privately — do not open a public issue.**

- Preferred: open a [private security advisory](https://github.com/ArnaudTurn-pro/medground/security/advisories/new)
  on this repository.
- Include a description, reproduction steps, the affected version/commit, and the impact.

We'll acknowledge as soon as we can and keep you posted on a fix.

## What's in scope

MedGround is an early-stage research tool that runs **locally** as an MCP server over your own data.
The most relevant concerns:

- **Secret handling** — API keys live in a gitignored `.env`. Report any path that logs or leaks
  `OPENAI_API_KEY` / `VOYAGE_API_KEY` / NCBI keys.
- **Unexpected data egress** — by default, search queries and ingested text are sent to your configured
  embedding provider (OpenAI / Voyage); the local `fastembed` provider keeps everything offline. Report
  any egress of corpus or query content beyond the configured provider.
- **Untrusted input** — ingested papers and tool arguments are untrusted. Report injection or
  path-traversal issues in ingestion, the CLI, or the MCP tools.
- **Privacy** — `docs/cases/` is gitignored because it may hold PHI. Report anything that could cause
  patient data to be committed or transmitted.

## Not in scope

- Clinical correctness of the retrieved literature — that's a usage/safety matter (see
  [SAFETY.md](SAFETY.md)); MedGround is **not medical advice**.
- Issues that require committing secrets or PHI to reproduce.

## Supported versions

MedGround is pre-1.0 (`0.0.x`); only the latest `main` is supported. Pin a commit if you need stability.
