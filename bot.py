#!/usr/bin/env python3
"""Joseph's Daily Compass v4: a Telegram accountability bot. Standard library only.

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
CMDVER = 5                         # bump to re-run the one-time clean-up (removes the old "/" command menu)
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


# ----------------------------------------------------- numbers, times, wording


def clock(hm):
    """'07:30' -> '7:30 am', '15:00' -> '3 pm'."""
    h, m = [int(x) for x in hm.split(":")]
    h12 = h % 12 or 12
    ap = "am" if h < 12 else "pm"
    return f"{h12} {ap}" if m == 0 else f"{h12}:{m:02d} {ap}"


def amt(ml):
    """Water amounts the way people say them: '500 ml', '1.5 L', '3 L'."""
    ml = int(ml)
    return f"{ml / 1000:g} L" if ml >= 1000 else f"{ml} ml"


WORDNUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
           "forty": 40, "forty five": 45, "forty-five": 45, "sixty": 60}
SMALLWORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve")


def parse_screen_loose(text):
    """Screen time in minutes from natural phrasing ('about 3 and a half hours', 'three hours 20 min', '3h20'); None if unreadable."""
    low = text.lower().replace(",", ".")
    low = re.sub(r"\b(\d+(?:\.\d+)?)\s+and\s+a\s+half\b", lambda m: str(float(m[1]) + 0.5), low)
    low = re.sub(r"\bhalf\s+an?\s+hour\b", "30 minutes", low)
    low = re.sub(r"\ban?\s+hour\b", "1 hour", low)
    for w in SMALLWORDS:
        low = re.sub(r"\b%s\b" % w, str(WORDNUM[w]), low)
    v = parse_screen(low.strip())
    if v is not None:
        return v
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hours?)\b\s*(?:and\s*)?(?:(\d+)\s*(?:m|min|mins|minutes?)?)?", low)
    if m:
        v = int(round(float(m[1]) * 60 + (int(m[2]) if m[2] else 0)))
    else:
        m = re.search(r"\b(\d{1,2}):(\d{2})\b", low)
        if m:
            v = int(m[1]) * 60 + int(m[2])
        else:
            m = re.search(r"(\d+)\s*(?:m|min|mins|minutes?)\b", low)
            if m:
                v = int(m[1])
            else:
                m = re.fullmatch(r"\D{0,12}(\d+(?:\.\d+)?)\D{0,12}", low.strip())
                if not m:
                    return None
                x = float(m[1])
                v = int(round(x * 60 if x <= 16 else x))
    return v if 0 <= v <= 1440 else None


def parse_duration(s):
    """(minutes, start, end) for 'in 10 minutes', 'in an hour', 'in half an hour', 'in 2 hours'; else None."""
    low = s.lower()
    m = re.search(r"\bin\s+half\s+an?\s+hour\b", low)
    if m:
        return 30, m.start(), m.end()
    m = re.search(r"\bin\s+(?:an?|one)\s+hour\s+and\s+a\s+half\b", low)
    if m:
        return 90, m.start(), m.end()
    m = re.search(r"\bin\s+(\d+(?:\.\d+)?|an?|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty[ -]five|forty|sixty)\s*"
                  r"(min(?:ute)?s?|hours?|hrs?|h)\b", low)
    if not m:
        return None
    tok = m[1]
    n = float(tok) if tok[0].isdigit() else WORDNUM[tok]
    if m[2].startswith("h"):
        n *= 60
    return int(round(n)), m.start(), m.end()


def span_text(n):
    if n < 60:
        return f"{n} min"
    if n == 60:
        return "an hour"
    return f"{n / 60:g} hours"


def guess_hour(h, cur, tomorrow=False):
    """24h hour for a bare 1-12 'o'clock': the next one that is still ahead today, or a sensible guess for another day."""
    if h == 0 or h > 12:
        return h % 24
    if tomorrow:
        return h if 7 <= h <= 11 else (h if h == 12 else h + 12)
    cands = [h % 12, h % 12 + 12]
    return next((c for c in cands if c * 60 > cur), cands[0])


RE_BARE = re.compile(r"\b(?:at|by|around|for)\s+(\d{1,2})\b(?![:.]\d)(?!\s*(?:am|pm|a\.m|p\.m))", re.I)


def find_time_bare(s, cur):
    """'at 3' (no am/pm): pick the next sensible 3 o'clock. Returns (HH:MM, text without it) or (None, s)."""
    m = RE_BARE.search(s)
    if not m or int(m[1]) > 23:
        return None, s
    h = guess_hour(int(m[1]), cur, "tomorrow" in s.lower())
    return f"{h:02d}:00", s[:m.start()] + " " + s[m.end():]


def find_time_wake(text):
    """A wake-up time from 'wake me at 6:30', '7', '6am', 'seven thirty'; None if unreadable."""
    hm = find_time(text)[0]
    if hm:
        return hm
    m = re.search(r"\b(?:at|for|to|by)?\s*(\d{1,2})\b(?!\s*[:\d])", text)
    if m and 1 <= int(m[1]) <= 23:
        return f"{int(m[1]):02d}:00"
    return None


# ----------------------------------------------------- stats and summaries


def stats_text(st):
    today = datetime.now(TZ).date()
    days = [c for _, c in hist_items(st)][-30:]
    days = [c for c in days if c != "."]
    ok30 = sum(1 for c in days if c in "PSF")
    moods = [int(c) for c in st["moods"][-7:] if c.isdigit()]
    sa = screen_avg(st, today)
    lines = [f"📊 Here's where you are, {NAME}", f"Day {st['day']} · streak {st['streak']} 🔥 · best {st['best']}"]
    if days:
        lines.append(f"Last 30 days: {round(100 * ok30 / len(days))}% on track")
    if moods:
        lines.append(f"Mood this week: {sum(moods) / len(moods):.1f}/5")
    emo = emo_summary(st, today)
    if emo != "none logged":
        lines.append(f"Feelings: {emo}")
    if sa is not None:
        lines.append(f"Screen time: {fmt_min(sa)} a day on average")
    lines.append(heatmap(st) if days else "No finished days yet. Day 1 is coming 💪")
    return "\n".join(lines)


def weekly_text(st, today):
    week = [c for d, c in hist_items(st) if today - timedelta(days=6) <= d <= today]
    p, s, f, x = (week.count(k) for k in "PSFX")
    moods = [int(c) for c in st["moods"][-7:] if c.isdigit()]
    sa = screen_avg(st, today)
    lines = [f"🗓️ Your week, {NAME}", f"{p} perfect · {s} slips · {f} freezes · {x} resets",
             f"Streak {st['streak']} (best {st['best']})"]
    if moods:
        lines.append(f"Mood: {sum(moods) / len(moods):.1f}/5 on average")
    emo = emo_summary(st, today)
    if emo != "none logged":
        lines.append(f"Feelings: {emo}")
    if sa is not None:
        lines.append(f"Screen time: {fmt_min(sa)} a day")
    if hist_items(st):
        lines.append(heatmap(st, 6))
    lines.append("What worked this week? Tell me in a line and I'll keep it.")
    return "\n".join(lines)


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


def when_text(hm, rule, today):
    """'today at 3 pm', 'every day at 7 am', 'on Fri 06 Nov at 9 am'."""
    t = clock(hm)
    if rule == "daily":
        return f"every day at {t}"
    if rule == "weekdays":
        return f"on weekdays at {t}"
    if rule == "weekends":
        return f"on weekends at {t}"
    if rule.startswith("days:"):
        return "every " + ", ".join(x.capitalize() for x in rule[5:].split(",")) + f" at {t}"
    d = rule[5:]
    if d == str(today):
        return f"today at {t}"
    if d == str(today + timedelta(days=1)):
        return f"tomorrow at {t}"
    try:
        return f"on {pdate(d).strftime('%a %d %b')} at {t}"
    except ValueError:
        return f"on {d} at {t}"


def reminder_words(words):
    words = list(words)
    while words and words[0].lower() in ("me", "to", "at", "on", "every", "of", "that", "about", "for"):
        words.pop(0)
    while words and words[-1].lower() in ("on", "at", "by", "for", "to", "in", "around", "and", "then"):
        words.pop()
    return " ".join(words).replace("|", "/").strip()


def parse_reminder(arg, today, cur):
    """Return (dict(time, rule, text), None) or (None, error message)."""
    tomorrow_word = "tomorrow" in arg.lower()
    hm, rest = find_time(arg)
    if hm:
        m = RE_TIME.search(arg)
        has_ampm = bool(m and (m[3] or m[5]))
        h = int(hm[:2])
        if not has_ampm and 1 <= h <= 12 and ":" in m.group(0):
            hm = f"{guess_hour(h, cur, tomorrow_word):02d}:{hm[3:]}"
    else:
        hm, rest = find_time_bare(arg, cur)
    if not hm:
        return None, "What time? Try \"remind me to call mum at 3pm\"."
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
    text = reminder_words(t for t, u in zip(toks, used) if not u)
    if not text:
        return None, "What should I remind you about?"
    if rule == f"once:{today}" and mins(hm) + 120 < cur:
        return None, "That time has already passed today. Say \"tomorrow\" and I'll set it for then."
    return dict(time=hm, rule=rule, text=text[:60]), None


def reminders_text(st, today):
    if not st["reminders"]:
        return "Nothing set right now. Tell me what to remind you about, like \"remind me to call mum at 3pm\"."
    lines = ["Here's what I'm keeping for you ⏰"]
    for r in sorted(st["reminders"], key=lambda r: (r["time"], r["id"])):
        lines.append(f"{r['id']}. {r['text']} · {when_text(r['time'], r['rule'], today)}")
    return "\n".join(lines)


