"""Check a running Kokoro-FastAPI server has every character's voice, and render one line each.

    python scripts/voice_smoke.py --url http://localhost:8880 --out out/voice
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

from common import read_csv

SAMPLE_LINES = {
    "C001": "That box is mine. I saw it first.",
    "C002": "You saw it first. I sat in it first.",
    "C003": "Okay, okay. I'll get another box.",
}


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def speak(base: str, text: str, voice: str) -> bytes:
    body = json.dumps({"model": "kokoro", "input": text, "voice": voice,
                       "response_format": "wav", "speed": 1.0}).encode()
    req = urllib.request.Request(f"{base}/v1/audio/speech", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8880")
    ap.add_argument("--out", default="out/voice")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    voices = get_json(f"{args.url}/v1/audio/voices")["voices"]
    available = {v["id"] if isinstance(v, dict) else v for v in voices}
    print(f"{len(available)} voices available")

    missing = 0
    print("| id | name | voice | text | render time | file |\n|---|---|---|---|---|---|")
    for c in read_csv("characters.csv"):
        voice = c["voice_id"]
        if voice not in available:
            missing += 1
            print(f"| {c['character_id']} | {c['name']} | {voice} | MISSING | | |")
            continue
        text = SAMPLE_LINES.get(c["character_id"], f"Hello, I am {c['name']}.")
        start = time.monotonic()
        audio = speak(args.url, text, voice)
        secs = time.monotonic() - start
        path = out / f"{c['character_id']}_{voice}.wav"
        path.write_bytes(audio)
        print(f"| {c['character_id']} | {c['name']} | {voice} | {text} | {secs:.1f}s | {path.name} |")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
