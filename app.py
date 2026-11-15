"""Brasle web server: the Pi mic -> glove pipeline plus the iPhone Morse page and judge dashboard.

  python app.py --device USB            # on the Pi (systemd runs exactly this)
  python app.py --backend console       # on a Mac: rings print as dots

  /       judge dashboard (laptop)
  /tap    iPhone Morse page (add it to the Home Screen)
"""
import argparse
import itertools
import queue
import signal
import sys
import threading
import time
import traceback
from collections import OrderedDict

from flask import Flask, Response, jsonify, request, send_from_directory

import agent
import main as core
import tts

PHONE_TIMEOUT = 3      # seconds without a poll before the phone counts as disconnected
MUTE_SAFETY = 20       # seconds: auto-unmute the mic if the phone never says it stopped speaking
KEEP_MESSAGES = 50
KEEP_AUDIO = 10

MORSE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E", "..-.": "F", "--.": "G",
    "....": "H", "..": "I", ".---": "J", "-.-": "K", ".-..": "L", "--": "M", "-.": "N",
    "---": "O", ".--.": "P", "--.-": "Q", ".-.": "R", "...": "S", "-": "T", "..-": "U",
    "...-": "V", ".--": "W", "-..-": "X", "-.--": "Y", "--..": "Z",
    "-----": "0", ".----": "1", "..---": "2", "...--": "3", "....-": "4",
    ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9",
}


def decode_morse(morse):
    """Letters separated by " ", words by " / ". Unknown codes become "?"."""
    words = [w.split() for w in morse.split("/")]
    return " ".join("".join(MORSE.get(code, "?") for code in w) for w in words if w)


app = Flask(__name__, static_folder="static", static_url_path="")
lock = threading.Lock()  # guards state and audio
state = {"listening": False, "muted": False, "rings": [False, False, False], "playing": False,
         "phone_last_seen": None, "conversation": [], "seconds_saved": 0.0}
audio = OrderedDict()    # id -> mp3 bytes, last KEEP_AUDIO replies
audio_ids = itertools.count(1)
mic_muted = threading.Event()
unmute_timer = None
haptics = None           # core.Haptics, created in run()
heard = queue.Queue()    # phrases from the mic (or /api/hear), handled in order by one worker


def set_state(**changes):
    with lock:
        state.update(changes)


def add_message(entry):
    with lock:
        state["conversation"] = (state["conversation"] + [entry])[-KEEP_MESSAGES:]


def history():
    with lock:
        return list(state["conversation"])


def phone_seen():
    set_state(phone_last_seen=time.time())


def set_muted(muted):
    global unmute_timer
    with lock:
        if unmute_timer:
            unmute_timer.cancel()
        unmute_timer = threading.Timer(MUTE_SAFETY, set_muted, [False]) if muted else None
        if unmute_timer:
            unmute_timer.daemon = True
            unmute_timer.start()
        mic_muted.set() if muted else mic_muted.clear()
        state["muted"] = muted


def on_heard(text):
    """Mic -> agent -> glove."""
    info = {}
    try:
        priority, message = agent.shorten_incoming(text, history(), info)
    except Exception:
        traceback.print_exc()
        priority, message = "normal", text
    buzz_raw = core.buzz_seconds(text)
    buzz_final = 0.0 if priority == "skip" else core.buzz_seconds(message)
    add_message({"who": "them", "raw": text, "final": message, "priority": priority, "ts": time.time(),
                 "buzz_raw": buzz_raw, "buzz_final": buzz_final,
                 "reason": info.get("reason"), "ms": info.get("ms")})
    with lock:
        state["seconds_saved"] += buzz_raw - buzz_final
    if priority != "skip":
        haptics.say(message, priority)


def heard_worker():
    """The only caller of on_heard: the mic loop never waits on the network, and messages stay in order."""
    while True:
        text = heard.get()
        try:
            on_heard(text)
        except Exception:
            traceback.print_exc()
        finally:
            heard.task_done()


threading.Thread(target=heard_worker, name="agent", daemon=True).start()


@app.after_request
def no_store(resp):
    # Home Screen apps cache aggressively; always serve fresh HTML/JS/JSON.
    if not resp.mimetype.startswith("image/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/")
def dashboard():
    return send_from_directory(app.static_folder, "dashboard.html")


