#!/usr/bin/env python3
"""Joseph's Daily Compass v2: a Telegram accountability bot. Standard library only.

State lives in a pinned Telegram message (the "tracker"), so nothing is stored in files.
Settings come from environment variables or a compass.env file next to this script:
TELEGRAM_TOKEN and CHAT_ID. Set DRY_RUN=1 to print instead of sending.
Run modes:  python3 bot.py           one pass (use with a cron job)
            python3 bot.py --loop    stay alive ~58s listening for messages (cron every minute)
"""
import json
import os
import random
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=3))  # Nairobi, no daylight saving


def pdate(s):
    """Parse YYYY-MM-DD (works on old Python versions, unlike the newer helper)."""
    return datetime.strptime(s, "%Y-%m-%d").date()


START = pdate(os.environ.get("COMPASS_START", "2026-11-04"))  # Day 1
NAME = "Joseph"
MAX_REMINDERS = 20
HIST_KEEP = 90                     # days of history kept in the tracker
DEFAULT_TIMES = dict(wakecall="05:00", midday="12:30", leavecheck="17:30", review="21:00", winddown="22:00")
DEFAULT_TARGETS = dict(water=3000, pages=10, glass=500, photo=1, outdoor=1, screen=0, music=1)
CMDVER = 5                         # bump to re-register the "/" command menu
REFLECT_AT = "23:20"               # last hourly slot of the evening Claude reflection
WAKE_DEADLINE_AFTER = 30           # minutes after the wake-up call: "I'm awake" deadline
WORKOUT_DEADLINE_AFTER = 90        # minutes after the wake-up call: workout video deadline
PLAY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "playlists.txt")
MUSIC_QUERY = {"stressed": "calming worship", "low": "uplifting praise and worship", "frustrated": "peaceful worship songs",
               "tired": "energetic gospel praise", "calm": "soft worship", "good": "joyful gospel praise",
               "focus": "instrumental worship focus"}
PLAYLIST_NAMES = ["wake"] + list(MUSIC_QUERY)
PLAYLISTS = {}                     # your own links, loaded from playlists.txt on the host
AWAIT_HOURS = 3                    # an unanswered wind-down question expires after this long
EMOTIONS = [("good", "😊"), ("calm", "😌"), ("stressed", "😰"), ("low", "😔"), ("frustrated", "😤"), ("tired", "😴")]
EMO_CH = {"good": "g", "calm": "c", "stressed": "s", "low": "l", "frustrated": "f", "tired": "t"}
CH_EMO = {v: k for k, v in EMO_CH.items()}
EMO_FACE = dict(EMOTIONS)
SEQ = ("hist", "moods", "emos", "screens")
SCR_CHARS = "0123456789abcdefghijklmnopqrstuvwxyz"
B36 = SCR_CHARS
SLEEP_KEEP = 60                    # days of sleep history kept in the tracker
SLEEP_GOAL = 480                   # default sleep goal in minutes (8 hours)
NIGHT_CUTOFF = 300                 # an "awake" before 05:00 is a night wake, not the morning
LATE_AWAKE_AFTER = 120             # awake logged this many minutes after the wake-up call: ask the real time
MAX_SESSION = 16 * 60              # a sleep session open longer than this was never closed
LONG_SLEEP = 14 * 60               # a night this long is suspicious: ask for the real wake-up time
MIN_SLEEP, MIN_NAP = 10, 5         # shorter sessions (minutes) are not counted
SHORT_NIGHT = 90                   # a "night" under this many minutes is counted as a nap
CONFIRM_MINUTES = 30               # a time confirmation expires after this long

DEFAULT = dict(
    day=0, streak=0, slips=0, week="2026-11-02",
    wake="05:30", workout="07:30", work="09:30", free=1,
    book="not set", page="not set",
    today="none", wake_t="-", workout_t="-", work_t="-", left_t="-", sent=[],
    last_id=0, notes="-", updated="setup", times=None, reminders=None,
    started="-", best=0, freezes=0, buddy="off", quiet="22:30-05:00", reviewed="-",
    targets=None, water=0, pages=0, photo_t="-", outdoor_t="-", mood="-",
    hist_start="-", hist="", moods="", emos="", screens="",
    aw_state="-", aw_emo="-", aw_at="-",
    ev_date="-", ev_screen="-", ev_emo="-", ev_why="-", ev_diary="-", ev_shot="-", ev_done="-", cmdver=0, next_wake="-",
    sl_state="-", sl_since="-", sl_gaps=0, sl_gapat="-", sl_goal=SLEEP_GOAL, sl_last="-",
    sl_start="-", sl_nights="", sl_naps="",
    cf_kind="-", cf_stage="-", cf_at="-", cf_data="-",
)


def new_state():
    s = dict(DEFAULT)
    s["sent"], s["times"], s["reminders"] = [], dict(DEFAULT_TIMES), []
    s["targets"] = dict(DEFAULT_TARGETS)
    return s


# ---------------------------------------------------------------- tracker text


def parse(text):
    s = new_state()
    m = re.search(r"Day: (\d+).*?Streak: (\d+).*?Slips this week: (\d+) \(week of (\S+?)\)", text)
    if m:
        s["day"], s["streak"], s["slips"], s["week"] = int(m[1]), int(m[2]), int(m[3]), m[4]
    m = re.search(r"Rules: wake (\S+) \| workout video (\S+) \| at work (\S+) \| free slips/week (\d+)", text)
    if m:
        s["wake"], s["workout"], s["work"], s["free"] = m[1], m[2], m[3], int(m[4])
    m = re.search(r"Times: wakecall (\S+) \| midday (\S+) \| leavecheck (\S+) \| review (\S+)(?: \| winddown (\S+))?", text)
    if m:
        s["times"] = dict(wakecall=m[1], midday=m[2], leavecheck=m[3], review=m[4], winddown=m[5] or "22:00")
    m = re.search(r"Started: (\S+) \| Best: (\d+) \| Freezes: (\d+) \| Buddy: (\S+) \| Quiet: (\S+) \| Reviewed: (\S+)", text)
    if m:
        s["started"], s["best"], s["freezes"], s["buddy"], s["quiet"], s["reviewed"] = m[1], int(m[2]), int(m[3]), m[4], m[5], m[6]
    m = re.search(r"Targets: water (\d+) \| pages (\d+) \| glass (\d+) \| photo (on|off) \| outdoor (on|off)(?: \| screen (\d+))?(?: \| music (on|off))?", text)
    if m:
        s["targets"] = dict(water=int(m[1]), pages=int(m[2]), glass=int(m[3]), photo=int(m[4] == "on"),
                            outdoor=int(m[5] == "on"), screen=int(m[6] or 0), music=int((m[7] or "on") == "on"))
    m = re.search(r"Progress: water (\d+) \| pages (\d+) \| photo (\S+) \| outdoor (\S+) \| mood (\S+)", text)
    if m:
        s["water"], s["pages"], s["photo_t"], s["outdoor_t"], s["mood"] = int(m[1]), int(m[2]), m[3], m[4], m[5]
    m = re.search(r"History: (\S+) (\S+) \| Moods: (\S+)(?: \| Emos: (\S+) \| Screens: (\S+))?", text)
    if m:
        s["hist_start"] = m[1]
        s["hist"] = "" if m[2] == "-" else m[2]
        s["moods"] = "" if m[3] == "-" else m[3]
        s["emos"] = "" if (m[4] or "-") == "-" else m[4]
        s["screens"] = "" if (m[5] or "-") == "-" else m[5]
    m = re.search(r"Await: (\S+) \| (\S+) \| (\S+)", text)
    if m:
        s["aw_state"], s["aw_emo"], s["aw_at"] = m[1], m[2], m[3]
    m = re.search(r"Evening: (\S+) \| screen (\S+) \| emo (\S+) \| why (.*?) \| diary (.*?) \| shot (\S+)(?: \| done (\S+))?[ \t]*$", text, re.M)
    if m:
        s["ev_date"], s["ev_screen"], s["ev_emo"], s["ev_why"], s["ev_diary"], s["ev_shot"] = m[1], m[2], m[3], m[4], m[5], m[6]
        s["ev_done"] = m[7] or "-"
    m = re.search(r"Next: wake (\S+)", text)
    if m:
        s["next_wake"] = m[1]
    m = re.search(r"Rest: (\S+) \| since (\S+) \| gaps (\d+) \| gapat (\S+) \| goal (\d+) \| last (\S+)", text)
    if m:
        s["sl_state"], s["sl_since"], s["sl_gaps"], s["sl_gapat"], s["sl_goal"], s["sl_last"] = m[1], m[2], int(m[3]), m[4], int(m[5]), m[6]
    m = re.search(r"SleepLog: (\S+) (\S+) \| naps (\S+)", text)
    if m:
        s["sl_start"] = m[1]
        s["sl_nights"] = "" if m[2] == "-" else m[2]
        s["sl_naps"] = "" if m[3] == "-" else m[3]
    m = re.search(r"^Confirm: (\S+) \| (\S+) \| (\S+) \| (.*?)\s*$", text, re.M)
    if m:
        s["cf_kind"], s["cf_stage"], s["cf_at"], s["cf_data"] = m[1], m[2], m[3], m[4]
    m = re.search(r"Meta: cmds (\d+)", text)
    if m:
        s["cmdver"] = int(m[1])
    m = re.search(r"Book: (.*?) \| Page: (.*?)\s*$", text, re.M)
    if m:
        s["book"], s["page"] = m[1], m[2]
    m = re.search(r"Today: (\S+) \| wake (\S+) \| workout (\S+) \| work (\S+) \| left early (\S+) \| sent: (.*)", text)
    if m:
        s["today"], s["wake_t"], s["workout_t"], s["work_t"], s["left_t"] = m[1], m[2], m[3], m[4], m[5]
        s["sent"] = [x.strip() for x in m[6].split(",") if x.strip() and x.strip() != "-"]
    m = re.search(r"Last update id: (\d+)", text)
    if m:
        s["last_id"] = int(m[1])
    m = re.search(r"Updated: (.*)", text)
    if m:
        s["updated"] = m[1].strip()
    m = re.search(r"Notes: (.*)", text)
    if m:
        s["notes"] = m[1].strip()
    for m in re.finditer(r"^R(\d+): (\S+) \| (\S+) \| (.*?) \| last (\S+)\s*$", text, re.M):
        s["reminders"].append(dict(id=int(m[1]), time=m[2], rule=m[3], text=m[4], last=m[5]))
    return s


def render(s, updated=None):
    sent = ",".join(s["sent"]) if s["sent"] else "-"
    t, g = s["times"], s["targets"]
    onoff = lambda v: "on" if v else "off"
    lines = [
        "📊 COMPASS TRACKER",
        f"Updated: {updated or s['updated']}",
        f"Day: {s['day']} (starts {START}) | Streak: {s['streak']} | Slips this week: {s['slips']} (week of {s['week']})",
        f"Rules: wake {s['wake']} | workout video {s['workout']} | at work {s['work']} | free slips/week {s['free']} (2nd slip resets)",
        f"Times: wakecall {t['wakecall']} | midday {t['midday']} | leavecheck {t['leavecheck']} | review {t['review']} | winddown {t['winddown']}",
        f"Started: {s['started']} | Best: {s['best']} | Freezes: {s['freezes']} | Buddy: {s['buddy']} | Quiet: {s['quiet']} | Reviewed: {s['reviewed']}",
        f"Targets: water {g['water']} | pages {g['pages']} | glass {g['glass']} | photo {onoff(g['photo'])} | outdoor {onoff(g['outdoor'])} | screen {g['screen']} | music {onoff(g['music'])}",
        f"Book: {s['book']} | Page: {s['page']}",
        f"Today: {s['today']} | wake {s['wake_t']} | workout {s['workout_t']} | work {s['work_t']} | left early {s['left_t']} | sent: {sent}",
        f"Progress: water {s['water']} | pages {s['pages']} | photo {s['photo_t']} | outdoor {s['outdoor_t']} | mood {s['mood']}",
        f"Await: {s['aw_state']} | {s['aw_emo']} | {s['aw_at']}",
        f"Evening: {s['ev_date']} | screen {s['ev_screen']} | emo {s['ev_emo']} | why {s['ev_why']} | diary {s['ev_diary']} | shot {s['ev_shot']} | done {s['ev_done']}",
        f"Next: wake {s['next_wake']}",
        f"Rest: {s['sl_state']} | since {s['sl_since']} | gaps {s['sl_gaps']} | gapat {s['sl_gapat']} | goal {s['sl_goal']} | last {s['sl_last']}",
        f"SleepLog: {s['sl_start']} {s['sl_nights'] or '-'} | naps {s['sl_naps'] or '-'}",
        f"Confirm: {s['cf_kind']} | {s['cf_stage']} | {s['cf_at']} | {s['cf_data']}",
        f"History: {s['hist_start']} {s['hist'] or '-'} | Moods: {s['moods'] or '-'} | Emos: {s['emos'] or '-'} | Screens: {s['screens'] or '-'}",
        f"Meta: cmds {s['cmdver']}",
        f"Last update id: {s['last_id']}",
    ]
    for r in sorted(s["reminders"], key=lambda r: r["id"]):
        lines.append(f"R{r['id']}: {r['time']} | {r['rule']} | {r['text']} | last {r['last']}")
    lines.append(f"Notes: {s['notes']}")
    return "\n".join(lines)


# -------------------------------------------------------------------- helpers


def load_playlists():
    """Read your own playlist links from playlists.txt (host only); lines look like: stressed=https://..."""
    global PLAYLISTS
    found = {}
    try:
        for line in open(PLAY_FILE, encoding="utf-8"):
            if "=" in line:
                k, v = line.strip().split("=", 1)
                if k in PLAYLIST_NAMES and v:
                    found[k] = v
    except OSError:
        pass
    PLAYLISTS = found


def music_url(name):
    if name in PLAYLISTS:
        return PLAYLISTS[name]
    q = MUSIC_QUERY.get(name)
    return ("https://open.spotify.com/search/" + urllib.parse.quote(q)) if q else None


def apply_wake(st, hm):
    """Set the wake-up call to hm, with the 'I'm awake' and workout deadlines after it."""
    w = mins(hm)
    st["times"]["wakecall"] = hm
    st["wake"] = hm_of(w + WAKE_DEADLINE_AFTER)
    cap = mins(st["work"]) - 30
    st["workout"] = hm_of(min(w + WORKOUT_DEADLINE_AFTER, cap) if cap >= w + WAKE_DEADLINE_AFTER + 15 else w + WAKE_DEADLINE_AFTER + 15)


def parse_screen(text):
    """Screen time in minutes from '3h20', '3h 20m', '3:20', '3.5h', '200' (minutes) or '3' (hours); None if unreadable."""
    s = text.strip().lower().replace(",", ".")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\s*(?:(\d+)\s*(?:m|min|mins|minutes?)?)?", s)
    if m:
        v = float(m[1]) * 60 + (int(m[2]) if m[2] else 0)
    else:
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
        if m:
            v = int(m[1]) * 60 + int(m[2])
        else:
            m = re.fullmatch(r"(\d+)\s*(?:m|min|mins|minutes?)", s)
            if m:
                v = int(m[1])
            else:
                m = re.fullmatch(r"(\d+(?:\.\d+)?)", s)
                if not m:
                    return None
                v = float(m[1]) * 60 if float(m[1]) <= 16 else float(m[1])
    v = int(round(v))
    return v if 0 <= v <= 1440 else None


def clean(t, n):
    t = re.sub(r"\s+", " ", (t or "").replace("|", "/")).strip()[:n]
    return t or "-"


def nice(emo):
    return emo.replace("_", " ")


def stamp(now):
    return now.strftime("%Y-%m-%dT%H:%M")


def mins(hm):
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def hm_of(total):
    total %= 1440
    return f"{total // 60:02d}:{total % 60:02d}"


def tm(st, key):
    v = st["times"].get(key, DEFAULT_TIMES[key])
    return None if v == "off" else mins(v)


def spoken(hm):
    h, m = [int(x) for x in hm.split(":")]
    ampm = "a m" if h < 12 else "p m"
    h12 = h % 12 or 12
    return f"{h12} {ampm}" if m == 0 else f"{h12} {m:02d} {ampm}"


def pick(seed, options):
    return random.Random(seed).choice(options)


RE_TIME = re.compile(r"(?:\bat\s+)?\b(\d{1,2}):(\d{2})\s*(am|pm)?\b|(?:\bat\s+)?\b(\d{1,2})\s*(am|pm)\b", re.I)


def to_hm(h, m, ap):
    h, m = int(h), int(m or 0)
    if ap:
        if h < 1 or h > 12:
            return None
        h = (0 if h == 12 else h) + (12 if ap.lower() == "pm" else 0)
    if not (0 <= h < 24 and 0 <= m < 60):
        return None
    return f"{h:02d}:{m:02d}"


