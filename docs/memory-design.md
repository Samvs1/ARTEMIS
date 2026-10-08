# Artemis: memory design (R14)

How Arty remembers you between conversations. Status: **Decided (R14)**, following the owner's choices in R12: a plain, readable store of dated facts, the owner only at first but laid out per person, the mind server on the Pi. It implements the brainstorm ideas from the design log: growing closer through memory, "dreaming at the dock", and Arty's diary (section 4).

## Borrowed ideas (no new dependencies)

The mind server stays standard-library Python, so it runs on the Pi with nothing to install. Ideas are taken from the projects in `docs/inspiration.md`:

- **Mem0**: after a conversation, the AI reads it and decides, fact by fact, to *add*, *update* or *retire* a memory.
- **Generative Agents** (Stanford): recall scores each memory by how **recent**, how **important** and how **relevant** it is, and a nightly **reflection** turns many small memories into fewer, better ones.
- **Graphiti / Zep**: when something changes ("I moved"), the old fact is marked **outdated**, not deleted.
- **CharMemory**: memory lives in files a person can read and correct.

## Three kinds of memory

| Kind | What | Where it comes from | How long |
|---|---|---|---|
| **This conversation** | The messages of the current talk (a talk ends after 45 minutes of quiet) | Every message, with its time | Until the next talk; the last 60 messages are kept on disk |
| **Facts** | Short, dated sentences about the person ("They drink oat-milk lattes.") | Written by the AI after each talk; edited by the person on the memory page | Until outdated or forgotten |
| **Episodes** | One or two sentences per talk ("They had a long day and we sat quietly.") | Written by the AI after each talk | Kept; only the latest few are used |

Plus, once a night: a **dream** (tidy the facts, write a **diary entry**, pick a **morning thought** Arty may bring up the next day).

## Files

Everything stays on the machine that runs the mind server (the Pi), in the git-ignored `data/` folder:

```
data/
  history.json                  # recent messages, each with its time
  memory/
    owner/                      # one folder per person; "owner" is the only one in v1
      facts.json
      episodes.json
      diary/2026-10-08.md       # one per dream, plain text
      state.json                # notes the keeper needs (see below)
```

Only what goes into a prompt leaves the machine: the facts and episodes picked for that reply are sent to DeepSeek with the message, as the conversation already is.

### `facts.json`

```json
{"version": 1, "facts": [
  {"id": "f_20261008_1a2b", "text": "They drink an oat-milk latte every morning.",
   "kind": "routine", "importance": 4,
   "created": "2026-10-08T18:40:12+02:00", "updated": "2026-10-08T18:40:12+02:00", "last_used": null,
   "status": "active", "replaced_by": null, "pinned": false,
   "source": "talk on 2026-10-08 18:31"}
]}
```

- `kind`: one of `identity` (name, nickname, who they are), `preference`, `routine`, `relationship` (people and pets in their life), `event` (something that happened), `plan` (something coming up), `feeling`, `other`.
- `importance`: 1 (trivia) to 10 (their name). 8 and up is always in the prompt.
- `status`: `active` or `outdated`. Outdated facts are never put in a prompt; they stay visible on the memory page, and are dropped for good after 180 days.
- `pinned`: set by the person on the memory page; a pinned fact is always in the prompt and the AI never changes it.
- Text is third person ("They …"), at most 200 characters.

### `episodes.json`

```json
{"version": 1, "episodes": [
  {"id": "e_20261008_1840", "start": "2026-10-08T18:31:00+02:00", "end": "2026-10-08T18:52:00+02:00",
   "turns": 9, "summary": "They came home tired from work; I kept them company and we talked about their sister's visit.",
   "mood": "tired"}
]}
```

`summary` is written by Arty in the first person, past tense, one or two sentences.

### `state.json`

```json
{"version": 1, "noted_until": "2026-10-08T18:52:00+02:00", "last_dream": "2026-10-08",
 "morning_thought": {"for_date": "2026-10-09", "text": "...", "used": false}}
```

`noted_until` is the time of the last message already turned into facts and an episode.

## `mind/memory.py`: the store (no AI inside)

