import http.client
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from mind.config import Settings
from mind.server import App, Handler, Limiter

NEW_PERSONALITY = "You are Milo, a tiny robot with a very short personality that is still long enough to save."


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings = Settings(mock=True)
        cls.tmp = tempfile.TemporaryDirectory()          # the tests must never touch your real data folder
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.app = App(settings, data_dir=Path(cls.tmp.name))
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def post_json(self, path, body):
        status, _, payload = self.request("POST", path, body)
        return status, json.loads(payload)

    def test_personality_can_be_read_saved_tried_and_restored(self):
        status, _, payload = self.request("GET", "/api/personality")
        info = json.loads(payload)
        self.assertEqual((status, info["source"]), (200, "default"))
        self.assertIn("Milo", info["text"])

        status, info = self.post_json("/api/personality", {"text": NEW_PERSONALITY, "note": "tiny"})
        self.assertEqual((status, info["source"], info["note"]), (200, "yours", "tiny"))
        _, _, payload = self.request("GET", "/api/prompt")
        prompt = json.loads(payload)["prompt"]
        self.assertIn(NEW_PERSONALITY, prompt)                  # chat uses it straight away
        self.assertIn("[emote:NAME]", prompt)                   # and the rules for the body are still added

        first = info["versions"][0]["id"]
        self.post_json("/api/personality", {"text": NEW_PERSONALITY + " Second.", "note": "second"})
        status, info = self.post_json("/api/personality/restore", {"id": first})
        self.assertEqual(status, 200)
        self.assertTrue(info["text"].strip().endswith("save."))
        status, info = self.post_json("/api/personality/reset", {})
        self.assertEqual((status, info["source"]), (200, "default"))

    def test_bad_personalities_are_refused_politely(self):
        status, info = self.post_json("/api/personality", {"text": "hi"})
        self.assertEqual(status, 400)
        self.assertIn("short", info["error"])
        status, info = self.post_json("/api/personality/restore", {"id": "banana"})
        self.assertEqual(status, 400)

    def test_probe_tries_a_draft_without_saving_or_touching_the_conversation(self):
        self.request("POST", "/api/reset", {})
        before = json.loads(self.request("GET", "/api/personality")[2])["text"]
        status, out = self.post_json("/api/probe", {"character": NEW_PERSONALITY, "prompts": ["hello", "tell me a joke", "  "]})
        self.assertEqual(status, 200)
        self.assertEqual(out["mode"], "demo")
        self.assertEqual([r["prompt"] for r in out["results"]], ["hello", "tell me a joke"])
        for result in out["results"]:
            self.assertTrue(result["said"])
            self.assertIn("cues", result)
            self.assertEqual(result["warnings"], [])
        self.assertEqual(len(self.server.app.mind.history), 0)
        self.assertEqual(json.loads(self.request("GET", "/api/personality")[2])["text"], before)

    def test_probe_without_prompts_uses_the_standard_situations(self):
        status, out = self.post_json("/api/probe", {})
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(out["results"]), 5)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body).encode() if body is not None else None
        hdrs = {"Content-Type": "application/json"} if body is not None else {}
        hdrs.update(headers or {})
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        return resp.status, resp.getheader("Content-Type", ""), payload

    def test_health_reports_demo_mode(self):
        status, _, payload = self.request("GET", "/api/health")
        info = json.loads(payload)
        self.assertEqual(status, 200)
        self.assertEqual(info["brain"]["kind"], "demo")
        self.assertEqual(info["voice"]["kind"], "babble")

    def test_serves_the_simulator_page(self):
        status, ctype, payload = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"Milo", payload)

    def test_chat_streams_actions_in_order_and_ends_with_done(self):
        status, ctype, payload = self.request("POST", "/api/chat", {"text": "hello there", "state": {"lights": True}})
        self.assertEqual(status, 200)
        self.assertIn("ndjson", ctype)
        events = [json.loads(line) for line in payload.decode().splitlines() if line]
        self.assertEqual(events[0], {"type": "emote", "name": "happy"})
        self.assertTrue(any(e["type"] == "say" for e in events))
        self.assertEqual(events[-1]["type"], "done")
        self.assertIn("first_token_ms", events[-1])

    def test_chat_remembers_the_conversation(self):
        self.request("POST", "/api/reset", {})
        self.request("POST", "/api/chat", {"text": "hello"})
        self.assertEqual(len(self.server.app.mind.history), 2)
        self.request("POST", "/api/reset", {})
        self.assertEqual(len(self.server.app.mind.history), 0)

    def test_event_without_text_works(self):
        _, _, payload = self.request("POST", "/api/chat", {"event": "wants_company"})
        events = [json.loads(line) for line in payload.decode().splitlines() if line]
        self.assertTrue(any(e["type"] == "say" for e in events))

    def test_chat_rejects_bad_input(self):
        status, _, _ = self.request("POST", "/api/chat", None, {"Content-Length": "0"})
        self.assertEqual(status, 400)
        status, _, payload = self.request("POST", "/api/chat", {"text": ""})
        events = [json.loads(line) for line in payload.decode().splitlines() if line]
        self.assertEqual(events[0]["type"], "error")

    def test_tts_returns_audio(self):
        status, ctype, payload = self.request("POST", "/api/tts", {"text": "Hello there, friend."})
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "audio/wav")
        self.assertTrue(payload.startswith(b"RIFF"))
        status, _, _ = self.request("POST", "/api/tts", {"text": "   "})
        self.assertEqual(status, 400)

    def test_tts_format_defaults_to_mp3_asks_and_accepts_wav(self):
        # the demo voice always makes WAV, and says so, whichever format was asked for
        for body in ({"text": "Hello there."}, {"text": "Hello there.", "format": "mp3"}, {"text": "Hello there.", "format": "wav"}):
            status, ctype, payload = self.request("POST", "/api/tts", body)
            self.assertEqual((status, ctype), (200, "audio/wav"), body)
            self.assertTrue(payload.startswith(b"RIFF"))

    def test_tts_refuses_an_unknown_format_in_plain_words(self):
        for fmt in ("ogg", "", 7, ["wav"], None):
            status, _, payload = self.request("POST", "/api/tts", {"text": "Hello there.", "format": fmt})
            self.assertEqual(status, 400, fmt)
            self.assertIn("mp3", json.loads(payload)["error"])

    def test_other_websites_are_refused(self):
        status, _, _ = self.request("POST", "/api/chat", {"text": "hi"}, {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/tts", {"text": "hi"}, {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)

    def test_same_origin_is_accepted(self):
        origin = f"http://127.0.0.1:{self.port}"
        status, _, _ = self.request("POST", "/api/tts", {"text": "hi there"}, {"Origin": origin})
        self.assertEqual(status, 200)

    def test_wrong_host_header_is_refused(self):
        status, _, _ = self.request("GET", "/api/health", None, {"Host": "evil.example:80"})
        self.assertEqual(status, 403)

    def test_unknown_paths_and_traversal_are_not_served(self):
        for path in ("/nope", "/../../etc/passwd", "/mind/.env", "/.env"):
            status, _, payload = self.request("GET", path)
            self.assertEqual(status, 404, path)
            self.assertNotIn(b"root:", payload)


class LimiterTests(unittest.TestCase):
    def test_limit(self):
        limiter = Limiter(2)
        self.assertTrue(limiter.allow())
        self.assertTrue(limiter.allow())
        self.assertFalse(limiter.allow())

    def test_cost(self):
        limiter = Limiter(10)
        self.assertTrue(limiter.allow(6))
        self.assertFalse(limiter.allow(6))
        self.assertTrue(limiter.allow(4))


if __name__ == "__main__":
    unittest.main()
