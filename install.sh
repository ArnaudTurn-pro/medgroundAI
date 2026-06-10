#!/usr/bin/env bash
# ===========================================================================
# medground — guided setup.
#
#   ./install.sh
#
# Walks you through everything in one go: installs medground, writes your
# .env for you (no hand-editing), optionally downloads a starter corpus,
# connects it to Claude, and installs the /doc skills.
#
# Safe to re-run at any time — re-run it to change a setting.
# ===========================================================================
set -euo pipefail
cd "$(dirname "$0")"

# --- pretty output (plain text if not a terminal) --------------------------
if [ -t 1 ]; then
  B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; C=$'\033[36m'; D=$'\033[2m'; R=$'\033[0m'
else B=; G=; Y=; C=; D=; R=; fi
step() { printf "\n${B}${C}==>${R} ${B}%s${R}\n" "$*"; }
ok()   { printf "  ${G}OK${R} %s\n" "$*"; }
warn() { printf "  ${Y}!${R}  %s\n" "$*"; }
note() { printf "     ${D}%s${R}\n" "$*"; }

ask() {  # ask "Question" "default" -> prints the answer
  local p="$1" d="${2:-}" a
  if [ ! -t 0 ]; then printf '%s' "$d"; return; fi
  read -r -p "$(printf '%s ' "$p")" a || true
  printf '%s' "${a:-$d}"
}
confirm() {  # confirm "Question?" "Y"|"N" -> exit 0 if yes
  local p="$1" d="${2:-Y}" hint a
  [ "$d" = "Y" ] && hint="[Y/n]" || hint="[y/N]"
  if [ ! -t 0 ]; then [ "$d" = "Y" ]; return; fi
  read -r -p "$(printf '%s %s ' "$p" "$hint")" a || true
  a="${a:-$d}"
  case "$a" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}
# set_env KEY VALUE — write/replace a line in .env (idempotent, no sed quirks)
set_env() {
  local key="$1" val="$2" f=".env"
  touch "$f"
  if grep -qE "^${key}=" "$f" 2>/dev/null; then
    awk -v k="$key" -v v="$val" '$0 ~ "^"k"=" && !d {print k"="v; d=1; next} {print}' "$f" > "$f.tmp"
    mv "$f.tmp" "$f"
  else
    printf '%s=%s\n' "$key" "$val" >> "$f"
  fi
}

printf "\n${B}medground setup${R}  ${D}— grounded answers for medical research${R}\n"

# --- 1. prerequisites ------------------------------------------------------
step "1/6  Checking prerequisites"
if ! command -v uv >/dev/null 2>&1; then
  warn "'uv' (the Python installer medground uses) is not installed."
  note "Install it — takes about 10 seconds:"
  note "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  note "Then open a new terminal and run ./install.sh again."
  exit 1
fi
ok "uv is installed"

# --- 2. install ------------------------------------------------------------
step "2/6  Installing medground"
uv sync
ok "dependencies installed into .venv"

# --- 3. configuration (.env) ----------------------------------------------
step "3/6  Configuration"
if [ ! -f .env ]; then cp .env.example .env; ok "created .env"; else ok ".env already exists (updating)"; fi

DEFAULT_DATA="$(pwd)/data"
DATA="$(ask "Where should medground keep its data? [${DEFAULT_DATA}]" "$DEFAULT_DATA")"
set_env MG_DATA_DIR "$DATA"
ok "data folder -> $DATA"

printf "\n  How should medground understand text? (for searching the literature)\n"
note "1) OpenAI  — best quality. Needs an API key. Small cost per import."
note "2) Local   — free and offline, lower quality. Great for trying it out."
EMB="$(ask "Choose 1 or 2 [1]:" "1")"
if [ "$EMB" = "2" ]; then
  set_env MG_EMBED_PROVIDER fastembed
  set_env MG_EMBED_MODEL "BAAI/bge-small-en-v1.5"
  set_env MG_EMBED_DIM 384
  ok "using the free local model (downloads once, then offline)"
else
  set_env MG_EMBED_PROVIDER openai
  set_env MG_EMBED_MODEL text-embedding-3-large
  set_env MG_EMBED_DIM 3072
  KEY="$(ask "Paste your OpenAI API key (starts with sk-), or press Enter to add it later:" "")"
  if [ -n "$KEY" ]; then set_env OPENAI_API_KEY "$KEY"; ok "OpenAI key saved to .env"
  else warn "No key yet — add OPENAI_API_KEY to .env (or re-run this script) before importing."; fi
fi

EMAIL="$(ask "Your email for PubMed (optional, a courtesy to NCBI) [skip]:" "")"
[ -n "$EMAIL" ] && { set_env MG_NCBI_EMAIL "$EMAIL"; ok "email saved"; }

# --- 4. starter corpus -----------------------------------------------------
step "4/6  Starter corpus (optional)"
note "CIViC is a curated set of ~11k biomarker -> therapy facts (with A-E evidence levels)."
if confirm "Download it now so you have something to ask about?" "Y"; then
  if uv run medground ingest civic; then ok "CIViC evidence imported"
  else warn "Import failed — you can run it later:  uv run medground ingest civic"; fi
else
  note "Skipped. Later:  uv run medground ingest civic"
fi

# --- 5. connect to Claude --------------------------------------------------
step "5/6  Connect medground to Claude"
if command -v claude >/dev/null 2>&1; then
  if confirm "Register medground with Claude Code now?" "Y"; then
    if claude mcp add medground -- uv run --directory "$(pwd)" medground-mcp; then
      ok "registered with Claude Code"
    else
      warn "Couldn't register automatically. Run this yourself:"
      note "claude mcp add medground -- uv run --directory $(pwd) medground-mcp"
    fi
  fi
else
  warn "Claude Code CLI not found."
  note "Claude Code: run  claude mcp add medground -- uv run --directory $(pwd) medground-mcp"
  note "Claude Desktop: copy the mcpServers block from README.md into claude_desktop_config.json"
fi

# --- 6. skills -------------------------------------------------------------
step "6/6  The /doc skills (optional, recommended)"
note "Turns medground into ready-made slash commands like /doc-evidence and /doc-case."
if confirm "Install them into Claude Code now?" "Y"; then
  ./install-skills.sh
fi

# --- done ------------------------------------------------------------------
step "All set."
printf "  Open Claude Code or Claude Desktop and ask:\n"
printf "    ${B}\"Using medground, how many papers are in the corpus?\"${R}\n\n"
printf "  Next:\n"
note "Try the copy-paste prompts in  EXAMPLES.md"
note "If you installed the skills, type  /doc  (or /doc-help)"
note "Change any setting anytime by re-running  ./install.sh"
printf "\n  ${D}Research synthesis, not medical advice. See SAFETY.md.${R}\n\n"
