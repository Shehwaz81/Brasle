"""No-hardware, no-network tests: python -m pytest -q"""
import json
import threading
import urllib.error
from types import SimpleNamespace as NS

import anthropic
import httpx2
import pytest

import agent
import app
import main
import tts


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    """No test reaches the network: keys loaded from .env are removed; tests that need one set a fake."""
    for key in ("ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "BRASLE_USER_NAME", "CLAUDE_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(agent, "_client", None)


@pytest.mark.parametrize("morse, text", [
    (".... ..", "HI"),
    (".... .. / .-- --- .-. .-.. -..", "HI WORLD"),
    ("...-- -----", "30"),
    ("........ ..", "?I"),         # unknown code -> "?"
    (" / .... .. /  / ", "HI"),     # stray separators are ignored
    ("", ""),
])
def test_decode_morse(morse, text):
    assert app.decode_morse(morse) == text


@pytest.mark.parametrize("letter, left, right", [
    ("a", [1, 0, 0], [0, 0, 0]),
    ("h", [1, 1, 0], [0, 1, 0]),
    ("l", [1, 1, 1], [0, 0, 0]),
])
def test_braille_columns(letter, left, right):
    assert main.columns(letter) == ([bool(x) for x in left], [bool(x) for x in right])


@pytest.mark.parametrize("raw, clean", [
    ("Gate 3", "gate three"),
    ("42!", "four two"),
    ("Hello, World", "hello world"),
    ("!!! ...", ""),
])
def test_normalize(raw, clean):
    assert main.normalize(raw) == clean


def test_buzz_seconds():
    per_letter = 2 * main.PULSE + main.COL_GAP + main.LETTER_GAP
    assert main.buzz_seconds("hi") == pytest.approx(2 * per_letter + main.WORD_GAP)
    assert main.buzz_seconds("Hi, 3") == pytest.approx(main.buzz_seconds("hi three"))
    assert main.buzz_seconds("") == 0


class FakeOut:
    def __init__(self):
        self.pulses = []
        self.closed = False

    def pulse(self, rings, duration):
        self.pulses.append(tuple(rings))

    def close(self):
        self.closed = True


def test_priority_queue_urgent_first(monkeypatch):
    played, gate = [], threading.Event()

    def fake_play(self, rank, kind, text):
        played.append(text or kind)
        gate.wait(2)  # hold the worker on the first item while the rest queue up

    monkeypatch.setattr(main.Haptics, "_play", fake_play)
    h = main.Haptics(FakeOut())
    h.buzz("connected")                # picked up first, blocks the worker
    while not played:
        pass
    h.buzz("sent")
    h.say("normal one")
    h.say("fire", "urgent")
    h.say("normal two")
    gate.set()
    h.wait()
    h.close()
    assert played == ["connected", "fire", "normal one", "normal two", "sent"]


def test_urgent_cuts_into_normal_between_letters(monkeypatch):
    for name in ("PULSE", "COL_GAP", "LETTER_GAP", "WORD_GAP"):
        monkeypatch.setattr(main, name, 0.01)
    out = FakeOut()
    h = main.Haptics(out)
    h.say("a" * 200)
    while len(out.pulses) < 4:
        pass
    h.say("l", "urgent")
    h.wait()
    h.close()
    assert len(out.pulses) < 2 * 200                       # the long message was cut short
    assert out.pulses[-2:] == [(True, True, True), (False, False, False)]  # ...and "l" still played
    assert out.closed


def test_reply_endpoint(monkeypatch):
    class FakeHaptics:
        buzzed = []
        def buzz(self, pattern): self.buzzed.append(pattern)

    fake = FakeHaptics()
    monkeypatch.setattr(app, "haptics", fake)
    client = app.app.test_client()
    r = client.post("/api/reply", json={"morse": ".... .."})
    assert r.get_json() == {"decoded": "HI", "final": "HI", "audio_url": None}
    assert r.headers["Cache-Control"] == "no-store"
    assert client.post("/api/reply", json={"morse": " / "}).get_json()["decoded"] == ""
    assert client.post("/api/reply", json={}).status_code == 400
    assert fake.buzzed == ["sent", "error"]
    assert client.get("/api/state").get_json()["conversation"][-1]["final"] == "HI"


# ---------- AI layer, with the Anthropic client and ElevenLabs mocked ----------
class FakeClaude:
    """Stands in for anthropic.Anthropic(): hands out queued responses, raising any that are exceptions."""
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.messages = self

    def with_options(self, **options):
        return self

    def create(self, **params):
        self.calls.append(params)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeHaptics:
    def __init__(self):
        self.said, self.buzzed = [], []

    def say(self, text, priority="normal"):
        self.said.append((text, priority))

    def buzz(self, pattern):
        self.buzzed.append(pattern)


def text(t):
    return NS(type="text", text=t)


def reply(*blocks, stop="end_turn"):
    return NS(stop_reason=stop, content=list(blocks))


def decision(priority, message, reason="test"):
    return reply(text(json.dumps({"priority": priority, "message": message, "reason": reason})))


REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.fixture
def claude(monkeypatch):
    def install(*replies):
        fake = FakeClaude(replies)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(agent, "_client", fake)
        return fake
    return install


@pytest.fixture
def haptics(monkeypatch):
    fake = FakeHaptics()
    monkeypatch.setattr(app, "haptics", fake)
    return fake


@pytest.mark.parametrize("failure", [
    anthropic.APITimeoutError(request=REQUEST),
    reply(text("not json")),
    reply(stop="refusal"),
    reply(text('{"priority": "loud", "message": "x", "reason": "y"}')),
], ids=["timeout", "invalid-json", "refusal", "bad-priority"])
def test_shorten_falls_back(claude, failure):
    claude(failure)
    info = {}
    assert agent.shorten_incoming("watch out", [], info) == ("normal", "watch out")
    assert info["reason"].startswith("fallback")


def test_shorten_request_and_longer_message_keeps_original(claude):
    fake = claude(decision("urgent", "fire exit now"), decision("normal", "a message longer than the input"))
    assert agent.shorten_incoming("there is a fire please exit now", []) == ("urgent", "fire exit now")
    assert agent.shorten_incoming("hi bob", []) == ("normal", "hi bob")
    sent = fake.calls[0]
    assert sent["model"] == "claude-haiku-5-5"
    assert sent["thinking"] == {"type": "disabled"}
    assert sent["output_config"]["effort"] == "low"
    assert sent["output_config"]["format"]["schema"]["required"] == ["priority", "message", "reason"]


def test_skip_is_not_buzzed(claude, haptics):
    claude(decision("skip", "", "filler"))
    assert app.app.test_client().post("/api/hear", json={"text": "um yeah so"}).get_json() == {"queued": True}
    app.heard.join()  # the same worker queue the mic feeds
    last = app.history()[-1]
    assert (last["priority"], last["buzz_final"], last["reason"]) == ("skip", 0.0, "filler")
    assert haptics.said == []


def test_fix_outgoing(claude):
    claude(reply(text("Yes, three works for me.")), anthropic.APIConnectionError(request=REQUEST))
    assert agent.fix_outgoing("Y 3", [{"who": "them", "raw": "can we meet at 3", "final": "meet at three"}]) \
        == "Yes, three works for me."
    assert agent.fix_outgoing("Y 3", []) == "Y 3"


def test_ask_buzzes_answer_and_speaks_nothing(claude, haptics, monkeypatch):
    monkeypatch.setattr(tts, "speak", lambda text: pytest.fail("ask must not speak"))
    fake = claude(
        reply(text("Let me search."), NS(type="server_tool_use", input={"query": "weather ypsilanti"}),
              stop="pause_turn"),
        reply(NS(type="web_search_tool_result", content=[]), text("fifty degrees "), text("and sunny")),
    )
    r = app.app.test_client().post("/api/reply", json={"morse": ".-- . .- - .... . .-.", "ask": True})
    assert r.get_json() == {"decoded": "WEATHER", "final": "", "answer": "fifty degrees and sunny"}
    assert haptics.said == [("fifty degrees and sunny", "normal")]
    assert haptics.buzzed == ["sent"]
    last = app.history()[-1]
    assert (last["who"], last["raw"], last["search"]) == ("assistant", "WEATHER", "weather ypsilanti")
    assert fake.calls[0]["tools"][0]["type"] == "web_search_20250305"
    assert "thinking" not in fake.calls[0]
    assert fake.calls[1]["messages"][-1]["role"] == "assistant"  # pause_turn sent back once


def test_missing_keys_use_stubs(haptics, monkeypatch):
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: pytest.fail("no key: must not build a client"))
    monkeypatch.setattr(tts.urllib.request, "urlopen", lambda *a, **k: pytest.fail("no key: no network"))
    assert agent.shorten_incoming("hello there", []) == ("normal", "hello there")
    assert agent.fix_outgoing("HI", []) == "HI"
    assert agent.answer("WEATHER", []) == ""
    assert tts.speak("hello") is None
    client = app.app.test_client()
    assert client.post("/api/reply", json={"morse": ".... ..", "ask": True}).get_json()["answer"] == ""
    assert haptics.buzzed == ["sent", "error"]


