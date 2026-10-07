"""Tests for the DeepSeek and Fish Audio clients, against small fake servers.

The fakes copy the behaviour we have to be ready for: a retired model name, an option the
API refuses, a wrong key, a streamed reply with keep-alive lines and reasoning text.
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mind.brain import BrainError, DeepSeekBrain, Mind
from mind.config import Settings
from mind.voice import FishVoice, VoiceError


class FakeServer:
    """Runs `handler` on a local port for the length of a test."""

    def __init__(self, handler):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def sse(*pieces, reasoning=None):
    lines = [": keep-alive", ""]
    if reasoning:
        lines += ["data: " + json.dumps({"choices": [{"delta": {"reasoning_content": reasoning}}]}), ""]
    for piece in pieces:
        lines += ["data: " + json.dumps({"choices": [{"delta": {"content": piece}}]}), ""]
    lines += ["data: [DONE]", ""]
    return "\n".join(lines).encode()


def deepseek_handler(behaviour, seen):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"model": body.get("model"), "thinking": body.get("thinking"), "auth": self.headers.get("Authorization")})
            status, payload, ctype = behaviour(body, self.headers)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(payload)
    return H


def brain_for(url, **extra):
    return DeepSeekBrain(Settings(deepseek_key="test-key", deepseek_base=url, **extra))


MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]


class DeepSeekTests(unittest.TestCase):
    def test_streams_text_and_ignores_reasoning_and_keepalives(self):
        seen = []
        ok = lambda body, headers: (200, sse("[emote:happy] Hi", " there!", reasoning="hmm"), "text/event-stream")
        with FakeServer(deepseek_handler(ok, seen)) as fake:
            brain = brain_for(fake.url)
            text = "".join(brain.stream(MESSAGES))
        self.assertEqual(text, "[emote:happy] Hi there!")
        self.assertEqual(seen[0]["auth"], "Bearer test-key")
        self.assertEqual(seen[0]["model"], "deepseek-v4-flash")
        self.assertEqual(seen[0]["thinking"], {"type": "disabled"})     # chat should not wait to think
        self.assertEqual(brain.working, ("deepseek-v4-flash", "off"))

    def test_retries_without_the_thinking_option_if_it_is_refused(self):
        seen = []

        def behaviour(body, headers):
            if "thinking" in body:
                return 400, json.dumps({"error": {"message": "unknown field: thinking"}}).encode(), "application/json"
            return 200, sse("Hello!"), "text/event-stream"

        with FakeServer(deepseek_handler(behaviour, seen)) as fake:
            brain = brain_for(fake.url)
            self.assertEqual("".join(brain.stream(MESSAGES)), "Hello!")
            self.assertEqual(brain.working, ("deepseek-v4-flash", "default"))
            seen.clear()
            "".join(brain.stream(MESSAGES))                              # the next call goes straight to what worked
            self.assertEqual(len(seen), 1)

    def test_falls_back_to_the_old_model_name(self):
        seen = []

        def behaviour(body, headers):
            if body["model"] != "deepseek-chat":
                return 404, json.dumps({"error": {"message": "Model Not Exist"}}).encode(), "application/json"
            return 200, sse("Old name works."), "text/event-stream"

        with FakeServer(deepseek_handler(behaviour, seen)) as fake:
            brain = brain_for(fake.url)
            self.assertEqual("".join(brain.stream(MESSAGES)), "Old name works.")
        self.assertEqual(brain.working[0], "deepseek-chat")

    def test_a_chosen_model_is_tried_first(self):
        seen = []
        ok = lambda body, headers: (200, sse("ok"), "text/event-stream")
        with FakeServer(deepseek_handler(ok, seen)) as fake:
            brain = brain_for(fake.url, deepseek_model="deepseek-v4-pro")
            "".join(brain.stream(MESSAGES))
        self.assertEqual(seen[0]["model"], "deepseek-v4-pro")

    def test_wrong_key_gives_a_plain_message_and_no_retry_storm(self):
        seen = []
        bad = lambda body, headers: (401, b"Authentication Fails (governor)", "text/plain")
        with FakeServer(deepseek_handler(bad, seen)) as fake:
            with self.assertRaises(BrainError) as ctx:
                "".join(brain_for(fake.url).stream(MESSAGES))
        self.assertIn("rejected the API key", str(ctx.exception))
        self.assertIn("Authentication Fails", str(ctx.exception))
        self.assertEqual(len(seen), 1)

    def test_no_balance_and_busy_messages(self):
        for code, words in ((402, "no balance"), (429, "limiting requests"), (503, "having trouble")):
            seen = []
            fail = lambda body, headers, code=code: (code, b'{"error":{"message":"x"}}', "application/json")
            with FakeServer(deepseek_handler(fail, seen)) as fake:
                with self.assertRaises(BrainError) as ctx:
                    "".join(brain_for(fake.url).stream(MESSAGES))
            self.assertIn(words, str(ctx.exception))

    def test_unreachable_server_is_explained(self):
        brain = brain_for("http://127.0.0.1:1")          # nothing listens on port 1
        with self.assertRaises(BrainError) as ctx:
            "".join(brain.stream(MESSAGES))
        self.assertIn("Could not reach DeepSeek", str(ctx.exception))

    def test_a_whole_conversation_turn_through_the_mind(self):
        seen = []
        ok = lambda body, headers: (200, sse("[emote:curious] Ooh, ", "a door! ", "[look:left] Which one?"), "text/event-stream")
        with FakeServer(deepseek_handler(ok, seen)) as fake:
            mind = Mind(brain_for(fake.url))
            events = list(mind.chat("there is a door", "", {"lights": True}))
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds[0], "emote")
        self.assertIn("look", kinds)
        self.assertEqual(kinds[-1], "done")
        self.assertEqual(len(mind.history), 2)


def fish_handler(behaviour, seen):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"model": self.headers.get("model"), "auth": self.headers.get("Authorization"), "body": body})
            status, payload, ctype = behaviour(body, self.headers)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(payload)
    return H


MP3ISH = b"ID3" + bytes(400)


def voice_for(url, **extra):
    return FishVoice(Settings(fish_key="test-key", fish_base=url, **extra))


class FishTests(unittest.TestCase):
    def test_finds_the_model_name_that_works_and_remembers_it(self):
        seen = []

        def behaviour(body, headers):
            if headers.get("model") != "s2-pro":
                return 422, json.dumps({"status": 422, "message": "invalid model header"}).encode(), "application/json"
            return 200, MP3ISH, "audio/mpeg"

        with FakeServer(fish_handler(behaviour, seen)) as fake:
            voice = voice_for(fake.url)
            audio, ctype = voice.synthesize("Hello there.")
            self.assertEqual((ctype, len(audio)), ("audio/mpeg", len(MP3ISH)))
            self.assertEqual(voice.working, "s2-pro")
            self.assertEqual([s["model"] for s in seen], ["s2.1-pro", "s2-pro"])
            seen.clear()
            voice.synthesize("Again.")
            self.assertEqual([s["model"] for s in seen], ["s2-pro"])
        self.assertEqual(seen[0]["auth"], "Bearer test-key")
        self.assertEqual(seen[0]["body"]["format"], "mp3")
        self.assertNotIn("reference_id", seen[0]["body"])

    def test_voice_id_is_sent_when_set(self):
        seen = []
        ok = lambda body, headers: (200, MP3ISH, "audio/mpeg")
        with FakeServer(fish_handler(ok, seen)) as fake:
            voice_for(fake.url, fish_voice="abc123").synthesize("Hi.")
        self.assertEqual(seen[0]["body"]["reference_id"], "abc123")

    def test_a_bad_voice_id_is_blamed_on_the_voice_not_the_model(self):
        seen = []
        bad = lambda body, headers: (400, json.dumps({"message": "reference_id not found"}).encode(), "application/json")
        with FakeServer(fish_handler(bad, seen)) as fake:
            with self.assertRaises(VoiceError) as ctx:
                voice_for(fake.url, fish_voice="nope").synthesize("Hi.")
        self.assertIn("FISH_AUDIO_VOICE_ID", str(ctx.exception))
        self.assertEqual(len(seen), 1)

    def test_pinned_model_is_the_only_one_tried(self):
        seen = []
        bad = lambda body, headers: (422, json.dumps({"message": "invalid model header"}).encode(), "application/json")
        with FakeServer(fish_handler(bad, seen)) as fake:
            with self.assertRaises(VoiceError):
                voice_for(fake.url, fish_model="s1").synthesize("Hi.")
        self.assertEqual([s["model"] for s in seen], ["s1"])

    def test_every_model_refused_explains_what_to_do(self):
        bad = lambda body, headers: (422, json.dumps({"message": "invalid model header"}).encode(), "application/json")
        with FakeServer(fish_handler(bad, [])) as fake:
            with self.assertRaises(VoiceError) as ctx:
                voice_for(fake.url).synthesize("Hi.")
        self.assertIn("invalid model header", str(ctx.exception))

    def test_wrong_key_message(self):
        bad = lambda body, headers: (401, json.dumps({"status": 401, "message": "this route requires an api-key"}).encode(), "application/json")
        with FakeServer(fish_handler(bad, [])) as fake:
            with self.assertRaises(VoiceError) as ctx:
                voice_for(fake.url).synthesize("Hi.")
        self.assertIn("rejected the API key", str(ctx.exception))

    def test_a_non_audio_answer_is_an_error(self):
        odd = lambda body, headers: (200, b'{"ok":true}', "application/json")
        with FakeServer(fish_handler(odd, [])) as fake:
            with self.assertRaises(VoiceError):
                voice_for(fake.url).synthesize("Hi.")


if __name__ == "__main__":
    unittest.main()
