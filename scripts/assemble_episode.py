"""Assemble finished vertical episodes from rendered clips, voice lines and music (FFmpeg only).

    python scripts/assemble_episode.py --stories S0001,S0002,S0003 \
        --clips out/clips --voice out/voice --music out/music --out out/episodes

Per episode: 12 clips scaled/cropped to fill 9:16 and cut to exactly 5 s each, dialogue burned in as captions
above the TikTok/Shorts UI, each voice line placed at its scene, music under it, loudness normalised.
A missing clip becomes a black 5 s gap (and is reported), so a partial render still assembles.
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from common import SCENE_SECONDS, SCENES_PER_EPISODE, read_csv

FPS = 24
MUSIC_VOLUME = 0.22
VOICE_DELAY = 0.3   # seconds into the scene before a line starts


def _ts(seconds: float) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def captions(scenes: list[dict], w: int, h: int) -> str:
    size = round(w / 13)
    head = ("[Script Info]\nScriptType: v4.00+\n"
            f"PlayResX: {w}\nPlayResY: {h}\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
            f"Style: Default,DejaVu Sans,{size},&H00FFFFFF,&H00000000,&H64000000,1,1,{max(2, size // 14)},1,2,"
            f"{w // 12},{w // 12},{round(h * 0.22)}\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Text\n")
    events = []
    for s in scenes:
        if s["dialogue"].strip():
            start = (int(s["scene_id"]) - 1) * SCENE_SECONDS
            text = s["dialogue"].replace("\n", " ").replace("{", "(").replace("}", ")")
            events.append(f"Dialogue: 0,{_ts(start + VOICE_DELAY)},{_ts(start + SCENE_SECONDS - 0.15)},Default,{text}")
    return head + "\n".join(events) + "\n"


def run(cmd: list[str], cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        sys.stderr.write(r.stderr[-3000:])
        raise SystemExit(f"ffmpeg failed: {' '.join(cmd[:6])} ...")


def assemble(sid: str, scenes: list[dict], clips: Path, voice: Path, music: Path, out: Path, w: int, h: int) -> list[str]:
    problems = []
    total = SCENES_PER_EPISODE * SCENE_SECONDS
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "subs.ass").write_text(captions(scenes, w, h), encoding="utf-8")

        inputs, parts = [], []
        for i in range(1, SCENES_PER_EPISODE + 1):
            clip = clips / f"{sid}_{i:02d}.mp4"
            if clip.exists():
                inputs += ["-i", str(clip.resolve())]
            else:
                problems.append(f"scene {i}: no clip, using black")
                inputs += ["-f", "lavfi", "-t", str(SCENE_SECONDS), "-i", f"color=c=black:s={w}x{h}:r={FPS}"]
            parts.append(f"[{i - 1}:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                         f"fps={FPS},trim=duration={SCENE_SECONDS},setpts=PTS-STARTPTS,setsar=1[v{i}]")
        graph = ";".join(parts) + ";" + "".join(f"[v{i}]" for i in range(1, SCENES_PER_EPISODE + 1)) + \
            f"concat=n={SCENES_PER_EPISODE}:v=1:a=0[cat];[cat]subtitles=subs.ass[v]"
        run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "[v]",
             "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "video.mp4"], tmp)

        a_inputs, a_parts, labels = [], [], []
        n = 0
        bed = music / f"{sid}_music.wav"
        if bed.exists():
            a_inputs += ["-i", str(bed.resolve())]
            a_parts.append(f"[{n}:a]atrim=0:{total},volume={MUSIC_VOLUME}[m]")
            labels.append("[m]")
            n += 1
        else:
            problems.append("no music bed")
        for s in scenes:
            line = voice / f"{sid}_{int(s['scene_id']):02d}.wav"
            if not s["dialogue"].strip():
                continue
            if not line.exists():
                problems.append(f"scene {s['scene_id']}: no voice line")
                continue
            delay = int(((int(s["scene_id"]) - 1) * SCENE_SECONDS + VOICE_DELAY) * 1000)
            a_inputs += ["-i", str(line.resolve())]
            a_parts.append(f"[{n}:a]aresample=48000,adelay={delay}|{delay}[d{n}]")
            labels.append(f"[d{n}]")
            n += 1
        if labels:
            graph = ";".join(a_parts) + ";" + "".join(labels) + \
                f"amix=inputs={len(labels)}:normalize=0:duration=longest,apad,atrim=0:{total}," \
                f"loudnorm=I=-14:TP=-1.5:LRA=11[a]"
            run(["ffmpeg", "-y", "-loglevel", "error", *a_inputs, "-filter_complex", graph, "-map", "[a]",
                 "-ar", "48000", "-ac", "2", "audio.wav"], tmp)
        else:
            run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-t", str(total), "-i",
                 "anullsrc=r=48000:cl=stereo", "audio.wav"], tmp)

        out.mkdir(parents=True, exist_ok=True)
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", "video.mp4", "-i", "audio.wav", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
             str((out / f"{sid}.mp4").resolve())], tmp)
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stories", required=True)
    ap.add_argument("--clips", default="out/clips")
    ap.add_argument("--voice", default="out/voice")
    ap.add_argument("--music", default="out/music")
    ap.add_argument("--out", default="out/episodes")
    ap.add_argument("--size", default="720x1280")
    args = ap.parse_args()
    w, h = map(int, args.size.split("x"))

    all_scenes = read_csv("scenes.csv")
    for sid in args.stories.split(","):
        scenes = sorted((s for s in all_scenes if s["story_id"] == sid), key=lambda s: int(s["scene_id"]))
        if not scenes:
            print(f"{sid}: no scenes in scenes.csv", file=sys.stderr)
            return 1
        problems = assemble(sid, scenes, Path(args.clips), Path(args.voice), Path(args.music), Path(args.out), w, h)
        print(f"{sid}: {Path(args.out) / (sid + '.mp4')}" + (f"  ({'; '.join(problems)})" if problems else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
