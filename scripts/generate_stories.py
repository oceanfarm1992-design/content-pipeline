"""Draft new episodes with the OpenAI API and append them to series/*.csv as 'pending'.

    python scripts/generate_stories.py --count 3
    python scripts/generate_stories.py --from-json response.json   # ingest a saved response (no API call)

The cast grows with the story: an episode may introduce up to MAX_NEW_CHARACTERS new
characters (recurring or guest), which are appended to characters.csv with it.
Every episode is written against the story bible (locations, relationships, timeline,
open threads) and adds its own events and threads to series/continuity.csv.
"""
import argparse
import json
import os
import re
import sys

from common import (CALLBACK_FIELDS, CHARACTER_FIELDS, CONTINUITY_FIELDS, MAX_CHARACTERS_PER_SCENE,
                    MAX_NEW_CHARACTERS, REGISTERS, SCENE_FIELDS, SCENES_PER_EPISODE, STORY_FIELDS, TIMES_OF_DAY,
                    VOICES, append_csv, character_id, read_csv, season_for, story_id)
from validate_stories import check_character, check_episode

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
MAX_ATTEMPTS = 3
NEW_CHARACTER_KEYS = ["character_id", "name", "species", "role", "age", "description", "clothing", "traits",
                      "strengths", "weaknesses", "motivation", "backstory", "wound", "fear", "arc", "relationships",
                      "voice_id", "never_change", "negative"]
SCENE_KEYS = ["scene_id", "duration_s", "location", "time_of_day", "characters", "action", "emotion", "camera",
              "speaker", "dialogue", "sfx", "transition"]

SYSTEM_PROMPT = f"""You are the head writer of a photorealistic vertical (9:16) micro-drama series for TikTok and YouTube Shorts.
The series concept, characters and story so far are in the story bible you are given.
You think like a screenwriter, a drama director and an audience-retention strategist: every episode is a small,
complete beat people want to watch to the end, every scene moves it forward, and the episode ends on a hook.

FORMAT:
- Each episode = 60 seconds = exactly {SCENES_PER_EPISODE} scenes of 5 seconds (duration_s is always 5).
- Per scene: location (one of the story bible's locations, exact name), time_of_day (one of {", ".join(sorted(TIMES_OF_DAY))}),
  characters present, action (what happens, concrete physical actions), emotion (what the characters feel, shown through
  face, body language and action), camera (angle and simple movement), speaker + dialogue, sfx (background sounds), transition.
- emotional_register must be exactly one of: {", ".join(sorted(REGISTERS))}.
- transition is one of: cut, fade, dissolve, match_cut.
- Use bare ids only: characters "C001;C002", speaker "C001", scene_id 1..{SCENES_PER_EPISODE}, duration_s 5.

STORYTELLING:
- Open on a hook in scene 1; build to one emotional or dramatic turn; land the ending.
- life_lesson is shown through events, never stated aloud.
- Dialogue: max 12 words per scene, natural, in the speaker's voice, no explaining; silence and reactions are welcome.
- Use suspense, reveals and real cliffhangers only where they are earned, not every episode.
- future_tease: a one-line hint of a later episode or season, or "None". Plant one every 3-5 episodes.

PRODUCIBLE ON SCREEN:
- At most {MAX_CHARACTERS_PER_SCENE} characters per scene. One clear physical action per scene. No impossible camera moves.
- Characters look and dress exactly as in their bible entry unless the story deliberately changes it.
- No lip-sync: dialogue is heard as voice while the camera shows the listener's reaction, hands, a profile, a back or an
  over-the-shoulder view. Never write a long front-on close-up of someone speaking. Animals never talk on screen
  (a lead who hears animals hears them as voice-over; the animal's speaker is that animal's id).

CONTINUITY:
- Check the story bible before writing: timeline, relationships, clothing, open threads, due payoffs.
- Report continuity: "events" (1-3 facts this episode establishes), "opened" (new unresolved threads, if any),
  "resolved" (ids of open threads this episode resolves, if any).

CAST:
- Use the cast you are given; the series' main characters carry every episode.
- Introduce a new character when the story needs someone the cast lacks. At most {MAX_NEW_CHARACTERS} per episode,
  using exactly the new ids you are given, in order.
- role "recurring" if they will matter again in this series; "guest" if they appear in this episode only.
- description: concrete photographic appearance (species, breed or ethnicity, build, face, hair, colours, markings, age).
  clothing: what they wear. Adults look clearly adult.
- never_change: 2-4 visual traits that must stay identical in every scene, separated by ';'.
- negative: visual traits the artist must avoid for this character (comma-separated), or "".
- voice_id: one of {", ".join(sorted(VOICES))} (a=American, b=British, f=female, m=male). Do not reuse a voice already in the cast.

Return ONLY valid JSON with exactly these keys:
{{"stories": [{{"title","emotional_register","life_lesson","future_tease","hook","synopsis","characters_used","music_prompt"}}],
  "scenes": [{{{",".join(f'"{k}"' for k in SCENE_KEYS)}}} x {SCENES_PER_EPISODE}],
  "callbacks": [{{"setup_moment","payoff_episode","payoff_moment"}}],
  "continuity": {{"events": [], "opened": [], "resolved": []}},
  "new_characters": [{{{",".join(f'"{k}"' for k in NEW_CHARACTER_KEYS)}}}]}}
"stories" has exactly one entry. "callbacks" lists setups planted in THIS episode (payoff_episode is an integer episode number greater than this one); use [] if none. "new_characters" is [] if nobody new appears."""


