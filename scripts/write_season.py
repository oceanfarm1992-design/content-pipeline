"""Write the next series (30 episodes, one per day for a month) the way a drama is developed:
story first, characters second, screenplay third, visual production last.

Each series is a standalone vertical micro-drama. If seasons_plan.csv has no row for it, the showrunner develops
the concept from series/channel_brief.md and the row is added; a row you add yourself fixes the concept.

  1. showrunner        drama concept, four acts, a beat per episode, locations, the cast it needs (leads included)
  2. story editor      checks the outline against the quality checklist; weak outlines are rewritten
  3. casting director  full character bible + Kokoro voice for each new character
  4. screenwriter      each episode's 12-scene script, checked against the story bible
  5. story editor      checks each episode; weak ones go back to the writer
  6. film director     each episode's shot list: framing, camera move, lighting, image + motion prompts
  7. season package    series/seasons/season_<n>.md, the production-ready document for review

    python scripts/write_season.py
    python scripts/write_season.py --fixtures DIR   # offline: read <step>.json files instead of calling the API

Resumable: the outline is saved to series/season_plans/season_<n>.json, and episodes that
already have a script or shot list are skipped. Episodes are written as 'approved' (merging
the season's pull request is the approval); one the editor still rejects after rewrites is
left 'pending' and flagged in the package.
"""
import argparse
import json
import os
import re
import sys

from common import (ACTS, CHAPTER_FIELDS, CHARACTER_FIELDS, LOCATION_FIELDS, MAX_CHARACTERS_PER_SCENE, MOVEMENTS,
                    REGISTERS, SERIES, SHOT_FIELDS, SHOT_SIZES, VOICES, append_csv, character_id, read_csv,
                    series_slot, story_id, write_csv)
from generate_stories import (MAX_ATTEMPTS, NEW_CHARACTER_KEYS, SYSTEM_PROMPT as WRITER_PROMPT, _slug, _text,
                              available_cast, bible_text, build_user_prompt, call_api, ingest, save)
from validate_stories import check_character

PLANS = SERIES / "season_plans"
BRIEF = SERIES / "channel_brief.md"
SEASON_FIELDS = ["season", "title", "episode_count", "logline", "main_arc", "finale_hook"]
MAX_NEW_CAST = 8
EDIT_ROUNDS = 2          # editor rejections before an episode is left 'pending' for a human
MIN_SCORE = 3            # editor scores are 1-5; anything lower is a rejection

SHOWRUNNER_PROMPT = f"""You are the showrunner of a channel of photorealistic vertical (9:16) micro-drama series.
Each series is standalone; each episode is 60 seconds, so every episode needs one clear, visual beat and ends on a
hook. Follow the channel brief you are given. Develop the series as a complete drama before any scene is written.

1. Drama concept: title (states the whole premise in one line, like the brief's examples), concept (2 sentences),
   genre, target audience, main message, central conflict, ending concept.
2. Exactly 4 acts as chapters, contiguous, together covering every episode once:
   Act 1 {ACTS[0]}: introduce the characters and their world, create curiosity, introduce the main problem.
   Act 2 {ACTS[1]}: raise tension, obstacles, emotional situations, deepen relationships, unexpected events.
   Act 3 {ACTS[2]}: the biggest conflict, the emotional or dramatic turning point, the key reveal.
   Act 4 {ACTS[3]}: resolve the conflict, show consequences, a satisfying, memorable ending (the season's finale hook).
3. One entry per episode: working title, one emotional_register (from: {", ".join(sorted(REGISTERS))}), and a 1-2
   sentence beat saying what happens and what changes. Vary registers; never the same twice in a row. Episode 1 of the
   season opens on a strong hook. Earn cliffhangers; don't force one every episode.
4. Locations: the 3-6 recurring sets the season is filmed in (name, what it looks like, its usual lighting).
   Keep scenes producible: at most {MAX_CHARACTERS_PER_SCENE} characters on screen at once.
5. The cast the series needs beyond any existing cast (at most {MAX_NEW_CAST}): name, role ("main" for the leads,
   who appear from the first episode; "recurring"; or "guest" for one episode), first episode, and why the story
   needs them. Include the animal ally if the concept has one (role main or recurring).

Return ONLY JSON:
{{"concept": {{"title", "concept", "genre", "audience", "message", "central_conflict", "ending"}},
  "chapters": [{{"chapter", "title", "summary", "first_episode", "last_episode"}}],
  "episodes": [{{"episode", "chapter", "title", "emotional_register", "beat", "characters"}}],
  "locations": [{{"name", "description", "lighting"}}],
  "new_cast": [{{"name", "role", "first_episode", "purpose"}}]}}
Episode numbers are integers. "characters" lists the names appearing in that episode."""

