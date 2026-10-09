# Setup

This walkthrough uses Windows and PowerShell. Notes for macOS and Linux are at the end.

## 1. Get the code

```powershell
cd $env:USERPROFILE\Projects
git clone https://github.com/abugeja/compass2claude
cd compass2claude
python -m pip install --user -r requirements.txt
```

## 2. Fill in your local files

```powershell
copy config.example.ini config.ini
copy school.example.md school.md
copy private_terms.example.txt private_terms.txt
```

- **school.md**: school name, Compass sender address, your year level, any external newsletter site, and allowed link domains.
- **private_terms.txt**: your child's name and nicknames, your surname, your and your partner's first names, other children's names you know, and your Compass user ID and student code. One per line.
- **config.ini**: filled in over the next steps.

These three files are git-ignored. Keep them that way.

## 3. Telegram bot and channel

1. In Telegram, message **@BotFather** and send `/newbot`. Pick a name and a username ending in `bot`. Copy the token into `bot_token` in `config.ini`.
2. Create a **channel** (New Channel). Private is a good default, and you share the invite link with parents.
3. In the channel, open Administrators, add your bot, and allow it to post messages.
4. Post anything in the channel, and send the bot a direct message.
5. Run:
   ```powershell
   python compass_post.py --whoami
   ```
   You'll see lines like `-100… channel <name>` and `123… private <you>`. Put the first number in `channel_id` and the second in `private_chat_id`.

If you ever paste the token somewhere you shouldn't, send `/revoke` to @BotFather and update `config.ini`.

## 4. Schedule the poster

Run it every 10 minutes:

```powershell
$here = (Get-Location).Path
$action  = New-ScheduledTaskAction -Execute (Get-Command pythonw.exe).Source -Argument "`"$here\compass_post.py`"" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName "compass2claude poster" -Action $action -Trigger $trigger -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew)
```

To remove it: `Unregister-ScheduledTask -TaskName "compass2claude poster"`.

## 5. Claude

1. **Connect Gmail** in Claude's connector settings, using the account that receives Compass notifications.
2. **Install Claude in Chrome**, and sign in to Compass in that Chrome.
3. **Desktop app:** install the Claude desktop app on this computer, sign in, and keep it running.
4. **Create the scheduled task** from a Claude conversation linked to this computer. Ask Claude to create a scheduled task that requires this computer, attaches this folder, runs on weekdays every two hours (for example 8am to 6pm), and uses the prompt in [scheduled-task.md](scheduled-task.md) with your folder path.
5. **Grant folder access** when the desktop app asks. If a run reports it can't reach the folder, re-add the folder in the task's settings in the desktop app.

## 6. Test

```powershell
copy samples\*.json outbox\
python compass_post.py
```

You should get two previews in Telegram. Tap **Post to channel** on TEST 1 and **Skip** on TEST 2. Then delete TEST 1 from the channel.

Then trigger the Claude task once from the app and watch the previews arrive.

## 7. Optional: iCloud calendar

1. At account.apple.com, go to Sign-In and Security, then App-Specific Passwords, and create one.
2. Uncomment `[icloud]` in `config.ini` and fill in your Apple ID, that password and the calendar name.
3. Queued events are added on the next cycle. Each event is added once, even if several emails mention it.

## macOS and Linux

- Use `python3` and `pip3`.
- Schedule with cron (`*/10 * * * * cd /path/to/compass2claude && /usr/bin/python3 compass_post.py`) or a launchd agent.
- Everything else is the same.
