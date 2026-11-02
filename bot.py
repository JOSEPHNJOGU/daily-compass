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
from datetime import date, datetime, timedelta, timezone

TZ = timezone(timedelta(hours=3))  # Nairobi, no daylight saving
START = date(2026, 11, 3)          # Day 1
NAME = "Joseph"
MAX_REMINDERS = 25
HIST_KEEP = 120                    # days of history kept in the tracker
DEFAULT_TIMES = dict(wakecall="05:00", midday="12:30", leavecheck="17:30", review="21:00")
DEFAULT_TARGETS = dict(water=3000, pages=10, glass=500, photo=1, outdoor=1)

DEFAULT = dict(
    day=0, streak=0, slips=0, week="2026-11-02",
    wake="05:30", workout="07:30", work="09:30", free=1,
    book="not set", page="not set",
    today="none", wake_t="-", workout_t="-", work_t="-", left_t="-", sent=[],
    last_id=0, notes="-", updated="setup", times=None, reminders=None,
    started="-", best=0, freezes=0, buddy="off", quiet="22:30-05:00", reviewed="-",
    targets=None, water=0, pages=0, photo_t="-", outdoor_t="-", mood="-",
    hist_start="-", hist="", moods="",
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
    m = re.search(r"Times: wakecall (\S+) \| midday (\S+) \| leavecheck (\S+) \| review (\S+)", text)
    if m:
        s["times"] = dict(wakecall=m[1], midday=m[2], leavecheck=m[3], review=m[4])
    m = re.search(r"Started: (\S+) \| Best: (\d+) \| Freezes: (\d+) \| Buddy: (\S+) \| Quiet: (\S+) \| Reviewed: (\S+)", text)
    if m:
        s["started"], s["best"], s["freezes"], s["buddy"], s["quiet"], s["reviewed"] = m[1], int(m[2]), int(m[3]), m[4], m[5], m[6]
    m = re.search(r"Targets: water (\d+) \| pages (\d+) \| glass (\d+) \| photo (on|off) \| outdoor (on|off)", text)
    if m:
        s["targets"] = dict(water=int(m[1]), pages=int(m[2]), glass=int(m[3]), photo=int(m[4] == "on"), outdoor=int(m[5] == "on"))
    m = re.search(r"Progress: water (\d+) \| pages (\d+) \| photo (\S+) \| outdoor (\S+) \| mood (\S+)", text)
    if m:
        s["water"], s["pages"], s["photo_t"], s["outdoor_t"], s["mood"] = int(m[1]), int(m[2]), m[3], m[4], m[5]
    m = re.search(r"History: (\S+) (\S+) \| Moods: (\S+)", text)
    if m:
        s["hist_start"] = m[1]
        s["hist"] = "" if m[2] == "-" else m[2]
        s["moods"] = "" if m[3] == "-" else m[3]
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
        f"Times: wakecall {t['wakecall']} | midday {t['midday']} | leavecheck {t['leavecheck']} | review {t['review']}",
        f"Started: {s['started']} | Best: {s['best']} | Freezes: {s['freezes']} | Buddy: {s['buddy']} | Quiet: {s['quiet']} | Reviewed: {s['reviewed']}",
        f"Targets: water {g['water']} | pages {g['pages']} | glass {g['glass']} | photo {onoff(g['photo'])} | outdoor {onoff(g['outdoor'])}",
        f"Book: {s['book']} | Page: {s['page']}",
        f"Today: {s['today']} | wake {s['wake_t']} | workout {s['workout_t']} | work {s['work_t']} | left early {s['left_t']} | sent: {sent}",
        f"Progress: water {s['water']} | pages {s['pages']} | photo {s['photo_t']} | outdoor {s['outdoor_t']} | mood {s['mood']}",
        f"History: {s['hist_start']} {s['hist'] or '-'} | Moods: {s['moods'] or '-'}",
        f"Last update id: {s['last_id']}",
    ]
    for r in sorted(s["reminders"], key=lambda r: r["id"]):
        lines.append(f"R{r['id']}: {r['time']} | {r['rule']} | {r['text']} | last {r['last']}")
    lines.append(f"Notes: {s['notes']}")
    return "\n".join(lines)


