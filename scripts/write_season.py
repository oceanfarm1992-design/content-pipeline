"""Write the next season (30 episodes, one per day for a month) with four OpenAI passes:

  1. showrunner        season outline: chapters, a beat per episode, who new is needed
  2. casting director  full designs + Kokoro voices for the new characters
  3. screenwriter      each episode's 12-scene script (same rules as generate_stories.py)
  4. film director     each episode's shot list: framing, camera move, lighting, image + motion prompts

    python scripts/write_season.py
    python scripts/write_season.py --fixtures DIR   # offline: read <step>.json files instead of calling the API

Resumable: the outline is saved to series/season_plans/season_<n>.json, and episodes that
already have a script or shot list are skipped. Episodes are written as 'approved': the
monthly workflow puts them on a branch, and merging that pull request is the approval.
"""
import argparse
import json
import os
import re
import sys

from common import (CHAPTER_FIELDS, CHARACTER_FIELDS, MOVEMENTS, REGISTERS, SERIES, SHOT_FIELDS, SHOT_SIZES, VOICES,
                    append_csv, character_id, read_csv, season_for, story_id)
from generate_stories import (MAX_ATTEMPTS, NEW_CHARACTER_KEYS, SYSTEM_PROMPT as WRITER_PROMPT, _text,
                              available_cast, build_user_prompt, call_api, ingest, save)
from validate_stories import check_character

PLANS = SERIES / "season_plans"
MAX_NEW_CAST = 6

SHOWRUNNER_PROMPT = f"""You are the showrunner of "The Box Debates," a serialized photorealistic short-form drama about two broken cats
(Mango, Shadow) and a lonely human learning to be a family. Each episode is 60 seconds, so every episode needs one
clear, visual beat. You plan a whole season before the writers start.

Plan the season you are given:
- 4 or 5 chapters (story movements), contiguous, together covering every episode exactly once.
- One entry per episode: a working title, one emotional_register (from: {", ".join(sorted(REGISTERS))}),
  and a 1-2 sentence beat saying what happens and what changes. Vary the registers; never the same twice in a row.
- Build the season's arc toward its finale; the last episode ends on the season's finale hook.
- Decide who the season needs beyond the existing cast (at most {MAX_NEW_CAST}): name, role ("recurring" if they
  matter across several episodes, "guest" for a single episode), the episode they first appear in, and why.
- Callbacks: plant small setups early that pay off later.

Return ONLY JSON:
{{"chapters": [{{"chapter", "title", "summary", "first_episode", "last_episode"}}],
  "episodes": [{{"episode", "chapter", "title", "emotional_register", "beat", "characters"}}],
  "new_cast": [{{"name", "role", "first_episode", "purpose"}}]}}
Episode numbers are integers. "characters" lists the names appearing in that episode."""

CASTING_PROMPT = f"""You are the casting director for "The Box Debates," a photorealistic short-form series. For each new
character the showrunner needs, write the full casting and appearance sheet a cinematographer and a voice director can use.

- description: concrete photographic appearance (species, breed, build, colours, markings, clothing, age). Adults look clearly adult.
- never_change: 2-4 visual traits that stay identical in every scene, separated by ';'.
- negative: visual traits the artist must avoid (comma-separated), or "".
- traits: personality, separated by ';'. backstory, wound, fear, arc: one short sentence each.
- voice_id: one of {", ".join(sorted(VOICES))} (a=American, b=British, f=female, m=male), matching the character,
  and different from every voice already in the cast and from each other.
- Use exactly the character_id, name, role and first_episode you are given for each one, in order.

Return ONLY JSON: {{"new_characters": [{{{", ".join(f'"{k}"' for k in NEW_CHARACTER_KEYS + ["first_episode"])}}}]}}"""

DIRECTOR_PROMPT = f"""You are the film director of "The Box Debates," a photorealistic series filmed (rendered) one
5-second shot per scene. Turn the episode's script into a shot list, one shot per scene.

- shot_size: one of {", ".join(sorted(SHOT_SIZES))}.
- angle: e.g. eye level, low angle, high angle, cat's-eye view, over the shoulder; include the lens (24mm wide, 50mm, 85mm portrait).
- movement: one of {", ".join(sorted(MOVEMENTS))}. Use stillness for emotion and movement for comedy or tension.
- lighting: time of day, light source and mood in a few words; keep one location's lighting consistent.
- image_prompt: a self-contained photorealistic text-to-image prompt for the shot's key frame (cinematic still, lens, depth of field): composition, each visible character's
  look (from their design), their pose and expression, the setting and the lighting. No dialogue, no text in frame.
  The Human's face is never shown.
- motion_prompt: what moves during the 5 seconds, under 25 words.
- Vary framing for rhythm; open on an establishing shot; save close-ups for the emotional turn.

Return ONLY JSON: {{"shots": [{{"scene_id", "shot_size", "angle", "movement", "lighting", "image_prompt", "motion_prompt"}}]}}
with exactly one entry per scene."""


