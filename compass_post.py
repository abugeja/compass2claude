"""
compass2claude poster.

Claude's scheduled run drops one structured JSON file per school email into
./outbox. This script, run every 10 minutes (Task Scheduler / cron):

  1. Decides, in code, what may reach the parents' Telegram channel
     (audience allowlist, blocked-words scan, pattern scan, link allowlist).
  2. Renders posts in a fixed layout with traffic-light markers.
  3. Sends each channel post to the owner privately for approval first
     (review mode), then posts it when the owner taps "Post to channel".
  4. Sends family-only content, and anything withheld, to the owner privately.
  5. Adds dates to an iCloud calendar over CalDAV (optional).
  6. Records everything in manifest.json.

Secrets and the blocked-words list live on the local machine only.
See README.md and docs/ for setup.
"""

import configparser
import hashlib
import html
import json
import logging
import re
import shutil
import sys
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUTBOX = BASE / "outbox"
SENT = BASE / "sent"
FAILED = BASE / "failed"
HOLD = BASE / "hold"
PENDING = BASE / "pending"
LOCK = BASE / "run.lock"
MANIFEST = BASE / "manifest.json"
STATE = BASE / "state.json"
CONFIG = BASE / "config.ini"
TERMS = BASE / "private_terms.txt"
LOG = BASE / "compass_post.log"
TZ_NAME = "Australia/Melbourne"  # overridden by [school] timezone
TG_LIMIT = 3800
DIVIDER = "━━━━━━━━━━━━━━━━━━"

GROUP_AUDIENCES_DEFAULT = "school, prep, class"
AUDIENCE_LABEL = {"school": "All families", "prep": "Year level families", "class": "Class families"}
LINK_ALLOW_DEFAULT = "compasstix.com"

RED, AMBER, GREEN = "🔴", "🟡", "🟢"
LEGEND = (f"<b>How to read these posts</b>\n\n"
          f"{RED} Act now, or no school / routine change\n"
          f"{AMBER} Plan for it, or a due date coming up\n"
          f"{GREEN} Optional or social\n\n"
          f"🔔 reminder  ·  📰 newsletter  ·  📣 news  ·  💬 message")
KIND_TAG = {"reminder": "🔔 REMINDER", "newsletter": "📰 NEWSLETTER",
            "news": "📣 NEWS", "message": "💬 MESSAGE"}

logging.basicConfig(filename=LOG, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("compass2claude")


# ---------------------------------------------------------------- storage

def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def now():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(TZ_NAME)).replace(tzinfo=None)


def stamp():
    return now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- telegram

def tg_call(token, method, params):
    data = urllib.parse.urlencode(
        {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
         for k, v in params.items()}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.loads(r.read())
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method} error: {body}")
    return body["result"]


def split_oversized(block):
    """A single part longer than a message: drop quote tags and split by line."""
    if len(block) <= TG_LIMIT:
        return [block]
    plain = re.sub(r"</?blockquote[^>]*>", "", block)
    out, buf = [], ""
    for line in plain.split("\n"):
        if len(buf) + len(line) + 1 > TG_LIMIT and buf:
            out.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    return out + ([buf] if buf else [])


def chunk_blocks(blocks, header=""):
    """Join blocks into messages under the Telegram size limit."""
    msgs, buf = [], header
    for big in blocks:
        for b in split_oversized(big):
            piece = ("\n" if buf else "") + b
            if len(buf) + len(piece) > TG_LIMIT and buf:
                msgs.append(buf)
                buf = b
            else:
                buf += piece
    if buf:
        msgs.append(buf)
    return msgs


def tg_send(token, chat_id, text, buttons=None, silent=False):
    params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": "true"}
    if silent:
        params["disable_notification"] = "true"
    if buttons:
        params["reply_markup"] = {"inline_keyboard": [buttons]}
    return tg_call(token, "sendMessage", params)


# ---------------------------------------------------------------- guardrails

def load_terms():
    if not TERMS.exists():
        return []
    terms = []
    for line in TERMS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            terms.append(line)
    return terms


# Compass emails about one student carry a line like
# "Re: Firstname SURNAME (ABC1234), 0X at School". Either part is enough.
STUDENT_CODE = re.compile(r"\(\s*[A-Z]{2,5}\d{3,6}\s*\)")
STUDENT_NAME = re.compile(r"\bRe:\s*[A-Z][a-z'-]+(?:\s+[A-Z][a-z'-]+)*\s+[A-Z][A-Z'-]{1,}\b")


