"""Quality gate for series/*.csv. Exits non-zero on any error."""
import sys
from collections import Counter

from common import (MAX_DIALOGUE_WORDS, MOVEMENTS, REGISTERS, ROLES, SCENE_SECONDS, SCENES_PER_EPISODE,
                    SHOT_SIZES, STATUSES, TRANSITIONS, VOICES, read_csv, story_id)


def check_character(c: dict) -> list[str]:
    """Errors for one characters.csv row."""
    cid = c["character_id"]
    errors = []
    for col in ("name", "species", "description", "never_change"):
        if not str(c[col]).strip():
            errors.append(f"{cid}: empty {col}")
    if c["role"] not in ROLES:
        errors.append(f"{cid}: bad role '{c['role']}'")
    if c["voice_id"] not in VOICES:
        errors.append(f"{cid}: unknown Kokoro voice '{c['voice_id']}'")
    if not (len(c["first_episode"]) == 5 and c["first_episode"].startswith("S") and c["first_episode"][1:].isdigit()):
        errors.append(f"{cid}: bad first_episode '{c['first_episode']}'")
    if c["reference_sheet"] != f"series/refs/{cid}.png":
        errors.append(f"{cid}: reference_sheet must be series/refs/{cid}.png")
    return errors


def check_episode(s: dict, scenes: list[dict], chars: set[str]) -> list[str]:
    """Errors for one story row and its scene rows."""
    sid = s["story_id"]
    errors = []
    if not str(s["episode"]).isdigit() or story_id(int(s["episode"])) != sid:
        errors.append(f"{sid}: episode '{s['episode']}' does not match story_id")
    if s["status"] not in STATUSES:
        errors.append(f"{sid}: bad status '{s['status']}'")
    if s["emotional_register"] not in REGISTERS:
        errors.append(f"{sid}: bad emotional_register '{s['emotional_register']}'")
    for col in ("title", "life_lesson", "hook", "synopsis", "music_prompt"):
        if not str(s[col]).strip():
            errors.append(f"{sid}: empty {col}")
    used = [c for c in s["characters_used"].split(";") if c]
    if not used or any(c not in chars for c in used):
        errors.append(f"{sid}: bad characters_used '{s['characters_used']}'")

    nums = sorted(int(r["scene_id"]) for r in scenes if str(r["scene_id"]).isdigit())
    if nums != list(range(1, SCENES_PER_EPISODE + 1)):
        errors.append(f"{sid}: scenes must be 1..{SCENES_PER_EPISODE}, got {nums}")

    for sc in scenes:
        tag = f"{sid} scene {sc['scene_id']}"
        if str(sc["duration_s"]) != str(SCENE_SECONDS):
            errors.append(f"{tag}: duration_s must be {SCENE_SECONDS}")
        words = len(sc["dialogue"].split())
        if words > MAX_DIALOGUE_WORDS:
            errors.append(f"{tag}: dialogue has {words} words (max {MAX_DIALOGUE_WORDS})")
        if sc["dialogue"].strip() and sc["speaker"] not in chars:
            errors.append(f"{tag}: dialogue needs a valid speaker, got '{sc['speaker']}'")
        if any(c not in chars for c in sc["characters"].split(";") if c):
            errors.append(f"{tag}: bad characters '{sc['characters']}'")
        if sc["transition"] not in TRANSITIONS:
            errors.append(f"{tag}: bad transition '{sc['transition']}'")
        if not sc["action"].strip():
            errors.append(f"{tag}: empty action")
    return errors


def validate() -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    cast = read_csv("characters.csv")
    chars = {c["character_id"] for c in cast}
    debut = {c["character_id"]: c["first_episode"] for c in cast}
    for cid, n in Counter(c["character_id"] for c in cast).items():
        if n > 1:
            errors.append(f"{cid}: duplicate character_id")
    for c in cast:
        errors += check_character(c)
    stories = read_csv("stories.csv")
    callbacks = read_csv("callbacks.csv")

    by_story: dict[str, list[dict]] = {}
    for sc in read_csv("scenes.csv"):
        by_story.setdefault(sc["story_id"], []).append(sc)

    ids = [s["story_id"] for s in stories]
    for sid, n in Counter(ids).items():
        if n > 1:
            errors.append(f"{sid}: duplicate story_id")
    for sid in by_story:
        if sid not in ids:
            errors.append(f"{sid}: scenes exist but story is missing")
    for s in stories:
        errors += check_episode(s, by_story.get(s["story_id"], []), chars)
        early = [c for c in s["characters_used"].split(";") if debut.get(c, "") > s["story_id"]]
        if early:
            errors.append(f"{s['story_id']}: uses {';'.join(early)} before their first_episode")

    shots_by_story: dict[str, list[dict]] = {}
    for sh in read_csv("shots.csv"):
        shots_by_story.setdefault(sh["story_id"], []).append(sh)
    for sid, shots in shots_by_story.items():
        if sid not in ids:
            errors.append(f"{sid}: shots exist but story is missing")
            continue
        if sorted(s["scene_id"] for s in shots) != sorted(s["scene_id"] for s in by_story.get(sid, [])):
            errors.append(f"{sid}: shot list doesn't match the scenes one-to-one")
        for sh in shots:
            tag = f"{sid} shot {sh['scene_id']}"
            if sh["shot_size"] not in SHOT_SIZES:
                errors.append(f"{tag}: bad shot_size '{sh['shot_size']}'")
            if sh["movement"] not in MOVEMENTS:
                errors.append(f"{tag}: bad movement '{sh['movement']}'")
            if not sh["image_prompt"].strip():
                errors.append(f"{tag}: empty image_prompt")

    seen: dict[str, str] = {}
    for ch in read_csv("chapters.csv"):
        tag = f"season {ch['season']} chapter {ch['chapter']}"
        if not ch["first_episode"] <= ch["last_episode"]:
            errors.append(f"{tag}: first_episode after last_episode")
        for n in range(int(ch["first_episode"][1:]), int(ch["last_episode"][1:]) + 1):
            if story_id(n) in seen:
                errors.append(f"{tag}: {story_id(n)} is already in {seen[story_id(n)]}")
            seen[story_id(n)] = tag

    for cb in callbacks:
        s_ep, p_ep = cb["setup_episode"], cb["payoff_episode"]
        if s_ep not in ids:
            errors.append(f"callback setup {s_ep}: no such story")
        if p_ep <= s_ep:
            errors.append(f"callback {s_ep} -> {p_ep}: payoff must come after setup")

    # Pacing: a future_tease at least every 5 episodes (soft rule).
    gap = 0
    for s in sorted(stories, key=lambda s: int(s["episode"])):
        gap = 0 if s["future_tease"] not in ("", "None") else gap + 1
        if gap > 5:
            warnings.append(f"{s['story_id']}: {gap} episodes since last future_tease")
    return errors, warnings


if __name__ == "__main__":
    errs, warns = validate()
    for w in warns:
        print(f"WARN  {w}")
    for e in errs:
        print(f"ERROR {e}")
    print(f"{len(errs)} error(s), {len(warns)} warning(s)")
    sys.exit(1 if errs else 0)
