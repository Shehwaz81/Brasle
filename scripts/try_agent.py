"""Run canned sentences through the REAL Claude API and print what the glove would buzz.

  .venv/bin/python scripts/try_agent.py

Needs ANTHROPIC_API_KEY in .env. Every number printed comes from live model output.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main as core  # loads .env
import agent

if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit("ANTHROPIC_API_KEY is not set in .env: everything would just fall back.")
NAME = os.environ.setdefault("BRASLE_USER_NAME", "alex")

HEARD = [
    ("filler", "um yeah so like you know"),
    ("urgent, by name", f"hey {NAME} watch out there's a car backing up right behind you"),
    ("long directions", "okay so to get to the train station you go straight down this road for about two blocks "
                        "then you turn left at the big church and it's going to be on your right side"),
    ("negation", "please do not take the elevator it is not working today"),
    ("time", "the meeting got moved from two thirty to four fifteen this afternoon"),
    ("vosk garbage", "interaction and than you guys"),
    ("question", "do you want to grab some coffee with us after the talk is over"),
    ("alarm", "that's the fire alarm everyone needs to leave the building right now"),
    ("small talk", "nice weather we're having today huh"),
    ("name and number", "hi my name is maria and my room number is four oh seven"),
    ("vosk garbage", "the the uh of and"),
    ("polite request", "excuse me would you mind if i sat in this seat next to you or is somebody sitting here"),
]


def main():
    print(f"Model {agent.model()}, user name {NAME!r}\n")
    history, total_raw, total_final, latencies = [], 0.0, 0.0, []
    for label, text in HEARD:
        info = {}
        priority, message = agent.shorten_incoming(text, history, info)
        buzz_raw = core.buzz_seconds(text)
        buzz_final = 0.0 if priority == "skip" else core.buzz_seconds(message)
        total_raw, total_final = total_raw + buzz_raw, total_final + buzz_final
        latencies.append(info["ms"])
        history.append({"who": "them", "raw": text, "final": message, "ts": time.time()})
        print(f"[{label}] {priority.upper()}  {info['ms']} ms  saved {buzz_raw - buzz_final:.1f} s "
              f"({buzz_raw:.1f} -> {buzz_final:.1f})  reason: {info['reason']}")
        print(f"   raw:   {text}")
        print(f"   final: {message if priority != 'skip' else '(skipped, no buzz)'}\n")
    latencies.sort()
    print(f"TOTAL braille time {total_raw:.1f} s raw -> {total_final:.1f} s sent: "
          f"saved {total_raw - total_final:.1f} s ({100 * (1 - total_final / total_raw):.0f}%)")
    print(f"Claude latency: median {latencies[len(latencies) // 2]} ms, max {latencies[-1]} ms")


if __name__ == "__main__":
    main()
