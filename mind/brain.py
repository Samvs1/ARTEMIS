"""Milo's mind.

This file holds four things:

1. The "stage directions" parser. The AI writes its reply with little tags such as
   [emote:happy] in it. The parser turns the streamed text into an ordered list of
   actions for the body: change the face, look somewhere, play a chirp, say a sentence.
2. The prompt: the character bible plus the rules for those tags.
3. DeepSeekBrain: the real mind, talking to the DeepSeek API.
4. DemoBrain: a tiny canned mind, so everything can be tried without any key.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator

from mind.config import ROOT, Settings

CHARACTER_FILE = ROOT / "mind" / "character.md"

# What the body can do. These names must match the simulator (sim/index.html).
EMOTES = {"calm", "happy", "curious", "surprised", "thinking", "excited", "sleepy"}
LOOKS = {"left", "right", "up", "down", "center"}
SOUNDS = {"happy", "curious", "surprised", "thinking", "excited", "sleepy", "wake", "listen", "bored", "poke"}

# Tags a model might write on its own. We turn them into something the body understands.
ALIASES = {
    "laughs": {"type": "emote", "name": "happy"}, "laugh": {"type": "emote", "name": "happy"},
    "giggles": {"type": "emote", "name": "happy"}, "smiles": {"type": "emote", "name": "happy"},
    "winks": {"type": "emote", "name": "happy"}, "gasps": {"type": "emote", "name": "surprised"},
    "yawns": {"type": "emote", "name": "sleepy"}, "thinks": {"type": "emote", "name": "thinking"},
    "ponders": {"type": "emote", "name": "thinking"}, "sighs": {"type": "sound", "name": "sleepy"},
    "hums": {"type": "sound", "name": "curious"}, "chirps": {"type": "sound", "name": "happy"},
}


# ---------------------------------------------------------------------------
# 1. The stage-directions parser
# ---------------------------------------------------------------------------

EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")
ACTION_RE = re.compile(r"\*[^*\n]{0,60}\*")          # *waves* style actions are not spoken
SENTENCE_END = re.compile(r"(?:[.!?…]+[\"'”’)]*(?=\s))|(?:\n+)")


def clean_speech(text: str) -> str:
    """Make text safe to speak: no emoji, no markdown stars, no stray spaces."""
    text = ACTION_RE.sub(" ", text)
    text = EMOJI_RE.sub("", text)
    text = text.replace("*", "").replace("`", "")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([.,!?;:])", r"\1", text).strip()      # no gap left where an emoji or action was
    return text if re.search(r"[A-Za-z0-9]", text) else ""


def parse_tag(tag: str) -> dict | None:
    """Turn the inside of [brackets] into a body action, or None if it is not one we know."""
    t = tag.strip().lower()
    name, _, value = t.partition(":")
    name, value = name.strip(), value.strip().replace(" ", "_")
    if name == "emote" and value in EMOTES:
        return {"type": "emote", "name": value}
    if name == "look" and value in LOOKS:
        return {"type": "look", "dir": value}
    if name == "sound" and value in SOUNDS:
        return {"type": "sound", "name": value}
    if not value and name in EMOTES:
        return {"type": "emote", "name": name}
    alias = ALIASES.get(t)
    return dict(alias) if alias else None


class DirectiveStream:
    """Turns streamed text into body actions, in the order the AI wrote them.

    Call feed() with each piece of text as it arrives, and finish() at the end.
    Each call returns a list of actions:
        {"type": "emote", "name": "happy"}
        {"type": "look", "dir": "left"}
        {"type": "sound", "name": "curious"}
        {"type": "say", "text": "Hello there!"}
    Sentences are handed out as soon as they are complete, so the voice can start
    on the first sentence while the rest is still being written.
    """

    MIN_LATER_SENTENCE = 18   # shorter sentences after the first are joined to the next one

    def __init__(self) -> None:
        self._buf = ""        # text we have not looked at yet
        self._speech = ""     # speakable text waiting for the end of a sentence
        self._held = ""       # a short sentence waiting to be joined with the next
        self._said = 0        # how many sentences we have handed out

    def feed(self, delta: str) -> list[dict]:
        self._buf += delta
        return self._drain(final=False)

    def finish(self) -> list[dict]:
        return self._drain(final=True)

    def _drain(self, final: bool) -> list[dict]:
        events: list[dict] = []
        while self._buf:
            i = self._buf.find("[")
            if i < 0:
                self._speech += self._buf
                self._buf = ""
                break
            self._speech += self._buf[:i]
            self._buf = self._buf[i:]
            j = self._buf.find("]")
            if j < 0:
                if final:
                    self._buf = ""                    # a tag that never closed is dropped, not spoken
                elif len(self._buf) > 48:
                    self._buf = self._buf[1:]         # far too long to be a tag: forget the bracket
                    continue
                break                                 # otherwise wait for the rest of the tag
            tag, self._buf = self._buf[1:j], self._buf[j + 1:]
            events.extend(self._flush(force=True))    # speech before a tag is complete
            action = parse_tag(tag)
            if action:
                events.append(action)
        events.extend(self._flush(force=final))
        return events

    def _flush(self, force: bool) -> list[dict]:
        out: list[dict] = []
        text = self._speech
        while True:
            m = SENTENCE_END.search(text)
            if not m:
                break
            sentence, text = text[:m.end()], text[m.end():]
            self._emit(sentence, out, force=False)
        self._speech = text
        if force:
            if text.strip():
                self._emit(text, out, force=True)
                self._speech = ""
            elif self._held:
                self._emit("", out, force=True)
        return out

    def _emit(self, raw: str, out: list[dict], force: bool) -> None:
        clean = clean_speech(self._held + " " + raw)
        if not clean:
            self._held = ""
            return
        if self._said > 0 and len(clean) < self.MIN_LATER_SENTENCE and not force:
            self._held = clean
            return
        self._held = ""
        self._said += 1
        out.append({"type": "say", "text": clean})


# ---------------------------------------------------------------------------
# 2. The prompt
# ---------------------------------------------------------------------------

BODY_PROTOCOL = """\
## How you control your body

