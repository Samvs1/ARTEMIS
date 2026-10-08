#!/usr/bin/env python3
"""Milo's mind server.

Run it from the project folder:

    python3 mind/server.py            use your keys from .env (or the demo mind if there are none)
    python3 mind/server.py --mock     ignore any keys and use the demo mind and the babble voice
    python3 mind/server.py --check    test your keys with one tiny request each, then exit

Then open http://127.0.0.1:8000 in your browser. The page is the Milo simulator, now with a
chat box. The keys stay in this program. The browser never sees them.

It uses only what comes with Python, so there is nothing to install.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mind.brain import (BrainError, CHARACTER_FILE, DEFAULT_PROBES, DeepSeekBrain, DemoBrain, Mind,   # noqa: E402
                        clean_speech)
from mind.config import ROOT, Settings, load_env_file                                                # noqa: E402
from mind.memory import FACT_CHARS, KINDS, MemoryStore                                               # noqa: E402
from mind.personality import MAX_CHARS, Personality, PersonalityError                                # noqa: E402
from mind.voice import DemoVoice, FishVoice, VoiceError                                              # noqa: E402

VERSION = "0.2"
SIM_FILE = ROOT / "sim" / "index.html"
DATA_DIR = ROOT / "data"          # your own local files: your personality, its history, the conversation
MAX_BODY = 100_000


def log(message: str) -> None:
    print(time.strftime("%H:%M:%S ") + message, flush=True)


def make_printing_safe() -> None:
    """Some consoles (older Windows ones) cannot show emoji or accents. Show a code instead of crashing."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")      # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


NO_VOICE_ID_NOTE = ("No FISH_AUDIO_VOICE_ID is set, so Fish Audio picks a random voice for every sentence. "
                     "Pick a voice at fish.audio, copy its ID and put it after FISH_AUDIO_VOICE_ID= in .env.")


class Limiter:
    """A safety net: at most `per_hour` units per hour, so a bug cannot burn through your credit."""

    def __init__(self, per_hour: int) -> None:
        self.per_hour = per_hour
        self._events: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def allow(self, cost: int = 1) -> bool:
        now = time.time()
        with self._lock:
            while self._events and self._events[0][0] < now - 3600:
                self._events.popleft()
            if sum(c for _, c in self._events) + cost > self.per_hour:
                return False
            self._events.append((now, cost))
            return True


class App:
    """Everything the server needs: the settings, the mind and the voice."""

    def __init__(self, settings: Settings, data_dir: Path | None = None) -> None:
        data_dir = data_dir or Path(os.environ.get("MILO_DATA_DIR") or DATA_DIR)
        self.settings = settings
        self.brain = DeepSeekBrain(settings) if settings.use_deepseek else DemoBrain()
        self.voice = FishVoice(settings) if settings.use_fish else DemoVoice()
        self.personality = Personality(CHARACTER_FILE, data_dir / "character.md", data_dir / "character-history.json")
        # What Milo knows about the owner lives in plain files (data/memory/owner). The page can read and fix them.
        self.memory = MemoryStore(data_dir / "memory", person="owner")
        # Only the real mind keeps its conversation between runs. The demo mind has nothing worth remembering.
        self.mind = Mind(self.brain, self.personality, history_file=data_dir / "history.json" if settings.use_deepseek else None,
                         memory=self.memory)
        # The keeper is the part that does the remembering after a talk (it needs the real mind to read the talk).
        self.keeper = self._make_keeper() if settings.use_deepseek and not settings.mock else None
        self.chat_limit = Limiter(settings.max_chats_per_hour)
        self.tts_limit = Limiter(settings.max_tts_chars_per_hour)

    def _make_keeper(self):
        try:
            from mind.keeper import MemoryKeeper
        except ImportError:
            log("memory keeper not found: Milo will use what it remembers, but will not learn anything new")
            return None
        return MemoryKeeper(self.brain, self.memory, self.mind.messages)

    def memory_overview(self) -> dict:
        keeper = ({"running": bool(self.keeper.running), "last": str(self.keeper.status)} if self.keeper
                  else {"running": False, "last": "off in demo mode"})
        return {
            "person": self.memory.person,
            "facts": self.memory.facts(include_outdated=True),
            "episodes": self.memory.episodes(limit=20),          # the newest last
            "diary": self.memory.diary(14),                      # the newest first
            "state": self.memory.state(),
            "keeper": keeper,
        }

    def health(self) -> dict:
        return {
            "ok": True,
            "version": VERSION,
            "brain": {"kind": self.brain.kind, "label": self.brain.label},
            "voice": {"kind": self.voice.kind, "label": self.voice.label},
        }


