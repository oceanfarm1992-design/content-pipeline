"""Render every dialogue line of the given episodes with each speaker's Kokoro voice.

    python scripts/render_voice.py --stories S0001,S0002 --url http://localhost:8880 --out out/voice

Writes <story_id>_<scene>.wav for each scene that has dialogue.
"""
import argparse
import sys
import time
from pathlib import Path

from common import read_csv
from voice_smoke import get_json, speak


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stories", required=True)
    ap.add_argument("--url", default="http://localhost:8880")
    ap.add_argument("--out", default="out/voice")
    args = ap.parse_args()
    wanted = set(args.stories.split(","))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    available = {v["id"] if isinstance(v, dict) else v for v in get_json(f"{args.url}/v1/audio/voices")["voices"]}
    chars = {c["character_id"]: c for c in read_csv("characters.csv")}
    lines = [s for s in read_csv("scenes.csv") if s["story_id"] in wanted and s["dialogue"].strip()]
    missing = 0
    start = time.monotonic()
    for s in lines:
        voice = chars.get(s["speaker"], {}).get("voice_id", "")
        if voice not in available:
            print(f"{s['story_id']} scene {s['scene_id']}: speaker {s['speaker']} has no usable voice '{voice}'",
                  file=sys.stderr)
            missing += 1
            continue
        (out / f"{s['story_id']}_{int(s['scene_id']):02d}.wav").write_bytes(speak(args.url, s["dialogue"], voice))
    print(f"{len(lines) - missing}/{len(lines)} lines in {time.monotonic() - start:.0f}s -> {out}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
