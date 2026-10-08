"""Milo's memory: the store. There is no AI in this file.

It keeps what Milo knows about a person in plain files you can open and read:

    <root>/<person>/facts.json     short dated sentences about the person
    <root>/<person>/episodes.json  one or two sentences per talk
    <root>/<person>/diary/         Milo's diary, one text file per day
    <root>/<person>/state.json     small notes the memory keeper needs

It also picks which facts go into one reply (recall) and writes them as the text
the prompt gets (render). The design is in docs/memory-design.md.
"""
from __future__ import annotations

import json
import math
import os
import re
import secrets
import shutil
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

KINDS = ("identity", "preference", "routine", "relationship", "event", "plan", "feeling", "other")

VERSION = 1
MAX_ACTIVE_FACTS = 400          # past this, the least important, least used fact is retired
FACT_CHARS = 200
SUMMARY_CHARS = 400
SOURCE_CHARS = 120
MOOD_CHARS = 40
CORE_IMPORTANCE = 8             # this important or more: always in the prompt
SCORE_THRESHOLD = 0.9           # a fact that is not core needs a score above this
HALF_LIFE_HOURS = 72.0
BM25_K1 = 1.5
BM25_B = 0.75

PERSON_RE = re.compile(r"[a-z0-9_-]{1,64}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")

STOP_WORDS = frozenset("""
a am an and are as at be been but by can could did do does for from had has have he her hers him his how i if in
into is it its just me my of on or our ours she so some than that the their theirs them then there these they
this those to too us was we were what when where which who whom why will with would you your yours
im ive id youre theyre dont doesnt didnt cant wont thats whats about also any very really yes
""".split())


def now_local() -> datetime:
    """The current time, with the local UTC offset."""
    return datetime.now().astimezone()


def _log(message: str) -> None:
    print(time.strftime("%H:%M:%S ") + message, flush=True)


class MemoryStoreError(OSError):
    """The memory could not be saved or erased. The message is written for a person to read."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

_LOCKS: dict = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(folder: Path):
    """One lock per folder, so two store objects for the same person cannot trip over each other."""
    key = str(folder.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _suffix() -> str:
    return secrets.token_hex(2)


def _unique_id(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"


def _clean(text, limit: int) -> str:
    """One tidy line: no square brackets (those are stage directions), spaces collapsed, not too long."""
    text = "" if text is None else str(text)
    text = text.replace("[", "").replace("]", "")
    return " ".join(text.split())[:limit].rstrip()


def _importance(value, default: int = 5) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(1, min(10, number))


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.astimezone()      # no offset given: it was local time


def _iso(moment: datetime) -> str:
    return _aware(moment).isoformat(timespec="seconds")


def _parse_time(value) -> datetime | None:
    if isinstance(value, datetime):
        return _aware(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _aware(datetime.fromisoformat(value.strip()))
    except ValueError:
        return None


def _starts_at(episode: dict) -> float:
    started = _parse_time(episode.get("start"))
    return started.timestamp() if started else 0.0


def _seen(fact: dict, fallback: datetime) -> datetime:
    """When this fact was last touched: the later of last_used and updated."""
    times = [t for t in (_parse_time(fact.get("last_used")), _parse_time(fact.get("updated")),
                         _parse_time(fact.get("created"))) if t]
    return max(times) if times else fallback


def _write_atomic(path: Path, text: str) -> None:
    """Write to a temporary file, then rename it over the real one, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise MemoryStoreError(f"Milo's memory could not be saved ({e.strerror or e}).") from e


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


# ---------------------------------------------------------------------------
# Words and BM25 (keyword matching, no embeddings)
# ---------------------------------------------------------------------------

_VOWELS = "aeiouy"


