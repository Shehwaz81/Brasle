"""Use this laptop's microphone when the Pi has none.

  .venv/bin/python scripts/mac_mic.py                               # Pi at http://hearth.local:8080
  .venv/bin/python scripts/mac_mic.py --pi http://172.20.10.5:8080  # if .local doesn't resolve

Speech recognition (Vosk) runs here on the laptop; only the recognized text goes to the Pi, through
POST /api/hear, the same path as the Pi's own mic. The laptop stays quiet while the Pi has its own mic,
while the phone is speaking a reply (so it never hears the phone), and while the Pi can't be reached.
Leave it running in a terminal; Ctrl+C stops it.
"""
import argparse
import json
import sys
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main as core

POLL_S = 0.5


def hold_reason(s):
    """Why the laptop must not send right now, or None when it should listen. s is the Pi's /api/state."""
    if s is None:
        return "can't reach the Pi"
    if s.get("listening"):
        return "the Pi has its own mic"
    if s.get("muted"):
        return "the phone is speaking"
    return None


def watch(pi, mic_open, hold, stop):
    """Poll the Pi: tell it this laptop's mic is open, and set `hold` whenever the laptop should stay quiet."""
    last = "start"
    while not stop.is_set():
        try:
            url = f"{pi}/api/state?from=mac" + ("&mic=1" if mic_open.is_set() else "")
            with urllib.request.urlopen(url, timeout=2) as r:
                s = json.load(r)
        except Exception:
            s = None
        why = hold_reason(s)
        hold.set() if why else hold.clear()
        if why != last and why != "the phone is speaking":  # muting for each reply would flood the screen
            print(f"Standing by: {why}" if why else "Listening: the laptop mic feeds the glove", flush=True)
            last = why
        stop.wait(POLL_S)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pi", default="http://hearth.local:8080", help="the Pi's address (default %(default)s)")
    ap.add_argument("--device", help="mic index or name substring (see: python -m sounddevice); default mic if omitted")
    ap.add_argument("--model", help="path to the unzipped Vosk model (default: repo root, then models/)")
    args = ap.parse_args()
    pi = args.pi.rstrip("/")

    def send(text):
        if hold.is_set():  # finished just as a hold began: the Pi would drop it anyway
            return
        req = urllib.request.Request(f"{pi}/api/hear", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"text": text, "source": "mac"}).encode())
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                reply = json.load(r)
            print(f"  -> sent: {text}" if reply.get("queued") else f"  -> not used ({reply.get('why')}): {text}",
                  flush=True)
        except Exception as e:
            print(f"  -> could not reach the Pi ({e}): {text}", flush=True)

    mic_open, hold, stop = threading.Event(), threading.Event(), threading.Event()
    hold.set()  # quiet until the first poll says otherwise
    threading.Thread(target=watch, args=(pi, mic_open, hold, stop), daemon=True).start()
    print(f"Laptop mic for the glove. Pi: {pi}", flush=True)
    try:
        core.listen(core.find_model(args.model), send, core.parse_device(args.device), stop, hold, mic_open.set)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


if __name__ == "__main__":
    main()