def test_tts_elevenlabs(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice1")
    sent = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *exc): pass
        def read(self): return b"ID3mp3"

    def ok(req, timeout):
        sent.append((req, timeout))
        return Response()

    monkeypatch.setattr(tts.urllib.request, "urlopen", ok)
    assert tts.speak("hello") == b"ID3mp3"
    req, timeout = sent[0]
    assert req.full_url.startswith("https://api.elevenlabs.io/v1/text-to-speech/voice1?output_format=mp3")
    assert req.get_header("Xi-api-key") == "test-key" and timeout == 5
    assert json.loads(req.data) == {"text": "hello", "model_id": tts.MODEL}

    def down(req, timeout):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(tts.urllib.request, "urlopen", down)
    assert tts.speak("hello") is None


# ---------- Stand-in mics: Pi mic first, then the laptop, then the phone ----------

@pytest.fixture
def mics(monkeypatch):
    """Fresh mic state for each test; returns the Flask test client."""
    monkeypatch.setitem(app.state, "listening", False)
    monkeypatch.setitem(app.state, "muted", False)
    monkeypatch.setitem(app.state, "mic_seen", {"mac": None, "phone": None})
    return app.app.test_client()


def test_active_mic_priority(mics):
    assert mics.get("/api/state").get_json()["mic"] is None
    assert mics.get("/api/state?from=phone&mic=1").get_json()["mic"] == "phone"
    assert mics.get("/api/state?from=mac").get_json()["mic"] == "phone"  # no mic=1: the laptop's mic isn't open
    assert mics.get("/api/state?from=mac&mic=1").get_json()["mic"] == "mac"
    app.state["listening"] = True
    assert mics.get("/api/state").get_json()["mic"] == "pi"
    assert "mic_seen" not in mics.get("/api/state").get_json()


