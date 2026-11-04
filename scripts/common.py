"""Shared constants and CSV helpers for the micro-drama production pipeline."""
import csv
from pathlib import Path

SERIES = Path(__file__).resolve().parent.parent / "series"

STORY_FIELDS = ["story_id", "episode", "title", "status", "emotional_register", "life_lesson",
                "future_tease", "hook", "synopsis", "characters_used", "music_prompt"]
SCENE_FIELDS = ["story_id", "scene_id", "duration_s", "location", "time_of_day", "characters", "action", "emotion",
                "camera", "speaker", "dialogue", "sfx", "transition"]
CALLBACK_FIELDS = ["setup_episode", "setup_moment", "payoff_episode", "payoff_moment"]
# The character bible: appearance and clothing stay fixed unless the story changes them.
CHARACTER_FIELDS = ["character_id", "name", "species", "role", "series", "first_episode", "age", "description", "clothing",
                    "traits", "strengths", "weaknesses", "motivation", "backstory", "wound", "fear", "arc",
                    "relationships", "voice_id", "never_change", "negative", "reference_sheet"]
# Story bible: recurring sets, and a continuity log of what happened and which threads are open.
LOCATION_FIELDS = ["name", "first_episode", "description", "lighting"]
CONTINUITY_FIELDS = ["story_id", "kind", "thread_id", "note"]
CONTINUITY_KINDS = {"event", "open", "resolved"}
TIMES_OF_DAY = {"dawn", "morning", "midday", "afternoon", "golden_hour", "evening", "night", "late_night"}
MAX_CHARACTERS_PER_SCENE = 3   # more than this per shot breaks image/video consistency
ACTS = ["Introduction", "Conflict", "Climax", "Resolution"]

CHAPTER_FIELDS = ["season", "chapter", "title", "first_episode", "last_episode", "summary"]
SHOT_FIELDS = ["story_id", "scene_id", "shot_size", "angle", "movement", "lighting", "image_prompt", "motion_prompt"]
SHOT_SIZES = {"extreme_wide", "wide", "medium", "medium_close", "close_up", "extreme_close_up",
              "over_the_shoulder", "insert"}
# Camera moves the renderer can do: on a still (pan/zoom) or as a Wan motion prompt.
MOVEMENTS = {"static", "slow_push_in", "slow_pull_out", "pan_left", "pan_right", "tilt_up", "tilt_down",
             "tracking", "handheld"}

SERIES_EPISODES = 30   # one standalone series per month, one 60 s episode per day

# Every character belongs to one series. main: the series' leads. recurring: may return. guest: one episode.
ROLES = {"main", "recurring", "guest"}
MAX_NEW_CHARACTERS = 2
# Kokoro voices the writer may cast from (a = American, b = British; f = female, m = male).
VOICES = {
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole", "af_nova",
    "af_river", "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam",
    "am_michael", "am_onyx", "am_puck", "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
}

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


def character_id(n: int) -> str:
    return f"C{n:03d}"


def read_csv(name: str) -> list[dict]:
    with open(SERIES / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(name: str, fields: list[str], rows: list[dict]) -> None:
    with open(SERIES / name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


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


def series_slot(episode: int, seasons: list[dict]) -> tuple[dict | None, int, int, int]:
    """(plan row or None, series number, first episode, last episode) for the series containing `episode`.
    Past the end of seasons_plan.csv the next series is unplanned (None): the showrunner develops it."""
    start = 1
    for s in seasons:
        count = int(s["episode_count"])
        if episode < start + count:
            return s, int(s["season"]), start, start + count - 1
        start += count
    return None, len(seasons) + 1, start, start + SERIES_EPISODES - 1