def available_cast(cast: list[dict], sid: str, series=None) -> list[dict]:
    """Characters episode `sid` may use: in this series, debuted by then, and guests only in their own episode."""
    return [c for c in cast if (series is None or c["series"] == str(series)) and c["first_episode"] <= sid
            and (c["role"] != "guest" or c["first_episode"] == sid)]


def open_threads(continuity: list[dict]) -> dict[str, dict]:
    """thread_id -> the 'open' row, for threads not resolved yet."""
    opened = {r["thread_id"]: r for r in continuity if r["kind"] == "open"}
    for r in continuity:
        if r["kind"] == "resolved":
            opened.pop(r["thread_id"], None)
    return opened


def bible_text(sid: str, cast: list[dict], locations: list[dict], continuity: list[dict], concept: dict | None = None,
               series=None) -> str:
    """The story bible as the writer sees it before episode `sid` (cast and locations are already this series')."""
    lines = []
    if concept:
        lines.append(f"DRAMA: {concept.get('title', '')} - {concept.get('concept', '')} Genre: {concept.get('genre', '')}. "
                     f"Message: {concept.get('message', '')}. Central conflict: {concept.get('central_conflict', '')}. "
                     f"Ending: {concept.get('ending', '')}.")
    if locations:
        lines.append("LOCATIONS (use these exact names):")
        lines += [f"- {loc['name']}: {loc['description']} Lighting: {loc['lighting']}." for loc in locations
                  if loc["first_episode"] <= sid]
    rel = [c for c in available_cast(cast, sid, series) if c.get("relationships")]
    if rel:
        lines.append("RELATIONSHIPS:")
        lines += [f"- {c['name']}: {c['relationships']}" for c in rel]
    events = [r for r in continuity if r["kind"] == "event" and r["story_id"] < sid][-12:]
    if events:
        lines.append("TIMELINE (most recent events):")
        lines += [f"- {r['story_id']}: {r['note']}" for r in events]
    threads = open_threads([r for r in continuity if r["story_id"] < sid])
    if threads:
        lines.append("OPEN THREADS (put an id in 'resolved' when this episode resolves it):")
        lines += [f"- {tid} (since {r['story_id']}): {r['note']}" for tid, r in threads.items()]
    return "\n".join(lines)


def build_user_prompt(episode: int, stories: list[dict], callbacks: list[dict], seasons: list[dict],
                      cast: list[dict], outline: str = "", allow_new: bool = True, bible: str = "") -> str:
    sid = story_id(episode)
    season, n, finale = season_for(episode, seasons)
    lines = [
        f"Write episode {episode} (episode {n} of {season['episode_count']} in Season {season['season']}: {season['title']}).",
        f"Season logline: {season['logline']}",
        f"Season arc: {season['main_arc']}",
    ]
    if finale:
        lines.append(f"This is the SEASON FINALE. It must end on this cliffhanger: {season['finale_hook']}")
    if outline:
        lines.append(f"SHOWRUNNER OUTLINE (follow it):\n{outline}")
    if bible:
        lines.append(f"STORY BIBLE:\n{bible}")
    lines.append("Cast available in this episode:")
    lines += [f"- {c['character_id']} {c['name']} ({c['species']}, {c['role']}, age {c.get('age', '?')}, voice {c['voice_id']}): "
              f"{c['description']}. Wears: {c.get('clothing', '')}. Motivation: {c.get('motivation', '')}. "
              f"Wound: {c['wound']}." for c in available_cast(cast, sid, season["season"])]
    if allow_new:
        first_free = max((int(c["character_id"][1:]) for c in cast), default=0) + 1
        new_ids = [character_id(first_free + i) for i in range(MAX_NEW_CHARACTERS)]
        lines.append(f"Ids for new characters, if any: {', '.join(new_ids)}.")
    else:
        lines.append("The casting director has already cast this season: do not invent characters; new_characters must be [].")
    recent = sorted(stories, key=lambda s: int(s["episode"]))[-5:]
    if recent:
        lines.append("Recent episodes:")
        lines += [f"- Ep {s['episode']} \"{s['title']}\" ({s['emotional_register']}): {s['synopsis']}" for s in recent]
        lines.append("Do not repeat the emotional_register of the previous episode.")
    due = [c for c in callbacks if c["payoff_episode"] == story_id(episode)]
    if due:
        lines.append("PAYOFFS DUE in this episode:")
        lines += [f"- Planted in {c['setup_episode']}: {c['setup_moment']} -> pay off as: {c['payoff_moment']}" for c in due]
    return "\n".join(lines)


