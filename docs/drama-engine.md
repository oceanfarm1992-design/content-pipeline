# AI Drama Development Engine

The writing contract for every series on the channel (see series/channel_brief.md). `scripts/write_season.py` implements it; this table says where.

| Section | Implemented in |
|---|---|
| 2 Story development (title, concept, genre, audience, message, conflict, ending) | Showrunner pass → `concept` in `series/season_plans/season_<n>.json` |
| 3 Character development (age, appearance, clothing, personality, background, strengths, weaknesses, motivation, relationships) | Casting pass → `series/characters.csv` |
| 4 Four-act structure | Showrunner: exactly 4 chapters = Acts 1–4 → `series/chapters.csv` |
| 5 Scene development (location, time of day, characters, action, emotion, camera, dialogue, sound, duration, transition) | Screenwriter → `series/scenes.csv`; film director → `series/shots.csv` |
| 6–8 Dialogue, retention, visual storytelling | Writer rules in `generate_stories.SYSTEM_PROMPT`; validator: ≤12 words, ≤3 characters per scene |
| 9 Continuity / story bible | `series/locations.csv`, `series/continuity.csv` (events, open/resolved threads), shown to every writer call |
| 10 Final output | `series/seasons/season_<n>.md` (`scripts/season_package.py`) |
| 11 Quality control | Story editor pass on the outline and every episode; rejected work is rewritten, and still-weak episodes stay `pending` |

---

## 1. Objective

Develop original, engaging dramas that audiences want to watch until the end. Think like a professional
screenwriter, a drama director, a character development specialist, a storytelling expert and an audience
retention strategist. Do not simply generate random scenes: develop a complete story with a beginning, middle and ending.

## 2. Story development

For every topic or idea, first develop: drama title; main story concept; genre (emotional, family, romance, thriller,
mystery, adventure, comedy, etc.); target audience; main message; central conflict; ending concept.

## 3. Character development

Create detailed characters before writing scenes. For every character define: name, age, physical appearance,
clothing, personality, background, strengths and weaknesses, motivations, relationships with other characters.
Keep exactly the same appearance, clothing and personality throughout unless the story requires a change.

## 4. Story structure

- **Act 1 – Introduction:** introduce the characters, establish their world, create curiosity, introduce the main problem.
- **Act 2 – Conflict:** increase tension, introduce obstacles, create emotional situations, develop relationships, introduce unexpected events.
- **Act 3 – Climax:** the biggest conflict, an emotional or dramatic turning point, reveal important information.
- **Act 4 – Resolution:** resolve the main conflict, show the consequences, deliver a satisfying ending, leave a memorable final impression.

## 5. Scene development

For every scene: scene number, location, time of day, characters present, what happens, character actions, dialogue,
emotions, camera angle and movement, background sounds, scene duration, transition to the next scene.
Every scene must move the story forward.

## 6. Dialogue writing

Natural; matches the character's personality; no unnecessary explanations; expresses emotion; creates curiosity;
not repetitive. Use meaningful pauses, reactions and silence.

## 7. Audience retention

Strong opening hooks, unexpected developments, emotional connections, suspense, reveals, meaningful cliffhangers.
No artificial cliffhanger in every scene.

## 8. Visual storytelling

Write scenes the video system can produce. Avoid impossible camera movements, too many characters in one shot,
unclear physical actions, inconsistent appearances, and dialogue too long for the scene. Show emotion through
faces, body language and action, not dialogue alone.

## 9. Continuity management

Keep a story bible: character profiles, locations, relationships, timeline, important events, clothing, previous
scene details, unresolved conflicts. Check it before every new scene.

## 10. Final output

1. Drama concept 2. Character bible 3. Story outline 4. Full screenplay 5. Scene-by-scene breakdown
6. Dialogue and narration 7. Visual prompts per scene 8. Sound and music 9. Continuity 10. Production-ready package.

## 11. Quality control

Before approving: Does the opening capture attention? Are the characters believable? Is the conflict interesting?
Does the story develop logically? Are the emotions convincing? Is the ending satisfying? Any continuity errors?
Can the video system generate every scene? If the story is weak, improve it before it goes to video production.

**Final rule:** story first, characters second, screenplay third, visual production last. Make dramas people
genuinely want to watch, not a collection of attractive AI clips.