CASTING_PROMPT = f"""You are the casting director of a photorealistic vertical micro-drama series. For each new
character the showrunner needs, write the full character bible entry a cinematographer, a costume designer and a
voice director can use. Appearance and clothing stay identical in every episode unless the story changes them.

- age; description: concrete photographic appearance. People: ethnicity, build, a distinctive face (face shape, eyes,
  brows, nose), hair colour and style. Animals: species, breed, size, coat colour and markings. Adults look clearly adult.
- clothing: exactly what they wear (or "none (no collar)" for an animal without one).
- traits (personality), strengths, weaknesses: separated by ';'. motivation, backstory, wound, fear, arc: one sentence each.
- relationships: how they relate to each other character, separated by ';'.
- never_change: 2-4 visual traits that stay identical in every scene, separated by ';'.
- negative: visual traits the artist must avoid (comma-separated), or "".
- voice_id: one of {", ".join(sorted(VOICES))} (a=American, b=British, f=female, m=male), matching the character,
  and different from every voice already in the cast and from each other.
- Use exactly the character_id, name, role and first_episode you are given for each one, in order.

Return ONLY JSON: {{"new_characters": [{{{", ".join(f'"{k}"' for k in NEW_CHARACTER_KEYS + ["first_episode"])}}}]}}"""

EDITOR_PROMPT = f"""You are the story editor of a vertical micro-drama series. Nothing goes to video production until it is good.
Judge the material you are given against this checklist and score each 1-5 (3 = acceptable):
- opening: does the opening capture attention?
- characters: are the characters believable and consistent with their bible?
- conflict: is the conflict interesting?
- logic: does the story develop logically from what came before (timeline, open threads)?
- emotion: are the emotions convincing and shown, not explained?
- ending: is the ending satisfying for this unit (or an earned cliffhanger)?
- continuity: no contradictions with the story bible (appearance, clothing, locations, events)?
- producible: can every scene be filmed as 5-second shots with at most {MAX_CHARACTERS_PER_SCENE} characters, one clear
  action, simple camera moves, dialogue of 12 words or fewer, no front-on shots of someone speaking (there is no
  lip-sync), and no talking animals on screen?
approved is true only if every score is {MIN_SCORE} or more. If not, list concrete, fixable issues for the writer.

Return ONLY JSON: {{"approved": true, "scores": {{"opening": 0, "characters": 0, "conflict": 0, "logic": 0,
"emotion": 0, "ending": 0, "continuity": 0, "producible": 0}}, "issues": []}}"""

DIRECTOR_PROMPT = f"""You are the film director of a photorealistic vertical (9:16) micro-drama series filmed (rendered)
one 5-second shot per scene. Turn the episode's script into a shot list, one shot per scene, composed for a tall frame.

- shot_size: one of {", ".join(sorted(SHOT_SIZES))}.
- angle: e.g. eye level, low angle, high angle, cat's-eye view, over the shoulder; include the lens (24mm wide, 50mm, 85mm portrait).
- movement: one of {", ".join(sorted(MOVEMENTS))}. Use stillness for emotion and movement for comedy or tension.
- lighting: matches the scene's location and time of day; consistent within a location.
- image_prompt: a self-contained photorealistic text-to-image prompt for the shot's key frame (cinematic still, lens,
  depth of field): composition, each visible character's exact appearance and clothing (from their bible), pose and
  expression, the location and the lighting, starting with "vertical 9:16 cinematic still". No text in frame.
- There is no lip-sync: in a scene with dialogue, frame the listener's reaction, the speaker from behind, in profile
  or over the shoulder, or their hands; never a front-on shot of a moving mouth.
- motion_prompt: what moves during the 5 seconds, under 25 words.
- Vary framing for rhythm; open on an establishing shot; save close-ups for the emotional turn.

Return ONLY JSON: {{"shots": [{{"scene_id", "shot_size", "angle", "movement", "lighting", "image_prompt", "motion_prompt"}}]}}
with exactly one entry per scene."""


