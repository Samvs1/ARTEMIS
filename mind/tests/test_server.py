import http.client
import json
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from mind.config import Settings
from mind.server import App, Handler, Limiter

NEW_PERSONALITY = "You are Milo, a tiny robot with a very short personality that is still long enough to save."


class HttpHelpers:
    """Ask a test server (on self.port) for things."""

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

    def post_json(self, path, body, headers=None):
        status, _, payload = self.request("POST", path, body, headers)
        return status, json.loads(payload)

    def get_json(self, path):
        status, _, payload = self.request("GET", path)
        return status, json.loads(payload)


class ServerTests(HttpHelpers, unittest.TestCase):
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


class FakeKeeper:
    """Stands in for mind.keeper.MemoryKeeper: the same small interface, and it records how it was asked."""

    def __init__(self, brain=None, store=None, history=None):
        self.args = (brain, store, history)
        self.running = True
        self.status = "waiting for a talk to finish"
        self.calls = []
        self.talks, self.dreams = 2, True

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def note_talks(self, force=False):
        self.calls.append(("note", force))
        self.status = f"noted {self.talks} talk(s)"
        return self.talks

    def dream(self, force=False):
        self.calls.append(("dream", force))
        self.status = "dreamed just now"
        return self.dreams


class RecordingBrain:
    """A brain that keeps the messages it is sent, so a test can see what Milo was told."""

    kind = "demo"
    label = "recording brain"

    def __init__(self):
        self.sent = []

    def stream(self, messages):
        self.sent.append(messages)
        yield "[emote:happy] Hello again."


