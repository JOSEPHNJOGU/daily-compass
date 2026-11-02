#!/usr/bin/env python3
"""Send the spoken (George voice) version of the morning message. Best effort."""
import os
import subprocess
import sys
import urllib.request

BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
os.makedirs("models", exist_ok=True)
try:
    text = open("voice_text.txt").read().strip()
    for name in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
        path = os.path.join("models", name)
        if not os.path.exists(path):
            urllib.request.urlretrieve(BASE + name, path)
    import soundfile as sf
    from kokoro_onnx import Kokoro
    k = Kokoro("models/kokoro-v1.0.onnx", "models/voices-v1.0.bin")
    samples, rate = k.create(text, voice="bm_george", speed=1.0, lang="en-gb")
    sf.write("note.wav", samples, rate)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", "note.wav", "-c:a", "libopus", "-b:a", "32k", "note.ogg"], check=True)
    subprocess.run(["curl", "-sS", f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/sendVoice",
                    "-F", f"chat_id={os.environ['CHAT_ID']}", "-F", "voice=@note.ogg"], check=True, stdout=subprocess.DEVNULL)
    print("voice sent")
except Exception as e:  # voice is optional; the text message was already sent
    print("voice skipped:", type(e).__name__)
    sys.exit(0)
