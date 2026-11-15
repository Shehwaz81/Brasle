"""Brasle core: speech -> braille vibrations on 3 rings.

Runs on a Mac (console backend, no hardware) or a Raspberry Pi (GPIO backend).
The backend is picked automatically: GPIO on a Raspberry Pi, console elsewhere.

  python main.py --text "hello world"     # typed text, no mic needed
  python main.py --selftest               # buzz each ring once, then all three
  python main.py --device USB             # live voice from the mic (no web page)
  python main.py --backend console ...    # force on-screen dots
  python main.py --doctor --device USB    # Pi preflight: PASS/FAIL per check

The full system (mic + iPhone page + judge dashboard) is app.py.
"""
import argparse
import itertools
import json
import os
import queue
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv

import agent

# Pi 5 (RP1 chip): RPi.GPIO does not work there, so gpiozero must use lgpio. Set before gpiozero is imported.
os.environ.setdefault("GPIOZERO_PIN_FACTORY", "lgpio")

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
PORT = 8080  # not 5000: macOS AirPlay Receiver holds 5000

# Standard 6-dot braille: left column = dots 1,2,3 ; right column = dots 4,5,6
BRAILLE = {
    "a": {1}, "b": {1, 2}, "c": {1, 4}, "d": {1, 4, 5}, "e": {1, 5},
    "f": {1, 2, 4}, "g": {1, 2, 4, 5}, "h": {1, 2, 5}, "i": {2, 4}, "j": {2, 4, 5},
    "k": {1, 3}, "l": {1, 2, 3}, "m": {1, 3, 4}, "n": {1, 3, 4, 5}, "o": {1, 3, 5},
    "p": {1, 2, 3, 4}, "q": {1, 2, 3, 4, 5}, "r": {1, 2, 3, 5}, "s": {2, 3, 4},
    "t": {2, 3, 4, 5}, "u": {1, 3, 6}, "v": {1, 2, 3, 6}, "w": {2, 4, 5, 6},
    "x": {1, 3, 4, 6}, "y": {1, 3, 4, 5, 6}, "z": {1, 3, 5, 6},
}
DIGITS = "zero one two three four five six seven eight nine".split()

# GPIO numbers (not header pin numbers) for ring 1, 2, 3. Override with --pins 26,19,13
DEFAULT_PINS = (26, 19, 13)

# Timing (seconds) - tune these by feel once the glove is on a hand
PULSE = 0.30       # one braille column
COL_GAP = 0.10     # between the two columns of a letter
LETTER_GAP = 0.35  # between letters
WORD_GAP = 0.80    # between words

# Short signals. Each step = (rings, seconds on, seconds off after)
OFF = (False, False, False)
R1, R2, R3, ALL = (True, False, False), (False, True, False), (False, False, True), (True, True, True)
PATTERNS = {
    "urgent": [(ALL, 0.08, 0.08)] * 3,
    "ready": [(R1, 0.15, 0.05), (R2, 0.15, 0.05), (R3, 0.15, 0.05)],
    "connected": [(R2, 0.08, 0.08)] * 2,
    "sent": [(ALL, 0.06, 0.06)] * 2,
    "error": [(ALL, 0.80, 0.0)],
    "tap_dot": [(R2, 0.04, 0.0)],
    "tap_dash": [(R2, 0.12, 0.0)],
    "selftest": [(R1, 0.6, 0.3), (R2, 0.6, 0.3), (R3, 0.6, 0.3), (ALL, 0.6, 0.0)],
}
RANK = {"urgent": 0, "normal": 1, "feedback": 2}  # lower plays first


def columns(letter):
    """3 rings can't show 6 dots at once, so each letter = 2 pulses:
    first the left column (dots 1-3), then the right column (dots 4-6)."""
    dots = BRAILLE.get(letter, set())
    left = [(i + 1) in dots for i in range(3)]
    right = [(i + 4) in dots for i in range(3)]
    return left, right


def normalize(text):
    """Lowercase, digits -> words ("3" -> "three"), drop anything with no braille pattern."""
    text = re.sub(r"[0-9]", lambda m: f" {DIGITS[int(m[0])]} ", text.lower())
    words = ("".join(c for c in w if c in BRAILLE) for w in text.split())
    return " ".join(w for w in words if w)


def buzz_seconds(text):
    """How long play_text() takes for this text (the raw-vs-final demo stat)."""
    words = normalize(text).split()
    letters = sum(len(w) for w in words)
    return letters * (2 * PULSE + COL_GAP + LETTER_GAP) + len(words) * WORD_GAP