@app.get("/tap")
def tap():
    phone_seen()
    haptics.buzz("connected")
    return send_from_directory(app.static_folder, "tap.html")


@app.get("/api/state")
def api_state():
    if request.args.get("from") == "phone":
        phone_seen()
    with lock:
        s = dict(state, conversation=list(state["conversation"]))
    s["phone_connected"] = bool(s["phone_last_seen"]) and time.time() - s["phone_last_seen"] < PHONE_TIMEOUT
    return jsonify(s)


@app.post("/api/reply")
def api_reply():
    phone_seen()
    morse = (request.get_json(silent=True) or {}).get("morse")
    if not isinstance(morse, str):
        return jsonify(error="send JSON {\"morse\": \"... --- ...\"}"), 400
    decoded = decode_morse(morse)
    if not decoded:
        haptics.buzz("error")
        return jsonify(decoded="", final="", audio_url=None)
    try:
        final = agent.fix_outgoing(decoded, history()) or decoded
    except Exception:
        traceback.print_exc()
        final = decoded
    try:
        mp3 = tts.speak(final)
    except Exception:
        traceback.print_exc()
        mp3 = None
    audio_url = None
    if mp3:
        with lock:
            audio_id = str(next(audio_ids))
            audio[audio_id] = mp3
            while len(audio) > KEEP_AUDIO:
                audio.popitem(last=False)
        audio_url = f"/api/audio/{audio_id}"
    add_message({"who": "me", "raw": decoded, "final": final, "ts": time.time()})
    print(f"Reply: {decoded!r} -> {final!r}", flush=True)
    haptics.buzz("sent")
    return jsonify(decoded=decoded, final=final, audio_url=audio_url)


@app.get("/api/audio/<audio_id>")
def api_audio(audio_id):
    with lock:
        mp3 = audio.get(audio_id)
    if mp3 is None:
        return jsonify(error="no such audio"), 404
    return Response(mp3, mimetype="audio/mpeg")


@app.post("/api/speaking")
def api_speaking():
    phone_seen()
    speaking = (request.get_json(silent=True) or {}).get("speaking")
    if not isinstance(speaking, bool):
        return jsonify(error="send JSON {\"speaking\": true|false}"), 400
    set_muted(speaking)
    return jsonify(muted=speaking)


@app.post("/api/feedback")
def api_feedback():
    phone_seen()
    kind = (request.get_json(silent=True) or {}).get("kind")
    if kind not in ("dot", "dash"):
        return jsonify(error="kind must be dot or dash"), 400
    with lock:
        playing = state["playing"]
    if not playing:  # a tap buzz in the middle of braille would garble the letter
        haptics.buzz(f"tap_{kind}")
    return jsonify(ok=True)


def run_listener(model, device, stop_event):
    def ready():
        set_state(listening=True)
        haptics.buzz("ready")
    try:
        core.listen(model, heard.put, device, stop_event, mic_muted, ready)
    except Exception:
        traceback.print_exc()
        print("LISTENER DIED: the iPhone page still works, but speech will not reach the glove.", flush=True)
    set_state(listening=False)


def run():
    global haptics
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="path to the unzipped Vosk model (default: repo root, then models/)")
    ap.add_argument("--backend", choices=["auto", "console", "gpio"], default="auto")
    ap.add_argument("--pins", help="GPIO numbers for rings 1,2,3 (default 26,19,13)")
    ap.add_argument("--device", help="mic index or name substring, e.g. USB")
    ap.add_argument("--port", type=int, default=core.PORT)
    args = ap.parse_args()

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # systemd stop -> finally below
    model = core.find_model(args.model)
    out = core.make_backend(args.backend, args.pins)
    print(f"Output: {out.name}", flush=True)
    haptics = core.Haptics(out, on_rings=lambda rings: set_state(rings=rings),
                           on_playing=lambda playing: set_state(playing=playing))
    stop_event = threading.Event()
    threading.Thread(target=run_listener, args=(model, core.parse_device(args.device), stop_event),
                     name="listener", daemon=True).start()
    try:
        print("\nOpen on the iPhone / laptop:", flush=True)
        for url in core.urls(args.port):
            print(f"  {url}/tap    {url}/", flush=True)
        app.run(host="0.0.0.0", port=args.port, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        haptics.close()


if __name__ == "__main__":
    run()
