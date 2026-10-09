# Outbox file format (schema 2)

The Claude run writes one JSON file per email to `outbox/<YYYYMMDD>-<gmail message id>.json`. Files without `"schema": 2` are moved to `hold/` untouched.

```json
{
  "schema": 2,
  "source_id": "18f0c2a1b3d4e5f6",
  "kind": "reminder",
  "subject": "Event Reminder: Aquarium Excursion",
  "re_line": "Re: Event - Aquarium Excursion at Example Primary School",
  "from": "Example Primary",
  "received": "2026-10-05T09:14",
  "audience": "prep",
  "actions": [
    {"text": "Pay and give consent for the excursion on Compass",
     "due": "2026-10-16T23:59", "level": "required"}
  ],
  "dates": [
    {"title": "Aquarium excursion", "start": "2026-10-21", "end": null,
     "all_day": true, "kind": "attend", "location": null, "note": null}
  ],
  "sections": [
    {"heading": "FYI", "fyi": ["Bring a packed lunch"]}
  ],
  "public_link": null,
  "private": {"actions": [], "dates": [], "note": null}
}
```

| Field | Type | Notes |
|---|---|---|
| `schema` | `2` | Required |
| `source_id` | string | Gmail message id. Used to skip repeats |
| `kind` | `newsletter`, `news`, `message`, `reminder` | Drives `auto_post_kinds` |
| `subject` | string | Shown as the post header |
| `re_line` | string or null | The email's `Re: …` line. Checked for student names and codes |
| `from` | string | Shown in the header |
| `received` | `YYYY-MM-DDTHH:MM` | Local time |
| `audience` | `school`, `prep`, `class`, `family`, `other` | Only those in `config.ini` reach the channel |
| `actions[]` | object | `text`, `due` (date or datetime, or null), `level` (`required` or `optional`) |
| `dates[]` | object | `title`, `start`, `end`, `all_day`, `kind` (`no_school`, `routine_change`, `attend`, `optional`), `location`, `note` |
| `sections[]` | object | `heading`, `fyi` (list of one-line strings) |
| `public_link` | string or null | Must be on the link allowlist |
| `private` | object or null | `actions`, `dates`, `note`. Only ever sent to the owner |

Calendar events are built from `dates`, `private.dates`, and a "Due:" all-day event for each required action with a `due` date.

## manifest.json

Written by the poster. Per email (`processed[<source_id>]`):

- `group_status` is one of `posted`, `awaiting_approval`, `skipped` or `withheld`.
- `withheld_reason` explains a `withheld` status.
- `group_text` is the exact text rendered for the channel.
- `subject`, `kind`, `audience`, `received` and `processed` are also recorded.

Top level:

- `group_keys` lists actions and dates already posted, used to drop repeats.
- `events_queue` holds events waiting to be added.
- `events_added` holds events already in the calendar.
