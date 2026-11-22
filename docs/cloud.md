# Cloud setup

In the cloud setup no computer has to be on. Three hosted pieces replace the laptop:

| Piece | Runs on | Does |
|---|---|---|
| Claude scheduled task | Anthropic's cloud | Reads Gmail, reads News pages through the fetch Worker, writes `outbox/` files and pushes them |
| Poster workflow (`.github/workflows/poster.yml`) | GitHub Actions in your **private** data repo | Runs `compass_post.py`: guardrails, previews, reminders, calendar. Commits the state back |
| Approvals Worker (`worker/`) | Cloudflare | Takes your Post / Skip taps and posts at once |
| Fetch Worker (`fetch-worker/`) | Cloudflare Browser Run | Opens Compass and newsletter pages for the Claude task, which has no browser |

The code is the same as the laptop setup. What changes is where it runs and where the state lives: in the cloud setup, `manifest.json`, `state.json`, `learnings.md`, `feedback/`, `school.md` and the work folders are committed to a private repo, because nothing else persists between runs.

## 1. Make a private data repo

Never put family data in a public repo. Create a new **private** repo, for example `you/yourschool-news`, and copy this repo's code into it:

```powershell
git clone https://github.com/abugeja/compass2claude yourschool-news
cd yourschool-news
git remote set-url origin https://github.com/<you>/yourschool-news.git
```

Then make `.gitignore` keep only the secrets out. Replace its contents with:

```
config.ini
private_terms.txt
.dev.vars
*.log
*.tmp
*.attempts
run.lock
manifest.bak-*.json
__pycache__/
*.pyc
.pytest_cache/
.venv/
venv/
node_modules/
.wrangler/
```

Fill in `school.md`, `config.ini` and `private_terms.txt` as in [setup.md](setup.md). Commit `school.md`, but never `config.ini` or `private_terms.txt`. Push.

## 2. GitHub secrets and the poster

In the data repo, open Settings → Secrets and variables → Actions:

| Kind | Name | Value |
|---|---|---|
| Secret | `CONFIG_INI` | The whole contents of `config.ini`, including `[worker]` once you have it |
| Secret | `PRIVATE_TERMS` | The whole contents of `private_terms.txt` |
| Variable | `POSTER_ENABLED` | `true` |

The workflow does nothing until `POSTER_ENABLED` is `true`, and refuses to run without either secret, so nothing can post without the blocked-words list. When you change `config.ini` or `private_terms.txt`, update the matching secret.

The schedule in `poster.yml` is in UTC. The default, `*/30 19-23,0-11 * * *`, is every 30 minutes from 6am to about 10pm in Melbourne. Change it for your timezone.

Only one poster may run at a time. If you used the laptop setup before, disable its Task Scheduler (or cron) entry, or every preview is sent twice.

## 3. Approvals Worker

Without it, taps are only read while the poster runs, which is every 30 minutes here. Set it up as in [worker.md](worker.md), then:

```powershell
python compass_post.py --set-webhook
```

Add the `[worker]` section to `config.ini` and update the `CONFIG_INI` secret.

## 4. Fetch Worker

Set `ALLOWED_HOSTS` in `fetch-worker/wrangler.toml` to your Compass site, your newsletter host and your school website, then deploy it and set its key as in [fetch-worker.md](fetch-worker.md).

## 5. The Claude scheduled task

Create a cloud scheduled task with the "Cloud setup" prompt in [scheduled-task.md](scheduled-task.md). Give it:

- the data repo, with permission to push to `main`
- the environment variables `BROWSER_FETCH_URL` and `BROWSER_FETCH_KEY`
- the Gmail connector

Press **Run now** once. Its reply should say it pulled, read the emails and pushed. A minute later the poster workflow runs and the previews arrive in Telegram. Then switch the task on.

## Checking on it

| Question | Where |
|---|---|
| Did the Claude run work? | The task's run history in Claude |
| Did the poster run? What did it do? | GitHub → Actions → Poster → the run → "Show the log" |
| Why don't taps work? | `python -X utf8 scripts/check_approvals.py` on any computer with `config.ini` |
| What was posted, and why was something withheld? | `manifest.json` in the data repo |