def names_a_student(text):
    return bool(text and (STUDENT_CODE.search(text) or STUDENT_NAME.search(text)))


PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "an email address"),
    (re.compile(r"(?<!\d)(?:\+?61|0)[\s-]?[2-478](?:[\s-]?\d){8}(?!\d)"), "a phone number"),
    (re.compile(r"accessToken|userId=|schoolId=|compass\.education", re.I), "a personal Compass link"),
    (re.compile(r"\byour (?:child|son|daughter)\b", re.I), "'your child' wording"),
    (STUDENT_CODE, "a Compass student code"),
    (STUDENT_NAME, "a student name in Compass format"),
    (re.compile(r"\bDear\s+(?:Mr|Mrs|Ms|Miss|Dr)\b", re.I), "a personal greeting"),
    (re.compile(r"\b(?:absent|absence|attendance|report card|incident|injur|medical|"
                r"medication|allerg|behaviour|wellbeing referral|invoice|overdue|"
                r"outstanding (?:balance|amount))", re.I), "a personal-record topic"),
]

URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)


def guard(text, terms, link_allow):
    """Return a list of reasons this text must not go to the group."""
    reasons = []
    low = text.lower()
    for t in terms:
        if re.search(r"(?<![a-z])" + re.escape(t.lower()) + r"(?![a-z])", low):
            reasons.append(f"blocked word '{t}'")
    for rx, why in PATTERNS:
        if rx.search(text):
            reasons.append(f"contains {why}")
    for url in URL_RE.findall(text):
        host = urllib.parse.urlparse(url).hostname or ""
        if not any(host == d or host.endswith("." + d) for d in link_allow):
            reasons.append(f"link not on allowlist ({host})")
    return sorted(set(reasons))


# ---------------------------------------------------------------- rendering

def esc(s):
    return html.escape(str(s or ""), quote=False)


def fmt_day(d):
    return d.strftime("%a ") + str(d.day) + d.strftime(" %b")


def fmt_time(t):
    s = t.strftime("%I:%M%p").lstrip("0").lower()
    return s.replace(":00", "")


def parse_when(s):
    if not s:
        return None
    return datetime.fromisoformat(s) if "T" in s else datetime.fromisoformat(s + "T00:00")


def fmt_range(d):
    start, end = parse_when(d.get("start")), parse_when(d.get("end"))
    if d.get("all_day") or "T" not in (d.get("start") or ""):
        if end and end.date() != start.date():
            if (start.year, start.month) == (end.year, end.month):
                return f"{start.strftime('%a')} {start.day} – {fmt_day(end)}"
            return f"{fmt_day(start)} – {fmt_day(end)}"
        return fmt_day(start)
    if end and end.date() == start.date():
        a, b = fmt_time(start), fmt_time(end)
        if a[-2:] == b[-2:]:
            a = a[:-2]  # 12–3pm rather than 12pm–3pm
        return f"{fmt_day(start)}, {a}–{b}"
    return f"{fmt_day(start)}, {fmt_time(start)}"


def action_light(a):
    if a.get("level") == "optional":
        return GREEN
    due = parse_when(a.get("due"))
    if due and (due.date() - now().date()).days <= 3:
        return RED
    return AMBER


DATE_LIGHT = {"no_school": RED, "routine_change": RED, "attend": AMBER, "optional": GREEN}


def date_light(d):
    return DATE_LIGHT.get(d.get("kind"), AMBER)


def render_action(a):
    line = f"{action_light(a)} {esc(a.get('text'))}"
    due = parse_when(a.get("due"))
    if due:
        when = fmt_day(due) + (f" {fmt_time(due)}" if "T" in a["due"] else "")
        line += f"\n      ↳ <i>by {when}</i>"
    return line


def render_date(d):
    line = f"{date_light(d)} <b>{fmt_range(d)}</b> · {esc(d.get('title'))}"
    if d.get("note"):
        line += f"\n      ↳ <i>{esc(d['note'])}</i>"
    return line