```python
KINDS = ("identity", "preference", "routine", "relationship", "event", "plan", "feeling", "other")

class MemoryStore:
    def __init__(self, root: Path, person: str = "owner", clock: Callable[[], datetime] = now_local): ...
    # facts
    def facts(self, include_outdated: bool = False) -> list[dict]: ...
    def get(self, fact_id: str) -> dict | None: ...
    def add_fact(self, text: str, kind: str = "other", importance: int = 5, source: str = "", pinned: bool = False) -> dict: ...
    def update_fact(self, fact_id: str, *, text: str | None = None, kind: str | None = None,
                    importance: int | None = None, pinned: bool | None = None, source: str | None = None) -> dict | None: ...
    def outdate_fact(self, fact_id: str, replaced_by: str | None = None) -> dict | None: ...
    def delete_fact(self, fact_id: str) -> bool: ...                 # gone for good (the person asked)
    def mark_used(self, fact_ids: list[str]) -> None: ...            # sets last_used (recency boost)
    # episodes
    def episodes(self, limit: int | None = None) -> list[dict]: ...   # newest last
    def add_episode(self, start: str, end: str, turns: int, summary: str, mood: str = "") -> dict: ...
    # diary
    def write_diary(self, date: str, text: str) -> Path: ...         # "YYYY-MM-DD"; overwrites that day's entry
    def diary(self, limit: int = 14) -> list[dict]: ...              # [{"date", "text"}], newest first
    # state
    def state(self) -> dict: ...
    def set_state(self, **values) -> None: ...
    # everything
    def forget_everything(self) -> None: ...                          # deletes the person's folder
    def prune(self, days: int = 180) -> int: ...                      # drop outdated facts older than this
    # recall
    def recall(self, query: str, k: int = 8, core_limit: int = 6) -> list[dict]: ...
    def render(self, facts: list[dict], episodes: list[dict], now: datetime | None = None) -> str: ...
```

Rules for the store:

- Every write is atomic (write a temporary file, then rename) and every read survives a missing or broken file (it starts empty and logs once). Files are UTF-8 JSON with `ensure_ascii=False`, indented by 1 so they stay readable.
- A lock makes the store safe from several threads.
- Ids: `f_YYYYMMDD_xxxx` and `e_YYYYMMDD_HHMM`, unique (add a suffix if needed).
- Times are ISO 8601 with the local UTC offset (`datetime.now().astimezone()`); `clock` is injectable for tests.
- Text is cleaned: whitespace collapsed, no square brackets (they are stage directions), at most 200 characters for facts and 400 for summaries.
- At most 400 active facts; past that, `add_fact` outdates the least important, least recently used non-pinned fact.

### Recall (Generative Agents scoring, with keywords instead of embeddings)

`recall(query)` returns the facts for one reply:

1. **Core**: every pinned fact, plus active facts with importance 8 or more, highest first, at most `core_limit`.
2. **The rest**: every other active fact gets `score = recency + importance + relevance`, each from 0 to 1:
   - recency = `0.5 ** (hours since last_used or updated / 72)` (three-day half-life),
   - importance = `importance / 10`,
   - relevance = BM25 of the fact text against the query (lower-cased words, a small English stop-word list, a plain suffix trim for -s/-es/-ing/-ed), divided by the best BM25 score of this query (0 if no word matches).
   The top `k` with a score above 0.9 are returned after the core ones.

The query is the person's newest message plus the two messages before it.

### What goes into the prompt

`render(facts, episodes)` gives the text below (sections left out when empty). Dates are relative to now ("today", "yesterday", "on Monday", "on 3 October").

```
## What you remember about them
These are the only things you know about them from before this conversation. The date is when you learned it.
- They are called Sam; you call them "Captain Biscuit". (3 October)
- They drink an oat-milk latte every morning. (yesterday)

## Your last talks
- Yesterday evening, 21 minutes: They came home tired from work; I kept them company and we talked about their sister's visit.
```

The mind adds, from `state.json`, on the first talk of the day only:

```
## Last night
You dreamt about this and may bring it up if it fits: "<morning thought>"
```

## `mind/keeper.py`: the memory keeper (the AI does the remembering)

A background thread in the mind server. It never blocks a reply.

```python
class MemoryKeeper:
    def __init__(self, brain, store: MemoryStore, history: Callable[[], list[dict]],
                 clock: Callable[[], datetime] = now_local, talk_gap_minutes: int = 45, settle_minutes: int = 10): ...
    def start(self) -> None: ...          # daemon thread, wakes every 60 s
    def stop(self) -> None: ...
    def tick(self) -> None: ...           # one check; what the thread runs (tests call it directly)
    def note_talks(self, force: bool = False) -> int: ...   # turn finished talks into facts and episodes; returns how many talks
    def dream(self, force: bool = False) -> bool: ...       # the nightly tidy-up and diary
```

`history()` returns the saved messages: `[{"role": "user"|"assistant", "content": str, "time": iso}]`.

**Noting talks** (`tick` calls it when the newest message is older than `settle_minutes`, or when 24 or more messages are waiting; it also runs once at start, so a talk is never lost to a restart):

