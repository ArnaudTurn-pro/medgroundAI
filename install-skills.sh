#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Install the medground "/doc" skills into Claude Code.
#
#   ./install-skills.sh
#
# Copies the skills from ./skills into Claude Code's skills directory
# (~/.claude/skills by default). Safe to re-run — it overwrites in place.
# Override the destination with:  CLAUDE_SKILLS_DIR=/path ./install-skills.sh
# ---------------------------------------------------------------------------
set -euo pipefail

SRC="$(cd "$(dirname "$0")/skills" 2>/dev/null && pwd || true)"
DEST="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"

if [ -z "$SRC" ] || [ ! -d "$SRC" ]; then
  echo "Could not find a skills/ folder next to this script." >&2
  echo "Run it from inside the medground repo." >&2
  exit 1
fi

mkdir -p "$DEST"

count=0
for d in "$SRC"/doc "$SRC"/doc-*; do
  [ -d "$d" ] || continue
  cp -R "$d" "$DEST/"
  echo "  installed  $(basename "$d")"
  count=$((count + 1))
done

echo ""
echo "Installed $count skills into:  $DEST"
echo "Restart Claude Code, then type  /doc  (or /doc-help) to begin."