# -------------------------------------------------------------------- helpers


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


def day_set(st, d, ch=None, mood=None):
    """Record a status char and/or a mood digit for date d (aligned strings, last HIST_KEEP days)."""
    if d < START:
        return
    if st["hist_start"] == "-":
        st["hist_start"] = str(d)
    start = date.fromisoformat(st["hist_start"])
    i = (d - start).days
    if i < 0:
        return
    h, mo = list(st["hist"].ljust(i + 1, ".")), list(st["moods"].ljust(i + 1, "."))
    h, mo = h + ["."] * (len(mo) - len(h)), mo + ["."] * (len(h) - len(mo))
    if ch:
        h[i] = ch
    if mood:
        mo[i] = str(mood)
    if len(h) > HIST_KEEP:
        cut = len(h) - HIST_KEEP
        h, mo, start = h[cut:], mo[cut:], start + timedelta(days=cut)
    st["hist_start"], st["hist"], st["moods"] = str(start), "".join(h), "".join(mo)


def hist_items(st):
    if st["hist_start"] == "-":
        return []
    start = date.fromisoformat(st["hist_start"])
    return [(start + timedelta(days=i), c) for i, c in enumerate(st["hist"])]


EMO = {"P": "🟩", "S": "🟨", "F": "🟦", "X": "🟥", ".": "⬜"}


def heatmap(st, weeks=12):
    items = hist_items(st)
    if not items:
        return "(no completed days yet)"
    chars = [c for _, c in items]
    rows = ["".join(EMO.get(c, "⬜") for c in chars[i:i + 7]) for i in range(0, len(chars), 7)]
    return "\n".join(rows[-weeks:])


def stats_text(st):
    items = [c for _, c in hist_items(st) if c != "."]
    last30 = [c for _, c in hist_items(st)][-30:]
    last30 = [c for c in last30 if c != "."]
    ok30 = sum(1 for c in last30 if c in "PSF")
    pct = f"{round(100 * ok30 / len(last30))}%" if last30 else "n/a"
    moods = [int(c) for c in st["moods"][-7:] if c.isdigit()]
    mood_txt = f"{sum(moods) / len(moods):.1f}/5" if moods else "n/a"
    return (f"📊 Your stats, {NAME}\n"
            f"Day {st['day']} | Streak {st['streak']} | Best {st['best']} | Freezes {st['freezes']}/3\n"
            f"Days finished: {len(items)} | Last 30 days: {pct} on track\n"
            f"Mood (last 7 days): {mood_txt}\n"
            f"Today: water {st['water']}/{st['targets']['water']} ml, pages {st['pages']}/{st['targets']['pages']}\n"
            f"Heatmap (🟩 perfect 🟨 slip 🟦 freeze 🟥 reset):\n{heatmap(st)}")


def weekly_text(st, today):
    week = [c for d, c in hist_items(st) if today - timedelta(days=6) <= d <= today]
    p, s, f, x = (week.count(k) for k in "PSFX")
    moods = [int(c) for c in st["moods"][-7:] if c.isdigit()]
    mood_txt = f"{sum(moods) / len(moods):.1f}/5" if moods else "n/a"
    return (f"🗓️ Weekly review, {NAME}\n"
            f"Perfect days: {p} | Slips: {s} | Freezes used: {f} | Resets: {x}\n"
            f"Streak {st['streak']} (best {st['best']}) | Mood avg {mood_txt}\n"
            f"Heatmap:\n{heatmap(st, 6)}\n"
            f"Reflect: what worked this week? Reply with journal: <one line>")


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
    if rule.startswith("once:"):
        return rule[5:] == str(d)
    return False


def describe(rule, today=None):
    if rule in ("daily", "weekdays", "weekends"):
        return rule
    if rule.startswith("days:"):
        return "every " + ", ".join(x.capitalize() for x in rule[5:].split(","))
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
                date.fromisoformat(w)
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