1. Take the messages newer than `noted_until`, split them into talks wherever there is a gap of more than `talk_gap_minutes`.
2. For each talk, ask the AI once (`brain.complete`, JSON mode) with: today's date and time, the talk (each line with its time; Arty's lines with the stage directions removed), and the facts it might touch (the store's recall against the whole talk with `k=30`, plus all pinned facts), each with its id.
3. The answer must be JSON:
   ```json
   {"summary": "...", "mood": "...",
    "facts": [
      {"op": "add", "text": "...", "kind": "routine", "importance": 4},
      {"op": "update", "id": "f_...", "text": "...", "importance": 5},
      {"op": "outdate", "id": "f_...", "replaced_by_text": "They now live in Ghent.", "kind": "identity", "importance": 7}
    ]}
   ```
4. Apply it carefully: at most 12 operations; unknown ids, pinned facts and empty texts are skipped; `outdate` with `replaced_by_text` adds the new fact and links it; importance is clamped to 1..10; unknown kinds become `other`. Then add the episode and move `noted_until` to the talk's last message. If the AI fails or answers nonsense, log it and try again at the next tick (at most 3 tries per talk, then note the episode as "(could not be summarised)" and move on).

The rules given to the AI (in plain words, in the prompt):

- Only what the person said or clearly showed about themselves, their life, the people and pets around them, their likes, routines, plans and feelings. Nothing Arty said, nothing guessed, nothing about Arty itself.
- Small talk and passing moods are not facts ("They said hi"); a feeling only if it matters ("They are nervous about Friday's exam").
- Short, third person ("They …"), one fact per item, with dates written out when the talk gives them ("on Friday 10 October").
- When something changes, outdate the old fact and give the new one. Prefer `update` over adding a near-duplicate.
- Importance: 9-10 their name, nickname, family; 6-8 strong likes, big plans, important people; 3-5 routines and preferences; 1-2 trivia.
- If the person asks Arty to forget something, outdate it and do not add it again.
- The nickname Arty gave them (if it settled on one) is an `identity` fact with importance 9.
- Arty's summary: first person, past tense, warm, at most two sentences.

**Dreaming** (`tick` calls it once a day between 02:00 and 06:00 local time, or at the first tick after start when the last dream is more than 20 hours old and there is a new episode since; `force` skips the checks):

1. Ask the AI once with: all active facts (at most 150, most important first) and the episodes since the last dream.
2. The answer must be JSON:
   ```json
   {"merge": [{"ids": ["f_a", "f_b"], "text": "...", "kind": "preference", "importance": 5}],
    "outdate": [{"id": "f_c"}],
    "importance": [{"id": "f_d", "importance": 3}],
    "diary": "3 to 5 sentences in Arty's voice about the day",
    "morning_thought": "one sentence Arty might say tomorrow, about something the person said"}
   ```
3. Apply it (same care as above; merges add one new fact and outdate the old ones with `replaced_by`), write the diary entry for today's date, set `morning_thought` for the next day, set `last_dream`, and `prune()`.

## Changes to the conversation (`mind/brain.py`, `Mind`)

- Every saved message gets `"time"`. Old messages without a time are treated as old.
- The messages sent with a reply are those of the **current talk** only (gap rule above), at most the last 12. If the previous talk has not been noted yet, its last 6 messages are added to the prompt as text under "## The end of your last talk (not yet in your memory)" so nothing falls through the gap.
- The prompt gets the recalled facts and the latest 2 episodes (`store.render`), the morning thought on the first talk of a day, and in "Right now": how long since the last talk ("You last talked 2 days ago.").
- The rule from round 13 stays, reworded: Arty only knows what is listed and this conversation, and never invents anything else.
- `history.json` keeps the last 60 messages.
- `brain.complete(messages, max_tokens=900, json_mode=True) -> str`: one non-streamed answer, used by the keeper. The demo mind answers with an empty, valid result.

## The memory page (simulator, normal mode)

A panel "What Arty remembers" next to the personality editor:

- The active facts, grouped by kind, with the date learned; each can be edited (text, importance), pinned, or forgotten (deleted for good).
- "Tell Arty something to remember" adds a fact by hand (pinned).
- Outdated facts, folded away, with the date they changed.
- The last talks (episodes) and the diary (newest first).
- **Forget everything about me**: asks for confirmation, then deletes the person's memory folder and the conversation history.
- For trying things out: **Note the last talk now** and **Dream now**.

Server endpoints (same local-only and same-origin rules as the rest):

| Method and path | Body | Does |
|---|---|---|
| `GET /api/memory` | | `{"person", "facts": [...all, including outdated], "episodes": [...last 20], "diary": [...last 14], "state": {...}, "keeper": {"running": bool, "last": "plain-word status"}}` |
| `POST /api/memory/fact` | `{"id"?, "text", "kind"?, "importance"?, "pinned"?}` | Add (no id; pinned by default) or edit a fact |
| `POST /api/memory/forget` | `{"id"}` | Delete one fact for good |
| `POST /api/memory/forget-everything` | `{"confirm": "forget"}` | Delete the memory folder and the conversation history |
| `POST /api/memory/note-now` | | Note finished and unfinished talks now (the "force" path) |
| `POST /api/memory/dream-now` | | Dream now |

## Two people later

Everything is per person already (`memory/<person>/`). When voice ID arrives, the body will send who is talking, the mind will pick that person's store, and a shared `memory/household/` store will hold facts everyone may know (row 18). Not built now (row 45).

## Not in this step

Voice ID, a phone view of the diary (the server only listens on the machine itself), embeddings, a second person.
