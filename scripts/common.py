"""Shared constants and CSV helpers for The Box Debates pipeline."""
import csv
from pathlib import Path

SERIES = Path(__file__).resolve().parent.parent / "series"

STORY_FIELDS = ["story_id", "episode", "title", "status", "emotional_register", "life_lesson",
                "future_tease", "hook", "synopsis", "characters_used", "music_prompt"]
SCENE_FIELDS = ["story_id", "scene_id", "duration_s", "characters", "action", "camera",
                "speaker", "dialogue", "sfx", "transition"]
CALLBACK_FIELDS = ["setup_episode", "setup_moment", "payoff_episode", "payoff_moment"]

STATUSES = {"pending", "approved", "rendering", "done", "failed"}
REGISTERS = {
    "comedy_pure", "comedy_shallow", "comedy_heart", "comedy_paranoia", "comedy_growth",
    "tender_vulnerable", "tender_introspective", "tender_identity", "tender_safety",
    "tender_growth", "tender_fearful",
    "bittersweet_tender", "bittersweet_melancholy", "bittersweet_mortality",
    "tense_jealous", "tense_anxious", "tense_trauma",
    "emotional_climax_hopeful",
}
SCENES_PER_EPISODE = 12
SCENE_SECONDS = 5
MAX_DIALOGUE_WORDS = 12
TRANSITIONS = {"cut", "fade", "dissolve", "match_cut"}


def story_id(episode: int) -> str:
    return f"S{episode:04d}"


def read_csv(name: str) -> list[dict]:
    with open(SERIES / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_csv(name: str, fields: list[str], rows: list[dict]) -> None:
    with open(SERIES / name, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writerows(rows)


def season_for(episode: int, seasons: list[dict]) -> tuple[dict, int, bool]:
    """Return (season row, episode number within season, is_finale)."""
    start = 1
    for s in seasons:
        count = int(s["episode_count"])
        if episode < start + count:
            n = episode - start + 1
            return s, n, n == count
        start += count
    raise ValueError(f"episode {episode} is past the end of the series plan")
