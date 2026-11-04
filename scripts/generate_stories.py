"""Draft new episodes with the OpenAI API and append them to series/*.csv as 'pending'.

    python scripts/generate_stories.py --count 10
    python scripts/generate_stories.py --from-json response.json   # ingest a saved response (no API call)
"""
import argparse
import json
import os
import sys

from common import (CALLBACK_FIELDS, REGISTERS, SCENE_FIELDS, SCENES_PER_EPISODE, STORY_FIELDS,
                    append_csv, read_csv, season_for, story_id)
from validate_stories import check_episode

MODEL = "gpt-4o-mini"
MAX_ATTEMPTS = 3

SYSTEM_PROMPT = f"""You are the head writer for "The Box Debates," a serialized animated drama about two broken cats and a lonely human learning to be a family.

CHARACTERS:
- C001 Mango: former show cat, surrendered when his owner married. Wound: abandonment. Dramatic, lazy, food-motivated.
- C002 Shadow: born feral, caged 3 months. Wound: betrayal. Sarcastic, clever, aloof.
- C003 The Human: works from home, lonely. Wound: loneliness. Face is never shown.

RULES:
- Each episode = 60 seconds = exactly {SCENES_PER_EPISODE} scenes of 5 seconds (duration_s is always 5).
- Max 12 words of dialogue per scene. Dialogue may be empty. If a scene has dialogue, speaker is the character_id who says it.
- emotional_register must be exactly one of: {", ".join(sorted(REGISTERS))}.
- life_lesson is shown through events, never stated aloud in dialogue.
- future_tease: a one-line hint of a later episode or season, or "None". Plant one every 3-5 episodes.
- Honour the continuity notes, due payoffs, and season position you are given.
- transition is one of: cut, fade, dissolve, match_cut. Character ids in scenes are separated by ';'.

Return ONLY valid JSON with exactly these keys:
{{"stories": [{{"title","emotional_register","life_lesson","future_tease","hook","synopsis","characters_used","music_prompt"}}],
  "scenes": [{{"scene_id","duration_s","characters","action","camera","speaker","dialogue","sfx","transition"}} x {SCENES_PER_EPISODE}],
  "callbacks": [{{"setup_moment","payoff_episode","payoff_moment"}}]}}
"stories" has exactly one entry. "callbacks" lists setups planted in THIS episode (payoff_episode is an integer episode number greater than this one); use [] if none."""


def build_user_prompt(episode: int, stories: list[dict], callbacks: list[dict], seasons: list[dict]) -> str:
    season, n, finale = season_for(episode, seasons)
    lines = [
        f"Write episode {episode} (episode {n} of {season['episode_count']} in Season {season['season']}: {season['title']}).",
        f"Season logline: {season['logline']}",
        f"Season arc: {season['main_arc']}",
    ]
    if finale:
        lines.append(f"This is the SEASON FINALE. It must end on this cliffhanger: {season['finale_hook']}")
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


def ingest(data: dict, episode: int, chars: set[str]) -> tuple[dict, list[dict], list[dict], list[str]]:
    """Turn model JSON into CSV-ready rows. Returns (story, scenes, callbacks, errors)."""
    sid = story_id(episode)
    try:
        raw = data["stories"][0]
        story = {
            "story_id": sid, "episode": episode, "status": "pending",
            **{k: str(raw.get(k, "")).strip() for k in STORY_FIELDS[2:] if k != "status"},
        }
        if isinstance(raw.get("characters_used"), list):
            story["characters_used"] = ";".join(raw["characters_used"])
        scenes = []
        for r in data["scenes"]:
            row = {k: str(r.get(k, "")).strip() for k in SCENE_FIELDS[1:]}
            if isinstance(r.get("characters"), list):
                row["characters"] = ";".join(r["characters"])
            scenes.append({"story_id": sid, **row})
        cbs = [{
            "setup_episode": sid, "setup_moment": str(c["setup_moment"]).strip(),
            "payoff_episode": story_id(int(c["payoff_episode"])), "payoff_moment": str(c["payoff_moment"]).strip(),
        } for c in data.get("callbacks", [])]
    except (KeyError, IndexError, TypeError, ValueError) as e:
        return {}, [], [], [f"{sid}: malformed response ({type(e).__name__}: {e})"]
    errors = check_episode(story, scenes, chars)
    errors += [f"{sid}: callback payoff {c['payoff_episode']} must come after this episode"
               for c in cbs if c["payoff_episode"] <= sid]
    return story, scenes, cbs, errors


def save(story: dict, scenes: list[dict], cbs: list[dict]) -> None:
    append_csv("stories.csv", STORY_FIELDS, [story])
    append_csv("scenes.csv", SCENE_FIELDS, scenes)
    append_csv("callbacks.csv", CALLBACK_FIELDS, cbs)


def call_api(client, user_prompt: str, feedback: str = "") -> dict:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}]
    if feedback:
        messages.append({"role": "user", "content": f"Your previous draft was rejected:\n{feedback}\nFix these and return the full JSON again."})
    resp = client.chat.completions.create(
        model=MODEL, messages=messages, response_format={"type": "json_object"}, temperature=0.9,
    )
    return json.loads(resp.choices[0].message.content)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--from-json", help="ingest this saved response as the next episode instead of calling the API")
    args = ap.parse_args()

    chars = {c["character_id"] for c in read_csv("characters.csv")}
    seasons = read_csv("seasons_plan.csv")
    total = sum(int(s["episode_count"]) for s in seasons)
    client = None
    made = 0

    for _ in range(1 if args.from_json else args.count):
        stories, callbacks = read_csv("stories.csv"), read_csv("callbacks.csv")
        episode = max((int(s["episode"]) for s in stories), default=0) + 1
        if episode > total:
            print(f"Series plan complete ({total} episodes); nothing more to write.")
            break

        if args.from_json:
            with open(args.from_json, encoding="utf-8") as f:
                data = json.load(f)
            story, scenes, cbs, errors = ingest(data, episode, chars)
        else:
            if client is None:
                from openai import OpenAI
                client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
            prompt, feedback = build_user_prompt(episode, stories, callbacks, seasons), ""
            for attempt in range(1, MAX_ATTEMPTS + 1):
                story, scenes, cbs, errors = ingest(call_api(client, prompt, feedback), episode, chars)
                if not errors:
                    break
                print(f"Episode {episode} attempt {attempt} rejected: {len(errors)} error(s)", file=sys.stderr)
                feedback = "\n".join(errors[:15])

        if errors:
            print("\n".join(errors), file=sys.stderr)
            print(f"Giving up on episode {episode}; nothing written for it.", file=sys.stderr)
            return 1 if made == 0 else 0
        save(story, scenes, cbs)
        made += 1
        print(f"Wrote {story['story_id']} \"{story['title']}\" ({story['emotional_register']}) as pending")
    return 0


if __name__ == "__main__":
    sys.exit(main())
