# Runs the Claude reading step from the terminal with Claude Code, instead of
# a Claude scheduled task. Run it from the repo folder, or schedule it.
# Needs: Claude Code installed and signed in (claude --version), the Gmail
# connector enabled on your Claude account, and Chrome with Claude in Chrome.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $here

$prompt = @"
Compass school email digest.

This folder is the working directory. Read instructions.md, school.md,
learnings.md (if present), manifest.json and feedback/inbox.jsonl (if
present), and follow instructions.md exactly. It is the single source of
truth. Write output files straight into outbox/ and update learnings.md as
instructions.md says. Do not post to Telegram or touch the calendar; the
local poster does that.
"@

claude -p $prompt `
  --chrome `
  --permission-mode acceptEdits `
  --allowedTools "Read" "Write" "Edit" "Glob" "Grep" "mcp__claude_ai_Gmail" "mcp__claude-in-chrome" `
  2>&1 | Tee-Object -FilePath (Join-Path $here "claude_run.log") -Append
