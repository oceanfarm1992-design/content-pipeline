"""Wan 2.1 T2V 1.3B video clips on a Modal GPU (the paid-by-credit half of the hybrid pipeline).

    modal run scripts/modal_video.py --gpu L4 --steps 25 --out out/modal
    modal run scripts/modal_video.py::download            # fetch weights into the Volume (CPU only)

Weights live in the Modal Volume "box-debates-models", downloaded by a CPU-only function so no
GPU time is spent on downloads. Each run returns the mp4 plus a timing/cost report.
Needs MODAL_TOKEN_ID / MODAL_TOKEN_SECRET in the environment (GitHub secrets in CI).
"""
import json
import time
from pathlib import Path

import modal

MODEL_ID = "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"
HF_HOME = "/models/hf"
# Approximate Modal GPU rates, $/second (GPU only; CPU and memory add a little). Check modal.com/pricing.
RATES = {"L4": 0.000222, "L40S": 0.000542}

app = modal.App("box-debates-video")
volume = modal.Volume.from_name("box-debates-models", create_if_missing=True)
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install(
        "torch==2.6.0", "diffusers==0.33.1", "transformers>=4.49,<5", "accelerate>=1.4", "ftfy",
        "sentencepiece", "imageio", "imageio-ffmpeg", "huggingface_hub>=0.30",
    )
    .env({"HF_HOME": HF_HOME})
)


@app.function(image=image, volumes={"/models": volume}, timeout=3600)
def download() -> str:
    from huggingface_hub import snapshot_download

    path = snapshot_download(MODEL_ID)
    volume.commit()
    return path


def _generate(prompt: str, negative: str, width: int, height: int, frames: int, steps: int,
              guidance: float, seed: int) -> dict:
    import torch
    from diffusers import AutoencoderKLWan, UniPCMultistepScheduler, WanPipeline
    from diffusers.utils import export_to_video

    t0 = time.monotonic()
    vae = AutoencoderKLWan.from_pretrained(MODEL_ID, subfolder="vae", torch_dtype=torch.float32)
    pipe = WanPipeline.from_pretrained(MODEL_ID, vae=vae, torch_dtype=torch.bfloat16)
    pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config, flow_shift=3.0)  # 3.0 for 480p
    pipe.to("cuda")
    load_s = time.monotonic() - t0

    t1 = time.monotonic()
    result = pipe(prompt=prompt, negative_prompt=negative, width=width, height=height, num_frames=frames,
                  num_inference_steps=steps, guidance_scale=guidance,
                  generator=torch.Generator("cuda").manual_seed(seed))
    gen_s = time.monotonic() - t1

    out = "/tmp/clip.mp4"
    export_to_video(result.frames[0], out, fps=16)
    return {"mp4": Path(out).read_bytes(), "load_s": load_s, "gen_s": gen_s,
            "gpu_name": torch.cuda.get_device_name(0),
            "peak_vram_gb": torch.cuda.max_memory_allocated() / 2**30}


GPU_KW = dict(image=image, volumes={"/models": volume}, timeout=3600)


@app.function(gpu="L4", **GPU_KW)
def clip_l4(settings: dict) -> dict:
    return _generate(**settings)


@app.function(gpu="L40S", **GPU_KW)
def clip_l40s(settings: dict) -> dict:
    return _generate(**settings)


CLIP_FUNCTIONS = {"L4": clip_l4, "L40S": clip_l40s}


@app.local_entrypoint()
def main(gpu: str = "L4", steps: int = 25, width: int = 832, height: int = 480, frames: int = 81,
         guidance: float = 5.0, seed: int = 42, out: str = "out/modal",
         prompt: str = "photorealistic cinematic shot, a chubby orange tabby house cat with a notched left ear "
                       "stares suspiciously at a cardboard box on a wooden living-room floor, warm afternoon "
                       "window light, shallow depth of field, 50mm lens",
         negative: str = "cartoon, anime, drawing, blurry, low quality, deformed, extra legs, static, text, watermark"):
    print("Ensuring weights are in the Volume (CPU only)...")
    download.remote()

    wall = time.monotonic()
    r = CLIP_FUNCTIONS[gpu].remote(dict(prompt=prompt, negative=negative, width=width, height=height,
                                        frames=frames, steps=steps, guidance=guidance, seed=seed))
    wall_s = time.monotonic() - wall

    rate = RATES[gpu]
    clip_cost = (r["load_s"] + r["gen_s"]) * rate
    # One container per episode: load once, then 12 clips back to back.
    episode_s = r["load_s"] + 12 * r["gen_s"]
    report = {
        "gpu": gpu, "gpu_name": r["gpu_name"], "size": f"{width}x{height}", "frames": frames, "steps": steps,
        "load_s": round(r["load_s"], 1), "gen_s": round(r["gen_s"], 1), "wall_s_incl_cold_start": round(wall_s, 1),
        "peak_vram_gb": round(r["peak_vram_gb"], 1),
        "clip_cost_usd": round(clip_cost, 3),
        "episode_gpu_minutes": round(episode_s / 60, 1),
        "episode_cost_usd": round(episode_s * rate, 2),
        "month_cost_usd_30_episodes": round(30 * episode_s * rate, 2),
    }
    dest = Path(out)
    dest.mkdir(parents=True, exist_ok=True)
    name = f"wan13b_{gpu}_{width}x{height}_{frames}f_{steps}s"
    (dest / f"{name}.mp4").write_bytes(r["mp4"])
    (dest / f"{name}.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
