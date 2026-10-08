"""Tests for the face link (body/face.py). Everything stays on 127.0.0.1 and nothing needs a browser."""
from __future__ import annotations

import http.client
import json
import time
import unittest
from pathlib import Path
from unittest import mock

from body.face import MAX_INPUT, FaceServer

SIM_FILE = Path(__file__).resolve().parent.parent.parent / "sim" / "index.html"


def wait_until(check, seconds: float = 3.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.01)
    return check()


class Stream:
    """A page listening to /events."""

    def __init__(self, port: int, host_header: str | None = None) -> None:
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        self.conn.putrequest("GET", "/events", skip_host=True)
        self.conn.putheader("Host", host_header or f"127.0.0.1:{port}")
        self.conn.endheaders()
        self.resp = self.conn.getresponse()      # the server has already counted us in when the headers arrive

    def lines(self):
        while True:
            line = self.resp.readline()
            if not line:
                return
            yield line.decode("utf-8").rstrip("\n")

    def next_event(self) -> dict | None:
        for line in self.lines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        return None

    def close(self) -> None:
        self.resp.close()
        self.conn.close()


class FaceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("body.face.log")            # keep the test output quiet
        patcher.start()
        self.addCleanup(patcher.stop)
        self.face = FaceServer("127.0.0.1", 0, SIM_FILE)
        self.face.start()
        self.addCleanup(self.face.stop)
        self.port = self.face.port

    def open_stream(self) -> Stream:
        stream = Stream(self.port)
        self.addCleanup(stream.close)
        return stream

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
        headers = dict(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            conn.putrequest(method, path, skip_host=True)
            conn.putheader("Host", headers.pop("Host", f"127.0.0.1:{self.port}"))
            for name, value in headers.items():
                conn.putheader(name, value)
            if body is not None:
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body)
            resp = conn.getresponse()
            return resp.status, resp.getheader("Content-Type") or "", resp.read()
        finally:
            conn.close()

    def post_input(self, payload, headers: dict | None = None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return self.request("POST", "/input", body, {"Content-Type": "application/json", **(headers or {})})


class ServingTests(FaceTestCase):
    def test_port_zero_gives_a_real_port(self):
        self.assertGreater(self.port, 0)

    def test_serves_the_page(self):
        for path in ("/", "/index.html", "/?face=1&captions=1"):
            status, kind, body = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertTrue(kind.startswith("text/html"), path)
            self.assertEqual(body, SIM_FILE.read_bytes(), path)

    def test_unknown_path_is_not_found(self):
        self.assertEqual(self.request("GET", "/secrets")[0], 404)
        self.assertEqual(self.request("POST", "/api/chat", b"{}")[0], 404)

    def test_missing_page_file_is_a_clean_404(self):
        self.face.sim_file = Path("/nonexistent/index.html")
        self.assertEqual(self.request("GET", "/")[0], 404)


class StreamTests(FaceTestCase):
    def test_events_arrive_in_order(self):
        stream = self.open_stream()
        self.assertTrue(stream.resp.getheader("Content-Type").startswith("text/event-stream"))
        sent = [{"type": "state", "name": "listening"}, {"type": "emote", "name": "happy"}, {"type": "mouth", "level": 0.5}]
        for event in sent:
            self.face.send(event)
        self.assertEqual([stream.next_event() for _ in sent], sent)

    def test_a_page_that_connects_late_gets_the_current_state_and_mood(self):
        self.face.send({"type": "state", "name": "idle"})
        self.face.send({"type": "emote", "name": "sad"})
        self.face.send({"type": "state", "name": "speaking"})
        self.face.send({"type": "mouth", "level": 0.4})           # momentary: not replayed
        stream = self.open_stream()
        self.assertEqual(stream.next_event(), {"type": "state", "name": "speaking"})
        self.assertEqual(stream.next_event(), {"type": "emote", "name": "sad"})
        self.face.send({"type": "look", "dir": "up"})
        self.assertEqual(stream.next_event(), {"type": "look", "dir": "up"})

    def test_two_pages_both_receive(self):
        first, second = self.open_stream(), self.open_stream()
        self.assertEqual(self.face.clients(), 2)
        self.face.send({"type": "look", "dir": "left"})
        self.face.send({"type": "caption", "text": "hello ☃"})
        for stream in (first, second):
            self.assertEqual(stream.next_event(), {"type": "look", "dir": "left"})
            self.assertEqual(stream.next_event(), {"type": "caption", "text": "hello ☃"})

    def test_a_page_leaving_does_not_break_the_others(self):
        leaving, staying = self.open_stream(), self.open_stream()
        leaving.close()

        def gone() -> bool:
            self.face.send({"type": "mouth", "level": 0.2})      # the first write after a close may still "work"
            return self.face.clients() == 1
        self.assertTrue(wait_until(gone))
        self.face.send({"type": "state", "name": "idle"})
        seen = None
        while seen != {"type": "state", "name": "idle"}:
            seen = staying.next_event()
            self.assertIsNotNone(seen)

    def test_a_page_that_never_reads_does_not_slow_the_others(self):
        self.open_stream()                       # connected, but nobody reads from it
        reader = self.open_stream()
        started = time.monotonic()
        for i in range(150):
            self.face.send({"type": "mouth", "level": i / 150})
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(reader.next_event(), {"type": "mouth", "level": 0.0})

    def test_a_page_that_falls_too_far_behind_is_dropped(self):
        self.face.client_queue = 3
        client = self.face.add_client()          # nothing reads its queue
        for i in range(10):
            self.face.send({"type": "mouth", "level": i})
        self.assertEqual(self.face.clients(), 0)
        self.assertIsNone(client.events.get_nowait())        # it was told to stop

    def test_keepalive_comment_when_quiet(self):
        self.face.keepalive = 0.05
        stream = self.open_stream()
        self.assertIn(": keepalive", list(line for _, line in zip(range(4), stream.lines())))

    def test_events_that_are_not_json_are_ignored(self):
        stream = self.open_stream()
        self.face.send({"type": "bad", "value": object()})
        self.face.send({"type": "state", "name": "idle"})
        self.assertEqual(stream.next_event(), {"type": "state", "name": "idle"})

    def test_stop_ends_the_streams(self):
        stream = self.open_stream()
        self.face.stop()
        self.assertIsNone(stream.next_event())               # the stream ended instead of hanging
        with self.assertRaises(OSError):
            http.client.HTTPConnection("127.0.0.1", self.port, timeout=1).request("GET", "/")


class InputTests(FaceTestCase):
    def test_a_tap_lands_in_the_queue(self):
        status, _, _ = self.post_input({"type": "touch", "where": "face"})
        self.assertEqual(status, 200)
        self.assertEqual(self.face.inputs.get(timeout=1), {"type": "touch", "where": "face"})

    def test_a_tap_from_the_same_page_origin_is_accepted(self):
        origin = {"Origin": f"http://127.0.0.1:{self.port}"}
        self.assertEqual(self.post_input({"type": "touch", "where": "face"}, origin)[0], 200)
        self.assertEqual(self.face.inputs.qsize(), 1)

    def test_taps_come_out_oldest_first(self):
        for n in range(3):
            self.post_input({"type": "touch", "n": n})
        self.assertEqual([self.face.inputs.get(timeout=1)["n"] for _ in range(3)], [0, 1, 2])

    def test_rubbish_is_refused(self):
        for body in (b"not json", b"[1, 2]", b'"touch"', b""):
            self.assertEqual(self.post_input(body)[0], 400, body)
        self.assertTrue(self.face.inputs.empty())

    def test_oversize_body_is_refused(self):
        body = json.dumps({"type": "touch", "pad": "x" * (MAX_INPUT + 10)}).encode()
        self.assertEqual(self.post_input(body)[0], 413)
        self.assertTrue(self.face.inputs.empty())
        self.assertEqual(self.post_input({"type": "touch"})[0], 200)      # and the server is fine afterwards

    def test_a_body_just_under_the_limit_is_taken(self):
        body = json.dumps({"type": "touch", "pad": "x" * (MAX_INPUT - 100)}).encode()
        self.assertLessEqual(len(body), MAX_INPUT)
        self.assertEqual(self.post_input(body)[0], 200)


class LocalOnlyTests(FaceTestCase):
    def test_wrong_host_is_refused_everywhere(self):
        evil = {"Host": f"evil.example:{self.port}"}
        self.assertEqual(self.request("GET", "/", headers=evil)[0], 403)
        self.assertEqual(self.request("GET", "/events", headers=evil)[0], 403)
        self.assertEqual(self.post_input({"type": "touch"}, evil)[0], 403)
        self.assertTrue(self.face.inputs.empty())
        self.assertEqual(self.face.clients(), 0)

    def test_localhost_names_are_fine(self):
        self.assertEqual(self.request("GET", "/", headers={"Host": f"localhost:{self.port}"})[0], 200)

    def test_wrong_origin_is_refused(self):
        for origin in ("http://evil.example", f"http://evil.example:{self.port}", "null"):
            self.assertEqual(self.post_input({"type": "touch"}, {"Origin": origin})[0], 403, origin)
        self.assertTrue(self.face.inputs.empty())


if __name__ == "__main__":
    unittest.main()