def add_reminder(st, time_hm, rule, text):
    nid = max([r["id"] for r in st["reminders"]], default=0) + 1
    st["reminders"].append(dict(id=nid, time=time_hm, rule=rule, text=text[:60], last="-"))
    return nid


def handle_remind(st, arg, ts):
    if len(st["reminders"]) >= MAX_REMINDERS or len(render(st)) > 3500:
        return "My reminder list is full. Let's clear a few first."
    cur = ts.hour * 60 + ts.minute
    dur = parse_duration(arg)
    if dur:
        amount, a, b = dur
        if not 1 <= amount <= 1440:
            return "I can set anything from 1 minute to 24 hours. Try \"remind me in 10 minutes to stretch\"."
        body = reminder_words((arg[:a] + " " + arg[b:]).split())
        if not body:
            return "What should I remind you about?"
        t_, rule, _ = after(ts, amount, "")
        add_reminder(st, t_, rule, body)
        return f"Got it, I'll remind you in {span_text(amount)} ({clock(t_)}): {body[:60]} ⏰"
    rem, err = parse_reminder(arg, ts.date(), cur)
    if err:
        return err
    add_reminder(st, rem["time"], rem["rule"], rem["text"])
    note = ""
    if in_quiet(st, mins(rem["time"])):
        note = f"\nThat's inside your quiet hours, so it'll arrive at {clock(st['quiet'].split('-')[1])}."
    return f"Got it, I'll remind you {when_text(rem['time'], rem['rule'], ts.date())}: {rem['text']} ⏰{note}"


def after(now, minutes_ahead, text):
    """(time, rule, text) for a one-off timer `minutes_ahead` from now, rounded up to the next minute."""
    t = now + timedelta(minutes=minutes_ahead)
    if t.second or t.microsecond:
        t = t.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return f"{t:%H:%M}", f"soon:{t.date()}", text


def is_once(rule):
    return rule.startswith(("once:", "soon:"))


# ----------------------------------------------------------- message reading

SLASH = {"/start": "start", "/status": "status", "/help": "help", "/stats": "stats", "/menu": "help", "/wd": "wd",
         "/cancel": "cancel", "/feel": "feel", "/feelings": "feelings", "/export": "export", "/awake": "awake",
         "/atwork": "atwork", "/leaving": "leave", "/leave": "leave", "/outdoor": "outdoor", "/photo": "hint_photo",
         "/video": "hint_video", "/reminders": "rlist"}
# These always run, even while the bot is waiting for an answer in the wind-down.
EXPLICIT = {"setwake", "playlist", "start", "status", "help", "stats", "cancel", "feelings", "export", "rlist", "rremove",
            "rremove_txt", "rclear", "redit", "remind", "setrule", "quiet", "buddy", "screen", "diary_show"}

RE_BOOK = [
    re.compile(r"book\s*:\s*[\"“'‘]?(.+?)[\"”'’]?\s*,\s*page\s*(\d+)", re.I),
    re.compile(r"reading\s+[\"“'‘]?(.+?)[\"”'’]?\s*,?\s*(?:on\s+)?page\s*(\d+)", re.I),
]
RE_BOOK_NEW = re.compile(r"^(?:i'?m|i am)?\s*(?:now\s+)?(?:reading|started reading|starting|just started)\s+[\"“'‘]?(.{3,40}?)[\"”'’]?[.!]?$", re.I)
RE_BAD_TITLE = re.compile(r"^(?:chapter|page|pg|now|again|more|it|this|that|something|a |an |some|my |for |to |on |at )", re.I)
RE_PAGE_AT = re.compile(r"\b(?:on|reached|reach|at|to|now|up to|reading|finished|read|hit)\b[^.!?]{0,20}?\b(?:page|pg\.?)\s*(\d{1,4})\b|^(?:page|pg\.?)\s*(\d{1,4})\b", re.I)
RE_WATER = re.compile(r"^/?(?:water|drank|drink)\s*([+-]?\d+(?:\.\d+)?)\s*(ml|l|litres?|liters?|glass(?:es)?)?\s*$", re.I)
RE_READ = re.compile(r"^/?(?:read|pages)\s+(\d+)\s*(?:pages?)?\s*$", re.I)
RE_SET = re.compile(
    r"^/?set\s+(wake|workout|work|wakecall|midday|leavecheck|leave|review|water|pages|glass|photo|outdoor|screen|music|winddown)\s*(?:to|at)?\s*(.+)$", re.I)
RE_GOAL = re.compile(r"\b(?:change|make|set|update|raise|lower|increase|decrease)\s+(?:my\s+|the\s+)?(water|reading|pages|screen\s*time|screen)\s+(?:goal|target|limit)\s*(?:to|at)?\s*(.+)$", re.I)
RE_GLASS = re.compile(r"\b(?:my\s+)?(?:glass|cup)\s+(?:is|holds|size is|=)\s*(?:about\s+)?(\d+)\s*ml\b", re.I)
RE_FOCUS = re.compile(r"^/?focus\b(?:\s+(\d{1,3}))?\s*(.*)$", re.I)
RE_FOCUS_NAT = re.compile(r"^(?:let'?s\s+|start\s+(?:a\s+)?|begin\s+|starting\s+|doing\s+)?(?:a\s+)?(?:focus|pomodoro)\b", re.I)
RE_MOOD = re.compile(r"^/?mood\b(?:\s+([1-5]))?\s*$", re.I)
RE_DIARY = re.compile(r"^/?(?:diary|journal|dear diary|note to self)\b\s*[:,]?\s*(.*)$", re.I | re.S)
RE_WAKE = re.compile(r"^/?wake\s+(?:me\s+)?(?:up\s+)?(?:at\s+|to\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*$", re.I)
RE_SETWAKE = re.compile(r"\b(?:wake\s+me|wake[- ]?up\s+(?:call|time)|wake\s+time|alarm|get\s+me\s+up|wake\s+up\s+at|wake\s+at|get\s+up\s+at|waking\s+up\s+at)\b", re.I)
RE_PLAYLIST = re.compile(r"^/?playlists?\b\s*(.*)$", re.I)
RE_SCREEN = re.compile(r"^/?screen\b\s*(?:time|today|usage)?\s*(.*)$", re.I)
RE_SCREEN_NAT = re.compile(r"\bscreen\s*time\b|\bphone\s+(?:usage|time)\b|\btime on (?:my )?phone\b|\b(?:used|on|spent)\b.{0,12}\bphone\b", re.I)
RE_QUIET = re.compile(r"^/?quiet\b\s*(?:hours)?\s*(.+)$", re.I)
RE_BUDDY = re.compile(r"^/?buddy\s+(\S+)\s*$", re.I)
RE_RLIST = re.compile(r"^/?(?:reminders|list reminders|my reminders|show reminders|show my reminders|what reminders do i have|any reminders)\s*\??\s*$", re.I)
RE_RREMOVE = re.compile(r"^/?(?:remove|delete|cancel|del)\s*(?:reminder\s*)?#?(\d+)\s*$", re.I)
RE_RREMOVE_TXT = re.compile(r"^(?:cancel|remove|delete|forget|drop)\s+(?:the\s+|my\s+)?(?:reminder|alarm)(?:\s+(?:about|for|to))?\s+(.+)$", re.I)
RE_RCLEAR = re.compile(r"^/?(?:clear|remove all|delete all)\s*(?:all\s*)?reminders\s*$", re.I)
RE_REDIT = re.compile(r"^/?(?:change|edit|move|reschedule)\s*(?:reminder\s*)?#?(\d+)\s*(?:to|at)?\s*(.+)$", re.I)
RE_REMIND = re.compile(r"^/?remind\b[\s:,]*(?:me\b[\s,]*)?(?:to\b[\s,]*)?(.*)$", re.I | re.S)
RE_POLITE = re.compile(r"^(?:hey|hi|hello|ok|okay|so|um|uh|please|pls|plz|kindly|can you|could you|would you|will you|go ahead and|i want you to|i need you to|i'?d like you to|i would like you to)[\s,!]+", re.I)
RE_SETREM = re.compile(r"^(?:set\s+(?:me\s+)?(?:up\s+)?(?:a\s+|an\s+)?reminder\s*(?:for|to|about|that)?\s*|reminder\s*[:\-,]\s*|add\s+(?:a\s+)?reminder\s*(?:for|to|about)?\s*|don'?t\s+let\s+me\s+forget\s+(?:to\s+)?|make\s+sure\s+i\s+(?:remember\s+to\s+)?)", re.I)
RE_LEAVE = re.compile(
    r"leaving early|left early|leaving work|left work|off work|knock(?:ed|ing) off|done (?:with )?work|finished work|got off work|leaving the office|left the office|heading home|on my way home|clocking out|clocked out", re.I)
RE_WORK = re.compile(
    r"\bat work\b|\bat the office\b|\b(?:reached|arrived|got to|made it to|got into)\s+(?:the\s+|my\s+)?(?:work|office|desk)\b|\bin the office\b|\bat my desk\b", re.I)
RE_AWAKE = re.compile(
    r"\bawake\b|\bwoke\b|\bwaking\b|\bwake(?:n)? up\b|\bi'?m up\b(?!\s+(?:to|for|against|with|in|on)\b)|\bim up\b(?!\s+(?:to|for|against|with|in|on)\b)|\bup now\b|\bam up\b(?!\s+(?:to|for|against|with|in|on)\b)|\bup and (?:ready|about|awake)\b|\bgood morning\b|^(?:morning|gm)\b|^up\b", re.I)