def _ids(value) -> str:
    """'C002 Unit-7;C003 Maya' or ['C002', ...] -> 'C002;C003'."""
    text = ";".join(map(str, value)) if isinstance(value, list) else str(value or "")
    return ";".join(dict.fromkeys(re.findall(r"C\d{3}", text)))


def _num(value) -> str:
    """'S0002_02' or '5s' -> '2' / '5' (last run of digits, leading zeros dropped)."""
    found = re.findall(r"\d+", str(value))
    return str(int(found[-1])) if found else str(value).strip()


def _text(value) -> str:
    return ";".join(map(str, value)) if isinstance(value, list) else str(value or "").strip()


def _slug(value) -> str:
    return re.sub(r"[\s\-]+", "_", str(value).strip().lower())


def ingest(data: dict, episode: int, cast: list[dict], allow_new: bool = True, locations: list[dict] | None = None,
           continuity: list[dict] | None = None, series=None):
    """Turn model JSON into CSV-ready rows. Returns (story, scenes, callbacks, new_characters, continuity, errors)."""
    sid = story_id(episode)
    first_free = max((int(c["character_id"][1:]) for c in cast), default=0) + 1
    continuity = continuity or []
    known_locations = {loc["name"].lower(): loc["name"] for loc in locations or [] if loc["first_episode"] <= sid}
    try:
        raw = data["stories"][0]
        story = {
            "story_id": sid, "episode": episode, "status": "pending",
            **{k: str(raw.get(k, "")).strip() for k in STORY_FIELDS[2:] if k != "status"},
            "characters_used": _ids(raw.get("characters_used")),
        }
        scenes = []
        for r in data["scenes"]:
            row = {k: str(r.get(k, "")).strip() for k in SCENE_FIELDS[1:]}
            row.update(scene_id=_num(r.get("scene_id")), duration_s=_num(r.get("duration_s")),
                       characters=_ids(r.get("characters")), speaker=_ids(r.get("speaker")),
                       time_of_day=_slug(r.get("time_of_day", "")))
            row["location"] = known_locations.get(row["location"].lower(), row["location"])
            scenes.append({"story_id": sid, **row})
        cbs = [{
            "setup_episode": sid, "setup_moment": str(c["setup_moment"]).strip(),
            "payoff_episode": story_id(int(c["payoff_episode"])), "payoff_moment": str(c["payoff_moment"]).strip(),
        } for c in data.get("callbacks", [])]
        new_chars = []
        for i, c in enumerate(data.get("new_characters") or []):
            row = {k: _text(c.get(k)) for k in NEW_CHARACTER_KEYS}
            row.update(character_id=_ids(row["character_id"]) or row["character_id"],
                       role=row["role"].lower(), first_episode=sid, series=str(series or ""))
            row["reference_sheet"] = f"series/refs/{row['character_id']}.png"
            row["_expected_id"] = character_id(first_free + i)
            new_chars.append(row)
        cont = data.get("continuity") or {}
        next_thread = 1 + max((int(r["thread_id"][1:]) for r in continuity if r["thread_id"]), default=0)
        cont_rows = [{"story_id": sid, "kind": "event", "thread_id": "", "note": str(n).strip()}
                     for n in cont.get("events") or []]
        cont_rows += [{"story_id": sid, "kind": "open", "thread_id": f"T{next_thread + i:03d}", "note": str(n).strip()}
                      for i, n in enumerate(cont.get("opened") or [])]
        resolved = [re.findall(r"T\d{3}", str(t)) for t in cont.get("resolved") or []]
        cont_rows += [{"story_id": sid, "kind": "resolved", "thread_id": t[0], "note": ""} for t in resolved if t]
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
        return {}, [], [], [], [], [f"{sid}: malformed response ({type(e).__name__}: {e})"]

    errors = []
    if new_chars and not allow_new:
        errors.append(f"{sid}: new_characters must be [] (the season is already cast)")
    if len(new_chars) > MAX_NEW_CHARACTERS:
        errors.append(f"{sid}: {len(new_chars)} new characters (max {MAX_NEW_CHARACTERS})")
    used_voices = {c["voice_id"] for c in cast}
    for c in new_chars:
        expected = c.pop("_expected_id")
        if c["character_id"] != expected:
            errors.append(f"{sid}: new character '{c['name']}' must use id {expected}, got '{c['character_id']}'")
        if c["voice_id"] in used_voices:
            errors.append(f"{sid}: new character {c['character_id']} reuses voice {c['voice_id']}")
        used_voices.add(c["voice_id"])
        errors += [f"{sid}: {e}" for e in check_character(c)]

    allowed = {c["character_id"] for c in available_cast(cast, sid, series)} | {c["character_id"] for c in new_chars}
    errors += check_episode(story, scenes, allowed)
    if known_locations:
        errors += [f"{sid} scene {s['scene_id']}: location '{s['location']}' is not in the story bible "
                   f"({', '.join(known_locations.values())})" for s in scenes if s["location"] not in known_locations.values()]
    errors += [f"{sid}: callback payoff {c['payoff_episode']} must come after this episode"
               for c in cbs if c["payoff_episode"] <= sid]
    if not any(r["kind"] == "event" for r in cont_rows):
        errors.append(f"{sid}: continuity.events needs at least one fact this episode establishes")
    still_open = open_threads(continuity)
    errors += [f"{sid}: resolved thread {r['thread_id']} is not open" for r in cont_rows
               if r["kind"] == "resolved" and r["thread_id"] not in still_open]
    return story, scenes, cbs, new_chars, cont_rows, errors


