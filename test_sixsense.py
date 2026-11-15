"""No-hardware tests: python -m pytest -q"""
import io
import json
import threading

import pytest

import agent
import app
import main
import tts


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
    for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY"):  # a real .env must not send this test to the network
        monkeypatch.delenv(key, raising=False)
    client = app.app.test_client()
    r = client.post("/api/reply", json={"morse": ".... .."})
    assert r.get_json() == {"decoded": "HI", "final": "HI", "audio_url": None}
    assert r.headers["Cache-Control"] == "no-store"
    assert client.post("/api/reply", json={"morse": " / "}).get_json()["decoded"] == ""
    assert client.post("/api/reply", json={}).status_code == 400
    assert fake.buzzed == ["sent", "error"]
    assert client.get("/api/state").get_json()["conversation"][-1]["final"] == "HI"


def offline(req, timeout):
    raise OSError("offline")


def test_fix_outgoing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert agent.fix_outgoing("HELO", []) == "HELO"  # no key: no network, text unchanged

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    sent = []
    def chatgpt(req, timeout):
        sent.append(json.loads(req.data))
        return io.BytesIO(json.dumps({"output": [
            {"type": "reasoning"},
            {"type": "message", "content": [{"type": "output_text", "text": " Hello. "}]}]}).encode())
    monkeypatch.setattr(agent.urllib.request, "urlopen", chatgpt)
    assert agent.fix_outgoing("HELO", [{"who": "them", "final": "HI"}], ".... . .-.. ---") == "Hello."
    assert "them: HI" in sent[0]["input"] and ".... . .-.. ---" in sent[0]["input"]

    monkeypatch.setattr(agent.urllib.request, "urlopen", lambda req, timeout: io.BytesIO(json.dumps(
        {"output": [{"type": "message", "content": [{"type": "output_text", "text": "x" * 500}]}]}).encode()))
    assert agent.fix_outgoing("HELO", []) == "HELO"  # a runaway answer is not what was tapped

    monkeypatch.setattr(agent.urllib.request, "urlopen", offline)
    assert agent.fix_outgoing("HELO", []) == "HELO"


def test_speak(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert tts.speak("Hello.") is None  # no key: the phone uses its own voice

    monkeypatch.setenv("ELEVENLABS_API_KEY", "test")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice 1")
    sent = []
    def elevenlabs(req, timeout):
        sent.append(req)
        return io.BytesIO(b"mp3 bytes")
    monkeypatch.setattr(tts.urllib.request, "urlopen", elevenlabs)
    assert tts.speak("Hello.") == b"mp3 bytes"
    assert sent[0].full_url.endswith("/text-to-speech/voice%201")
    assert json.loads(sent[0].data)["text"] == "Hello."

    monkeypatch.setattr(tts.urllib.request, "urlopen", offline)
    assert tts.speak("Hello.") is None
