"""Draft new episodes with the OpenAI API and append them to series/*.csv as 'pending'.

    python scripts/generate_stories.py --count 3
    python scripts/generate_stories.py --from-json response.json   # ingest a saved response (no API call)

The cast grows with the story: an episode may introduce up to MAX_NEW_CHARACTERS new
characters (recurring or guest), which are appended to characters.csv with it.
"""
import argparse
import json
import os
import re
import sys

from common import (CALLBACK_FIELDS, CHARACTER_FIELDS, MAX_NEW_CHARACTERS, REGISTERS, SCENE_FIELDS,
                    SCENES_PER_EPISODE, STORY_FIELDS, VOICES, append_csv, character_id, read_csv,
                    season_for, story_id)
from validate_stories import check_character, check_episode

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
MAX_ATTEMPTS = 3
NEW_CHARACTER_KEYS = ["character_id", "name", "species", "role", "description", "traits", "backstory",
                      "wound", "fear", "arc", "voice_id", "never_change", "negative"]

SYSTEM_PROMPT = f"""You are the head writer for "The Box Debates," a serialized animated drama about two broken cats and a lonely human learning to be a family.

RULES:
- Each episode = 60 seconds = exactly {SCENES_PER_EPISODE} scenes of 5 seconds (duration_s is always 5).
- Max 12 words of dialogue per scene. Dialogue may be empty. If a scene has dialogue, speaker is the character_id who says it.
- emotional_register must be exactly one of: {", ".join(sorted(REGISTERS))}.
- life_lesson is shown through events, never stated aloud in dialogue.
- future_tease: a one-line hint of a later episode or season, or "None". Plant one every 3-5 episodes.
- Honour the continuity notes, due payoffs, and season position you are given.
- transition is one of: cut, fade, dissolve, match_cut.
- Use bare ids only: characters "C001;C002", speaker "C001", scene_id 1..{SCENES_PER_EPISODE}, duration_s 5.

CAST:
- Use the cast you are given. Mango, Shadow and The Human anchor every season.
- Introduce a new character when the story needs someone the cast lacks (a vet, the former owner, a kitten,
  a neighbour's dog...). At most {MAX_NEW_CHARACTERS} per episode, using exactly the new ids you are given, in order.
- role "recurring" if they will matter again this season or later; "guest" if they appear in this episode only.
- description: concrete visual design for an anime artist (species, build, colours, markings, clothing).
  Adults look clearly adult. The Human's face is never shown.
- never_change: 2-4 visual traits that must stay identical in every scene, separated by ';'.
- negative: visual traits the artist must avoid for this character (comma-separated), or "".
- voice_id: one of {", ".join(sorted(VOICES))} (a=American, b=British, f=female, m=male). Do not reuse a voice already in the cast.

Return ONLY valid JSON with exactly these keys:
{{"stories": [{{"title","emotional_register","life_lesson","future_tease","hook","synopsis","characters_used","music_prompt"}}],
  "scenes": [{{"scene_id","duration_s","characters","action","camera","speaker","dialogue","sfx","transition"}} x {SCENES_PER_EPISODE}],
  "callbacks": [{{"setup_moment","payoff_episode","payoff_moment"}}],
  "new_characters": [{{{",".join(f'"{k}"' for k in NEW_CHARACTER_KEYS)}}}]}}
"stories" has exactly one entry. "callbacks" lists setups planted in THIS episode (payoff_episode is an integer episode number greater than this one); use [] if none. "new_characters" is [] if nobody new appears."""


def available_cast(cast: list[dict], sid: str) -> list[dict]:
    """Characters episode `sid` may use: debuted by then, and guests only in their own episode."""
    return [c for c in cast if c["first_episode"] <= sid and (c["role"] != "guest" or c["first_episode"] == sid)]


def build_user_prompt(episode: int, stories: list[dict], callbacks: list[dict], seasons: list[dict],
                      cast: list[dict], outline: str = "", allow_new: bool = True) -> str:
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
    lines.append("Cast available in this episode:")
    lines += [f"- {c['character_id']} {c['name']} ({c['species']}, {c['role']}, voice {c['voice_id']}): "
              f"{c['description']}. {c['backstory']}. Wound: {c['wound']}." for c in available_cast(cast, sid)]
    if allow_new:
        first_free = max(int(c["character_id"][1:]) for c in cast) + 1
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
    """'C002 Shadow;C003 The Human' or ['C002', ...] -> 'C002;C003'."""
    text = ";".join(map(str, value)) if isinstance(value, list) else str(value or "")
    return ";".join(dict.fromkeys(re.findall(r"C\d{3}", text)))


