"""Build the production-ready season package: series/seasons/season_<n>.md.

    python scripts/season_package.py --season 1

One document to review a season before merging it: drama concept, character bible, outline by act,
full screenplay, visual prompts, sound and music, continuity, and a production readiness check.
Built only from the series/ files (no API calls).
"""
import argparse
import json
import sys

from common import SCENES_PER_EPISODE, SERIES, read_csv, story_id

OUT = SERIES / "seasons"


def _cell(text: str) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def write_package(season_no: int) -> str:
    plan = json.loads((SERIES / "season_plans" / f"season_{season_no}.json").read_text(encoding="utf-8"))
    first = min(c["first_episode"] for c in plan["chapters"])
    last = max(c["last_episode"] for c in plan["chapters"])
    ids = [story_id(n) for n in range(first, last + 1)]
    stories = {s["story_id"]: s for s in read_csv("stories.csv") if s["story_id"] in ids}
    scenes, shots = {}, {}
    for s in read_csv("scenes.csv"):
        scenes.setdefault(s["story_id"], []).append(s)
    for s in read_csv("shots.csv"):
        shots.setdefault(s["story_id"], {})[s["scene_id"]] = s
    cast = [c for c in read_csv("characters.csv") if c["series"] == str(season_no)]
    names = {c["character_id"]: c["name"] for c in cast}
    continuity = [r for r in read_csv("continuity.csv") if r["story_id"] in ids]
    locations = [l for l in read_csv("locations.csv") if ids[0] <= l["first_episode"] <= ids[-1]]
    flagged = {f["story_id"]: f for f in plan.get("flagged", [])}
    c = plan["concept"]

    md = [f"# Series {season_no}: {c['title']}", "",
          f"Episodes {ids[0]}-{ids[-1]} · {len(stories)}/{len(ids)} written · "
          f"{sum(1 for s in stories.values() if s['status'] == 'approved')} approved by the story editor", ""]

    md += ["## 1. Drama concept", "", "| | |", "|---|---|"]
    md += [f"| {label} | {_cell(c[key])} |" for label, key in
           (("Concept", "concept"), ("Genre", "genre"), ("Audience", "audience"), ("Message", "message"),
            ("Central conflict", "central_conflict"), ("Ending", "ending"))]
    review = plan.get("review") or {}
    if review:
        md += ["", f"Outline review: **{'approved' if review.get('approved') else 'not approved'}** - "
               + ", ".join(f"{k} {v}" for k, v in review.get("scores", {}).items())]

    md += ["", "## 2. Character bible", ""]
    for ch in cast:
        if ch["role"] == "guest" and ch["first_episode"] not in ids:
            continue
        md += [f"### {ch['name']} ({ch['character_id']}, {ch['role']}, from {ch['first_episode']})", "",
               f"- **Species / age:** {ch['species']}, {ch['age']}",
               f"- **Appearance:** {ch['description']}",
               f"- **Clothing:** {ch['clothing']}",
               f"- **Never changes:** {ch['never_change'].replace(';', ', ')}",
               f"- **Personality:** {ch['traits'].replace(';', ', ')}",
               f"- **Strengths / weaknesses:** {ch['strengths'].replace(';', ', ')} / {ch['weaknesses'].replace(';', ', ')}",
               f"- **Motivation:** {ch['motivation']}",
               f"- **Background:** {ch['backstory']} Wound: {ch['wound']}. Fear: {ch['fear']}. Arc: {ch['arc']}.",
               f"- **Relationships:** {ch['relationships'].replace(';', '; ')}",
               f"- **Voice:** {ch['voice_id']} · reference: `{ch['reference_sheet']}`", ""]

    md += ["## 3. Story outline", ""]
    for act in plan["chapters"]:
        md += [f"### Act {act['chapter']}: {act['title']} (episodes {act['first_episode']}-{act['last_episode']})", "",
               act["summary"], ""]
        md += [f"{e['episode']}. **{e['title']}** _{e['emotional_register']}_ - {e['beat']}" for e in plan["episodes"]
               if act["first_episode"] <= e["episode"] <= act["last_episode"]]
        md.append("")

    md += ["## 4. Screenplay and scene breakdown", ""]
    for sid in ids:
        s = stories.get(sid)
        if not s:
            md += [f"### {sid} - not written yet", ""]
            continue
        flag = " ⚠ flagged by the story editor" if sid in flagged else ""
        md += [f"### {sid}: {s['title']}{flag}", "",
               f"_{s['emotional_register']}_ · **Hook:** {s['hook']}", "",
               f"{s['synopsis']}", "", f"**Lesson (shown, never said):** {s['life_lesson']}"
               + (f" · **Tease:** {s['future_tease']}" if s["future_tease"] not in ("", "None") else ""), ""]
        if sid in flagged:
            md += ["> Editor issues: " + "; ".join(flagged[sid]["issues"]), ""]
        for sc in sorted(scenes.get(sid, []), key=lambda r: int(r["scene_id"])):
            who = ", ".join(names.get(x, x) for x in sc["characters"].split(";") if x)
            md += [f"**SCENE {sc['scene_id']} - {sc['location'].upper()} - {sc['time_of_day'].replace('_', ' ').upper()}** "
                   f"({sc['duration_s']} s) · {who}", "",
                   f"{sc['action']} _({sc['emotion']})_"]
            if sc["dialogue"]:
                md.append(f"> **{names.get(sc['speaker'], sc['speaker']).upper()}:** {sc['dialogue']}")
            md += [f"Camera: {sc['camera']} · Sound: {sc['sfx'] or '-'} · Transition: {sc['transition']}", ""]

    md += ["## 5. Visual prompts", ""]
    for sid in ids:
        if sid not in shots:
            continue
        md += [f"### {sid}", "", "| # | Shot | Angle | Move | Lighting | Image prompt | Motion |", "|---|---|---|---|---|---|---|"]
        md += [f"| {n} | {sh['shot_size']} | {_cell(sh['angle'])} | {sh['movement']} | {_cell(sh['lighting'])} | "
               f"{_cell(sh['image_prompt'])} | {_cell(sh['motion_prompt'])} |"
               for n, sh in sorted(shots[sid].items(), key=lambda kv: int(kv[0]))]
        md.append("")

    md += ["## 6. Sound and music", "", "| Episode | Music | Sound effects |", "|---|---|---|"]
    md += [f"| {sid} | {_cell(stories[sid]['music_prompt'])} | "
           f"{_cell('; '.join(dict.fromkeys(sc['sfx'] for sc in scenes.get(sid, []) if sc['sfx'])))} |"
           for sid in ids if sid in stories]

    md += ["", "## 7. Continuity", "", "**Locations:** " + "; ".join(f"{l['name']} - {l['description']} ({l['lighting']})"
                                                                     for l in locations), ""]
    md += [f"- {r['story_id']}: {r['note']}" for r in continuity if r["kind"] == "event"]
    resolved = {r["thread_id"]: r["story_id"] for r in continuity if r["kind"] == "resolved"}
    threads = [r for r in continuity if r["kind"] == "open"]
    if threads:
        md += ["", "**Threads:**"]
        md += [f"- {r['thread_id']} opened {r['story_id']}: {r['note']} - "
               + (f"resolved {resolved[r['thread_id']]}" if r["thread_id"] in resolved else "**still open**") for r in threads]

    md += ["", "## 8. Production readiness", "", "| Episode | Status | Scenes | Shots | Ready |", "|---|---|---|---|---|"]
    for sid in ids:
        s = stories.get(sid)
        n_sc, n_sh = len(scenes.get(sid, [])), len(shots.get(sid, {}))
        ready = bool(s) and s["status"] == "approved" and n_sc == n_sh == SCENES_PER_EPISODE
        md.append(f"| {sid} | {s['status'] if s else 'missing'} | {n_sc} | {n_sh} | {'yes' if ready else 'no'} |")
    missing_refs = [ch["name"] for ch in cast if not (SERIES.parent / ch["reference_sheet"]).exists()]
    if missing_refs:
        md += ["", f"Reference sheets still to render: {', '.join(missing_refs)} (`render_refs.py --missing`)."]

    OUT.mkdir(exist_ok=True)
    path = OUT / f"season_{season_no}.md"
    path.write_text("\n".join(md) + "\n", encoding="utf-8")
    return str(path.relative_to(SERIES.parent).as_posix())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, required=True)
    print(write_package(ap.parse_args().season))
    sys.exit(0)