def test_active_mic_expires(mics, monkeypatch):
    mics.get("/api/state?from=mac&mic=1")
    now = app.time.time()
    monkeypatch.setattr(app.time, "time", lambda: now + app.MIC_TIMEOUT + 1)
    assert app.active_mic() is None


def test_hear_drops_a_lower_priority_mic(mics, haptics):
    app.state["listening"] = True  # the Pi has its own mic
    assert mics.post("/api/hear", json={"text": "hello", "source": "mac"}).get_json()["queued"] is False
    assert mics.post("/api/hear", json={"text": "hello", "source": "phone"}).get_json()["queued"] is False
    assert mics.post("/api/hear", json={"text": "hello"}).get_json() == {"queued": True}  # typed always works
    app.state["listening"] = False
    mics.get("/api/state?from=mac&mic=1")
    assert mics.post("/api/hear", json={"text": "hi", "source": "phone"}).get_json()["queued"] is False
    assert mics.post("/api/hear", json={"text": "hi", "source": "mac"}).get_json() == {"queued": True}
    assert mics.post("/api/hear", json={"text": "hi", "source": "bluetooth"}).status_code == 400
    app.heard.join()


def test_hear_drops_mics_while_the_phone_speaks(mics, haptics):
    app.state["muted"] = True
    r = mics.post("/api/hear", json={"text": "yes three works for me", "source": "mac"}).get_json()
    assert r == {"queued": False, "why": "the phone is speaking"}
    assert mics.post("/api/hear", json={"text": "typed still works"}).get_json() == {"queued": True}
    app.heard.join()


def test_mac_mic_hold_reason():
    import importlib.util
    spec = importlib.util.spec_from_file_location("mac_mic", main.ROOT / "scripts" / "mac_mic.py")
    mac_mic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mac_mic)
    assert mac_mic.hold_reason(None) == "can't reach the Pi"
    assert mac_mic.hold_reason({"listening": True, "muted": False}) == "the Pi has its own mic"
    assert mac_mic.hold_reason({"listening": False, "muted": True}) == "the phone is speaking"
    assert mac_mic.hold_reason({"listening": False, "muted": False}) is None
