"""Text-to-speech seam for replies the user taps on the iPhone. Today: a stub, no network."""


def speak(text):
    """Text -> MP3 bytes for the iPhone to play, or None.

    None tells the phone to use its own built-in voice (speechSynthesis), which works
    offline, so returning None is always safe.

    Future (ElevenLabs): POST `text` to the text-to-speech API with ELEVENLABS_API_KEY and
    ELEVENLABS_VOICE_ID from .env, return the MP3 bytes. Use a short timeout (about 5 s) and
    return None on any error so the phone falls back to its own voice.
    """
    return None
