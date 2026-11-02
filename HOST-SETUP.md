# Running the Compass on HostAfrica (shared hosting, cron every minute)

Shared hosting can't keep a program running, but a cron job can start the bot every minute.
Each run takes a second or two, reads your messages, replies and exits.

## Steps
1. Log in to your HostAfrica control panel (cPanel or DirectAdmin).
2. In **File Manager**, create a folder called `compass` in your home folder, NOT inside
   `public_html` (so nobody can open your files from a web address).
3. Upload `bot.py` and `host_check.py` into that folder.
4. Create `compass.env` in the same folder (copy `compass.env.example`, fill in your bot token;
   CHAT_ID is 5288703744).
5. Check the plan: run `python3 host_check.py --send` (SSH or the panel's Terminal, if you have it).
   You should get "Hello from your hosting!" in Telegram. If you have no terminal, create a cron job
   that runs the same command once, then read its output or just look for the Telegram message.
6. Add the cron job: **Cron Jobs**, every minute (`* * * * *`), command:
   `cd ~/domains/compass && python3 bot.py --loop >> run.log 2>&1`
   The `--loop` part keeps each run listening for about 58 seconds, so replies take a couple of seconds
   instead of up to a minute. A lock file makes sure only one copy listens at a time.
   (Use the Python path your panel shows, for example `python3` or `/usr/local/bin/python3`.)
7. Tell Claude it runs. Claude then switches off GitHub's schedule so only one copy reads your messages.

## Notes
- The voice note is skipped on shared hosting (too heavy). The text message still goes out.
- To go back to GitHub later: delete the cron job and tell Claude. Your streak and settings live in the
  pinned Telegram tracker, so nothing needs moving.
- `run.log` shows what the last runs did.
