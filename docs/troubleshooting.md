# Troubleshooting

Check `compass_post.log` first.

| Symptom | Likely cause | Fix |
|---|---|---|
| Claude run says it can't reach the folder | Folder access not granted for the task | Re-add the folder in the scheduled task's settings in the desktop app |
| Claude run finishes in seconds with nothing written | As above, or the computer was asleep or the app closed | Keep the desktop app open and the computer awake at run times |
| Files sit in `outbox/` | Poster not scheduled, quiet hours, or `private_chat_id` empty while `review = true` | Check Task Scheduler and the log. Fill in `private_chat_id` |
| Buttons do nothing | Poster not running, so taps are only read on the next cycle | Run `python compass_post.py`. While posts await approval it listens for up to `listen_seconds` |
| Button says "Not allowed" | Tapped from a different Telegram account | Only `private_chat_id` can approve |
| `Telegram sendMessage error: chat not found` | Wrong `channel_id`, or bot not a channel admin | Rerun `--whoami`, and make the bot an admin with post rights |
| Something was withheld unexpectedly | A blocked word or pattern matched (see the private note) | Check `withheld_reason` in `manifest.json`. If it's a false positive, post it by hand or adjust `private_terms.txt` |
| Files in `hold/` | Old or unknown format | Delete them, or ask Claude to rewrite them in schema 2 |
| Files in `failed/` | Unreadable JSON or a missing required field | Check the log for the error |
| Calendar events not appearing | `[icloud]` not configured, or wrong app-specific password or calendar name | Fix `config.ini`. The queue is kept and retried |
| Old messages in the bot chat | Over 48 hours old, or sent by you, so the bot can't delete them | Delete them by hand. Newer ones clear automatically, or run `--purge` |
| Same post twice | Shouldn't happen. Each email is committed only after its sends succeed | Check the log around that time and open an issue |

## Re-running an email

Delete its entry under `processed` in `manifest.json` and move its file from `sent/` back to `outbox/`. If its dates were already posted, remove the matching entries from `group_keys` too.

## Running the tests

```
python -m unittest discover -s tests -v
```

The tests are offline. Telegram and iCloud are stubbed.
