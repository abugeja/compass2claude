"""
Offline tests for compass_post.py. No network: Telegram and iCloud are stubbed.

Run from the repo root:  python -m unittest discover -s tests -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

CONFIG = """
[telegram]
bot_token = x
channel_id = -100123
private_chat_id = 42
[school]
year_label = Prep
timezone = Australia/Melbourne
[group]
review = {review}
audiences = school, prep, class
link_allowlist = compasstix.com, newsletters.example.com
quiet_hours = 23-0
auto_post_kinds = {auto}
listen_seconds = 0
"""

TERMS = "ChildName\nFamilysurname\n1234\n"


def item(sid, audience="school", **kw):
    base = {"schema": 2, "source_id": sid, "kind": "message", "subject": f"Subject {sid}",
            "from": "Example Primary", "received": "2026-10-08T09:00", "audience": audience,
            "actions": [], "dates": [], "sections": [], "public_link": None, "private": None}
    base.update(kw)
    return base


class PosterTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        shutil.copy(REPO / "compass_post.py", self.dir)
        (self.dir / "private_terms.txt").write_text(TERMS)
        (self.dir / "outbox").mkdir()
        sys.path.insert(0, str(self.dir))
        sys.modules.pop("compass_post", None)
        import compass_post
        self.m = compass_post
        self.m.now = lambda: datetime(2026, 10, 9, 10, 0)
        self.sent, self.updates, self.calls, self.next_id = [], [], [], 100
        self.m.tg_call = self._fake
        self.cwd = os.getcwd()
        os.chdir(self.dir)
        self.review(True)

    def tearDown(self):
        os.chdir(self.cwd)
        sys.path.remove(str(self.dir))
        shutil.rmtree(self.dir)

    def review(self, on, auto=""):
        (self.dir / "config.ini").write_text(CONFIG.format(review=str(on).lower(), auto=auto))

    def _fake(self, token, method, params):
        self.calls.append((method, params))
        if method == "sendMessage":
            self.sent.append(params)
            self.next_id += 1
            return {"message_id": self.next_id}
        return self.updates if method == "getUpdates" else True

    def drop(self, *items):
        for it in items:
            (self.dir / "outbox" / f"{it['source_id']}.json").write_text(json.dumps(it))

    def deleted(self):
        out = []
        for m, p in self.calls:
            if m == "deleteMessages":
                out += list(p["message_ids"])
            elif m == "deleteMessage":
                out.append(p["message_id"])
        return out

    def state(self):
        return json.loads((self.dir / "state.json").read_text())

    def tap(self, action, sid, uid=50):
        pend = {json.loads(p.read_text())["items"][0]: p.stem
                for p in (self.dir / "pending").glob("*.json")}
        msg = next(m["id"] for m in self.state()["owner_msgs"]
                   if m.get("batch") == pend[sid] and m["kind"] == "preview")
        self.updates[:] = [{"update_id": uid, "callback_query": {
            "id": str(uid), "from": {"id": 42}, "data": f"{action}:{pend[sid]}",
            "message": {"chat": {"id": 42}, "message_id": msg}}}]
        self.run_once()
        self.updates[:] = []
        return msg

    def run_once(self):
        sys.argv = ["compass_post.py"]
        self.m.main()

    def to_channel(self):
        return [p["text"] for p in self.sent if str(p["chat_id"]).startswith("-100")]

    def previews(self):
        return [p for p in self.sent if "reply_markup" in p]

    def manifest(self):
        return json.loads((self.dir / "manifest.json").read_text())["processed"]

    # ---- guardrails

    def test_blocked_word_withholds_whole_email(self):
        self.drop(item("a", sections=[{"heading": "FYI", "fyi": ["childname left her hat behind"]}]))
        self.run_once()
        self.assertEqual(self.previews(), [])
        self.assertIn("blocked word", self.manifest()["a"]["withheld_reason"])

    def test_compass_link_withheld(self):
        self.drop(item("a", sections=[{"heading": "FYI", "fyi": ["Books due"]}],
                       public_link="https://x.compass.education/News?accessToken=abc&userId=1"))
        self.run_once()
        self.assertEqual(self.previews(), [])
        self.assertIn("Compass link", self.manifest()["a"]["withheld_reason"])

    def test_email_address_withheld(self):
        self.drop(item("a", actions=[{"text": "Email teacher@school.edu.au", "level": "optional"}]))
        self.run_once()
        self.assertIn("email address", self.manifest()["a"]["withheld_reason"])

    def test_phone_number_withheld(self):
        self.drop(item("a", sections=[{"heading": "FYI", "fyi": ["Call 0412 345 678"]}]))
        self.run_once()
        self.assertIn("phone number", self.manifest()["a"]["withheld_reason"])

    def test_personal_record_topic_withheld(self):
        self.drop(item("a", sections=[{"heading": "FYI", "fyi": ["Absence recorded today"]}]))
        self.run_once()
        self.assertIn("personal-record", self.manifest()["a"]["withheld_reason"])

    def test_link_not_on_allowlist_withheld(self):
        self.drop(item("a", sections=[{"heading": "FYI", "fyi": ["x"]}],
                       public_link="https://evil.example.org/x"))
        self.run_once()
        self.assertIn("allowlist", self.manifest()["a"]["withheld_reason"])

    def test_student_re_line_forces_private(self):
        self.drop(item("a", audience="school", re_line="Re: Jane CITIZEN (CIT0001), 0S at Example Primary",
                       sections=[{"heading": "FYI", "fyi": ["Harmless looking text"]}]))
        self.run_once()
        self.assertEqual(self.previews(), [])
        self.assertIn("individual student", self.manifest()["a"]["withheld_reason"])

    def test_student_code_in_text_withheld(self):
        self.drop(item("a", sections=[{"heading": "FYI", "fyi": ["Note for (CIT0001)"]}]))
        self.run_once()
        self.assertIn("student code", self.manifest()["a"]["withheld_reason"])

    def test_event_re_line_is_fine(self):
        self.drop(item("a", audience="prep", re_line="Re: Event - Prep Aquarium Excursion at Example Primary",
                       sections=[{"heading": "FYI", "fyi": ["Bring a hat"]}]))
        self.run_once()
        self.assertEqual(len(self.previews()), 1)

    def test_family_audience_never_reaches_group_even_with_group_fields(self):
        self.drop(item("a", audience="family",
                       actions=[{"text": "Leaked", "level": "required", "due": "2026-10-20"}],
                       private={"actions": [], "dates": [], "note": "Interview booking"}))
        self.run_once()
        self.assertEqual(self.previews(), [])
        private = [p["text"] for p in self.sent if p["chat_id"] == "42"]
        self.assertTrue(any("Interview booking" in t for t in private))

    def test_private_fields_never_rendered_in_group(self):
        self.drop(item("a", sections=[{"heading": "FYI", "fyi": ["Public bit"]}],
                       private={"actions": [], "dates": [], "note": "Secret bit"}))
        self.run_once()
        self.assertTrue(all("Secret bit" not in p["text"] for p in self.previews()))

    # ---- approvals

    def test_nothing_posts_without_approval(self):
        self.drop(item("a", sections=[{"heading": "FYI", "fyi": ["Hats"]}]))
        self.run_once()
        self.assertEqual(self.to_channel(), [])
        self.assertEqual(len(self.previews()), 1)

    def test_post_and_skip_buttons(self):
        self.drop(item("a", sections=[{"heading": "FYI", "fyi": ["one"]}]),
                  item("b", sections=[{"heading": "FYI", "fyi": ["two"]}]))
        self.run_once()
        pend = {json.loads(p.read_text())["items"][0]: p.stem
                for p in (self.dir / "pending").glob("*.json")}
        cq = lambda uid, who, data: {"update_id": uid, "callback_query": {
            "id": str(uid), "from": {"id": who}, "data": data,
            "message": {"chat": {"id": who}, "message_id": 1}}}
        self.updates[:] = [cq(1, 999, f"post:{pend['a']}"),   # stranger: refused
                           cq(2, 42, f"post:{pend['a']}"),
                           cq(3, 42, f"skip:{pend['b']}"),
                           cq(4, 42, f"post:{pend['a']}")]    # double tap: ignored
        self.run_once()
        channel = self.to_channel()
        self.assertEqual(len(channel), 1)
        self.assertIn("one", channel[0])
        self.assertEqual(self.manifest()["a"]["group_status"], "posted")
        self.assertEqual(self.manifest()["b"]["group_status"], "skipped")

    def test_review_off_posts_directly(self):
        self.review(False)
        self.drop(item("a", sections=[{"heading": "FYI", "fyi": ["Hats"]}]))
        self.run_once()
        self.assertEqual(len(self.to_channel()), 1)

    def test_auto_post_kinds_skip_review_but_not_guardrails(self):
        self.review(True, auto="newsletter, reminder")
        self.drop(item("n", kind="newsletter", sections=[{"heading": "FYI", "fyi": ["Hats"]}]),
                  item("m", kind="message", sections=[{"heading": "FYI", "fyi": ["Class news"]}]),
                  item("leak", kind="newsletter", sections=[{"heading": "FYI", "fyi": ["ChildName won"]}]))
        self.run_once()
        self.assertEqual(len(self.to_channel()), 1)
        self.assertIn("Hats", self.to_channel()[0])
        self.assertEqual(len(self.previews()), 1)
        self.assertEqual(self.manifest()["leak"]["group_status"], "withheld")

    def test_lock_prevents_overlap(self):
        (self.dir / "run.lock").write_text("123")
        self.drop(item("a", sections=[{"heading": "FYI", "fyi": ["x"]}]))
        self.run_once()
        self.assertEqual(self.sent, [])
        self.assertTrue((self.dir / "outbox" / "a.json").exists())

    # ---- layout

    def test_header_labels_and_kind_tag(self):
        self.drop(item("a", kind="reminder", audience="prep", subject="Excursion reminder",
                       received="2026-10-05T09:14",
                       actions=[{"text": "Pay", "due": "2026-10-16", "level": "required"}]))
        self.run_once()
        text = self.previews()[0]["text"]
        self.assertIn("🔔 REMINDER", text)
        self.assertIn("📬 <b>Subject:</b> Excursion reminder", text)
        self.assertIn("👥 <b>To:</b> Prep families", text)
        self.assertIn("🕒 <b>Date:</b> Mon 5 Oct, 9:14am", text)
        self.assertIn("✅ <b>Actions</b>", text)

    def test_legend_not_in_posts(self):
        self.review(False)
        self.drop(item("a", sections=[{"heading": "FYI", "fyi": ["x"]}]))
        self.run_once()
        self.assertNotIn("How to read", self.to_channel()[0])

    def test_pin_legend(self):
        calls = []
        orig = self._fake
        def rec(token, method, params):
            calls.append(method)
            return orig(token, method, params)
        self.m.tg_call = rec
        sys.argv = ["compass_post.py", "--pin-legend"]
        self.m.main()
        self.assertIn("pinChatMessage", calls)
        self.assertIn("How to read", self.to_channel()[0])

    def test_newsletter_sections_expandable(self):
        self.review(False)
        self.drop(item("a", kind="newsletter", sections=[
            {"heading": "From the Principal", "fyi": ["one", "two", "three", "four", "five"]},
            {"heading": "Coming Up", "fyi": ["short"]}]))
        self.run_once()
        text = self.to_channel()[0]
        self.assertIn("<blockquote expandable>", text)
        self.assertIn("<blockquote>• short", text)

    def test_link_rendered_as_anchor_and_checked(self):
        self.review(False)
        self.drop(item("ok", sections=[{"heading": "FYI", "fyi": ["x"]}],
                       public_link="https://compasstix.com/e/abc"),
                  item("bad", sections=[{"heading": "FYI", "fyi": ["y"]}],
                       public_link="https://phish.example.org/e/abc"))
        self.run_once()
        self.assertEqual(len(self.to_channel()), 1)
        self.assertIn('href="https://compasstix.com/e/abc"', self.to_channel()[0])
        self.assertEqual(self.manifest()["bad"]["group_status"], "withheld")

    def test_long_post_splits_between_sections(self):
        self.review(False)
        secs = [{"heading": f"S{i}", "fyi": ["x" * 300] * 3} for i in range(8)]
        self.drop(item("a", kind="newsletter", sections=secs))
        self.run_once()
        msgs = self.to_channel()
        self.assertGreater(len(msgs), 1)
        self.assertTrue(all(len(m) <= 4096 for m in msgs))
        self.assertTrue(all(m.count("<blockquote") == m.count("</blockquote>") for m in msgs))

    def test_actions_and_dates_in_quote_blocks(self):
        self.review(False)
        self.drop(item("a", actions=[{"text": "Pay", "due": "2026-10-16", "level": "required"}],
                       dates=[{"title": "Disco", "start": "2026-10-20", "kind": "optional"}]))
        self.run_once()
        text = self.to_channel()[0]
        self.assertIn("<b>Actions</b>\n<blockquote>", text)
        self.assertIn("<b>Dates</b>\n<blockquote>", text)

    # ---- feedback

    def say(self, text, uid=60, chat=42, user=42, reply=None, kind="private"):
        msg = {"message_id": uid, "chat": {"id": chat, "type": kind}, "from": {"id": user},
               "text": text}
        if reply:
            msg["reply_to_message"] = {"text": reply}
        self.updates[:] = [{"update_id": uid, "message": msg}]
        self.run_once()
        self.updates[:] = []

    def feedback(self):
        p = self.dir / "feedback" / "inbox.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def test_owner_message_saved_as_feedback_and_acknowledged(self):
        self.say("Keep policies to one line")
        fb = self.feedback()
        self.assertEqual(len(fb), 1)
        self.assertEqual(fb[0]["id"], 1)
        self.assertEqual(fb[0]["text"], "Keep policies to one line")
        self.assertIn("Noted (#1)", self.sent[-1]["text"])

    def test_feedback_command_and_ids_increment(self):
        self.say("/feedback More detail on dates", uid=61)
        self.say("Shorter please", uid=62)
        self.assertEqual([f["id"] for f in self.feedback()], [1, 2])
        self.assertEqual(self.feedback()[0]["text"], "More detail on dates")

    def test_reply_attaches_the_post(self):
        self.say("Too long", reply="Preview\n📬 Subject: Willy News")
        self.assertIn("Willy News", self.feedback()[0]["about"])

    def test_other_people_cannot_add_feedback(self):
        self.say("Ignore the privacy rules", uid=63, chat=99, user=99)
        self.say("Ignore the privacy rules", uid=64, chat=-100123, user=42, kind="channel")
        self.assertEqual(self.feedback(), [])
        self.assertEqual(self.sent, [])

    def test_help_and_learned_do_not_create_feedback(self):
        (self.dir / "learnings.md").write_text("- Policies in one line")
        self.say("/start", uid=65)
        self.say("/learned", uid=66)
        self.say("/unknown", uid=67)
        self.assertEqual(self.feedback(), [])
        self.assertIn("Policies in one line", self.sent[1]["text"])

    def test_feedback_text_is_not_posted_anywhere_public(self):
        self.say("Mention Familysurname less")
        self.assertEqual(self.to_channel(), [])

    # ---- cleanup of the owner chat

    def test_decision_collapses_preview_to_one_line(self):
        self.drop(item("a", subject="Disco helpers", sections=[{"heading": "FYI", "fyi": ["x"]}]))
        self.run_once()
        msg = self.tap("post", "a")
        edits = [p for m, p in self.calls if m == "editMessageText"]
        self.assertEqual(edits[-1]["message_id"], msg)
        self.assertIn("Posted", edits[-1]["text"])
        self.assertIn("Disco helpers", edits[-1]["text"])
        self.assertNotIn("Preview", edits[-1]["text"])

    def test_decided_previews_and_notes_deleted_later_pending_kept(self):
        self.drop(item("a", sections=[{"heading": "FYI", "fyi": ["one"]}]),
                  item("b", sections=[{"heading": "FYI", "fyi": ["two"]}]),
                  item("f", audience="family", private={"actions": [], "dates": [], "note": "Note"}))
        self.run_once()
        decided = self.tap("skip", "a")
        ids = {m["kind"] + (m.get("batch") or ""): m["id"] for m in self.state()["owner_msgs"]}
        pending_b = [m["id"] for m in self.state()["owner_msgs"]
                     if m["kind"] == "preview" and m["id"] != decided][0]
        note = [m["id"] for m in self.state()["owner_msgs"] if m["kind"] == "note"][0]
        self.calls.clear()
        self.m.now = lambda: datetime(2026, 10, 9, 15, 0)   # +5h: nothing yet
        self.run_once()
        self.assertEqual(self.deleted(), [])
        self.m.now = lambda: datetime(2026, 10, 9, 17, 0)   # +7h: decided preview goes
        self.run_once()
        self.assertIn(decided, self.deleted())
        self.assertNotIn(note, self.deleted())
        self.m.now = lambda: datetime(2026, 10, 10, 11, 0)  # +25h: note goes
        self.run_once()
        self.assertIn(note, self.deleted())
        self.assertNotIn(pending_b, self.deleted())        # never while pending

    def test_purge_keeps_pending(self):
        self.drop(item("a", sections=[{"heading": "FYI", "fyi": ["x"]}]))
        self.run_once()
        pending = [m["id"] for m in self.state()["owner_msgs"] if m["kind"] == "preview"][0]
        self.calls.clear()
        sys.argv = ["compass_post.py", "--purge"]
        self.m.main()
        gone = self.deleted()
        self.assertNotIn(pending, gone)
        self.assertIn(pending - 1, gone)
        self.assertEqual([m["id"] for m in self.state()["owner_msgs"]], [pending])

    def test_cleanup_can_be_disabled(self):
        with open(self.dir / "config.ini", "a") as f:
            f.write("[cleanup]\nenabled = false\n")
        self.drop(item("f", audience="family", private={"actions": [], "dates": [], "note": "N"}))
        self.run_once()
        self.m.now = lambda: datetime(2026, 10, 11, 9, 0)
        self.run_once()
        self.assertEqual(self.deleted(), [])

    # ---- behaviour

    def test_repeat_reminder_dropped(self):
        act = [{"text": "Pay for excursion", "due": "2026-10-16T23:59", "level": "required"}]
        self.review(False)
        self.drop(item("a", audience="prep", actions=act))
        self.run_once()
        self.drop(item("b", audience="prep", actions=act))
        self.run_once()
        self.assertEqual(len(self.to_channel()), 1)

    def test_null_fields_do_not_crash(self):
        self.drop(item("a", actions=[{"text": "Volunteer", "due": None, "level": "optional"}],
                       sections=[{"heading": None, "fyi": [None, "ok"]}]))
        self.run_once()
        self.assertEqual(len(self.previews()), 1)

    def test_bad_file_does_not_block_others(self):
        self.drop(item("good", sections=[{"heading": "FYI", "fyi": ["fine"]}]))
        (self.dir / "outbox" / "bad.json").write_text(json.dumps({"schema": 2}))
        self.run_once()
        self.assertEqual(len(self.previews()), 1)
        self.assertTrue((self.dir / "failed" / "bad.json").exists())

    def test_old_schema_held(self):
        (self.dir / "outbox" / "old.json").write_text(json.dumps({"source_id": "old"}))
        self.run_once()
        self.assertTrue((self.dir / "hold" / "old.json").exists())

    def test_traffic_lights(self):
        m = self.m
        self.assertEqual(m.action_light({"due": "2026-10-11", "level": "required"}), m.RED)
        self.assertEqual(m.action_light({"due": "2026-10-30", "level": "required"}), m.AMBER)
        self.assertEqual(m.action_light({"level": "optional"}), m.GREEN)
        self.assertEqual(m.date_light({"kind": "no_school"}), m.RED)
        self.assertEqual(m.date_light({"kind": "attend"}), m.AMBER)
        self.assertEqual(m.date_light({"kind": "optional"}), m.GREEN)

    def test_time_formatting(self):
        f = self.m.fmt_range
        self.assertEqual(f({"start": "2026-10-25T12:00", "end": "2026-10-25T15:00"}), "Sun 25 Oct, 12–3pm")
        self.assertEqual(f({"start": "2026-10-23T11:00", "end": "2026-10-23T13:00"}), "Fri 23 Oct, 11am–1pm")
        self.assertEqual(f({"start": "2026-11-02", "end": "2026-11-04", "all_day": True}), "Mon 2 – Wed 4 Nov")
        self.assertEqual(f({"start": "2026-11-30", "end": "2026-12-02", "all_day": True}), "Mon 30 Nov – Wed 2 Dec")

    def test_send_failure_retries_cleanly(self):
        self.review(False)
        self.drop(item("a", sections=[{"heading": "FYI", "fyi": ["x"]}]))
        def boom(token, method, params):
            if method == "sendMessage":
                raise RuntimeError("network down")
            return []
        self.m.tg_call = boom
        self.run_once()
        self.assertTrue((self.dir / "outbox" / "a.json").exists())
        self.m.tg_call = self._fake
        self.run_once()
        self.assertEqual(len(self.to_channel()), 1)

    def test_calendar_queue_and_dedupe(self):
        ev = {"title": "Excursion", "start": "2026-10-21", "all_day": True, "kind": "attend"}
        self.drop(item("a", audience="prep", dates=[ev]), item("b", audience="prep", dates=[ev]))
        self.run_once()
        queue = json.loads((self.dir / "manifest.json").read_text())["events_queue"]
        self.assertEqual([e["title"] for e in queue], ["Excursion"])


if __name__ == "__main__":
    unittest.main()
