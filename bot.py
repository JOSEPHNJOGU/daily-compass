#!/usr/bin/env python3
"""Joseph's Daily Compass: a Telegram accountability bot. Standard library only.

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
MAX_REMINDERS = 30
DEFAULT_TIMES = dict(wakecall="05:00", midday="12:30", leavecheck="17:30", review="21:00")

DEFAULT = dict(
    day=0, streak=0, slips=0, week="2026-11-02",
    wake="05:30", workout="07:30", work="09:30", free=1,
    book="not set", page="not set",
    today="none", wake_t="-", workout_t="-", work_t="-", left_t="-", sent=[],
    last_id=0, notes="-", updated="setup", times=None, reminders=None,
)


def new_state():
    s = dict(DEFAULT)
    s["sent"], s["times"], s["reminders"] = [], dict(DEFAULT_TIMES), []
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
    m = re.search(r"Book: (.*?) \| Page: (\S+)", text)
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
    t = s["times"]
    lines = [
        "📊 COMPASS TRACKER",
        f"Updated: {updated or s['updated']}",
        f"Day: {s['day']} (starts {START}) | Streak: {s['streak']} | Slips this week: {s['slips']} (week of {s['week']})",
        f"Rules: wake {s['wake']} | workout video {s['workout']} | at work {s['work']} | free slips/week {s['free']} (2nd slip resets)",
        f"Times: wakecall {t['wakecall']} | midday {t['midday']} | leavecheck {t['leavecheck']} | review {t['review']}",
        f"Book: {s['book']} | Page: {s['page']}",
        f"Today: {s['today']} | wake {s['wake_t']} | workout {s['workout_t']} | work {s['work_t']} | left early {s['left_t']} | sent: {sent}",
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
    if every and toks and rule is None:
        # "every day" => daily
        for i, t in enumerate(toks):
            if t.strip(",.").lower() == "day":
                rule, used[i] = "daily", True
    if days and rule is None:
        days = [d for d in DAYNAMES if d in days]
        if every:
            rule = "days:" + ",".join(days)
        else:  # a single upcoming weekday
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
    return dict(time=hm, rule=rule, text=text[:200]), None


def reminders_text(st, today):
    if not st["reminders"]:
        return "No reminders yet. Try: remind 15:00 call mum, or remind daily 7am drink water."
    lines = ["Your reminders:"]
    for r in sorted(st["reminders"], key=lambda r: (r["time"], r["id"])):
        lines.append(f"{r['id']}) {r['time']}, {describe(r['rule'], today)}: {r['text']}")
    lines.append("Remove: remove <number>. Change time: change <number> to <time>.")
    return "\n".join(lines)


def handle_remind(st, arg, ts):
    if len(st["reminders"]) >= MAX_REMINDERS:
        return f"You already have {MAX_REMINDERS} reminders. Remove some first."
    rem, err = parse_reminder(arg, ts.date(), ts.hour * 60 + ts.minute)
    if err:
        return err + "\nExamples: remind 15:00 call mum | remind daily 7am drink water | remind every mon,wed 18:00 gym"
    nid = max([r["id"] for r in st["reminders"]], default=0) + 1
    rem.update(id=nid, last="-")
    st["reminders"].append(rem)
    return f"Reminder {nid} set: {rem['time']}, {describe(rem['rule'], ts.date())}: {rem['text']} ⏰"


# ----------------------------------------------------------- message reading

RE_BOOK = [
    re.compile(r"book\s*:\s*[\"“'‘]?(.+?)[\"”'’]?\s*,\s*page\s*(\d+)", re.I),
    re.compile(r"reading\s+[\"“'‘]?(.+?)[\"”'’]?\s*,?\s*(?:on\s+)?page\s*(\d+)", re.I),
]
RE_PAGE = re.compile(r"\bpage\s*(\d+)\b|\bp\.?\s*(\d+)\b", re.I)
RE_SET = re.compile(r"^/?set\s+(wake|workout|work|wakecall|midday|leavecheck|leave|review)\s*(?:to|at)?\s*(.+)$", re.I)
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


def classify(m):
    """Return (kind, data) for one Telegram message."""
    doc = m.get("document") or {}
    if "video" in m or "video_note" in m or str(doc.get("mime_type", "")).startswith("video"):
        return "workout", None
    text = (m.get("text") or m.get("caption") or "").strip()
    low = text.lower()
    if low in ("/status", "status"):
        return "status", None
    if low in ("/start", "/help", "help"):
        return "help", None
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


def ack(kind, t, deadline, test, seed):
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
        read = f"1. Read {block} minutes of {book}, continuing from page {page}"
    return "\n".join([
        f"You're out early, {NAME}. About {hrs:.0f} hours of free time before tonight. Use it well:",
        read,
        "2. A 20-minute walk or a stretch session",
        "3. Plan tomorrow: three things that must happen",
        "4. Drink water and eat a proper meal",
        "Text me the page you reach.",
    ])


def wake_call(st):
    text = pick(f"{st['today']}wc", [
        f"Good morning, {NAME}! ☀️ Day {st['day']}.\nToday: text me you're up by {st['wake']}, send your workout video by {st['workout']}, and text me when you're at work by {st['work']}.\nLet's go.",
        f"Rise and shine, {NAME}. Day {st['day']} is yours. 💪\nUp by {st['wake']}. Workout video by {st['workout']}. At work by {st['work']}.",
    ])
    voice = (f"Good morning, {NAME}. Day {st['day']}. Text me when you're up by {spoken(st['wake'])}, "
             f"then get your workout done. You've got this.")
    return text, voice


def help_text():
    return (
        f"Here's what I understand, {NAME}:\n"
        "CHECK-INS\n• \"I'm awake\" (any wording)\n• a video = workout\n• \"I'm at work\"\n"
        "• \"leaving early\" or \"left work\"\n"
        "READING\n• book: <title>, page <n>\n• read to page <n>\n"
        "REMINDERS\n• remind 15:00 call mum (once)\n• remind daily 7am drink water\n"
        "• remind weekdays 08:00 stand-up\n• remind every mon,wed 18:00 gym\n"
        "• remind tomorrow 9am dentist\n• reminders (list)\n• remove 2\n• change 2 to 4:30pm\n• clear reminders\n"
        "TIMES\n• set wake 05:45 (also workout, work)\n• set midday 13:00, set review 21:30, set wakecall 04:50, set leavecheck 17:00\n"
        "• set midday off (switches a message off)\n• /status"
    )


def status_text(st):
    return (f"Day {st['day']} | Streak {st['streak']} | Slips this week {st['slips']}/{st['free']} free\n"
            f"Today: wake {st['wake_t']}, workout {st['workout_t']}, work {st['work_t']}\n"
            f"Book: {st['book']}, page {st['page']}\n"
            f"Deadlines: wake {st['wake']}, workout {st['workout']}, work {st['work']}\n"
            f"Reminders: {len(st['reminders'])} (send \"reminders\" to see them)")


def apply_setrule(st, key, val):
    key = {"leave": "leavecheck"}.get(key, key)
    off = val.lower().strip() in ("off", "none", "stop", "disable")
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


# -------------------------------------------------------------------- engine


def run(st, updates, now):
    """Apply messages, reminders and due phases. Returns (state, outbox, voice_text)."""
    out, voice = [], None
    today = now.date()
    cur = now.hour * 60 + now.minute
    test = today < START

    # day rollover
    roll_at = min(4 * 60 + 45, (tm(st, "wakecall") or 300) - 15)
    if st["today"] != str(today) and cur >= roll_at:
        if today >= START:
            st["day"] += 1
        if today.weekday() == 0 and st["week"] != str(today):
            st["slips"], st["week"] = 0, str(today)
        st.update(today=str(today), wake_t="-", workout_t="-", work_t="-", left_t="-", sent=[])
        if today >= START and st["notes"].startswith("TEST DAY"):
            st["notes"] = "-"

    for u in updates:
        m = u.get("message") or {}
        ts = datetime.fromtimestamp(m.get("date", 0), TZ)
        t = ts.strftime("%H:%M")
        kind, data = classify(m)
        seed = f"{u['update_id']}{kind}"
        if kind == "awake":
            if st["wake_t"] == "-":
                st["wake_t"] = t
                out.append(ack("awake", t, st["wake"], test, seed))
            else:
                out.append(f"Already logged you up at {st['wake_t']}. 👍")
        elif kind == "workout":
            if st["workout_t"] == "-":
                st["workout_t"] = t
                out.append(ack("workout", t, st["workout"], test, seed) + " Video received. 🎥")
            else:
                out.append(f"Another video, nice. Your workout is already logged at {st['workout_t']}.")
        elif kind == "atwork":
            if st["work_t"] == "-":
                st["work_t"] = t
                out.append(ack("work", t, st["work"], test, seed))
            else:
                out.append(f"Already logged you at work at {st['work_t']}. 👍")
        elif kind == "leave":
            st["left_t"] = t
            out.append(early_leave_plan(st, t))
        elif kind == "book":
            st["book"], st["page"] = data
            out.append(f"Saved: {st['book']}, page {st['page']}. 📚")
        elif kind == "page":
            st["page"] = data
            out.append(f"Page {data} saved. Nice reading, {NAME}. 📖")
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
                r["text"] = rest[5:].replace("|", "/").strip()[:200] or r["text"]
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

    # custom reminders (these work every day, including test days)
    keep = []
    for r in st["reminders"]:
        t = mins(r["time"])
        if rule_matches(r["rule"], today) and r["last"] != str(today) and t <= cur <= t + 120:
            out.append(f"⏰ Reminder: {r['text']}" + (" (a bit late)" if cur - t > 10 else ""))
            r["last"] = str(today)
            if r["rule"].startswith("once:"):
                continue
        elif r["rule"].startswith("once:") and r["rule"][5:] < str(today):
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
    once("midday", tm(st, "midday"), True,
         pick(f"{st['today']}md", [
             f"Midday check, {NAME}. How's the day going? Remember your reading today. 📚",
             f"Halfway there, {NAME}. Stay on it. Water, focus, and your reading later."]), 120)
    once("leavecheck", tm(st, "leavecheck"), st["left_t"] == "-",
         f"How's work, {NAME}? Text me when you leave and I'll plan your evening.", 120)

    rv = tm(st, "review")
    if rv is not None and "review" not in st["sent"] and rv <= cur <= rv + 179:
        misses = []
        for label, got, d in (("wake", st["wake_t"], st["wake"]),
                              ("workout video", st["workout_t"], st["workout"]),
                              ("at work", st["work_t"], st["work"])):
            if got == "-":
                misses.append(f"{label} missing")
            elif mins(got) > mins(d):
                misses.append(f"{label} late ({got})")
        if not misses:
            st["streak"] += 1
            verdict = f"Perfect day. Streak: {st['streak']}. 🔥"
        else:
            st["slips"] += 1
            if st["slips"] <= st["free"]:
                verdict = (f"Slip: {', '.join(misses)}. That uses your free slip this week. Streak kept at {st['streak']}.")
            else:
                st["streak"], st["slips"], st["day"] = 0, 0, 0
                verdict = (f"Slip: {', '.join(misses)}. Second slip this week, so the streak resets. Tomorrow is a fresh Day 1.")
        left = max(st["free"] - st["slips"], 0)
        out.append(f"Evening review, {NAME}.\n{verdict}\nFree slips left this week: {left}.\n"
                   f"Tomorrow: up by {st['wake']}. Text me the page you reached tonight. 📖")
        st["sent"].append("review")
    return st, out, voice


# ------------------------------------------------------------------- telegram


class Telegram:
    def __init__(self, token, chat, dry):
        self.token, self.chat, self.dry = token, chat, dry

    def call(self, method, **params):
        if self.dry and method in ("sendMessage", "editMessageText", "pinChatMessage"):
            print(f"[DRY {method}] {params.get('text', '')}")
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

    def send(self, text):
        return self.call("sendMessage", chat_id=self.chat, text=text[:4000])


def load_env_file(path="compass.env"):
    """Optional KEY=VALUE file next to the script (used on shared hosting)."""
    full = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    if os.path.exists(full):
        for line in open(full, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def cycle(tg, chat, dry, fake_now=None, poll=0):
    """One read-reply-save pass. With poll > 0, waits up to that many seconds for new messages."""
    mid, old_text = tg.tracker()
    st = parse(old_text) if old_text else new_state()

    r = tg.call("getUpdates", offset=st["last_id"] + 1, timeout=poll, allowed_updates=json.dumps(["message"]))
    if not r.get("ok"):
        print("getUpdates failed:", r)
        return False
    now = fake_now or datetime.now(TZ)
    updates = [u for u in r["result"] if str((u.get("message") or {}).get("chat", {}).get("id")) == str(chat)]
    if r["result"]:
        st["last_id"] = max(u["update_id"] for u in r["result"])

    st, out, voice = run(st, updates, now)
    for text in out:
        tg.send(text)
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
        print(f"ok {now:%Y-%m-%d %H:%M:%S} messages={len(updates)} sent={len(out)}")
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
        st["reminders"] = []
        h, m = test_time.split(":")
        now = datetime(START.year, START.month, START.day, int(h), int(m), tzinfo=TZ)
        st.update(day=1, today=str(START), wake_t="-", workout_t="-", work_t="-", left_t="-", sent=[])
        st, out, voice = run(st, [], now)
        for text in out:
            tg.send(f"🧪 TEST ({test_time}): {text}")
        if not out:
            tg.send(f"🧪 TEST ({test_time}): nothing would be sent at this time.")
        if voice:
            with open("voice_text.txt", "w") as f:
                f.write(voice)
        print(f"test ok {test_time} sent={len(out)}")
        return

    if "--loop" in sys.argv:
        # Stay alive about a minute, listening for messages (for a cron job that starts every minute).
        # A lock file makes sure only one copy listens at a time.
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
