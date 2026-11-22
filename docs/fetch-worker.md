# Reading pages in cloud runs (Browser Run fetch Worker)

A cloud run of the digest has no browser, so it can't open "News:" items the way a laptop run does with Claude in Chrome. The Worker in `fetch-worker/` gives it one: it opens a page in Cloudflare Browser Run (headless Chrome) and returns the page as Markdown, or as a PNG for pages that are only images.

```
cloud task ──POST {url}──▶ fetch Worker ──quickAction──▶ Browser Run
           ◀── Markdown ───┘ (Compass links and tokens stripped)
```

This is separate from the approvals Worker in `worker/`, and you can use either one without the other.

## Guardrails

- **Key required.** Every request needs `Authorization: Bearer <FETCH_KEY>`, otherwise it gets 401.
- **School sites only.** It only fetches `https://` pages on the hosts in `ALLOWED_HOSTS` in `wrangler.toml`. Set it to your Compass site, your external newsletter host and your school website before deploying. Anything else gets 400.
- **No tokens out.** Compass links and `accessToken=` values are removed from every reply, so a parent's login token can't reach an outbox file.
- **Login pages are reported, not returned.** If Compass shows a sign-in page, the Worker returns 403 `login_required`. The digest then falls back to a private "open in Compass" note.
- **No URL logging.** Logs are off in `wrangler.toml`, and errors never echo the URL.

## Cost

Browser Run's free plan includes 10 minutes of browser time a day. A full newsletter is roughly 15 pages at a few seconds each, so normal use stays free.

## Setup

1. Deploy the Worker:

   ```
   cd fetch-worker
   npm test
   npx wrangler deploy
   ```

   Note the URL it prints, for example `https://compass2claude-fetch.<you>.workers.dev`.

2. Set the key. Generate a long random value, for example with `openssl rand -hex 32`, then run:

   ```
   npx wrangler secret put FETCH_KEY
   ```

3. Give the key to the cloud task as environment variables in the cloud environment's settings. Never put them in the repo or the task prompt.

   ```
   BROWSER_FETCH_URL=https://compass2claude-fetch.<you>.workers.dev
   BROWSER_FETCH_KEY=<the same key>
   ```

4. Check it works. Run this from the cloud environment, or anywhere with the variables set:

   ```
   python scripts/browser_fetch.py https://newsletters.naavi.com/i/<issue>
   ```

   Exit codes: `0` ok, `2` variables missing, `3` Compass wants a login, `4` other error.

## First thing to test

Check whether a Compass "View news item" link opens in a fresh browser with no sign-in. Run `scripts/browser_fetch.py` on the link from a recent email:

- If you get Markdown back, Compass-only News items work in the cloud.
- If you get exit code `3`, they don't. Newsletters still work, because the Naavi pages are public, but other News items become private "open in Compass" notes.
