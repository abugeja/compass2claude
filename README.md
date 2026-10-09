# compass2claude

Turns school emails from [Compass](https://www.compass.education/) into short, structured summaries for a parents' Telegram channel, with strong guardrails so nothing about an individual child is ever posted. Dates can also go into an iCloud calendar.

Claude does the reading and summarising on a schedule. A small local Python script does everything that touches the outside world, and it enforces the privacy rules in code.

```
 Gmail (Compass emails)                               Telegram channel
        │                                                    ▲
        ▼                                                    │ approved or auto posts
 ┌───────────────────────┐  outbox/*.json  ┌───────────────────────────┐
 │ Claude scheduled task │ ──────────────▶ │ compass_post.py           │──▶ you (previews, private notes)
 │ follows instructions  │                 │ guardrails, layout,       │
 │ reads Compass in      │                 │ approvals, calendar,      │──▶ iCloud calendar (optional)
 │ Chrome                │                 │ manifest                  │
 └───────────────────────┘                 └───────────────────────────┘
```

## What a post looks like

```
━━━━━━━━━━━━━━
Event Reminder: Year Level Aquarium Excursion
Example Primary · Mon 5 Oct, 9:14am · Prep

Actions
🟡 Pay and give consent for the excursion on Compass, by Fri 16 Oct 11:59pm

Dates
🟡 Wed 21 Oct · Aquarium excursion
🔴 act now / no school  🟡 plan for it  🟢 optional
```

Each email gets a header (subject, sender, date and time, audience), then **Actions**, **Dates**, and FYI items under the newsletter's own section headings. A divider separates emails. The traffic lights are applied in code:

| | Actions | Dates |
|---|---|---|
| 🔴 | Required, due within 3 days | No school, early finish, routine change |
| 🟡 | Required, due later | Something your child attends |
| 🟢 | Optional (tickets, volunteering) | Optional or social events |

## Privacy guardrails

The channel is seen by other parents, so the design assumes Claude will sometimes get things wrong and checks independently in code. An email reaches the channel only if it passes **all** of these:

1. **Student emails are always private.** Compass marks emails about one student with a line like `Re: Firstname SURNAME (ABC1234), …`. Claude copies that line into `re_line`, and the script checks it, the subject and the post text for student names and codes. Any match keeps the email private, whatever Claude decided.
2. **Audience allowlist.** Claude tags each email `school`, `prep` (your year level), `class`, `family` or `other`. Only the audiences in `config.ini` can reach the channel. Unclear means `family`.
3. **Separate private fields.** Family-specific content goes in a `private` field that the channel layout never reads.
4. **Blocked words.** The finished post is scanned against your `private_terms.txt` (names, IDs), plus built-in patterns for email addresses, phone numbers, Compass links, "your child", personal greetings and personal-record topics (attendance, reports, health, incidents, money owed). One hit withholds the whole email, and you get a private note saying why.
5. **Link allowlist.** Only domains you list can appear. Compass links never can, because they carry your personal login token.
6. **Approval.** With `review = true`, each post comes to you first with **Post to channel** and **Skip** buttons. Only your Telegram account can press them. Low-risk kinds such as newsletters and reminders can be set to post automatically, and they still go through checks 1 to 5.
7. **Audit trail.** `manifest.json` keeps the exact text of every post and the reason for every withheld item.

It fails closed. If anything is unclear or errors, nothing is posted.

More detail is in [docs/privacy.md](docs/privacy.md).

## Requirements

**Accounts and services**

| What | Why | Notes |
|---|---|---|
| Claude plan with scheduled tasks | Runs the reader on a schedule | Pro, Max, Team or Enterprise |
| Claude desktop app on the computer | Lets scheduled runs use that computer's browser and files | Must be open and online when runs fire |
| **Gmail connector** in Claude | Reads Compass emails | Read access is enough. Nothing is labelled, moved or sent |
| **Claude in Chrome** extension | Opens Compass news items and newsletters | Chrome must be signed in to Compass |
| Folder access for the task | Lets the run read `instructions.md` and write to `outbox/` | Granted in the desktop app for the scheduled task |
| Compass parent account | Source of the emails | Email notifications on, delivered to the Gmail account above |
| Telegram account and bot | Posting and approvals | Free. Create a bot with @BotFather |
| iCloud account (optional) | Calendar events | App-specific password, not your Apple ID password |

**On the computer**

- Python 3.10 or newer, plus the packages in `requirements.txt` (`caldav`, `icalendar`, `tzdata`). The calendar packages are only needed if you use iCloud.
- Something to run `compass_post.py` every 10 minutes: Windows Task Scheduler, cron or launchd.
- Outbound HTTPS to `api.telegram.org`, and to `caldav.icloud.com` if you use the calendar.

## Setup

Full walkthrough: [docs/setup.md](docs/setup.md). In short:

1. Clone this repo to the computer that has the Claude desktop app and Chrome.
2. Copy the three templates and fill them in. The real files are git-ignored.
   - `config.example.ini` → `config.ini` (bot token, channel, your chat id, settings)
   - `school.example.md` → `school.md` (school name, sender, year level, allowed links)
   - `private_terms.example.txt` → `private_terms.txt` (names and IDs that must never be posted)
3. `pip install -r requirements.txt`
4. Create the Telegram bot and channel, add the bot as a channel admin, and run `python compass_post.py --whoami` to get the channel and chat ids.
5. Schedule `compass_post.py` every 10 minutes.
6. In Claude, create a scheduled task that requires this computer, attach this folder, and use the prompt in [docs/scheduled-task.md](docs/scheduled-task.md).
7. Test with the files in `samples/`.

## Repository layout

| Path | What it is |
|---|---|
| `instructions.md` | What the Claude run does. The single source of truth for its behaviour |
| `compass_post.py` | Guardrails, rendering, Telegram, approvals, calendar, manifest |
| `school.example.md` | Template for your school's details |
| `config.example.ini` | Template for settings and secrets |
| `private_terms.example.txt` | Template for blocked words |
| `requirements.txt` | Python packages |
| `samples/` | Two test posts: one to approve, one to skip |
| `tests/` | Offline tests (`python -m unittest discover -s tests -v`) |
| `docs/` | Setup, privacy, file format, scheduled task, troubleshooting |

Created at runtime and git-ignored: `outbox/`, `pending/`, `sent/`, `hold/`, `failed/`, `manifest.json`, `state.json`, `run.lock`, `compass_post.log`.

## Limits

- **WhatsApp isn't supported.** Meta's official API can't post into an ordinary parents' group, and unofficial tools risk a ban on your number. Telegram's bot API supports this properly. Share the channel's invite link in your WhatsApp group instead.
- **The computer must be on.** Scheduled runs and the poster both run there.
- **Approvals are handled while the poster runs.** It listens for up to 9 minutes after each cycle while anything awaits approval, so taps normally respond within seconds.

## Contributing

Run the tests before sending changes. Never commit `config.ini`, `school.md`, `private_terms.txt` or anything from the runtime folders. They contain personal details.