def render_header(item, audience_label=None, tag=True):
    rec = parse_when(item.get("received"))
    when = (f"{fmt_day(rec)}, {fmt_time(rec)}" if rec and "T" in item["received"]
            else fmt_day(rec) if rec else "")
    lines = [DIVIDER]
    if tag:
        lines.append(f"<b>{KIND_TAG.get(item.get('kind'), '📌 UPDATE')}</b>")
    lines.append(f"📬 <b>Subject:</b> {esc(item.get('subject'))}")
    if audience_label:
        lines.append(f"👥 <b>To:</b> {esc(audience_label)}")
    else:
        lines.append(f"👤 <b>From:</b> {esc(item.get('from') or 'School')}")
    if when:
        lines.append(f"🕒 <b>Date:</b> {when}")
    return "\n".join(lines)


def quote(lines, expandable=False):
    body = "\n".join(lines)
    return (f"<blockquote expandable>{body}</blockquote>" if expandable
            else f"<blockquote>{body}</blockquote>")


def render_group_parts(item, actions, dates):
    """The post as a list of parts, so long posts split between sections."""
    parts = [render_header(item, AUDIENCE_LABEL.get(item["audience"]))]
    if actions:
        parts.append("\n✅ <b>Actions</b>\n" + "\n".join(render_action(a) for a in actions))
    if dates:
        parts.append("\n📅 <b>Dates</b>\n" + "\n".join(render_date(d) for d in dates))
    newsletter = item.get("kind") == "newsletter"
    for sec in item.get("sections") or []:
        fyi = [f for f in sec.get("fyi") or [] if isinstance(f, str) and f.strip()]
        if not fyi:
            continue
        head = sec.get("heading") or "FYI"
        icon = "📰" if newsletter and head != "FYI" else "ℹ️"
        bullets = [f"• {esc(f)}" for f in fyi]
        parts.append(f"\n{icon} <b>{esc(head)}</b>\n" +
                     quote(bullets, expandable=newsletter and len(bullets) > 3))
    if item.get("public_link"):
        label = "Read the full newsletter" if newsletter else "More details"
        parts.append(f'\n🔗 <a href="{html.escape(item["public_link"])}">{label}</a>')
    return parts


def render_group_block(item, actions, dates):
    return "\n".join(render_group_parts(item, actions, dates))


def render_private_block(item, note_lines):
    return render_header(item, tag=False) + "\n" + "\n".join(note_lines)


# ---------------------------------------------------------------- calendar

def get_calendar(cfg):
    import caldav
    client = caldav.DAVClient(url="https://caldav.icloud.com/",
                              username=cfg["icloud"]["apple_id"],
                              password=cfg["icloud"]["app_password"])
    want = cfg["icloud"].get("calendar_name", "Family").strip().lower()
    for cal in client.principal().calendars():
        if (cal.name or "").strip().lower() == want:
            return cal
    raise RuntimeError(f"Calendar '{want}' not found on iCloud")


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def event_key(ev):
    return f"{(ev.get('start') or '')[:10]}|{norm(ev.get('title'))}"


def action_key(a):
    return f"a|{(a.get('due') or '')[:10]}|{norm(a.get('text'))[:40]}"


def build_ics(uid, ev):
    from icalendar import Calendar, Event
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(TZ_NAME)
    cal = Calendar()
    cal.add("prodid", "-//compass2claude//EN")
    cal.add("version", "2.0")
    e = Event()
    e.add("uid", uid)
    e.add("summary", ev["title"])
    e.add("dtstamp", datetime.now(tz))
    if ev.get("all_day") or "T" not in ev["start"]:
        start = date.fromisoformat(ev["start"][:10])
        end = date.fromisoformat((ev.get("end") or ev["start"])[:10])
        e.add("dtstart", start)
        e.add("dtend", end + timedelta(days=1))
    else:
        start = datetime.fromisoformat(ev["start"]).replace(tzinfo=tz)
        end = (datetime.fromisoformat(ev["end"]).replace(tzinfo=tz)
               if ev.get("end") else start + timedelta(hours=1))
        e.add("dtstart", start)
        e.add("dtend", end)
    if ev.get("location"):
        e.add("location", ev["location"])
    if ev.get("note"):
        e.add("description", ev["note"])
    cal.add_component(e)
    return cal.to_ical().decode()