RE_OUTDOOR = re.compile(r"\b(outdoor|outdoors|outside)\b", re.I)
RE_OUT_SURE = re.compile(r"\b(?:outdoor|outside)\s+(?:workout|exercise|run|session|training)|\b(?:went|go)\s+(?:for\s+)?(?:a\s+)?(?:run|jog|hike)\b|\b(?:ran|jogged|hiked)\b|\bdid my outdoor\b", re.I)
RE_OUT_MAYBE = re.compile(r"\b(?:went|go)\s+(?:for\s+)?(?:a\s+)?walk\b|\bwalked\b|\bcycled\b|\bbike ride\b|\bwent cycling\b", re.I)
RE_WD = re.compile(r"\bwind(?:ing|s)?[\s-]*down\b|\bgood\s*night\b|\bnight\s*night\b|\bnighty\s*night\b|\bcalling\s+it\s+a\s+night\b|"
                   r"\b(?:going|heading|off|ready|time)\s+(?:to|for)\s+(?:bed|sleep)\b|\bhitting\s+the\s+sack\b|\bturning\s+in\b|^gn\b", re.I)
RE_STATUS = re.compile(r"\bhow\s+am\s+i\s+doing\b|\bhow'?s\s+my\s+day\b|\bhow\s+is\s+my\s+day\b|\bwhat'?s\s+(?:still\s+)?(?:left|open|pending|remaining)\b|"
                       r"\bwhat\s+(?:do\s+i\s+have|have\s+i\s+got)\s+left\b|\bwhat\s+(?:is|are)\s+left\b|\bmy\s+(?:progress|streak)\b|"
                       r"\bwhere\s+(?:am\s+i|do\s+i\s+stand)\b|^(?:status|progress|update)\s*\??$", re.I)
RE_STATS = re.compile(r"\bmy\s+stats\b|^stats$|\bheat\s*map\b|\bhow\s+was\s+my\s+week\b|\bhow\s+have\s+i\s+been\s+doing\b", re.I)
RE_HELPQ = re.compile(r"\bwhat\s+can\s+you\s+do\b|\bhow\s+does\s+this\s+work\b|\bwhat\s+can\s+i\s+(?:say|send|do)\b|^help\b|\bhow\s+do\s+i\s+use\b", re.I)
RE_DIARY_SHOW = re.compile(r"\b(?:show|read|see|open|view|check)\s+(?:me\s+)?(?:my\s+|the\s+|today'?s\s+)?(?:diary|journal)\b|\bwhat\s+did\s+i\s+write\b", re.I)
RE_EXPORT = re.compile(r"\b(?:send|export|give)\s+(?:me\s+)?(?:my\s+|the\s+)?(?:whole\s+|full\s+)?(?:diary|journal)\b", re.I)
RE_FEELINGS_LOG = re.compile(r"\bfeelings?\s+(?:log|history)\b|\bhow\s+have\s+i\s+been\s+feeling\b", re.I)
RE_THANKS = re.compile(r"^(?:thanks|thank you|thx|ty|cheers|appreciate it|much appreciated)\b", re.I)
RE_GREET = re.compile(r"^(?:hi|hello|hey|yo|hola|howdy|sup)\b[\s!.,]*(?:there|joseph|bot|compass|buddy)?[\s!.]*$", re.I)
RE_OKAY = re.compile(r"^(?:ok(?:ay)?|k|kk|cool|nice|great|alright|sure|yes|yeah|yep|yup|noted|got it|lol|haha|😂|👍|🙏|❤️|🔥|💪)[\s!.]*$", re.I)
RE_QUESTION = re.compile(r"^(?:what|why|how|when|where|who|which|can|could|should|would|is|are|do|does|did|will|am|may)\b", re.I)
RE_HYPO = re.compile(
    r"\b(?:will|won'?t|shall|gonna|going\s+to|should|shouldn'?t|need\s+to|needs\s+to|have\s+to|has\s+to|must|want\s+to|wanna|might|maybe|"
    r"later|tomorrow|soon|if\s+i|when\s+i|whether|can\s+i|could\s+i|do\s+i|did\s+i|how\s+much|how\s+many|how\s+do|supposed\s+to|"
    r"plan\s+to|planning\s+to|about\s+to|trying\s+to|try\s+to|i'?ll|would\s+like|let'?s)\b|\?\s*$", re.I)
RE_WATER_VERB = re.compile(r"\b(?:drank|drunk|drink|drinking|had|finished|finish|downed|sipped|got\s+through|polished\s+off|done\s+with|chugged|gulped|another|more)\b", re.I)
RE_OTHER_DRINK = re.compile(r"\b(?:juice|tea|coffee|milk|soda|coke|beer|wine|smoothie|chai|energy\s+drink|soup|porridge|latte)\b", re.I)

EMO_MAP = {}
for _e, _ws in {"good": "happy great amazing awesome grateful excited motivated hopeful proud joyful cheerful energized energised wonderful blessed fantastic positive thankful",
                "calm": "calm peaceful relaxed content relieved settled chill serene",
                "stressed": "stressed anxious overwhelmed worried nervous tense pressured panicked scared afraid uneasy",
                "low": "sad low down lonely discouraged depressed unmotivated empty hopeless heavy gloomy",
                "frustrated": "angry frustrated annoyed irritated upset mad furious bitter resentful",
                "tired": "tired exhausted drained sleepy burnt burned fatigued wiped spent"}.items():
    for _w in _ws.split():
        EMO_MAP[_w] = _e
WEAK_EMO = {"good": "good", "fine": "calm", "okay": "calm", "ok": "calm", "alright": "calm", "well": "good", "better": "good", "meh": "low"}
RE_FEEL = re.compile(r"\b(?:i\s*am\s+feeling|i'?m\s+feeling|i\s+am|i'?m|im|i\s+feel|i\s+have\s+been|i'?ve\s+been|been\s+feeling|feeling|feel)\s+"
                     r"((?:(?:so|really|very|quite|a\s+bit|a\s+little|kinda|kind\s+of|pretty|super|extremely|rather|totally|just|feeling)\s+)*)([a-z]+)", re.I)
RE_WHY = re.compile(r"\W*(?:because|cause|cos|since|about|over|due\s+to|from|after|with)\s+(.+)$", re.I | re.S)


def read_feeling(text):
    """(emotion, why or None) when the message says how Joseph feels ('I'm so stressed about work'), else None."""
    low = text.lower()
    for m in RE_FEEL.finditer(low):
        word = m[2]
        explicit = "feel" in m[0]
        emo = EMO_MAP.get(word) or (WEAK_EMO.get(word) if explicit else None)
        if emo:
            w = RE_WHY.match(text[m.end():])
            return emo, (w[1].strip() if w else None)
    return None


def emotion_from(text):
    """Pick an emotion from whatever he typed in the wind-down: 'stressed', 'a bit anxious about work', 'mixed'."""
    fe = read_feeling(text)
    if fe:
        return fe
    words = re.findall(r"[a-z]+", text.lower())
    for w in words:
        if w in EMO_MAP:
            return EMO_MAP[w], (text.strip() if len(words) > 2 else None)
    if len(words) <= 2:
        w = words[0] if words else "other"
        return WEAK_EMO.get(w, "_".join(words) or "other"), None
    return "other", text.strip()


def clauses(low):
    return [c.strip() for c in re.split(r"[,;!]|\.(?!\d)| and | but | then | also ", low) if c.strip()]


def parse_water(text, glass):
    """(millilitres, sure) for a plain-language water message ('had a glass of water', 'two bottles', '1.5 litres'), or None."""
    low = text.lower().strip()
    if RE_HYPO.search(low):
        return None
    has_water = bool(re.search(r"\bwater\b|hydrat", low))
    verb = bool(RE_WATER_VERB.search(low))
    if low in ("water", "a glass of water"):
        return glass, True
    if RE_OTHER_DRINK.search(low) and not has_water:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(ml|millilit\w*|l|lit(?:re|er)s?|ltrs?)\b", low)
    if m and (has_water or verb or len(low.split()) <= 2):
        v = float(m[1])
        return int(v if m[2].startswith("m") else v * 1000), True
    m = re.search(r"\b(half\s+a|half|a|one|an)\s+lit(?:re|er)\b", low)
    if m and (has_water or verb):
        return (500 if m[1].startswith("half") else 1000), True
    m = re.search(r"\b(glass(?:es)?|cups?|mugs?|bottles?|jugs?|pitchers?)\b", low)
    if m and (has_water or verb):
        g = m[1]
        unit = glass if g.startswith("glass") else 250 if g.startswith("cup") else 300 if g.startswith("mug") else 500 if g.startswith("bottle") else 1000
        before = low[:m.start()]
        qty = 1.0
        if re.search(r"half\s+(?:of\s+)?(?:an?\s+|the\s+|my\s+)?(?:\w+\s+)?$", before):
            qty = 0.5
        else:
            for tok in reversed(before.split()[-3:]):
                if tok in ("couple",):
                    qty = 2.0
                    break
                if tok == "few":
                    qty = 3.0
                    break
                v = float(tok) if re.fullmatch(r"\d+(?:\.\d+)?", tok) else WORDNUM.get(tok)
                if v:
                    qty = float(v)
                    break
        if re.search(r"\b(?:or two|or 2)\b", low[m.end():]):
            qty = 2.0
        return int(unit * qty), True
    if has_water and verb:
        return glass, False
    if has_water and re.search(r"\b(?:some|sip|sipped|little|bit)\b", low):
        return 250, False
    return None


