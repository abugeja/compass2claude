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
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

# Data (config, manifest, outbox and the other work folders) normally sits
# next to this script. COMPASS_DATA_DIR points it elsewhere, so the code can
# live in one repository and the personal data in another.
BASE = Path(os.environ.get("COMPASS_DATA_DIR") or Path(__file__).resolve().parent).resolve()
OUTBOX = BASE / "outbox"
SENT = BASE / "sent"
FAILED = BASE / "failed"
HOLD = BASE / "hold"
PENDING = BASE / "pending"
FEEDBACK = BASE / "feedback" / "inbox.jsonl"
LEARNINGS = BASE / "learnings.md"
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


def send_owner(token, owner, state, text, kind, batch=None, buttons=None, silent=False):
    """Send to the owner and remember the message so it can be cleaned up."""
    res = tg_send(token, owner, text, buttons=buttons, silent=silent)
    state.setdefault("owner_msgs", []).append({
        "id": res.get("message_id"), "kind": kind, "batch": batch,
        "sent": stamp(), "decided": None})
    return res


def delete_owner_msgs(token, owner, ids):
    """Delete bot messages from the owner chat. Missing or too-old messages
    are skipped; Telegram only allows deleting within 48 hours."""
    ids = sorted({i for i in ids if i})
    for n in range(0, len(ids), 100):
        chunk = ids[n:n + 100]
        try:
            tg_call(token, "deleteMessages", {"chat_id": owner, "message_ids": chunk})
        except Exception:
            for i in chunk:
                try:
                    tg_call(token, "deleteMessage", {"chat_id": owner, "message_id": i})
                except Exception:
                    pass


def cleanup_owner_chat(cfg, token, owner, state):
    """Delete decided previews and old notes; never touch pending previews."""
    if not cfg.getboolean("cleanup", "enabled", fallback=True):
        return
    keep_decided = cfg.getfloat("cleanup", "keep_decided_hours", fallback=6)
    keep_notes = cfg.getfloat("cleanup", "keep_notes_hours", fallback=24)
    t = now()
    pending_batches = {p.stem for p in PENDING.glob("*.json")}
    doomed, kept = [], []
    for m in state.get("owner_msgs", []):
        if m["kind"] == "preview" and not m.get("decided") \
                and m.get("batch") not in pending_batches:
            doomed.append(m["id"])  # decided, but the tap handling broke off
            continue
        sent = datetime.fromisoformat(m["sent"])
        if (t - sent).total_seconds() > 47 * 3600:
            continue  # too old for Telegram to delete; stop tracking it
        if m["kind"] == "preview":
            if m.get("decided") and (t - datetime.fromisoformat(m["decided"])).total_seconds() \
                    > keep_decided * 3600:
                doomed.append(m["id"])
                continue
        elif (t - sent).total_seconds() > keep_notes * 3600:
            doomed.append(m["id"])
            continue
        kept.append(m)
    if doomed:
        delete_owner_msgs(token, owner, doomed)
        log.info("cleanup: deleted %d bot messages", len(doomed))
    state["owner_msgs"] = kept


def purge_owner_chat(cfg, token, owner, state, sweep=1000):
    """Delete every bot message in the owner chat except pending previews.
    Also sweeps message ids sent before tracking existed."""
    pending_batches = {p.stem for p in PENDING.glob("*.json")}
    keep_ids = {m["id"] for m in state.get("owner_msgs", [])
                if m["kind"] == "preview" and m.get("batch") in pending_batches}
    probe = tg_send(token, owner, "Cleaning up…", silent=True)["message_id"]
    ids = [i for i in range(max(1, probe - sweep), probe + 1) if i not in keep_ids]
    delete_owner_msgs(token, owner, ids)
    state["owner_msgs"] = [m for m in state.get("owner_msgs", []) if m["id"] in keep_ids]
    return len(ids)


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
    (TOPIC_RE := re.compile(r"\b(?:absent|absence|attendance|report card|incident|injur|medical|"
                r"medication|allerg|behaviour|wellbeing referral|invoice|overdue|"
                r"outstanding (?:balance|amount))", re.I), "a personal-record topic"),
]
# Topic words are often fine in whole-school notices ("seek medical advice").
# On their own they don't block a post; they force a manual review instead.
SOFT_REASONS = {"contains a personal-record topic"}

URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)


def guard(text, terms, link_allow):
    """Return a list of reasons this text must not go to the group.
    Only reasons in SOFT_REASONS may be overridden by the owner."""
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
    lines.append(f"👤 <b>From:</b> {esc(item.get('from') or 'School')}")
    if audience_label:
        lines.append(f"👥 <b>To:</b> {esc(audience_label)}")
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
        parts.append("\n✅ <b>Actions</b>\n" + quote([render_action(a) for a in actions]))
    if dates:
        parts.append("\n📅 <b>Dates</b>\n" + quote([render_date(d) for d in dates]))
    newsletter = item.get("kind") == "newsletter"
    for sec in item.get("sections") or []:
        fyi = [f for f in sec.get("fyi") or [] if isinstance(f, str) and f.strip()]
        if not fyi:
            continue
        head = sec.get("heading") or "FYI"
        icon = "📰" if newsletter and head != "FYI" else "ℹ️"
        bullets = [f"• {esc(f)}" for f in fyi]
        parts.append(f"\n{icon} <b>{esc(head)}</b>\n" +
                     quote(bullets, expandable=newsletter and len(bullets) > 4))
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


def is_soon(ev, days):
    """True when an event starts today or within the next `days` days. A
    parent wants the event shown again on the day-before reminder, even if it
    was posted weeks earlier."""
    if days <= 0:
        return False
    try:
        start = datetime.fromisoformat((ev.get("start") or "")[:10]).date()
    except ValueError:
        return False
    today = now().date()
    return today <= start <= today + timedelta(days=days)


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


# ---------------------------------------------------------------- feedback

HELP = ("<b>Feedback</b>\n"
        "Send me any message and it's saved as feedback for the next Claude run. "
        "Reply to a preview or a note to attach it to that post.\n\n"
        "/learned shows what has been learned so far.")


