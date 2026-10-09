# Feedback and learnings

The Claude run improves from your feedback. You message the bot, the poster saves it, and the next Claude run folds it into a short list of standing guidance.

```
you ──message──▶ bot ──▶ feedback/inbox.jsonl ──▶ Claude run ──▶ learnings.md ──▶ read on every run
```

## Giving feedback

Send the bot any message in your private chat, for example:

- `Policies should be one line, not a list`
- `Skip the community page unless there's an event`
- `Dates should say the year level when it's not the whole school`

Reply to a preview or a note instead of sending a fresh message and the post's text is attached, so Claude knows which one you mean. The bot answers "Noted (#3)". Taps are read on the next poster cycle, so it can take up to 10 minutes to acknowledge.

| Command | What it does |
|---|---|
| `/feedback text` | Same as sending the text on its own |
| `/learned` | Shows the current `learnings.md` |
| `/help` | Shows these instructions |

Only your own account counts. Messages from anyone else are ignored, because feedback ends up in Claude's instructions.

## What happens next

Step 0 of `instructions.md` runs before any email is read:

1. Claude reads `feedback/inbox.jsonl`. Entries with an `id` above `last_feedback_id` (the first line of `learnings.md`) are new.
2. Lasting guidance becomes a one-line bullet in `learnings.md`. Duplicates merge and newer feedback wins. One-off comments about a single post don't become rules.
3. Claude rewrites `learnings.md` with the new `last_feedback_id`, and mentions what it learned in its run summary.
4. The same run, and every later one, follows `learnings.md`.

Only Claude writes `learnings.md` and only the poster writes `feedback/inbox.jsonl`, so the two never collide, and nothing needs deleting.

## Limits

Learnings tune **style and coverage**. They cannot loosen the privacy rule, the audience rules, the link allowlist or the file format. Claude is told to ignore feedback that tries, and the poster's guardrails in code check every post regardless.

## Editing what it has learned

`learnings.md` is plain text and stays on your computer. Delete or reword any bullet and the next run follows the new version. Keep the first line (`<!-- last_feedback_id: N -->`) so old feedback isn't read again. Both `learnings.md` and `feedback/` are git-ignored.
