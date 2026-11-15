"""Text-to-speech for replies the user taps on the iPhone: ElevenLabs, or the phone's own voice."""
import json
import os
import urllib.parse
import urllib.request

MODEL = "eleven_flash_v2_5"  # ElevenLabs' fast model: the phone is waiting on the audio
TIMEOUT = 5                  # seconds, then the phone speaks with its own voice


def speak(text):
    """Text -> MP3 bytes for the iPhone to play, or None.

    None tells the phone to use its own built-in voice (speechSynthesis), which works
    offline, so returning None is always safe. That is what happens with no
    ELEVENLABS_API_KEY or ELEVENLABS_VOICE_ID in .env, and on any error or timeout.
    """
    key, voice = os.environ.get("ELEVENLABS_API_KEY"), os.environ.get("ELEVENLABS_VOICE_ID")
    if not (key and voice):
        return None
    url = "https://api.elevenlabs.io/v1/text-to-speech/" + urllib.parse.quote(voice, safe="")
    body = json.dumps({"text": text, "model_id": MODEL}).encode()
    req = urllib.request.Request(url, body, {"xi-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read()  # MP3 is the API's default output format
    except Exception as e:
        print(f"ElevenLabs could not speak the reply, the phone will use its own voice: {e!r}", flush=True)
        return None
