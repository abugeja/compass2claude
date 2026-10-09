# compass2claude

Turns Compass school emails into short summaries for a parents' Telegram channel. You do the reading step. `compass_post.py` does everything that touches Telegram or the calendar.

## When asked to run the digest

Follow `instructions.md` exactly. It is the single source of truth for the run. Read `school.md`, `learnings.md` (if present), `manifest.json` and `feedback/inbox.jsonl` (if present) first. Write output only to `outbox/` and `learnings.md`.

The `/run-digest` command does this in one step.

## Hard rules

- The channel is seen by other parents. Nothing about any individual child or family may go in the group fields of an outbox file. When in doubt, the email is `family`.
- Never post to Telegram, run `compass_post.py` against the live channel, or add calendar events from a digest run. The poster does that on its own schedule.
- Read only in Gmail and Compass: no replies, labels, archiving, payments, RSVPs or consents.
- Never open or copy Compass links into output. They carry a personal login token.
- Never commit `config.ini`, `school.md`, `private_terms.txt`, `learnings.md`, `feedback/`, `manifest.json`, `state.json` or logs. `config.ini` holds the bot token.

## When asked to change the code

- Layout and guardrails live in `compass_post.py`. Behaviour of the reading step lives in `instructions.md`. Prefer editing those over the scheduled task's prompt.
- Run the tests before finishing: `python -m unittest discover -s tests -v`. They're offline.
- Keep personal names (family, child, school-specific) out of anything committed. Use the `*.example.*` templates for examples.
- Docs are in `docs/`. Update them when behaviour changes.