def flush_calendar(cfg, manifest):
    """Add queued events once [icloud] is configured. Checkpoints each one."""
    queue = manifest.setdefault("events_queue", [])
    if not queue or not cfg.has_section("icloud"):
        return
    added = manifest.setdefault("events_added", {})
    cal = get_calendar(cfg)
    while queue:
        ev = queue[0]
        key = event_key(ev)
        if key not in added:
            uid = "c2c-" + hashlib.sha1(key.encode()).hexdigest()[:20] + "@compass2claude"
            cal.save_event(build_ics(uid, ev))
            added[key] = {"uid": uid, "title": ev["title"], "start": ev["start"],
                          "added": stamp()}
        queue.pop(0)
        save_json(MANIFEST, manifest)


def queue_events(manifest, events):
    added = manifest.setdefault("events_added", {})
    queue = manifest.setdefault("events_queue", [])
    queued = {event_key(e) for e in queue}
    for ev in events:
        k = event_key(ev)
        if k not in added and k not in queued:
            queue.append(ev)
            queued.add(k)


# ---------------------------------------------------------------- approvals

def handle_callbacks(cfg, manifest, state, timeout=0):
    token = cfg["telegram"]["bot_token"]
    owner = cfg["telegram"].get("private_chat_id", "").strip()
    updates = tg_call(token, "getUpdates", {
        "offset": state.get("offset", 0), "timeout": timeout,
        "allowed_updates": ["callback_query"]})
    for u in updates:
        state["offset"] = u["update_id"] + 1
        cq = u.get("callback_query")
        if not cq:
            continue
        if str(cq["from"]["id"]) != owner:
            tg_call(token, "answerCallbackQuery",
                    {"callback_query_id": cq["id"], "text": "Not allowed."})
            continue
        action, _, batch_id = (cq.get("data") or "").partition(":")
        pfile = PENDING / f"{batch_id}.json"
        if not pfile.exists():
            tg_call(token, "answerCallbackQuery",
                    {"callback_query_id": cq["id"], "text": "Already handled."})
            continue
        batch = load_json(pfile, {})
        if action == "post":
            for msg in batch["messages"]:
                tg_send(token, cfg["telegram"]["channel_id"], msg)
            outcome = "posted"
        else:
            outcome = "skipped"
        for sid in batch["items"]:
            rec = manifest["processed"].setdefault(sid, {})
            rec["group_status"] = outcome
            rec["group_decided"] = stamp()
        save_json(MANIFEST, manifest)
        pfile.unlink()
        tg_call(token, "answerCallbackQuery",
                {"callback_query_id": cq["id"],
                 "text": "Posted to the channel." if outcome == "posted" else "Skipped."})
        tg_call(token, "editMessageReplyMarkup", {
            "chat_id": cq["message"]["chat"]["id"],
            "message_id": cq["message"]["message_id"],
            "reply_markup": {"inline_keyboard": []}})
        subj = ", ".join(manifest["processed"].get(s, {}).get("subject") or s
                         for s in batch["items"])
        tg_send(token, owner, (f"Posted to the channel: {esc(subj)}" if outcome == "posted"
                else f"Skipped, nothing went to the channel: {esc(subj)}"), silent=True)
    save_json(STATE, state)


# ---------------------------------------------------------------- processing

def in_quiet_hours(cfg):
    spec = cfg.get("group", "quiet_hours", fallback="20-7")
    try:
        start, end = (int(x) for x in spec.split("-"))
    except ValueError:
        return False
    h = now().hour
    return h >= start or h < end if start > end else start <= h < end


