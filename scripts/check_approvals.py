#!/usr/bin/env python3
"""Diagnose "Post to channel does nothing" when the approvals Worker is in use.

    python -X utf8 scripts/check_approvals.py

Reads config.ini, asks Telegram where it sends taps (getWebhookInfo) and asks
the Worker what it holds (/sync). Prints no tokens or secrets.
"""
import configparser
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def call(url, headers=None, data=None):
    # Cloudflare blocks Python's default User-Agent (error 1010).
    req = urllib.request.Request(url, headers={"User-Agent": "compass2claude", **(headers or {})}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def main():
    cfg = configparser.ConfigParser()
    if not cfg.read(ROOT / "config.ini", encoding="utf-8"):
        sys.exit("config.ini not found next to compass_post.py")
    token = cfg["telegram"]["bot_token"].strip()
    owner = cfg["telegram"].get("private_chat_id", "").strip()
    wurl = cfg.get("worker", "url", fallback="").strip().rstrip("/")
    wsecret = cfg.get("worker", "secret", fallback="").strip()
    problems = []

    print("== Telegram webhook")
    st, body = call(f"https://api.telegram.org/bot{token}/getWebhookInfo")
    info = json.loads(body).get("result", {}) if st == 200 else {}
    if st != 200:
        problems.append(f"Telegram getWebhookInfo failed ({st}): bot_token in config.ini may be wrong")
        print("  failed:", st, body[:200])
    else:
        hook = info.get("url", "")
        print("  url:            ", hook or "(none)")
        print("  pending updates:", info.get("pending_update_count"))
        if info.get("last_error_date"):
            ago = int(time.time() - info["last_error_date"])
            print("  last error:     ", info.get("last_error_message"), f"({ago // 60} min ago)")
        print("  allowed updates:", info.get("allowed_updates") or "(all)")
        if not hook:
            problems.append("Telegram has no webhook, and the poster doesn't poll while [worker] is set, "
                            "so taps go nowhere. Fix: python compass_post.py --set-webhook")
        elif wurl and not hook.startswith(wurl + "/tg"):
            problems.append(f"Webhook points somewhere else ({hook.split('?')[0]}), not {wurl}/tg. "
                            "Fix: python compass_post.py --set-webhook")
        err = (info.get("last_error_message") or "").lower()
        if "403" in err:
            problems.append("Worker rejects Telegram (403): the Worker's API_SECRET doesn't match "
                            "[worker] secret. Re-set API_SECRET, then --set-webhook")
        elif "503" in err:
            problems.append("Worker says 'not configured' (503): one of BOT_TOKEN, OWNER_ID, CHANNEL_ID, "
                            "API_SECRET is missing on the Worker")
        elif err:
            problems.append(f"Telegram can't deliver taps to the Worker: {info.get('last_error_message')}")
        if hook and not err:
            print("  -> Telegram is delivering taps to the Worker without errors.")
        allowed = info.get("allowed_updates")
        if allowed and "callback_query" not in allowed:
            problems.append("Webhook excludes button taps (callback_query). Fix: --set-webhook")

    print("\n== Worker")
    if not wurl or not wsecret:
        problems.append("[worker] url/secret missing in config.ini")
        print("  not configured in config.ini")
    else:
        st, body = call(wurl + "/sync", {"authorization": f"Bearer {wsecret}"})
        if st == 200:
            d = json.loads(body)
            on = set(d.get("pending", []))
            print("  previews held:  ", sorted(on) or "(none)")
            print("  decisions:      ", [(x.get("batch"), x.get("outcome"), x.get("error")) for x in d.get("decisions", [])] or "(none)")
            local = sorted(p.stem for p in (ROOT / "pending").glob("*.json"))
            print("  pending/ here:  ", local or "(none)")
            missing = [b for b in local if b not in on]
            if missing:
                problems.append(f"Previews {missing} are not on the Worker, so tapping them says 'Already handled'. "
                                "The next poster run re-uploads them.")
        else:
            print("  /sync failed:", st, body[:200])
            if st == 403:
                problems.append("Worker refused [worker] secret: API_SECRET on the Worker differs from config.ini")
            elif "1010" in body:
                problems.append("Cloudflare blocked this check's User-Agent (1010); not a setup problem")
            elif st == 503:
                problems.append("Worker is missing secrets (BOT_TOKEN, OWNER_ID, CHANNEL_ID, API_SECRET)")
            else:
                problems.append(f"Worker unreachable at {wurl} ({st})")

    print(f"\n  config.ini private_chat_id: {owner} (the Worker's OWNER_ID must equal this)")
    print("\n== Verdict")
    for p in problems or ["Telegram and the Worker look connected. Next: run "
                          "`npx.cmd wrangler tail compass2claude-approvals` in worker/, tap Post, "
                          "and read the log line (e.g. 'tap from ... refused: not OWNER_ID')."]:
        print(" -", p)


if __name__ == "__main__":
    main()