def parse_pages(low):
    """('n', pages, sure), ('ask',) or None for a plain-language reading message."""
    if RE_HYPO.search(low):
        return None
    m = re.search(r"\b(?:read|reading|finished|did|done|completed|got through|managed|knocked out)\b[^.!?]*?\b(\d{1,4})\s+pages?\b", low)
    if m:
        return ("n", int(m[1]), True)
    if re.search(r"\b(?:read|reading)\b[^.!?]*\b(?:a\s+couple(?:\s+of)?|couple\s+of|a\s+few|some)\s+pages?\b", low):
        return ("n", 2 if "couple" in low else 5, False)
    m = re.search(r"\b(?:read|reading|studied)\b[^.!?]*?\b(\d{1,3})\s*(min(?:ute)?s?|hours?|hrs?)\b", low)
    if m:
        minutes = int(m[1]) * (60 if m[2].startswith("h") else 1)
        return ("n", max(1, minutes // 2), False)
    if re.search(r"\b(?:finished|read|completed|done with)\b[^.!?]*\b(?:chapters?|the book|my book)\b", low):
        return ("ask",)
    return None


def norm(t):
    t = t.strip()
    for _ in range(4):
        n = RE_POLITE.sub("", t, count=1)
        if n == t:
            break
        t = n
    return RE_SETREM.sub("remind me to ", t, count=1).strip()


def classify(m, glass=500):
    """Return (kind, data) for one Telegram message."""
    doc = m.get("document") or {}
    mime = str(doc.get("mime_type", ""))
    caption = (m.get("caption") or "")
    if "video" in m or "video_note" in m or mime.startswith("video"):
        return ("outdoor" if RE_OUTDOOR.search(caption) else "workout"), None
    if "photo" in m or mime.startswith("image"):
        return "photo", None
    if "voice" in m or "audio" in m:
        return "voice", None
    raw = (m.get("text") or caption or "").strip()
    if not raw:
        return "sticker", None
    text = re.sub(r"^(/\w+)@\w+", r"\1", raw).replace("\u2019", "'").replace("\u2018", "'")
    low = text.lower()
    if low in SLASH:
        return SLASH[low], None
    if low == "/water":
        return "water", (glass, True)
    if low == "/pages":
        return "pages", (5, True)
    if low in ("status", "help", "menu", "stats"):
        return {"menu": "help"}.get(low, low), None
    if RE_THANKS.match(low):
        return "chat", "thanks"
    if RE_GREET.match(low):
        return "chat", "greet"
    if RE_OKAY.match(low):
        return "chat", "ok"

    text = norm(text)
    low = text.lower()
    # typed forms (still understood, just never advertised)
    if RE_RLIST.match(text):
        return "rlist", None
    if RE_RCLEAR.match(text):
        return "rclear", None
    r = RE_RREMOVE.match(text)
    if r:
        return "rremove", int(r[1])
    r = RE_RREMOVE_TXT.match(text)
    if r:
        return "rremove_txt", r[1].strip()
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
        val, unit = float(r[1]), (r[2] or "").lower()
        if unit.startswith("glass") or (unit == "" and abs(val) <= 20):
            ml = val * glass
        elif unit.startswith("l"):
            ml = val * 1000
        else:
            ml = val
        return "water", (int(ml), True)
    r = RE_READ.match(text)
    if r:
        return "pages", (int(r[1]), True)
    r = RE_FOCUS.match(text)
    if r and not RE_FOCUS_NAT.match(text) or (r and r[1]):
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
        return "screen", (parse_screen_loose(r[1]) if r[1] else None)
    r = RE_WAKE.match(text)
    if r:
        hm = find_time_wake(r[1])
        return ("setwake", hm) if hm else ("other", text)
    r = RE_PLAYLIST.match(text)
    if r:
        return "playlist", r[1].strip()
    r = RE_QUIET.match(text)
    if r:
        return "quiet", r[1].strip()
    r = RE_BUDDY.match(text)
    if r:
        return "buddy", r[1].strip()
    for rx in RE_BOOK:
        r = rx.search(text)
        if r:
            return "book", (r[1].strip(), r[2])

    # plain language
    asking = low.endswith("?")
    if RE_WD.search(low) and not asking:
        return "wd", None
    if RE_STATUS.search(low):
        return "status", None
    if RE_STATS.search(low):
        return "stats", None
    if RE_HELPQ.search(low):
        return "help", None
    if RE_RLIST.match(low.rstrip("?! ")):
        return "rlist", None
    if RE_EXPORT.search(low):
        return "export", None
    if RE_DIARY_SHOW.search(low):
        return "diary_show", None
    if RE_FEELINGS_LOG.search(low):
        return "feelings", None
    if RE_SETWAKE.search(low) and not asking:
        hm = find_time_wake(text)
        if hm:
            return "setwake", hm
    r = RE_GOAL.search(text)
    if r:
        key = {"reading": "pages", "screen time": "screen", "screentime": "screen"}.get(r[1].lower().replace("  ", " "), r[1].lower())
        return "setrule", (key, r[2].strip())
    r = RE_GLASS.search(text)
    if r:
        return "setrule", ("glass", r[1])
    if "timer" in low:
        t = re.search(r"(\d+(?:\.\d+)?)\s*(min\w*|hours?|hrs?)", low)
        if t:
            return "remind", f"in {t[1]} {t[2]} Time's up ⏱"
    if RE_FOCUS_NAT.match(low):
        t = re.search(r"(\d{1,3})\s*(min\w*|m\b|hours?|h\b)", low)
        minutes = (int(t[1]) * (60 if t[2].startswith("h") else 1)) if t else 25
        label = re.sub(r"(\d{1,3})\s*(min\w*|m\b|hours?|h\b)", " ", RE_FOCUS_NAT.sub("", low, count=1))
        label = re.sub(r"^\s*(?:session|timer|for|on|of)\b\s*", "", label.strip()).strip()
        return "focus", (minutes, label)
    if RE_SCREEN_NAT.search(low) and not asking:
        v = parse_screen_loose(text)
        if v is not None:
            return "screen", v

    cl = clauses(low)

    def said(rx):
        return any(rx.search(c) and not RE_HYPO.search(c) for c in cl)

    if said(re.compile(r"\bbook\b")) or low.startswith(("i'm reading", "im reading", "i am reading", "reading ", "started reading", "just started")):
        r = RE_BOOK_NEW.match(text.strip())
        if r and r[1][:1].isupper() and not RE_BAD_TITLE.match(r[1]):
            return "book_new", r[1].strip()
    acts = []  # one message can hold several things: "I'm at work, drank a glass of water"
    if said(RE_AWAKE):
        acts.append(("awake", None))
    if said(RE_WORK):
        acts.append(("atwork", None))
    if said(RE_LEAVE):
        acts.append(("leave", None))
    if said(RE_OUT_SURE):
        acts.append(("outdoor", None))
    elif said(RE_OUT_MAYBE):
        acts.append(("outdoor_ask", None))
    got_pages = False
    for c in cl:
        rp = parse_pages(c)
        if rp:
            acts.append(("pages", (rp[1], rp[2])) if rp[0] == "n" else ("pages_ask", None))
            got_pages = True
            break
    if not got_pages:
        for c in cl:
            r = RE_PAGE_AT.search(c)
            if r and not RE_HYPO.search(c):
                acts.append(("page", int(r[1] or r[2])))
                break
    for c in cl:
        w = parse_water(c, glass)
        if w:
            acts.append(("water", w))
            break
    fe = read_feeling(text)
    if fe and acts:
        acts.append(("feel", fe))
    if len(acts) == 1:
        return acts[0]
    if acts:
        return "multi", acts
    fe = read_feeling(text)
    if fe:
        return "feel", fe
    if asking or RE_QUESTION.match(low):
        return "question", None
    return "other", text


# ----------------------------------------------------------------- templates


def ack(t, deadline, test, seed, what="up"):
    diff = mins(t) - mins(deadline)
    c, dl = clock(t), clock(deadline)
    if diff <= 0:
        options = {
            "up": [f"Up at {c} ✅", f"Morning! Logged you up at {c} ✅", f"{c}, right on time 💪"],
            "workout": [f"Workout video in, {c} ✅ Strong.", f"Got your workout at {c} 🎥 Nice work.", f"{c} workout, on time 💪"],
            "work": [f"At work, {c} ✅", f"Logged you at work at {c} ✅", f"{c}, right on time. Have a good one 💪"],
        }[what]
    else:
        options = [f"Logged at {c} ✅ That's {diff} min past {dl}. We'll beat it tomorrow.",
                   f"Got it, {c}. {diff} min after {dl}. Tomorrow we tighten it up."]
    return pick(seed, options) + (" (test day)" if test else "")


def early_leave_plan(st, t):
    end = tm(st, "review") or 21 * 60
    hrs = max(end - mins(t), 0) / 60
    book, page = st["book"], st["page"]
    block = 60 if hrs >= 4 else 40 if hrs >= 2 else 25
    read = f"• Read for {block} minutes" + ("" if book == "not set" else f" of {book}" + (f" from page {page}" if page.isdigit() else ""))
    pending = []
    g = st["targets"]
    if g["water"] and st["water"] < g["water"]:
        pending.append(f"💧 {amt(st['water'])} of {amt(g['water'])}")
    if g["pages"] and st["pages"] < g["pages"]:
        pending.append(f"📖 {st['pages']} of {g['pages']} pages")
    if g["outdoor"] and st["outdoor_t"] == "-":
        pending.append("🌳 outdoor workout")
    if g["photo"] and st["photo_t"] == "-":
        pending.append("📸 photo")
    lines = [f"You're out early, {NAME} 🙌 That's about {hrs:.0f} hours before tonight. Some ideas:", read,
             "• A 20-minute walk or a stretch", "• Write down tomorrow's top three"]
    if pending:
        lines.append("Still open today: " + ", ".join(pending))
    return "\n".join(lines)


def wake_call(st):
    head = pick(f"{st['today']}wc", [f"Good morning, {NAME} ☀️ Day {st['day']}.", f"Good morning, {NAME} 💪 Day {st['day']} is yours."])
    text = head + f"\nUp by {clock(st['wake'])}, workout by {clock(st['workout'])}, at work by {clock(st['work'])}."
    g = st["targets"]
    extra = []
    if g["water"]:
        extra.append(f"💧 {amt(g['water'])} of water")
    if g["pages"]:
        extra.append(f"📖 {g['pages']} pages")
    if g["outdoor"]:
        extra.append("🌳 an outdoor workout")
    if g["photo"]:
        extra.append("📸 a progress photo")
    if extra:
        text += "\nToday also: " + ", ".join(extra) + "."
    voice = (f"Good morning, {NAME}. Day {st['day']}. Text me when you're up by {spoken(st['wake'])}, "
             f"then get your workout done. You've got this.")
    return text, voice


def welcome_text(st, today):
    when = "Day 1 starts on " + START.strftime("%d %B") + "." if today < START else "You're on the clock. Let's go."
    return (f"Hi {NAME} 👋 I'm your Compass. {when}\n"
            "Just talk to me like a friend: \"I'm up\", \"had a glass of water\", \"read 10 pages\", "
            "\"remind me to call mum at 3\". A video counts as your workout and a photo as your progress shot.")


def help_text():
    return ("Just talk to me 😊 Some examples:\n"
            "• \"I'm up\", \"at work\", \"leaving work\"\n"
            "• \"had a glass of water\", \"read 15 pages\", \"went for a run\"\n"
            "• \"remind me to call mum at 3\" or \"in 20 minutes\"\n"
            "• \"I'm feeling stressed\" or \"winding down\"\n"
            "• \"how am I doing?\"\n"
            "A video is your workout and a photo is your progress shot.")


def status_text(st):
    g = st["targets"]
    done, todo = [], []
    if st["wake_t"] != "-":
        done.append(f"up at {clock(st['wake_t'])}")
    else:
        todo.append("tell me you're up")
    if st["workout_t"] != "-":
        done.append(f"workout at {clock(st['workout_t'])}")
    else:
        todo.append(f"workout video (by {clock(st['workout'])})")
    if st["work_t"] != "-":
        done.append(f"at work at {clock(st['work_t'])}")
    else:
        todo.append(f"get to work (by {clock(st['work'])})")
    if g["water"]:
        (done if st["water"] >= g["water"] else todo).append(
            "water" if st["water"] >= g["water"] else f"💧 {amt(g['water'] - st['water'])} more water")
    if g["pages"]:
        (done if st["pages"] >= g["pages"] else todo).append(
            "reading" if st["pages"] >= g["pages"] else f"📖 {g['pages'] - st['pages']} more pages")
    if g["outdoor"]:
        (done if st["outdoor_t"] != "-" else todo).append("outdoor workout")
    if g["photo"]:
        (done if st["photo_t"] != "-" else todo).append("progress photo")
    lines = [f"Day {st['day']} · streak {st['streak']} 🔥"]
    lines.append("Done: " + ", ".join(done) if done else "Nothing logged yet.")
    lines.append("Still to do: " + ", ".join(todo) if todo else "That's everything for today 🎉")
    if st["slips"]:
        lines.append(f"Slips this week: {st['slips']} ({st['free']} free)")
    return "\n".join(lines)


def apply_setrule(st, key, val):
    key = {"leave": "leavecheck"}.get(key, key)
    low = val.lower().strip()
    off = low in ("off", "none", "stop", "disable", "no")
    on = low in ("on", "yes", "enable")
    if key == "screen":
        if off:
            st["targets"]["screen"] = 0
            return "Done, no screen-time limit now."
        v = parse_screen_loose(val)
        if not v:
            return "Tell me the limit like 3h, or say off."
        st["targets"]["screen"] = v
        return f"Done, your daily screen-time limit is {fmt_min(v)}. Going over it, or not logging it, counts as a miss."
    if key in ("water", "pages", "glass"):
        n = re.search(r"\d+(?:\.\d+)?", low)
        if off and key != "glass":
            st["targets"][key] = 0
            return f"Okay, the {key} goal is off."
        if not n:
            return "I didn't catch the number. Try again?"
        v = float(n[0])
        if key == "water" and v <= 10:
            v *= 1000  # litres
        if v < 1 or v > 20000:
            return "That number looks off. Try again?"
        st["targets"][key] = int(v)
        if key == "pages":
            return f"Done, your reading goal is {int(v)} pages."
        return f"Done, your water goal is {amt(v)}." if key == "water" else f"Done, a glass is {amt(v)} now."
    if key in ("photo", "outdoor", "music"):
        if not (on or off):
            return "Say on or off?"
        st["targets"][key] = int(on)
        what = "music suggestions" if key == "music" else f"the {key} task"
        return f"Done, {what} {'on' if on else 'off'}."
    hm = None if off else find_time(val)[0]
    if key in ("wake", "workout", "work"):
        if not hm:
            return "I couldn't read that time. Try 5:45am."
        st[key] = hm
        return f"Done, your {key} deadline is {clock(hm)}."
    if not off and not hm:
        return "I couldn't read that time. Try 5:45am, or say off."
    st["times"][key] = "off" if off else hm
    return f"Done, {key} is now {'off' if off else clock(hm)}."


# ------------------------------------------------------------------ buttons

REMOVE_KB = {"remove_keyboard": True}


def emotion_buttons():
    row = lambda items: [{"text": f"{f} {n}", "callback_data": f"emo:{n}"} for n, f in items]
    return {"inline_keyboard": [row(EMOTIONS[:3]), row(EMOTIONS[3:])]}


def reminder_buttons():
    return {"inline_keyboard": [[{"text": "✅ Done", "callback_data": "done"},
                                 {"text": "💤 10 min", "callback_data": "snz:10"}]]}


def mood_buttons():
    faces = ["😞", "😕", "😐", "🙂", "😄"]
    return {"inline_keyboard": [[{"text": f"{faces[i]} {i + 1}", "callback_data": f"mood:{i + 1}"} for i in range(5)]]}


def wd_buttons():
    return {"inline_keyboard": [[{"text": "Yes 🌙", "callback_data": "wd:yes"}, {"text": "Not tonight", "callback_data": "wd:no"}]]}


def confirm_buttons(code):
    return {"inline_keyboard": [[{"text": "✅ Yes", "callback_data": "cf:" + code}, {"text": "No", "callback_data": "cf:x"}]]}


MUSIC_PHRASE = {"stressed": "Something calming for you 🎧", "low": "Something uplifting for you 🎧",
                "frustrated": "Something peaceful for you 🎧", "tired": "A little energy for you 🎧",
                "calm": "Something gentle to match 🎧", "good": "Something joyful to match 🎧", "focus": "Some focus music 🎧"}
WHY_Q = {"good": "Love that 😊 What's making you feel good?", "calm": "Nice, calm is a good place 😌 What helped?",
         "stressed": "I'm sorry, that's heavy 😔 What's behind the stress?", "low": "I'm here 🧡 What's behind feeling low?",
         "frustrated": "That's rough 😤 What happened?", "tired": "Rest is coming 😴 What wore you out?"}


# ----------------------------------------------------------------- day closing


def close_day(st, d, test_note=False):
    """Finish day d: update streak, slips, freezes and history. Returns the verdict text."""
    st["reviewed"] = str(d)
    if d < START:
        return "Today was a test, so nothing counted."
    g = st["targets"]
    misses = []
    for label, got, dl in (("wake check-in", st["wake_t"], st["wake"]), ("workout video", st["workout_t"], st["workout"]),
                           ("getting to work", st["work_t"], st["work"])):
        if got == "-":
            misses.append(f"{label} missing")
        elif mins(got) > mins(dl):
            misses.append(f"{label} late ({clock(got)})")
    if g["water"] and st["water"] < g["water"]:
        misses.append(f"water {amt(st['water'])} of {amt(g['water'])}")
    if g["pages"] and st["pages"] < g["pages"]:
        misses.append(f"reading {st['pages']} of {g['pages']} pages")
    if g["outdoor"] and st["outdoor_t"] == "-":
        misses.append("no outdoor workout")
    if g["photo"] and st["photo_t"] == "-":
        misses.append("no progress photo")
    if g["screen"]:
        if st["ev_date"] != str(d) or st["ev_screen"] == "-":
            misses.append("screen time not logged")
        elif int(st["ev_screen"]) > g["screen"]:
            misses.append(f"screen time {fmt_min(int(st['ev_screen']))} (limit {fmt_min(g['screen'])})")
    if not misses:
        st["streak"] += 1
        st["best"] = max(st["best"], st["streak"])
        verdict = f"Perfect day, {NAME} 🔥 Streak: {st['streak']}."
        if st["streak"] % 7 == 0 and st["freezes"] < 3:
            st["freezes"] += 1
            verdict += f"\n🧊 You earned a streak freeze (you have {st['freezes']})."
        if st["day"] >= 75:
            verdict += f"\n🏆 75 days done, {NAME}. That's huge."
        day_set(st, d, "P")
        return verdict
    st["slips"] += 1
    why = ", ".join(misses)
    if st["slips"] <= st["free"]:
        day_set(st, d, "S")
        return f"A slip today: {why}. That's your free slip this week, so the streak stays at {st['streak']}."
    if st["freezes"] > 0:
        st["freezes"] -= 1
        day_set(st, d, "F")
        return f"A slip today: {why}. A streak freeze saved you 🧊 ({st['freezes']} left)."
    day_set(st, d, "X")
    st["streak"], st["slips"], st["day"] = 0, 0, 0
    return f"A slip today: {why}. No free slips or freezes left, so the streak resets. Tomorrow is a fresh Day 1 💪"


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
    g = st["targets"]

    # day rollover
    roll_at = min(4 * 60 + 45, (tm(st, "wakecall") or 300) - 15)
    if st["today"] != str(today) and cur >= roll_at:
        old = st["today"]
        if started and old != "none" and st["reviewed"] != old:
            try:
                od = pdate(old)
                if od >= START:
                    out.append("I closed out yesterday for you. " + close_day(st, od))
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

    def pages_log(n):
        pages_add(n)
        tail = f" You're on page {st['page']}." if st["page"].isdigit() else ""
        if g["pages"]:
            if st["pages"] >= g["pages"]:
                return f"📖 {st['pages']} pages today. Reading goal done 🎉" + tail
            return f"📖 Nice, {st['pages']} of {g['pages']} pages today." + tail
        return f"📖 {st['pages']} pages today." + tail

    def set_page(newp):
        if st["page"].isdigit() and newp > int(st["page"]):
            return pages_log(newp - int(st["page"]))
        st["page"] = str(newp)
        return f"Page {newp} saved 📖"

    def water_add(ml):
        before = st["water"]
        st["water"] = max(0, before + int(ml))
        have = amt(st["water"])
        goal = g["water"]
        if ml < 0:
            return f"Took it off 👍 {have} so far."
        if not goal:
            return f"💧 {have} today."
        tot = amt(goal)
        if before >= goal:
            return f"💧 {have} today, goal's already done 🎉"
        if st["water"] >= goal:
            return f"💧 That's your {tot}. Water goal done 🎉"
        ratio = st["water"] / float(goal)
        if ratio >= 0.8:
            return f"💧 {have} of {tot}, nearly there."
        if ratio >= 0.5:
            return f"💧 {have} of {tot}, over halfway."
        return f"💧 Got it. {have} of {tot} so far."

    def set_outdoor(t):
        if st["outdoor_t"] == "-":
            st["outdoor_t"] = t
            return f"🌳 Outdoor workout logged at {clock(t)}. Great, {NAME}."
        return f"Outdoor workout was already logged at {clock(st['outdoor_t'])} 👍"

    def ev_touch():
        if st["ev_date"] != str(today):
            st.update(ev_date=str(today), ev_screen="-", ev_emo="-", ev_why="-", ev_diary="-", ev_shot="-")

    def log_diary(kind, text):
        out.append({"k": "diary_add", "ts": now.strftime("%Y-%m-%d %H:%M"), "kind": kind,
                    "text": re.sub(r"\s+", " ", text).strip()})

    def add_note(text):
        ev_touch()
        prev = st["ev_diary"]
        merged = text if prev == "-" else prev + " / " + text
        st["ev_diary"] = clean(merged[-250:], 250)

    def clear_await():
        st["aw_state"], st["aw_emo"], st["aw_at"] = "-", "-", "-"

    def set_screen(minutes):
        ev_touch()
        st["ev_screen"] = str(minutes)
        day_set(st, today, screen=minutes)
        log_diary("screen", f"{minutes} min ({fmt_min(minutes)})")

    def ask_emotion():
        st["aw_state"], st["aw_at"] = "wd_emotion", stamp(now)
        out.append(msg("How are you feeling tonight? Tap one, or tell me in your own words.", emotion_buttons()))

    def ask_why(emo, solo):
        st["aw_state"], st["aw_emo"], st["aw_at"] = ("feel_why" if solo else "wd_why"), emo, stamp(now)
        out.append(WHY_Q.get(emo) or f"What's behind feeling {nice(emo)}?")

    def set_why(emo, why):
        ev_touch()
        st["ev_why"] = clean(why, 120)
        log_diary("why", f"{nice(emo)}: {why}")

    def ask_diary():
        st["aw_state"], st["aw_at"] = "wd_diary", stamp(now)
        out.append("Anything else on your mind? I'll save it to your diary. Or say done.")

    def record_emotion(emo):
        ev_touch()
        emo = clean(emo.lower(), 14).replace(" ", "_")
        st["ev_emo"] = emo
        day_set(st, today, emo=EMO_CH.get(emo, "o"))
        log_diary("feeling", nice(emo))
        return emo

    def suggest_music(name, once_flag=True):
        if not st["targets"]["music"] or name not in MUSIC_QUERY:
            return
        flag = f"music_{name}"
        if once_flag and flag in st["sent"]:
            return
        st["sent"].append(flag)
        out.append(f"{MUSIC_PHRASE.get(name, 'Something for the moment 🎧')}\n{music_url(name)}")

    def ask_wake():
        st["aw_state"], st["aw_at"] = "wd_wake", stamp(now)
        cur_wc = st["next_wake"] if st["next_wake"] != "-" else st["times"]["wakecall"]
        out.append(f"What time should I wake you tomorrow? Say something like 6:30 or 7am, or \"same\" to keep {clock(cur_wc)}.")

    def finish_wd():
        clear_await()
        st["ev_done"] = stamp(now)
        parts = []
        if st["ev_screen"] != "-":
            parts.append(f"screen {fmt_min(int(st['ev_screen']))}")
        if st["ev_emo"] != "-":
            parts.append(f"{EMO_FACE.get(st['ev_emo'], '🙂')} {nice(st['ev_emo'])}")
        if st["ev_diary"] != "-":
            parts.append("diary ✓")
        parts.append("wake " + clock(st["next_wake"] if st["next_wake"] != "-" else st["times"]["wakecall"]))
        out.append("Saved 🌙 " + " · ".join(parts) + f"\nSleep well, {NAME}.")

    def start_wd():
        ev_touch()
        st["aw_state"], st["aw_emo"], st["aw_at"] = "wd_screen", "-", stamp(now)
        lim = g["screen"]
        out.append(f"🌙 Let's wind down, {NAME}. How much screen time did you have today? "
                   "Send a screenshot or just tell me, like 3h20 (or skip)."
                   + (f" Your limit is {fmt_min(lim)}." if lim else ""))

    def feeling_reply(emo, why):
        emo = record_emotion(emo)
        if why:
            set_why(emo, why)
            out.append(pick(f"{now}fr", ["Thanks for telling me 🧡 I've noted it.", "I hear you 🧡 Noted.", "Got it, that's saved 🧡"]))
        else:
            ask_why(emo, True)
        suggest_music(emo)

    def handle_await(text):
        s = st["aw_state"]
        low = text.strip().lower()
        if low in ("cancel", "never mind", "nevermind", "stop", "forget it", "leave it", "not now"):
            clear_await()
            out.append("No worries, we'll leave it 🙂")
            return
        skip = low in ("skip", "no", "none", "-", "pass", "nothing", "nope", "nah", "n/a")
        if s == "wd_screen":
            if skip:
                ask_emotion()
                return
            v = parse_screen_loose(text)
            if v is None:
                out.append("I didn't catch that. Try something like 3h20 or 200 minutes, or say skip.")
                return
            set_screen(v)
            lim = g["screen"]
            out.append(f"📱 {fmt_min(v)} noted" + ((f", within your {fmt_min(lim)} limit 👏" if v <= lim else f", a bit over your {fmt_min(lim)} limit") if lim else "."))
            ask_emotion()
        elif s == "wd_emotion":
            if skip:
                ask_diary()
                return
            emo, why = emotion_from(text)
            emo = record_emotion(emo)
            if why:
                set_why(emo, why)
                out.append("Thanks for telling me 🧡")
                ask_diary()
            else:
                ask_why(emo, False)
            suggest_music(emo)
        elif s in ("wd_why", "feel_why"):
            emo = st["aw_emo"]
            if not skip:
                set_why(emo, text)
            if s == "feel_why":
                clear_await()
                out.append("Thanks for telling me 🧡 It's saved.")
            else:
                ask_diary()
        elif s == "wd_diary":
            if low not in ("done", "skip", "no", "none", "that's all", "thats all", "nothing", "nothing else", "nope", "nah"):
                add_note(text)
                log_diary("diary", text)
            ask_wake()
        elif s == "wd_wake":
            if low in ("same", "skip", "keep", "no", "-", "done", "same time", "unchanged", "nothing", "nope"):
                finish_wd()
                return
            hm = find_time_wake(text)
            if hm is None:
                out.append("I didn't catch the time. Try 6:30 or 7am, or say same.")
                return
            st["next_wake"] = hm
            finish_wd()
        else:
            clear_await()

    if st["aw_state"] != "-":
        try:
            if now - datetime.strptime(st["aw_at"], "%Y-%m-%dT%H:%M").replace(tzinfo=TZ) > timedelta(hours=AWAIT_HOURS):
                clear_await()
        except ValueError:
            clear_await()

    def act(kind, data):
        if kind == "awake":
            if st["wake_t"] == "-":
                st["wake_t"] = t
                out.append(ack(t, st["wake"], test, seed, "up"))
            else:
                out.append(f"Already have you up at {clock(st['wake_t'])} ✅")
        elif kind == "workout":
            if st["workout_t"] == "-":
                st["workout_t"] = t
                out.append(ack(t, st["workout"], test, seed, "workout"))
            else:
                out.append(f"Nice, another one 🎥 Your workout is already in at {clock(st['workout_t'])}.")
        elif kind == "outdoor":
            out.append(set_outdoor(t))
        elif kind == "outdoor_ask":
            if g["outdoor"] and st["outdoor_t"] == "-":
                out.append(msg("Nice! Should that count as your outdoor workout?", confirm_buttons("o")))
            else:
                out.append("Lovely, that's good for you 💪")
        elif kind == "photo" and st["aw_state"].startswith("wd_"):
            ev_touch()
            ph = m.get("photo") or []
            st["ev_shot"] = clean((ph[-1].get("file_id") if ph else (m.get("document") or {}).get("file_id")) or "x", 120)
            out.append("Screenshot saved 📱" + (" Now tell me the total, like 3h20 (or skip)." if st["aw_state"] == "wd_screen" else ""))
        elif kind == "photo":
            if st["photo_t"] == "-":
                st["photo_t"] = t
                out.append(f"📸 Progress photo saved at {clock(t)}.")
            else:
                out.append(f"Photo received 📸 (your progress photo was already in at {clock(st['photo_t'])}).")
        elif kind == "atwork":
            if st["work_t"] == "-":
                st["work_t"] = t
                out.append(ack(t, st["work"], test, seed, "work"))
            else:
                out.append(f"Already have you at work from {clock(st['work_t'])} ✅")
        elif kind == "leave":
            st["left_t"] = t
            out.append(early_leave_plan(st, t))
        elif kind == "hint_video":
            out.append("Send it over whenever you're done 🎥")
        elif kind == "hint_photo":
            out.append("Send it over as a photo 📸")
        elif kind == "water":
            ml, sure = data
            if sure:
                out.append(water_add(ml))
            else:
                out.append(msg(f"Log {amt(ml)} of water?", confirm_buttons(f"w:{ml}")))
        elif kind == "pages":
            n, sure = data
            if sure:
                out.append(pages_log(n))
            else:
                out.append(msg(f"About {n} pages? I'll log it.", confirm_buttons(f"p:{n}")))
        elif kind == "pages_ask":
            st["aw_state"], st["aw_at"] = "pg_page", stamp(now)
            out.append("Nice, that feels good 📖 What page are you on now?")
        elif kind == "page":
            out.append(set_page(int(data)))
        elif kind == "book_new":
            title = clean(data, 40)
            out.append(msg(f"Are you reading \"{title}\" now? 📚", confirm_buttons("b:" + title)))
        elif kind == "focus":
            minutes, label = data or (25, "")
            minutes = max(1, min(minutes, 180))
            t_, rule, _ = after(now, minutes, "")
            add_reminder(st, t_, rule, f"🍅 Time's up{': ' + label[:30] if label else ''}. Nice focus, take 5.")
            out.append(f"🍅 {minutes} minutes, starting now. I'll ping you at {clock(t_)}.")
            suggest_music("focus")
        elif kind == "mood":
            if data:
                st["mood"] = str(data)
                day_set(st, today, mood=data)
                out.append(f"Mood saved: {data}/5.")
            else:
                out.append(msg("How are you feeling today?", mood_buttons()))
        elif kind == "journal":
            log_diary("diary", data)
            add_note(data)
            out.append("Saved to your diary 📝")
        elif kind == "wd":
            if st["ev_date"] == str(today) and st["ev_done"] != "-":
                out.append(f"Already saved tonight 🌙 Sleep well, {NAME}.")
            else:
                start_wd()
        elif kind == "screen":
            if data is None:
                out.append("Tell me like 3h20 and I'll save it.")
            else:
                set_screen(data)
                lim = g["screen"]
                out.append(f"📱 {fmt_min(data)} noted" + ((f", within your {fmt_min(lim)} limit 👏" if data <= lim else f", a bit over your {fmt_min(lim)} limit") if lim else "."))
        elif kind == "setwake":
            st["next_wake"] = data
            out.append(f"⏰ Done. I'll wake you at {clock(data)} tomorrow.")
            if st["aw_state"] == "wd_wake":
                finish_wd()
        elif kind == "playlist":
            parts = data.split(None, 1)
            if not parts:
                out.append("Which one? wake, stressed, low, tired, frustrated, calm, good or focus, then the Spotify link.")
            elif parts[0].lower() not in PLAYLIST_NAMES:
                out.append("Names I know: " + ", ".join(PLAYLIST_NAMES) + ".")
            elif len(parts) == 2 and parts[1].strip().lower() == "reset":
                out.append({"k": "playlist_set", "name": parts[0].lower(), "url": ""})
                out.append(f"Reset, {parts[0].lower()} goes back to a Spotify search.")
            elif len(parts) == 2 and re.match(r"^(https?://|spotify:)\S+$", parts[1].strip()):
                url = parts[1].strip().split("?")[0] if "open.spotify.com" in parts[1] else parts[1].strip()
                out.append({"k": "playlist_set", "name": parts[0].lower(), "url": url})
                out.append(f"🎧 Saved your {parts[0].lower()} playlist.")
            else:
                out.append("Send the name, then the Spotify link.")
        elif kind == "feel":
            if data:
                feeling_reply(*data)
            else:
                out.append(msg("How are you feeling right now?", emotion_buttons()))
        elif kind == "cancel":
            clear_await()
            out.append("Okay, no worries 🙂")
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
                a = find_time(parts[0])[0] if parts else None
                b = find_time(parts[1])[0] if len(parts) > 1 else None
                if a and b:
                    st["quiet"] = f"{a}-{b}"
                    out.append(f"Quiet hours: {clock(a)} to {clock(b)}. Reminders in that window wait until {clock(b)}.")
                else:
                    out.append("Tell me like 10:30pm to 5am, or say off.")
        elif kind == "buddy":
            if data.lower() == "off":
                st["buddy"] = "off"
                out.append("Weekly summary sharing is off.")
            elif re.fullmatch(r"-?\d{5,15}", data):
                st["buddy"] = data
                out.append("Done. Your weekly summary will go to that chat too. Your friend must have started this bot first.")
            else:
                out.append("Send me their Telegram chat id, or say off.")
        elif kind == "stats":
            out.append(stats_text(st))
        elif kind == "book":
            st["book"], st["page"] = data
            out.append(f"Saved: {st['book']}, page {st['page']} 📚")
        elif kind == "setrule":
            out.append(apply_setrule(st, *data))
        elif kind == "remind":
            out.append(handle_remind(st, data, ts))
        elif kind == "rlist":
            out.append(reminders_text(st, ts.date()))
        elif kind == "rremove":
            before = len(st["reminders"])
            st["reminders"] = [r for r in st["reminders"] if r["id"] != data]
            out.append("Removed 🗑️" if len(st["reminders"]) < before else f"I don't have a reminder number {data}.")
        elif kind == "rremove_txt":
            hits = [r for r in st["reminders"] if data.lower() in r["text"].lower()]
            if len(hits) == 1:
                st["reminders"] = [r for r in st["reminders"] if r["id"] != hits[0]["id"]]
                out.append(f"Removed: {hits[0]['text']} 🗑️")
            elif hits:
                out.append("Which one? " + "; ".join(f"{r['id']}. {r['text']}" for r in hits) + "\nSay \"remove\" and its number.")
            else:
                out.append("I couldn't find that one.")
        elif kind == "rclear":
            n = len(st["reminders"])
            st["reminders"] = []
            out.append(f"Cleared {n} reminder{'s' if n != 1 else ''}.")
        elif kind == "redit":
            rid, rest = data
            r = next((x for x in st["reminders"] if x["id"] == rid), None)
            if not r:
                out.append(f"I don't have a reminder number {rid}.")
            elif rest.lower().startswith("text "):
                r["text"] = rest[5:].replace("|", "/").strip()[:60] or r["text"]
                out.append("Updated ✅")
            else:
                hm = find_time(rest)[0]
                if hm:
                    r["time"], r["last"] = hm, "-"
                    out.append(f"Moved to {clock(hm)}: {r['text']}")
                else:
                    out.append("I couldn't read that time. Try 4:30pm.")
        elif kind == "status":
            out.append(status_text(st))
        elif kind == "help":
            out.append(help_text())
        elif kind == "chat":
            if data == "thanks":
                out.append(pick(seed, ["Anytime, Joseph 🙌", "Always 😊", "You've got this 💪"]))
            elif data == "greet":
                out.append(pick(seed, [f"Hey {NAME} 👋", "Hey you 😊 How's it going?"]))
        elif kind == "question":
            out.append(pick(seed, ["Good question, but that one's for Claude 😊 Ask in your chat with Claude.",
                                   "That's one for Claude, not me 😊 Ask it in your Claude chat."]))
        elif kind == "voice":
            out.append("I can't listen to voice notes yet 🙈 Could you type it?")
        elif kind == "sticker":
            pass
        else:  # other: keep it
            if len(re.findall(r"\w+", text0)) >= 3:
                log_diary("note", text0)
                add_note(text0)
                out.append("Saved to your diary 📝")
            else:
                out.append("Got it 👍")

    for u in updates:
        if "callback_query" in u:
            cq = u["callback_query"]
            data = cq.get("data", "")
            mtxt = (cq.get("message") or {}).get("text", "")
            mid = (cq.get("message") or {}).get("message_id")
            if data == "done":
                out.append({"k": "answer", "id": cq["id"], "text": "Nice work! ✅"})
                out.append({"k": "edit", "message_id": mid, "text": "✅ " + re.sub(r"^(?:⏰ )?", "", re.sub(r"\s*\(a bit late\)$", "", mtxt))})
            elif data.startswith("snz:"):
                mins_ahead = int(data.split(":")[1])
                body = re.sub(r"\s*\(a bit late\)$", "", re.sub(r"^⏰ ", "", mtxt))
                t_, rule, text = after(now, mins_ahead, body)
                add_reminder(st, t_, rule, text)
                out.append({"k": "answer", "id": cq["id"], "text": f"Snoozed {mins_ahead} min 💤"})
                out.append({"k": "edit", "message_id": mid, "text": f"💤 Okay, I'll nudge you again at {clock(t_)}"})
            elif data.startswith("emo:"):
                emo = record_emotion(data.split(":", 1)[1])
                out.append({"k": "answer", "id": cq["id"], "text": f"{nice(emo)} noted"})
                out.append({"k": "edit", "message_id": mid, "text": f"{EMO_FACE.get(emo, '🙂')} {nice(emo)}"})
                ask_why(emo, st["aw_state"] != "wd_emotion")
                suggest_music(emo)
            elif data.startswith("mood:"):
                n = int(data.split(":")[1])
                st["mood"] = str(n)
                day_set(st, today, mood=n)
                out.append({"k": "answer", "id": cq["id"], "text": "Saved 🙂"})
                out.append({"k": "edit", "message_id": mid, "text": f"Mood: {['😞', '😕', '😐', '🙂', '😄'][n - 1]} {n}/5"})
                if n <= 2:
                    suggest_music("low")
            elif data == "wd:yes":
                out.append({"k": "answer", "id": cq["id"], "text": ""})
                out.append({"k": "edit", "message_id": mid, "text": "🌙 Ready to wind down? Yes"})
                if st["ev_done"] == "-" or st["ev_date"] != str(today):
                    start_wd()
            elif data == "wd:no":
                out.append({"k": "answer", "id": cq["id"], "text": "Okay 🌙"})
                out.append({"k": "edit", "message_id": mid, "text": "Okay, rest well, " + NAME + " 🌙"})
                st["sent"] += [f for f in ("winddown2", "winddown3") if f not in st["sent"]]
            elif data.startswith("cf:"):
                parts = data.split(":", 2)
                txt = "No problem 👍"
                try:
                    if parts[1] == "w":
                        txt = water_add(int(parts[2]))
                    elif parts[1] == "p":
                        txt = pages_log(int(parts[2]))
                    elif parts[1] == "o":
                        txt = set_outdoor(now.strftime("%H:%M"))
                    elif parts[1] == "b":
                        st["book"], st["page"] = clean(parts[2], 40), "not set"
                        st["aw_state"], st["aw_at"] = "pg_page", stamp(now)
                        txt = f"Got it, {st['book']} 📚 What page are you on?"
                except (ValueError, IndexError):
                    pass
                out.append({"k": "answer", "id": cq["id"], "text": ""})
                out.append({"k": "edit", "message_id": mid, "text": txt})
            else:
                out.append({"k": "answer", "id": cq["id"], "text": ""})
            continue
        m = u.get("message") or {}
        ts = datetime.fromtimestamp(m.get("date", 0), TZ)
        t = ts.strftime("%H:%M")
        kind, data = classify(m, g["glass"])
        seed = f"{u['update_id']}{kind}"
        is_media = "video" in m or "video_note" in m or "photo" in m or str((m.get("document") or {}).get("mime_type", "")).startswith(("video", "image"))
        if kind == "start" or not started:
            if not started:
                st["started"] = str(today)
                started = True
            out.append(msg(welcome_text(st, today)))
            out.append({"k": "commands"})
            continue
        text0 = re.sub(r"^(/\w+)@\w+", r"\1", (m.get("text") or m.get("caption") or "").strip())
        if st["aw_state"] == "pg_page" and text0 and not is_media:
            mm = re.fullmatch(r"\s*(?:page\s*|pg\.?\s*|p\.?\s*)?(\d{1,4})\s*[.!]?\s*", text0, re.I)
            clear_await()
            if mm:
                out.append(set_page(int(mm[1])))
                continue
        if (st["aw_state"] != "-" and text0 and kind not in EXPLICIT and not is_media):
            handle_await(text0)
            continue
        items = data if kind == "multi" else [(kind, data)]
        for kind, data in items:
            act(kind, data)

    if not started:
        wc = tm(st, "wakecall")
        if today >= START and wc is not None and wc <= cur <= wc + 180 and "startnudge" not in st["sent"]:
            out.append(msg(f"Hey {NAME} 👋 Say hi and I'll switch your Compass on for today."))
            st["sent"].append("startnudge")
        return st, out, voice

    if st["cmdver"] != CMDVER:
        st["cmdver"] = CMDVER
        out.append({"k": "commands"})

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
            lead = "" if r["text"][:1] in ("🍅", "⏱") else "⏰ "
            out.append(msg(f"{lead}{r['text']}{late}", reminder_buttons()))
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
    once("wakenudge", wake_d + 1, st["wake_t"] == "-",
         f"It's past {clock(st['wake'])}, {NAME}. Are you up? ⏰", 120)
    once("wakenudge2", wake_d + 45, st["wake_t"] == "-",
         f"{NAME}, I haven't heard from you 😅 Up and moving, workout's next.", 120)
    once("workoutask", work_out_d - 30, st["wake_t"] != "-" and st["workout_t"] == "-",
         f"30 minutes left for your workout video, {NAME}.", 29)
    once("workoutnudge", work_out_d + 1, st["workout_t"] == "-",
         "The workout deadline has passed. Send the video when you're done, and tell me when you reach work.", 120)
    once("worknudge", work_d + 1, st["work_t"] == "-",
         f"It's past {clock(st['work'])}. Are you at work yet?", 120)
    open_tasks = []
    if g["water"] and st["water"] < g["water"]:
        open_tasks.append(f"💧 {amt(st['water'])} of {amt(g['water'])}")
    if g["pages"] and st["pages"] < g["pages"]:
        open_tasks.append(f"📖 {st['pages']} of {g['pages']} pages")
    if g["outdoor"] and st["outdoor_t"] == "-":
        open_tasks.append("🌳 outdoor workout")
    if g["photo"] and st["photo_t"] == "-":
        open_tasks.append("📸 photo")
    midday_txt = pick(f"{st['today']}md", [f"Midday check, {NAME} 🌤️ How's the day going?", f"Halfway there, {NAME}. Stay on it 💪"])
    if open_tasks:
        midday_txt += "\nStill open: " + ", ".join(open_tasks)
    once("midday", tm(st, "midday"), True, midday_txt, 120)
    once("leavecheck", tm(st, "leavecheck"), st["left_t"] == "-",
         f"How's work, {NAME}? Tell me when you head out and I'll help plan your evening.", 120)

    wd_done = st["ev_date"] == str(today) and st["ev_done"] != "-"
    wdt = tm(st, "winddown")
    once("winddown", wdt, not wd_done, msg("🌙 Ready to wind down?", wd_buttons()), 120)
    once("winddown2", None if wdt is None else wdt + 30, not wd_done and "winddown" in st["sent"],
         msg("Still up? Whenever you're ready 🌙", wd_buttons()), 60)
    once("winddown3", None if wdt is None else wdt + 60, not wd_done and "winddown" in st["sent"],
         msg("Last nudge for tonight, then rest. Tomorrow starts early.", wd_buttons()), 60)

    rv = tm(st, "review")
    if rv is not None and "review" not in st["sent"] and rv <= cur <= rv + 179 and st["reviewed"] != str(today):
        verdict = close_day(st, today)
        nxt = hm_of(mins(st["next_wake"]) + WAKE_DEADLINE_AFTER) if st["next_wake"] != "-" else st["wake"]
        out.append(f"{verdict}\nTomorrow: up by {clock(nxt)}.")
        st["sent"].append("review")
        if today.weekday() == 6 and "weekly" not in st["sent"]:
            wk = weekly_text(st, today)
            out.append(wk)
            st["sent"].append("weekly")
            if st["buddy"] != "off":
                out.append({"k": "buddy", "to": st["buddy"], "text": f"{NAME}'s weekly summary:\n" + wk})
    return st, out, voice


# ------------------------------------------------------------------- telegram

WRITES = ("sendMessage", "editMessageText", "pinChatMessage", "answerCallbackQuery", "setMyCommands", "deleteMyCommands")


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
                send_long(tg, f"📓 Nothing for {d} yet. Here's your latest:\n" + "\n".join(lines[-12:]), REMOVE_KB)
            else:
                tg.send("📓 Your diary is empty so far. Tell me about your day and I'll keep it.", REMOVE_KB)
        elif item["k"] == "feelings":
            sel = [x for x in diary_lines() if "] feeling:" in x or "] why:" in x][-24:]
            send_long(tg, ("🧡 Your feelings lately:\n" + "\n".join(sel)) if sel else "🧡 No feelings logged yet. Tell me how you're feeling any time.", REMOVE_KB)
        elif item["k"] == "export":
            if diary_lines():
                tg.send_document(DIARY, "📓 Your diary")
            else:
                tg.send("📓 Your diary is empty, so there's nothing to send yet.", REMOVE_KB)
        elif item["k"] == "edit":
            tg.call("editMessageText", chat_id=chat, message_id=item["message_id"], text=item["text"],
                    reply_markup=json.dumps({"inline_keyboard": []}))
        elif item["k"] == "answer":
            tg.call("answerCallbackQuery", callback_query_id=item["id"], text=item["text"])
        elif item["k"] == "buddy":
            tg.send(item["text"], chat=item["to"])
        elif item["k"] == "commands":
            tg.call("deleteMyCommands")  # no command menu: the chat should look like a normal chat


BACKUP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracker_backup.txt")


def fit(st, text):
    """Telegram messages hold 4096 characters: shorten diary snippets, then drop the oldest history days."""
    if len(text) > 4000:
        st["ev_diary"], st["ev_why"] = clean(st["ev_diary"], 60), clean(st["ev_why"], 60)
        text = render(st, updated=st["updated"])
    while len(text) > 4000 and len(st["hist"]) > 14:
        date_cut(st)
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