def _stem(word: str) -> str:
    """A light suffix trim (-s, -es, -ing, -ed). Short words are left alone, so 'bus' and 'sing' survive."""
    if len(word) <= 3 or not word.isalpha():
        return word
    w = word
    if w.endswith("sses"):
        w = w[:-2]
    elif w.endswith("es") and w[:-2].endswith(("ch", "sh", "x", "z", "s")):
        w = w[:-2]
    elif w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]
    for ending in ("ing", "ed"):
        if w.endswith(ending):
            stem = w[:-len(ending)]
            if len(stem) >= 3 and any(c in _VOWELS for c in stem):
                if len(stem) >= 4 and stem[-1] == stem[-2] and stem[-1] not in "aeiouylszf":
                    stem = stem[:-1]                    # running -> run, stopped -> stop
                w = stem
            break
    if len(w) >= 4 and w.endswith("e"):
        w = w[:-1]                                      # bake, bakes, baking, baked all become "bak"
    if len(w) >= 3 and w.endswith("y") and w[-2] not in _VOWELS:
        w = w[:-1] + "i"                                # puppy and puppies meet at "puppi"
    return w


def _tokens(text: str) -> list[str]:
    text = re.sub(r"['’]s\b", "", str(text).lower())
    text = text.replace("'", "").replace("’", "")
    words = re.findall(r"[^\W_]+", text)
    return [_stem(w) for w in words if w not in STOP_WORDS and not (len(w) == 1 and w.isalpha())]


def _bm25(docs: list[list[str]], query: list[str]) -> list[float]:
    """The BM25 score of each document (a list of tokens) for the query terms."""
    if not docs or not query:
        return [0.0] * len(docs)
    n = len(docs)
    avg_len = (sum(len(d) for d in docs) / n) or 1.0
    having = Counter(t for d in docs for t in set(d))
    scores = []
    for doc in docs:
        counts = Counter(doc)
        score = 0.0
        for term in query:
            tf = counts.get(term, 0)
            if not tf:
                continue
            idf = math.log(1 + (n - having[term] + 0.5) / (having[term] + 0.5))
            score += idf * tf * (BM25_K1 + 1) / (tf + BM25_K1 * (1 - BM25_B + BM25_B * len(doc) / avg_len))
        scores.append(score)
    return scores


# ---------------------------------------------------------------------------
# Words for dates (never uses the computer's language settings)
# ---------------------------------------------------------------------------

def _days_ago(moment: datetime, now: datetime) -> int:
    return (now.date() - moment.astimezone(now.tzinfo).date()).days


def _plain_date(moment: datetime, now: datetime) -> str:
    text = f"{moment.day} {MONTHS[moment.month - 1]}"
    return text if moment.year == now.year else f"{text} {moment.year}"


def _fact_date(moment: datetime, now: datetime) -> str:
    moment = moment.astimezone(now.tzinfo)
    days = _days_ago(moment, now)
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days <= 5:
        return WEEKDAYS[moment.weekday()]
    return _plain_date(moment, now)


def _part_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"


def _talk_time(moment: datetime, now: datetime) -> str:
    moment = moment.astimezone(now.tzinfo)
    days = _days_ago(moment, now)
    part = _part_of_day(moment.hour)
    if days <= 0:
        return "tonight" if part == "night" else f"this {part}"
    if days == 1:
        return f"yesterday {part}"
    if days <= 5:
        return f"on {WEEKDAYS[moment.weekday()]} {part}"
    return f"on {_plain_date(moment, now)}"


def _minutes(start: datetime | None, end: datetime | None) -> str:
    if not start or not end:
        return ""
    minutes = round((end - start).total_seconds() / 60)
    if minutes < 0:
        return ""
    return "a few minutes" if minutes < 3 else f"{minutes} minutes"


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