class StepFailed(Exception):
    pass


def ask(client, fixtures: str | None, key: str, system: str, prompt: str, check):
    """Call the API (or read fixture <key>.json), validate with check(data) -> (result, errors), retry with feedback."""
    feedback = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if fixtures:
            with open(os.path.join(fixtures, f"{key}.json"), encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = call_api(client, prompt, feedback, system=system)
        result, errors = check(data)
        if not errors:
            return result
        print(f"{key}: attempt {attempt} rejected ({len(errors)} error(s))", file=sys.stderr)
        feedback = "\n".join(errors[:15])
        if fixtures:
            break
    raise StepFailed(f"{key}: " + "; ".join(errors[:5]))


def _slug(value) -> str:
    return re.sub(r"[\s\-]+", "_", str(value).strip().lower())


# --- 1. showrunner -------------------------------------------------------------------------------------------

def showrunner_prompt(season: dict, first: int, last: int, stories: list[dict], cast: list[dict],
                      callbacks: list[dict]) -> str:
    lines = [f"Plan Season {season['season']}: {season['title']}, episodes {first} to {last}.",
             f"Logline: {season['logline']}", f"Arc: {season['main_arc']}",
             f"Finale hook (episode {last}): {season['finale_hook']}", "Existing cast:"]
    lines += [f"- {c['name']} ({c['species']}, {c['role']}): {c['description']}. Arc: {c['arc']}."
              for c in cast if c["role"] != "guest"]
    earlier = sorted(stories, key=lambda s: int(s["episode"]))[-8:]
    if earlier:
        lines.append("Most recent episodes already written:")
        lines += [f"- Ep {s['episode']} \"{s['title']}\": {s['synopsis']}" for s in earlier]
    open_cbs = [c for c in callbacks if c["payoff_episode"] >= story_id(first)]
    if open_cbs:
        lines.append("Planted setups still waiting for a payoff:")
        lines += [f"- {c['setup_moment']} -> {c['payoff_moment']} (due {c['payoff_episode']})" for c in open_cbs]
    return "\n".join(lines)


def check_plan(data: dict, first: int, last: int) -> tuple[dict, list[str]]:
    errors = []
    try:
        chapters = [{**c, "chapter": int(c["chapter"]), "first_episode": int(c["first_episode"]),
                     "last_episode": int(c["last_episode"])} for c in data["chapters"]]
        episodes = [{**e, "episode": int(e["episode"]), "chapter": int(e["chapter"])} for e in data["episodes"]]
        new_cast = [{**n, "role": str(n["role"]).lower(), "first_episode": int(n["first_episode"])}
                    for n in data.get("new_cast") or []]
    except (KeyError, TypeError, ValueError) as e:
        return {}, [f"malformed outline ({type(e).__name__}: {e})"]

    covered = [n for c in sorted(chapters, key=lambda c: c["first_episode"])
               for n in range(c["first_episode"], c["last_episode"] + 1)]
    if covered != list(range(first, last + 1)):
        errors.append(f"chapters must cover episodes {first}-{last} contiguously, once each")
    if not 3 <= len(chapters) <= 6:
        errors.append(f"{len(chapters)} chapters (want 4-5)")
    if sorted(e["episode"] for e in episodes) != list(range(first, last + 1)):
        errors.append(f"need exactly one episode entry for each of {first}-{last}")
    prev = None
    for e in sorted(episodes, key=lambda e: e["episode"]):
        if e["emotional_register"] not in REGISTERS:
            errors.append(f"episode {e['episode']}: bad emotional_register '{e['emotional_register']}'")
        if e["emotional_register"] == prev:
            errors.append(f"episode {e['episode']}: same emotional_register as the episode before")
        prev = e["emotional_register"]
        if not str(e.get("beat", "")).strip():
            errors.append(f"episode {e['episode']}: empty beat")
    if len(new_cast) > MAX_NEW_CAST:
        errors.append(f"{len(new_cast)} new characters (max {MAX_NEW_CAST})")
    for n in new_cast:
        if n["role"] not in ("recurring", "guest"):
            errors.append(f"new cast '{n.get('name')}': role must be recurring or guest")
        if not first <= n["first_episode"] <= last:
            errors.append(f"new cast '{n.get('name')}': first_episode must be within {first}-{last}")
    return {"chapters": chapters, "episodes": episodes, "new_cast": new_cast}, errors


# --- 2. casting director -------------------------------------------------------------------------------------

def casting_prompt(plan: dict, cast: list[dict], first_id: int) -> str:
    lines = ["Voices already taken: " + ", ".join(sorted({c["voice_id"] for c in cast})) + ".",
             "Existing cast for style reference:"]
    lines += [f"- {c['name']} ({c['species']}): {c['description']}" for c in cast if c["role"] == "main"]
    lines.append("Cast these new characters:")
    for i, n in enumerate(plan["new_cast"]):
        lines.append(f"- character_id {character_id(first_id + i)}, name {n['name']}, role {n['role']}, "
                     f"first_episode {n['first_episode']}: {n['purpose']}")
    return "\n".join(lines)


def check_cast(data: dict, plan: dict, cast: list[dict], first_id: int) -> tuple[list[dict], list[str]]:
    try:
        raw = data["new_characters"]
        rows = [{k: _text(c.get(k)) for k in NEW_CHARACTER_KEYS} for c in raw]
    except (KeyError, TypeError) as e:
        return [], [f"malformed casting ({type(e).__name__}: {e})"]
    errors = []
    if len(rows) != len(plan["new_cast"]):
        errors.append(f"cast {len(rows)} characters, the showrunner asked for {len(plan['new_cast'])}")
    used = {c["voice_id"] for c in cast}
    for i, (row, need) in enumerate(zip(rows, plan["new_cast"])):
        cid = character_id(first_id + i)
        row.update(character_id=cid, name=need["name"], role=need["role"],
                   first_episode=story_id(need["first_episode"]), reference_sheet=f"series/refs/{cid}.png")
        if row["voice_id"] in used:
            errors.append(f"{cid} {row['name']}: voice {row['voice_id']} is already taken")
        used.add(row["voice_id"])
        errors += check_character(row)
    return rows, errors


# --- 4. film director ----------------------------------------------------------------------------------------

def director_prompt(story: dict, scenes: list[dict], cast: list[dict]) -> str:
    in_episode = {c for s in scenes for c in s["characters"].split(";") if c}
    lines = [f"Episode {story['episode']} \"{story['title']}\" ({story['emotional_register']}): {story['synopsis']}",
             "Character designs:"]
    lines += [f"- {c['character_id']} {c['name']}: {c['description']}. Always: {c['never_change'].replace(';', ', ')}."
              for c in cast if c["character_id"] in in_episode]
    lines.append("Script:")
    lines += [f"{s['scene_id']}. [{s['characters']}] {s['action']} | camera note: {s['camera']} | "
              f"{s['speaker'] + ': ' + s['dialogue'] if s['dialogue'] else '(no dialogue)'} | sfx: {s['sfx']}"
              for s in sorted(scenes, key=lambda s: int(s["scene_id"]))]
    return "\n".join(lines)


def check_shots(data: dict, sid: str, scene_ids: list[str]) -> tuple[list[dict], list[str]]:
    try:
        rows = [{"story_id": sid, "scene_id": re.findall(r"\d+", str(s["scene_id"]))[-1].lstrip("0") or "0",
                 "shot_size": _slug(s["shot_size"]), "angle": str(s["angle"]).strip(),
                 "movement": _slug(s["movement"]), "lighting": str(s["lighting"]).strip(),
                 "image_prompt": str(s["image_prompt"]).strip(), "motion_prompt": str(s["motion_prompt"]).strip()}
                for s in data["shots"]]
    except (KeyError, TypeError, IndexError) as e:
        return [], [f"{sid}: malformed shot list ({type(e).__name__}: {e})"]
    errors = []
    if sorted(r["scene_id"] for r in rows) != sorted(scene_ids):
        errors.append(f"{sid}: need exactly one shot per scene ({len(scene_ids)}), got {len(rows)}")
    for r in rows:
        tag = f"{sid} shot {r['scene_id']}"
        if r["shot_size"] not in SHOT_SIZES:
            errors.append(f"{tag}: bad shot_size '{r['shot_size']}'")
        if r["movement"] not in MOVEMENTS:
            errors.append(f"{tag}: bad movement '{r['movement']}'")
        for col in ("angle", "lighting", "image_prompt", "motion_prompt"):
            if not r[col]:
                errors.append(f"{tag}: empty {col}")
    return sorted(rows, key=lambda r: int(r["scene_id"])), errors


# --- orchestration -------------------------------------------------------------------------------------------

def outline_for(plan: dict, episode: int) -> str:
    e = next(e for e in plan["episodes"] if e["episode"] == episode)
    ch = next(c for c in plan["chapters"] if c["first_episode"] <= episode <= c["last_episode"])
    who = e.get("characters") or []
    return (f"Chapter {ch['chapter']} \"{ch['title']}\": {ch['summary']}\n"
            f"This episode: \"{e['title']}\" ({e['emotional_register']}). {e['beat']}"
            + (f"\nFeaturing: {', '.join(map(str, who))}." if who else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", help="directory of <step>.json responses to use instead of the API")
    args = ap.parse_args()

    seasons = read_csv("seasons_plan.csv")
    stories = read_csv("stories.csv")
    next_ep = max((int(s["episode"]) for s in stories), default=0) + 1
    try:
        season, n, _ = season_for(next_ep, seasons)
    except ValueError:
        print("Every season in seasons_plan.csv is written. Add the next season's row to continue.")
        return 0
    first, last = next_ep - n + 1, next_ep - n + int(season["episode_count"])
    todo_from = next_ep
    print(f"Season {season['season']} \"{season['title']}\": episodes {first}-{last}, writing from {todo_from}")

    client = None
    if not args.fixtures:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    PLANS.mkdir(exist_ok=True)
    plan_path = PLANS / f"season_{season['season']}.json"
    try:
        if plan_path.exists():
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            print(f"Resuming from {plan_path.name}")
        else:
            prompt = showrunner_prompt(season, todo_from, last, stories, read_csv("characters.csv"),
                                       read_csv("callbacks.csv"))
            plan = ask(client, args.fixtures, "showrunner", SHOWRUNNER_PROMPT, prompt,
                       lambda d: check_plan(d, todo_from, last))
            plan["cast_done"] = False
            plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
            append_csv("chapters.csv", CHAPTER_FIELDS, [
                {"season": season["season"], "chapter": c["chapter"], "title": c["title"],
                 "first_episode": story_id(c["first_episode"]), "last_episode": story_id(c["last_episode"]),
                 "summary": c["summary"]} for c in plan["chapters"]])
            print(f"Showrunner: {len(plan['chapters'])} chapters, {len(plan['new_cast'])} new characters needed")

        if not plan["cast_done"]:
            cast = read_csv("characters.csv")
            first_id = max(int(c["character_id"][1:]) for c in cast) + 1
            if plan["new_cast"]:
                rows = ask(client, args.fixtures, "casting", CASTING_PROMPT, casting_prompt(plan, cast, first_id),
                           lambda d: check_cast(d, plan, cast, first_id))
                append_csv("characters.csv", CHARACTER_FIELDS, rows)
                print("Casting: " + ", ".join(f"{r['character_id']} {r['name']} ({r['role']}, {r['voice_id']})" for r in rows))
            plan["cast_done"] = True
            plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

        for episode in range(todo_from, last + 1):
            sid = story_id(episode)
            stories, cast = read_csv("stories.csv"), read_csv("characters.csv")
            if not any(s["story_id"] == sid for s in stories):
                prompt = build_user_prompt(episode, stories, read_csv("callbacks.csv"), seasons, cast,
                                           outline=outline_for(plan, episode), allow_new=False)

                def check_script(d, episode=episode, cast=cast):
                    story, scenes, cbs, _new, errors = ingest(d, episode, cast, allow_new=False)
                    return (story, scenes, cbs), errors

                story, scenes, cbs = ask(client, args.fixtures, f"writer_{sid}", WRITER_PROMPT, prompt, check_script)
                story["status"] = "approved"
                save(story, scenes, cbs, [])
                print(f"Writer:   {sid} \"{story['title']}\"")

            if not any(s["story_id"] == sid for s in read_csv("shots.csv")):
                story = next(s for s in read_csv("stories.csv") if s["story_id"] == sid)
                scenes = [s for s in read_csv("scenes.csv") if s["story_id"] == sid]
                shots = ask(client, args.fixtures, f"director_{sid}", DIRECTOR_PROMPT,
                            director_prompt(story, scenes, available_cast(read_csv("characters.csv"), sid)),
                            lambda d, sid=sid, ids=[s["scene_id"] for s in scenes]: check_shots(d, sid, ids))
                append_csv("shots.csv", SHOT_FIELDS, shots)
                print(f"Director: {sid} {len(shots)} shots")
    except StepFailed as e:
        print(f"Stopped: {e}. Everything before this step is saved; rerun to continue.", file=sys.stderr)
        return 1
    print(f"Season {season['season']} written: episodes {todo_from}-{last}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