MEMORY_ACTIONS = {
    "/api/memory/fact": "fact",
    "/api/memory/forget": "forget",
    "/api/memory/forget-everything": "forget-everything",
    "/api/memory/note-now": "note-now",
    "/api/memory/dream-now": "dream-now",
}


def parse_fact_fields(data: dict, adding: bool) -> tuple[dict, str]:
    """The fields of a fact sent by the memory page, checked. Returns (fields, problem); problem is "" when all is well."""
    fields: dict = {}
    if adding or "text" in data:
        text = data.get("text")
        text = " ".join(text.replace("[", "").replace("]", "").split()) if isinstance(text, str) else ""
        if not text:
            return {}, "Write what Milo should remember."
        if len(text) > FACT_CHARS:
            return {}, f"That is too long for one memory ({len(text)} characters; the limit is {FACT_CHARS}). Try a shorter sentence."
        fields["text"] = text
    kind = data.get("kind")
    if kind is not None:
        if not isinstance(kind, str) or kind not in KINDS:
            return {}, "That kind of memory is not known. The kinds are: " + ", ".join(KINDS) + "."
        fields["kind"] = kind
    importance = data.get("importance")
    if importance is not None:
        if isinstance(importance, str) and importance.strip().isdigit():
            importance = int(importance)
        if isinstance(importance, bool) or not isinstance(importance, int) or not 1 <= importance <= 10:
            return {}, "Importance must be a whole number from 1 (trivia) to 10 (their name)."
        fields["importance"] = importance
    pinned = data.get("pinned")
    if pinned is not None:
        if not isinstance(pinned, bool):
            return {}, "Pinned must be true or false."
        fields["pinned"] = pinned
    return fields, ""