def save(story: dict, scenes: list[dict], cbs: list[dict], new_chars: list[dict], cont_rows: list[dict] = ()) -> None:
    append_csv("characters.csv", CHARACTER_FIELDS, new_chars)
    append_csv("stories.csv", STORY_FIELDS, [story])
    append_csv("scenes.csv", SCENE_FIELDS, scenes)
    append_csv("callbacks.csv", CALLBACK_FIELDS, cbs)
    append_csv("continuity.csv", CONTINUITY_FIELDS, list(cont_rows))


def call_api(client, user_prompt: str, feedback: str = "", system: str = SYSTEM_PROMPT) -> dict:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user_prompt}]
    if feedback:
        messages.append({"role": "user", "content": f"Your previous draft was rejected:\n{feedback}\nFix these and return the full JSON again."})
    resp = client.chat.completions.create(
        model=MODEL, messages=messages, response_format={"type": "json_object"}, temperature=0.9,
    )
    return json.loads(resp.choices[0].message.content)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--from-json", help="ingest this saved response as the next episode instead of calling the API")
    args = ap.parse_args()

    seasons = read_csv("seasons_plan.csv")
    total = sum(int(s["episode_count"]) for s in seasons)
    client = None
    made = 0

    for _ in range(1 if args.from_json else args.count):
        stories, callbacks, cast = read_csv("stories.csv"), read_csv("callbacks.csv"), read_csv("characters.csv")
        locations, continuity = read_csv("locations.csv"), read_csv("continuity.csv")
        episode = max((int(s["episode"]) for s in stories), default=0) + 1
        if episode > total:
            print(f"Series plan complete ({total} episodes); nothing more to write.")
            break
        sid = story_id(episode)
        series = season_for(episode, seasons)[0]["season"]

        def attempt(data):
            return ingest(data, episode, cast, locations=locations, continuity=continuity, series=series)

        if args.from_json:
            with open(args.from_json, encoding="utf-8") as f:
                story, scenes, cbs, new_chars, cont_rows, errors = attempt(json.load(f))
        else:
            if client is None:
                from openai import OpenAI
                client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
            prompt = build_user_prompt(episode, stories, callbacks, seasons, cast,
                                       bible=bible_text(sid, cast, locations, continuity, series=series))
            feedback = ""
            for n in range(1, MAX_ATTEMPTS + 1):
                story, scenes, cbs, new_chars, cont_rows, errors = attempt(call_api(client, prompt, feedback))
                if not errors:
                    break
                print(f"Episode {episode} attempt {n} rejected: {len(errors)} error(s)", file=sys.stderr)
                feedback = "\n".join(errors[:15])

        if errors:
            print("\n".join(errors), file=sys.stderr)
            print(f"Giving up on episode {episode}; nothing written for it.", file=sys.stderr)
            return 1 if made == 0 else 0
        save(story, scenes, cbs, new_chars, cont_rows)
        made += 1
        added = ", ".join(f"{c['character_id']} {c['name']} ({c['role']})" for c in new_chars)
        print(f"Wrote {story['story_id']} \"{story['title']}\" ({story['emotional_register']}) as pending"
              + (f"; new cast: {added}" if added else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
