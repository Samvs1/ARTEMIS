"""Arty's personality: the text the mind is told about who Arty is.

It can come from two places:

- mind/character.md ships with the project. This is the default.
- data/character.md is your own version. If it exists, Arty uses it instead.

Every save is kept in a history, so you can go back to an older version.
Nothing needs a restart: the next reply uses whatever is saved.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

MAX_CHARS = 20000
MIN_CHARS = 20
MAX_VERSIONS = 100

FALLBACK = "You are Arty, a small, curious, friendly robot. Keep replies short, warm and playful."


class PersonalityError(ValueError):
    """The text cannot be saved. The message is written for a person to read."""


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


class Personality:
    def __init__(self, default_file: Path, user_file: Path | None = None, history_file: Path | None = None) -> None:
        self.default_file = default_file
        self.user_file = user_file
        self.history_file = history_file
        self._lock = threading.Lock()

    # ---- reading ----
    def default_text(self) -> str:
        try:
            return self.default_file.read_text(encoding="utf-8")
        except OSError:
            return FALLBACK

    def _mine(self) -> str:
        if not self.user_file:
            return ""
        try:
            return self.user_file.read_text(encoding="utf-8")
        except OSError:
            return ""

    def text(self) -> str:
        """What Arty is told right now. Read from disk every time, so edits apply at once."""
        mine = self._mine()
        return mine if mine.strip() else self.default_text()

    def source(self) -> str:
        return "yours" if self._mine().strip() else "default"

    # ---- history ----
    def _read_history(self) -> list[dict]:
        if not self.history_file:
            return []
        try:
            data = json.loads(self.history_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = data.get("versions") if isinstance(data, dict) else None
        return [v for v in (items or []) if isinstance(v, dict) and isinstance(v.get("id"), int) and isinstance(v.get("text"), str)]

    def _add_version(self, text: str, note: str) -> None:
        if not self.history_file:
            return
        versions = self._read_history()
        entry = {
            "id": (versions[-1]["id"] + 1) if versions else 1,
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "note": note.strip()[:120],
            "text": text,
        }
        versions = (versions + [entry])[-MAX_VERSIONS:]
        try:
            _write_atomic(self.history_file, json.dumps({"versions": versions}, ensure_ascii=False))
        except OSError:
            pass                                   # the history is a convenience, never a reason to fail a save

    def describe(self) -> dict:
        """Everything the editor needs to show."""
        versions = self._read_history()
        current = self.text()
        newest = versions[-1] if versions else None
        matches = bool(newest) and newest["text"].strip() == current.strip()
        return {
            "text": current,
            "source": self.source(),
            "saved_at": newest["time"] if matches else "",
            "note": newest["note"] if matches else "",
            "default_chars": len(self.default_text()),
            "versions": [{"id": v["id"], "time": v["time"], "note": v.get("note", ""), "chars": len(v["text"])} for v in reversed(versions)],
        }

    # ---- changing ----
    @staticmethod
    def _check(text: str) -> str:
        text = (text or "").replace("\r\n", "\n").strip()
        if len(text) < MIN_CHARS:
            raise PersonalityError("The personality is empty or far too short. Arty needs at least a sentence or two about who it is.")
        if len(text) > MAX_CHARS:
            raise PersonalityError(f"That is too long ({len(text):,} characters; the limit is {MAX_CHARS:,}). A shorter personality works better anyway.")
        return text

    def save(self, text: str, note: str = "") -> dict:
        text = self._check(text)
        if not self.user_file:
            raise PersonalityError("There is nowhere to save the personality.")
        with self._lock:
            try:
                _write_atomic(self.user_file, text + "\n")
            except OSError as e:
                raise PersonalityError(f"The personality could not be saved ({e.strerror or e}).") from e
            self._add_version(text, note)
        return self.describe()

    def adopt_new_name(self) -> bool:
        """The robot was called Milo until round 16. A personality saved before then is updated once,
        as a new version, so the old one can still be restored from the history."""
        mine = self._mine()
        if not mine or not re.search(r"\bMilo\b", mine):
            return False
        text = re.sub(r"\bYou are Milo\b", "You are Artemis (Arty for short)", mine)
        text = re.sub(r"\bMilo\b", "Arty", text)
        self.save(text.strip(), "Renamed Milo to Artemis (Arty for short)")
        return True

    def restore(self, version_id: int) -> dict:
        for v in self._read_history():
            if v["id"] == version_id:
                return self.save(v["text"], f"Went back to version {version_id}")
        raise PersonalityError("That version was not found.")

    def reset(self) -> dict:
        """Go back to the shipped default. Your own versions stay in the history."""
        with self._lock:
            if self.user_file:
                try:
                    self.user_file.unlink()
                except OSError:
                    pass
            self._add_version(self.default_text().strip(), "Went back to the shipped default")
        return self.describe()