def _num(value) -> str:
    """'S0002_02' or '5s' -> '2' / '5' (last run of digits, leading zeros dropped)."""
    found = re.findall(r"\d+", str(value))
    return str(int(found[-1])) if found else str(value).strip()


def _text(value) -> str:
    return ";".join(map(str, value)) if isinstance(value, list) else str(value or "").strip()


def ingest(data: dict, episode: int, cast: list[dict],
           allow_new: bool = True) -> tuple[dict, list[dict], list[dict], list[dict], list[str]]:
    """Turn model JSON into CSV-ready rows. Returns (story, scenes, callbacks, new_characters, errors)."""
    sid = story_id(episode)
    first_free = max(int(c["character_id"][1:]) for c in cast) + 1
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
                       characters=_ids(r.get("characters")), speaker=_ids(r.get("speaker")))
            scenes.append({"story_id": sid, **row})
        cbs = [{
            "setup_episode": sid, "setup_moment": str(c["setup_moment"]).strip(),
            "payoff_episode": story_id(int(c["payoff_episode"])), "payoff_moment": str(c["payoff_moment"]).strip(),
        } for c in data.get("callbacks", [])]
        new_chars = []
        for i, c in enumerate(data.get("new_characters") or []):
            row = {k: _text(c.get(k)) for k in NEW_CHARACTER_KEYS}
            row.update(character_id=_ids(row["character_id"]) or row["character_id"],
                       role=row["role"].lower(), first_episode=sid)
            row["reference_sheet"] = f"series/refs/{row['character_id']}.png"
            row["_expected_id"] = character_id(first_free + i)
            new_chars.append(row)
    except (KeyError, IndexError, TypeError, ValueError) as e:
        return {}, [], [], [], [f"{sid}: malformed response ({type(e).__name__}: {e})"]

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

    allowed = {c["character_id"] for c in available_cast(cast, sid)} | {c["character_id"] for c in new_chars}
    errors += check_episode(story, scenes, allowed)
    errors += [f"{sid}: callback payoff {c['payoff_episode']} must come after this episode"
               for c in cbs if c["payoff_episode"] <= sid]
    return story, scenes, cbs, new_chars, errors


def save(story: dict, scenes: list[dict], cbs: list[dict], new_chars: list[dict]) -> None:
    append_csv("characters.csv", CHARACTER_FIELDS, new_chars)
    append_csv("stories.csv", STORY_FIELDS, [story])
    append_csv("scenes.csv", SCENE_FIELDS, scenes)
    append_csv("callbacks.csv", CALLBACK_FIELDS, cbs)


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
        episode = max((int(s["episode"]) for s in stories), default=0) + 1
        if episode > total:
            print(f"Series plan complete ({total} episodes); nothing more to write.")
            break

        if args.from_json:
            with open(args.from_json, encoding="utf-8") as f:
                data = json.load(f)
            story, scenes, cbs, new_chars, errors = ingest(data, episode, cast)
        else:
            if client is None:
                from openai import OpenAI
                client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
            prompt, feedback = build_user_prompt(episode, stories, callbacks, seasons, cast), ""
            for attempt in range(1, MAX_ATTEMPTS + 1):
                story, scenes, cbs, new_chars, errors = ingest(call_api(client, prompt, feedback), episode, cast)
                if not errors:
                    break
                print(f"Episode {episode} attempt {attempt} rejected: {len(errors)} error(s)", file=sys.stderr)
                feedback = "\n".join(errors[:15])

        if errors:
            print("\n".join(errors), file=sys.stderr)
            print(f"Giving up on episode {episode}; nothing written for it.", file=sys.stderr)
            return 1 if made == 0 else 0
        save(story, scenes, cbs, new_chars)
        made += 1
        added = ", ".join(f"{c['character_id']} {c['name']} ({c['role']})" for c in new_chars)
        print(f"Wrote {story['story_id']} \"{story['title']}\" ({story['emotional_register']}) as pending"
              + (f"; new cast: {added}" if added else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
