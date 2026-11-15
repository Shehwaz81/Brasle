"""The agent seam between people and the glove. Replies go through Claude; incoming speech is still a pass-through.

`history` is the recent conversation, oldest first, as a list of
{"who": "them" | "me", "raw": str, "final": str, "ts": float}.
Keys (ANTHROPIC_API_KEY, EXA_API_KEY) come from .env, already loaded by main.py.
"""
import os

CLAUDE_MODEL = "claude-haiku-5-5"  # the fast model: the phone is waiting on the answer
TIMEOUT = 8                        # seconds, then the reply goes out as tapped
MAX_REPLY = 200                    # characters: a longer answer is Claude talking, not what was tapped

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


def ask_claude(instructions, text):
    """One Claude call -> its reply text. Raises on any failure, so callers must fall back."""
    import anthropic  # imported here so a Pi without the package still runs: the reply goes out as tapped

    client = anthropic.Anthropic(timeout=TIMEOUT, max_retries=0)  # no retries: the phone is waiting
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        system=instructions,
        output_config={"effort": "low"},  # a one-sentence fix: answer fast
        messages=[{"role": "user", "content": text}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined the request")
    return "".join(block.text for block in response.content if block.type == "text").strip()


def shorten_incoming(text, history):
    """Speech the Pi mic heard -> what to buzz on the glove.

    Returns (priority, message), where priority is "urgent" | "normal" | "skip".

    Future (Claude): braille costs about a second per letter, so rewrite `text` into
    the fewest letters that keep the meaning, using `history` for context. Return
    "urgent" for danger or anything addressed to the user by name ("fire", "watch out"),
    and "skip" for filler and noise. May use Exa for a quick fact lookup when the speaker
    asks something the user would want answered. On any error or timeout, fall back to
    ("normal", text) so the glove always buzzes something.
    """
    return "normal", text


def fix_outgoing(text, history, morse=""):
    """Text the user tapped in Morse (already decoded, may contain "?") -> what the phone says.

    Claude fixes typos and "?" letters, expands abbreviations, and turns terse replies into
    a natural sentence using `history` and the raw `morse`, without changing the meaning.
    With no ANTHROPIC_API_KEY, or on any error or timeout, returns `text` unchanged.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return text
    said = "\n".join(f'{m["who"]}: {m["final"]}' for m in history[-6:]) or "(nothing yet)"
    try:
        fixed = ask_claude(FIX_PROMPT, f"Conversation so far:\n{said}\n\nMorse: {morse}\nDecoded: {text}")
    except Exception as e:
        print(f"Claude could not fix the reply, sending it as tapped: {e!r}", flush=True)
        return text
    return fixed if 0 < len(fixed) <= MAX_REPLY else text
