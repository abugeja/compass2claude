# Scheduled task prompt

Use this as the prompt for the Claude scheduled task. Replace `<FOLDER>` with the absolute path of your clone, for example `C:\Users\you\Projects\compass2claude`.

```
Compass school email digest.

Read <FOLDER>\instructions.md and <FOLDER>\school.md on this computer
(stage them with the remote-devices file tools) and follow instructions.md
exactly. It is the single source of truth for this task. Also read
manifest.json, learnings.md (if present) and feedback\inbox.jsonl (if
present), and list outbox\ and hold\ in that folder as it describes.

Write your output files into <FOLDER>\outbox\ on the computer (write each
file in the workspace, send it with SendUserFile, then device_commit_files
to the outbox path). If instructions.md tells you to update learnings.md,
write it the same way, to <FOLDER>\learnings.md. Do not post to Telegram or create calendar events
yourself; the local script does that.

If instructions.md, school.md or the folder can't be reached, stop and say
so in one line.
```

## Task settings

| Setting | Value |
|---|---|
| Requires this computer | Yes. The run needs Chrome (signed in to Compass) and the folder |
| Folder | Your clone of this repo |
| Schedule | For example, weekdays every two hours, 8am to 6pm |
| Approval mode | Automatic. Runs are unattended, and the run is read-only everywhere except `outbox/` |
| Connectors | Gmail (read). Others can be off |

## Changing behaviour

Edit `instructions.md` and `school.md`, not the prompt. The next run picks up the change.
