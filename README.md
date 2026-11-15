# Hearth <img width="44" height="44" alt="Hearth logo" src="static/logo.svg" />




**Speech you can feel.** Hearth is a wearable glove that turns spoken words into braille based vibration patterns, so deafblind people can follow a conversation by touch.

A USB microphone picks up speech, offline speech recognition turns it into text, and a Raspberry Pi pulses three vibrating rings on a glove in the pattern of each letter. Speech recognition runs on the device, so no audio ever leaves it. Claude (Haiku 5.5) then shortens each phrase to the fewest letters that keep its meaning, flags urgent ones and skips noise; without internet the glove simply buzzes the raw words.

Try the Live Web Simulator Here ---> https://staging.d1037t7wpg0zfd.amplifyapp.com/#how

> First built at **Hack Dearborn 5** (as SixSense), where it won **3rd place**.



https://github.com/user-attachments/assets/e3931427-1830-4823-ae05-c111e474c40d


## How it works


1. **Listen:** `sounddevice` streams audio from the USB mic into [Vosk](https://alphacephei.com/vosk/), an offline speech recognizer.
2. **Encode:** each recognized letter is looked up in a table of standard 6-dot braille patterns.
3. **Vibrate:** the Pi switches its GPIO pins in time with the pattern, driving the vibration discs through transistors.

### Fitting 6-dot braille onto 3 rings

A braille cell has 6 dots, but the glove has 3 rings. So each letter is sent as **two pulses**: first the left column (dots 1-3), then the right column (dots 4-6).

| Letter | Pulse 1 (dots 1-3) | Pulse 2 (dots 4-6) |
|---|---|---|
| a | ● ○ ○ | ○ ○ ○ |
| h | ● ● ○ | ○ ● ○ |
| l | ● ● ● | ○ ○ ○ |

With three more discs, the same code could show a whole cell at once.


## Hardware

| Part | Notes |
|---|---|
| Raspberry Pi 5 (a Pi 4 also works) | Runs everything. The 40-pin header is the same, so the wiring is too |
| USB microphone | Any USB mic works |
| 3 × vibration discs | Adafruit Vibrating Mini Motor Discs |
| 3 × NPN transistors | e.g. 2N2222 / PN2222, one per disc |
| 3 × resistors | e.g. 1 kΩ, between each GPIO pin and its transistor base |
| 3 × diodes | e.g. 1N4001 / 1N4148, across each disc |
| Breadboard, jumper wires, glove | Electric Tape to hold the discs on the fingers |
| Soldering Iron | Optional but may be useful to solder the discs to jumper wire |

### Wiring (one ring)

GPIO pins can't power a vibration motor directly, so each disc has a small transistor driver:

- GPIO pin → resistor → transistor **base**
- Transistor **emitter** → Pi GND
- Disc between the supply rail (5 V from the Pi) and the transistor **collector**
- Diode across the disc, stripe toward the supply side
- All grounds connected together

| Ring | GPIO | Physical header pin |
|---|---|---|
| 1 | GPIO 26 | pin 37 |
| 2 | GPIO 19 | pin 35 |
| 3 | GPIO 13 | pin 33 |

The GPIO numbers are set in `main.py` (`DEFAULT_PINS`) and can be changed from the command line with `--pins`.

## Software

- Python 3
- [Vosk](https://alphacephei.com/vosk/) (offline speech-to-text)
- `sounddevice` (microphone input)
- `gpiozero` + `lgpio` (GPIO control on the Pi, installed with apt, not pip)
- `python-dotenv` (API keys from `.env`)
- `anthropic` (Claude Haiku 5.5: shortening, reply fixing, web answers)
- ElevenLabs text-to-speech (plain HTTPS from the standard library)

### Project files

| File | What it does |
|---|---|
| `main.py` | The pipeline: listening, braille encoding and motor output, with command line options |
| `app.py` | Web server on port 8080: runs the pipeline, serves the iPhone Morse page and the judge dashboard |
| `tts.py` | ElevenLabs text-to-speech for replies (falls back to the phone's own voice) |
| `static/` | `tap.html` (iPhone page), `dashboard.html` (judges), Home Screen manifest and icon |
| `agent.py` | Claude: shortens and prioritizes incoming speech, fixes outgoing replies, answers questions with web search. Falls back to pass-through on any error |
| `scripts/try_agent.py` | Runs canned sentences through the real Claude API and prints raw → final, priority, reason, seconds saved, latency |
| `requirements.txt` | Python dependencies (pip) |
| `.env.example` | Copy to `.env` and fill in API keys (never commit `.env`) |

## Setup

### 1. Get the code

```bash
git clone https://github.com/Shehwaz81/BrasilGlove.git
cd BrasilGlove
```

### 2. Create the environment and install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**On the Raspberry Pi:** `gpiozero` and `lgpio` come from apt (`sudo apt install python3-gpiozero python3-lgpio`), not pip (pip-building lgpio fails on the Pi). Create the venv with `python3 -m venv --system-site-packages .venv` so it can see them.

API keys: `cp .env.example .env` and fill it in (`ANTHROPIC_API_KEY`, `BRASLE_USER_NAME`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`). Every key is optional: without one, that part falls back.

### 3. Download the speech model

The model `vosk-model-small-en-us-0.15` is already in the repo root. The code looks for it there, then in `models/`. If it is missing, download [vosk-model-small-en-us-0.15.zip](https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip) and unzip it into the repo folder.

## Pi 5 + iPhone bring-up

The iPhone, the Pi and the judges' laptop all join the iPhone's Personal Hotspot. The Pi serves everything on port 8080.
Replace `hearth` below with the Pi's hostname (`hostname` on the Pi).

1. **Wi-Fi country** (otherwise Wi-Fi stays blocked): `sudo raspi-config nonint do_wifi_country US`
2. **iPhone:** Settings → Personal Hotspot → Allow Others to Join ON, **Maximize Compatibility ON**. Keep that screen open while the Pi joins (the hotspot hides itself when nobody is connected).
3. **On the Pi:** `sudo nmcli dev wifi connect "<hotspot>" password "<pw>"`. It is saved and reconnects automatically.
4. **Setup + preflight:**
   ```bash
   scripts/pi_setup.sh                                  # safe to re-run
   .venv/bin/python main.py --doctor --device USB       # every line should say PASS
   sudo systemctl start hearth                        # or reboot: the glove buzzes "ready" (sweep 1→2→3)
   ```
   Stop the service before re-running `--doctor` (`sudo systemctl stop hearth`): the service holds the rings and the mic.
5. **iPhone Safari:** open `http://hearth.local:8080/tap` → Share → **Add to Home Screen**, then launch it from the Home Screen. The glove buzzes twice on ring 2 when the page connects.
6. **iPhone for the demo:** silent mode OFF, volume up, Settings → Display → Auto-Lock **Never**, VoiceOver OFF (VoiceOver captures taps).
7. **Laptop:** join the hotspot and open `http://hearth.local:8080/` for the judges.
8. **If `.local` doesn't resolve,** give the Pi a fixed IP on the hotspot and use `http://172.20.10.14:8080/tap` instead:
   ```bash
   sudo nmcli con mod "<hotspot>" ipv4.method manual ipv4.addresses 172.20.10.14/28 ipv4.gateway 172.20.10.1 ipv4.dns 172.20.10.1
   sudo nmcli con down "<hotspot>"; sudo nmcli con up "<hotspot>"
   ```

**No USB mic on the Pi?** The glove uses the first mic it can get: the Pi's own, then a laptop's, then the iPhone's.
- **Laptop mic:** on the laptop (same Wi-Fi as the Pi), run `.venv/bin/python scripts/mac_mic.py` (add `--pi http://<pi-ip>:8080` if `hearth.local` doesn't resolve) and leave it running. Speech recognition runs on the laptop; only text goes to the Pi. It stands by on its own while the Pi has a mic or the phone is speaking. The first run asks for microphone permission for the terminal.
- **iPhone mic:** if neither the Pi nor a laptop has a mic, the Morse page listens with Safari's speech recognition after the first tap ("phone mic on" at the top). Safari may not allow this over plain http or from the Home Screen; then the page simply doesn't listen.
- The dashboard shows which mic is in use. The Simulate speech box always works.

At the demo table: plug in power → the Pi boots → the service starts → the glove buzzes `ready`.
Logs: `journalctl -u hearth -f`.

| Glove signal | Pattern |
|---|---|
| ready | sweep ring 1 → 2 → 3 |
| connected (iPhone page opened) | 2 short pulses on ring 2 |
| tap feedback | 40 ms (dot) / 120 ms (dash) on ring 2 |
| sent | 2 quick pulses on all rings |
| error (nothing to send) | 1 long pulse on all rings |
| urgent (before an urgent message) | 3 fast pulses on all rings |

## Usage

### The full system: `app.py`

```bash
python app.py --device USB          # on the Pi
python app.py --backend console     # on a Mac: rings print as dots in the terminal
```

At startup it prints the URLs to open, for example `http://hearth.local:8080/tap`.
It uses port 8080 because macOS AirPlay Receiver holds port 5000.

| URL | Who | What |
|---|---|---|
| `/tap` | Deafblind user, iPhone | The whole screen is one Morse key. Tap = dot, hold (250 ms or more) = dash, pause 0.7 s = next letter, pause 1.5 s = space. Swipe right = send, swipe left = delete, swipe up = ask the assistant (the answer buzzes on the glove, nothing is spoken). Every tap buzzes ring 2 on the glove. The reply is spoken out loud by the phone. |
| `/` | Judges, laptop | Live rings, the conversation (raw vs. Claude-edited, with Claude's reason, latency and web searches), braille seconds saved this session, phone/mic status, and a Simulate speech box (no mic needed) |

The flow:

- **In:** someone speaks → Pi mic → Vosk → `agent.shorten_incoming` (one worker thread, in order) → braille on the glove
- **Out:** Morse on the iPhone → `POST /api/reply` → decoded on the Pi → `agent.fix_outgoing` → `tts.speak` → the phone says it out loud (ElevenLabs MP3, or the phone's own voice if that fails). The Pi mic is muted while the phone speaks.
- **Ask:** swipe up → `POST /api/reply` with `ask: true` → `agent.answer` (Claude decides whether to search the web) → the answer buzzes on the glove.

API: `GET /api/state`, `POST /api/reply {"morse": ".... .. / - ....", "ask": false}`, `POST /api/hear {"text": "..."}` (same path as the mic), `POST /api/speaking {"speaking": true}`, `POST /api/feedback {"kind": "dot"}`, `GET /api/audio/<id>`.

### Core only: `main.py`

The output type is chosen automatically: real vibrations on a Raspberry Pi, on-screen dots anywhere else.

```bash
# Listen to the microphone from the command line
python main.py --device "USB"

# Play typed text instead of speech (no mic or model needed)
python main.py --text "hello world"

# Buzz each ring once to check the wiring
python main.py --selftest
```

| Option | Meaning |
|---|---|
| `--model PATH` | Path to the unzipped Vosk model (default: repo root, then `models/`) |
| `--device NAME` | Microphone name or index (list them with `python -m sounddevice`) |
| `--backend auto\|console\|gpio` | Force on-screen dots (`console`) or real vibrations (`gpio`) |
| `--pins 26,19,13` | GPIO numbers for rings 1, 2 and 3 |
| `--text "..."` | Play this text and exit |
| `--selftest` | Buzz each ring once and exit |

Timing (pulse length, gaps between letters and words) is set at the top of `main.py` and can be tuned by feel.

## Testing

No hardware needed (pytest is a dev tool, not in `requirements.txt`):

```bash
pip install pytest
python -m pytest -q
```

Covers Morse decoding, braille columns, digit normalization, the buzz-time estimate, priority-queue ordering (urgent first, urgent cutting into a long message), `/api/reply`, and the AI layer with Claude and ElevenLabs mocked (timeouts, bad JSON, refusals, skip, ask, missing keys).

Against the real API (needs `ANTHROPIC_API_KEY`): `python scripts/try_agent.py`.

Quick API check with the server running: `curl -X POST localhost:8080/api/reply -H 'Content-Type: application/json' -d '{"morse":".... .."}'` → `{"decoded": "HI", ...}`.

## Design decisions

- **Offline speech recognition (Vosk):** works without wifi, and speech never leaves the device.
- **Vibration discs, not buzzers:** piezo buzzers (provided by the MHL) are mostly sound and are hard to feel, so we used real vibration motors.
- **Two pulses per letter:** keeps the hardware to three motors.
- **Raspberry Pi GPIO directly:** decided against Arduino (provided by MHL again) because the pi allowed for simpler wiring and more features
- **Swappable output backends:** the same code runs on a laptop for testing and on the Pi for real vibrations.

## Limitations and next steps

- Not yet tested with deaf-blind users or braille readers. Timing and practicality need feedback from them.
- Letters only: numbers are spelled out as words ("3" → "three"), punctuation is dropped.
- English speech model only.
- Next: more discs for a full 6-dot cell, a battery and enclosure, and a more wearable build, as well as a more user-friendly interface.

## Team

- Ali Alfoaady, Software Lead
- Rayan Sidiqqui, Hardware Lead
- Patrick Mikha, Project Lead
- Hassan El-Sabeh, Marketing Specialist