def process_outbox(cfg, manifest, paths):
    terms = load_terms()
    link_allow = [d.strip().lower() for d in cfg.get(
        "group", "link_allowlist", fallback=LINK_ALLOW_DEFAULT).split(",") if d.strip()]
    group_aud = {a.strip() for a in cfg.get(
        "group", "audiences", fallback=GROUP_AUDIENCES_DEFAULT).split(",") if a.strip()}
    posted_keys = manifest.setdefault("group_keys", [])

    group_blocks, group_items, private_blocks, done_files = [], [], [], []

    for path in paths:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.error("unreadable %s: %s", path.name, exc)
            shutil.move(str(path), FAILED / path.name)
            continue
        if item.get("schema") != 2:
            log.warning("held %s: not schema 2", path.name)
            shutil.move(str(path), HOLD / path.name)
            continue

        try:
            sid = item["source_id"]
            rec = manifest["processed"].setdefault(sid, {})
            rec.update({"subject": item.get("subject"), "kind": item.get("kind"),
                        "audience": item.get("audience"), "received": item.get("received")})
            notes = []

            # Calendar: public and private dates, plus required-action deadlines.
            cal = list(item.get("dates") or []) + list((item.get("private") or {}).get("dates") or [])
            for a in list(item.get("actions") or []) + list((item.get("private") or {}).get("actions") or []):
                if a.get("due") and a.get("level") != "optional":
                    cal.append({"title": "Due: " + (a.get("text") or ""), "start": a["due"][:10],
                                "all_day": True, "note": a.get("note")})
            queue_events(manifest, cal)

            # Group eligibility, decided in code.
            actions = [a for a in item.get("actions") or []
                       if action_key(a) not in posted_keys]
            dates = [d for d in item.get("dates") or []
                     if f"d|{event_key(d)}" not in posted_keys]
            has_fyi = any(s.get("fyi") for s in item.get("sections") or [])
            withheld = None
            if names_a_student(item.get("re_line")) or names_a_student(item.get("subject")):
                withheld = "email is about an individual student (Compass Re: line)"
            elif item.get("audience") not in group_aud:
                withheld = f"audience is '{item.get('audience')}', not a group audience"
            elif not (actions or dates or has_fyi):
                withheld = "nothing new for the group (already posted)"
            else:
                block = render_group_block(item, actions, dates)
                reasons = guard(html.unescape(block), terms, link_allow)
                if reasons:
                    withheld = "; ".join(reasons)
                else:
                    group_blocks.append(block)
                    group_items.append(sid)
                    rec["group_text"] = block
                    rec["_parts"] = render_group_parts(item, actions, dates)
                    for a in actions:
                        posted_keys.append(action_key(a))
                    for d in dates:
                        posted_keys.append(f"d|{event_key(d)}")

            if withheld:
                rec["group_status"] = "withheld"
                rec["withheld_reason"] = withheld
                if not withheld.startswith("nothing new"):
                    notes.append(f"<i>Not sent to the group: {esc(withheld)}</i>")

            priv = item.get("private") or {}
            for a in priv.get("actions") or []:
                notes.append(render_action(a))
            for d in priv.get("dates") or []:
                notes.append(render_date(d))
            if priv.get("note"):
                notes.append(esc(priv["note"]))
            if notes:
                private_blocks.append(render_private_block(item, notes))

            rec["processed"] = stamp()
            done_files.append(path)
        except Exception as exc:
            log.exception("failed %s", path.name)
            shutil.move(str(path), FAILED / path.name)
            private_blocks.append(f"{DIVIDER}\n<i>Could not process {esc(path.name)}: {esc(exc)}. "
                                  f"Moved to failed/. Nothing from it was posted.</i>")

    return group_blocks, group_items, private_blocks, done_files


def apply_school_settings(cfg):
    global TZ_NAME
    TZ_NAME = cfg.get("school", "timezone", fallback=TZ_NAME)
    year = cfg.get("school", "year_label", fallback="").strip()
    if year:
        AUDIENCE_LABEL["prep"] = f"{year} families"
        AUDIENCE_LABEL["class"] = cfg.get("school", "class_label", fallback=f"{year} class")


def main():
    for d in (OUTBOX, SENT, FAILED, HOLD, PENDING):
        d.mkdir(exist_ok=True)
    cfg = configparser.ConfigParser()
    if not cfg.read(CONFIG, encoding="utf-8"):
        sys.exit("config.ini missing")
    apply_school_settings(cfg)
    token = cfg["telegram"]["bot_token"]

    if "--pin-legend" in sys.argv:
        msg = tg_send(token, cfg["telegram"]["channel_id"], LEGEND, silent=True)
        tg_call(token, "pinChatMessage", {"chat_id": cfg["telegram"]["channel_id"],
                                          "message_id": msg["message_id"],
                                          "disable_notification": "true"})
        print("Legend posted and pinned.")
        return

    if "--whoami" in sys.argv:
        for u in tg_call(token, "getUpdates", {}):
            chat = (u.get("message") or u.get("channel_post") or {}).get("chat", {})
            if chat:
                print(chat["id"], chat.get("type"), chat.get("title") or chat.get("first_name"))
        return

    if not take_lock():
        log.info("another run is active; exiting")
        return
    try:
        run(cfg, token)
    finally:
        LOCK.unlink(missing_ok=True)


