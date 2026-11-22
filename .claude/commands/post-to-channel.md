---
description: Post something to the parents' channel, or re-post a missed item
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
---
The owner wants this posted to the Telegram channel, or an item that didn't go out re-posted: $ARGUMENTS

Read `instructions.md`, especially "The privacy rule", "Posting on request" and "Publishing". Follow "Posting on request" exactly:

1. `git pull --rebase origin main`.
2. Work out what is wanted. For a re-post, find the item in `manifest.json` and `sent/` and copy its file. For new text, write a manual file. Ask only if what to post or the audience is unclear.
3. Write the outbox file with `"force": true`, push it, wait about two minutes, pull, and report the item's `group_status` and any `withheld_reason` from `manifest.json`.

Don't run `compass_post.py` or post to Telegram yourself. The poster does that from the outbox file, and the owner taps Post on the preview in Telegram.
