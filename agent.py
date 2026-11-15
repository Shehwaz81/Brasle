"""The agent seam between people and the glove. Today: pass-through stubs, no network.

`history` is the recent conversation, oldest first, as a list of
{"who": "them" | "me", "raw": str, "final": str, "ts": float}.
Keys (OPENAI_API_KEY, EXA_API_KEY) come from .env, already loaded by main.py.
"""


def shorten_incoming(text, history):
    """Speech the Pi mic heard -> what to buzz on the glove.

    Returns (priority, message), where priority is "urgent" | "normal" | "skip".

    Future (OpenAI): braille costs about a second per letter, so rewrite `text` into
    the fewest letters that keep the meaning, using `history` for context. Return
    "urgent" for danger or anything addressed to the user by name ("fire", "watch out"),
    and "skip" for filler and noise. May use Exa for a quick fact lookup when the speaker
    asks something the user would want answered. On any error or timeout, fall back to
    ("normal", text) so the glove always buzzes something.
    """
    return "normal", text


def fix_outgoing(text, history):
    """Text the user tapped in Morse (already decoded, may contain "?") -> what the phone says.

    Future (OpenAI): fix typos and "?" letters, expand abbreviations, and turn terse
    replies into a natural sentence using `history`, without changing the meaning.
    On any error or timeout, return `text` unchanged.
    """
    return text
