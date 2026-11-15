# Instant approvals with a Cloudflare Worker (optional)

By default the poster reads your button taps itself, so a tap only acts while the poster is running. With the Worker in `worker/`, Telegram sends each tap straight to Cloudflare and the post reaches the channel within a second, whether or not the poster is running. This is what makes a slow schedule (for example every 30 minutes on GitHub Actions) practical.

```
poster ──upload preview batch──▶ Worker (KV) ◀──tap / message── Telegram ◀── you
poster ◀──decisions, feedback───┘     │
                                      └──post──▶ channel
```

## What moves and what doesn't

| | Without the Worker | With the Worker |
|---|---|---|
| Guardrails, rendering, previews, reminders, calendar | Poster | Poster (unchanged) |
| Acting on **Post** / **Skip** | Poster, when it next runs | Worker, at once |
| Collapsing the preview to one line | Poster | Worker |
| Saving feedback you message the bot | Poster | Worker queues it and replies "Noted"; the poster writes it to `feedback/inbox.jsonl` on its next run |
| `/learned` | Poster reads `learnings.md` | Worker answers from a copy the poster uploads whenever `learnings.md` changes |
| Updating `manifest.json`, deleting reminder copies | Poster | Poster, on its next run |

The Worker never decides what may be posted. It can only post the exact messages the poster uploaded for a batch, and only when your Telegram account taps **Post** on it.

## What is stored on Cloudflare

In one KV namespace, on your own Cloudflare account:

- The text of each preview awaiting your decision. This is the group post, which has already passed every guardrail. It is deleted when you decide, or after 21 days.
- Your decision, feedback messages and the ids of the bot's replies, until the poster collects them (normally minutes).
- A copy of `learnings.md`.

"For you only" notes, family-only content and the manifest never go to the Worker. The bot token, your chat id and the channel id are stored as encrypted Worker secrets.

## Setup

You need Node.js and a free Cloudflare account. Run these from the `worker/` folder.

1. Sign in, create the storage namespace and put the id it prints into `wrangler.toml` (in place of `REPLACE_WITH_YOUR_KV_NAMESPACE_ID`). Then deploy. The first deploy prints the Worker's address (`https://compass2claude-approvals.<your-subdomain>.workers.dev`).

   ```
   npx wrangler login
   npx wrangler kv namespace create compass2claude-approvals
   npx wrangler deploy
   ```

   To keep your own settings out of this folder, copy `wrangler.toml` somewhere private, point its `main` at `worker/src/index.js`, and add `--config path/to/wrangler.toml` to each `wrangler` command.

2. Make a shared secret (letters and digits only) and keep it for step 4:

   ```
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

3. Set the four secrets. Each command asks you to paste the value.

   ```
   npx wrangler secret put BOT_TOKEN
   npx wrangler secret put OWNER_ID
   npx wrangler secret put CHANNEL_ID
   npx wrangler secret put API_SECRET
   ```

   `BOT_TOKEN`, `OWNER_ID` and `CHANNEL_ID` are `bot_token`, `private_chat_id` and `channel_id` from `config.ini`. `API_SECRET` is the value from step 2.

4. Add to `config.ini`:

   ```ini
   [worker]
   url = https://compass2claude-approvals.<your-subdomain>.workers.dev
   secret = <the value from step 2>
   ```

5. Switch Telegram over, from the project folder:

   ```
   python compass_post.py --set-webhook
   ```

   This handles any taps already waiting, uploads the previews still pending and your learnings, then points Telegram at the Worker.

From here `listen_seconds` is ignored: each poster run finishes in seconds.

## Running the poster without a computer

The poster keeps all its state in files, so it can run on a timer anywhere, for example GitHub Actions. Keep the data in a separate private repository and set `COMPASS_DATA_DIR` to that folder: the poster then reads `config.ini` and `private_terms.txt` and keeps `manifest.json`, `state.json` and the work folders there instead of next to the script. Write the two settings files from the runner's secrets at the start of each run, and commit the changed state files at the end. Never put that data in a public repository.

## Going back

```
python compass_post.py --delete-webhook
```

Then remove `url` from `[worker]` in `config.ini`. Do both: Telegram delivers taps either to the Worker or to the poster, never both. Previews already in your chat keep working either way.

## Tests

```
node --test worker/test/worker.test.mjs
```