class Handler(BaseHTTPRequestHandler):
    server_version = "MiloMind/" + VERSION
    protocol_version = "HTTP/1.0"      # the connection closes after each reply, which is what streaming needs

    @property
    def app(self) -> App:
        return self.server.app          # type: ignore[attr-defined]

    def log_message(self, format, *args):   # noqa: A002  (quiet: we log what matters ourselves)
        pass

    # ---- small helpers ----
    def send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, obj: dict) -> None:
        self.send_bytes(status, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0 or length > MAX_BODY:
            return None
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def host_ok(self) -> bool:
        """Only answer requests addressed to localhost, so other websites cannot reach this server."""
        if self.app.settings.host not in ("127.0.0.1", "localhost", "::1"):
            return True
        port = self.server.server_address[1]
        allowed = {f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"}
        return (self.headers.get("Host") or "") in allowed

    def origin_ok(self) -> bool:
        """The page must come from this server. A page on another website is refused."""
        origin = self.headers.get("Origin")
        if not origin:
            return True                  # tools like curl send no Origin
        host = self.headers.get("Host") or ""
        return origin in (f"http://{host}", f"https://{host}")

    # ---- GET ----
    def do_GET(self) -> None:
        if not self.host_ok():
            return self.send_json(403, {"error": "Wrong host."})
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html", "/sim/index.html"):
            try:
                self.send_bytes(200, SIM_FILE.read_bytes(), "text/html; charset=utf-8")
            except OSError:
                self.send_json(404, {"error": "sim/index.html was not found."})
        elif path == "/api/health":
            self.send_json(200, self.app.health())
        elif path == "/api/personality":
            self.send_json(200, self.app.personality.describe())
        elif path == "/api/personality/default":
            self.send_json(200, {"text": self.app.personality.default_text()})
        elif path == "/api/prompt":
            sample = {"local_time": "(the day and time, filled in at every reply)", "lights": True, "mood": "calm"}
            self.send_json(200, {"prompt": self.app.mind.system_prompt(sample)})
        elif path == "/api/memory":
            self.send_json(200, self.app.memory_overview())
        elif path == "/favicon.ico":
            self.send_bytes(204, b"", "image/x-icon")
        else:
            self.send_json(404, {"error": "Not found."})

    # ---- POST ----
    def do_POST(self) -> None:
        if not (self.host_ok() and self.origin_ok()):
            return self.send_json(403, {"error": "This request did not come from the Milo page."})
        path = self.path.split("?", 1)[0]
        if path == "/api/chat":
            self.handle_chat()
        elif path == "/api/tts":
            self.handle_tts()
        elif path == "/api/reset":
            self.app.mind.reset()
            self.send_json(200, {"ok": True})
        elif path == "/api/personality":
            self.handle_personality("save")
        elif path == "/api/personality/restore":
            self.handle_personality("restore")
        elif path == "/api/personality/reset":
            self.handle_personality("reset")
        elif path == "/api/probe":
            self.handle_probe()
        elif path in MEMORY_ACTIONS:
            self.handle_memory(MEMORY_ACTIONS[path])
        else:
            self.send_json(404, {"error": "Not found."})

    def handle_personality(self, action: str) -> None:
        data = self.read_json() or {}
        try:
            if action == "save":
                result = self.app.personality.save(str(data.get("text") or ""), str(data.get("note") or ""))
                log(f"personality saved ({len(result['text']):,} characters)")
            elif action == "restore":
                try:
                    version = int(data.get("id"))
                except (TypeError, ValueError):
                    raise PersonalityError("Say which version to go back to.") from None
                result = self.app.personality.restore(version)
                log(f"personality went back to version {version}")
            else:
                result = self.app.personality.reset()
                log("personality went back to the shipped default")
        except PersonalityError as e:
            return self.send_json(400, {"error": str(e)})
        self.send_json(200, result)

    def handle_memory(self, action: str) -> None:
        """The memory page: add or fix a fact, forget one, forget everything, note a talk now, dream now."""
        try:
            status, payload = self.memory_action(action, self.read_json() or {})
        except BrainError as e:
            status, payload = 502, {"error": str(e)}
        except ValueError as e:                  # the store refused the words (for example only square brackets)
            status, payload = 400, {"error": str(e)}
        except OSError as e:                     # a file could not be written or erased
            log("memory problem: " + str(e))
            status, payload = 500, {"error": str(e) or "Milo's memory files could not be changed."}
        except Exception as e:                   # the keeper has its own care, but the page should still get a plain answer
            log(f"memory problem: {e}")
            status, payload = 500, {"error": "Something went wrong with Milo's memory. The server log says more."}
        self.send_json(status, payload)

    def memory_action(self, action: str, data: dict) -> tuple[int, dict]:
        """Do one thing to the memory. Returns the status and the answer to send."""
        memory = self.app.memory
        gone = (404, {"error": "Milo has no memory like that (it may already be gone)."})
        if action == "fact":
            fact_id = data.get("id")
            if fact_id is not None and not isinstance(fact_id, str):
                return 400, {"error": "Say which memory to change."}
            fields, problem = parse_fact_fields(data, adding=fact_id is None)
            if problem:
                return 400, {"error": problem}
            if fact_id is None:
                fields.setdefault("pinned", True)            # something you tell Milo by hand is always remembered
                fact = memory.add_fact(source="added on the memory page", **fields)
                log("memory  a fact was added by hand")
            else:
                fact = memory.update_fact(fact_id, **fields)
                if fact is None:
                    return gone
                log("memory  a fact was changed by hand")
            return 200, {"ok": True, "fact": fact}

        if action == "forget":
            fact_id = data.get("id")
            if not isinstance(fact_id, str) or not fact_id.strip():
                return 400, {"error": "Say which memory to forget."}
            if not memory.delete_fact(fact_id):
                return gone
            log("memory  one fact was forgotten")
            return 200, {"ok": True}

        if action == "forget-everything":
            if data.get("confirm") != "forget":
                return 400, {"error": 'To forget everything, send {"confirm": "forget"}.'}
            memory.forget_everything()
            self.app.mind.reset()
            log("memory  everything about the owner was forgotten, and the conversation")
            return 200, {"ok": True}

        # note-now and dream-now ask the AI, so they need the keeper (which needs the real mind)
        keeper = self.app.keeper
        if keeper is None:
            return 409, {"error": "Memory needs the real mind (a DeepSeek key)."}
        if not self.app.chat_limit.allow():
            return 429, {"error": "That is a lot of asking for one hour. Try again a little later."}
        if action == "note-now":
            noted = keeper.note_talks(force=True)
            log(f"memory  noted {noted} talk(s) on request")
            return 200, {"ok": True, "noted": noted}
        dreamed = bool(keeper.dream(force=True))
        log("memory  dreamed on request" if dreamed else "memory  nothing to dream about")
        return 200, {"ok": True, "dreamed": dreamed, "status": str(keeper.status)}

    def handle_probe(self) -> None:
        """Ask a list of questions with a personality text (even an unsaved draft) and report the replies."""
        data = self.read_json() or {}
        raw = data.get("prompts")
        prompts = [str(p).strip() for p in raw if str(p).strip()][:10] if isinstance(raw, list) else []
        prompts = prompts or list(DEFAULT_PROBES)
        character = data.get("character")
        character = character if isinstance(character, str) and character.strip() else self.app.personality.text()
        if len(character) > MAX_CHARS:
            return self.send_json(400, {"error": "That personality is too long to try."})
        if not self.app.chat_limit.allow(len(prompts)):
            return self.send_json(429, {"error": "That is a lot of asking for one hour. Try again a little later."})
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda p: self.app.mind.probe(character, p), prompts))
        log(f"probe {len(prompts)} situations in {int((time.monotonic() - started) * 1000)} ms")
        self.send_json(200, {"results": results, "mode": self.app.brain.kind})

    def handle_chat(self) -> None:
        data = self.read_json()
        if data is None:
            return self.send_json(400, {"error": 'Send JSON like {"text": "hello"}.'})
        text = str(data.get("text") or "")
        event = str(data.get("event") or "")
        detail = str(data.get("detail") or "")[:80]        # e.g. the timer's label; cleaned again by the mind
        state = data.get("state") if isinstance(data.get("state"), dict) else {}
        if not self.app.chat_limit.allow():
            return self.send_json(429, {"error": "That is a lot of chatting for one hour. Milo is taking a short rest."})
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        label = self.app.brain.label
        try:
            for event_out in self.app.mind.chat(text, event, state, detail):
                self.wfile.write((json.dumps(event_out, ensure_ascii=False) + "\n").encode("utf-8"))
                self.wfile.flush()
                if event_out["type"] == "done":
                    log(f"chat  {(text or event)[:50]!r} -> first words after {event_out['first_token_ms']} ms ({label})")
                elif event_out["type"] == "error":
                    log("chat  problem: " + event_out["message"])
        except (BrokenPipeError, ConnectionResetError):
            log("chat  the page stopped listening (interrupted)")

    def handle_tts(self) -> None:
        data = self.read_json()
        text = clean_speech(str((data or {}).get("text") or ""))[:400]
        if not text:
            return self.send_json(400, {"error": "There was no text to speak."})
        fmt = (data or {}).get("format", "mp3")
        if fmt not in ("mp3", "wav"):
            return self.send_json(400, {"error": 'The audio format must be "mp3" or "wav".'})
        if not self.app.tts_limit.allow(len(text)):
            return self.send_json(429, {"error": "Milo has spoken a lot this hour. Try again later."})
        started = time.monotonic()
        try:
            audio, content_type = self.app.voice.synthesize(text, fmt)
        except VoiceError as e:
            log("voice problem: " + str(e))
            return self.send_json(502, {"error": str(e)})
        self.send_bytes(200, audio, content_type)
        log(f"voice {len(text)} characters -> {len(audio) // 1024} KB in {int((time.monotonic() - started) * 1000)} ms")