def handle_remind(st, arg, ts):
    if len(st["reminders"]) >= MAX_REMINDERS or len(render(st)) > 3500:
        return f"The reminder list is full (limit {MAX_REMINDERS}). Remove some first."
    rem, err = parse_reminder(arg, ts.date(), ts.hour * 60 + ts.minute)
    if err:
        return err + "\nExamples: remind 15:00 call mum | remind daily 7am drink water | remind every mon,wed 18:00 gym"
    nid = add_reminder(st, rem["time"], rem["rule"], rem["text"])
    return f"Reminder {nid} set: {rem['time']}, {describe(rem['rule'], ts.date())}: {rem['text']} ⏰"


def after(now, minutes_ahead, text):
    """A one-off reminder dict `minutes_ahead` from now."""
    t = now + timedelta(minutes=minutes_ahead)
    return f"{t:%H:%M}", f"once:{t.date()}", text


# ----------------------------------------------------------- message reading

L_AWAKE, L_VIDEO, L_WORK, L_LEAVE = "☀️ I'm awake", "🎥 Workout video", "📍 I'm at work", "🚪 Leaving work"
L_OUTDOOR, L_PHOTO, L_FOCUS, L_MOOD = "🌳 Outdoor workout", "📸 Progress photo", "🍅 Focus 25", "😊 Mood"
L_STATS, L_REM, L_HELP = "📊 Stats", "⏰ Reminders", "❓ Help"
LABELS = {L_AWAKE: "awake", L_VIDEO: "hint_video", L_WORK: "atwork", L_LEAVE: "leave", L_OUTDOOR: "outdoor",
          L_PHOTO: "hint_photo", L_FOCUS: "focus", L_MOOD: "mood", L_STATS: "stats", L_REM: "rlist", L_HELP: "help"}
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
    r"^/?set\s+(wake|workout|work|wakecall|midday|leavecheck|leave|review|water|pages|glass|photo|outdoor)\s*(?:to|at)?\s*(.+)$", re.I)
RE_FOCUS = re.compile(r"^/?focus(?:\s+(\d{1,3}))?\s*(.*)$", re.I)
RE_MOOD = re.compile(r"^/?mood(?:\s+([1-5]))?\s*$", re.I)
RE_JOURNAL = re.compile(r"^/?journal\s*:?\s*(.+)$", re.I | re.S)
RE_QUIET = re.compile(r"^/?quiet\s*(?:hours)?\s*(.+)$", re.I)
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
    text = (m.get("text") or caption).strip()
    low = text.lower()
    if low == "/start":
        return "start", None
    if low in ("/status", "status"):
        return "status", None
    if low in ("/help", "help"):
        return "help", None
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
        return "water", (float(r[1]), (r[2] or "ml").lower())
    r = RE_READ.match(text)
    if r:
        return "pages", int(r[1])
    r = RE_FOCUS.match(text)
    if r:
        return "focus", (int(r[1]) if r[1] else 25, r[2].strip())
    r = RE_MOOD.match(text)
    if r:
        return "mood", (int(r[1]) if r[1] else None)
    r = RE_JOURNAL.match(text)
    if r:
        return "journal", r[1].strip()
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
    voice = (f"Good morning, {NAME}. Day {st['day']}. Text me when you're up by {spoken(st['wake'])}, "
             f"then get your workout done. You've got this.")
    return text, voice


def welcome_text(st, today):
    when = "Day 1 starts tomorrow at 5:00." if today < START else "You're on the clock. Let's go."
    return (f"👋 Welcome, {NAME}! Your Daily Compass is on.\n{when}\n\n"
            "How it works:\n"
            "• The buttons below change with your day. Tap the one that applies.\n"
            "• 🎥 videos count as your workout, 📸 photos as your progress photo.\n"
            "• Add reminders by typing, for example: remind daily 7am drink water\n"
            "• Send /help any time for everything I understand.")


