#!/usr/bin/env python3
"""Fetch one school page through the compass2claude Browser Run Worker.

Used by the cloud digest task in place of a local browser.

    python scripts/browser_fetch.py <url>                   # prints Markdown
    python scripts/browser_fetch.py <url> --screenshot out.png

Reads BROWSER_FETCH_URL (the Worker's https URL) and BROWSER_FETCH_KEY from
the environment. Never pass the key on the command line or write it to a file.

Exit codes: 0 ok, 2 setup missing, 3 login required (Compass), 4 other error.
The URL is never printed, because Compass URLs carry a login token.
"""

import json
import os
import sys
import urllib.error
import urllib.request


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        return 2
    url = argv[1]
    shot = None
    if "--screenshot" in argv:
        i = argv.index("--screenshot")
        if i + 1 >= len(argv):
            print("error: --screenshot needs an output path", file=sys.stderr)
            return 2
        shot = argv[i + 1]

    endpoint = os.environ.get("BROWSER_FETCH_URL", "").strip()
    key = os.environ.get("BROWSER_FETCH_KEY", "").strip()
    if not endpoint or not key:
        print("error: BROWSER_FETCH_URL / BROWSER_FETCH_KEY not set", file=sys.stderr)
        return 2

    body = json.dumps({"url": url, "format": "screenshot" if shot else "markdown"}).encode()
    req = urllib.request.Request(endpoint, data=body, method="POST", headers={
        "authorization": f"Bearer {key}",
        "content-type": "application/json",
        "user-agent": "compass2claude-digest",
    })
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = r.read()
            ctype = r.headers.get("content-type", "")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode()).get("error", "")
        except Exception:
            err = ""
        if e.code == 403 and err == "login_required":
            print("error: login_required (Compass wants a sign-in)", file=sys.stderr)
            return 3
        print(f"error: HTTP {e.code} {err}".rstrip(), file=sys.stderr)
        return 4
    except Exception as e:  # network, timeout
        print(f"error: {type(e).__name__}", file=sys.stderr)
        return 4

    if shot:
        if not ctype.startswith("image/"):
            print("error: expected an image", file=sys.stderr)
            return 4
        with open(shot, "wb") as f:
            f.write(data)
        print(shot)
        return 0

    print(json.loads(data.decode())["markdown"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
