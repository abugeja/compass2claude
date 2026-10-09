# Privacy design

The channel is read by other parents. One leaked line about a child can't be taken back, so the design assumes any single safeguard can fail, and it layers independent ones. The language model is treated as helpful but fallible. The final decisions are made in code you can read and test.

## What must never be posted

- Anything about an individual child or family, including your own.
- Any child's name.
- Staff or parent contact details (emails, phone numbers).
- Anything from a child's record: attendance, reports, results, behaviour, incidents, health, wellbeing, learning support, fees or debts, interview bookings.
- Compass links. These embed your personal login token and user ID.
- Wording addressed to you personally.

## The layers

| # | Layer | Where | Catches |
|---|---|---|---|
| 1 | Student `Re:` line check | `compass_post.py` (`names_a_student`) | Compass emails about one student (`Re: Firstname SURNAME (ABC1234), …`), via `re_line`, subject and post text |
| 2 | Audience allowlist | `compass_post.py`, `config.ini [group] audiences` | Family, other-year and uncertain emails |
| 3 | Separate `private` field | outbox schema, renderer | Family content can't be rendered into a channel post |
| 4 | Blocked words | `private_terms.txt` | Your names, nicknames, IDs, other children's names |
| 5 | Pattern scan | `compass_post.py` (`PATTERNS`) | Email addresses, AU phone numbers, Compass links and tokens, student codes, "your child", "Dear Mr/Mrs", personal-record topics |
| 6 | Link allowlist | `config.ini [group] link_allowlist` | Any link off approved domains |
| 7 | Owner approval | Telegram buttons, `review = true` | Anything that looks fine to a machine but not to you |
| 8 | Audit trail | `manifest.json` | After-the-fact review of every post and every withheld item |

Layers 1 to 6 run on the **final rendered text** of each post, not only on Claude's fields, so a mistake anywhere upstream is still caught.

When something is withheld, the whole email is withheld, not just the matching line. You get a private message naming the reason.

## Auto-posting

`auto_post_kinds` (default in the template: none) lets chosen kinds skip approval. A typical choice is `newsletter, reminder`. Auto-posted items still go through layers 1 to 6. Teacher messages and news items are the most likely to touch on an individual child, so keep them on review.

## Things to check from time to time

- Add new names to `private_terms.txt` as you learn them: classmates, siblings, nicknames.
- Skim `manifest.json` for `withheld_reason` entries. A pattern of near-misses suggests a rule to add to `instructions.md`.
- If you change year level or school, update `school.md` and `config.ini [school]`.

## Your own data

- `config.ini` holds your bot token and, optionally, an Apple app-specific password. It never leaves the computer and is git-ignored.
- The Claude run has read-only Gmail access and writes only to `outbox/`. It never posts, labels, deletes or submits anything.
- The poster talks only to `api.telegram.org`, and optionally `caldav.icloud.com`.