def help_text():
    return (
        f"Here's what I understand, {NAME}:\n"
        "BUTTONS: tap the keyboard below.\n"
        "CHECK-INS\n• \"I'm awake\", a 🎥 video, \"I'm at work\", \"leaving early\"\n"
        "• a 🌳 video with 'outdoor' in the caption, or the 🌳 button\n• a 📸 photo = progress photo\n"
        "TASKS\n• water 500 (or water -250, water 1 l)\n• read 10 pages, or read to page 80\n"
        "• book: <title>, page <n>\n"
        "REMINDERS\n• remind 15:00 call mum\n• remind daily 7am drink water\n• remind weekdays 08:00 stand-up\n"
        "• remind every mon,wed 18:00 gym\n• reminders | remove 2 | change 2 to 4:30pm | clear reminders\n"
        "• quiet 22:30-05:00 (reminders wait until morning) | quiet off\n"
        "FOCUS, MOOD, JOURNAL\n• focus 25 (timer) | mood 4 | journal: <one line>\n"
        "STATS\n• /stats | /status | buddy <chat id> (weekly summary to a friend) | buddy off\n"
        "SETTINGS\n• set wake 05:45 (also workout, work, wakecall, midday, leavecheck, review; add off to disable)\n"
        "• set water 3000 | set pages 10 | set glass 250 | set photo off | set outdoor off"
    )


def status_text(st):
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
    if key in ("photo", "outdoor"):
        if not (on or off):
            return f"Try: set {key} on  or  set {key} off"
        st["targets"][key] = int(on)
        return f"Done. The {key} task is now {'on' if on else 'off'}."
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
    g = st["targets"]
    rows = []
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
    for r in (r1, r2, r3, [L_STATS, L_REM, L_HELP]):
        if r:
            rows.append([{"text": b} for b in r])
    return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True}


def reminder_buttons():
    return {"inline_keyboard": [[{"text": "✅ Done", "callback_data": "done"},
                                 {"text": "💤 Snooze 10 min", "callback_data": "snz:10"}]]}


def mood_buttons():
    faces = ["😞", "😕", "😐", "🙂", "😄"]
    return {"inline_keyboard": [[{"text": f"{faces[i]} {i + 1}", "callback_data": f"mood:{i + 1}"} for i in range(5)]]}


BOT_COMMANDS = [("start", "Start the Compass"), ("status", "Today at a glance"), ("stats", "Streak, heatmap, mood"),
                ("reminders", "List your reminders"), ("water", "Log water, e.g. /water 500"),
                ("focus", "Focus timer, e.g. /focus 25"), ("mood", "Rate your mood"), ("help", "Everything I understand")]


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
                od = date.fromisoformat(old)
                if od >= START:
                    out.append(f"I closed out yesterday for you, {NAME}. " + close_day(st, od))
            except ValueError:
                pass
        if today >= START:
            st["day"] += 1
        if today.weekday() == 0 and st["week"] != str(today):
            st["slips"], st["week"] = 0, str(today)
        st.update(today=str(today), wake_t="-", workout_t="-", work_t="-", left_t="-", sent=[],
                  water=0, pages=0, photo_t="-", outdoor_t="-", mood="-")
        if today >= START and st["notes"].startswith("TEST DAY"):
            st["notes"] = "-"

    def pages_add(n):
        st["pages"] += n
        if st["page"].isdigit():
            st["page"] = str(int(st["page"]) + n)

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
            elif data.startswith("mood:"):
                n = int(data.split(":")[1])
                st["mood"] = str(n)
                day_set(st, today, mood=n)
                out.append({"k": "answer", "id": cq["id"], "text": "Saved 🙂"})
                out.append({"k": "edit", "message_id": mid, "text": f"Mood saved: {['😞', '😕', '😐', '🙂', '😄'][n - 1]} {n}/5"})
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
        if kind == "awake":
            if st["wake_t"] == "-":
                st["wake_t"] = t
                out.append(ack(t, st["wake"], test, seed))
            else:
                out.append(f"Already logged you up at {st['wake_t']}. 👍")
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
            ml = val * (1000 if unit.startswith(("l", "li")) and unit != "ml" and not unit.startswith("glass") else 1)
            if unit.startswith("glass"):
                ml = val * g["glass"]
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
        elif kind == "mood":
            if data:
                st["mood"] = str(data)
                day_set(st, today, mood=data)
                out.append(f"Mood saved: {data}/5.")
            else:
                out.append(msg("How are you feeling today?", mood_buttons()))
        elif kind == "journal":
            out.append("📝 Saved to your journal. It lives in this chat, so search \"journal\" to find your entries.")
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
                    out.append(f"Quiet hours set: {a} to {b}. Reminders in that window wait until {b}.")
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
            out.append(apply_setrule(st, *data))
        elif kind == "remind":
            out.append(handle_remind(st, data, ts))
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
                hm = find_time(rest)[0]
                if hm:
                    r["time"], r["last"] = hm, "-"
                    out.append(f"Reminder {rid} moved to {hm}: {r['text']}")
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

    # custom reminders (work every day, including test days). Quiet hours defer them to the morning.
    keep = []
    for r in st["reminders"]:
        t = mins(r["time"])
        d = today
        eff = t
        if in_quiet(st, t):
            qe = mins(st["quiet"].split("-")[1])
            eff = qe
            if t >= qe:  # late-evening reminder waits for tomorrow morning
                d = today - timedelta(days=1)
        if rule_matches(r["rule"], d) and r["last"] != str(d) and eff <= cur <= eff + 120:
            late = " (a bit late)" if cur - eff > 10 else ""
            out.append(msg(f"⏰ Reminder: {r['text']}{late}", reminder_buttons()))
            r["last"] = str(d)
            if r["rule"].startswith("once:"):
                continue
        elif r["rule"].startswith("once:") and r["rule"][5:] < str(today - timedelta(days=1)):
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
        r = self.call("getChat", chat_id=self.chat)
        pm = (r.get("result") or {}).get("pinned_message")
        return (pm["message_id"], pm["text"]) if pm else (None, None)

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


