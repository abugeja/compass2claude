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
        self.sent, self.updates = [], []
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
        if method == "sendMessage":
            self.sent.append(params)
        return self.updates if method == "getUpdates" else {"message_id": 1}

    def drop(self, *items):
        for it in items:
            (self.dir / "outbox" / f"{it['source_id']}.json").write_text(json.dumps(it))

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
        self.assertEqual(f({"start": "2026-11-02", "end": "2026-11-04", "all_day": True}), "Mon 2 Nov – Wed 4 Nov")

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
