# Running the Claude step from the terminal

Instead of a Claude scheduled task, you can run the reading step with Claude Code from a terminal. The poster doesn't change: it still picks files up from `outbox/`.

## Requirements

- Claude Code installed and signed in to the same Claude account (`claude --version`).
- The Gmail connector enabled on that account, and Claude in Chrome installed, with Chrome signed in to Compass.
- This folder as the working directory, so `instructions.md`, `school.md`, `learnings.md` and `outbox/` are local files.

## Run it

```powershell
.\scripts\run_claude.ps1        # Windows
./scripts/run_claude.sh         # macOS or Linux
```

Output is appended to `claude_run.log` (git-ignored).

## Schedule it

Windows, weekdays every two hours from 8am to 6pm:

```powershell
$here = (Get-Location).Path
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$here\scripts\run_claude.ps1`"" -WorkingDirectory $here
$triggers = 8,10,12,14,16,18 | ForEach-Object { New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At ("{0}:00" -f $_) }
Register-ScheduledTask -TaskName "compass2claude reader" -Action $action -Trigger $triggers
```

If you switch to this, pause the Claude scheduled task so the two don't both run.

## Notes

- The tool names in `--allowedTools` (`mcp__claude_ai_Gmail`, `mcp__claude-in-chrome`) depend on how your connectors are named. Run `claude mcp list` and adjust the script if they differ.
- Chrome must be open for `--chrome`. The computer has to be awake and signed in when the schedule fires.
- Unattended runs only get the tools in `--allowedTools`. If a run stalls on a permission prompt, add the missing tool there.
