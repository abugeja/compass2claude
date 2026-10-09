# compass2claude: run instructions

The Claude scheduled task reads this file on every run and follows it. Change behaviour by editing this file, not the scheduled task.

**Before you start, read `school.md` in the same folder.** It holds this install's details: the school name, the Compass sender address, the year level, any external newsletter site, and which link domains are allowed. Wherever this file says "the school", "our year level" or "our class", use the values from `school.md`. If `school.md` is missing, stop and say so in one line.

## The privacy rule (read first)

The group part of every file you write is shown to **other parents** in a Telegram channel. It must only ever contain information meant for the **whole school**, **all families in our year level**, or **our whole class**.

Never put any of the following in `actions`, `dates`, `sections` or `public_link`. These go in `private` instead, or are left out:

- Anything about our child, our family, or any other individual child, student or family.
- The name of **any child**, whether or not it is ours. Don't name students even in good news ("Student of the week: …").
- Staff or parent email addresses, phone numbers, or personal names in a contact context ("email Mr X at …"). Write "reply via the Compass message" instead.
- Attendance, absences, reports, assessment results, behaviour, incidents, injuries, health, medication, allergies, wellbeing, learning support, fees or money owed, interview bookings, or anything else from one child's record.
- **Compass links of any kind.** They contain the parent's personal login token. Only the domains listed under "Allowed links" in `school.md` may be used.
- Greetings or wording addressed to us personally ("Dear Mrs Smith", "your child").

**Emails about one student are always `family`.** Compass marks these with a line near the top like `Re: Firstname SURNAME (ABC1234), 0X at <school>`, which has a student's name in capitals and a student code in brackets. Whenever you see that, set the audience to `family`, with no exceptions, even if the content looks general. Event and class emails use different lines (`Re: Event - …`, `Re: Class - …`) and are judged on their content.

**When in doubt, the email is `family`.** A missing group post costs nothing. A leaked one can't be undone.

The local script enforces this independently. It checks a private blocked-words list, scans for emails, phone numbers, Compass links and personal-record topics, and only posts the allowed audiences. The owner also approves every post. None of that is a reason to be less careful here.

## What you have

- Gmail (read only). School mail comes from the sender address in `school.md`.
- Claude in Chrome on this computer, signed in to Compass.
- This folder via the device file tools: `manifest.json`, `outbox/`, `sent/`, `hold/`.

You do not post to Telegram or touch the calendar. The local script does that from your outbox files.

## Steps

1. **Load what's done.** Read `manifest.json`. Its `processed` keys are Gmail message IDs already handled. Also treat any file in `outbox/` or `hold/` as handled.
2. **Find new mail.** Search Gmail: `from:<sender from school.md> newer_than:7d`. Skip handled message IDs. Work oldest first. If nothing is new, stop and say "No new school emails." Write no files.
3. **Read each email** with get_thread (PLAIN_TEXT). Ignore the Compass footer.
   - Direct messages and event reminders: the content is in the body.
   - "News:" emails: open the "View news item" link in Chrome in a new tab. If it links on to an external newsletter (see `school.md`), open that too and read the sections that matter to a family in our year level. Skip pages that hold nothing dated or actionable. Close your tabs when done.
   - If a page won't load, put that in `private.note` with no link, and say so in your final reply.
4. **Decide the audience** (see below), then **write one file per email** to `outbox/<YYYYMMDD>-<gmail message id>.json` in the format below.
5. Never reply to, label, archive, delete or mark emails as read. Never pay, RSVP, consent or submit anything in Compass. Read only.

## Audience

| audience | Use when | Reaches the group? |
|---|---|---|
| `school` | Sent to all families (newsletters, whole-school news) | Yes |
| `prep` | For all families in our year level (the name is historical; it means "our year level") | Yes |
| `class` | A teacher message to our whole class, with nothing about any individual child | Yes |
| `family` | About our child or family, any email whose `Re:` line names a student, or anything you're unsure of | No, owner only |
| `other` | Another year level only, or not relevant to our families | No |

For `family` and `other`, leave `actions`, `dates` and `sections` empty and use `private`.

A newsletter is `school` even if a few items in it are for other year levels. Just leave those items out.

## File format

```json
{
  "schema": 2,
  "source_id": "<gmail message id>",
  "kind": "newsletter | news | message | reminder",
  "subject": "<email subject, without the 'News: ' prefix>",
  "re_line": "<the email's 'Re: …' line copied exactly, or null if it has none>",
  "from": "<school short name from school.md>",
  "received": "YYYY-MM-DDTHH:MM",
  "audience": "school | prep | class | family | other",
  "actions": [
    {"text": "Pay and give consent for the excursion on Compass",
     "due": "YYYY-MM-DDTHH:MM", "level": "required | optional"}
  ],
  "dates": [
    {"title": "Year level excursion", "start": "YYYY-MM-DD", "end": null,
     "all_day": true, "kind": "no_school | routine_change | attend | optional",
     "location": null, "note": null}
  ],
  "sections": [
    {"heading": "From the Principal", "fyi": ["Named school hat every day this term"]}
  ],
  "public_link": "<allowed link or null>",
  "private": {
    "actions": [], "dates": [], "note": null
  }
}
```

Times are local school time with no offset. `received` is the email's date and time in local time. Use `"start": "YYYY-MM-DDTHH:MM"` and `"all_day": false` for timed events. For multi-day ranges, give `end` as the last date. Use `null` for any field you don't have. Never leave out `text` or `title`.

### Field rules

- **re_line**: copy the `Re: …` line from the top of the email body exactly as written, or null if there isn't one. The script checks it independently. If it names a student, nothing from the email reaches the group, whatever audience you chose.

- **actions**: things a family in our year level needs to do. `level` is `required` for payment, consent or forms, and `optional` for tickets, volunteering or donations. Include `due` whenever there's a deadline. One action per line, starting with a verb.
- **dates** are only what a family in our year level would attend or plan around:
  - `no_school`: curriculum days, public holidays, student-free days.
  - `routine_change`: early finishes, changed drop-off or pickup, gate changes.
  - `attend`: our child takes part, such as excursions, swimming or incursions.
  - `optional`: social or community events like art shows, BBQs, discos or fundraisers.
  - Skip other year levels' camps and events, staff-only days and council meetings.
- **sections**: FYI items with no date or action. For newsletters, use the newsletter's own section headings. For other emails, use one section with heading "FYI". Keep only items that matter to a family in our year level, at one short line each, and drop anything already covered in actions or dates.
- **public_link**: a link on an allowed domain only (see `school.md`), such as the external newsletter or a ticketing page. Otherwise null.
- **private**: actions, dates and a short note for the owner only, in the same shapes. Use this for anything family-specific, and for anything you moved out of the group fields for privacy reasons.

If an email only repeats something already posted, such as a second reminder for the same deadline, still write the file, but keep only what's new. The script also drops exact repeats.

## Style

Short and factual. Say what, when, who, what to do and by when. No intros, sign-offs, filler or emoji, since the script adds the traffic lights. Plain English, using the spelling of `school.md`'s locale.

## Finish

Reply with one line per email: the subject, the audience, and roughly what went where (group or private). Or reply "No new school emails."
