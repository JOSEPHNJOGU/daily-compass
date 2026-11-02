#!/usr/bin/env python3
"""Joseph's Daily Compass: a Telegram accountability bot that runs on GitHub Actions.

Standard library only. State lives in a pinned Telegram message (the "tracker"),
so nothing is stored in the repository. Secrets come from environment variables:
TELEGRAM_TOKEN and CHAT_ID. Set DRY_RUN=1 to print instead of sending.
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
DAY_END = 21 * 60                  # evening review time (minutes after midnight)

DEFAULT = dict(
    day=0, streak=0, slips=0, week="2026-11-02",
    wake="05:30", workout="07:30", work="09:30", free=1,
    book="not set", page="not set",
    today="none", wake_t="-", workout_t="-", work_t="-", left_t="-", sent=[],
    last_id=0, notes="-", updated="setup",
)

# ---------------------------------------------------------------- tracker text


def parse(text):
    s = dict(DEFAULT)
    s["sent"] = []
    m = re.search(r"Day: (\d+).*?Streak: (\d+).*?Slips this week: (\d+) \(week of (\S+?)\)", text)
    if m:
        s["day"], s["streak"], s["slips"], s["week"] = int(m[1]), int(m[2]), int(m[3]), m[4]
    m = re.search(r"Rules: wake (\S+) \| workout video (\S+) \| at work (\S+) \| free slips/week (\d+)", text)
    if m:
        s["wake"], s["workout"], s["work"], s["free"] = m[1], m[2], m[3], int(m[4])
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
    return s


def render(s, updated=None):
    sent = ",".join(s["sent"]) if s["sent"] else "-"
    return (
        "📊 COMPASS TRACKER\n"
        f"Updated: {updated or s['updated']}\n"
        f"Day: {s['day']} (starts {START}) | Streak: {s['streak']} | Slips this week: {s['slips']} (week of {s['week']})\n"
        f"Rules: wake {s['wake']} | workout video {s['workout']} | at work {s['work']} | free slips/week {s['free']} (2nd slip resets)\n"
        f"Book: {s['book']} | Page: {s['page']}\n"
        f"Today: {s['today']} | wake {s['wake_t']} | workout {s['workout_t']} | work {s['work_t']} | left early {s['left_t']} | sent: {sent}\n"
        f"Last update id: {s['last_id']}\n"
        f"Notes: {s['notes']}"
    )


# -------------------------------------------------------------------- helpers


def mins(hm):
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def hm_str(total):
    return f"{total // 60:02d}:{total % 60:02d}"


def spoken(hm):
    h, m = [int(x) for x in hm.split(":")]
    ampm = "a m" if h < 12 else "p m"
    h12 = h % 12 or 12
    return f"{h12} {ampm}" if m == 0 else f"{h12} {m:02d} {ampm}"


def pick(seed, options):
    return random.Random(seed).choice(options)


# ----------------------------------------------------------- message reading

RE_BOOK = [
    re.compile(r"book\s*:\s*[\"“'‘]?(.+?)[\"”'’]?\s*,\s*page\s*(\d+)", re.I),
    re.compile(r"reading\s+[\"“'‘]?(.+?)[\"”'’]?\s*,?\s*(?:on\s+)?page\s*(\d+)", re.I),
]
RE_PAGE = re.compile(r"\bpage\s*(\d+)\b|\bp\.?\s*(\d+)\b", re.I)
RE_SET = re.compile(r"\bset\s+(wake|workout|work)\s*(?:to|at)?\s*(\d{1,2}):(\d{2})", re.I)
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
    r = RE_SET.search(text)
    if r:
        return "setrule", (r[1].lower(), f"{int(r[2]):02d}:{r[3]}")
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


def late_info(t, deadline):
    diff = mins(t) - mins(deadline)
    return diff


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
    left = max(DAY_END - mins(t), 0)
    hrs = left / 60
    book = st["book"]
    page = st["page"]
    if book == "not set":
        read = "1. Read for 30-45 minutes. Tell me your book with: book: <title>, page <n>"
    else:
        block = 60 if hrs >= 4 else 40 if hrs >= 2 else 25
        read = f"1. Read {block} minutes of {book}, continuing from page {page}"
    lines = [
        f"You're out early, {NAME}. About {hrs:.0f} hours of free time before tonight. Use it well:",
        read,
        "2. A 20-minute walk or a stretch session",
        "3. Plan tomorrow: three things that must happen",
        "4. Drink water and eat a proper meal",
        "Text me the page you reach.",
    ]
    return "\n".join(lines)


def wake_call(st):
    rules = f"wake {st['wake']}, workout video {st['workout']}, at work {st['work']}"
    text = pick(f"{st['today']}wc", [
        f"Good morning, {NAME}! ☀️ Day {st['day']}.\nToday: text me you're up by {st['wake']}, send your workout video by {st['workout']}, and text me when you're at work by {st['work']}.\nLet's go.",
        f"Rise and shine, {NAME}. Day {st['day']} is yours. 💪\nUp by {st['wake']}. Workout video by {st['workout']}. At work by {st['work']}.",
    ])
    voice = (f"Good morning, {NAME}. Day {st['day']}. Text me when you're up by {spoken(st['wake'])}, "
             f"then get your workout done. You've got this.")
    return text, voice


def help_text():
    return ("Here's what I understand, " + NAME + ":\n"
            "• \"I'm awake\" (any wording)\n• a video = workout\n• \"I'm at work\"\n"
            "• \"leaving early\" or \"left work\"\n• \"book: <title>, page <n>\"\n"
            "• \"read to page <n>\"\n• \"set wake 05:45\" (also workout, work)\n• /status")


def status_text(st):
    return (f"Day {st['day']} | Streak {st['streak']} | Slips this week {st['slips']}/{st['free']} free\n"
            f"Today: wake {st['wake_t']}, workout {st['workout_t']}, work {st['work_t']}\n"
            f"Book: {st['book']}, page {st['page']}")


# -------------------------------------------------------------------- engine


def run(st, updates, now):
    """Apply messages and due phases. Returns (state, outbox, voice_text)."""
    out, voice = [], None
    today = now.date()
    cur = now.hour * 60 + now.minute
    test = today < START

    # day rollover
    if st["today"] != str(today) and cur >= 4 * 60 + 45:
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
                out.append(ack("awake", t, st["wake"], test or today < START, seed))
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
            which, val = data
            st[{"wake": "wake", "workout": "workout", "work": "work"}[which]] = val
            out.append(f"Done. Your {which} deadline is now {val}.")
        elif kind == "status":
            out.append(status_text(st))
        elif kind == "help":
            out.append(help_text())
        else:
            out.append("Noted. I only understand check-ins for now. Send /help to see them.")

    if test:
        return st, out, voice

    def once(flag, start, cond, text, max_late=180):
        nonlocal voice
        if flag in st["sent"] or not cond:
            return
        if start <= cur <= start + max_late:
            if isinstance(text, tuple):
                out.append(text[0])
                voice = text[1]
            else:
                out.append(text)
            st["sent"].append(flag)

    wake_d, work_out_d, work_d = mins(st["wake"]), mins(st["workout"]), mins(st["work"])
    once("wakecall", 5 * 60, True, wake_call(st), 6 * 60)
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
    once("midday", 12 * 60 + 30, True,
         pick(f"{st['today']}md", [
             f"Midday check, {NAME}. How's the day going? Remember your reading today. 📚",
             f"Halfway there, {NAME}. Stay on it. Water, focus, and your reading later."]), 120)
    once("leavecheck", 17 * 60 + 30, st["left_t"] == "-",
         f"How's work, {NAME}? Text me when you leave and I'll plan your evening.", 120)

    if "review" not in st["sent"] and DAY_END <= cur <= DAY_END + 179:
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
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            return json.loads(e.read().decode() or "{}")

    def tracker(self):
        r = self.call("getChat", chat_id=self.chat)
        pm = (r.get("result") or {}).get("pinned_message")
        return (pm["message_id"], pm["text"]) if pm else (None, None)

    def send(self, text):
        return self.call("sendMessage", chat_id=self.chat, text=text)


def load_env_file(path="compass.env"):
    """Optional KEY=VALUE file next to the script (used on shared hosting)."""
    here = os.path.dirname(os.path.abspath(__file__))
    full = os.path.join(here, path)
    if os.path.exists(full):
        for line in open(full, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main():
    load_env_file()
    token, chat = os.environ["TELEGRAM_TOKEN"], os.environ["CHAT_ID"]
    dry = os.environ.get("DRY_RUN") == "1"
    now = datetime.fromisoformat(os.environ["FAKE_NOW"]).replace(tzinfo=TZ) if os.environ.get("FAKE_NOW") else datetime.now(TZ)
    tg = Telegram(token, chat, dry)

    test_time = os.environ.get("TEST_TIME", "").strip()
    if test_time:
        # Safe test mode: play a fake morning at TEST_TIME on Day 1. Reads your rules and book
        # from the tracker but never edits it and never reads your messages.
        _, old_text = tg.tracker()
        st = parse(old_text) if old_text else dict(DEFAULT, sent=[])
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

    mid, old_text = tg.tracker()
    st = parse(old_text) if old_text else dict(DEFAULT, sent=[])

    r = tg.call("getUpdates", offset=st["last_id"] + 1, timeout=0, allowed_updates=json.dumps(["message"]))
    if not r.get("ok"):
        print("getUpdates failed:", r)
        sys.exit(1)
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
    print(f"ok {now:%Y-%m-%d %H:%M} messages={len(updates)} sent={len(out)}")


if __name__ == "__main__":
    main()
