#!/usr/bin/env python3
"""Checks that this hosting plan can run the Compass bot. Safe: changes nothing.

Usage:  python3 host_check.py            (read-only checks)
        python3 host_check.py --send     (also sends one hello message to your Telegram)
Needs compass.env next to this file with TELEGRAM_TOKEN and CHAT_ID.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

print("Python version:", sys.version.split()[0], "(need 3.8 or newer)")
here = os.path.dirname(os.path.abspath(__file__))
env = {}
path = os.path.join(here, "compass.env")
if not os.path.exists(path):
    print("MISSING: compass.env (copy compass.env.example, fill it in)")
    sys.exit(1)
for line in open(path, encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
token, chat = env.get("TELEGRAM_TOKEN", ""), env.get("CHAT_ID", "")
print("compass.env found. Token set:", bool(token), "| Chat id set:", bool(chat))


def call(method, **params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


try:
    me = call("getMe")
    print("Telegram reachable. Bot:", me["result"]["username"])
    if "--send" in sys.argv:
        r = call("sendMessage", chat_id=chat, text="Hello from your hosting! The Compass can run here. ✅")
        print("Hello message sent:", r.get("ok"))
except Exception as e:
    print("FAILED to reach Telegram:", type(e).__name__, e)
    print("Your plan may block outgoing connections. Ask HostAfrica support to allow outgoing HTTPS to api.telegram.org.")
    sys.exit(1)
print("All good.")