def feedback_count():
    try:
        return sum(1 for line in FEEDBACK.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def save_feedback(text, about=None):
    FEEDBACK.parent.mkdir(exist_ok=True)
    n = feedback_count() + 1
    rec = {"id": n, "at": stamp(), "text": text.strip()[:2000], "about": about}
    with open(FEEDBACK, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return n


def handle_message(cfg, state, msg):
    """Owner messages become feedback. Anyone else is ignored."""
    token = cfg["telegram"]["bot_token"]
    owner = cfg["telegram"].get("private_chat_id", "").strip()
    if not owner or msg.get("chat", {}).get("type") != "private" \
            or str(msg.get("chat", {}).get("id")) != owner \
            or str((msg.get("from") or {}).get("id")) != owner:
        return
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if not text:
        return
    cmd, _, rest = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    if cmd in ("/start", "/help"):
        send_owner(token, owner, state, HELP, "note", silent=True)
        return
    if cmd == "/learned":
        body = LEARNINGS.read_text(encoding="utf-8").strip() if LEARNINGS.exists() else ""
        out = esc(body[:3500]) if body else "Nothing learned yet."
        send_owner(token, owner, state, f"🧠 <b>Learned so far</b>\n{out}", "note", silent=True)
        return
    if cmd == "/feedback":
        text = rest.strip()
        if not text:
            send_owner(token, owner, state, HELP, "note", silent=True)
            return
    elif text.startswith("/"):
        send_owner(token, owner, state, HELP, "note", silent=True)
        return
    about = ((msg.get("reply_to_message") or {}).get("text") or "")[:300] or None
    n = save_feedback(text, about)
    send_owner(token, owner, state,
               f"📝 Noted (#{n}). It will be applied from the next run.", "note", silent=True)
    log.info("feedback #%d saved", n)


# ---------------------------------------------------------------- approvals

def answer_callback(token, cq_id, text):
    """Best effort: Telegram rejects answers to taps older than a few seconds
    (HTTP 400), which happens when a tap is picked up on a later cycle. That
    must not stop the preview being collapsed and cleaned up."""
    try:
        tg_call(token, "answerCallbackQuery", {"callback_query_id": cq_id, "text": text})
    except Exception as e:
        log.info("could not answer tap (%s); carrying on", e)


def handle_callbacks(cfg, manifest, state, timeout=0):
    token = cfg["telegram"]["bot_token"]
    owner = cfg["telegram"].get("private_chat_id", "").strip()
    updates = tg_call(token, "getUpdates", {
        "offset": state.get("offset", 0), "timeout": timeout,
        "allowed_updates": ["callback_query", "message"]})
    for u in updates:
        state["offset"] = u["update_id"] + 1
        if u.get("message"):
            try:
                handle_message(cfg, state, u["message"])
            except Exception:
                log.exception("feedback handling failed")
            continue
        cq = u.get("callback_query")
        if not cq:
            continue
        if str(cq["from"]["id"]) != owner:
            answer_callback(token, cq["id"], "Not allowed.")
            continue
        action, _, batch_id = (cq.get("data") or "").partition(":")
        pfile = PENDING / f"{batch_id}.json"
        if not pfile.exists():
            answer_callback(token, cq["id"], "Already handled.")
            # A stale preview (e.g. a tap that failed earlier): remove it
            # rather than leave dead buttons in the chat.
            stale = (cq.get("message") or {}).get("message_id")
            if stale:
                delete_owner_msgs(token, owner, [stale])
                state["owner_msgs"] = [m for m in state.get("owner_msgs", [])
                                       if m["id"] != stale]
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
        answer_callback(token, cq["id"],
                        "Posted to the channel." if outcome == "posted" else "Skipped.")
        subj = ", ".join(manifest["processed"].get(i, {}).get("subject") or i
                         for i in batch["items"])
        icon, word = ("✅", "Posted") if outcome == "posted" else ("⏭️", "Skipped")
        line = f"{icon} <b>{word}</b> · {esc(subj)} · {fmt_time(now())}"
        button_msg = cq["message"]["message_id"]
        try:
            tg_call(token, "editMessageText", {
                "chat_id": owner, "message_id": button_msg, "text": line,
                "parse_mode": "HTML", "reply_markup": {"inline_keyboard": []}})
        except Exception:
            log.exception("could not collapse preview")
        extra = []
        for m in state.get("owner_msgs", []):
            if m.get("batch") == batch_id:
                m["decided"] = stamp()
                if m["id"] != button_msg:
                    extra.append(m["id"])
        if extra:
            delete_owner_msgs(token, owner, extra)
            state["owner_msgs"] = [m for m in state["owner_msgs"] if m["id"] not in extra]
    save_json(STATE, state)


# ---------------------------------------------------------------- worker
#
# Optional. With [worker] url and secret set, a Cloudflare Worker (worker/)
# receives button taps and owner messages from Telegram and acts on taps at
# once. This script uploads each preview batch to it and, on every run,
# collects what happened. Without [worker], taps are read by polling above.

def worker_cfg(cfg):
    url = cfg.get("worker", "url", fallback="").strip().rstrip("/")
    secret = cfg.get("worker", "secret", fallback="").strip()
    return (url, secret) if url and secret else None


def worker_call(w, method, path, body=None):
    req = urllib.request.Request(
        w[0] + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + w[1], "Content-Type": "application/json",
                 "User-Agent": "compass2claude"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


def worker_push_pending(w, manifest, pfile):
    """Give the Worker the exact messages to post if the owner taps Post."""
    batch = load_json(pfile, {})
    subj = ", ".join(manifest["processed"].get(i, {}).get("subject") or i
                     for i in batch.get("items", []))
    worker_call(w, "PUT", f"/pending/{pfile.stem}", {
        "items": batch.get("items", []), "messages": batch["messages"],
        "subject": subj, "created": batch.get("created")})
    batch["on_worker"] = True
    save_json(pfile, batch)


def local_stamp(epoch):
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(epoch, ZoneInfo(TZ_NAME)).replace(tzinfo=None) \
        .isoformat(timespec="seconds")


def sync_worker(cfg, manifest, state, w):
    """Upload what the Worker needs, then apply what it has collected."""
    token = cfg["telegram"]["bot_token"]
    owner = cfg["telegram"].get("private_chat_id", "").strip()

    for pfile in sorted(PENDING.glob("*.json")):
        if not load_json(pfile, {}).get("on_worker"):
            worker_push_pending(w, manifest, pfile)

    # Lessons learnt: the Worker answers /learned from this copy.
    text = LEARNINGS.read_text(encoding="utf-8") if LEARNINGS.exists() else ""
    digest = hashlib.sha1(text.encode()).hexdigest()
    if state.get("worker_learnings") != digest:
        worker_call(w, "PUT", "/learnings", {"text": text})
        state["worker_learnings"] = digest

    data = worker_call(w, "GET", "/sync")
    seen = state.setdefault("worker_seen", [])
    keys = []

    for d in data.get("decisions", []):
        keys.append(d["key"])
        batch_id = d["batch"]
        pfile = PENDING / f"{batch_id}.json"
        when = local_stamp(d["at"])
        for sid in load_json(pfile, {}).get("items") or d.get("items") or []:
            rec = manifest["processed"].setdefault(sid, {})
            rec["group_status"] = d["outcome"]
            rec["group_decided"] = when
        save_json(MANIFEST, manifest)
        pfile.unlink(missing_ok=True)
        extra = []
        for m in state.get("owner_msgs", []):
            if m.get("batch") == batch_id:
                m["decided"] = when
                if m["id"] != d.get("message_id"):
                    extra.append(m["id"])
        if extra:
            delete_owner_msgs(token, owner, extra)
            state["owner_msgs"] = [m for m in state["owner_msgs"] if m["id"] not in extra]
        log.info("worker: %s %s", batch_id, d["outcome"])

    # Feedback the owner sent to the bot. The Worker has already replied.
    for msg in data.get("messages", []):
        keys.append(msg["key"])
        if msg["key"] in seen:
            continue
        n = save_feedback(msg["text"], msg.get("about"))
        seen.append(msg["key"])
        log.info("feedback #%d saved (via worker)", n)

    # Replies the Worker sent, so cleanup can delete them later.
    for nt in data.get("notes", []):
        keys.append(nt["key"])
        if nt["key"] in seen:
            continue
        state.setdefault("owner_msgs", []).append({
            "id": nt["id"], "kind": "note", "batch": None,
            "sent": local_stamp(nt["at"]), "decided": None})
        seen.append(nt["key"])

    # A batch the Worker lost (expired) would leave dead buttons: re-upload.
    on_worker = set(data.get("pending", []))
    decided = {d["batch"] for d in data.get("decisions", [])}
    for pfile in PENDING.glob("*.json"):
        if pfile.stem not in on_worker and pfile.stem not in decided:
            worker_push_pending(w, manifest, pfile)

    state["worker_seen"] = seen[-200:]
    save_json(STATE, state)
    if keys:
        worker_call(w, "POST", "/ack", {"keys": keys})


def set_webhook(cfg, token, w):
    """Point Telegram at the Worker. Uploads pending previews first so no
    button is left without its batch."""
    manifest = load_json(MANIFEST, {"processed": {}})
    manifest.setdefault("processed", {})
    state = load_json(STATE, {})
    handle_callbacks(cfg, manifest, state)  # drain taps already waiting
    sync_worker(cfg, manifest, state, w)
    tg_call(token, "setWebhook", {
        "url": w[0] + "/tg", "secret_token": w[1], "max_connections": 1,
        "allowed_updates": ["callback_query", "message"]})


# ---------------------------------------------------------------- processing

def in_quiet_hours(cfg, section="group", default="20-7"):
    spec = cfg.get(section, "quiet_hours", fallback=default)
    try:
        start, end = (int(x) for x in spec.split("-"))
    except ValueError:
        return False
    h = now().hour
    return h >= start or h < end if start > end else start <= h < end


def remind_pending(cfg, token, owner, manifest, state):
    """Nudge the owner about previews still waiting for a decision. Each
    reminder is a fresh, notifying message with the same buttons."""
    if not cfg.getboolean("reminders", "enabled", fallback=True):
        return
    if in_quiet_hours(cfg, "reminders", "21-7"):
        return
    first = cfg.getfloat("reminders", "first_after_minutes", fallback=60) * 60
    repeat = cfg.getfloat("reminders", "repeat_minutes", fallback=180) * 60
    limit = cfg.getint("reminders", "max_reminders", fallback=8)
    t = now()
    for pfile in sorted(PENDING.glob("*.json")):
        batch = load_json(pfile, {})
        created = batch.get("created")
        if not created:
            continue
        age = (t - datetime.fromisoformat(created)).total_seconds()
        sent = batch.get("reminders", 0)
        last = batch.get("last_reminded")
        since = (t - datetime.fromisoformat(last)).total_seconds() if last else age
        if sent >= limit or age < first or (last and since < repeat):
            continue
        subj = ", ".join(manifest["processed"].get(i, {}).get("subject") or i
                         for i in batch.get("items", []))
        hours = age / 3600
        waited = f"{int(age // 60)} min" if hours < 1.5 else f"{hours:.0f} hours"
        batch_id = pfile.stem
        send_owner(token, owner, state,
                   f"⏰ <b>Still waiting for you</b>\n{esc(subj)}\n"
                   f"<i>Preview sent {waited} ago. Nothing has been posted.</i>",
                   "preview", batch=batch_id, buttons=[
                       {"text": "Post to channel", "callback_data": f"post:{batch_id}"},
                       {"text": "Skip", "callback_data": f"skip:{batch_id}"}])
        batch["reminders"] = sent + 1
        batch["last_reminded"] = stamp()
        save_json(pfile, batch)
        log.info("reminded about %s (#%d)", batch_id, sent + 1)


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
            soon_days = cfg.getint("group", "repeat_dates_within_days", fallback=2)
            dates = [d for d in item.get("dates") or []
                     if f"d|{event_key(d)}" not in posted_keys
                     or is_soon(d, soon_days)]
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
                hard = [r for r in reasons if r not in SOFT_REASONS]
                if hard:
                    withheld = "; ".join(reasons)
                else:
                    if reasons:
                        words = sorted({m.group(0).lower() for m in
                                        TOPIC_RE.finditer(html.unescape(block))})
                        rec["_review"] = ", ".join(words) or "a sensitive topic"
                    group_blocks.append(block)
                    group_items.append(sid)
                    rec["group_text"] = block
                    rec["_parts"] = render_group_parts(item, actions, dates)
                    for a in actions:
                        posted_keys.append(action_key(a))
                    for d in dates:
                        if f"d|{event_key(d)}" not in posted_keys:
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
    for d in (OUTBOX, SENT, FAILED, HOLD, PENDING, FEEDBACK.parent):
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

    if "--purge" in sys.argv:
        owner = cfg["telegram"].get("private_chat_id", "").strip()
        if not owner:
            sys.exit("private_chat_id is empty")
        if not take_lock():
            sys.exit("The poster is running; try again in a few minutes.")
        try:
            state = load_json(STATE, {})
            n = purge_owner_chat(cfg, token, owner, state)
            save_json(STATE, state)
        finally:
            LOCK.unlink(missing_ok=True)
        print(f"Cleared the bot chat (checked {n} message ids). Pending previews kept.")
        return

    if "--set-webhook" in sys.argv or "--delete-webhook" in sys.argv:
        w = worker_cfg(cfg)
        if "--set-webhook" in sys.argv and not w:
            sys.exit("Fill in [worker] url and secret in config.ini first.")
        if not take_lock():
            sys.exit("The poster is running; try again in a few minutes.")
        try:
            if "--set-webhook" in sys.argv:
                set_webhook(cfg, token, w)
                print("Taps and messages now go to the Worker.")
            else:
                tg_call(token, "deleteWebhook", {})
                print("Webhook removed. Now clear [worker] url in config.ini "
                      "so the poster reads taps itself again.")
        finally:
            LOCK.unlink(missing_ok=True)
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
    worker = worker_cfg(cfg)

    if owner and worker:
        try:
            sync_worker(cfg, manifest, state, worker)
        except Exception:
            log.exception("worker sync failed")
    elif owner:
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
        process_and_send(cfg, token, owner, review, manifest, state)
        save_json(STATE, state)

    if owner:
        try:
            remind_pending(cfg, token, owner, manifest, state)
        except Exception:
            log.exception("reminders failed")
        try:
            cleanup_owner_chat(cfg, token, owner, state)
        except Exception:
            log.exception("cleanup failed")
        save_json(STATE, state)
        if not worker:
            listen_for_approvals(cfg, manifest, state, started)


def process_and_send(cfg, token, owner, review, manifest, state):
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
                  group_blocks, group_items, private_blocks, state)
        except Exception:
            log.exception("failed on %s; will retry next cycle", path.name)
            for p in set(PENDING.glob("*.json")) - before:
                if worker_cfg(cfg):
                    try:
                        worker_call(worker_cfg(cfg), "DELETE", f"/pending/{p.stem}")
                    except Exception:
                        log.info("could not remove %s from the worker", p.stem)
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


def _send(cfg, token, owner, review, manifest, group_blocks, group_items, private_blocks,
          state):
    if private_blocks and owner:
        for msg in chunk_blocks(private_blocks, "🔒 <b>For you only</b>"):
            send_owner(token, owner, state, msg, "note")

    if not group_blocks:
        return
    auto_kinds = {k.strip() for k in cfg.get(
        "group", "auto_post_kinds", fallback="").split(",") if k.strip()}
    for block, sid in zip(group_blocks, group_items):
        msgs = chunk_blocks(manifest["processed"][sid].pop("_parts", None) or [block])
        kind = manifest["processed"][sid].get("kind")
        flagged = manifest["processed"][sid].pop("_review", None)
        if flagged:
            manifest["processed"][sid]["review_reason"] = flagged
            if not owner:
                manifest["processed"][sid]["group_status"] = "withheld"
                manifest["processed"][sid]["withheld_reason"] = f"needs review ({flagged})"
                continue
        if (review and kind not in auto_kinds) or (flagged and owner):
            batch_id = uuid.uuid4().hex[:12]
            save_json(PENDING / f"{batch_id}.json",
                      {"items": [sid], "messages": msgs, "created": stamp()})
            if worker_cfg(cfg):  # before any button exists
                worker_push_pending(worker_cfg(cfg), manifest, PENDING / f"{batch_id}.json")
            for i, msg in enumerate(msgs):
                last = i == len(msgs) - 1
                label = ""
                if i == 0:
                    label = "<i>Preview, not posted yet</i>\n"
                    if flagged:
                        label = (f"⚠️ <b>Check before posting.</b> It mentions: {esc(flagged)}. "
                                 f"Fine for a general notice, not if it's about one child.\n")
                send_owner(token, owner, state, label + msg, "preview", batch=batch_id, buttons=[
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
