"""Render one photoreal reference sheet per character with RealVisXL V5.0 Lightning via sd-cli.

    python scripts/render_refs.py --sd-cli bin/sd-cli [--only C001] [--missing] [--size 832x1216]

Writes each image to the character's reference_sheet path and prints a timing table.
--missing skips characters whose sheet already exists (new cast from the story writer).
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from common import read_csv

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models" / "realvis"
STYLE = ("photorealistic, professional studio photograph, 35mm lens, soft natural light, sharp focus, "
         "single subject, (full body shot, entire body in frame:1.3), plain light grey seamless background")
# The model card's recommended negative, plus our own.
NEGATIVE = ("(octane render, render, drawing, anime, cartoon, bad photo, bad photography:1.3), "
            "(worst quality, low quality, blurry:1.2), (bad anatomy, bad proportions:1.1), "
            "(deformed eyes, deformed iris, deformed pupils), deformed hands, fused fingers, "
            "(close-up, portrait crop, cropped body:1.2), multiple views, text, watermark, signature, nsfw")


def prompt_for(c: dict) -> str:
    keep = c["never_change"].replace(";", ", ")
    return f"{STYLE}, {c['species']}, {c['description']}, {keep}"


def negative_for(c: dict) -> str:
    return ", ".join(p for p in (NEGATIVE, c.get("negative", "").strip()) if p)


def render(sd_cli: str, prompt: str, negative: str, out: Path, w: int, h: int, steps: int, cfg: float,
           seed: int) -> float:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sd_cli, "-v", "-t", str(os.cpu_count() or 4),
        "-m", str(MODELS / "realvisxl-v5-lightning-q8_0.gguf"),
        "--vae", str(MODELS / "sdxl_vae_fp16_fix.safetensors"),
        "-p", prompt, "-n", negative,
        "--cfg-scale", str(cfg), "--sampling-method", "dpm++2m", "--scheduler", "karras", "--steps", str(steps),
        "-W", str(w), "-H", str(h), "--seed", str(seed), "-o", str(out),
    ]
    start = time.monotonic()
    subprocess.run(cmd, check=True)
    return time.monotonic() - start


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sd-cli", default="bin/sd-cli")
    ap.add_argument("--only", help="comma-separated character_ids")
    ap.add_argument("--missing", action="store_true", help="only characters without a reference sheet yet")
    ap.add_argument("--size", default="832x1216")
    ap.add_argument("--steps", type=int, default=6)
    ap.add_argument("--cfg", type=float, default=1.5, help="model card: 1.0-2.0; above 1.0 doubles compute")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    w, h = map(int, args.size.lower().split("x"))

    chars = read_csv("characters.csv")
    if args.only:
        wanted = set(args.only.split(","))
        chars = [c for c in chars if c["character_id"] in wanted]
    if args.missing:
        chars = [c for c in chars if not (ROOT / c["reference_sheet"]).exists()]
    if not chars:
        print("No characters to render.")
        return 0

    rows = []
    for c in chars:
        out = ROOT / c["reference_sheet"]
        secs = render(args.sd_cli, prompt_for(c), negative_for(c), out, w, h, args.steps, args.cfg, args.seed)
        rows.append((c["character_id"], c["name"], out.relative_to(ROOT).as_posix(), secs))
        print(f"{c['character_id']} {c['name']}: {secs:.0f}s -> {out}", flush=True)

    print(f"\n| id | name | file | {w}x{h}, {args.steps} steps, cfg {args.cfg} |\n|---|---|---|---|")
    for cid, name, path, secs in rows:
        print(f"| {cid} | {name} | {path} | {secs / 60:.1f} min |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
