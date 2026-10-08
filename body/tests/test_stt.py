"""Tests for speech to text, against a fake OpenAI on a local port. No key, no network, no faster-whisper needed."""
import contextlib
import io
import json
import sys
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from body.config import BodySettings
from body.stt import (FakeTranscriber, LocalWhisper, OpenAITranscriber, SttError, TranscriberChain, build_chain,
                      multipart_form)

WAV = b"RIFF" + bytes(60)


class FakeOpenAI:
    """Answers POST /v1/audio/transcriptions with `behaviour(headers, raw_body)` -> (status, body, content type)."""

    def __init__(self, behaviour):
        seen = self.seen = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                seen.append({"path": self.path, "headers": self.headers, "body": raw})
                status, payload, ctype = behaviour(self.headers, raw)
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except OSError:
                    pass                                  # the client gave up (the timeout test)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def ok(text):
    return lambda headers, raw: (200, json.dumps({"text": text}).encode(), "application/json")


def parse_multipart(headers, raw):
    """Split a multipart body into {field name: (filename, content type, bytes)}."""
    ctype = headers["Content-Type"]
    assert ctype.startswith("multipart/form-data; boundary="), ctype
    boundary = ("--" + ctype.split("boundary=")[1]).encode()
    parts = {}
    for chunk in raw.split(boundary)[1:-1]:
        head, _, body = chunk.partition(b"\r\n\r\n")
        body = body[:-2] if body.endswith(b"\r\n") else body
        lines = head.strip().decode().split("\r\n")
        disposition = lines[0]
        name = disposition.split('name="')[1].split('"')[0]
        filename = disposition.split('filename="')[1].split('"')[0] if "filename=" in disposition else None
        part_type = lines[1].split(": ", 1)[1] if len(lines) > 1 else None
        parts[name] = (filename, part_type, body)
    return parts


class MultipartTests(unittest.TestCase):
    def test_body_is_well_formed(self):
        body, ctype = multipart_form({"a": "1", "b": "two"}, "file", "x.wav", "audio/wav", b"\x00\r\n\xff")
        parts = parse_multipart({"Content-Type": ctype}, body)
        self.assertEqual(parts["a"], (None, None, b"1"))
        self.assertEqual(parts["file"], ("x.wav", "audio/wav", b"\x00\r\n\xff"))      # binary data is passed through untouched


class OpenAITests(unittest.TestCase):
    def transcriber(self, fake, **extra):
        return OpenAITranscriber("sk-secret-key", base_url=fake.url, **extra)

    def test_sends_the_right_request_and_returns_the_text(self):
        with FakeOpenAI(ok("  hello arty ")) as fake:
            text = self.transcriber(fake, model="my-model").transcribe(WAV)
        self.assertEqual(text, "hello arty")
        request = fake.seen[0]
        self.assertEqual(request["path"], "/v1/audio/transcriptions")
        self.assertEqual(request["headers"]["Authorization"], "Bearer sk-secret-key")
        parts = parse_multipart(request["headers"], request["body"])
        self.assertEqual(parts["file"], ("utterance.wav", "audio/wav", WAV))
        self.assertEqual(parts["model"][2], b"my-model")
        self.assertEqual(parts["language"][2], b"en")
        self.assertEqual(parts["response_format"][2], b"json")

    def test_the_base_url_can_come_from_the_environment(self):
        with FakeOpenAI(ok("from env")) as fake:
            with mock.patch.dict("os.environ", {"OPENAI_BASE_URL": fake.url + "/"}):
                text = OpenAITranscriber("sk-secret-key").transcribe(WAV)
        self.assertEqual(text, "from env")

    def test_silence_is_empty_text_not_an_error(self):
        with FakeOpenAI(ok("")) as fake:
            self.assertEqual(self.transcriber(fake).transcribe(WAV), "")

    def test_no_key_means_not_available(self):
        self.assertFalse(OpenAITranscriber("").available())
        self.assertTrue(OpenAITranscriber("sk-x").available())
        with self.assertRaises(SttError):
            OpenAITranscriber("").transcribe(WAV)

    def test_errors_are_explained_in_plain_words_and_never_show_the_key(self):
        cases = ((401, "rejected the API key"), (429, "limiting requests"), (500, "having trouble"), (503, "having trouble"),
                 (400, "HTTP 400"))
        for code, words in cases:
            # OpenAI really does echo a part of the key in its 401 message
            body = json.dumps({"error": {"message": "Incorrect API key provided: sk-secret-key"}}).encode()
            with FakeOpenAI(lambda h, r, code=code, body=body: (code, body, "application/json")) as fake:
                with self.assertRaises(SttError) as ctx:
                    self.transcriber(fake).transcribe(WAV)
            self.assertIn(words, str(ctx.exception), code)
            self.assertNotIn("sk-secret-key", str(ctx.exception), code)
            self.assertEqual(len(fake.seen), 1, "no retry storm")

    def test_a_slow_answer_is_a_timeout_message(self):
        release = threading.Event()

        def slow(headers, raw):
            release.wait(5)
            return 200, b"{}", "application/json"

        with FakeOpenAI(slow) as fake:
            try:
                with self.assertRaises(SttError) as ctx:
                    self.transcriber(fake, timeout=0.3).transcribe(WAV)
            finally:
                release.set()
        self.assertIn("longer than", str(ctx.exception))

    def test_unreachable_server_is_a_network_message(self):
        with self.assertRaises(SttError) as ctx:
            OpenAITranscriber("sk-secret-key", base_url="http://127.0.0.1:1/v1").transcribe(WAV)
        self.assertIn("Could not reach OpenAI", str(ctx.exception))
        self.assertNotIn("sk-secret-key", str(ctx.exception))

    def test_a_reply_that_is_not_a_transcript_is_an_error(self):
        for payload in (b"<html>nope</html>", b"[1, 2]"):
            with FakeOpenAI(lambda h, r, payload=payload: (200, payload, "application/json")) as fake:
                with self.assertRaises(SttError):
                    self.transcriber(fake).transcribe(WAV)