def run_check(app: App) -> int:
    """Try each key once, with the smallest possible request."""
    print("Checking your setup. This sends two tiny requests and costs a fraction of a cent.\n")
    ok = True
    if app.settings.use_deepseek:
        try:
            reply = "".join(app.brain.stream([{"role": "user", "content": "Say hello in three words."}]))
            print(f"  Mind  : OK. DeepSeek model '{app.brain.label}' answered: {reply.strip()[:60]!r}")
        except BrainError as e:
            ok = False
            print(f"  Mind  : FAILED. {e}")
    else:
        print("  Mind  : demo mind. No DEEPSEEK_API_KEY was found, so there is nothing to check.")
    if app.settings.use_fish:
        try:
            audio, content_type = app.voice.synthesize("Hello from Milo.")
            print(f"  Voice : OK. Fish Audio model '{app.voice.working}' returned {len(audio) // 1024} KB of {content_type}")
            if not app.settings.fish_voice:
                ok = False
                print("  Voice : " + NO_VOICE_ID_NOTE)
        except VoiceError as e:
            ok = False
            print(f"  Voice : FAILED. {e}")
    else:
        print("  Voice : babble voice. No FISH_AUDIO_API_KEY was found, so there is nothing to check.")
    print("\nAll good." if ok else "\nSomething needs fixing. The messages above say what.")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Milo's mind server")
    parser.add_argument("--mock", action="store_true", help="ignore any keys and use the demo mind and the babble voice")
    parser.add_argument("--check", action="store_true", help="test your keys with one tiny request each, then exit")
    parser.add_argument("--host", help="address to listen on (default 127.0.0.1, this computer only)")
    parser.add_argument("--port", type=int, help="port to listen on (default 8000)")
    args = parser.parse_args(argv)

    make_printing_safe()
    loaded = load_env_file()
    settings = Settings.from_env()
    settings.mock = args.mock
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port
    app = App(settings)

    if args.check:
        return run_check(app)

    try:
        server = ThreadingHTTPServer((settings.host, settings.port), Handler)
    except OSError as e:
        print(f"Could not start on {settings.host}:{settings.port} ({e}).")
        print(f"Another program may be using that port. Try: python3 mind/server.py --port {settings.port + 1}")
        return 1
    server.app = app                      # type: ignore[attr-defined]
    server.daemon_threads = True

    mind_line = (f"DeepSeek ({app.brain.label})" if settings.use_deepseek
                 else "demo mind (put DEEPSEEK_API_KEY in .env for the real one)")
    voice_line = ("Fish Audio" if settings.use_fish
                  else "babble voice (put FISH_AUDIO_API_KEY in .env for the real one)")
    if settings.mock:
        mind_line, voice_line = "demo mind (--mock)", "babble voice (--mock)"
    url_host = "127.0.0.1" if settings.host in ("0.0.0.0", "") else settings.host
    print("Milo's mind server")
    print(f"  Mind  : {mind_line}")
    print(f"  Voice : {voice_line}")
    if settings.use_fish and not settings.mock and not settings.fish_voice:
        print("  NOTE  : " + NO_VOICE_ID_NOTE)
    if app.keeper:
        try:
            memory_where = app.memory.dir.relative_to(ROOT).as_posix()
        except ValueError:
            memory_where = str(app.memory.dir)
        print(f"  Memory: on ({memory_where})")
        app.keeper.start()
    elif settings.use_deepseek and not settings.mock:
        print("  Memory: only what is already saved is used (the memory keeper was not found)")
    else:
        print("  Memory: off in demo mode")
    if loaded:
        print(f"  Keys  : read from .env ({', '.join(loaded)})")
    print(f"  Open  : http://{url_host}:{settings.port}")
    print("  Stop  : press Ctrl+C\n")
    if settings.host not in ("127.0.0.1", "localhost", "::1"):
        print("  WARNING: this server is open to your network. Anyone on it can use your API credit.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if app.keeper:
            app.keeper.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
