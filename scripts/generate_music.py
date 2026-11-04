"""Make a 60 s music bed for an episode with Stable Audio Open Small.

The model tops out at ~11 s, so one clip is generated from the story's music_prompt
and looped with equal-power crossfades to the target length.

    python scripts/generate_music.py --story S0001 --out out/music
    python scripts/generate_music.py --prompt "comedic pizzicato strings" --out out/music

Needs HF_TOKEN for an account that accepted the model's terms on Hugging Face.
"""
import argparse
import sys
import time
import wave
from pathlib import Path

from common import read_csv

MODEL_ID = "stabilityai/stable-audio-open-small"
STYLE = "gentle cartoon underscore, loopable, no vocals"


def generate_clip(prompt: str, seconds: float, steps: int, seed: int):
    import torch
    from stable_audio_tools import get_pretrained_model
    from stable_audio_tools.inference.generation import generate_diffusion_cond

    model, cfg = get_pretrained_model(MODEL_ID)
    out = generate_diffusion_cond(
        model, steps=steps, cfg_scale=1.0,
        conditioning=[{"prompt": prompt, "seconds_total": seconds}],
        sample_size=cfg["sample_size"], sampler_type="pingpong", device="cpu", seed=seed,
    )
    rate = cfg["sample_rate"]
    clip = out[0].to(torch.float32)[:, : int(seconds * rate)]  # (channels, samples)
    return clip / clip.abs().max().clamp(min=1e-6), rate


def loop_to(clip, rate: int, total: float, crossfade: float = 1.5, fade_out: float = 2.0):
    import torch

    cf = int(crossfade * rate)
    t = torch.linspace(0, 1, cf)
    fade_in, fade_away = torch.sin(t * torch.pi / 2), torch.cos(t * torch.pi / 2)
    bed = clip
    while bed.shape[1] < total * rate:
        bed = torch.cat([bed[:, :-cf], bed[:, -cf:] * fade_away + clip[:, :cf] * fade_in, clip[:, cf:]], dim=1)
    bed = bed[:, : int(total * rate)].clone()
    n = int(fade_out * rate)
    bed[:, -n:] *= torch.linspace(1, 0, n)
    return bed


def write_wav(path: Path, audio, rate: int) -> None:
    pcm = (audio.clamp(-1, 1) * 32767).round().short().T.contiguous()  # (samples, channels)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(pcm.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.numpy().tobytes())


def main() -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--story", help="story_id whose music_prompt to use")
    src.add_argument("--prompt")
    ap.add_argument("--out", default="out/music")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--clip-seconds", type=float, default=10)
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    name = args.story or "custom"
    prompt = args.prompt
    if args.story:
        story = next((s for s in read_csv("stories.csv") if s["story_id"] == args.story), None)
        if story is None:
            print(f"no story {args.story} in stories.csv", file=sys.stderr)
            return 1
        prompt = story["music_prompt"]
    prompt = f"{prompt}, {STYLE}"

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    clip, rate = generate_clip(prompt, args.clip_seconds, args.steps, args.seed)
    gen = time.monotonic() - start
    write_wav(out / f"{name}_clip.wav", clip, rate)
    write_wav(out / f"{name}_music.wav", loop_to(clip, rate, args.seconds), rate)

    print(f"| story | prompt | clip | generate time | bed |\n|---|---|---|---|---|")
    print(f"| {name} | {prompt} | {args.clip_seconds:.0f}s @ {rate} Hz, {args.steps} steps | {gen:.0f}s | {args.seconds:.0f}s |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
