"""Text-to-speech for replies the user taps on the iPhone: ElevenLabs, standard library only."""
import json
import os
import time
import urllib.parse
import urllib.request

MODEL = "eleven_flash_v2_5"  # ElevenLabs' low-latency model (~100 ms)


def speak(text):
    """Text -> MP3 bytes for the iPhone to play, or None.

    None tells the phone to use its own built-in voice (speechSynthesis), which works
    offline, so returning None is always safe: no keys, no network, any error, all -> None.
    Keys come from .env: ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID.
    """
    key, voice = os.environ.get("ELEVENLABS_API_KEY"), os.environ.get("ELEVENLABS_VOICE_ID")
    if not (key and voice and text):
        return None
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{urllib.parse.quote(voice, safe='')}"
        "?output_format=mp3_44100_128",
        data=json.dumps({"text": text, "model_id": MODEL}).encode(),
        headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            mp3 = r.read()
    except Exception as e:  # HTTPError, URLError, timeouts: the phone's own voice takes over
        print(f"[tts] FALLBACK {type(e).__name__}: {e}", flush=True)
        return None
    print(f"[tts] {len(mp3)} bytes in {round((time.monotonic() - t0) * 1000)} ms", flush=True)
    return mp3 or None
