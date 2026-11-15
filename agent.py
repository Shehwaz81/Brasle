"""The agent seam between people and the glove. Replies go through ChatGPT; incoming speech is still a pass-through.

`history` is the recent conversation, oldest first, as a list of
{"who": "them" | "me", "raw": str, "final": str, "ts": float}.
Keys (OPENAI_API_KEY, EXA_API_KEY) come from .env, already loaded by main.py.
"""
import json
import os
import urllib.request

OPENAI_URL = "https://api.openai.com/v1/responses"
OPENAI_MODEL = "gpt-6-luna"  # OpenAI's small, fast model: the phone is waiting on the answer
TIMEOUT = 5                  # seconds, then the reply goes out as tapped
MAX_REPLY = 200              # characters: a longer answer is ChatGPT talking, not what was tapped

FIX_PROMPT = """You fix short messages that a deafblind person tapped in Morse code on a phone.
You get the conversation so far ("them" is other people, "me" is the user), the raw Morse they
tapped (letters separated by spaces, words by " / ") and its letter-by-letter decoding, where
"?" marks a code that matched no letter.

Work out what they meant to say. Assume the usual tapping mistakes: a dot held too long or a
dash too short, a pause in the wrong place that split or merged letters, a missing or doubled
letter, a missing space. Expand obvious abbreviations and write it as one short, natural
sentence they would say out loud.

Never answer the message, never add information, never change the meaning. If you cannot tell
what they meant, return the decoded text unchanged. Reply with the sentence only."""


def ask_chatgpt(instructions, text):
    """One ChatGPT call -> its reply text. Raises on any failure, so callers must fall back."""
    body = json.dumps({"model": OPENAI_MODEL, "instructions": instructions, "input": text}).encode()
    req = urllib.request.Request(OPENAI_URL, body, {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
                                                    "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        output = json.load(r)["output"]
    return "".join(part["text"] for item in output if item["type"] == "message"
                   for part in item["content"] if part["type"] == "output_text").strip()


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


def fix_outgoing(text, history, morse=""):
    """Text the user tapped in Morse (already decoded, may contain "?") -> what the phone says.

    ChatGPT fixes typos and "?" letters, expands abbreviations, and turns terse replies into
    a natural sentence using `history` and the raw `morse`, without changing the meaning.
    With no OPENAI_API_KEY, or on any error or timeout, returns `text` unchanged.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        return text
    said = "\n".join(f'{m["who"]}: {m["final"]}' for m in history[-6:]) or "(nothing yet)"
    try:
        fixed = ask_chatgpt(FIX_PROMPT, f"Conversation so far:\n{said}\n\nMorse: {morse}\nDecoded: {text}")
    except Exception as e:
        print(f"ChatGPT could not fix the reply, sending it as tapped: {e!r}", flush=True)
        return text
    return fixed if 0 < len(fixed) <= MAX_REPLY else text