class MemoryEndpointTests(HttpHelpers, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()          # every test gets its own empty memory
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.app = self.server.app = App(Settings(mock=True), data_dir=Path(self.tmp.name))
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()     # a quick poll keeps shutdown quick
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def remember(self, text="They are called Sam.", **extra):
        status, out = self.post_json("/api/memory/fact", dict(text=text, **extra))
        self.assertEqual(status, 200, out)
        return out["fact"]

    def facts(self):
        return self.get_json("/api/memory")[1]["facts"]

    # ---- reading ----
    def test_memory_starts_empty_and_says_the_keeper_is_off_in_demo_mode(self):
        status, info = self.get_json("/api/memory")
        self.assertEqual(status, 200)
        self.assertEqual(info["person"], "owner")
        self.assertEqual((info["facts"], info["episodes"], info["diary"], info["state"]), ([], [], [], {}))
        self.assertEqual(info["keeper"], {"running": False, "last": "off in demo mode"})

    def test_memory_shows_facts_episodes_diary_state_and_the_keepers_status(self):
        self.app.keeper = FakeKeeper()
        old = self.remember("They moved to Ghent.")
        self.app.memory.outdate_fact(old["id"])
        self.app.memory.add_episode("2026-10-07T18:31:00+02:00", "2026-10-07T18:52:00+02:00", 9, "We talked about the move.", "tired")
        self.app.memory.write_diary("2026-10-07", "Today we talked about a move.")
        self.app.memory.set_state(last_dream="2026-10-07")
        info = self.get_json("/api/memory")[1]
        self.assertEqual([(f["text"], f["status"]) for f in info["facts"]], [("They moved to Ghent.", "outdated")])   # outdated ones are included
        self.assertEqual(info["episodes"][0]["summary"], "We talked about the move.")
        self.assertEqual(info["diary"], [{"date": "2026-10-07", "text": "Today we talked about a move."}])
        self.assertEqual(info["state"]["last_dream"], "2026-10-07")
        self.assertEqual(info["keeper"], {"running": True, "last": "waiting for a talk to finish"})

    def test_memory_is_only_for_this_computer(self):
        status, _, _ = self.request("GET", "/api/memory", None, {"Host": "evil.example:80"})
        self.assertEqual(status, 403)

    # ---- adding and editing ----
    def test_a_fact_added_by_hand_is_pinned_and_dated(self):
        fact = self.remember("  They   are called Sam. ", kind="identity", importance=9)
        self.assertEqual((fact["text"], fact["kind"], fact["importance"], fact["pinned"], fact["status"]),
                         ("They are called Sam.", "identity", 9, True, "active"))
        self.assertTrue(fact["id"].startswith("f_") and fact["created"])
        self.assertEqual([f["id"] for f in self.facts()], [fact["id"]])
        self.assertEqual(self.remember("They like tea.", pinned=False)["pinned"], False)
        self.assertEqual(self.remember("They like jam.")["kind"], "other")           # defaults

    def test_a_fact_can_be_edited_pinned_and_unpinned(self):
        fact = self.remember("They like tea.", pinned=False)
        status, out = self.post_json("/api/memory/fact", {"id": fact["id"], "text": "They like green tea.", "importance": 7})
        self.assertEqual((status, out["fact"]["text"], out["fact"]["importance"], out["fact"]["pinned"]), (200, "They like green tea.", 7, False))
        status, out = self.post_json("/api/memory/fact", {"id": fact["id"], "pinned": True})
        self.assertEqual((status, out["fact"]["pinned"], out["fact"]["text"]), (200, True, "They like green tea."))      # only what was sent changes
        status, out = self.post_json("/api/memory/fact", {"id": fact["id"], "pinned": False, "kind": "preference"})
        self.assertEqual((status, out["fact"]["pinned"], out["fact"]["kind"]), (200, False, "preference"))
        self.assertEqual(len(self.facts()), 1)

    def test_bad_facts_are_refused_in_plain_words(self):
        fact = self.remember("They like tea.")
        bad = [
            ({"text": ""}, "Write"), ({"text": "   "}, "Write"), ({}, "Write"), ({"text": 5}, "Write"), ({"text": "[]"}, "Write"),
            ({"text": "x" * 201}, "too long"),
            ({"text": "ok", "kind": "banana"}, "kind"), ({"text": "ok", "kind": 3}, "kind"),
            ({"text": "ok", "importance": 0}, "Importance"), ({"text": "ok", "importance": 11}, "Importance"),
            ({"text": "ok", "importance": "high"}, "Importance"), ({"text": "ok", "importance": True}, "Importance"),
            ({"text": "ok", "importance": 5.5}, "Importance"),
            ({"text": "ok", "pinned": "yes"}, "Pinned"),
            ({"id": fact["id"], "text": ""}, "Write"), ({"id": fact["id"], "text": "y" * 201}, "too long"),
            ({"id": fact["id"], "kind": "banana"}, "kind"), ({"id": fact["id"], "importance": 99}, "Importance"),
            ({"id": 7, "text": "ok"}, "which"),
        ]
        for body, word in bad:
            status, out = self.post_json("/api/memory/fact", body)
            self.assertEqual(status, 400, body)
            self.assertIn(word, out["error"], body)
        self.assertEqual([(f["text"], f["importance"]) for f in self.facts()], [("They like tea.", 5)])      # nothing changed

    def test_an_importance_typed_as_digits_is_accepted_and_the_edge_values_work(self):
        self.assertEqual(self.remember("One.", importance="3")["importance"], 3)
        self.assertEqual(self.remember("Two.", importance=1)["importance"], 1)
        self.assertEqual(self.remember("Three.", importance=10)["importance"], 10)

    def test_editing_an_unknown_fact_is_a_404(self):
        status, out = self.post_json("/api/memory/fact", {"id": "f_nope", "text": "They like tea."})
        self.assertEqual(status, 404)
        self.assertIn("no memory", out["error"])
        self.assertEqual(self.facts(), [])

    # ---- forgetting ----
    def test_a_fact_can_be_forgotten_for_good(self):
        keep, gone = self.remember("They like tea."), self.remember("They like jam.")
        status, out = self.post_json("/api/memory/forget", {"id": gone["id"]})
        self.assertEqual((status, out), (200, {"ok": True}))
        self.assertEqual([f["id"] for f in self.facts()], [keep["id"]])
        status, _ = self.post_json("/api/memory/forget", {"id": gone["id"]})
        self.assertEqual(status, 404)                                                  # already gone

    def test_forgetting_needs_an_id_that_exists(self):
        self.remember("They like tea.")
        for body in ({}, {"id": ""}, {"id": 5}, {"id": None}):
            self.assertEqual(self.post_json("/api/memory/forget", body)[0], 400, body)
        self.assertEqual(self.post_json("/api/memory/forget", {"id": "f_nope"})[0], 404)
        self.assertEqual(len(self.facts()), 1)

    def test_forget_everything_needs_the_word_forget(self):
        self.remember("They like tea.")
        self.app.memory.add_episode("2026-10-07T18:31:00+02:00", "2026-10-07T18:52:00+02:00", 3, "We chatted.")
        self.request("POST", "/api/chat", {"text": "hello"})
        self.assertEqual(len(self.app.mind.history), 2)
        for body in ({}, {"confirm": ""}, {"confirm": "yes"}, {"confirm": "Forget"}, {"confirm": True}, {"confirm": ["forget"]}):
            status, out = self.post_json("/api/memory/forget-everything", body)
            self.assertEqual(status, 400, body)
            self.assertIn("forget", out["error"])
        status, _, _ = self.request("POST", "/api/memory/forget-everything", None, {"Content-Length": "0"})
        self.assertEqual(status, 400)
        self.assertEqual(len(self.facts()), 1)                                          # nothing was lost
        self.assertEqual(len(self.app.memory.episodes()), 1)
        self.assertEqual(len(self.app.mind.history), 2)

    def test_forget_everything_deletes_the_memory_folder_and_the_conversation(self):
        self.remember("They like tea.")
        self.app.memory.write_diary("2026-10-07", "A quiet day.")
        self.request("POST", "/api/chat", {"text": "hello"})
        self.assertTrue(self.app.memory.dir.exists())
        status, out = self.post_json("/api/memory/forget-everything", {"confirm": "forget"})
        self.assertEqual((status, out), (200, {"ok": True}))
        self.assertFalse(self.app.memory.dir.exists())
        self.assertEqual(self.app.mind.history, [])
        info = self.get_json("/api/memory")[1]
        self.assertEqual((info["facts"], info["episodes"], info["diary"]), ([], [], []))
        self.remember("They like jam.")                                                 # and Milo can start again
        self.assertEqual(len(self.facts()), 1)

    # ---- trying things out ----
    def test_note_now_and_dream_now_need_the_real_mind(self):
        for path in ("/api/memory/note-now", "/api/memory/dream-now"):
            status, out = self.post_json(path, {})
            self.assertEqual(status, 409, path)
            self.assertEqual(out["error"], "Memory needs the real mind (a DeepSeek key).")

    def test_note_now_asks_the_keeper_to_note_everything(self):
        self.app.keeper = FakeKeeper()
        status, out = self.post_json("/api/memory/note-now", {})
        self.assertEqual((status, out), (200, {"ok": True, "noted": 2}))
        self.assertEqual(self.app.keeper.calls, [("note", True)])
        self.app.keeper.talks = 0
        self.assertEqual(self.post_json("/api/memory/note-now", {})[1]["noted"], 0)

    def test_dream_now_asks_the_keeper_to_dream_and_reports_its_status(self):
        self.app.keeper = FakeKeeper()
        status, out = self.post_json("/api/memory/dream-now", {})
        self.assertEqual((status, out), (200, {"ok": True, "dreamed": True, "status": "dreamed just now"}))
        self.assertEqual(self.app.keeper.calls, [("dream", True)])
        self.app.keeper.dreams = False
        self.assertEqual(self.post_json("/api/memory/dream-now", {})[1]["dreamed"], False)

    def test_a_keeper_that_breaks_gives_a_plain_answer_not_a_crash(self):
        class Broken(FakeKeeper):
            def note_talks(self, force=False):
                raise RuntimeError("secret internals")
        self.app.keeper = Broken()
        status, out = self.post_json("/api/memory/note-now", {})
        self.assertEqual(status, 500)
        self.assertNotIn("secret", out["error"])

    # ---- the rules for every change ----
    def test_other_websites_cannot_read_or_change_the_memory(self):
        self.app.keeper = FakeKeeper()
        fact = self.remember("They like tea.")
        evil = {"Origin": "http://evil.example"}
        attempts = [("/api/memory/fact", {"text": "They are hacked."}), ("/api/memory/fact", {"id": fact["id"], "text": "Changed."}),
                    ("/api/memory/forget", {"id": fact["id"]}), ("/api/memory/forget-everything", {"confirm": "forget"}),
                    ("/api/memory/note-now", {}), ("/api/memory/dream-now", {})]
        for path, body in attempts:
            self.assertEqual(self.post_json(path, body, evil)[0], 403, path)
            self.assertEqual(self.post_json(path, body, {"Host": "evil.example:80"})[0], 403, path)
        self.assertEqual([(f["id"], f["text"]) for f in self.facts()], [(fact["id"], "They like tea.")])
        self.assertEqual(self.app.keeper.calls, [])

    def test_the_same_origin_is_accepted(self):
        origin = {"Origin": f"http://127.0.0.1:{self.port}"}
        self.assertEqual(self.post_json("/api/memory/fact", {"text": "They like tea."}, origin)[0], 200)

    def test_unknown_memory_paths_are_not_found(self):
        self.assertEqual(self.post_json("/api/memory/banana", {})[0], 404)
        self.assertEqual(self.request("GET", "/api/memory/fact")[0], 404)

    # ---- the memory reaches Milo ----
    def test_the_prompt_page_shows_what_is_remembered(self):
        self.assertNotIn("## What you remember about them", self.get_json("/api/prompt")[1]["prompt"])      # nothing yet: no section
        self.remember("They drink an oat-milk latte every morning.")
        prompt = self.get_json("/api/prompt")[1]["prompt"]
        self.assertIn("## What you remember about them", prompt)
        self.assertIn("They drink an oat-milk latte every morning.", prompt)
        self.assertIn("[emote:NAME]", prompt)                          # the rest of the prompt is still there

    def test_a_forgotten_or_outdated_fact_leaves_the_prompt(self):
        fact = self.remember("They drink an oat-milk latte every morning.")
        self.post_json("/api/memory/forget", {"id": fact["id"]})
        self.assertNotIn("oat-milk", self.get_json("/api/prompt")[1]["prompt"])
        other = self.remember("They live in Ghent.")
        self.app.memory.outdate_fact(other["id"])
        self.assertNotIn("Ghent", self.get_json("/api/prompt")[1]["prompt"])

    def test_a_chat_puts_a_remembered_fact_into_what_milo_is_told(self):
        brain = self.app.mind.brain = RecordingBrain()
        self.remember("They are called Sam; you call them Captain Biscuit.", kind="identity", importance=9)
        status, _, payload = self.request("POST", "/api/chat", {"text": "hello Milo", "state": {"lights": True}})
        self.assertEqual(status, 200)
        self.assertIn(b'"done"', payload)
        system = brain.sent[0][0]
        self.assertEqual(system["role"], "system")
        self.assertIn("Captain Biscuit", system["content"])
        self.assertIn("## What you remember about them", system["content"])
        self.assertEqual(brain.sent[0][-1], {"role": "user", "content": "hello Milo"})

    # ---- how the app is put together ----
    def test_the_demo_mind_has_a_memory_but_no_keeper(self):
        self.assertIsNone(self.app.keeper)
        self.assertEqual(self.app.memory.dir, Path(self.tmp.name) / "memory" / "owner")
        self.assertIs(self.app.mind.memory, self.app.memory)

    def test_the_real_mind_gets_a_keeper_that_reads_its_messages(self):
        fake = types.ModuleType("mind.keeper")
        fake.MemoryKeeper = FakeKeeper
        with mock.patch.dict(sys.modules, {"mind.keeper": fake}):
            with tempfile.TemporaryDirectory() as tmp:
                real = App(Settings(deepseek_key="sk-test"), data_dir=Path(tmp))
                brain, store, history = real.keeper.args
                self.assertIs(brain, real.brain)
                self.assertIs(store, real.memory)
                self.assertEqual(history, real.mind.messages)
                demo = App(Settings(deepseek_key="sk-test", mock=True), data_dir=Path(tmp))        # --mock ignores keys
                self.assertIsNone(demo.keeper)

    def test_the_real_mind_still_works_when_the_keeper_is_missing(self):
        with mock.patch.dict(sys.modules, {"mind.keeper": None}):          # None makes the import fail
            with tempfile.TemporaryDirectory() as tmp:
                real = App(Settings(deepseek_key="sk-test"), data_dir=Path(tmp))
        self.assertIsNone(real.keeper)
        self.assertIs(real.mind.memory, real.memory)


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


class ChatDetailTests(unittest.TestCase):
    def test_the_timer_label_reaches_the_mind(self):
        from mind.brain import Mind
        seen = []

        class Brain:
            kind, label = "demo", "demo"

            def stream(self, messages):
                seen.append(messages[-1]["content"])
                yield "[emote:happy] Tea time!"

        mind = Mind(Brain())
        list(mind.chat("", "timer_done", {}, "tea"))
        self.assertIn("for 'tea'", seen[0])


class CheckTests(unittest.TestCase):
    """python3 mind/server.py --check (check-keys.bat), with a stand-in voice."""

    class Voice:
        kind, label, working = "fish", "Fish Audio", "s2-pro"

        def synthesize(self, text, fmt="mp3"):
            return b"ID3" + bytes(400), "audio/mpeg"

    def run_check(self, **settings):
        import contextlib
        import io
        import tempfile
        from mind.server import App, run_check
        with tempfile.TemporaryDirectory() as folder:
            app = App(Settings(fish_key="test-key", **settings), data_dir=Path(folder))
            app.voice = self.Voice()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = run_check(app)
        return code, out.getvalue()

    def test_a_missing_voice_id_is_reported_as_something_to_fix(self):
        code, text = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("FISH_AUDIO_VOICE_ID", text)

    def test_with_a_voice_id_the_voice_check_passes(self):
        code, text = self.run_check(fish_voice="abc123")
        self.assertEqual(code, 0)
        self.assertNotIn("random voice", text)