class StepFailed(Exception):
    pass


def ask(client, fixtures: str | None, key: str, system: str, prompt: str, check, feedback: str = ""):
    """Call the API (or read fixture <key>.json), validate with check(data) -> (result, errors), retry with feedback."""
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


def check_review(data: dict) -> tuple[dict, list[str]]:
    try:
        scores = {k: int(v) for k, v in data["scores"].items()}
        issues = [str(i).strip() for i in data.get("issues") or [] if str(i).strip()]
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        return {}, [f"malformed review ({type(e).__name__}: {e})"]
    approved = bool(data.get("approved")) and all(v >= MIN_SCORE for v in scores.values())
    if not approved and not issues:
        issues = [f"raise {k} (scored {v})" for k, v in scores.items() if v < MIN_SCORE] or ["not approved"]
    return {"approved": approved, "scores": scores, "issues": issues}, []


# --- 1. showrunner -------------------------------------------------------------------------------------------

def showrunner_prompt(row: dict | None, number: int, first: int, last: int, stories: list[dict], cast: list[dict],
                      callbacks: list[dict], locations: list[dict], continuity: list[dict], brief: str) -> str:
    """`cast`, `locations`, `continuity` and `stories` are already limited to this series."""
    lines = [f"CHANNEL BRIEF:\n{brief}", "",
             f"Develop Series {number}: episodes {first} to {last} ({last - first + 1} episodes)."]
    if row:
        lines.append("The producer has fixed this much of the concept; build everything on it and develop the rest:")
        lines += [f"- {label}: {row[key]}" for label, key in
                  (("Title", "title"), ("Premise", "logline"), ("Arc", "main_arc"), (f"Ending (episode {last})", "finale_hook"))
                  if row.get(key, "").strip()]
    else:
        lines.append("Pitch and develop a new, original concept for this series that fits the brief "
                     "(do not reuse the brief's example titles).")
    if cast:
        lines.append("Cast already in this series:")
        lines += [f"- {c['name']} ({c['species']}, {c['role']}, age {c['age']}): {c['description']}. "
                  f"Motivation: {c['motivation']}. Arc: {c['arc']}. Relationships: {c['relationships']}."
                  for c in cast if c["role"] != "guest"]
    if locations:
        lines.append("Locations already established (reuse them where they fit): " + ", ".join(l["name"] for l in locations))
    earlier = sorted(stories, key=lambda s: int(s["episode"]))[-8:]
    if earlier:
        lines.append("Most recent episodes already written:")
        lines += [f"- Ep {s['episode']} \"{s['title']}\": {s['synopsis']}" for s in earlier]
    bible = bible_text(story_id(first), cast, [], continuity)
    if bible:
        lines.append(bible)
    open_cbs = [c for c in callbacks if c["payoff_episode"] >= story_id(first)]
    if open_cbs:
        lines.append("Planted setups still waiting for a payoff:")
        lines += [f"- {c['setup_moment']} -> {c['payoff_moment']} (due {c['payoff_episode']})" for c in open_cbs]
    return "\n".join(lines)


