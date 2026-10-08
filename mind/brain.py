"""Arty's mind.

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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from mind.config import ROOT, Settings
from mind.personality import Personality

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


DURATION_RE = re.compile(r"^(?:(\d{1,2})\s*h)?\s*(?:(\d{1,4})\s*m(?:in)?)?\s*(?:(\d{1,5})\s*s(?:ec)?)?$")
TIMER_LIMITS = (5, 12 * 3600)          # seconds
FOCUS_LIMITS = (5, 90)                 # minutes


def parse_duration(text: str) -> int | None:
    """'10m', '1h30m', '90s', '1h' or a bare number of minutes, as seconds; None if it is not a duration."""
    text = text.strip().lower()
    if text.isdigit():
        return int(text) * 60
    m = DURATION_RE.match(text)
    if not m or not any(m.groups()):
        return None
    h, mins, secs = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mins * 60 + secs


def parse_timer(value: str) -> dict | None:
    if value in ("cancel", "stop", "off", "clear"):
        return {"type": "timer", "cancel": True}
    first, _, rest = value.partition(" ")
    seconds = parse_duration(first)
    if seconds is None or not TIMER_LIMITS[0] <= seconds <= TIMER_LIMITS[1]:
        return None
    label = re.sub(r"[^\w '\-]", "", rest).strip()[:40]
    return {"type": "timer", "seconds": seconds, "label": label}


def parse_focus(value: str) -> dict | None:
    if value in ("stop", "off", "end", "done"):
        return {"type": "focus", "stop": True}
    value = value.replace("min", "").replace("m", "").strip()
    minutes = int(value) if value.isdigit() else (25 if not value else None)
    if minutes is None:
        return None
    return {"type": "focus", "minutes": max(FOCUS_LIMITS[0], min(FOCUS_LIMITS[1], minutes))}


def parse_tag(tag: str) -> dict | None:
    """Turn the inside of [brackets] into a body action, or None if it is not one we know."""
    t = tag.strip().lower()
    name, _, value = t.partition(":")
    if name.strip() == "timer":
        return parse_timer(value.strip())
    if name.strip() == "focus":
        return parse_focus(value.strip())
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
- [timer:DURATION LABEL] starts a timer, for example [timer:10m tea], [timer:90s] or [timer:1h30m oven]. [timer:cancel] stops all timers. Only when they ask for a timer, and say it out loud too ("Ten minutes for the tea!"). You will be told when it ends.
- [focus:MINUTES] starts focus-buddy mode (25 minutes if you leave the number out): you stay quietly beside them while they work and cheer them on when it ends. [focus:stop] ends it early. Only when they ask for help focusing or working.

## What you remember

You only know what is written under "What you remember about them" and "Your last talks" below (if anything), and what has been said in this conversation. Never make up things you remember about the person, things they did, or past moments together. If you are asked what you know about them and it is not there, say so honestly: you are still getting to know them.

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
    "timer_done": (
        "(Event: the timer you set{detail_for} has just finished. Tell them in one short, cheerful sentence. "
        "Do not start a new timer.)"
    ),
    "focus_break": (
        "(Event: the {detail}-minute focus block you kept with them has just ended. Cheer them on quietly "
        "and suggest a short break: stretch, water, a look out of the window. Do not start a new focus block.)"
    ),
    "good_morning": (
        "(Event: it is morning and the lights just came on. Greet them warmly in one or two short sentences. "
        "If you dreamt about something they said, you may bring it up.)"
    ),
    "good_night": (
        "(Event: it is late and the lights just went off. Say a short, sleepy good night.)"
    ),
}


def event_prompt(event: str, detail: str = "") -> str:
    """The instruction for an event Arty did not hear from the person, or "" for an unknown event."""
    template = EVENT_PROMPTS.get(event, "")
    detail = clean_speech(TAG_IN_TEXT.sub(" ", detail or ""))[:80]
    return template.format(detail=detail or "25", detail_for=f" for '{detail}'" if detail else "")


def context_block(state: dict, notes: list[str] | None = None) -> str:
    """What is going on right now, taken from the body. Only simple values are accepted.

    `notes` are trusted lines from the mind itself, such as how long ago the last talk was."""
    lines = list(notes or [])
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


def build_system_prompt(character: str, state: dict, memory: str = "", notes: list[str] | None = None) -> str:
    """`memory` comes from the memory store; `notes` are lines the mind adds itself (never from the body)."""
    parts = [character.strip(), BODY_PROTOCOL.strip(), memory.strip(), context_block(state or {}, notes or [])]
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

    def complete(self, messages: list[dict], max_tokens: int = 900, json_mode: bool = True) -> str:
        """One whole answer, not streamed. The memory keeper uses it; chat uses stream()."""
        combos = [(m, t) for m in self.models for t in self.thinking_modes]
        if self.working:
            combos = [self.working] + [c for c in combos if c != self.working]
        last: _Retry | None = None
        for model, thinking in combos:
            for use_json in ([True, False] if json_mode else [False]):
                try:
                    return self._complete_once(model, thinking, messages, max_tokens, use_json)
                except _Retry as e:
                    last = e
        raise BrainError(str(last) if last else "The mind did not answer.")

    def _complete_once(self, model: str, thinking: str, messages: list[dict], max_tokens: int, use_json: bool) -> str:
        body = {"model": model, "messages": messages, "stream": False, "temperature": 0.3, "max_tokens": max_tokens}
        if thinking == "off":
            body["thinking"] = {"type": "disabled"}
        if use_json:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            self.base + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                obj = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            detail = describe_http_error(e)
            if e.code in (400, 404, 422):
                raise _Retry(f"DeepSeek did not accept model '{model}' (HTTP {e.code}: {detail})") from e
            raise BrainError(friendly_status("DeepSeek", e.code, detail)) from e
        except (urllib.error.URLError, OSError) as e:
            raise BrainError(network_message("DeepSeek", self.base, e)) from e
        except ValueError as e:
            raise BrainError("DeepSeek sent back something that is not JSON.") from e
        text = (((obj.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if not text:
            raise BrainError("DeepSeek sent back an empty answer.")
        return text

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
    (("name", "who are you"), "[emote:happy] I'm Artemis, Arty for short! A small robot with big questions. What should I call you?"),
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

    def complete(self, messages: list[dict], max_tokens: int = 900, json_mode: bool = True) -> str:
        """The demo mind remembers nothing: an empty but valid answer for the memory keeper."""
        return json.dumps({"summary": "We had a little demo chat.", "mood": "", "facts": [], "merge": [],
                           "outdate": [], "importance": [], "diary": "", "morning_thought": ""})


# ---------------------------------------------------------------------------
# The conversation
# ---------------------------------------------------------------------------

# Situations that show a personality quickly. The editor lets you change this list.
DEFAULT_PROBES = [
    "Hi Arty!",
    "I had a rough day.",
    "What do you think is behind that door?",
    "Can you set a timer for ten minutes?",
    "Are you a real person?",
    "The vacuum cleaner is coming out.",
    "I'm going to bed. Goodnight!",
]

TAG_IN_TEXT = re.compile(r"\[([^\]\n]{1,40})\]")


def review_reply(reply: str, events: list[dict]) -> tuple[list[str], list[str]]:
    """Plain facts and warnings about one reply, so a personality can be judged quickly."""
    said = [e["text"] for e in events if e["type"] == "say"]
    spoken = " ".join(said).strip()
    words = len(spoken.split())
    # Count real sentences. The voice chunks are not the same thing: short sentences are joined for the voice.
    sentences = len([s for s in re.split(r"(?<=[.!?…])\s+", spoken) if s.strip()])
    notes = [f"{sentences} sentence{'s' if sentences != 1 else ''}, {words} words"]
    warnings: list[str] = []
    if sentences > 3:
        warnings.append("more than three sentences")
    if words > 60:
        warnings.append("long: over 60 words")
    first = next((e for e in events if e["type"] in ("emote", "say")), None)
    if not first or first["type"] != "emote":
        warnings.append("does not start with an [emote:...]")
    if "*" in reply or re.search(r"^\s*(#|[-•]\s)", reply, re.M) or EMOJI_RE.search(reply):
        warnings.append("contains markdown, a list or an emoji")
    for tag in TAG_IN_TEXT.findall(reply):
        if parse_tag(tag) is None:
            warnings.append(f"unknown stage direction [{tag}]")
    if not said:
        warnings.append("says nothing")
    return notes, warnings


MAX_HISTORY = 12      # how many messages of the current talk go with each reply
KEEP_HISTORY = 60     # how many messages are kept on disk
TALK_GAP = timedelta(minutes=45)     # this much quiet ends a talk


def now_local() -> datetime:
    return datetime.now().astimezone()


def parse_time(value) -> datetime | None:
    """An ISO time from the history, or None. A time without an offset is taken as local time."""
    try:
        t = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.astimezone()


def split_talks(messages: list[dict], gap: timedelta = TALK_GAP) -> list[list[dict]]:
    """Group messages into talks: a new talk starts after `gap` of quiet. Messages without a time
    (saved before times were kept) count as one old talk."""
    talks: list[list[dict]] = []
    prev: datetime | None = None
    prev_untimed = False
    for m in messages:
        t = parse_time(m.get("time"))
        if not talks:
            new = True
        elif t is None:
            new = not prev_untimed
        else:
            new = prev_untimed or prev is None or t - prev > gap
        if new:
            talks.append([])
        talks[-1].append(m)
        prev_untimed = t is None
        prev = t
    return talks


def ago(delta: timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    if minutes < 90:
        return f"about {max(minutes, 1)} minutes ago"
    hours = minutes // 60
    if hours < 36:
        return f"about {hours} hours ago"
    days = round(hours / 24)
    return f"{days} days ago" if days < 60 else "a long time ago"


def plain_line(m: dict) -> str:
    """One message as a line of text for the prompt, without stage directions."""
    who = "You" if m.get("role") == "assistant" else "They"
    return f"- {who}: {clean_speech(TAG_IN_TEXT.sub(' ', m.get('content') or '')) or '...'}"


class Mind:
    """One conversation: a brain, its history, the memory and the parser.

    If `history_file` is given, the recent conversation is kept there between runs, so Arty
    does not forget what was said when the server restarts. It is a plain file on your own
    computer. Delete it and Arty forgets the conversation (its memory is kept separately,
    see docs/memory-design.md).
    """

    def __init__(self, brain, personality: Personality | None = None, history_file: Path | None = None,
                 memory=None, clock=now_local) -> None:
        self.brain = brain
        self.personality = personality or Personality(CHARACTER_FILE)
        self.history_file = history_file
        self.memory = memory                        # a mind.memory.MemoryStore, or None
        self.clock = clock
        self._lock = threading.Lock()
        self.history: list[dict] = self._load()

    def _load(self) -> list[dict]:
        if not self.history_file:
            return []
        try:
            data = json.loads(self.history_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        good = [m for m in data if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)] if isinstance(data, list) else []
        return good[-KEEP_HISTORY:]

    def _save(self) -> None:
        if not self.history_file:
            return
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.history_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.history, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.history_file)
        except OSError:
            pass                                    # remembering is a bonus, never a reason to fail

    def messages(self) -> list[dict]:
        """A copy of the saved messages (the memory keeper reads them)."""
        with self._lock:
            return [dict(m) for m in self.history]

    def character(self) -> str:
        return self.personality.text()             # read from disk every time, so edits apply at once

    def probe(self, character: str, text: str, state: dict | None = None) -> dict:
        """Ask one question without touching the conversation, using the given personality text.

        This is how a draft personality can be tried before it is saved.
        """
        text = (text or "").strip()[:300]
        system = build_system_prompt(character, state or {"lights": True, "mood": "calm"})
        messages = [{"role": "system", "content": system}, {"role": "user", "content": text}]
        parser = DirectiveStream()
        raw: list[str] = []
        events: list[dict] = []
        try:
            for piece in self.brain.stream(messages):
                raw.append(piece)
                events.extend(parser.feed(piece))
            events.extend(parser.finish())
        except BrainError as e:
            return {"prompt": text, "error": str(e)}
        reply = "".join(raw).strip()
        notes, warnings = review_reply(reply, events)
        return {
            "prompt": text,
            "reply": reply,
            "said": " ".join(e["text"] for e in events if e["type"] == "say"),
            "cues": [f"{e['type']} {e.get('name') or e.get('dir')}" for e in events if e["type"] != "say"],
            "notes": notes,
            "warnings": warnings,
        }

    def reset(self) -> None:
        """Start a fresh conversation. Arty's memory is not touched."""
        with self._lock:
            self.history.clear()
            if self.history_file:
                try:
                    self.history_file.unlink()
                except OSError:
                    pass

    def context(self, user_content: str, now: datetime) -> tuple[list[dict], str, list[str], list[str], bool]:
        """What goes with the next reply: the current talk's messages, the memory text, the trusted
        notes for "Right now", the ids of the facts used, and whether the morning thought was used."""
        with self._lock:
            hist = list(self.history)
        talks = split_talks(hist)
        current: list[dict] = []
        earlier = talks
        if talks:
            last_t = parse_time(talks[-1][-1].get("time"))
            if last_t is not None and now - last_t <= TALK_GAP:
                current, earlier = talks[-1], talks[:-1]
        past = [{"role": m["role"], "content": m["content"]} for m in current[-MAX_HISTORY:]]

        notes: list[str] = []
        sections: list[str] = []
        used: list[str] = []
        morning = False
        state = self.memory.state() if self.memory else {}
        episodes = self.memory.episodes(limit=2) if self.memory else []
        talk_start = parse_time(current[0].get("time")) if current else now

        # How long since the last talk.
        last_end = parse_time(earlier[-1][-1].get("time")) if earlier else None
        if last_end is None and episodes:
            last_end = parse_time(episodes[-1].get("end"))
        if last_end is not None and talk_start is not None:
            notes.append(f"Before this conversation, you last talked {ago(talk_start - last_end)}.")
        elif not earlier and not episodes:
            notes.append("This is the first conversation you remember having with them.")

        if self.memory:
            query = " ".join([m["content"] for m in current[-3:] if m["role"] == "user"] + [user_content])
            facts = self.memory.recall(query)
            used = [f["id"] for f in facts]
            text = self.memory.render(facts, episodes, now)
            if text:
                sections.append(text)
            thought = state.get("morning_thought") or {}
            today = now.date().isoformat()
            talked_today = any((parse_time(m.get("time")) or now.replace(year=1970)).date() == now.date() for m in hist)
            if (thought.get("text") and not thought.get("used") and thought.get("for_date") == today
                    and not talked_today):
                sections.append('## Last night\nYou dreamt about this and may bring it up if it fits: "'
                                + str(thought["text"])[:300] + '"')
                morning = True

        # The end of the last talk, while the memory keeper has not noted it yet.
        if earlier:
            noted = parse_time(state.get("noted_until")) if self.memory else None
            if self.memory is None or noted is None or (last_end is not None and last_end > noted):
                lines = [plain_line(m) for m in earlier[-1][-6:] if not m.get("event")]
                if lines:
                    sections.append("## The end of your last talk (not yet in your memory)\n" + "\n".join(lines))
        return past, "\n\n".join(sections), notes, used, morning

    def system_prompt(self, state: dict, user_content: str = "hello") -> str:
        """The whole prompt the next reply would get (for "See exactly what Arty is told"). Changes nothing."""
        _, memory_text, notes, _, _ = self.context(user_content, self.clock())
        return build_system_prompt(self.character(), state, memory_text, notes)

    def chat(self, text: str, event: str, state: dict, detail: str = "") -> Iterator[dict]:
        user_content = event_prompt(event, detail) if event else (text or "").strip()[:600]
        if not user_content:
            yield {"type": "error", "message": "There was nothing to say."}
            return
        now = self.clock()
        try:
            past, memory_text, notes, used, morning = self.context(user_content, now)
        except Exception as e:                      # memory trouble must never stop a reply
            print(f"memory problem, answering without it: {e}", flush=True)
            with self._lock:
                past = [{"role": m["role"], "content": m["content"]} for m in self.history[-MAX_HISTORY:]]
            memory_text, notes, used, morning = "", [], [], False
        system = build_system_prompt(self.character(), state or {}, memory_text, notes)
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
        stamp = now.isoformat(timespec="seconds")
        asked = {"role": "user", "content": user_content, "time": stamp}
        if event:
            asked["event"] = event                  # not something the person said: the keeper skips it
        with self._lock:
            self.history.extend([asked, {"role": "assistant", "content": reply, "time": self.clock().isoformat(timespec="seconds")}])
            del self.history[:-KEEP_HISTORY]
            self._save()
        if self.memory:
            try:
                if used:
                    self.memory.mark_used(used)
                if morning:
                    thought = dict(self.memory.state().get("morning_thought") or {})
                    thought["used"] = True
                    self.memory.set_state(morning_thought=thought)
            except Exception as e:
                print(f"memory problem after a reply: {e}", flush=True)
        yield {"type": "done", "first_token_ms": first_ms or 0, "total_ms": int((time.monotonic() - started) * 1000)}