def take_lock():
    """One instance at a time. A lock older than 15 minutes is stale."""
    import os
    import time
    try:
        if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 900:
            return False
        LOCK.write_text(str(os.getpid()))
        return True
    except OSError:
        return False


def listen_for_approvals(cfg, manifest, state, started):
    """While posts await approval, long-poll so taps are handled within
    seconds. Stops before the next scheduled run (default 9 minutes)."""
    import time
    budget = cfg.getint("group", "listen_seconds", fallback=540)
    while any(PENDING.glob("*.json")) and time.monotonic() - started < budget:
        left = budget - (time.monotonic() - started)
        try:
            handle_callbacks(cfg, manifest, state, timeout=int(max(1, min(25, left))))
        except Exception:
            log.exception("listening failed")
            time.sleep(5)
        LOCK.touch()


def run(cfg, token):
    import time
    started = time.monotonic()
    manifest = load_json(MANIFEST, {"processed": {}})
    manifest.setdefault("processed", {})
    state = load_json(STATE, {})
    owner = cfg["telegram"].get("private_chat_id", "").strip()
    review = cfg.getboolean("group", "review", fallback=True)

    if owner:
        try:
            handle_callbacks(cfg, manifest, state)
        except Exception:
            log.exception("callback handling failed")

    if review and not owner:
        if any(OUTBOX.glob("*.json")):
            log.warning("review is on but private_chat_id is empty; waiting")
        return
    if in_quiet_hours(cfg):
        if any(OUTBOX.glob("*.json")):
            log.info("quiet hours; waiting")
    else:
        process_and_send(cfg, token, owner, review, manifest)

    if owner:
        listen_for_approvals(cfg, manifest, state, started)


def process_and_send(cfg, token, owner, review, manifest):
    """One email at a time: process, send, then commit. A failure rolls back
    only that email, which retries next cycle, so nothing is sent twice."""
    import copy
    for path in sorted(OUTBOX.glob("*.json")):
        snapshot = copy.deepcopy(manifest)
        before = set(PENDING.glob("*.json"))
        try:
            group_blocks, group_items, private_blocks, done_files = \
                process_outbox(cfg, manifest, [path])
            _send(cfg, token, owner, review, manifest,
                  group_blocks, group_items, private_blocks)
        except Exception:
            log.exception("failed on %s; will retry next cycle", path.name)
            for p in set(PENDING.glob("*.json")) - before:
                p.unlink()
            manifest.clear()
            manifest.update(snapshot)
            save_json(MANIFEST, manifest)
            break
        save_json(MANIFEST, manifest)
        for p in done_files:
            if p.exists():
                shutil.move(str(p), SENT / p.name)
        log.info("%s: %d to group, %d private", path.name,
                 len(group_items), len(private_blocks))

    try:
        flush_calendar(cfg, manifest)
    except Exception:
        log.exception("calendar failed; will retry next cycle")


def _send(cfg, token, owner, review, manifest, group_blocks, group_items, private_blocks):
    if private_blocks and owner:
        for msg in chunk_blocks(private_blocks, "<b>For you only</b>"):
            tg_send(token, owner, msg)

    if not group_blocks:
        return
    auto_kinds = {k.strip() for k in cfg.get(
        "group", "auto_post_kinds", fallback="").split(",") if k.strip()}
    for block, sid in zip(group_blocks, group_items):
        msgs = chunk_blocks(manifest["processed"][sid].pop("_parts", None) or [block])
        kind = manifest["processed"][sid].get("kind")
        if review and kind not in auto_kinds:
            batch_id = uuid.uuid4().hex[:12]
            save_json(PENDING / f"{batch_id}.json",
                      {"items": [sid], "messages": msgs, "created": stamp()})
            for i, msg in enumerate(msgs):
                last = i == len(msgs) - 1
                label = "<i>Preview, not posted yet</i>\n" if i == 0 else ""
                tg_send(token, owner, label + msg, buttons=[
                    {"text": "Post to channel", "callback_data": f"post:{batch_id}"},
                    {"text": "Skip", "callback_data": f"skip:{batch_id}"},
                ] if last else None)
            manifest["processed"][sid]["group_status"] = "awaiting_approval"
        else:
            for msg in msgs:
                tg_send(token, cfg["telegram"]["channel_id"], msg)
            manifest["processed"][sid]["group_status"] = "posted"


if __name__ == "__main__":
    main()