def find_model(path=None):
    """--model if given, else the repo root, else models/. Exits with the download link if missing."""
    candidates = [Path(path)] if path else [ROOT / MODEL_NAME, ROOT / "models" / MODEL_NAME]
    for p in candidates:
        if p.is_dir():
            return str(p)
    sys.exit(f"Vosk model not found. Looked in:\n  " + "\n  ".join(map(str, candidates)) +
             f"\nDownload {MODEL_URL} and unzip it into {ROOT}")


def lan_ips():
    """IPv4 addresses other devices on the hotspot can reach us at."""
    try:  # Linux: every interface
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=2).stdout
        ips = [ip for ip in out.split() if "." in ip]
        if ips:
            return ips
    except (OSError, subprocess.SubprocessError):
        pass
    try:  # macOS fallback: the address of the default route (no packet is sent)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return [s.getsockname()[0]]
    except OSError:
        return []


def urls(port=PORT):
    host = socket.gethostname().removesuffix(".local")
    return [f"http://{host}.local:{port}"] + [f"http://{ip}:{port}" for ip in lan_ips()]


def on_raspberry_pi():
    try:
        with open("/proc/device-tree/model") as f:
            return "Raspberry Pi" in f.read()
    except OSError:
        return False


# ---------- Output backends (same interface, swap with --backend) ----------
class ConsoleBackend:
    """Mac / no-hardware: prints rings as filled/empty circles."""
    name = "console (on-screen dots only, rings will NOT buzz)"

    def pulse(self, rings, duration):
        print("  " + " ".join("●" if r else "○" for r in rings), flush=True)
        time.sleep(duration)

    def close(self):
        pass


class GPIOBackend:
    """Raspberry Pi: drives ring motors straight from GPIO (no Arduino).
    Each pin goes through a transistor driver to its vibration disc."""

    def __init__(self, pins=DEFAULT_PINS):
        try:
            from gpiozero import OutputDevice  # imported here so Mac doesn't need it
        except ImportError:
            raise SystemExit("gpiozero is not available. On the Pi, make sure the venv "
                             "can see system packages (include-system-site-packages = true).")
        self.pins = tuple(pins)
        self.rings = [OutputDevice(p) for p in self.pins]
        self.name = f"gpio (rings on GPIO {', '.join(map(str, self.pins))})"

    def pulse(self, rings, duration):
        try:
            for dev, on in zip(self.rings, rings):
                dev.on() if on else dev.off()
            time.sleep(duration)
        finally:
            for dev in self.rings:
                dev.off()

    def close(self):
        for dev in self.rings:
            dev.off()  # explicitly off: a released pin can keep its last level
            dev.close()


def make_backend(backend="auto", pins=None):
    if backend == "auto":
        backend = "gpio" if on_raspberry_pi() else "console"
    pins = tuple(int(p) for p in pins.split(",")) if pins else DEFAULT_PINS
    return GPIOBackend(pins) if backend == "gpio" else ConsoleBackend()


def play_text(text, out, stop=lambda: False):
    """Buzz already-normalized text. Checks stop() between letters."""
    for word in text.split():
        for ch in word:
            if stop():
                return
            print(ch.upper(), flush=True)
            left, right = columns(ch)
            out.pulse(left, PULSE)
            time.sleep(COL_GAP)
            out.pulse(right, PULSE)
            time.sleep(LETTER_GAP)
        time.sleep(WORD_GAP)


class Haptics:
    """The only thread that touches the rings. Everything buzzes through say() / buzz(),
    which queue work by priority: urgent before normal before feedback."""

    def __init__(self, out, on_rings=None, on_playing=None):
        self.out = out
        self.on_rings = on_rings or (lambda rings: None)        # live ring state, for the dashboard
        self.on_playing = on_playing or (lambda playing: None)  # True while braille text plays
        self.q = queue.PriorityQueue()
        self.cancel = threading.Event()  # set by an urgent message to cut in between letters
        self.closing = False
        self._seq = itertools.count()    # keeps FIFO order within one priority
        self.thread = threading.Thread(target=self._run, name="haptics", daemon=True)
        self.thread.start()

    def say(self, text, priority="normal"):
        """Queue text as braille. priority: "urgent" (cuts in, starts with the urgent pattern) or "normal"."""
        text = normalize(text)
        if text:
            self._put(RANK[priority], "text", text)

    def buzz(self, pattern):
        if pattern not in PATTERNS:
            raise ValueError(f"unknown pattern {pattern!r}")
        self._put(RANK["feedback"], pattern, None)

    def wait(self):
        """Block until everything queued so far has played."""
        self.q.join()

    def close(self):
        """Drop anything queued, stop after the current pulse, rings off, pins released."""
        self.closing = True
        self._put(-1, "stop", None)
        self.thread.join(timeout=5)

    def _put(self, rank, kind, text):
        self.q.put((rank, next(self._seq), kind, text))
        if rank == RANK["urgent"]:
            self.cancel.set()

    def _run(self):
        try:
            while True:
                # Clear before get(): an urgent message queued after this still interrupts what we pick up.
                self.cancel.clear()
                rank, _, kind, text = self.q.get()
                try:
                    if kind == "stop":
                        return
                    self._play(rank, kind, text)
                except Exception:
                    traceback.print_exc()  # never let one bad message kill the worker
                finally:
                    self.q.task_done()
        finally:
            self.out.close()

    def _play(self, rank, kind, text):
        stop = lambda: self.closing or (rank > RANK["urgent"] and self.cancel.is_set())
        if kind != "text":
            print(f"[buzz] {kind}", flush=True)
            self._pattern(kind, stop)
            return
        print(f"[buzz] {'URGENT ' if rank == RANK['urgent'] else ''}{text!r}", flush=True)
        if rank == RANK["urgent"]:
            self._pattern("urgent", stop)
        self.on_playing(True)
        try:
            play_text(text, self, stop)
        finally:
            self.on_playing(False)

    def _pattern(self, name, stop):
        for rings, on, off in PATTERNS[name]:
            if stop():
                return
            self.pulse(rings, on)
            time.sleep(off)

    def pulse(self, rings, duration):
        self.on_rings(list(rings))
        try:
            self.out.pulse(rings, duration)
        finally:
            self.on_rings(list(OFF))