Your reply is spoken out loud and shown on your face. You can put stage directions in square brackets anywhere in a reply. They are never spoken.

- [emote:NAME] changes your face. NAME is one of: calm, happy, curious, surprised, thinking, excited, sleepy.
- [look:DIRECTION] moves your eyes. DIRECTION is one of: left, right, up, down, center.
- [sound:NAME] plays a short chirp. NAME is one of: happy, curious, surprised, thinking, excited, sleepy.

Rules for every reply:
- Start with one [emote:...] that matches how you feel. Use a [look:...] or [sound:...] only when it adds something.
- One to three short sentences. Plain words. No lists, no markdown, no emoji, no asterisks.
- Never write anything in square brackets except the directions above.
"""

EVENT_PROMPTS = {
    "wants_company": (
        "(Event: you have been alone and quiet for a while and you would like some company. "
        "Say one short, in-character thing to get a little attention. Ask at most one question.)"
    ),
}


def context_block(state: dict) -> str:
    """What is going on right now, taken from the body. Only simple values are accepted."""
    lines = []
    when = str(state.get("local_time") or "")[:70].strip()
    if when:
        lines.append(f"Local time: {when}.")
    if "lights" in state:
        lines.append("The room lights are on." if state.get("lights") else "The room lights are off, so you feel a bit sleepy.")
    mood = str(state.get("mood") or "")
    if mood in EMOTES:
        lines.append(f"Your face currently shows: {mood}.")
    for key, label in (("energy", "energy"), ("boredom", "boredom"), ("social", "wish for company")):
        v = state.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            lines.append(f"Your {label} is {max(0, min(100, int(v)))} out of 100.")
    return "## Right now\n" + "\n".join("- " + line for line in lines) if lines else ""


def build_system_prompt(character: str, state: dict) -> str:
    parts = [character.strip(), BODY_PROTOCOL.strip(), context_block(state or {})]
    return "\n\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# 3. Errors and the DeepSeek client
# ---------------------------------------------------------------------------

class BrainError(Exception):
    """Something went wrong with the mind. The message is written for a person to read."""


class _Retry(Exception):
    """This model name or option was refused. Try the next one."""


def describe_http_error(err: urllib.error.HTTPError) -> str:
    """Pull a short, readable reason out of an error response (JSON or plain text)."""
    try:
        raw = err.read(2000).decode("utf-8", "replace").strip()
    except Exception:
        return ""
    finally:
        err.close()
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            inner = obj.get("error")
            msg = (inner.get("message") if isinstance(inner, dict) else inner) or obj.get("message") or obj.get("detail")
            if msg:
                raw = str(msg)
    except ValueError:
        pass
    return re.sub(r"\s+", " ", raw)[:200]


def friendly_status(service: str, code: int, detail: str) -> str:
    if code == 401:
        msg = f"{service} rejected the API key (401). Check the key in your .env file."
    elif code == 402:
        msg = f"{service} says the account has no balance left (402)."
    elif code == 403:
        msg = f"{service} refused the request (403). The key may not be allowed to do this."
    elif code == 429:
        msg = f"{service} is limiting requests right now (429). Wait a moment and try again."
    elif code >= 500:
        msg = f"{service} is having trouble (HTTP {code}). Try again in a moment."
    else:
        msg = f"{service} answered HTTP {code}."
    return f"{msg} ({detail})" if detail else msg


def network_message(service: str, host: str, err: Exception) -> str:
    reason = getattr(err, "reason", err)
    return (f"Could not reach {service} at {host} ({reason}). Check the internet connection, "
            f"or the network access settings if this runs in the cloud.")


class DeepSeekBrain:
    """The real mind. It asks DeepSeek for a streamed reply."""

    kind = "deepseek"

    def __init__(self, settings: Settings) -> None:
        self.key = settings.deepseek_key
        self.base = settings.deepseek_base
        names = [settings.deepseek_model, "deepseek-v4-flash", "deepseek-chat"]   # the last one is the old name
        self.models = list(dict.fromkeys(n for n in names if n))
        self.thinking_modes = ["default"] if settings.deepseek_thinking == "default" else ["off", "default"]
        self.working: tuple[str, str] | None = None     # the combination that worked last time

    @property
    def label(self) -> str:
        return self.working[0] if self.working else self.models[0]

    def stream(self, messages: list[dict]) -> Iterator[str]:
        combos = [(m, t) for m in self.models for t in self.thinking_modes]
        if self.working:
            combos = [self.working] + [c for c in combos if c != self.working]
        last: _Retry | None = None
        for model, thinking in combos:
            started = False
            try:
                for piece in self._stream_once(model, thinking, messages):
                    started = True
                    yield piece
                self.working = (model, thinking)
                return
            except _Retry as e:
                if started:
                    raise BrainError("The mind stopped in the middle of a reply. Try again.") from e
                last = e
        raise BrainError(str(last) if last else "The mind did not answer.")

    def _stream_once(self, model: str, thinking: str, messages: list[dict]) -> Iterator[str]:
        body = {"model": model, "messages": messages, "stream": True, "temperature": 0.9, "max_tokens": 220}
        if thinking == "off":
            body["thinking"] = {"type": "disabled"}      # chat should not wait for the model to think first
        req = urllib.request.Request(
            self.base + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:
            detail = describe_http_error(e)
            if e.code in (400, 404, 422):
                raise _Retry(f"DeepSeek did not accept model '{model}' with thinking '{thinking}' (HTTP {e.code}: {detail})") from e
            raise BrainError(friendly_status("DeepSeek", e.code, detail)) from e
        except (urllib.error.URLError, OSError) as e:
            raise BrainError(network_message("DeepSeek", self.base, e)) from e
        with resp:
            try:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue                          # blank lines and ": keep-alive" comments
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue
                    choice = (obj.get("choices") or [{}])[0]
                    text = (choice.get("delta") or {}).get("content")   # reasoning_content is ignored on purpose
                    if text:
                        yield text
            except (OSError, ValueError) as e:
                raise BrainError("The connection to DeepSeek broke in the middle of a reply.") from e


# ---------------------------------------------------------------------------
# 4. The demo mind (no key needed)
# ---------------------------------------------------------------------------

DEMO_REPLIES = [
    (("wants company", "(event"), "[emote:curious] [look:right] Psst. I'm bored in a very quiet way. Tell me something small?"),
    (("hello", "hi ", "hey"), "[emote:happy] Hi! I was just wondering what's behind that door. Want to find out together?"),
    (("door",), "[emote:excited] [look:left] A door! Which one? I've been staring at it for days. It's very closed."),
    (("vacuum", "hoover"), "[emote:surprised] [sound:surprised] Shh! Don't say that word so loudly. It might hear us."),
    (("sleep", "tired", "night", "bed"), "[emote:sleepy] Mmm, okay. But only if you tuck in my ears."),
    (("name", "who are you"), "[emote:happy] I'm Milo! A small robot with big questions. What should I call you?"),
    (("joke",), "[emote:curious] Why did the robot sit in the sunbeam? Because it was solar powered. And also sulking."),
    (("thank",), "[emote:happy] [sound:happy] Anytime. Small favours are my favourite size."),
]
DEMO_DEFAULT = "[emote:curious] Ooh, tell me more. I'm only a demo mind right now, so my answers are a bit canned, but my ears are listening."


class DemoBrain:
    """A few canned replies, streamed slowly like a real mind, so every part can be tried without a key."""

    kind = "demo"
    label = "demo mind"

    def stream(self, messages: list[dict]) -> Iterator[str]:
        last = (messages[-1]["content"] if messages else "").lower()
        reply = next((r for words, r in DEMO_REPLIES if any(w in last for w in words)), DEMO_DEFAULT)
        for i in range(0, len(reply), 4):
            time.sleep(0.015)
            yield reply[i:i + 4]


# ---------------------------------------------------------------------------
# The conversation
# ---------------------------------------------------------------------------

MAX_HISTORY = 12      # how many past messages Milo remembers within a conversation
KEEP_HISTORY = 40


class Mind:
    """One conversation: a brain, its history and the parser."""

    def __init__(self, brain, character_file: Path = CHARACTER_FILE) -> None:
        self.brain = brain
        self.character_file = character_file
        self.history: list[dict] = []
        self._lock = threading.Lock()

    def character(self) -> str:
        try:
            return self.character_file.read_text(encoding="utf-8")      # read every time, so edits apply at once
        except OSError:
            return "You are Milo, a small, curious, friendly robot. Keep replies short."

    def reset(self) -> None:
        with self._lock:
            self.history.clear()

    def chat(self, text: str, event: str, state: dict) -> Iterator[dict]:
        user_content = EVENT_PROMPTS.get(event, "") if event else (text or "").strip()[:600]
        if not user_content:
            yield {"type": "error", "message": "There was nothing to say."}
            return
        system = build_system_prompt(self.character(), state or {})
        with self._lock:
            past = list(self.history[-MAX_HISTORY:])
        messages = [{"role": "system", "content": system}] + past + [{"role": "user", "content": user_content}]
        parser = DirectiveStream()
        started = time.monotonic()
        first_ms = None
        raw: list[str] = []
        try:
            for piece in self.brain.stream(messages):
                if first_ms is None:
                    first_ms = int((time.monotonic() - started) * 1000)
                raw.append(piece)
                yield from parser.feed(piece)
            yield from parser.finish()
        except BrainError as e:
            yield {"type": "error", "message": str(e)}
            return
        reply = "".join(raw).strip()
        if not reply:
            yield {"type": "error", "message": "The mind sent back an empty reply."}
            return
        with self._lock:
            self.history.extend([{"role": "user", "content": user_content}, {"role": "assistant", "content": reply}])
            del self.history[:-KEEP_HISTORY]
        yield {"type": "done", "first_token_ms": first_ms or 0, "total_ms": int((time.monotonic() - started) * 1000)}