def check_plan(data: dict, first: int, last: int, known_locations: list[dict],
               need_leads: bool = False) -> tuple[dict, list[str]]:
    errors = []
    try:
        concept = {k: str(data["concept"].get(k, "")).strip()
                   for k in ("title", "concept", "genre", "audience", "message", "central_conflict", "ending")}
        chapters = [{**c, "chapter": int(c["chapter"]), "first_episode": int(c["first_episode"]),
                     "last_episode": int(c["last_episode"])} for c in data["chapters"]]
        episodes = [{**e, "episode": int(e["episode"]), "chapter": int(e["chapter"])} for e in data["episodes"]]
        locations = [{k: str(loc.get(k, "")).strip() for k in ("name", "description", "lighting")}
                     for loc in data.get("locations") or []]
        new_cast = [{**n, "role": str(n["role"]).lower(), "first_episode": int(n["first_episode"])}
                    for n in data.get("new_cast") or []]
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        return {}, [f"malformed outline ({type(e).__name__}: {e})"]

    errors += [f"concept.{k} is empty" for k, v in concept.items() if not v]
    if len(chapters) != len(ACTS):
        errors.append(f"{len(chapters)} chapters; need exactly {len(ACTS)} acts ({', '.join(ACTS)})")
    covered = [n for c in sorted(chapters, key=lambda c: c["first_episode"])
               for n in range(c["first_episode"], c["last_episode"] + 1)]
    if covered != list(range(first, last + 1)):
        errors.append(f"acts must cover episodes {first}-{last} contiguously, once each")
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
    if not known_locations and not 3 <= len(locations) <= 6:
        errors.append(f"{len(locations)} locations (want 3-6)")
    errors += [f"location {i + 1}: name, description and lighting are all required"
               for i, loc in enumerate(locations) if not all(loc.values())]
    if len(new_cast) > MAX_NEW_CAST:
        errors.append(f"{len(new_cast)} new characters (max {MAX_NEW_CAST})")
    for n in new_cast:
        if n["role"] not in ("main", "recurring", "guest"):
            errors.append(f"new cast '{n.get('name')}': role must be main, recurring or guest")
        if not first <= n["first_episode"] <= last:
            errors.append(f"new cast '{n.get('name')}': first_episode must be within {first}-{last}")
        if n["role"] == "main" and n["first_episode"] != first:
            errors.append(f"new cast '{n.get('name')}': a main character appears from episode {first}")
    if need_leads and not any(n["role"] == "main" for n in new_cast):
        errors.append("a new series needs its leads: at least one new_cast entry with role main")
    return {"concept": concept, "chapters": chapters, "episodes": episodes, "locations": locations,
            "new_cast": new_cast}, errors


def outline_review_prompt(plan: dict) -> str:
    c = plan["concept"]
    lines = [f"Review this series outline. Drama: {c['title']} ({c['genre']}, for {c['audience']}). {c['concept']} "
             f"Message: {c['message']}. Central conflict: {c['central_conflict']}. Ending: {c['ending']}.",
             "Locations: " + "; ".join(f"{l['name']} ({l['description']})" for l in plan["locations"])]
    for ch in plan["chapters"]:
        lines.append(f"Act {ch['chapter']} \"{ch['title']}\" (episodes {ch['first_episode']}-{ch['last_episode']}): {ch['summary']}")
        lines += [f"  {e['episode']}. \"{e['title']}\" [{e['emotional_register']}] {e['beat']}" for e in plan["episodes"]
                  if ch["first_episode"] <= e["episode"] <= ch["last_episode"]]
    lines += [f"New: {n['name']} ({n['role']}, from ep {n['first_episode']}): {n['purpose']}" for n in plan["new_cast"]]
    return "\n".join(lines)


# --- 3. casting director -------------------------------------------------------------------------------------

def casting_prompt(plan: dict, cast: list[dict], first_id: int) -> str:
    """`cast` is this series' existing cast (voices only need to be unique within a series)."""
    lines = ["Voices already taken: " + (", ".join(sorted({c["voice_id"] for c in cast})) or "none") + ".",
             f"Drama: {plan['concept']['title']} - {plan['concept']['concept']}"]
    if cast:
        lines.append("Existing cast (for style and relationships):")
        lines += [f"- {c['name']} ({c['species']}): {c['description']}. Wears: {c['clothing']}." for c in cast
                  if c["role"] != "guest"]
    lines.append("Cast these new characters:")
    for i, n in enumerate(plan["new_cast"]):
        lines.append(f"- character_id {character_id(first_id + i)}, name {n['name']}, role {n['role']}, "
                     f"first_episode {n['first_episode']}: {n['purpose']}")
    return "\n".join(lines)