def listen(model_path, on_text, device=None, stop_event=None, muted=None, on_ready=None):
    """Mic -> Vosk -> on_text(text) for every finished phrase. Blocks until stop_event is set.

    Loads the model itself, so run it on a background thread. While `muted` is set, audio is
    thrown away. If the mic is missing or drops out, logs it and retries every 2 s.
    """
    import sounddevice as sd
    from vosk import KaldiRecognizer, Model

    stop_event = stop_event or threading.Event()
    muted = muted or threading.Event()
    print(f"Loading speech model {model_path} ...", flush=True)
    model = Model(model_path)

    while not stop_event.is_set():
        try:
            rate = int(sd.query_devices(device, "input")["default_samplerate"])
            rec = KaldiRecognizer(model, rate)
            audio_q = queue.Queue()
            with sd.RawInputStream(samplerate=rate, blocksize=8000, dtype="int16", channels=1,
                                   device=device,
                                   callback=lambda data, frames, t, status: audio_q.put(bytes(data))) as stream:
                print(f"Listening on {sd.query_devices(stream.device)['name']!r} at {rate} Hz", flush=True)
                if on_ready:
                    on_ready()
                was_muted = False
                while not stop_event.is_set():
                    if not stream.active:
                        raise RuntimeError("mic stream stopped (unplugged?)")
                    try:
                        chunk = audio_q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if muted.is_set():
                        was_muted = True
                        continue
                    if was_muted:
                        rec.Reset()  # forget half-heard words from before the mute
                        was_muted = False
                    if rec.AcceptWaveform(chunk):
                        text = json.loads(rec.Result()).get("text", "")
                        if text:
                            print(f"Heard: {text}", flush=True)
                            try:
                                on_text(text)
                            except Exception:
                                traceback.print_exc()
        except Exception as e:
            print(f"MIC ERROR (device {device or 'default'!r}): {e} -- retrying in 2 s", flush=True)
            stop_event.wait(2)
            # shortcut: private sounddevice API; re-scans devices so a mic plugged in later is found
            sd._terminate()
            sd._initialize()


