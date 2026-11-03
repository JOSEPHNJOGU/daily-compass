# Joseph's Daily Compass (GitHub Actions)

A Telegram accountability bot. It runs every 5 minutes on GitHub for free, replies to your
check-ins, sends nudges, and keeps your streak in the pinned Telegram tracker message.
No Claude usage, no server.

## Setup (about 5 minutes)

1. On github.com, create a **new public repository**, e.g. `daily-compass`. (Public repos get
   unlimited free Actions minutes. The code contains no secrets and no personal data.)
2. Upload everything in this folder to the repository, keeping the structure:
   `bot.py`, `voice.py`, `README.md`, and `.github/workflows/compass.yml`.
   (Add file > Upload files. The hidden `.github` folder must be included.)
3. In the repository, go to **Settings > Secrets and variables > Actions > New repository secret**
   and add two secrets:
   - `TELEGRAM_TOKEN`: your bot token from BotFather
   - `CHAT_ID`: `5288703744`
4. Open the **Actions** tab. If asked, click **Enable workflows**.
5. Select the **Compass** workflow, click **Run workflow**, and check the run turns green.
6. Tell Claude it works. Claude will then switch off the old hourly Claude task, so the two
   don't both answer your messages.

## What it understands

- "I'm awake" (any wording), a video (= workout), "I'm at work"
- "leaving early" or "left work" (sends an evening plan)
- `book: Atomic Habits, page 45`, `read to page 80`
- `set wake 05:45` (also `workout`, `work`), `/status`, `/help`
- Sleep tracker: `sleeping` (or `/sleep`) when you fall asleep, `awake` when you wake. Before 05:00 an `awake`
  is a night wake (say `sleeping` again and the time awake is subtracted). `napping`, `I slept at 11pm`,
  `/sleepstats`, `set sleep 8h` (goal). If you log awake very late, it asks when you really woke up.
- Every clock time you give it (wake-up call, `set ...`, `quiet`, `remind 15:00`, `change 2 to ...`, past sleep)
  is read back with Confirm / Change / Cancel buttons, and it asks AM or PM when a time could be either.
  Relative times such as `remind in 20 minutes` need no confirmation.
- Say `I feel bored tonight` and it is saved to the diary, then it asks why and saves your answer too.

## Notes

- GitHub's scheduler can start runs a few minutes late, sometimes more. Your check-in times are
  read from the message timestamps, so deadlines stay exact. Only the replies lag.
- Scheduled workflows in a repository can be paused by GitHub after 60 days without repo
  activity. If replies stop, open the Actions tab and re-enable the workflow.
- To change times or wording, edit `bot.py` (or ask Claude to).