class MemoryStore:
    def __init__(self, root: Path, person: str = "owner", clock: Callable[[], datetime] = now_local) -> None:
        if not isinstance(person, str) or not PERSON_RE.fullmatch(person):
            raise ValueError("A person's name can only use the letters a to z, digits, - and _ (for example 'owner').")
        self.root = Path(root)
        self.person = person
        self.clock = clock
        self.dir = self.root / person
        self.facts_file = self.dir / "facts.json"
        self.episodes_file = self.dir / "episodes.json"
        self.state_file = self.dir / "state.json"
        self.diary_dir = self.dir / "diary"
        self._lock = _lock_for(self.dir)
        self._warned: set[Path] = set()         # files already reported, so a broken one is logged once
        self._backup: set[Path] = set()         # broken files to copy aside before they are saved over

    # ---- time ----
    def _now(self) -> datetime:
        return _aware(self.clock())

    # ---- reading and writing files ----
    def _problem(self, path: Path, what: str) -> None:
        self._backup.add(path)
        if path in self._warned:
            return
        self._warned.add(path)
        _log(f"Milo's memory file {path} {what}. It is treated as empty; before anything is saved over it, "
             f"a copy is kept as {path.name}.broken.")

    def _read_json(self, path: Path) -> dict | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            self._problem(path, f"could not be read ({getattr(e, 'strerror', None) or e})")
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            self._problem(path, "is not valid JSON")
            return None
        if not isinstance(data, dict):
            self._problem(path, "does not look like a memory file")
            return None
        return data

    def _write_json(self, path: Path, data: dict) -> None:
        if path in self._backup:
            try:
                shutil.copy2(path, path.with_name(path.name + ".broken"))
            except OSError:
                pass
        _write_atomic(path, json.dumps(data, ensure_ascii=False, indent=1, default=_json_default) + "\n")
        self._backup.discard(path)
        self._warned.discard(path)

    def _fact_from(self, item, fallback: str) -> dict | None:
        if not isinstance(item, dict):
            return None
        fact_id = item.get("id")
        text = _clean(item.get("text"), FACT_CHARS)
        if not isinstance(fact_id, str) or not fact_id.strip() or not text:
            return None
        kind = item.get("kind")
        created = item.get("created") if isinstance(item.get("created"), str) else None
        updated = item.get("updated") if isinstance(item.get("updated"), str) else None
        replaced_by = item.get("replaced_by")
        return {
            "id": fact_id.strip(),
            "text": text,
            "kind": kind if kind in KINDS else "other",
            "importance": _importance(item.get("importance")),
            "created": created or updated or fallback,
            "updated": updated or created or fallback,
            "last_used": item.get("last_used") if isinstance(item.get("last_used"), str) else None,
            "status": "outdated" if item.get("status") == "outdated" else "active",
            "replaced_by": replaced_by if isinstance(replaced_by, str) and replaced_by else None,
            "pinned": item.get("pinned") is True,
            "source": _clean(item.get("source"), SOURCE_CHARS),
        }

    def _load_facts(self) -> list[dict]:
        data = self._read_json(self.facts_file)
        raw = data.get("facts") if data else None
        if raw is None:
            return []
        if not isinstance(raw, list):
            self._problem(self.facts_file, "does not hold a list of facts")
            return []
        fallback = _iso(self._now())
        facts, ids = [], set()
        for item in raw:
            fact = self._fact_from(item, fallback)
            if fact is None or fact["id"] in ids:
                continue
            ids.add(fact["id"])
            facts.append(fact)
        if len(facts) < len(raw):
            self._problem(self.facts_file, f"had {len(raw) - len(facts)} entries that could not be understood")
        return facts

    def _save_facts(self, facts: list[dict]) -> None:
        self._write_json(self.facts_file, {"version": VERSION, "facts": facts})

    def _load_episodes(self) -> list[dict]:
        data = self._read_json(self.episodes_file)
        raw = data.get("episodes") if data else None
        if raw is None:
            return []
        if not isinstance(raw, list):
            self._problem(self.episodes_file, "does not hold a list of episodes")
            return []
        episodes, ids = [], set()
        for item in raw:
            summary = _clean(item.get("summary"), SUMMARY_CHARS) if isinstance(item, dict) else ""
            if not summary or not isinstance(item.get("id"), str) or item["id"] in ids:
                continue
            ids.add(item["id"])
            try:
                turns = max(0, int(item.get("turns")))
            except (TypeError, ValueError):
                turns = 0
            episodes.append({
                "id": item["id"],
                "start": item.get("start") if isinstance(item.get("start"), str) else "",
                "end": item.get("end") if isinstance(item.get("end"), str) else "",
                "turns": turns,
                "summary": summary,
                "mood": _clean(item.get("mood"), MOOD_CHARS),
            })
        if len(episodes) < len(raw):
            self._problem(self.episodes_file, f"had {len(raw) - len(episodes)} entries that could not be understood")
        episodes.sort(key=_starts_at)
        return episodes

    # ---- facts ----
    def facts(self, include_outdated: bool = False) -> list[dict]:
        """The facts, oldest first. Outdated ones only when asked for."""
        with self._lock:
            facts = self._load_facts()
        return facts if include_outdated else [f for f in facts if f["status"] == "active"]

    def get(self, fact_id: str) -> dict | None:
        with self._lock:
            return next((f for f in self._load_facts() if f["id"] == fact_id), None)

    def add_fact(self, text: str, kind: str = "other", importance: int = 5, source: str = "", pinned: bool = False) -> dict:
        text = _clean(text, FACT_CHARS)
        if not text:
            raise ValueError("A memory needs some words in it.")
        with self._lock:
            facts = self._load_facts()
            now = self._now()
            self._make_room(facts, now)
            stamp = _iso(now)
            fact = {
                "id": _unique_id(f"f_{now:%Y%m%d}_{_suffix()}", {f["id"] for f in facts}),
                "text": text,
                "kind": kind if kind in KINDS else "other",
                "importance": _importance(importance),
                "created": stamp,
                "updated": stamp,
                "last_used": None,
                "status": "active",
                "replaced_by": None,
                "pinned": bool(pinned),
                "source": _clean(source, SOURCE_CHARS),
            }
            facts.append(fact)
            self._save_facts(facts)
            return dict(fact)

    def _make_room(self, facts: list[dict], now: datetime) -> None:
        """Keep at most MAX_ACTIVE_FACTS active: retire the least important, least recently used one that is not pinned."""
        active = [f for f in facts if f["status"] == "active"]
        if len(active) < MAX_ACTIVE_FACTS:
            return
        loose = [f for f in active if not f["pinned"]]
        if not loose:
            return                                   # everything is pinned: nothing may be dropped
        victim = min(loose, key=lambda f: (f["importance"], _seen(f, now).timestamp()))
        victim["status"] = "outdated"
        victim["updated"] = _iso(now)

    def update_fact(self, fact_id: str, *, text: str | None = None, kind: str | None = None,
                    importance: int | None = None, pinned: bool | None = None, source: str | None = None) -> dict | None:
        if text is not None and not _clean(text, FACT_CHARS):
            raise ValueError("A memory needs some words in it.")
        with self._lock:
            facts = self._load_facts()
            fact = next((f for f in facts if f["id"] == fact_id), None)
            if fact is None:
                return None
            changed = False
            if text is not None:
                fact["text"], changed = _clean(text, FACT_CHARS), True
            if kind is not None:
                fact["kind"], changed = (kind if kind in KINDS else "other"), True
            if importance is not None:
                fact["importance"], changed = _importance(importance, fact["importance"]), True
            if pinned is not None:
                fact["pinned"], changed = bool(pinned), True
            if source is not None:
                fact["source"], changed = _clean(source, SOURCE_CHARS), True
            if changed:
                fact["updated"] = _iso(self._now())
                self._save_facts(facts)
            return dict(fact)

    def outdate_fact(self, fact_id: str, replaced_by: str | None = None) -> dict | None:
        """Mark a fact as no longer true. It stays on file (and visible on the memory page) but is never used."""
        with self._lock:
            facts = self._load_facts()
            fact = next((f for f in facts if f["id"] == fact_id), None)
            if fact is None:
                return None
            if fact["status"] != "outdated":
                fact["status"] = "outdated"
                fact["updated"] = _iso(self._now())
            if replaced_by:
                fact["replaced_by"] = _clean(replaced_by, 64)
            self._save_facts(facts)
            return dict(fact)

    def delete_fact(self, fact_id: str) -> bool:
        """Gone for good (the person asked)."""
        with self._lock:
            facts = self._load_facts()
            kept = [f for f in facts if f["id"] != fact_id]
            if len(kept) == len(facts):
                return False
            self._save_facts(kept)
            return True

    def mark_used(self, fact_ids: list[str]) -> None:
        """Remember that these facts were just put in a prompt, so they feel recent."""
        wanted = set(fact_ids or [])
        if not wanted:
            return
        with self._lock:
            facts = self._load_facts()
            stamp = _iso(self._now())
            touched = False
            for fact in facts:
                if fact["id"] in wanted:
                    fact["last_used"], touched = stamp, True
            if touched:
                self._save_facts(facts)

    # ---- episodes ----
    def episodes(self, limit: int | None = None) -> list[dict]:
        """The talks, newest last. With a limit, only the newest ones."""
        with self._lock:
            episodes = self._load_episodes()
        if limit is None:
            return episodes
        return episodes[-limit:] if limit > 0 else []

    def add_episode(self, start: str, end: str, turns: int, summary: str, mood: str = "") -> dict:
        summary = _clean(summary, SUMMARY_CHARS)
        if not summary:
            raise ValueError("A talk needs a summary with some words in it.")
        try:
            turns = max(0, int(turns))
        except (TypeError, ValueError):
            turns = 0
        with self._lock:
            episodes = self._load_episodes()
            now = self._now()
            episode = {
                "id": _unique_id(f"e_{now:%Y%m%d_%H%M}", {e["id"] for e in episodes}),
                "start": _iso(start) if isinstance(start, datetime) else str(start or ""),
                "end": _iso(end) if isinstance(end, datetime) else str(end or ""),
                "turns": turns,
                "summary": summary,
                "mood": _clean(mood, MOOD_CHARS),
            }
            episodes.append(episode)
            episodes.sort(key=_starts_at)
            self._write_json(self.episodes_file, {"version": VERSION, "episodes": episodes})
            return dict(episode)

    # ---- diary ----
    def write_diary(self, date: str, text: str) -> Path:
        """Write (or overwrite) the diary entry for a day. `date` is YYYY-MM-DD."""
        date = str(date)
        try:
            if not DATE_RE.fullmatch(date):
                raise ValueError
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise ValueError("A diary date must look like 2026-10-08.") from None
        text = str(text or "").replace("\r\n", "\n").strip()
        if not text:
            raise ValueError("A diary entry needs some words in it.")
        path = self.diary_dir / f"{date}.md"
        with self._lock:
            _write_atomic(path, text + "\n")
        return path

    def diary(self, limit: int = 14) -> list[dict]:
        """The newest diary entries first: [{"date": "2026-10-08", "text": "..."}]."""
        if limit <= 0:
            return []
        with self._lock:
            try:
                names = sorted((p for p in self.diary_dir.glob("*.md") if DATE_RE.fullmatch(p.stem)), reverse=True)
            except OSError:
                return []
            entries = []
            for path in names[:limit]:
                try:
                    entries.append({"date": path.stem, "text": path.read_text(encoding="utf-8").strip()})
                except (OSError, ValueError) as e:
                    self._problem(path, f"could not be read ({getattr(e, 'strerror', None) or e})")
            return entries

    # ---- state ----
    def state(self) -> dict:
        with self._lock:
            data = self._read_json(self.state_file) or {}
        return {k: v for k, v in data.items() if k != "version"}

    def set_state(self, **values) -> None:
        """Add or change notes in state.json; everything else in it stays."""
        if not values:
            return
        with self._lock:
            data = self._read_json(self.state_file) or {}
            data.update(values)
            data["version"] = VERSION
            self._write_json(self.state_file, data)

    # ---- everything ----
    def forget_everything(self) -> None:
        """Delete this person's whole memory folder."""
        with self._lock:
            root, target = self.root.resolve(), self.dir.resolve()
            if target == root or root not in target.parents:
                raise ValueError("Refusing to erase anything outside Milo's memory folder.")
            if self.dir.is_symlink():
                self.dir.unlink()
            elif self.dir.exists():
                try:
                    shutil.rmtree(target)
                except OSError as e:
                    raise MemoryStoreError(f"Milo's memory could not be erased ({e.strerror or e}).") from e
            self._warned.clear()
            self._backup.clear()

    def prune(self, days: int = 180) -> int:
        """Drop outdated facts that changed more than `days` days ago. Returns how many were dropped."""
        with self._lock:
            facts = self._load_facts()
            cutoff = self._now() - timedelta(days=max(0, days))
            kept = []
            for fact in facts:
                changed = _parse_time(fact["updated"])
                if fact["status"] == "outdated" and changed and changed < cutoff:
                    continue
                kept.append(fact)
            dropped = len(facts) - len(kept)
            if dropped:
                self._save_facts(kept)
            return dropped

    # ---- recall ----
    def recall(self, query: str, k: int = 8, core_limit: int = 6) -> list[dict]:
        """The facts for one reply: the core ones first, then the best-scoring others. Changes nothing."""
        with self._lock:
            active = [f for f in self._load_facts() if f["status"] == "active"]
            now = self._now()
        if not active:
            return []

        core = [f for f in active if f["pinned"] or f["importance"] >= CORE_IMPORTANCE]
        core.sort(key=lambda f: (not f["pinned"], -f["importance"], -_seen(f, now).timestamp(), f["id"]))
        core = core[:max(0, core_limit)]
        core_ids = {f["id"] for f in core}

        wanted = list(dict.fromkeys(_tokens(query)))
        relevance = _bm25([_tokens(f["text"]) for f in active], wanted)
        best = max(relevance, default=0.0)

        scored = []
        for fact, rel in zip(active, relevance):
            if fact["id"] in core_ids:
                continue
            seen = _seen(fact, now)
            hours = max(0.0, (now - seen).total_seconds() / 3600)
            score = 0.5 ** (hours / HALF_LIFE_HOURS) + fact["importance"] / 10 + (rel / best if best > 0 else 0.0)
            if score > SCORE_THRESHOLD:
                scored.append((score, seen.timestamp(), fact["id"], fact))
        scored.sort(key=lambda s: (-s[0], -s[1], s[2]))
        return core + [s[3] for s in scored[:max(0, k)]]

    # ---- the prompt text ----
    def render(self, facts: list[dict], episodes: list[dict], now: datetime | None = None) -> str:
        """The two sections of the prompt: what Milo remembers, and its last talks. Empty sections are left out."""
        now = _aware(now) if now else self._now()
        sections = []

        lines = []
        for fact in facts or []:
            text = _clean(fact.get("text"), FACT_CHARS)
            if not text:
                continue
            learned = _parse_time(fact.get("created")) or _parse_time(fact.get("updated"))
            lines.append(f"- {text} ({_fact_date(learned, now)})" if learned else f"- {text}")
        if lines:
            sections.append("\n".join([
                "## What you remember about them",
                "These are the only things you know about them from before this conversation. "
                "The date is when you learned it.",
                *lines,
            ]))

        lines = []
        for episode in episodes or []:
            summary = _clean(episode.get("summary"), SUMMARY_CHARS)
            if not summary:
                continue
            start, end = _parse_time(episode.get("start")), _parse_time(episode.get("end"))
            when = _talk_time(start, now) if start else ""
            when = when[:1].upper() + when[1:]
            head = ", ".join(part for part in (when, _minutes(start, end)) if part)
            lines.append(f"- {head}: {summary}" if head else f"- {summary}")
        if lines:
            sections.append("\n".join(["## Your last talks", *lines]))

        return "\n\n".join(sections)
