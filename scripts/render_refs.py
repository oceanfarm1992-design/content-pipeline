"""Render one reference sheet per character with Anima-Turbo via sd-cli.

    python scripts/render_refs.py --sd-cli bin/sd-cli [--only C001] [--size 768x768]

Writes each image to the character's reference_sheet path and prints a timing table.
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from common import read_csv

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models" / "anima"
STYLE = "anime style, clean line art, soft cel shading, character reference sheet, full body, plain light grey background"
NEGATIVE = "blurry, lowres, bad anatomy, extra limbs, text, watermark, signature"


def prompt_for(c: dict) -> str:
    keep = c["never_change"].replace(";", ", ")
    return f"{STYLE}, {c['description']}, {keep}"


def render(sd_cli: str, prompt: str, out: Path, w: int, h: int, steps: int, seed: int) -> float:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sd_cli, "-v", "-t", str(os.cpu_count() or 4),
        "--diffusion-model", str(MODELS / "dit-turbo-q4_k_m.gguf"),
        "--llm", str(MODELS / "qwen_3_06b_base.safetensors"),
        "--vae", str(MODELS / "qwen_image_vae.safetensors"),
        "-p", prompt, "-n", NEGATIVE,
        "--cfg-scale", "1.0", "--sampling-method", "euler", "--steps", str(steps),
        "-W", str(w), "-H", str(h), "--seed", str(seed), "-o", str(out),
    ]
    start = time.monotonic()
    subprocess.run(cmd, check=True)
    return time.monotonic() - start


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sd-cli", default="bin/sd-cli")
    ap.add_argument("--only", help="comma-separated character_ids")
    ap.add_argument("--size", default="768x768")
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    w, h = map(int, args.size.lower().split("x"))

    chars = read_csv("characters.csv")
    if args.only:
        wanted = set(args.only.split(","))
        chars = [c for c in chars if c["character_id"] in wanted]

    rows = []
    for c in chars:
        out = ROOT / c["reference_sheet"]
        secs = render(args.sd_cli, prompt_for(c), out, w, h, args.steps, args.seed)
        rows.append((c["character_id"], c["name"], out.relative_to(ROOT).as_posix(), secs))
        print(f"{c['character_id']} {c['name']}: {secs:.0f}s -> {out}", flush=True)

    print(f"\n| id | name | file | {w}x{h}, {args.steps} steps |\n|---|---|---|---|")
    for cid, name, path, secs in rows:
        print(f"| {cid} | {name} | {path} | {secs / 60:.1f} min |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