def find_time(s):
    """Return (HH:MM, text with the time removed), or (None, s)."""
    m = RE_TIME.search(s)
    if not m:
        return None, s
    hm = to_hm(m[1] or m[4], m[2] or 0, m[3] or m[5])
    if not hm:
        return None, s
    return hm, s[:m.start()] + " " + s[m.end():]


def in_quiet(st, minute):
    q = st["quiet"]
    if q == "off" or "-" not in q:
        return False
    a, b = [mins(x) for x in q.split("-")]
    return (a <= minute < b) if a < b else (minute >= a or minute < b)


# ------------------------------------------------------- history, stats, moods


def scr_char(minutes):
    return SCR_CHARS[max(0, min(35, int(round(minutes / 15.0))))]


def day_set(st, d, ch=None, mood=None, emo=None, screen=None):
    """Record per-day values (aligned strings, last HIST_KEEP days): status, mood, emotion, screen time."""
    if d < START:
        return
    if st["hist_start"] == "-":
        st["hist_start"] = str(d)
    start = pdate(st["hist_start"])
    i = (d - start).days
    if i < 0:
        return
    n = max([len(st[k]) for k in SEQ] + [i + 1])
    rows = {k: list(st[k].ljust(n, ".")) for k in SEQ}
    if ch:
        rows["hist"][i] = ch
    if mood:
        rows["moods"][i] = str(mood)
    if emo:
        rows["emos"][i] = emo
    if screen is not None:
        rows["screens"][i] = scr_char(screen)
    if n > HIST_KEEP:
        cut = n - HIST_KEEP
        rows = {k: v[cut:] for k, v in rows.items()}
        start = start + timedelta(days=cut)
    st["hist_start"] = str(start)
    for k in SEQ:
        st[k] = "".join(rows[k])


def seq_items(st, key="hist"):
    if st["hist_start"] == "-":
        return []
    start = pdate(st["hist_start"])
    return [(start + timedelta(days=i), c) for i, c in enumerate(st[key])]


def hist_items(st):
    return seq_items(st, "hist")


def fmt_min(m):
    return f"{m // 60}h{m % 60:02d}" if m >= 60 else f"{m} min"


def recent(st, key, today, days=7):
    return [c for d, c in seq_items(st, key) if today - timedelta(days=days - 1) <= d <= today and c != "."]


def screen_avg(st, today, days=7):
    vals = [SCR_CHARS.index(c) * 15 for c in recent(st, "screens", today, days) if c in SCR_CHARS]
    return int(sum(vals) / len(vals)) if vals else None


def emo_summary(st, today, days=7):
    chars = recent(st, "emos", today, days)
    names = [CH_EMO.get(c, "other") for c in chars]
    counts = sorted(((names.count(n), n) for n in set(names)), reverse=True)
    return ", ".join(f"{EMO_FACE.get(n, '🙂')} {n} x{c}" for c, n in counts) or "none logged"


EMO = {"P": "🟩", "S": "🟨", "F": "🟦", "X": "🟥", ".": "⬜"}


def heatmap(st, weeks=12):
    items = hist_items(st)
    if not items:
        return "(no completed days yet)"
    chars = [c for _, c in items]
    rows = ["".join(EMO.get(c, "⬜") for c in chars[i:i + 7]) for i in range(0, len(chars), 7)]
    return "\n".join(rows[-weeks:])


def stats_text(st):
    today = datetime.now(TZ).date()
    items = [c for _, c in hist_items(st) if c != "."]
    last30 = [c for _, c in hist_items(st)][-30:]
    last30 = [c for c in last30 if c != "."]
    ok30 = sum(1 for c in last30 if c in "PSF")
    pct = f"{round(100 * ok30 / len(last30))}%" if last30 else "n/a"
    moods = [int(c) for c in st["moods"][-7:] if c.isdigit()]
    mood_txt = f"{sum(moods) / len(moods):.1f}/5" if moods else "n/a"
    sa = screen_avg(st, today)
    return (f"📊 Your stats, {NAME}\n"
            f"Day {st['day']} | Streak {st['streak']} | Best {st['best']} | Freezes {st['freezes']}/3\n"
            f"Days finished: {len(items)} | Last 30 days: {pct} on track\n"
            f"Mood (last 7 days): {mood_txt}\n"
            f"Feelings (last 7 days): {emo_summary(st, today)}\n"
            f"Screen time (7-day average): {fmt_min(sa) if sa is not None else 'n/a'}\n"
            f"Sleep (7-day average): {sleep_avg_text(st, today) or 'n/a'} (/sleepstats for more)\n"
            f"Today: water {st['water']}/{st['targets']['water']} ml, pages {st['pages']}/{st['targets']['pages']}\n"
            f"Heatmap (🟩 perfect 🟨 slip 🟦 freeze 🟥 reset):\n{heatmap(st)}")


def weekly_text(st, today):
    week = [c for d, c in hist_items(st) if today - timedelta(days=6) <= d <= today]
    p, s, f, x = (week.count(k) for k in "PSFX")
    moods = [int(c) for c in st["moods"][-7:] if c.isdigit()]
    mood_txt = f"{sum(moods) / len(moods):.1f}/5" if moods else "n/a"
    sa = screen_avg(st, today)
    return (f"🗓️ Weekly review, {NAME}\n"
            f"Perfect days: {p} | Slips: {s} | Freezes used: {f} | Resets: {x}\n"
            f"Streak {st['streak']} (best {st['best']}) | Mood avg {mood_txt}\n"
            f"Feelings: {emo_summary(st, today)}\n"
            f"Screen time (average): {fmt_min(sa) if sa is not None else 'n/a'}\n"
            f"Sleep (average): {sleep_avg_text(st, today) or 'n/a'}\n"
            f"Heatmap:\n{heatmap(st, 6)}\n"
            f"Reflect: what worked this week? Reply with /diary <one line>")


# ------------------------------------------------- times, sleep and feelings

RE_CLOCK = re.compile(r"^(?:at\s+|around\s+|by\s+|about\s+|@\s*)?(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?$", re.I)
TIME_KEYS = ("wake", "workout", "work", "wakecall", "midday", "leavecheck", "leave", "review", "winddown")


def parse_clock(frag):
    """Read a clock time. Returns 'HH:MM' when it is certain (am/pm given, 13-23, 00, or a leading zero like 07:30),
    'h:mm?' when it could be AM or PM, or None when it is not a time."""
    s = (frag or "").strip().lower().rstrip(".,!?")
    if s in ("noon", "midday"):
        return "12:00"
    if s == "midnight":
        return "00:00"
    m = RE_CLOCK.match(s)
    if not m:
        return None
    raw_h, mi, ap = m[1], int(m[2] or 0), (m[3] or "").replace(".", "")
    h = int(raw_h)
    if mi > 59:
        return None
    if ap:
        if not 1 <= h <= 12:
            return None
        return f"{(0 if h == 12 else h) + (12 if ap.startswith('p') else 0):02d}:{mi:02d}"
    if h > 23:
        return None
    if h == 0 or h >= 13 or raw_h.startswith("0"):
        return f"{h:02d}:{mi:02d}"
    return f"{h}:{mi:02d}?"


def resolve_slot(slot, ap):
    """Turn an ambiguous 'h:mm?' into 'HH:MM' using 'am' or 'pm'."""
    h, mi = slot.rstrip("?").split(":")
    return f"{int(h) % 12 + (12 if ap == 'pm' else 0):02d}:{mi}"


def pretty(hm):
    """'19:30' -> '19:30 (7:30 PM)' so a wrong guess is easy to spot."""
    h, mi = [int(x) for x in hm.split(":")]
    return f"{hm} ({h % 12 or 12}:{mi:02d} {'AM' if h < 12 else 'PM'})"


def plain12(hm):
    h, mi = [int(x) for x in hm.split(":")]
    return f"{h % 12 or 12}:{mi:02d} {'AM' if h < 12 else 'PM'}"


RE_REM_TIME = re.compile(
    r"(?:\bat\s+)?\b\d{1,2}[:.]\d{2}\s*(?:a\.?m\.?|p\.?m\.?)?(?![\w:])"
    r"|(?:\bat\s+)?\b\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?)(?![\w])"
    r"|\bat\s+\d{1,2}\b(?![:.]\d)(?!\s*(?:min|mins|minutes?|hours?|hrs?|h)\b)", re.I)


def pstamp(s):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=TZ)
    except (ValueError, TypeError):
        return None


def latest_before(now, hm):
    """The most recent moment showing clock time hm that is not after now."""
    h, mi = [int(x) for x in hm.split(":")]
    dt = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    return dt if dt <= now else dt - timedelta(days=1)


def b36(n, width):
    n = max(0, int(n))
    out = ""
    for _ in range(width):
        out = B36[n % 36] + out
        n //= 36
    return out


def unb36(s):
    n = 0
    for ch in s:
        n = n * 36 + B36.index(ch)
    return n


def minutes_of(dt):
    return dt.hour * 60 + dt.minute


