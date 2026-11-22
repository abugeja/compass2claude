# Scheduled task prompt

The Claude scheduled task does the reading step: it reads the school emails and writes one file per email to `outbox/`. Use the prompt for your setup ([laptop or cloud](../README.md#two-ways-to-run-it)).

## Cloud setup

The task runs on Anthropic's side, so no computer has to be on. It works in a clone of your private data repo, and pushing its outbox files starts the poster workflow. Replace `<OWNER>/<DATA-REPO>` with your repo.

```
Compass school email digest.

You are in a checkout of the private GitHub repo <OWNER>/<DATA-REPO>.
Read instructions.md and school.md in it and follow instructions.md
exactly. It is the single source of truth, including its git steps:
git pull --rebase origin main before you start, and commit and push your
outbox files to main when you finish, so the poster workflow runs. Also
read manifest.json, learnings.md (if present) and feedback/inbox.jsonl
(if present).

Gmail is available as a connector. There is no browser: for "News:" items
follow the cloud setup rules in instructions.md step 3. Never write Compass
links into any output. Do not post to Telegram or create calendar events
yourself, and never commit config.ini or private_terms.txt.

If you can read the repo but cannot push to main, push to a branch named
claude/digest-<YYYYMMDDHHMM> instead and say so clearly in your final
reply, because nothing reaches the channel until that is fixed.

If instructions.md or the repo can't be reached, stop and say so in one line.
```

| Setting | Value |
|---|---|
| Repository | Your private data repo, with permission to push to `main` |
| Environment variables | `BROWSER_FETCH_URL`, `BROWSER_FETCH_KEY` ([fetch-worker.md](fetch-worker.md)) |
| Network | Allow `*.workers.dev` if the environment limits outbound access |
| Schedule | For example, weekdays hourly, 8am to 6pm |
| Connectors | Gmail (read). Others can be off |

## Laptop setup

The task runs through the Claude desktop app on your computer, using its Chrome (signed in to Compass) and this folder. Replace `<FOLDER>` with the absolute path of your clone, for example `C:\Users\you\Projects\compass2claude`.

```
Compass school email digest.

Read <FOLDER>\instructions.md and <FOLDER>\school.md on this computer
(stage them with the remote-devices file tools) and follow instructions.md
exactly, using its laptop setup rules. It is the single source of truth for
this task. Also read manifest.json, learnings.md (if present) and
feedback\inbox.jsonl (if present), and list outbox\ and hold\ in that
folder as it describes.

Write your output files into <FOLDER>\outbox\ on the computer (write each
file in the workspace, send it with SendUserFile, then device_commit_files
to the outbox path). If instructions.md tells you to update learnings.md,
write it the same way, to <FOLDER>\learnings.md. Do not post to Telegram or
create calendar events yourself; the local script does that.

If instructions.md, school.md or the folder can't be reached, stop and say
so in one line.
```

| Setting | Value |
|---|---|
| Requires this computer | Yes. The run needs Chrome (signed in to Compass) and the folder |
| Folder | Your clone of this repo |
| Schedule | For example, weekdays every two hours, 8am to 6pm |
| Approval mode | Automatic. Runs are unattended, and the run is read-only everywhere except `outbox/` |
| Connectors | Gmail (read). Others can be off |

## Posting on request

In a Claude session opened in the folder, say what you want, for example "re-post the excursion item" or "post to the channel: the sausage sizzle has moved to Friday". Claude writes an outbox file with `"force": true` (the `/post-to-channel` command does the same), and you get the usual Post / Skip preview in Telegram. `force` skips only the "already posted" checks. Every guardrail still applies.

## Changing behaviour

Edit `instructions.md` and `school.md`, not the prompt. The next run picks up the change.
