"""Tests for the body's mind client, against the REAL mind server (mock mode) running in this process."""
import contextlib
import io
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from body.mind_client import MindClient, MindError
from mind.config import Settings
from mind.server import App, Handler


class MindClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()          # never touch the real data folder
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.app = App(Settings(mock=True), data_dir=Path(cls.tmp.name))
        cls.server.daemon_threads = True
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.quiet = contextlib.redirect_stdout(io.StringIO())      # the server logs every request
        cls.quiet.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.quiet.__exit__(None, None, None)
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_health(self):
        info = MindClient(self.url).health()
        self.assertTrue(info["ok"])
        self.assertEqual(info["brain"]["kind"], "demo")

    def test_health_is_none_when_nothing_answers(self):
        self.assertIsNone(MindClient("http://127.0.0.1:1").health())

    def test_chat_streams_the_events_in_order(self):
        events = list(MindClient(self.url).chat("hello there", state={"lights": True}))
        self.assertEqual(events[0], {"type": "emote", "name": "happy"})
        self.assertTrue(any(e["type"] == "say" for e in events))
        self.assertEqual(events[-1]["type"], "done")

    def test_chat_with_an_event_and_no_text(self):
        events = list(MindClient(self.url).chat(event="wants_company"))
        self.assertTrue(any(e["type"] == "say" for e in events))

    def test_a_trailing_slash_in_the_address_is_fine(self):
        self.assertTrue(MindClient(self.url + "/").health())
        self.assertTrue(list(MindClient(self.url + "/").chat("hi")))

    def test_the_mind_server_error_event_is_passed_on(self):
        events = list(MindClient(self.url).chat(""))                 # nothing to say
        self.assertEqual(events[0]["type"], "error")

    def test_tts_gives_wav_for_wav_and_for_the_default(self):
        client = MindClient(self.url)
        self.assertTrue(client.tts("Hello there, friend.").startswith(b"RIFF"))
        self.assertTrue(client.tts("Hello there, friend.", "wav").startswith(b"RIFF"))

    def test_tts_errors_carry_the_servers_words(self):
        client = MindClient(self.url)
        with self.assertRaises(MindError) as ctx:
            client.tts("   ")
        self.assertIn("no text to speak", str(ctx.exception))
        with self.assertRaises(MindError) as ctx:
            client.tts("Hello there.", "ogg")
        self.assertIn("mp3", str(ctx.exception))

    def test_unreachable_mind_is_a_plain_error(self):
        client = MindClient("http://127.0.0.1:1")
        with self.assertRaises(MindError) as ctx:
            client.tts("Hello there.")
        self.assertIn("Could not reach Milo's mind", str(ctx.exception))
        with self.assertRaises(MindError):
            list(client.chat("hi"))

    def test_a_bad_address_is_refused(self):
        with self.assertRaises(MindError):
            MindClient("not a url")

    def test_the_mind_refuses_a_stranger_but_not_this_client(self):
        # the client's own requests pass the server's Origin check ...
        self.assertTrue(MindClient(self.url).tts("Hello there."))
        # ... and the check itself is real: the same request from another origin is refused
        client = MindClient(self.url)
        client.base_url = "http://evil.example"
        with self.assertRaises(MindError) as ctx:
            client.tts("Hello there.")
        self.assertIn("did not come from the Milo page", str(ctx.exception))


class RecordingHandler(BaseHTTPRequestHandler):
    """A fake mind that writes down what it was asked and answers with one sentence."""

    protocol_version = "HTTP/1.0"
    asked: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.asked.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        self.wfile.write(b'{"type": "say", "text": "Your tea is ready."}\n{"type": "done"}\n')


class DetailTests(unittest.TestCase):
    def setUp(self):
        self.handler = type("H", (RecordingHandler,), {"asked": []})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.client = MindClient(f"http://127.0.0.1:{self.server.server_address[1]}")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_the_detail_is_sent_with_an_event(self):
        events = list(self.client.chat(event="timer_done", detail="tea", state={"mood": "calm"}))
        self.assertEqual([e["type"] for e in events], ["say", "done"])
        self.assertEqual(self.handler.asked, [{"text": "", "event": "timer_done", "detail": "tea", "state": {"mood": "calm"}}])

    def test_no_detail_means_no_detail_field(self):
        list(self.client.chat("hello"))
        list(self.client.chat(event="focus_break", detail=""))
        self.assertEqual([sorted(body) for body in self.handler.asked], [["event", "state", "text"]] * 2)
        self.assertEqual(self.handler.asked[1]["event"], "focus_break")


class SlowHandler(BaseHTTPRequestHandler):
    """A fake mind that sends one event, then waits until the test says so (or the connection drops)."""

    protocol_version = "HTTP/1.0"
    first_sent = None
    release = None
    dropped = None

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        self.wfile.write(b'{"type": "emote", "name": "happy"}\n')
        self.wfile.flush()
        self.first_sent.set()
        self.release.wait(10)
        try:
            self.wfile.write(b'{"type": "say", "text": "too late"}\n')
            self.wfile.flush()
            time.sleep(0.1)
            self.wfile.write(b'{"type": "done"}\n')
            self.wfile.flush()
        except OSError:
            self.dropped.set()


class StreamingTests(unittest.TestCase):
    def setUp(self):
        self.handler = type("H", (SlowHandler,), {"first_sent": threading.Event(), "release": threading.Event(),
                                                  "dropped": threading.Event()})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.client = MindClient(f"http://127.0.0.1:{self.server.server_address[1]}")

    def tearDown(self):
        self.handler.release.set()
        self.server.shutdown()
        self.server.server_close()

    def test_events_arrive_as_soon_as_their_line_does(self):
        stream = self.client.chat("hi")
        first = next(stream)                                     # the server is still holding the rest back
        self.assertEqual(first, {"type": "emote", "name": "happy"})
        self.assertFalse(self.handler.release.is_set())
        self.handler.release.set()
        self.assertEqual([e["type"] for e in stream], ["say", "done"])

    def test_cancel_from_another_thread_stops_the_stream_promptly(self):
        stream = self.client.chat("hi")
        self.assertEqual(next(stream)["type"], "emote")
        started = time.monotonic()
        threading.Timer(0.2, self.client.cancel).start()
        rest = list(stream)                                      # blocks in the read until cancel() closes the connection
        self.assertEqual(rest, [])
        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse(self.handler.release.is_set())
        self.handler.release.set()                               # now the server tries to write and sees the drop
        self.assertTrue(self.handler.dropped.wait(5))

    def test_cancel_before_the_first_event_and_with_nothing_open(self):
        self.client.cancel()                                     # nothing open: harmless
        stream = self.client.chat("hi")
        self.client.cancel()
        self.assertEqual(list(stream), [])
        self.handler.release.set()                               # a cancel does not spoil the next chat
        self.assertEqual([e["type"] for e in self.client.chat("hi")], ["emote", "say", "done"])

    def test_a_stream_that_drops_by_itself_is_not_mistaken_for_a_cancel(self):
        class Dropper(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"type": "emote", "name": "happy"}\n')

        server = ThreadingHTTPServer(("127.0.0.1", 0), Dropper)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        try:
            events = list(MindClient(f"http://127.0.0.1:{server.server_address[1]}").chat("hi"))
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(events, [{"type": "emote", "name": "happy"}])


if __name__ == "__main__":
    unittest.main()