def doctor(args):
    """Demo-table preflight. Prints PASS/FAIL per check, returns the number of failures."""
    failures = 0

    def check(name, fn):
        nonlocal failures
        try:
            print(f"PASS  {name}: {fn()}", flush=True)
        except (Exception, SystemExit) as e:  # SystemExit: find_model / GPIOBackend explain themselves
            failures += 1
            print(f"FAIL  {name}: {e}", flush=True)

    on_systemd = Path("/run/systemd/system").exists()
    if on_systemd and subprocess.run(["systemctl", "is-active", "--quiet", "brasle"]).returncode == 0:
        print("NOTE  the brasle service is running and holds the rings and mic. "
              "Run `sudo systemctl stop brasle` first, then start it again after.", flush=True)

    def pi_model():
        model = Path("/proc/device-tree/model").read_text().rstrip("\x00")
        if "Raspberry Pi" not in model:
            raise RuntimeError(f"not a Raspberry Pi ({model})")
        return model

    def python_version():
        if sys.version_info < (3, 9):
            raise RuntimeError(f"Python {sys.version.split()[0]} is too old, need 3.9+")
        return sys.version.split()[0]

    out = None

    def gpio():
        nonlocal out
        out = make_backend("gpio", args.pins)
        from gpiozero import Device
        factory = type(Device.pin_factory).__name__
        if "LGPIO" not in factory.upper():
            raise RuntimeError(f"pin factory is {factory}, expected LGPIOFactory")
        return f"gpiozero OK, pin factory {factory}, {out.name}"

    def rings():
        if out is None:
            raise RuntimeError("skipped, GPIO check failed")
        try:
            for i in range(3):
                print(f"      ring {i + 1} (GPIO {out.pins[i]}) ...", flush=True)
                out.pulse([j == i for j in range(3)], 0.3)
                time.sleep(0.3)
        finally:
            out.close()
        return "each ring pulsed 0.3 s: you should have felt 1, 2, 3 in order"

    def mic():
        import array
        import sounddevice as sd
        inputs = [f"{i}: {d['name']}" for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
        print("      input devices: " + ("; ".join(inputs) or "none"), flush=True)
        device = parse_device(args.device)
        info = sd.query_devices(device, "input")
        rate = int(info["default_samplerate"])
        print(f"      recording 2 s from {info['name']!r} at {rate} Hz ... make some noise", flush=True)
        with sd.RawInputStream(samplerate=rate, channels=1, dtype="int16", device=device) as stream:
            data, _ = stream.read(2 * rate)
        samples = array.array("h", bytes(data))
        peak = max(map(abs, samples), default=0)
        rms = (sum(x * x for x in samples) / max(len(samples), 1)) ** 0.5
        level = f"level rms {rms:.0f}, peak {peak} (of 32767)"
        if peak < 30:
            raise RuntimeError(f"silent: {level}. Wrong device, muted, or unplugged?")
        return level

    def vosk_model():
        from vosk import Model, SetLogLevel
        SetLogLevel(-1)  # keep the PASS/FAIL list readable
        path = find_model(args.model)
        Model(path)
        return path

    def port():
        try:
            with socket.socket() as s:
                s.bind(("0.0.0.0", PORT))
            return f"{PORT} is free"
        except OSError:
            import urllib.request
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/state", timeout=2) as r:
                    if "rings" in json.load(r):
                        return f"{PORT} is held by Brasle itself (already running)"
            except Exception:
                pass
            raise RuntimeError(f"port {PORT} is used by another program (see: sudo lsof -i :{PORT})")

    def network():
        found = urls()
        if len(found) < 2:
            raise RuntimeError(f"no LAN IP: join the iPhone hotspot first. Would be {found[0]}/tap")
        return "  ".join(f"{u}/tap" for u in found)

    check("Pi model", pi_model)
    check("Python", python_version)
    check("GPIO", gpio)
    check("Rings", rings)
    check("Mic", mic)
    check("Vosk model", vosk_model)
    check("Port", port)
    check("URLs", network)
    print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}", flush=True)
    return failures


def parse_device(device):
    """--device 3 means index 3; anything else is a name substring like "USB"."""
    return int(device) if device and device.isdigit() else device


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help=f"path to the unzipped Vosk model (default: ./{MODEL_NAME} or ./models/{MODEL_NAME})")
    ap.add_argument("--text", help="skip the mic and play this text")
    ap.add_argument("--backend", choices=["auto", "console", "gpio"], default="auto",
                    help="auto = gpio on a Raspberry Pi, console elsewhere")
    ap.add_argument("--pins", help="GPIO numbers for rings 1,2,3 (default 26,19,13)")
    ap.add_argument("--device", help="mic index or name substring (see: python -m sounddevice)")
    ap.add_argument("--selftest", action="store_true", help="buzz each ring once and exit")
    ap.add_argument("--doctor", action="store_true", help="Pi preflight: PASS/FAIL per check, non-zero exit on failure")
    args = ap.parse_args()
    if args.doctor:
        sys.exit(1 if doctor(args) else 0)

    # systemd stops us with SIGTERM: turn it into the same clean exit as Ctrl+C (rings off).
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    model = None if args.text or args.selftest else find_model(args.model)

    out = make_backend(args.backend, args.pins)
    print(f"Output: {out.name}", flush=True)
    haptics = Haptics(out)
    try:
        if args.selftest:
            haptics.buzz("selftest")
            haptics.wait()
        elif args.text:
            haptics.say(args.text)
            haptics.wait()
        else:
            history = []

            def on_text(text):
                priority, message = agent.shorten_incoming(text, history)
                history.append({"who": "them", "raw": text, "final": message, "ts": time.time()})
                if priority != "skip":
                    haptics.say(message, priority)

            listen(model, on_text, parse_device(args.device))
    except KeyboardInterrupt:
        pass
    finally:
        haptics.close()


if __name__ == "__main__":
    main()