def check_cast(data: dict, plan: dict, cast: list[dict], first_id: int, series: int) -> tuple[list[dict], list[str]]:
    try:
        rows = [{k: _text(c.get(k)) for k in NEW_CHARACTER_KEYS} for c in data["new_characters"]]
    except (KeyError, TypeError, AttributeError) as e:
        return [], [f"malformed casting ({type(e).__name__}: {e})"]
    errors = []
    if len(rows) != len(plan["new_cast"]):
        errors.append(f"cast {len(rows)} characters, the showrunner asked for {len(plan['new_cast'])}")
    used = {c["voice_id"] for c in cast}
    for i, (row, need) in enumerate(zip(rows, plan["new_cast"])):
        cid = character_id(first_id + i)
        row.update(character_id=cid, name=need["name"], role=need["role"], series=str(series),
                   first_episode=story_id(need["first_episode"]), reference_sheet=f"series/refs/{cid}.png")
        if row["voice_id"] in used:
            errors.append(f"{cid} {row['name']}: voice {row['voice_id']} is already taken")
        used.add(row["voice_id"])
        errors += check_character(row)
    return rows, errors


# --- 4-5. screenwriter + story editor ------------------------------------------------------------------------

def outline_for(plan: dict, episode: int) -> str:
    e = next(e for e in plan["episodes"] if e["episode"] == episode)
    ch = next(c for c in plan["chapters"] if c["first_episode"] <= episode <= c["last_episode"])
    who = e.get("characters") or []
    return (f"Act {ch['chapter']} \"{ch['title']}\": {ch['summary']}\n"
            f"This episode: \"{e['title']}\" ({e['emotional_register']}). {e['beat']}"
            + (f"\nFeaturing: {', '.join(map(str, who))}." if who else ""))


def episode_review_prompt(story: dict, scenes: list[dict], cont_rows: list[dict], outline: str, bible: str) -> str:
    lines = [f"Review episode {story['episode']} \"{story['title']}\" ({story['emotional_register']}).",
             f"Outline it had to follow:\n{outline}", f"Story bible before this episode:\n{bible}",
             f"Hook: {story['hook']}  Synopsis: {story['synopsis']}  Lesson (shown, not said): {story['life_lesson']}",
             "Script:"]
    lines += [f"{s['scene_id']}. {s['location']}, {s['time_of_day']} | [{s['characters']}] {s['action']} | "
              f"feels: {s['emotion']} | camera: {s['camera']} | "
              f"{s['speaker'] + ': ' + repr(s['dialogue']) if s['dialogue'] else '(silence)'} | sfx: {s['sfx']}"
              for s in scenes]
    lines.append("Continuity it reports: " + "; ".join(f"{r['kind']} {r['thread_id']} {r['note']}".strip() for r in cont_rows))
    return "\n".join(lines)


# --- 6. film director ----------------------------------------------------------------------------------------