def sl_prepare(st, d):
    """Make room for date d in the sleep log; returns its index."""
    if st["sl_start"] == "-":
        st["sl_start"] = str(d)
    start = pdate(st["sl_start"])
    i = (d - start).days
    if i < 0:
        st["sl_nights"] = "." * 8 * (-i) + st["sl_nights"]
        st["sl_naps"] = "." * (-i) + st["sl_naps"]
        st["sl_start"], i = str(d), 0
    st["sl_nights"] = st["sl_nights"].ljust((i + 1) * 8, ".")
    st["sl_naps"] = st["sl_naps"].ljust(len(st["sl_nights"]) // 8, ".")
    return i


def sl_trim(st):
    n = len(st["sl_nights"]) // 8
    if n > SLEEP_KEEP:
        cut = n - SLEEP_KEEP
        st["sl_nights"], st["sl_naps"] = st["sl_nights"][cut * 8:], st["sl_naps"][cut:]
        st["sl_start"] = str(pdate(st["sl_start"]) + timedelta(days=cut))


def sl_put_night(st, d, bed, wake, dur, gap):
    i = sl_prepare(st, d)
    rec = b36(min(287, round(bed / 5.0)), 2) + b36(min(287, round(wake / 5.0)), 2) + b36(round(dur / 5.0), 2) + b36(round(gap / 5.0), 2)
    n = st["sl_nights"]
    st["sl_nights"] = n[:i * 8] + rec + n[i * 8 + 8:]
    sl_trim(st)


def sl_del_night(st, d):
    if st["sl_start"] == "-":
        return
    i = (d - pdate(st["sl_start"])).days
    if 0 <= i < len(st["sl_nights"]) // 8:
        n = st["sl_nights"]
        st["sl_nights"] = n[:i * 8] + "." * 8 + n[i * 8 + 8:]


def sl_add_nap(st, d, minutes):
    i = sl_prepare(st, d)
    cur = st["sl_naps"][i]
    total = (B36.index(cur) * 10 if cur in B36 else 0) + int(minutes)
    st["sl_naps"] = st["sl_naps"][:i] + B36[min(35, int(round(total / 10.0)))] + st["sl_naps"][i + 1:]
    sl_trim(st)


def sl_nights(st):
    """All logged nights as dicts, oldest first. 'date' is the day you woke up."""
    if st["sl_start"] == "-":
        return []
    start, res = pdate(st["sl_start"]), []
    for i in range(len(st["sl_nights"]) // 8):
        rec = st["sl_nights"][i * 8:i * 8 + 8]
        if "." in rec or any(c not in B36 for c in rec):
            continue
        res.append(dict(date=start + timedelta(days=i), bed=unb36(rec[0:2]) * 5, wake=unb36(rec[2:4]) * 5,
                        dur=unb36(rec[4:6]) * 5, gap=unb36(rec[6:8]) * 5))
    return res


def sl_nap_days(st, today, days=7):
    if st["sl_start"] == "-":
        return []
    start, res = pdate(st["sl_start"]), []
    for i, c in enumerate(st["sl_naps"]):
        d = start + timedelta(days=i)
        if c in B36 and c != "0" and today - timedelta(days=days - 1) <= d <= today:
            res.append((d, B36.index(c) * 10))
    return res


def sl_write(st, start, end, gaps):
    """Record a finished night. Returns (dict, None) or (None, reason)."""
    total = int((end - start).total_seconds() // 60)
    net = total - int(gaps)
    if total <= 0:
        return None, "that would end before it began"
    if net < MIN_SLEEP:
        return None, "short"
    rec = dict(date=end.date(), bed=minutes_of(start), wake=minutes_of(end), dur=net, gap=int(gaps), start=start, end=end)
    sl_put_night(st, end.date(), rec["bed"], rec["wake"], net, int(gaps))
    st["sl_last"] = f"{start:%Y-%m-%dT%H:%M}_{int(gaps)}_{end:%Y-%m-%dT%H:%M}"
    return rec, None


def sl_last_parts(st):
    p = st["sl_last"].split("_")
    if len(p) != 3 or not p[1].isdigit():
        return None
    a, b = pstamp(p[0]), pstamp(p[2])
    return (a, int(p[1]), b) if a and b else None


def avg_of(nights, today, days):
    sel = [n["dur"] for n in nights if today - timedelta(days=days - 1) <= n["date"] <= today]
    return (int(round(sum(sel) / len(sel))), len(sel)) if sel else (None, 0)


def sleep_done_text(st, rec, today):
    s = f"😴 You slept {fmt_min(rec['dur'])} ({rec['start']:%H:%M} to {rec['end']:%H:%M})"
    if rec["gap"]:
        s += f", with {rec['gap']} min awake in the night"
    s += f". Goal {fmt_min(st['sl_goal'])}: " + ("reached 🎉" if rec["dur"] >= st["sl_goal"] else f"{fmt_min(st['sl_goal'] - rec['dur'])} short.")
    avg = sleep_avg_text(st, today)
    if avg and len([n for n in sl_nights(st) if n["date"] >= today - timedelta(days=6)]) >= 2:
        s += f"\n7-day average: {avg}."
    return s


def sleep_avg_text(st, today):
    a7, n7 = avg_of(sl_nights(st), today, 7)
    return f"{fmt_min(a7)} ({n7} night{'s' if n7 != 1 else ''})" if a7 is not None else None


def clock_spread(values):
    """Average clock time and spread (minutes) for times around midnight; (None, None) with fewer than 3 values."""
    if len(values) < 3:
        return None, None
    shifted = [(v - 720) % 1440 for v in values]
    mean = sum(shifted) / float(len(shifted))
    sd = (sum((x - mean) ** 2 for x in shifted) / len(shifted)) ** 0.5
    return int(round(mean + 720)) % 1440, int(round(sd))


def steadiness(sd):
    return "very steady" if sd <= 30 else "fairly steady" if sd <= 60 else "quite variable"


def night_line(n, goal):
    s = f"{fmt_min(n['dur'])} ({hm_of(n['bed'])} to {hm_of(n['wake'])})"
    if n["gap"]:
        s += f", {n['gap']} min awake in the night"
    diff = n["dur"] - goal
    s += f". Goal {fmt_min(goal)}: " + ("reached 🎉" if diff >= 0 else f"{fmt_min(-diff)} short")
    return s


def sleep_stats_text(st, now):
    today = now.date()
    nights = sl_nights(st)
    goal = st["sl_goal"]
    lines = [f"😴 Your sleep, {NAME}"]
    state, since = st["sl_state"], pstamp(st["sl_since"])
    if state == "asleep" and since:
        lines.append(f"Now: asleep since {since:%H:%M} ({fmt_min(int((now - since).total_seconds() // 60))} so far).")
    elif state == "wake" and since:
        lines.append("Now: awake in the night. Say 'sleeping' when you're back asleep.")
    elif state == "nap" and since:
        lines.append(f"Now: napping since {since:%H:%M}.")
    else:
        lines.append("Now: not tracking. Send 'sleeping' when you drift off.")
    if not nights:
        lines.append("No nights logged yet. Send 'sleeping' when you fall asleep and 'awake' when you wake up.")
    else:
        last = [n for n in nights if n["date"] >= today - timedelta(days=1)]
        lines.append("Last night: " + night_line(last[-1], goal) if last else "Last night: not logged.")
        a7, n7 = avg_of(nights, today, 7)
        a30, n30 = avg_of(nights, today, 30)
        nn = lambda n: f"{n} night{'s' if n != 1 else ''}"
        lines.append("Average: " + (f"7 days {fmt_min(a7)} ({nn(n7)})" if a7 is not None else "7 days n/a")
                     + (f" | 30 days {fmt_min(a30)} ({nn(n30)})" if a30 is not None else ""))
        hit7 = sum(1 for n in nights if today - timedelta(days=6) <= n["date"] <= today and n["dur"] >= goal)
        hit30 = sum(1 for n in nights if today - timedelta(days=29) <= n["date"] <= today and n["dur"] >= goal)
        lines.append(f"Goal {fmt_min(goal)}: hit {hit7} of {nn(n7)} this week, {hit30} of {nn(n30)} in 30 days.")
        recent = [n for n in nights if n["date"] >= today - timedelta(days=13)]
        bed, bsd = clock_spread([n["bed"] for n in recent])
        wak, wsd = clock_spread([n["wake"] for n in recent])
        if bed is not None:
            lines.append(f"Falling asleep around {hm_of(bed)} (±{bsd} min, {steadiness(bsd)}), waking around {hm_of(wak)} (±{wsd} min, {steadiness(wsd)}).")
    naps = sl_nap_days(st, today)
    if naps:
        lines.append(f"Naps (7 days): {len(naps)} day{'s' if len(naps) != 1 else ''}, about {fmt_min(sum(m for _, m in naps))} in total (not in the averages).")
    lines.append("Change the goal: set sleep 7h30")
    return "\n".join(lines)


FEEL_WORDS = {
    "stressed": "stressed", "stress": "stressed", "anxious": "stressed", "overwhelmed": "stressed", "worried": "stressed",
    "nervous": "stressed", "tense": "stressed", "pressured": "stressed", "panicked": "stressed", "uneasy": "stressed",
    "sad": "low", "down": "low", "lonely": "low", "depressed": "low", "empty": "low", "hopeless": "low", "worthless": "low",
    "low": "low", "blue": "low", "gloomy": "low", "numb": "low", "discouraged": "low", "heavy": "low", "hurt": "low",
    "angry": "frustrated", "annoyed": "frustrated", "irritated": "frustrated", "frustrated": "frustrated", "mad": "frustrated",
    "upset": "frustrated", "furious": "frustrated", "irritable": "frustrated", "resentful": "frustrated",
    "tired": "tired", "sleepy": "tired", "exhausted": "tired", "drained": "tired", "weary": "tired", "fatigued": "tired",
    "worn": "tired", "burnt": "tired", "burned": "tired",
    "calm": "calm", "relaxed": "calm", "peaceful": "calm", "content": "calm", "chill": "calm", "serene": "calm", "settled": "calm",
    "good": "good", "great": "good", "happy": "good", "amazing": "good", "excited": "good", "proud": "good", "grateful": "good",
    "motivated": "good", "energetic": "good", "awesome": "good", "fine": "good", "okay": "good", "ok": "good", "alright": "good",
    "joyful": "good", "hopeful": "good", "confident": "good", "blessed": "good", "better": "good", "thankful": "good",
    "fantastic": "good", "wonderful": "good", "refreshed": "good", "positive": "good", "inspired": "good", "productive": "good",
    "well": "good",
}
FEEL_DARK = {"hopeless", "worthless", "suicidal", "trapped", "desperate", "broken"}
FEEL_STOP = {"like", "that", "the", "a", "an", "as", "it", "my", "this", "your", "you", "we", "i", "me", "to", "in", "at", "on",
             "for", "about", "if", "how", "what", "free", "up", "out", "of", "so", "very", "really", "just", "kind", "bit", "not",
             "no", "yes", "is", "was", "be", "and", "but", "when", "then", "sure", "some", "any", "there", "here", "something"}
ADV = (r"(?:really|so|very|just|still|kinda|kind of|quite|pretty|honestly|actually|currently|always|now|a bit|a little|rather|fairly|"
       r"slightly|incredibly|totally|completely|absolutely|too|extra|super|more|bit|little|sort of|somewhat)")
RE_FEELING = re.compile(
    r"^(?:(?:i['’]?m|im|i\s+am|i)\s+)?(?:" + ADV + r"\s+)*(not\s+)?(?:feel|feeling)\s+(?:" + ADV + r"\s+)*"
    r"([a-z][a-z'\-]{1,19})(?:\s+(?:and|&)\s+([a-z][a-z'\-]{1,19}))?\b(.*)$", re.I | re.S)
RE_REASON = re.compile(r"\b(?:because(?:\s+of)?|cause|cuz|due\s+to|about|over|since)\s+(.+)$", re.I | re.S)
GOOD_WORDS = {w for w, c in FEEL_WORDS.items() if c in ("good", "calm")}


def extract_feeling(text):
    """'I am feeling bored tonight because ...' -> (emotion, the word you used, reason or None); None if not a feeling."""
    t = re.sub(r"\s+", " ", (text or "").strip().lower().replace("’", "'"))
    t = re.sub(r"^(?:well|honestly|so|hey|um|uh|yeah|ok|okay|hi|hello)[,\s]+", "", t)
    m = RE_FEELING.match(t)
    if not m:
        return None
    neg, w1, w2, rest = m[1], m[2], m[3], m[4]
    if w1 in FEEL_STOP or len(w1) < 3:
        return None
    word = w1 + (" and " + w2 if w2 else "")
    if neg:
        if w1 not in GOOD_WORDS:
            return None
        emo, word = "low", "not " + w1
    else:
        emo = FEEL_WORDS.get(w1, w1)
    r = RE_REASON.search(rest or "")
    why = re.sub(r"[.!?\s]+$", "", r[1]).strip() if r else None
    return emo, word, (why or None)


# ---------------------------------------------------------------- reminders

DAYNAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAYMAP = {"mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "wednesday": 2,
          "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4,
          "sat": 5, "saturday": 5, "sun": 6, "sunday": 6}


def rule_matches(rule, d):
    if rule == "daily":
        return True
    if rule == "weekdays":
        return d.weekday() < 5
    if rule == "weekends":
        return d.weekday() >= 5
    if rule.startswith("days:"):
        return DAYNAMES[d.weekday()] in rule[5:].split(",")
    if rule.startswith(("once:", "soon:")):
        return rule[5:] == str(d)
    return False


def describe(rule, today=None):
    if rule in ("daily", "weekdays", "weekends"):
        return rule
    if rule.startswith("days:"):
        return "every " + ", ".join(x.capitalize() for x in rule[5:].split(","))
    if rule.startswith("soon:"):
        return "timer"
    if rule.startswith("once:"):
        d = rule[5:]
        if today and d == str(today):
            return "today only"
        if today and d == str(today + timedelta(days=1)):
            return "tomorrow only"
        return f"once on {d}"
    return rule


def parse_reminder(arg, today, cur):
    """Return (dict(time, rule, text), None) or (None, error message)."""
    hm, rest = find_time(arg)
    if not hm:
        return None, "I need a time, like 15:00 or 3pm."
    toks = rest.split()
    used = [False] * len(toks)
    rule = None
    days, every = [], False
    for i, t in enumerate(toks):
        w = t.strip(",.").lower()
        parts = [p for p in w.split(",") if p]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", w):
            try:
                pdate(w)
            except ValueError:
                return None, "That date doesn't look right. Use YYYY-MM-DD."
            rule, used[i] = f"once:{w}", True
        elif w in ("daily", "everyday"):
            rule, used[i] = "daily", True
        elif w in ("weekdays", "weekday"):
            rule, used[i] = "weekdays", True
        elif w in ("weekends", "weekend"):
            rule, used[i] = "weekends", True
        elif w == "tomorrow":
            rule, used[i] = f"once:{today + timedelta(days=1)}", True
        elif w == "today":
            rule, used[i] = f"once:{today}", True
        elif w in ("every", "weekly"):
            every, used[i] = True, True
        elif parts and all(p in DAYMAP for p in parts):
            days += [DAYNAMES[DAYMAP[p]] for p in parts]
            used[i] = True
    if every and rule is None:
        for i, t in enumerate(toks):
            if t.strip(",.").lower() == "day":
                rule, used[i] = "daily", True
    if days and rule is None:
        days = [d for d in DAYNAMES if d in days]
        if every:
            rule = "days:" + ",".join(days)
        else:
            target = DAYNAMES.index(days[0])
            ahead = (target - today.weekday()) % 7
            if ahead == 0 and mins(hm) <= cur:
                ahead = 7
            rule = f"once:{today + timedelta(days=ahead)}"
    if rule is None:
        rule = f"once:{today if mins(hm) > cur else today + timedelta(days=1)}"
    words = [t for t, u in zip(toks, used) if not u]
    while words and words[0].lower() in ("me", "to", "at", "on", "every", "of"):
        words.pop(0)
    text = " ".join(words).replace("|", "/").strip()
    if not text:
        return None, "What should I remind you about? Example: remind 15:00 call mum"
    if rule == f"once:{today}" and mins(hm) + 120 < cur:
        return None, f"{hm} has already passed today. Try: remind tomorrow {hm} {text}"
    return dict(time=hm, rule=rule, text=text[:60]), None


def reminders_text(st, today):
    if not st["reminders"]:
        return "No reminders yet. Try: remind 15:00 call mum, or remind daily 7am drink water."
    lines = ["Your reminders:"]
    for r in sorted(st["reminders"], key=lambda r: (r["time"], r["id"])):
        lines.append(f"{r['id']}) {r['time']}, {describe(r['rule'], today)}: {r['text']}")
    lines.append("Remove: remove <number>. Change time: change <number> to <time>.")
    return "\n".join(lines)


def add_reminder(st, time_hm, rule, text):
    nid = max([r["id"] for r in st["reminders"]], default=0) + 1
    st["reminders"].append(dict(id=nid, time=time_hm, rule=rule, text=text[:60], last="-"))
    return nid


RE_IN = re.compile(r"\bin\s+(\d+(?:\.\d+)?)\s*(min(?:ute)?s?|h|hrs?|hours?)\b", re.I)


def handle_remind(st, arg, ts):
    if len(st["reminders"]) >= MAX_REMINDERS or len(render(st)) > 3500:
        return f"The reminder list is full (limit {MAX_REMINDERS}). Remove some first."
    m = RE_IN.search(arg)
    if m:
        amount = float(m[1]) * (60 if m[2].lower().startswith("h") else 1)
        if not 1 <= amount <= 1440:
            return "Try a time between 1 minute and 24 hours, like: remind in 10 minutes call mum"
        text = (arg[:m.start()] + " " + arg[m.end():]).split()
        while text and text[0].lower() in ("me", "to"):
            text.pop(0)
        body = " ".join(text).replace("|", "/").strip()
        if not body:
            return "What should I remind you about? Example: remind in 10 minutes call mum"
        t_, rule, _ = after(ts, int(round(amount)), "")
        nid = add_reminder(st, t_, rule, body)
        return f"Reminder {nid} set for {t_} (in {int(round(amount))} min): {body} ⏰"
    rem, err = parse_reminder(arg, ts.date(), ts.hour * 60 + ts.minute)
    if err:
        return err + "\nExamples: remind 15:00 call mum | remind daily 7am drink water | remind every mon,wed 18:00 gym"
    nid = add_reminder(st, rem["time"], rem["rule"], rem["text"])
    note = ""
    if in_quiet(st, mins(rem["time"])):
        note = f"\nNote: {rem['time']} is inside your quiet hours ({st['quiet']}), so it will arrive at {st['quiet'].split('-')[1]}. Send \"quiet off\" if you want it exactly on time."
    return f"Reminder {nid} set: {rem['time']}, {describe(rem['rule'], ts.date())}: {rem['text']} ⏰{note}"


def after(now, minutes_ahead, text):
    """(time, rule, text) for a one-off timer `minutes_ahead` from now, rounded up to the next minute."""
    t = now + timedelta(minutes=minutes_ahead)
    if t.second or t.microsecond:
        t = t.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return f"{t:%H:%M}", f"soon:{t.date()}", text


def is_once(rule):
    return rule.startswith(("once:", "soon:"))


# ----------------------------------------------------------- message reading

L_AWAKE, L_VIDEO, L_WORK, L_LEAVE = "☀️ I'm awake", "🎥 Workout video", "📍 I'm at work", "🚪 Leaving work"
L_OUTDOOR, L_PHOTO, L_FOCUS, L_MOOD = "🌳 Outdoor workout", "📸 Progress photo", "🍅 Focus 25", "😊 Mood"
L_STATS, L_REM, L_HELP, L_WD = "📊 Stats", "⏰ Reminders", "❓ Help", "🌙 Wind-down"
LABELS = {L_AWAKE: "awake", L_VIDEO: "hint_video", L_WORK: "atwork", L_LEAVE: "leave", L_OUTDOOR: "outdoor",
          L_PHOTO: "hint_photo", L_FOCUS: "focus", L_MOOD: "mood", L_STATS: "stats", L_REM: "rlist", L_HELP: "help", L_WD: "wd"}
SLASH = {"/start": "start", "/status": "status", "/help": "help", "/stats": "stats", "/menu": "menu", "/wd": "wd",
         "/cancel": "cancel", "/feel": "feel", "/feelings": "feelings", "/export": "export", "/awake": "awake",
         "/atwork": "atwork", "/leaving": "leave", "/leave": "leave", "/outdoor": "outdoor", "/photo": "hint_photo",
         "/video": "hint_video", "/reminders": "rlist", "/sleep": "sleeping", "/sleeping": "sleeping", "/nap": "nap",
         "/sleepstats": "sleepstats", "/sleepstat": "sleepstats"}
EXPLICIT = {"setwake", "playlist", "start", "status", "help", "stats", "menu", "wd", "cancel", "feel", "feelings", "export", "rlist", "rremove",
            "rclear", "redit", "remind", "setrule", "quiet", "buddy", "screen", "diary_show", "sleeping", "nap", "sleepstats",
            "sleepat"}
RE_WATER_BTN = re.compile(r"^💧 \+(\d+) ?ml$")
RE_PAGES_BTN = re.compile(r"^📖 \+(\d+) pages?$")

RE_BOOK = [
    re.compile(r"book\s*:\s*[\"“'‘]?(.+?)[\"”'’]?\s*,\s*page\s*(\d+)", re.I),
    re.compile(r"reading\s+[\"“'‘]?(.+?)[\"”'’]?\s*,?\s*(?:on\s+)?page\s*(\d+)", re.I),
]
RE_PAGE = re.compile(r"\bpage\s*(\d+)\b|\bp\.?\s*(\d+)\b", re.I)
RE_READ = re.compile(r"^/?(?:read|pages?)\s+(\d+)\s*(?:pages?)?\s*$", re.I)
RE_WATER = re.compile(r"^/?(?:water|drank|drink)\s*([+-]?\d+(?:\.\d+)?)\s*(ml|l|litres?|liters?|glass(?:es)?)?\s*$", re.I)
RE_SET = re.compile(
    r"^/?set\s+(wake|workout|work|wakecall|midday|leavecheck|leave|review|water|pages|glass|photo|outdoor|screen|music|winddown|sleep)\s*(?:to|at)?\s*(.+)$", re.I)
RE_SLEEPGOAL = re.compile(r"^/?sleep\s+goal\s*(?:to|at|is)?\s*(.+)$", re.I)
RE_SLEEPING = re.compile(
    r"^(?:(?:i['’]?m|im|i\s+am)\s+)?(?:now\s+|finally\s+|officially\s+)?(?:sleeping|asleep|going\s+to\s+sleep|off\s+to\s+sleep|falling\s+asleep|"
    r"drifting\s+off|dozing\s+off|going\s+to\s+sleep\s+now|sleep|lights\s+out)(?:\s+now|\s+for\s+the\s+night|\s+tonight)?\s*[.!]*$", re.I)
RE_NAP = re.compile(
    r"^(?:(?:i['’]?m|im|i\s+am)\s+)?(?:going\s+to\s+|gonna\s+|about\s+to\s+)?(?:take\s+a\s+nap|taking\s+a\s+nap|have\s+a\s+nap|having\s+a\s+nap|napping|nap)"
    r"(?:\s+now)?\s*[.!]*$", re.I)
RE_SLEEPAT = re.compile(
    r"^(?:i\s+|i['’]?ve\s+)?(?:slept|fell\s+asleep|went\s+to\s+sleep|went\s+to\s+bed|was\s+asleep|got\s+to\s+sleep)\s+(?:at|around|by|about|@)\s*(.+?)[.!\s]*$", re.I)
RE_SLEEPSTATS = re.compile(
    r"^/?(?:my\s+)?sleep\s*(?:stats?|average|avg|history|log|summary|report|record)\s*$|^how\s+(?:long|much)\s+(?:did|do)\s+i\s+(?:usually\s+)?sleep", re.I)
RE_BEDHINT = re.compile(
    r"^(?:i['’]?m\s+|im\s+|i\s+am\s+)?(?:going\s+to\s+bed|heading\s+to\s+bed|off\s+to\s+bed|time\s+for\s+bed|in\s+bed|good\s*night|night\s*night|nighty\s*night)"
    r"(?:\s+now|\s+everyone|\s+all)?\s*[.!]*$", re.I)
RE_FOCUS = re.compile(r"^/?focus\b(?:\s+(\d{1,3}))?\s*(.*)$", re.I)
RE_MOOD = re.compile(r"^/?mood\b(?:\s+([1-5]))?\s*$", re.I)
RE_DIARY = re.compile(r"^/?(?:diary|journal)\b\s*:?\s*(.*)$", re.I | re.S)
RE_WAKE = re.compile(r"^/?wake\s+(?:me\s+)?(?:at\s+|to\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*$", re.I)
RE_PLAYLIST = re.compile(r"^/?playlists?\b\s*(.*)$", re.I)
RE_SCREEN = re.compile(r"^/?screen\b\s*(?:time|today|usage)?\s*(.*)$", re.I)
RE_QUIET = re.compile(r"^/?quiet\b\s*(?:hours)?\s*(.+)$", re.I)
RE_BUDDY = re.compile(r"^/?buddy\s+(\S+)\s*$", re.I)
RE_RLIST = re.compile(r"^/?(?:reminders|list reminders|my reminders|show reminders)\s*$", re.I)
RE_RREMOVE = re.compile(r"^/?(?:remove|delete|cancel|del)\s*(?:reminder\s*)?#?(\d+)\s*$", re.I)
RE_RCLEAR = re.compile(r"^/?(?:clear|remove all|delete all)\s*(?:all\s*)?reminders\s*$", re.I)
RE_REDIT = re.compile(r"^/?(?:change|edit|move|reschedule)\s*(?:reminder\s*)?#?(\d+)\s*(?:to|at)?\s*(.+)$", re.I)
RE_REMIND = re.compile(r"^/?remind\b[\s:,]*(?:me\b[\s,]*)?(?:to\b[\s,]*)?(.*)$", re.I | re.S)
RE_LEAVE = re.compile(
    r"leaving early|left early|leaving work|left work|off work|knock(?:ed|ing) off|done (?:with )?work|finished work|got off work|leaving the office|left the office",
    re.I)
RE_WORK = re.compile(
    r"\bat work\b|\bat the office\b|\b(?:reached|arrived|got to|made it to)\s+(?:the\s+)?(?:work|office)\b|\bin the office\b", re.I)
RE_AWAKE = re.compile(
    r"\bawake\b|\bwoke\b|\bwaking\b|\bwake(?:n)? up\b|\bi'?m up\b|\bim up\b|\bup now\b|\bam up\b|\bgood morning\b|^up\b", re.I)
RE_OUTDOOR = re.compile(r"\b(outdoor|outdoors|outside)\b", re.I)


def classify(m):
    """Return (kind, data) for one Telegram message."""
    doc = m.get("document") or {}
    mime = str(doc.get("mime_type", ""))
    caption = (m.get("caption") or "")
    if "video" in m or "video_note" in m or mime.startswith("video"):
        return ("outdoor" if RE_OUTDOOR.search(caption) else "workout"), None
    if "photo" in m or mime.startswith("image"):
        return "photo", None
    text = re.sub(r"^(/\w+)@\w+", r"\1", (m.get("text") or caption).strip())
    low = text.lower()
    if low in SLASH:
        return SLASH[low], None
    if low == "/water":
        return "water", (1.0, "glass")
    if low == "/pages":
        return "pages", 5
    if low in ("status", "help"):
        return low, None
    if text in LABELS:
        return LABELS[text], None
    r = RE_WATER_BTN.match(text)
    if r:
        return "water", (float(r[1]), "ml")
    r = RE_PAGES_BTN.match(text)
    if r:
        return "pages", int(r[1])
    if low in ("/stats", "stats"):
        return "stats", None
    if RE_RLIST.match(text):
        return "rlist", None
    if RE_RCLEAR.match(text):
        return "rclear", None
    r = RE_RREMOVE.match(text)
    if r:
        return "rremove", int(r[1])
    r = RE_REDIT.match(text)
    if r:
        return "redit", (int(r[1]), r[2].strip())
    r = RE_REMIND.match(text)
    if r:
        return "remind", r[1].strip()
    r = RE_SET.match(text)
    if r:
        return "setrule", (r[1].lower(), r[2].strip())
    r = RE_WATER.match(text)
    if r:
        return "water", (float(r[1]), (r[2] or "").lower())
    r = RE_READ.match(text)
    if r:
        return "pages", int(r[1])
    r = RE_FOCUS.match(text)
    if r:
        return "focus", (int(r[1]) if r[1] else 25, r[2].strip())
    r = RE_MOOD.match(text)
    if r:
        return "mood", (int(r[1]) if r[1] else None)
    r = RE_DIARY.match(text)
    if r:
        arg = r[1].strip()
        if not arg:
            return "diary_show", None
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", arg):
            return "diary_show", arg
        return "journal", arg
    r = RE_SCREEN.match(text)
    if r:
        return "screen", r[1].strip()
    r = RE_WAKE.match(text)
    if r:
        return ("setwake", r[1].strip()) if parse_clock(r[1]) else ("other", text)
    r = RE_PLAYLIST.match(text)
    if r:
        return "playlist", r[1].strip()
    r = RE_QUIET.match(text)
    if r:
        return "quiet", r[1].strip()
    r = RE_BUDDY.match(text)
    if r:
        return "buddy", r[1].strip()
    r = RE_SLEEPGOAL.match(text)
    if r:
        return "setrule", ("sleep", r[1].strip())
    if RE_SLEEPSTATS.match(text):
        return "sleepstats", None
    if RE_SLEEPING.match(text):
        return "sleeping", None
    if RE_NAP.match(text):
        return "nap", None
    r = RE_SLEEPAT.match(text)
    if r and parse_clock(r[1]):
        return "sleepat", r[1].strip()
    if RE_BEDHINT.match(text):
        return "bedhint", None
    for rx in RE_BOOK:
        r = rx.search(text)
        if r:
            return "book", (r[1].strip(), r[2])
    fe = extract_feeling(text)
    if fe:
        return "feeling", fe
    if RE_LEAVE.search(text):
        return "leave", None
    if RE_WORK.search(text):
        return "atwork", None
    r = RE_PAGE.search(text)
    if r:
        return "page", r[1] or r[2]
    if RE_AWAKE.search(text):
        return "awake", None
    return "other", text


# ----------------------------------------------------------------- templates


def ack(t, deadline, test, seed):
    diff = mins(t) - mins(deadline)
    suffix = " (test day, nothing counts)" if test else ""
    if diff <= 0:
        base = pick(seed, [
            f"On time, {NAME}. 💪 Logged at {t}.",
            f"Logged at {t}, right on time. Strong start, {NAME}.",
            f"{t}, on time. That's how it's done, {NAME}.",
        ])
    else:
        base = pick(seed, [
            f"Logged at {t}, {diff} min past the {deadline} deadline. Let's beat it tomorrow, {NAME}.",
            f"Got it at {t}. That's {diff} min late. Tighten it up next time, {NAME}.",
        ])
    return base + suffix


def early_leave_plan(st, t):
    end = tm(st, "review") or 21 * 60
    hrs = max(end - mins(t), 0) / 60
    book, page = st["book"], st["page"]
    if book == "not set":
        read = "1. Read for 30-45 minutes. Tell me your book with: book: <title>, page <n>"
    else:
        block = 60 if hrs >= 4 else 40 if hrs >= 2 else 25
        read = f"1. Read {block} minutes of {book}, continuing from page {page} (tap 🍅 Focus 25 to time it)"
    pending = []
    g = st["targets"]
    if g["water"] and st["water"] < g["water"]:
        pending.append(f"💧 water {st['water']}/{g['water']} ml")
    if g["pages"] and st["pages"] < g["pages"]:
        pending.append(f"📖 pages {st['pages']}/{g['pages']}")
    if g["outdoor"] and st["outdoor_t"] == "-":
        pending.append("🌳 outdoor workout")
    if g["photo"] and st["photo_t"] == "-":
        pending.append("📸 progress photo")
    lines = [f"You're out early, {NAME}. About {hrs:.0f} hours of free time before tonight. Use it well:", read,
             "2. A 20-minute walk or a stretch session", "3. Plan tomorrow: three things that must happen",
             "4. Drink water and eat a proper meal"]
    if pending:
        lines.append("Still open today: " + ", ".join(pending))
    lines.append("Text me the page you reach.")
    return "\n".join(lines)


def wake_call(st):
    text = pick(f"{st['today']}wc", [
        f"Good morning, {NAME}! ☀️ Day {st['day']}.\nToday: text me you're up by {st['wake']}, send your workout video by {st['workout']}, and text me when you're at work by {st['work']}.\nLet's go.",
        f"Rise and shine, {NAME}. Day {st['day']} is yours. 💪\nUp by {st['wake']}. Workout video by {st['workout']}. At work by {st['work']}.",
    ])
    g = st["targets"]
    extra = []
    if g["water"]:
        extra.append(f"💧 {g['water']} ml water")
    if g["pages"]:
        extra.append(f"📖 {g['pages']} pages")
    if g["outdoor"]:
        extra.append("🌳 an outdoor workout")
    if g["photo"]:
        extra.append("📸 a progress photo")
    if extra:
        text += "\nAlso today: " + ", ".join(extra) + "."
    if st["targets"]["music"]:
        link = PLAYLISTS.get("wake")
        text += "\n🎧 WAKEUP" + (f" {link}" if link else "")
    voice = (f"Good morning, {NAME}. Day {st['day']}. Text me when you're up by {spoken(st['wake'])}, "
             f"then get your workout done. You've got this.")
    return text, voice


def welcome_text(st, today):
    when = "Day 1 starts on " + START.strftime("%d %B") + "." if today < START else "You're on the clock. Let's go."
    return (f"👋 Welcome, {NAME}! Your Daily Compass is on.\n{when}\n\n"
            "How it works:\n"
            "• Type / to see every command, or send /menu for tap buttons.\n"
            "• 🎥 videos count as your workout, 📸 photos as your progress photo.\n"
            "• /wd starts your evening wind-down: screen time, how you feel and why, your diary and tomorrow's wake-up time.\n"
            "• Say 'sleeping' as you drift off and 'awake' when you wake: I track how long you sleep (/sleepstats).\n"
            "• Add reminders by typing, for example: remind daily 7am drink water\n"
            "• Send /help any time for everything I understand.")


def help_text():
    return (
        f"Here's what I understand, {NAME}. Type / to see the command menu; /menu shows tap buttons.\n"
        "CHECK-INS\n• /awake or \"I'm awake\" • a 🎥 video • /atwork • /leaving\n"
        "• /outdoor, or a video with 'outdoor' in the caption • a 📸 photo = progress photo\n"
        "TASKS\n• /water 500 (or water -250, water 1 l) • /pages 10 or read to page 80\n"
        "• book: <title>, page <n>\n"
        "SLEEP\n• sleeping (or /sleep) when you actually fall asleep • awake when you wake up (before 05:00 it counts as a night wake, then say sleeping again)\n"
        "• napping (or /nap), then awake • I slept at 11pm (add a night afterwards) • /sleepstats • set sleep 8h (your goal)\n"
        "TIMES\n• Every clock time you give me is read back for you to confirm (and I ask AM or PM when it could be either). \"in 20 minutes\" needs no confirmation.\n"
        "FEELINGS\n• Just say it: I feel bored tonight. I save it to your diary and ask why.\n"
        "EVENING\n• /wd wind-down: screen time, feelings + why, diary, tomorrow's wake-up time • screen 3h20 • /feel (quick check-in)\n"
        "• /wake 6:30 (set tomorrow's wake-up call) • playlist wake <spotify link> (also stressed, low, tired, frustrated, calm, good, focus)\n"
        "• /diary (read today) | /diary 2026-11-05 | /diary <write an entry> • /feelings • /export (diary file)\n"
        "• /cancel stops a question I'm waiting on\n"
        "REMINDERS\n• remind 15:00 call mum • remind daily 7am drink water • remind in 10 minutes stretch\n"
        "• remind every mon,wed 18:00 gym • reminders | remove 2 | change 2 to 4:30pm | clear reminders\n"
        "• quiet 22:30-05:00 | quiet off\n"
        "FOCUS, MOOD\n• focus 25 | mood 4\n"
        "STATS\n• /stats | /status | buddy <chat id> | buddy off\n"
        "SETTINGS\n• set wake 05:45 (also workout, work, wakecall, midday, leavecheck, review; add off to disable)\n"
        "• set water 3000 | set pages 10 | set glass 250 | set photo off | set outdoor off | set screen 3h (daily limit) | set screen off\n"
        "• set music off (no wake-up marker or music suggestions) | set winddown 22:00 (when I invite you to /wd)"
    )


def status_text(st):  # noqa
    g = st["targets"]
    return (f"Day {st['day']} | Streak {st['streak']} (best {st['best']}) | Slips this week {st['slips']}/{st['free']} free | Freezes {st['freezes']}\n"
            f"Today: wake {st['wake_t']}, workout {st['workout_t']}, work {st['work_t']}\n"
            f"Tasks: water {st['water']}/{g['water']} ml, pages {st['pages']}/{g['pages']}, "
            f"outdoor {st['outdoor_t']}, photo {st['photo_t']}\n"
            f"Book: {st['book']}, page {st['page']}\n"
            f"Deadlines: wake {st['wake']}, workout {st['workout']}, work {st['work']}\n"
            f"Reminders: {len(st['reminders'])}")


def apply_setrule(st, key, val):
    key = {"leave": "leavecheck"}.get(key, key)
    low = val.lower().strip()
    off = low in ("off", "none", "stop", "disable", "no")
    on = low in ("on", "yes", "enable")
    if key == "screen":
        if off:
            st["targets"]["screen"] = 0
            return "Done. The screen-time limit is off."
        v = parse_screen(val)
        if not v:
            return "Try: set screen 3h (a daily limit), or set screen off"
        st["targets"]["screen"] = v
        return f"Done. Your daily screen-time limit is {fmt_min(v)}. Log it in /wd or with: screen 3h20. Going over, or not logging it, counts as a miss."
    if key == "sleep":
        v = parse_screen(val)
        if off or not v or not 180 <= v <= 840:
            return "Try: set sleep 8h (or 7h30). It can be between 3h and 14h."
        st["sl_goal"] = v
        return f"Done. Your sleep goal is now {fmt_min(v)}."
    if key in ("water", "pages", "glass"):
        n = re.search(r"\d+(?:\.\d+)?", low)
        if off and key != "glass":
            st["targets"][key] = 0
            return f"Done. The {key} goal is off, so it no longer counts toward your day."
        if not n:
            return f"Try: set {key} {DEFAULT_TARGETS[key]}"
        v = float(n[0])
        if key == "water" and v <= 10:
            v *= 1000  # litres
        if v < 1 or v > 20000:
            return "That number looks off. Try again."
        st["targets"][key] = int(v)
        return f"Done. Your {key} {'goal' if key != 'glass' else 'button size'} is now {int(v)}{' ml' if key in ('water', 'glass') else ' pages'}."
    if key in ("photo", "outdoor", "music"):
        if not (on or off):
            return f"Try: set {key} on  or  set {key} off"
        st["targets"][key] = int(on)
        what = "Music suggestions and the wake-up music marker are" if key == "music" else f"The {key} task is"
        return f"Done. {what} now {'on' if on else 'off'}."
    hm = None if off else find_time(val)[0]
    if key in ("wake", "workout", "work"):
        if not hm:
            return "I couldn't read that time. Try: set wake 05:45 or set wake 5:45am"
        st[key] = hm
        return f"Done. Your {key} deadline is now {hm}."
    if not off and not hm:
        return "I couldn't read that time. Try 05:45 or 5:45am, or say off."
    st["times"][key] = "off" if off else hm
    return f"Done. {key} is now {'off' if off else hm}."


# ------------------------------------------------------------------ keyboards


def keyboard_for(st):
    """Tap buttons, shown only when you send /menu (they hide again after one tap)."""
    g = st["targets"]
    r1 = []
    if st["wake_t"] == "-":
        r1.append(L_AWAKE)
    if st["workout_t"] == "-":
        r1.append(L_VIDEO)
    if st["work_t"] == "-":
        r1.append(L_WORK)
    elif st["left_t"] == "-":
        r1.append(L_LEAVE)
    r2 = []
    if g["water"] and st["water"] < g["water"]:
        r2.append(f"💧 +{g['glass']} ml")
    if g["pages"] and st["pages"] < g["pages"]:
        r2.append("📖 +5 pages")
    if g["outdoor"] and st["outdoor_t"] == "-":
        r2.append(L_OUTDOOR)
    r3 = []
    if g["photo"] and st["photo_t"] == "-":
        r3.append(L_PHOTO)
    r3.append(L_FOCUS)
    if st["mood"] == "-":
        r3.append(L_MOOD)
    rows = [[{"text": b} for b in r] for r in (r1, r2, r3, [L_WD, L_STATS, L_REM, L_HELP]) if r]
    return {"keyboard": rows, "resize_keyboard": True, "one_time_keyboard": True}


REMOVE_KB = {"remove_keyboard": True}


def emotion_buttons():
    row = lambda items: [{"text": f"{f} {n}", "callback_data": f"emo:{n}"} for n, f in items]
    return {"inline_keyboard": [row(EMOTIONS[:3]), row(EMOTIONS[3:])]}


def reminder_buttons():
    return {"inline_keyboard": [[{"text": "✅ Done", "callback_data": "done"},
                                 {"text": "💤 Snooze 10 min", "callback_data": "snz:10"}]]}


def mood_buttons():
    faces = ["😞", "😕", "😐", "🙂", "😄"]
    return {"inline_keyboard": [[{"text": f"{faces[i]} {i + 1}", "callback_data": f"mood:{i + 1}"} for i in range(5)]]}


BOT_COMMANDS = [("wd", "Evening wind-down: screen time, feelings, diary"), ("awake", "Log that you're up"),
                ("sleep", "Log that you're falling asleep"), ("nap", "Log a nap"), ("sleepstats", "Your sleep average and history"),
                ("atwork", "Log arriving at work"), ("leaving", "Log leaving work"), ("water", "Log water, e.g. /water 500"),
                ("pages", "Log reading, e.g. /pages 10"), ("outdoor", "Log your outdoor workout"),
                ("photo", "Send your progress photo"), ("video", "Log your workout video"),
                ("focus", "Focus timer, e.g. /focus 25"), ("feel", "Quick feelings check-in"), ("mood", "Rate your mood"),
                ("wake", "Set tomorrow's wake-up call, e.g. /wake 6:30"), ("playlist", "Set your Spotify playlists"),
                ("diary", "Read or write your diary"), ("feelings", "Your feelings log"), ("export", "Send me my diary file"),
                ("stats", "Streak, heatmap, trends"), ("status", "Today at a glance"), ("reminders", "Your reminders"),
                ("menu", "Show tap buttons"), ("cancel", "Cancel what I'm asking"), ("help", "Everything I understand"),
                ("start", "Start the Compass")]


# ----------------------------------------------------------------- day closing


def close_day(st, d, test_note=False):
    """Finish day d: update streak, slips, freezes and history. Returns the verdict text."""
    st["reviewed"] = str(d)
    if d < START:
        return "Today was a test, so nothing was counted."
    g = st["targets"]
    misses = []
    for label, got, dl in (("wake", st["wake_t"], st["wake"]), ("workout video", st["workout_t"], st["workout"]),
                           ("at work", st["work_t"], st["work"])):
        if got == "-":
            misses.append(f"{label} missing")
        elif mins(got) > mins(dl):
            misses.append(f"{label} late ({got})")
    if g["water"] and st["water"] < g["water"]:
        misses.append(f"water {st['water']}/{g['water']} ml")
    if g["pages"] and st["pages"] < g["pages"]:
        misses.append(f"reading {st['pages']}/{g['pages']} pages")
    if g["outdoor"] and st["outdoor_t"] == "-":
        misses.append("outdoor workout missing")
    if g["photo"] and st["photo_t"] == "-":
        misses.append("progress photo missing")
    if g["screen"]:
        if st["ev_date"] != str(d) or st["ev_screen"] == "-":
            misses.append("screen time not logged")
        elif int(st["ev_screen"]) > g["screen"]:
            misses.append(f"screen time {fmt_min(int(st['ev_screen']))} (limit {fmt_min(g['screen'])})")
    if not misses:
        st["streak"] += 1
        st["best"] = max(st["best"], st["streak"])
        verdict = f"Perfect day. Streak: {st['streak']}. 🔥"
        if st["streak"] % 7 == 0 and st["freezes"] < 3:
            st["freezes"] += 1
            verdict += f"\n🧊 You earned a streak freeze (you have {st['freezes']})."
        if st["day"] >= 75:
            verdict += "\n🏆 75 days complete! That is a huge achievement, Joseph."
        day_set(st, d, "P")
        return verdict
    st["slips"] += 1
    if st["slips"] <= st["free"]:
        day_set(st, d, "S")
        return f"Slip: {', '.join(misses)}. That uses your free slip this week. Streak kept at {st['streak']}."
    if st["freezes"] > 0:
        st["freezes"] -= 1
        day_set(st, d, "F")
        return f"Slip: {', '.join(misses)}. 🧊 A streak freeze saved your streak ({st['streak']}). Freezes left: {st['freezes']}."
    day_set(st, d, "X")
    st["streak"], st["slips"], st["day"] = 0, 0, 0
    return f"Slip: {', '.join(misses)}. No free slips or freezes left, so the streak resets. Tomorrow is a fresh Day 1."


# -------------------------------------------------------------------- engine


def msg(text, markup=None):
    return {"k": "msg", "text": text, "markup": markup}


def run(st, updates, now):
    """Apply messages, buttons, reminders and due phases. Returns (state, outbox, voice_text)."""
    out, voice = [], None
    today = now.date()
    cur = now.hour * 60 + now.minute
    test = today < START
    started = st["started"] != "-"

    # day rollover
    roll_at = min(4 * 60 + 45, (tm(st, "wakecall") or 300) - 15)
    if st["today"] != str(today) and cur >= roll_at:
        old = st["today"]
        if started and old != "none" and st["reviewed"] != old:
            try:
                od = pdate(old)
                if od >= START:
                    out.append(f"I closed out yesterday for you, {NAME}. " + close_day(st, od))
            except ValueError:
                pass
        if today >= START:
            st["day"] += 1
        monday = today - timedelta(days=today.weekday())
        if st["week"] != str(monday):
            st["slips"], st["week"] = 0, str(monday)
        if st["next_wake"] != "-":
            apply_wake(st, st["next_wake"])
            st["next_wake"] = "-"
        st.update(today=str(today), wake_t="-", workout_t="-", work_t="-", left_t="-", sent=[],
                  water=0, pages=0, photo_t="-", outdoor_t="-", mood="-")
        if today >= START and st["notes"].startswith("TEST DAY"):
            st["notes"] = "-"

    def pages_add(n):
        st["pages"] += n
        if st["page"].isdigit():
            st["page"] = str(int(st["page"]) + n)

    def ev_touch():
        if st["ev_date"] != str(today):
            st.update(ev_date=str(today), ev_screen="-", ev_emo="-", ev_why="-", ev_diary="-", ev_shot="-")

    def log_diary(kind, text):
        out.append({"k": "diary_add", "ts": now.strftime("%Y-%m-%d %H:%M"), "kind": kind,
                    "text": re.sub(r"\s+", " ", text).strip()})

    def clear_await():
        st["aw_state"], st["aw_emo"], st["aw_at"] = "-", "-", "-"

    def set_screen(minutes):
        ev_touch()
        st["ev_screen"] = str(minutes)
        day_set(st, today, screen=minutes)
        log_diary("screen", f"{minutes} min ({fmt_min(minutes)})")

    def ask_emotion():
        st["aw_state"], st["aw_at"] = "wd_emotion", stamp(now)
        out.append(msg("2) How are you feeling tonight? Tap one, or type your own word.", emotion_buttons()))

    def ask_why(emo, solo):
        st["aw_state"], st["aw_emo"], st["aw_at"] = ("feel_why" if solo else "wd_why"), emo, stamp(now)
        out.append(f"What's behind feeling {nice(emo)}? A few words is enough (or type skip).")

    def ask_diary():
        st["aw_state"], st["aw_at"] = "wd_diary", stamp(now)
        out.append("3) Anything else on your mind? Write it as tonight's diary entry, or type done.")

    def record_emotion(emo, word=None):
        ev_touch()
        w = clean(emo.lower(), 14).replace(" ", "_")
        emo = FEEL_WORDS.get(w, w)
        if word is None and emo != w:
            word = nice(w)
        st["ev_emo"] = emo
        day_set(st, today, emo=EMO_CH.get(emo, "o"))
        log_diary("feeling", nice(emo) + (f" (you said: {word})" if word and word != nice(emo) else ""))
        return emo

    def suggest_music(name, once_flag=True):
        if not st["targets"]["music"] or name not in MUSIC_QUERY:
            return
        flag = f"music_{name}"
        if once_flag and flag in st["sent"]:
            return
        st["sent"].append(flag)
        own = name in PLAYLISTS
        out.append(f"🎧 Music for this moment ({name}){'' if own else ' (a search for you, set your own with: playlist ' + name + ' <link>)'}:\n{music_url(name)}")

    def ask_wake():
        st["aw_state"], st["aw_at"] = "wd_wake", stamp(now)
        cur_wc = st["next_wake"] if st["next_wake"] != "-" else st["times"]["wakecall"]
        out.append(f"4) What time should I wake you tomorrow? Type like 6:30 or 7am, or 'same' to keep {cur_wc}.")

    def finish_wd():
        clear_await()
        st["ev_done"] = stamp(now)
        parts = ["🌙 Saved for tonight:"]
        if st["ev_screen"] != "-":
            line = f"• Screen time: {fmt_min(int(st['ev_screen']))}"
            if st["targets"]["screen"]:
                line += f" (limit {fmt_min(st['targets']['screen'])})"
            parts.append(line)
        if st["ev_emo"] != "-":
            parts.append(f"• Feeling: {EMO_FACE.get(st['ev_emo'], '🙂')} {nice(st['ev_emo'])}" + (f", because {st['ev_why']}" if st["ev_why"] != "-" else ""))
        if st["ev_diary"] != "-":
            parts.append("• Diary entry ✓")
        if len(parts) == 1:
            parts.append("• (nothing logged)")
        if cur <= mins(REFLECT_AT):
            parts.append(f"Your reflection arrives around {REFLECT_AT}. Everything is also in /diary.")
        else:
            parts.append(f"It's after {REFLECT_AT}, so tonight's reflection has passed. It's all saved in /diary.")
        parts.append(f"Wake-up call tomorrow: {st['next_wake'] if st['next_wake'] != '-' else st['times']['wakecall']}.")
        parts.append("When you're in bed and drifting off, send 'sleeping' and I'll track your sleep.")
        parts.append(f"Sleep well, {NAME}. 🌙")
        out.append("\n".join(parts))

    def handle_await(text):
        s = st["aw_state"]
        low = text.strip().lower()
        skip = low in ("skip", "no", "none", "-", "pass", "nothing")
        if s == "wd_screen":
            if skip:
                ask_emotion()
                return
            v = parse_screen(text)
            if v is None:
                out.append("I couldn't read that. Type it like 3h20, 3.5h or 200 (minutes), or type skip.")
                return
            set_screen(v)
            lim = st["targets"]["screen"]
            out.append(f"📱 {fmt_min(v)}" + (f" ({'over' if v > lim else 'within'} your {fmt_min(lim)} limit)." if lim else " saved."))
            ask_emotion()
        elif s == "wd_emotion":
            if skip:
                ask_diary()
                return
            fe = extract_feeling(text)
            emo = record_emotion(fe[0], fe[1]) if fe else record_emotion(text)
            if fe and fe[2]:
                ev_touch()
                st["ev_why"] = clean(fe[2], 120)
                log_diary("why", f"{nice(emo)}: {fe[2]}")
                ask_diary()
            else:
                ask_why(emo, False)
            suggest_music(emo)
        elif s in ("wd_why", "feel_why"):
            emo = st["aw_emo"]
            if not skip:
                ev_touch()
                st["ev_why"] = clean(text, 120)
                log_diary("why", f"{nice(emo)}: {text}")
            if s == "feel_why":
                clear_await()
                out.append("Saved. I'll remember it for your diary and reflection. 🧡")
            else:
                ask_diary()
        elif s == "wd_diary":
            if not (low in ("done", "skip", "no", "none", "that's all", "thats all", "nothing")):
                ev_touch()
                st["ev_diary"] = clean(text, 250)
                log_diary("diary", text)
            ask_wake()
        elif s == "wd_wake":
            if low in ("same", "skip", "keep", "no", "-", "done"):
                finish_wd()
                return
            slot = parse_clock(text)
            if slot is None:
                out.append("I couldn't read that time. Try 6:30 AM or 7am, or type same.")
                return
            cf_start("wdwake", [slot])

    # ---- time confirmation: nothing that contains a clock time is saved until you confirm it
    def cf_clear():
        st["cf_kind"], st["cf_stage"], st["cf_at"], st["cf_data"] = "-", "-", "-", "-"

    def cf_set(kind, stage, a):
        st["cf_kind"], st["cf_stage"], st["cf_at"] = kind, stage, stamp(now)
        st["cf_data"] = json.dumps(a, separators=(",", ":"))

    def cf_get():
        if st["cf_kind"] == "-":
            return None
        at = pstamp(st["cf_at"])
        try:
            a = json.loads(st["cf_data"])
        except ValueError:
            a = None
        if not at or not isinstance(a, dict) or now - at > timedelta(minutes=CONFIRM_MINUTES):
            cf_clear()
            return None
        return st["cf_kind"], st["cf_stage"], a

    def cf_label(kind, a):
        x = a.get("x", {})
        if kind in ("setwake", "wdwake"):
            return "Setting tomorrow's wake-up call"
        if kind == "setrule":
            return f"Setting {x.get('key', 'time')}"
        if kind == "quiet":
            return "Setting quiet hours (" + ("start" if a.get("i", 0) == 0 else "end") + " time)"
        if kind == "remind":
            return f"New reminder: {x.get('text', '')}"
        if kind == "redit":
            return f"Moving reminder {x.get('id')} ({x.get('text', '')})"
        if kind == "sleepat":
            return "Logging when you fell asleep"
        if kind == "wakeq":
            return "Fixing your wake-up time"
        return "Logging when you fell back asleep"

    def cf_statement(kind, a):
        t, x = a["t"], a.get("x", {})
        if kind in ("setwake", "wdwake"):
            return f"Wake you tomorrow at {pretty(t[0])}"
        if kind == "setrule":
            return f"Set {x['key']} to {pretty(t[0])}"
        if kind == "quiet":
            return f"Quiet hours from {pretty(t[0])} to {pretty(t[1])}"
        if kind == "remind":
            rem, err = parse_reminder(x["arg"].replace("{T0}", t[0]), now.date(), now.hour * 60 + now.minute)
            if rem:
                return f"Remind you at {pretty(rem['time'])}, {describe(rem['rule'], now.date())}: {rem['text']}"
            return f"Remind you at {pretty(t[0])}: {x.get('text', '')}"
        if kind == "redit":
            return f"Move reminder {x['id']} ({x.get('text', '')}) to {pretty(t[0])}"
        if kind == "sleepat":
            dt = latest_before(now, t[0])
            return f"You fell asleep at {pretty(t[0])} ({'today' if dt.date() == now.date() else 'yesterday'})"
        if kind == "wakeq":
            return f"You actually woke up at {pretty(t[0])}"
        return f"You fell back asleep at {pretty(t[0])}"

    def cf_show(kind, a, mid=None):
        amb = [i for i, v in enumerate(a["t"]) if v.endswith("?")]
        if amb:
            i = amb[0]
            a["i"] = i
            raw = a["t"][i].rstrip("?")
            text = f"🕐 {cf_label(kind, a)}\nDid you mean {raw} AM or {raw} PM?"
            kb = {"inline_keyboard": [[{"text": f"🌅 {raw} AM", "callback_data": "cf:am"}, {"text": f"🌙 {raw} PM", "callback_data": "cf:pm"}],
                                      [{"text": "✖ Cancel", "callback_data": "cf:no"}]]}
            cf_set(kind, "ampm", a)
        else:
            text = f"🕐 {cf_label(kind, a)}\n{cf_statement(kind, a)}. Is that right?"
            kb = {"inline_keyboard": [[{"text": "✅ Confirm", "callback_data": "cf:yes"}, {"text": "✏️ Change", "callback_data": "cf:chg"},
                                       {"text": "✖ Cancel", "callback_data": "cf:no"}]]}
            cf_set(kind, "confirm", a)
        if mid:
            out.append({"k": "edit", "message_id": mid, "text": text, "markup": kb})
        else:
            out.append(msg(text, kb))

    def cf_start(kind, slots, x=None, mid=None):
        a = {"t": list(slots), "x": x or {}}
        if len(slots) == 1 and slots[0].endswith("?"):
            a["d"] = 1  # tapping AM or PM is the confirmation
        cf_show(kind, a, mid)

    def cf_say(mid, text):
        out.append({"k": "edit", "message_id": mid, "text": text} if mid else text)

    def cf_run(kind, a, mid):
        cf_clear()
        if mid:
            out.append({"k": "edit", "message_id": mid, "text": "✅ " + cf_statement(kind, a) + "."})
        cf_exec(kind, a)

    def cf_act(action, val=None, mid=None):
        c = cf_get()
        if not c:
            return False
        kind, stage, a = c
        if action == "no":
            cf_clear()
            cf_say(mid, "✖ Cancelled. Nothing was changed.")
            if kind == "wdwake":
                out.append("What time should I wake you tomorrow? Type like 6:30 AM, or 'same' to keep it.")
            return True
        if action in ("am", "pm") and stage == "ampm":
            i = a.get("i", 0)
            a["t"][i] = resolve_slot(a["t"][i], action)
            if a.get("d"):
                cf_run(kind, a, mid)
            else:
                cf_show(kind, a, mid)
            return True
        if action == "yes" and stage == "confirm":
            cf_run(kind, a, mid)
            return True
        if action == "chg" and stage in ("confirm", "ampm"):
            if len(a["t"]) != 1:
                cf_clear()
                cf_say(mid, "Okay, send the command again with the corrected times.")
                return True
            cf_set(kind, "ask", a)
            cf_say(mid, "✏️ Send the correct time, like 7:30 AM or 19:30 (or type cancel).")
            return True
        if action == "time" and len(a["t"]) <= 1 and stage in ("confirm", "ampm", "ask"):
            a["t"] = [val]
            a.pop("d", None)
            if val.endswith("?"):
                a["d"] = 1
            cf_show(kind, a, mid)
            return True
        if action == "same" and stage == "ask" and kind in ("wakeq", "gapq"):
            cf_clear()
            cf_say(mid, "Okay, I'll keep it as it is.")
            return True
        return False

    CF_YES = {"yes", "y", "yeah", "yep", "yup", "ok", "okay", "confirm", "confirmed", "correct", "right", "sure", "👍", "✅", "that's right", "thats right"}
    CF_NO = {"no", "n", "nope", "nah", "cancel", "stop", "never mind", "nevermind", "wrong"}

    def cf_text(text0):
        """Handle a typed reply to a pending confirmation. True if the message was used."""
        c = cf_get()
        if not c:
            return False
        kind, stage, a = c
        low = re.sub(r"[.!]+$", "", text0.strip().lower())
        if low in CF_NO:
            return cf_act("no")
        if stage == "confirm" and low in CF_YES:
            return cf_act("yes")
        if stage == "confirm" and low in ("change", "edit", "change it", "change time"):
            return cf_act("chg")
        if stage == "ampm" and low in ("am", "a.m", "a.m.", "a", "morning"):
            return cf_act("am")
        if stage == "ampm" and low in ("pm", "p.m", "p.m.", "p", "evening", "night", "afternoon"):
            return cf_act("pm")
        if stage == "ask" and low in ("same", "keep", "skip", "keep it"):
            return cf_act("same")
        slot = parse_clock(low)
        if slot and (stage == "ask" or re.search(r"[:.]|am|pm|a\.m|p\.m", low)):
            return cf_act("time", slot)
        return False

    def remind_flow(arg, ts):
        if RE_IN.search(arg):  # "in 20 minutes" is a fixed amount: no confirmation needed
            out.append(handle_remind(st, arg, ts))
            return
        m = RE_REM_TIME.search(arg)
        slot = parse_clock(re.sub(r"^at\s+", "", m[0].strip(), flags=re.I)) if m else None
        if not slot:
            out.append(handle_remind(st, arg, ts))  # explains what is missing
            return
        if len(st["reminders"]) >= MAX_REMINDERS or len(render(st)) > 3500:
            out.append(handle_remind(st, arg, ts))
            return
        marked = (arg[:m.start()] + " {T0} " + arg[m.end():]).strip()
        cands = [slot] if not slot.endswith("?") else [resolve_slot(slot, "am"), resolve_slot(slot, "pm")]
        good, err = None, None
        for c in cands:
            rem, e = parse_reminder(marked.replace("{T0}", c), ts.date(), ts.hour * 60 + ts.minute)
            if rem and not good:
                good = rem
            err = err or e
        if not good:
            out.append(err + "\nExamples: remind 15:00 call mum | remind daily 7am drink water | remind in 20 minutes stretch")
            return
        cf_start("remind", [slot], {"arg": marked, "text": good["text"]})

    def cf_exec(kind, a):
        t, x = a["t"], a.get("x", {})
        if kind == "setwake":
            st["next_wake"] = t[0]
            out.append(f"⏰ Tomorrow's wake-up call is set for {t[0]} ({plain12(t[0])}; awake deadline {hm_of(mins(t[0]) + WAKE_DEADLINE_AFTER)}, "
                       f"workout video by about {hm_of(mins(t[0]) + WORKOUT_DEADLINE_AFTER)}). It applies from tomorrow morning.")
        elif kind == "wdwake":
            st["next_wake"] = t[0]
            out.append(f"⏰ Wake-up call set for {t[0]} ({plain12(t[0])}) tomorrow (awake deadline {hm_of(mins(t[0]) + WAKE_DEADLINE_AFTER)}).")
            finish_wd()
        elif kind == "setrule":
            out.append(apply_setrule(st, x["key"], t[0]))
        elif kind == "quiet":
            st["quiet"] = f"{t[0]}-{t[1]}"
            out.append(f"Quiet hours set: {t[0]} to {t[1]}. Reminders in that window wait until {t[1]}.")
        elif kind == "remind":
            out.append(handle_remind(st, x["arg"].replace("{T0}", t[0]), now))
        elif kind == "redit":
            r = next((q for q in st["reminders"] if q["id"] == x["id"]), None)
            if r:
                r["time"], r["last"] = t[0], "-"
                out.append(f"Reminder {r['id']} moved to {t[0]} ({plain12(t[0])}): {r['text']}")
            else:
                out.append(f"I don't have a reminder {x['id']} any more.")
        elif kind == "sleepat":
            sleep_at_exec(t[0])
        elif kind == "wakeq":
            sleep_fix_end(t[0], x)
        elif kind == "gapq":
            sleep_regap(t[0], x)

    # ---- sleep tracking
    def sleep_reset():
        st["sl_state"], st["sl_since"], st["sl_gaps"], st["sl_gapat"] = "-", "-", 0, "-"

    def sleep_logged(rec, note=""):
        text = sleep_done_text(st, rec, today) + note
        log_diary("sleep", text.split("\n")[0])
        return text

    def sleep_ask(kind, text, x):
        cf_set(kind, "ask", {"t": [], "x": x})
        out.append(text)

    def handle_sleeping(ts, how=None):
        state, since = st["sl_state"], pstamp(st["sl_since"])
        if state == "asleep" and since and ts - since <= timedelta(minutes=MAX_SESSION):
            out.append(f"💤 I'm already tracking your sleep since {since:%H:%M}. Say 'awake' when you wake up.")
            return
        if state == "wake" and since and ts - since <= timedelta(minutes=MAX_SESSION):
            gapat = pstamp(st["sl_gapat"]) or ts
            gap = max(0, int((ts - gapat).total_seconds() // 60))
            st["sl_gaps"] += gap
            st["sl_state"], st["sl_gapat"] = "asleep", "-"
            out.append(f"😴 Back to sleep at {ts:%H:%M}. You were awake for {gap} min ({st['sl_gaps']} min in total tonight). Rest well. 🌙")
            return
        if state == "nap" and since and ts - since <= timedelta(minutes=MAX_SESSION):
            out.append(f"😴 You're already napping since {since:%H:%M}. Say 'awake' to end the nap.")
            return
        if state != "-":
            sleep_reset()
            out.append("💤 Your last sleep was never closed, so I left it out of your averages.")
        if how is None and 6 <= ts.hour < 18:
            out.append(msg(f"It's {ts:%H:%M}. Is this your night sleep or a nap?",
                           {"inline_keyboard": [[{"text": "🌙 Night sleep", "callback_data": f"slk:night:{ts:%Y-%m-%dT%H:%M}"},
                                                 {"text": "😴 Nap", "callback_data": f"slk:nap:{ts:%Y-%m-%dT%H:%M}"}]]}))
            return
        st["sl_state"], st["sl_since"] = ("nap" if how == "nap" else "asleep"), f"{ts:%Y-%m-%dT%H:%M}"
        if how == "nap":
            out.append(f"😴 Nap started at {ts:%H:%M}. Say 'awake' when you're up.")
        else:
            wc = st["next_wake"] if st["next_wake"] != "-" else st["times"]["wakecall"]
            out.append(f"😴 Sleep started at {ts:%H:%M}. Goodnight, {NAME}. 🌙\n"
                       f"Woken in the night? Say 'awake', then 'sleeping' again and I'll subtract the time awake.\nWake-up call: {wc}.")

    def sleep_wake_mode(ts):
        state = st["sl_state"]
        if state == "nap":
            return "nap"
        if state in ("asleep", "wake"):
            since = pstamp(st["sl_since"])
            if since and ts - since > timedelta(minutes=MAX_SESSION):
                return "morning"
            cutoff = min(NIGHT_CUTOFF, (tm(st, "wakecall") or 300) - 30)
            return "night" if minutes_of(ts) < cutoff else "morning"
        return "none"

    def sleep_night_wake(ts):
        if st["sl_state"] == "wake":
            g = pstamp(st["sl_gapat"])
            out.append(f"🌙 I already have you awake since {g:%H:%M}. Say 'sleeping' when you're back asleep." if g else "🌙 Say 'sleeping' when you're back asleep.")
            return
        st["sl_state"], st["sl_gapat"] = "wake", f"{ts:%Y-%m-%dT%H:%M}"
        out.append(msg(f"🌙 Awake at {ts:%H:%M}, noted. Say 'sleeping' when you're back asleep and I'll subtract the time awake.\n"
                       "If you're up for the day, tap below.",
                       {"inline_keyboard": [[{"text": "☀️ I'm up for the day", "callback_data": "sl:up"}]]}))

    def sleep_nap_end(ts):
        since = pstamp(st["sl_since"])
        sleep_reset()
        n = int((ts - since).total_seconds() // 60) if since else 0
        if n < MIN_NAP:
            out.append("😴 That nap was under 5 minutes, so I didn't count it.")
        elif n > 8 * 60:
            out.append("😴 That nap would be over 8 hours, so I left it out. If it was a night's sleep, use 'sleeping' at night.")
        else:
            sl_add_nap(st, since.date(), n)
            text = f"😴 Nap logged: {fmt_min(n)} ({since:%H:%M} to {ts:%H:%M}). Naps are kept out of your night averages."
            log_diary("sleep", text)
            out.append(text)

    def sleep_morning(ts):
        since = pstamp(st["sl_since"])
        if not since or ts - since > timedelta(minutes=MAX_SESSION):
            sleep_reset()
            out.append("💤 I never got an end for your last sleep, so I left it out of your averages. Tonight, send 'sleeping' when you drift off.")
            return
        if st["sl_state"] == "wake":
            gapat = pstamp(st["sl_gapat"]) or ts
            rec, why = sl_write(st, since, gapat, st["sl_gaps"])
            sleep_reset()
            if not rec:
                out.append("😴 That sleep was too short to count.")
                return
            out.append(sleep_logged(rec, f"\nI ended it at {gapat:%H:%M}, when you woke in the night and didn't tell me you went back to sleep."))
            sleep_ask("gapq", f"Did you fall back asleep after {gapat:%H:%M}? Send the time you fell asleep again (like 4:00 AM), or reply 'no'.",
                      {"awake": f"{ts:%Y-%m-%dT%H:%M}"})
            return
        net = int((ts - since).total_seconds() // 60) - st["sl_gaps"]
        if MIN_SLEEP <= net < SHORT_NIGHT:
            sleep_reset()
            sl_add_nap(st, since.date(), net)
            text = f"😴 That was {fmt_min(net)} ({since:%H:%M} to {ts:%H:%M}), under {fmt_min(SHORT_NIGHT)}, so I counted it as a nap and kept it out of your night averages."
            log_diary("sleep", text)
            out.append(text)
            return
        rec, why = sl_write(st, since, ts, st["sl_gaps"])
        sleep_reset()
        if not rec:
            out.append("😴 That sleep was under 10 minutes, so I didn't count it." if why == "short" else "😴 I couldn't work out that sleep.")
            return
        out.append(sleep_logged(rec))
        wc = tm(st, "wakecall") or 300
        if minutes_of(ts) > wc + LATE_AWAKE_AFTER or rec["dur"] > LONG_SLEEP:
            sleep_ask("wakeq", f"⏰ You logged awake at {ts:%H:%M}, which is late. Did you actually wake up earlier? Send the real time (like 8:30 AM), "
                      f"or reply 'same' if {ts:%H:%M} is right.", {"def": f"{ts:%H:%M}", "max": f"{ts:%Y-%m-%dT%H:%M}"})

    def sleep_fix_end(hm, x):
        last = sl_last_parts(st)
        if not last:
            out.append("I don't have a finished sleep to fix.")
            return
        start, gaps, old_end = last
        mx = pstamp(x.get("max")) or old_end
        end = latest_before(mx, hm)
        if end <= start:
            out.append(f"That would be before you fell asleep ({start:%H:%M}), so I kept it as it was.")
            return
        sl_del_night(st, old_end.date())
        rec, why = sl_write(st, start, end, gaps)
        out.append("✅ Updated. " + sleep_logged(rec) if rec else "I couldn't use that time, so I kept it as it was.")

    def sleep_regap(hm, x):
        last = sl_last_parts(st)
        awake = pstamp(x.get("awake"))
        if not last or not awake:
            out.append("I don't have that sleep any more.")
            return
        start, gaps, old_end = last
        again = latest_before(awake, hm)
        if again <= old_end or again >= awake:
            out.append(f"That doesn't fit between {old_end:%H:%M} and {awake:%H:%M}, so I kept it as it was.")
            return
        sl_del_night(st, old_end.date())
        rec, why = sl_write(st, start, awake, gaps + int((again - old_end).total_seconds() // 60))
        out.append("✅ Updated. " + sleep_logged(rec) if rec else "I couldn't use that time, so I kept it as it was.")

    def sleep_at_exec(hm):
        dt = latest_before(now, hm)
        state = st["sl_state"]
        if state in ("asleep", "wake"):
            st["sl_since"] = f"{dt:%Y-%m-%dT%H:%M}"
            out.append(f"✅ Got it: you fell asleep at {dt:%H:%M}. I'm still tracking, say 'awake' when you wake up.")
            return
        if state == "nap":
            out.append("You're napping right now. Send 'awake' to end the nap first.")
            return
        last = sl_last_parts(st)
        if last and last[2].date() == today and dt < last[2]:
            sl_del_night(st, last[2].date())
            rec, why = sl_write(st, dt, last[2], last[1])
            out.append("✅ Updated. " + sleep_logged(rec) if rec else "That doesn't fit with when you woke up, so I kept it as it was.")
            return
        if st["wake_t"] != "-" and st["today"] == str(today):
            end = pstamp(f"{today}T{st['wake_t']}")
            if end and dt < end:
                rec, why = sl_write(st, dt, end, 0)
                out.append(sleep_logged(rec) if rec else "That sleep was too short to count.")
                return
        st["sl_state"], st["sl_since"], st["sl_gaps"], st["sl_gapat"] = "asleep", f"{dt:%Y-%m-%dT%H:%M}", 0, "-"
        out.append(f"✅ Sleep started at {dt:%H:%M}. Say 'awake' when you wake up.")

    def handle_awake(ts, t, seed, force=False):
        mode = "morning" if force and st["sl_state"] in ("asleep", "wake") else sleep_wake_mode(ts)
        if mode == "night":
            sleep_night_wake(ts)
            return
        if mode == "nap":
            sleep_nap_end(ts)
            return
        if st["wake_t"] == "-":
            st["wake_t"] = t
            out.append(ack(t, st["wake"], today < START, seed))
        else:
            out.append(f"Already logged you up at {st['wake_t']}. 👍")
        if mode == "morning":
            sleep_morning(ts)
        elif ts.hour < 12 and "sleephint" not in st["sent"] and not any(n["date"] == ts.date() for n in sl_nights(st)):
            st["sent"].append("sleephint")
            out.append("💤 I didn't catch when you fell asleep. Add it with: I slept at 11pm (I'll confirm the time). Tonight, send 'sleeping' as you drift off.")

    def handle_feeling(emo, word, why):
        emo = record_emotion(emo, word)
        if word in FEEL_DARK or emo in FEEL_DARK:
            out.append(f"I'm sorry it feels this heavy, {NAME}. You don't have to carry it alone. Please tell someone you trust tonight, "
                       "and if you ever feel unsafe, contact local emergency services or a crisis line right away. 🧡")
        if why:
            ev_touch()
            st["ev_why"] = clean(why, 120)
            log_diary("why", f"{nice(emo)}: {why}")
            clear_await()
            out.append(f"Noted: feeling {nice(emo)} ({why}). Saved to your diary. 🧡")
        else:
            ask_why(emo, True)
        suggest_music(emo)

    def answers_await(kind, text0, is_cmd_):
        """Is this message the answer to the question I'm waiting on, rather than a new command?"""
        if kind in EXPLICIT or kind in ("workout", "outdoor", "photo", "bedhint") or is_cmd_:
            return False
        if st["aw_state"] in ("feel_why", "wd_why", "wd_diary"):
            if kind in ("water", "pages", "focus", "mood"):
                return False
            if kind == "feeling":
                return st["aw_state"] != "feel_why"
            if kind in ("atwork", "leave", "awake", "page", "book"):
                return len(text0.split()) > 3
        return True

    if st["aw_state"] != "-":
        try:
            if now - datetime.strptime(st["aw_at"], "%Y-%m-%dT%H:%M").replace(tzinfo=TZ) > timedelta(hours=AWAIT_HOURS):
                clear_await()
        except ValueError:
            clear_await()

    for u in updates:
        if "callback_query" in u:
            cq = u["callback_query"]
            data = cq.get("data", "")
            mtxt = (cq.get("message") or {}).get("text", "")
            mid = (cq.get("message") or {}).get("message_id")
            if data == "done":
                out.append({"k": "answer", "id": cq["id"], "text": "Nice work! ✅"})
                out.append({"k": "edit", "message_id": mid, "text": "✅ Done: " + re.sub(r"^⏰ Reminder: ", "", mtxt)})
            elif data.startswith("snz:"):
                mins_ahead = int(data.split(":")[1])
                body = re.sub(r"\s*\(a bit late\)$", "", re.sub(r"^⏰ Reminder: ", "", mtxt))
                t_, rule, text = after(now, mins_ahead, body)
                add_reminder(st, t_, rule, text)
                out.append({"k": "answer", "id": cq["id"], "text": f"Snoozed {mins_ahead} min 💤"})
                out.append({"k": "edit", "message_id": mid, "text": f"💤 Snoozed until {t_}: {body}"})
            elif data.startswith("emo:"):
                emo = record_emotion(data.split(":", 1)[1])
                out.append({"k": "answer", "id": cq["id"], "text": f"{nice(emo)} noted"})
                out.append({"k": "edit", "message_id": mid, "text": f"Feeling: {EMO_FACE.get(emo, '🙂')} {nice(emo)}"})
                ask_why(emo, st["aw_state"] != "wd_emotion")
                suggest_music(emo)
            elif data.startswith("mood:"):
                n = int(data.split(":")[1])
                st["mood"] = str(n)
                day_set(st, today, mood=n)
                out.append({"k": "answer", "id": cq["id"], "text": "Saved 🙂"})
                out.append({"k": "edit", "message_id": mid, "text": f"Mood saved: {['😞', '😕', '😐', '🙂', '😄'][n - 1]} {n}/5"})
                if n <= 2:
                    suggest_music("low")
            elif data.startswith("cf:"):
                action = data.split(":", 1)[1]
                if cf_act(action, None, mid):
                    out.append({"k": "answer", "id": cq["id"], "text": ""})
                else:
                    out.append({"k": "answer", "id": cq["id"], "text": "That one already expired or was handled."})
                    out.append({"k": "edit", "message_id": mid, "text": mtxt + "\n(expired, please send it again)"})
            elif data.startswith("slk:"):
                _, how, st_ts = data.split(":", 2)
                when = pstamp(st_ts) or now
                out.append({"k": "answer", "id": cq["id"], "text": "Noted"})
                out.append({"k": "edit", "message_id": mid, "text": f"{'😴 Nap' if how == 'nap' else '🌙 Night sleep'} at {when:%H:%M}"})
                handle_sleeping(when, how)
            elif data in ("sl:up", "sl:still"):
                out.append({"k": "answer", "id": cq["id"], "text": "Noted"})
                if data == "sl:up":
                    out.append({"k": "edit", "message_id": mid, "text": f"☀️ Up at {now:%H:%M}"})
                    handle_awake(now, f"{now:%H:%M}", f"{cq['id']}up", force=True)
                else:
                    out.append({"k": "edit", "message_id": mid, "text": "💤 Okay, rest a little longer. I'll check on you again soon."})
            else:
                out.append({"k": "answer", "id": cq["id"], "text": ""})
            continue
        m = u.get("message") or {}
        ts = datetime.fromtimestamp(m.get("date", 0), TZ)
        t = ts.strftime("%H:%M")
        kind, data = classify(m)
        seed = f"{u['update_id']}{kind}"
        if kind == "start":
            st["started"] = st["started"] if started else str(today)
            started = True
            out.append(msg(welcome_text(st, today)))
            out.append({"k": "commands"})
            continue
        if not started:
            out.append(msg("👋 Tap /start to begin."))
            continue
        g = st["targets"]
        text0 = re.sub(r"^(/\w+)@\w+", r"\1", (m.get("text") or m.get("caption") or "").strip())
        is_cmd = text0.startswith("/") or text0 in LABELS or RE_WATER_BTN.match(text0) or RE_PAGES_BTN.match(text0)
        if text0 and kind not in ("workout", "outdoor", "photo") and cf_text(text0):
            continue
        if st["aw_state"] != "-" and text0 and answers_await(kind, text0, is_cmd):
            handle_await(text0)
            continue
        if kind == "awake":
            handle_awake(ts, t, seed)
        elif kind == "sleeping":
            handle_sleeping(ts)
        elif kind == "nap":
            handle_sleeping(ts, "nap")
        elif kind == "sleepat":
            cf_start("sleepat", [parse_clock(data)])
        elif kind == "sleepstats":
            out.append(sleep_stats_text(st, now))
        elif kind == "bedhint":
            out.append(f"Rest well, {NAME}. 🌙 Send 'sleeping' once you actually drift off and I'll start your sleep timer." if st["sl_state"] == "-"
                       else f"Sweet dreams, {NAME}. I'm already tracking your sleep. 💤")
        elif kind == "feeling":
            handle_feeling(*data)
        elif kind == "workout":
            if st["workout_t"] == "-":
                st["workout_t"] = t
                out.append(ack(t, st["workout"], test, seed) + " Video received. 🎥")
            else:
                out.append(f"Another video, nice. Your workout is already logged at {st['workout_t']}. "
                           "If this was your outdoor workout, tap 🌳 Outdoor workout.")
        elif kind == "outdoor":
            if st["outdoor_t"] == "-":
                st["outdoor_t"] = t
                out.append(f"🌳 Outdoor workout logged at {t}. Great, {NAME}.")
            else:
                out.append(f"Outdoor workout already logged at {st['outdoor_t']}. 👍")
        elif kind == "photo" and st["aw_state"].startswith("wd_"):
            ev_touch()
            ph = m.get("photo") or []
            st["ev_shot"] = clean((ph[-1].get("file_id") if ph else (m.get("document") or {}).get("file_id")) or "x", 120)
            out.append("Screenshot saved 📱." + (" Now type the total, like 3h20 (or type skip)." if st["aw_state"] == "wd_screen" else ""))
        elif kind == "photo":
            if st["photo_t"] == "-":
                st["photo_t"] = t
                out.append(f"📸 Progress photo logged at {t}.")
            else:
                out.append(f"Photo received. Your progress photo was already logged at {st['photo_t']}.")
        elif kind == "atwork":
            if st["work_t"] == "-":
                st["work_t"] = t
                out.append(ack(t, st["work"], test, seed))
            else:
                out.append(f"Already logged you at work at {st['work_t']}. 👍")
        elif kind == "leave":
            st["left_t"] = t
            out.append(early_leave_plan(st, t))
        elif kind == "hint_video":
            out.append("Tap 📎 and send me your workout video when you're done. 🎥")
        elif kind == "hint_photo":
            out.append("Send me your progress photo now (as a photo, not a file). 📸")
        elif kind == "water":
            val, unit = data
            if unit.startswith("glass") or (unit == "" and abs(val) <= 20):
                ml = val * g["glass"]
            elif unit.startswith("l"):
                ml = val * 1000
            else:
                ml = val
            st["water"] = max(0, st["water"] + int(ml))
            if g["water"]:
                pct = min(100, round(100 * st["water"] / g["water"]))
                done = st["water"] >= g["water"]
                out.append(f"💧 {st['water']}/{g['water']} ml ({pct}%)." + (" 🎉 Water goal reached!" if done else ""))
            else:
                out.append(f"💧 {st['water']} ml today.")
        elif kind == "pages":
            pages_add(data)
            tail = f" Now on page {st['page']}." if st["page"].isdigit() else ""
            if g["pages"]:
                out.append(f"📖 {st['pages']}/{g['pages']} pages today." + tail + (" 🎉 Reading goal reached!" if st["pages"] >= g["pages"] else ""))
            else:
                out.append(f"📖 {st['pages']} pages today." + tail)
        elif kind == "focus":
            minutes, label = data or (25, "")
            minutes = max(1, min(minutes, 180))
            t_, rule, _ = after(now, minutes, "")
            add_reminder(st, t_, rule, f"🍅 Focus done ({minutes} min){': ' + label if label else ''}. Take a 5 min break.")
            out.append(f"🍅 Focus started: {minutes} minutes. I'll ping you at {t_}. No phone, just the task.")
            suggest_music("focus")
        elif kind == "mood":
            if data:
                st["mood"] = str(data)
                day_set(st, today, mood=data)
                out.append(f"Mood saved: {data}/5.")
            else:
                out.append(msg("How are you feeling today?", mood_buttons()))
        elif kind == "journal":
            ev_touch()
            st["ev_diary"] = clean(data, 250)
            log_diary("diary", data)
            out.append("📝 Saved to your diary. /diary shows today's entries, /export sends the whole file.")
        elif kind == "wd":
            ev_touch()
            st["aw_state"], st["aw_emo"], st["aw_at"] = "wd_screen", "-", stamp(now)
            lim = g["screen"]
            out.append(f"🌙 Wind-down, {NAME}. Let's close the day gently.\n"
                       "1) Screen time today: type the total, like 3h20, 3.5h or 200 (minutes)."
                       + (f" Your limit is {fmt_min(lim)}." if lim else "")
                       + "\nYou can send the screenshot first if you like, or type skip. (/cancel stops anything.)")
        elif kind == "screen":
            v = parse_screen(data) if data else None
            if v is None:
                out.append("Type it like: screen 3h20, screen 3.5h or screen 200 (minutes).")
            else:
                set_screen(v)
                lim = g["screen"]
                out.append(f"📱 Screen time saved: {fmt_min(v)}" + (f" ({'over' if v > lim else 'within'} your {fmt_min(lim)} limit)." if lim else "."))
                if st["aw_state"] == "wd_screen":
                    ask_emotion()
        elif kind == "setwake":
            cf_start("setwake", [parse_clock(data)])
        elif kind == "playlist":
            parts = data.split(None, 1)
            if not parts:
                lines = [f"• {n}: {PLAYLISTS.get(n, 'a Spotify search (set your own)')}" for n in PLAYLIST_NAMES]
                out.append("🎧 Your music links:\n" + "\n".join(lines) + "\nSet one: playlist stressed <spotify link>  |  reset: playlist stressed reset")
            elif parts[0].lower() not in PLAYLIST_NAMES:
                out.append("Names: " + ", ".join(PLAYLIST_NAMES) + ". Example: playlist stressed https://open.spotify.com/playlist/...")
            elif len(parts) == 2 and parts[1].strip().lower() == "reset":
                out.append({"k": "playlist_set", "name": parts[0].lower(), "url": ""})
                out.append(f"Reset: {parts[0].lower()} goes back to a Spotify search.")
            elif len(parts) == 2 and re.match(r"^(https?://|spotify:)\S+$", parts[1].strip()):
                url = parts[1].strip().split("?")[0] if "open.spotify.com" in parts[1] else parts[1].strip()
                out.append({"k": "playlist_set", "name": parts[0].lower(), "url": url})
                out.append(f"🎧 Saved your {parts[0].lower()} playlist.")
            else:
                out.append("Send it like: playlist stressed https://open.spotify.com/playlist/...")
        elif kind == "feel":
            out.append(msg("How are you feeling right now?", emotion_buttons()))
        elif kind == "cancel":
            clear_await()
            cf_clear()
            out.append("Okay, cancelled.")
        elif kind == "menu":
            out.append(msg("Tap one 👇 (the buttons hide after you tap).", keyboard_for(st)))
        elif kind == "diary_show":
            out.append({"k": "diary_show", "date": data or str(today)})
        elif kind == "feelings":
            out.append({"k": "feelings"})
        elif kind == "export":
            out.append({"k": "export"})
        elif kind == "quiet":
            low = data.lower()
            if low in ("off", "none", "no"):
                st["quiet"] = "off"
                out.append("Quiet hours are off.")
            else:
                parts = re.split(r"\s*(?:-|to|until)\s*", low)
                a = parse_clock(parts[0]) if parts else None
                b = parse_clock(parts[1]) if len(parts) > 1 else None
                if a and b:
                    cf_start("quiet", [a, b])
                else:
                    out.append("Try: quiet 22:30-05:00  or  quiet off")
        elif kind == "buddy":
            if data.lower() == "off":
                st["buddy"] = "off"
                out.append("Weekly summary sharing is off.")
            elif re.fullmatch(r"-?\d{5,15}", data):
                st["buddy"] = data
                out.append("Done. Your weekly summary will also go to that chat. Your friend must have started this bot first.")
            else:
                out.append("Try: buddy <their Telegram chat id>  or  buddy off")
        elif kind == "stats":
            out.append(stats_text(st))
        elif kind == "book":
            st["book"], st["page"] = data
            out.append(f"Saved: {st['book']}, page {st['page']}. 📚")
        elif kind == "page":
            newp = int(data)
            if st["page"].isdigit() and newp > int(st["page"]):
                pages_add(newp - int(st["page"]))
                prog = f" +{st['pages']} pages today" + (f" of {g['pages']}" if g["pages"] else "")
                out.append(f"Page {newp} saved, nice reading, {NAME}.{prog}. 📖")
            else:
                st["page"] = str(newp)
                out.append(f"Page {newp} saved. 📖")
        elif kind == "setrule":
            key, val = data
            slot = parse_clock(val) if key in TIME_KEYS and val.lower().strip() not in ("off", "none", "stop", "disable", "no") else None
            if slot:
                cf_start("setrule", [slot], {"key": key})
            else:
                out.append(apply_setrule(st, key, val))
        elif kind == "remind":
            remind_flow(data, ts)
        elif kind == "rlist":
            out.append(reminders_text(st, ts.date()))
        elif kind == "rremove":
            before = len(st["reminders"])
            st["reminders"] = [r for r in st["reminders"] if r["id"] != data]
            out.append(f"Reminder {data} removed. 🗑️" if len(st["reminders"]) < before else f"I don't have a reminder {data}. Send \"reminders\" to see the list.")
        elif kind == "rclear":
            n = len(st["reminders"])
            st["reminders"] = []
            out.append(f"Cleared {n} reminder(s).")
        elif kind == "redit":
            rid, rest = data
            r = next((x for x in st["reminders"] if x["id"] == rid), None)
            if not r:
                out.append(f"I don't have a reminder {rid}. Send \"reminders\" to see the list.")
            elif rest.lower().startswith("text "):
                r["text"] = rest[5:].replace("|", "/").strip()[:60] or r["text"]
                out.append(f"Reminder {rid} text updated.")
            else:
                slot = parse_clock(rest)
                if slot:
                    cf_start("redit", [slot], {"id": rid, "text": r["text"]})
                else:
                    out.append("I couldn't read that time. Try: change 2 to 16:30 or change 2 to 4:30pm")
        elif kind == "status":
            out.append(status_text(st))
        elif kind == "help":
            out.append(help_text())
        else:
            out.append("Noted. I only understand check-ins and commands for now. Send /help to see them.")

    if not started:
        wc = tm(st, "wakecall")
        if today >= START and wc is not None and wc <= cur <= wc + 180 and "startnudge" not in st["sent"]:
            out.append(msg(f"👋 Good morning, {NAME}! Tap /start to switch your Compass on for today."))
            st["sent"].append("startnudge")
        return st, out, voice

    if st["cmdver"] != CMDVER:
        st["cmdver"] = CMDVER
        out.append({"k": "commands"})
        out.append("🆕 Updated! Type / to see every command. /menu shows tap buttons (they only appear when you ask). "
                   "New: a sleep tracker (send 'sleeping' as you drift off and 'awake' when you wake, /sleepstats shows your average), "
                   "I confirm every clock time you give me before saving it, and 'I feel ...' goes straight into your diary.")

    # custom reminders (work every day, including test days). Quiet hours defer them to the morning.
    keep = []
    for r in st["reminders"]:
        t = mins(r["time"])
        d = today
        eff = t
        if in_quiet(st, t) and not r["rule"].startswith("soon:"):
            qe = mins(st["quiet"].split("-")[1])
            eff = qe
            if t >= qe:  # late-evening reminder waits for tomorrow morning
                d = today - timedelta(days=1)
        if rule_matches(r["rule"], d) and r["last"] != str(d) and eff <= cur <= eff + 120:
            late = " (a bit late)" if cur - eff > 10 else ""
            out.append(msg(f"⏰ Reminder: {r['text']}{late}", reminder_buttons()))
            r["last"] = str(d)
            if is_once(r["rule"]):
                continue
        elif is_once(r["rule"]) and r["rule"][5:] < str(today - timedelta(days=1)):
            continue
        keep.append(r)
    st["reminders"] = keep

    if test:
        return st, out, voice

    def once(flag, start, cond, text, max_late=180):
        nonlocal voice
        if start is None or flag in st["sent"] or not cond:
            return
        if start <= cur <= start + max_late:
            if isinstance(text, tuple):
                out.append(text[0])
                voice = text[1]
            else:
                out.append(text)
            st["sent"].append(flag)

    wake_d, work_out_d, work_d = mins(st["wake"]), mins(st["workout"]), mins(st["work"])
    once("wakecall", tm(st, "wakecall"), True, wake_call(st), 6 * 60)
    sleeping_now = st["sl_state"] in ("asleep", "wake")
    once("sleepcheck", wake_d, st["wake_t"] == "-" and sleeping_now,
         msg(f"☀️ It's {st['wake']}, {NAME}. Are you awake? Or just checking in. Tap below, or text me when you're up.",
             {"inline_keyboard": [[{"text": "☀️ I'm awake", "callback_data": "sl:up"}, {"text": "💤 Still sleeping", "callback_data": "sl:still"}]]}), 120)
    once("wakenudge", wake_d + 1, st["wake_t"] == "-" and not sleeping_now,
         f"It's past {st['wake']}, {NAME}. Text me you're up and get moving. ⏰", 120)
    once("wakenudge2", wake_d + 45, st["wake_t"] == "-",
         f"{NAME}, still nothing from you. Up now, workout next.", 120)
    once("workoutask", work_out_d - 30, st["wake_t"] != "-" and st["workout_t"] == "-",
         f"Workout video deadline is {st['workout']}, {NAME}. 30 minutes left.", 29)
    once("workoutnudge", work_out_d + 1, st["workout_t"] == "-",
         f"Workout video deadline passed ({st['workout']}). Send it when it's done, {NAME}. And remember to text me when you get to work by {st['work']}.", 120)
    once("worknudge", work_d + 1, st["work_t"] == "-",
         f"It's past {st['work']}, {NAME}. Text me when you're at work.", 120)
    g = st["targets"]
    open_tasks = []
    if g["water"] and st["water"] < g["water"]:
        open_tasks.append(f"💧 {st['water']}/{g['water']} ml")
    if g["pages"] and st["pages"] < g["pages"]:
        open_tasks.append(f"📖 {st['pages']}/{g['pages']} pages")
    if g["outdoor"] and st["outdoor_t"] == "-":
        open_tasks.append("🌳 outdoor workout")
    if g["photo"] and st["photo_t"] == "-":
        open_tasks.append("📸 photo")
    midday_txt = pick(f"{st['today']}md", [f"Midday check, {NAME}. How's the day going?", f"Halfway there, {NAME}. Stay on it."])
    if open_tasks:
        midday_txt += " Still open: " + ", ".join(open_tasks) + "."
    once("midday", tm(st, "midday"), True, midday_txt, 120)
    once("leavecheck", tm(st, "leavecheck"), st["left_t"] == "-",
         f"How's work, {NAME}? Tap 🚪 Leaving work when you leave and I'll plan your evening.", 120)

    wd_done = st["ev_date"] == str(today) and st["ev_done"] != "-"
    wdt = tm(st, "winddown")
    once("winddown", wdt, not wd_done,
         f"🌙 Evening, {NAME}. Ready to wind down? Send /wd: screen time, how you feel and why, your diary, and tomorrow's wake-up time.", 120)
    once("winddown2", None if wdt is None else wdt + 30, not wd_done and "winddown" in st["sent"],
         "Still up? Whenever you're ready, /wd takes about two minutes.", 60)
    once("winddown3", None if wdt is None else wdt + 60, not wd_done and "winddown" in st["sent"],
         "Last nudge for tonight: /wd, then rest. Tomorrow starts early.", 60)

    rv = tm(st, "review")
    if rv is not None and "review" not in st["sent"] and rv <= cur <= rv + 179 and st["reviewed"] != str(today):
        verdict = close_day(st, today)
        left = max(st["free"] - st["slips"], 0)
        out.append(f"Evening review, {NAME}.\n{verdict}\nFree slips left this week: {left}.\n"
                   f"Tomorrow: up by {st['wake']}. Tap 😊 Mood and tell me about your day with journal: <one line>. 📖")
        st["sent"].append("review")
        if today.weekday() == 6 and "weekly" not in st["sent"]:
            wk = weekly_text(st, today)
            out.append(wk)
            st["sent"].append("weekly")
            if st["buddy"] != "off":
                out.append({"k": "buddy", "to": st["buddy"], "text": f"{NAME}'s weekly summary:\n" + wk})
    return st, out, voice


# ------------------------------------------------------------------- telegram

WRITES = ("sendMessage", "editMessageText", "pinChatMessage", "answerCallbackQuery", "setMyCommands")


class Telegram:
    def __init__(self, token, chat, dry):
        self.token, self.chat, self.dry = token, chat, dry

    def call(self, method, **params):
        if self.dry and method in WRITES:
            print(f"[DRY {method}] {params.get('text', '')} {params.get('reply_markup', '')[:80]}")
            return {"ok": True, "result": {"message_id": 0}}
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/{method}", data=data)
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            return json.loads(e.read().decode() or "{}")

    def tracker(self):
        """(message id, text) of the pinned tracker; (None, None) only if it is really missing.
        A failed request raises, so a Telegram hiccup can never be mistaken for a lost tracker."""
        import time
        for attempt in range(2):
            r = self.call("getChat", chat_id=self.chat)
            if not r.get("ok"):
                raise RuntimeError("getChat failed: " + str(r)[:100])
            pm = (r.get("result") or {}).get("pinned_message")
            if pm and "COMPASS TRACKER" in (pm.get("text") or ""):
                return pm["message_id"], pm["text"]
            if attempt == 0:
                time.sleep(1.5)  # ask once more before deciding it is gone
        return None, None

    def send_document(self, path, caption=""):
        if self.dry:
            print(f"[DRY sendDocument] {path}")
            return {"ok": True}
        import uuid
        boundary = uuid.uuid4().hex
        with open(path, "rb") as f:
            data = f.read()
        head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{self.chat}\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{os.path.basename(path)}\"\r\n"
                "Content-Type: text/plain\r\n\r\n").encode("utf-8")
        body = head + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/sendDocument", data=body,
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            return json.loads(e.read().decode() or "{}")

    def send(self, text, markup=None, chat=None):
        p = dict(chat_id=chat or self.chat, text=text[:4000])
        if markup:
            p["reply_markup"] = json.dumps(markup)
        return self.call("sendMessage", **p)


def load_env_file(path="compass.env"):
    """Optional KEY=VALUE file next to the script (used on shared hosting)."""
    full = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    if os.path.exists(full):
        for line in open(full, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


DIARY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diary.txt")


def diary_lines():
    try:
        return open(DIARY, encoding="utf-8").read().splitlines()
    except OSError:
        return []


def send_long(tg, text, markup=None):
    while text:
        chunk, text = text[:3800], text[3800:]
        tg.send(chunk, markup)


def deliver(tg, chat, st, out):
    for item in out:
        if isinstance(item, str):
            tg.send(item, REMOVE_KB)
        elif item["k"] == "msg":
            tg.send(item["text"], item["markup"] or REMOVE_KB)
        elif item["k"] == "playlist_set":
            keep = {}
            try:
                for line in open(PLAY_FILE, encoding="utf-8"):
                    if "=" in line:
                        a, b = line.strip().split("=", 1)
                        keep[a] = b
            except OSError:
                pass
            if item["url"]:
                keep[item["name"]] = item["url"]
            else:
                keep.pop(item["name"], None)
            try:
                with open(PLAY_FILE, "w", encoding="utf-8") as f:
                    f.write("".join(f"{a}={b}\n" for a, b in keep.items()))
            except OSError:
                pass
            load_playlists()
        elif item["k"] == "diary_add":
            try:
                with open(DIARY, "a", encoding="utf-8") as f:
                    f.write(f"[{item['ts']}] {item['kind']}: {item['text']}\n")
            except OSError:
                pass
        elif item["k"] == "diary_show":
            lines, d = diary_lines(), item["date"]
            sel = [x for x in lines if x.startswith(f"[{d}")]
            if sel:
                send_long(tg, f"📓 Your diary for {d}:\n" + "\n".join(sel), REMOVE_KB)
            elif lines:
                send_long(tg, f"📓 Nothing for {d} yet. Your latest entries:\n" + "\n".join(lines[-12:]), REMOVE_KB)
            else:
                tg.send("📓 Your diary is empty. Run /wd, or write one with: /diary <text>", REMOVE_KB)
        elif item["k"] == "feelings":
            sel = [x for x in diary_lines() if "] feeling:" in x or "] why:" in x][-24:]
            send_long(tg, ("🧡 Your feelings log:\n" + "\n".join(sel)) if sel else "🧡 No feelings logged yet. Try /feel or /wd.", REMOVE_KB)
        elif item["k"] == "export":
            if diary_lines():
                tg.send_document(DIARY, "📓 Your diary")
            else:
                tg.send("📓 Your diary is empty, so there's nothing to export yet.", REMOVE_KB)
        elif item["k"] == "edit":
            tg.call("editMessageText", chat_id=chat, message_id=item["message_id"], text=item["text"],
                    reply_markup=json.dumps(item.get("markup") or {"inline_keyboard": []}))
        elif item["k"] == "answer":
            tg.call("answerCallbackQuery", callback_query_id=item["id"], text=item["text"])
        elif item["k"] == "buddy":
            tg.send(item["text"], chat=item["to"])
        elif item["k"] == "commands":
            tg.call("setMyCommands", commands=json.dumps([{"command": c, "description": d} for c, d in BOT_COMMANDS]))


BACKUP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracker_backup.txt")


def fit(st, text):
    """Telegram messages hold 4096 characters: shorten diary snippets, then drop the oldest history days."""
    if len(text) > 4000:
        st["ev_diary"], st["ev_why"] = clean(st["ev_diary"], 60), clean(st["ev_why"], 60)
        text = render(st, updated=st["updated"])
    while len(text) > 4000 and len(st["hist"]) > 14:
        date_cut(st)
        text = render(st, updated=st["updated"])
    while len(text) > 4000 and len(st["sl_nights"]) > 8 * 14:
        st["sl_nights"], st["sl_naps"] = st["sl_nights"][8:], st["sl_naps"][1:]
        st["sl_start"] = str(pdate(st["sl_start"]) + timedelta(days=1))
        text = render(st, updated=st["updated"])
    return text


def date_cut(st):
    cut = 10
    for k in SEQ:
        st[k] = st[k][cut:]
    st["hist_start"] = str(pdate(st["hist_start"]) + timedelta(days=cut))
    return cut


def cycle(tg, chat, dry, fake_now=None, poll=0):
    """One read-reply-save pass. With poll > 0, waits up to that many seconds for new messages."""
    load_playlists()
    mid, old_text = tg.tracker()
    restored = False
    if not old_text and not dry and os.path.exists(BACKUP):
        try:
            old_text = open(BACKUP, encoding="utf-8").read()  # the pinned message was lost: use the host's copy
            restored = bool(old_text)
        except OSError:
            old_text = None
    st = parse(old_text) if old_text else new_state()

    r = tg.call("getUpdates", offset=st["last_id"] + 1, timeout=poll,
                allowed_updates=json.dumps(["message", "callback_query"]))
    if not r.get("ok"):
        print("getUpdates failed:", r)
        return False
    now = fake_now or datetime.now(TZ)

    def chat_of(u):
        x = u.get("message") or (u.get("callback_query") or {}).get("message") or {}
        return str((x.get("chat") or {}).get("id"))

    updates = [u for u in r["result"] if chat_of(u) == str(chat)]
    if r["result"]:
        st["last_id"] = max(u["update_id"] for u in r["result"])

    st, out, voice = run(st, updates, now)
    deliver(tg, chat, st, out)
    if voice:
        with open("voice_text.txt", "w") as f:
            f.write(voice)

    base = render(st, updated=parse(old_text)["updated"] if old_text else "setup")
    if base != (old_text or "") or (restored and not mid):
        text = fit(st, render(st, updated=now.strftime("%Y-%m-%d %H:%M")))
        if not dry:
            try:
                open(BACKUP, "w", encoding="utf-8").write(text)
            except OSError:
                pass
        if mid:
            res = tg.call("editMessageText", chat_id=chat, message_id=mid, text=text)
            if not res.get("ok"):
                print("edit failed:", res)
        else:
            res = tg.send(text)
            if not dry and res.get("ok"):
                tg.call("pinChatMessage", chat_id=chat, message_id=res["result"]["message_id"], disable_notification="true")
    if updates or out:
        print(f"ok {now:%Y-%m-%d %H:%M:%S} updates={len(updates)} sent={len(out)}")
    return True


def hard_stop(seconds):
    """Kill this process after `seconds` no matter what (a hung connection must never hold the lock)."""
    try:
        import signal
        signal.alarm(seconds)  # default SIGALRM action ends the process, which releases the lock
    except (ImportError, AttributeError):
        pass


def take_lock(path):
    """Return an open lock file, or None if a healthy copy is already listening.
    A holder that stopped updating the file for 100+ seconds is stuck: it gets killed and replaced."""
    import time
    try:
        import fcntl
    except ImportError:
        return open(path, "w")
    for attempt in range(2):
        f = open(path, "a+")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            f.seek(0)
            f.truncate()
            f.write(f"{os.getpid()} {int(time.time())}")
            f.flush()
            return f
        except OSError:
            f.seek(0)
            info = f.read().split()
            age = time.time() - int(info[1]) if len(info) == 2 and info[1].isdigit() else 999
            if attempt == 0 and age > 100:
                print(f"stale lock (holder {info[:1]}, {int(age)}s old): replacing it")
                try:
                    import signal
                    os.kill(int(info[0]), signal.SIGKILL)
                except Exception:
                    pass
                time.sleep(2)
                try:  # a fresh file also frees us if the old holder could not be killed
                    os.remove(path)
                except OSError:
                    pass
                continue
            return None
    return None


def alert(tg, text):
    """Tell Joseph once an hour at most that something is wrong."""
    import time
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".alert")
    try:
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < 3600:
            return
        open(path, "w").write(str(time.time()))
        tg.send("⚠️ " + text)
    except Exception:
        pass


def heartbeat(lock):
    import time
    try:
        lock.seek(0)
        lock.truncate()
        lock.write(f"{os.getpid()} {int(time.time())}")
        lock.flush()
    except Exception:
        pass


def main():
    import socket
    socket.setdefaulttimeout(30)
    load_env_file()
    token, chat = os.environ["TELEGRAM_TOKEN"], os.environ["CHAT_ID"]
    dry = os.environ.get("DRY_RUN") == "1"
    fake_now = datetime.strptime(os.environ["FAKE_NOW"], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=TZ) if os.environ.get("FAKE_NOW") else None
    tg = Telegram(token, chat, dry)

    test_time = os.environ.get("TEST_TIME", "").strip()
    if test_time:
        # Safe test mode: play a fake morning at TEST_TIME on Day 1. Never edits the tracker.
        _, old_text = tg.tracker()
        st = parse(old_text) if old_text else new_state()
        st["reminders"], st["started"], st["cmdver"] = [], "test", CMDVER
        h, m = test_time.split(":")
        now = datetime(START.year, START.month, START.day, int(h), int(m), tzinfo=TZ)
        st.update(day=1, today=str(START), wake_t="-", workout_t="-", work_t="-", left_t="-", sent=[], reviewed="-")
        st, out, voice = run(st, [], now)
        shown = [o if isinstance(o, str) else o.get("text", "") for o in out]
        for text in shown:
            if text:
                tg.send(f"🧪 TEST ({test_time}): {text}")
        if not any(shown):
            tg.send(f"🧪 TEST ({test_time}): nothing would be sent at this time.")
        if voice:
            with open("voice_text.txt", "w") as f:
                f.write(voice)
        print(f"test ok {test_time} sent={len(shown)}")
        return

    if "--loop" in sys.argv:
        import time
        lock = take_lock(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".lock"))
        if lock is None:
            print("another copy is already listening, exiting")
            return
        idx = sys.argv.index("--loop") + 1
        seconds = int(sys.argv[idx]) if idx < len(sys.argv) and sys.argv[idx].isdigit() else 58
        hard_stop(seconds + 25)
        end = time.time() + seconds
        errors = 0
        while True:
            left = end - time.time()
            if left <= 3:
                break
            heartbeat(lock)
            try:
                ok = cycle(tg, chat, dry, fake_now, poll=int(min(10, left - 2)))
                errors = 0 if ok else errors
            except Exception as e:  # keep listening through brief network errors
                import traceback
                errors += 1
                print("cycle error:", type(e).__name__, e)
                if errors == 1:
                    traceback.print_exc()
                if errors >= 3:
                    alert(tg, f"The Compass hit an error ({type(e).__name__}: {e}). I'll keep retrying. Please check run.log.")
                    return  # stop this run; the next cron minute tries again, without flooding the log
                ok = False
            if not ok:
                time.sleep(3)
        return

    hard_stop(75)
    try:
        ok = cycle(tg, chat, dry, fake_now)
    except Exception as e:
        import traceback
        traceback.print_exc()
        alert(tg, f"The Compass hit an error ({type(e).__name__}: {e}). Please check run.log.")
        sys.exit(1)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
