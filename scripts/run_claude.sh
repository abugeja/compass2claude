#!/usr/bin/env bash
# macOS/Linux version of run_claude.ps1.
set -euo pipefail
cd "$(dirname "$0")/.."
read -r -d '' PROMPT <<'P' || true
Compass school email digest.

This folder is the working directory. Read instructions.md, school.md,
learnings.md (if present), manifest.json and feedback/inbox.jsonl (if
present), and follow instructions.md exactly. It is the single source of
truth. Write output files straight into outbox/ and update learnings.md as
instructions.md says. Do not post to Telegram or touch the calendar; the
local poster does that.
P
claude -p "$PROMPT" --chrome --permission-mode acceptEdits \
  --allowedTools "Read" "Write" "Edit" "Glob" "Grep" "mcp__claude_ai_Gmail" "mcp__claude-in-chrome" \
  2>&1 | tee -a claude_run.log