def deliver(tg, chat, st, out):
    kb = keyboard_for(st)
    for item in out:
        if isinstance(item, str):
            tg.send(item, kb)
        elif item["k"] == "msg":
            tg.send(item["text"], item["markup"] or kb)
        elif item["k"] == "edit":
            tg.call("editMessageText", chat_id=chat, message_id=item["message_id"], text=item["text"],
                    reply_markup=json.dumps({"inline_keyboard": []}))
        elif item["k"] == "answer":
            tg.call("answerCallbackQuery", callback_query_id=item["id"], text=item["text"])
        elif item["k"] == "buddy":
            tg.send(item["text"], chat=item["to"])
        elif item["k"] == "commands":
            tg.call("setMyCommands", commands=json.dumps([{"command": c, "description": d} for c, d in BOT_COMMANDS]))


def cycle(tg, chat, dry, fake_now=None, poll=0):
    """One read-reply-save pass. With poll > 0, waits up to that many seconds for new messages."""
    mid, old_text = tg.tracker()
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
    if base != (old_text or ""):
        text = render(st, updated=now.strftime("%Y-%m-%d %H:%M"))
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


def main():
    load_env_file()
    token, chat = os.environ["TELEGRAM_TOKEN"], os.environ["CHAT_ID"]
    dry = os.environ.get("DRY_RUN") == "1"
    fake_now = datetime.fromisoformat(os.environ["FAKE_NOW"]).replace(tzinfo=TZ) if os.environ.get("FAKE_NOW") else None
    tg = Telegram(token, chat, dry)

    test_time = os.environ.get("TEST_TIME", "").strip()
    if test_time:
        # Safe test mode: play a fake morning at TEST_TIME on Day 1. Never edits the tracker.
        _, old_text = tg.tracker()
        st = parse(old_text) if old_text else new_state()
        st["reminders"], st["started"] = [], "test"
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
        try:
            import fcntl
            lock = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".lock"), "w")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            pass
        except OSError:
            print("another copy is already listening, exiting")
            return
        idx = sys.argv.index("--loop") + 1
        seconds = int(sys.argv[idx]) if idx < len(sys.argv) and sys.argv[idx].isdigit() else 58
        end = time.time() + seconds
        while True:
            left = end - time.time()
            if left <= 3:
                break
            try:
                ok = cycle(tg, chat, dry, fake_now, poll=int(min(10, left - 2)))
            except Exception as e:  # keep listening through brief network errors
                print("cycle error:", type(e).__name__, e)
                ok = False
            if not ok:
                time.sleep(2)
        return

    if not cycle(tg, chat, dry, fake_now):
        sys.exit(1)


if __name__ == "__main__":
    main()