def director_prompt(story: dict, scenes: list[dict], cast: list[dict], locations: list[dict]) -> str:
    in_episode = {c for s in scenes for c in s["characters"].split(";") if c}
    used_locations = {s["location"] for s in scenes}
    lines = [f"Episode {story['episode']} \"{story['title']}\" ({story['emotional_register']}): {story['synopsis']}",
             "Character bible:"]
    lines += [f"- {c['character_id']} {c['name']}: {c['description']}. Wears: {c['clothing']}. "
              f"Always: {c['never_change'].replace(';', ', ')}." for c in cast if c["character_id"] in in_episode]
    lines += [f"Location {l['name']}: {l['description']} Usual lighting: {l['lighting']}." for l in locations
              if l["name"] in used_locations]
    lines.append("Script:")
    lines += [f"{s['scene_id']}. {s['location']}, {s['time_of_day']} [{s['characters']}] {s['action']} "
              f"(feels: {s['emotion']}) | camera note: {s['camera']} | "
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

def save_plan(path, plan: dict) -> None:
    path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")


def in_series(rows: list[dict], first: int, last: int, key: str = "story_id") -> list[dict]:
    lo, hi = story_id(first), story_id(last)
    return [r for r in rows if lo <= r[key] <= hi]


def plan_season(client, fixtures, row: dict | None, number: int, first: int, last: int, todo_from: int) -> dict:
    """Showrunner -> story editor loop. Returns the approved outline (with its reviews)."""
    cast = [c for c in read_csv("characters.csv") if c["series"] == str(number)]
    locations = in_series(read_csv("locations.csv"), first, last, "first_episode")
    brief = BRIEF.read_text(encoding="utf-8") if BRIEF.exists() else ""
    prompt = showrunner_prompt(row, number, todo_from, last, in_series(read_csv("stories.csv"), first, last), cast,
                               in_series(read_csv("callbacks.csv"), first, last, "setup_episode"), locations,
                               in_series(read_csv("continuity.csv"), first, last), brief)
    need_leads = not any(c["role"] == "main" for c in cast)
    feedback, review = "", None
    for rnd in range(EDIT_ROUNDS + 1):
        plan = ask(client, fixtures, "showrunner", SHOWRUNNER_PROMPT, prompt,
                   lambda d: check_plan(d, todo_from, last, locations, need_leads), feedback)
        review = ask(client, fixtures, "editor_outline", EDITOR_PROMPT, outline_review_prompt(plan), check_review)
        print(f"Editor:   outline round {rnd + 1}: {'approved' if review['approved'] else 'rejected'} {review['scores']}")
        if review["approved"]:
            break
        feedback = "The story editor sent the outline back:\n" + "\n".join(review["issues"])
    plan["review"] = review
    return plan


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", help="directory of <step>.json responses to use instead of the API")
    args = ap.parse_args()

    stories = read_csv("stories.csv")
    next_ep = max((int(s["episode"]) for s in stories), default=0) + 1
    row, number, first, last = series_slot(next_ep, read_csv("seasons_plan.csv"))
    todo_from = next_ep
    label = (f'"{row["title"] or row["logline"]}"' if row else "(new concept from the channel brief)")
    print(f"Series {number} {label}: episodes {first}-{last}, writing from {todo_from}")

    client = None
    if not args.fixtures:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    PLANS.mkdir(exist_ok=True)
    plan_path = PLANS / f"season_{number}.json"
    try:
        if plan_path.exists():
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            print(f"Resuming from {plan_path.name}")
        else:
            plan = plan_season(client, args.fixtures, row, number, first, last, todo_from)
            plan.update(season=number, cast_done=False, flagged=[])
            save_plan(plan_path, plan)
            c = plan["concept"]
            developed = {"season": str(number), "title": c["title"], "episode_count": str(last - first + 1),
                         "logline": c["concept"], "main_arc": c["central_conflict"], "finale_hook": c["ending"]}
            if not row:
                append_csv("seasons_plan.csv", SEASON_FIELDS, [developed])
            else:   # keep what the producer fixed, fill in what the showrunner developed
                rows = [{**r, **{k: v for k, v in developed.items() if not r[k].strip()}} if r["season"] == str(number)
                        else r for r in read_csv("seasons_plan.csv")]
                write_csv("seasons_plan.csv", SEASON_FIELDS, rows)
            append_csv("chapters.csv", CHAPTER_FIELDS, [
                {"season": number, "chapter": ch["chapter"], "title": ch["title"],
                 "first_episode": story_id(ch["first_episode"]), "last_episode": story_id(ch["last_episode"]),
                 "summary": ch["summary"]} for ch in plan["chapters"]])
            known = {l["name"].lower() for l in in_series(read_csv("locations.csv"), first, last, "first_episode")}
            append_csv("locations.csv", LOCATION_FIELDS, [
                {**l, "first_episode": story_id(todo_from)} for l in plan["locations"] if l["name"].lower() not in known])
            print(f'Showrunner: "{c["title"]}" ({c["genre"]}), {len(plan["chapters"])} acts, '
                  f'{len(plan["locations"])} locations, {len(plan["new_cast"])} characters to cast')

        if not plan["cast_done"]:
            everyone = read_csv("characters.csv")
            cast = [ch for ch in everyone if ch["series"] == str(number)]
            first_id = max((int(ch["character_id"][1:]) for ch in everyone), default=0) + 1
            if plan["new_cast"]:
                rows = ask(client, args.fixtures, "casting", CASTING_PROMPT, casting_prompt(plan, cast, first_id),
                           lambda d: check_cast(d, plan, cast, first_id, number))
                append_csv("characters.csv", CHARACTER_FIELDS, rows)
                print("Casting:  " + ", ".join(f"{r['character_id']} {r['name']} ({r['role']}, {r['voice_id']})" for r in rows))
            plan["cast_done"] = True
            save_plan(plan_path, plan)

        seasons = read_csv("seasons_plan.csv")
        for episode in range(todo_from, last + 1):
            sid = story_id(episode)
            stories = read_csv("stories.csv")
            cast = [ch for ch in read_csv("characters.csv") if ch["series"] == str(number)]
            locations = in_series(read_csv("locations.csv"), first, last, "first_episode")
            continuity = in_series(read_csv("continuity.csv"), first, last)
            if not any(s["story_id"] == sid for s in stories):
                outline = outline_for(plan, episode)
                bible = bible_text(sid, cast, locations, continuity, plan["concept"], series=number)
                prompt = build_user_prompt(episode, in_series(stories, first, last), read_csv("callbacks.csv"), seasons,
                                           cast, outline=outline, allow_new=False, bible=bible)

                def check_script(d, episode=episode, cast=cast, locations=locations, continuity=continuity):
                    story, scenes, cbs, _new, cont_rows, errors = ingest(d, episode, cast, allow_new=False,
                                                                         locations=locations, continuity=continuity,
                                                                         series=number)
                    return (story, scenes, cbs, cont_rows), errors

                feedback, review = "", None
                for rnd in range(EDIT_ROUNDS + 1):
                    story, scenes, cbs, cont_rows = ask(client, args.fixtures, f"writer_{sid}", WRITER_PROMPT, prompt,
                                                        check_script, feedback)
                    review = ask(client, args.fixtures, f"editor_{sid}", EDITOR_PROMPT,
                                 episode_review_prompt(story, scenes, cont_rows, outline, bible), check_review)
                    if review["approved"]:
                        break
                    feedback = "The story editor sent this episode back:\n" + "\n".join(review["issues"])
                story["status"] = "approved" if review["approved"] else "pending"
                if not review["approved"]:
                    plan["flagged"].append({"story_id": sid, "issues": review["issues"], "scores": review["scores"]})
                    save_plan(plan_path, plan)
                save(story, scenes, cbs, [], cont_rows)
                low = min(review["scores"].values()) if review["scores"] else 0
                verdict = "approved" if review["approved"] else "FLAGGED"
                print(f'Writer:   {sid} "{story["title"]}" - editor {verdict} (lowest score {low})')

            if not any(s["story_id"] == sid for s in read_csv("shots.csv")):
                story = next(s for s in read_csv("stories.csv") if s["story_id"] == sid)
                scenes = [s for s in read_csv("scenes.csv") if s["story_id"] == sid]
                shots = ask(client, args.fixtures, f"director_{sid}", DIRECTOR_PROMPT,
                            director_prompt(story, scenes, available_cast(cast, sid, number), locations),
                            lambda d, sid=sid, ids=[s["scene_id"] for s in scenes]: check_shots(d, sid, ids))
                append_csv("shots.csv", SHOT_FIELDS, shots)
                print(f"Director: {sid} {len(shots)} shots")
    except StepFailed as e:
        print(f"Stopped: {e}. Everything before this step is saved; rerun to continue.", file=sys.stderr)
        return 1
    finally:
        if plan_path.exists():
            from season_package import write_package
            print(f"Package:  {write_package(number)}")
    print(f"Series {number} written: episodes {todo_from}-{last}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