def no_log():
    return contextlib.redirect_stdout(io.StringIO())


class ChainTests(unittest.TestCase):
    def test_the_first_available_one_answers(self):
        first, second = FakeTranscriber(["one"], name="a"), FakeTranscriber(["two"], name="b")
        self.assertEqual(TranscriberChain([first, second]).transcribe(WAV), ("one", "a"))
        self.assertEqual(second.calls, [])

    def test_skips_unavailable_and_falls_back_on_failure_in_order(self):
        off = FakeTranscriber(["x"], name="off", is_available=False)
        broken = FakeTranscriber([SttError("the cloud is down")], name="cloud")
        good = FakeTranscriber(["hello"], name="local")
        with no_log():
            self.assertEqual(TranscriberChain([off, broken, good]).transcribe(WAV), ("hello", "local"))
        self.assertEqual((off.calls, len(broken.calls)), ([], 1))

    def test_empty_text_counts_as_success(self):
        quiet, other = FakeTranscriber([""], name="a"), FakeTranscriber(["words"], name="b")
        self.assertEqual(TranscriberChain([quiet, other]).transcribe(WAV), ("", "a"))
        self.assertEqual(other.calls, [])

    def test_when_all_fail_the_error_lists_each_reason(self):
        chain = TranscriberChain([FakeTranscriber([SttError("no network")], name="openai"),
                                  FakeTranscriber(name="local", is_available=False)])
        with no_log(), self.assertRaises(SttError) as ctx:
            chain.transcribe(WAV)
        message = str(ctx.exception)
        self.assertIn("openai: no network", message)
        self.assertIn("local: it is switched off", message)

    def test_an_unexpected_exception_does_not_stop_the_chain(self):
        chain = TranscriberChain([FakeTranscriber([RuntimeError("boom")], name="a"), FakeTranscriber(["ok"], name="b")])
        with no_log():
            self.assertEqual(chain.transcribe(WAV), ("ok", "b"))

    def test_an_empty_chain_explains_itself(self):
        with self.assertRaises(SttError) as ctx:
            TranscriberChain([]).transcribe(WAV)
        self.assertIn("No speech to text", str(ctx.exception))

    def test_build_chain_follows_the_settings_order_and_needs_a_key_for_openai(self):
        chain = build_chain(BodySettings(stt_order=("local", "openai"), openai_api_key="sk-x"))
        self.assertEqual([i.name for i in chain.items], ["local", "openai"])
        self.assertTrue(chain.items[1].available())
        with no_log():
            chain = build_chain(BodySettings(stt_order=("openai", "local", "nonsense"), openai_api_key=""))
        self.assertEqual([i.name for i in chain.items], ["openai", "local"])
        self.assertFalse(chain.items[0].available())

    def test_build_chain_skips_unknown_names_with_a_log_line(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            chain = build_chain(BodySettings(stt_order=("nonsense",)))
        self.assertEqual(chain.items, [])
        self.assertIn("nonsense", out.getvalue())

    def test_chain_ends_to_end_with_the_fake_openai(self):
        with FakeOpenAI(ok("turn the lights on")) as fake:
            with mock.patch.dict("os.environ", {"OPENAI_BASE_URL": fake.url}):
                chain = build_chain(BodySettings(openai_api_key="sk-x"))
                self.assertEqual(chain.transcribe(WAV), ("turn the lights on", "openai"))


class LocalWhisperTests(unittest.TestCase):
    def test_unavailable_when_faster_whisper_is_missing(self):
        with mock.patch.dict(sys.modules, {"faster_whisper": None}):
            local = LocalWhisper()
            self.assertFalse(local.available())
            with self.assertRaises(SttError) as ctx:
                local.transcribe(WAV)
        self.assertIn("faster-whisper", str(ctx.exception))

    def test_a_missing_local_model_lets_the_chain_report_why(self):
        with mock.patch.dict(sys.modules, {"faster_whisper": None}):
            chain = TranscriberChain([LocalWhisper()])
            with self.assertRaises(SttError) as ctx:
                chain.transcribe(WAV)
        self.assertIn("local: faster-whisper is not installed", str(ctx.exception))

    def test_loads_the_model_once_on_cpu_int8_and_joins_the_segments(self):
        loads = []

        class Model:
            def __init__(self, name, device, compute_type):
                loads.append((name, device, compute_type))

            def transcribe(self, audio, **kwargs):
                self.kwargs = kwargs
                return [types.SimpleNamespace(text=" hello"), types.SimpleNamespace(text=" there ")], None

        fake_module = types.ModuleType("faster_whisper")
        fake_module.WhisperModel = Model
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}), no_log():
            local = LocalWhisper("base.en")
            self.assertTrue(local.available())
            self.assertEqual(loads, [])                       # nothing is loaded until it is used
            self.assertEqual(local.transcribe(WAV), "hello there")
            self.assertEqual(local.transcribe(WAV), "hello there")
        self.assertEqual(loads, [("base.en", "cpu", "int8")])


class FakeTests(unittest.TestCase):
    def test_returns_queued_texts_then_empty(self):
        fake = FakeTranscriber(["a", "b"])
        self.assertEqual([fake.transcribe(WAV) for _ in range(3)], ["a", "b", ""])
        self.assertEqual(len(fake.calls), 3)


if __name__ == "__main__":
    unittest.main()
