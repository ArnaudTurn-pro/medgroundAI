# Contributing to MedGround

Thanks for your interest. MedGround is a grounded medical-literature engine, and its whole reason to
exist is that **it does not state a clinical claim without a real, retrievable citation.** Please keep
that property intact in everything you change.

## The one rule that matters

**Preserve the grounding loop.** Any path that produces a clinical or biological claim must go through
*retrieve → cite → `check_grounding` → repair*. Don't add a shortcut that lets unsourced text reach the
user, and don't weaken the deterministic gate ([`src/medground/retrieve/grounding.py`](src/medground/retrieve/grounding.py)).
If you touch retrieval or the gate, add a test proving a fabricated or uncited claim is still caught.

## Dev setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
git clone <your-fork> medground && cd medground
uv sync
uv run pytest          # tests are offline and use isolated temp dirs
uv run ruff check .    # lint (line length 100)
```

If a store test collides with a running MCP server's file lock, use a throwaway data dir:
`MG_DATA_DIR="$(mktemp -d)" uv run pytest`.

## Making a change

1. Branch off `main`.
2. Keep it focused and match the surrounding style. The repo lints with `ruff check` (it does **not**
   enforce `ruff format`).
3. Add or update tests — especially around retrieval and grounding.
4. Run `uv run ruff check .` and `uv run pytest` before opening a PR.
5. Record non-obvious design decisions as an ADR in [`docs/decisions/`](docs/decisions/) (Nygard format —
   follow the existing ones).

## Scope & honesty

- This is **research / decision-support tooling, not medical advice** (see [SAFETY.md](SAFETY.md)).
  Don't add features that present output as clinical guidance, dosing, or a medical device.
- **Never commit secrets or patient data.** `.env` is gitignored; `docs/cases/` is gitignored because it
  may contain PHI. Don't add either to the repo.
- The repo ships the **pipeline, not a corpus.** Don't commit large data files or third-party article text.

## Security

Found a vulnerability? See [SECURITY.md](SECURITY.md) — please report it privately, not in a public issue.
