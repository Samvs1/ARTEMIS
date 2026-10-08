"""Speech to text: turn a finished utterance (WAV bytes) into words.

OpenAITranscriber uses the cloud, LocalWhisper works on the robot itself (faster-whisper, optional),
and TranscriberChain tries them in the order the settings give. The key is never logged or put in a message.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
import uuid
from collections import deque
from typing import Protocol

from body.config import BodySettings, log
from mind.brain import describe_http_error, friendly_status

OPENAI_BASE = "https://api.openai.com/v1"


class SttError(Exception):
    """The speech could not be turned into text. The message is written for a person to read."""


class Transcriber(Protocol):
    name: str

    def available(self) -> bool: ...

    def transcribe(self, wav: bytes) -> str: ...      # raises SttError with a plain-word message


def multipart_form(fields: dict[str, str], file_field: str, filename: str, content_type: str, data: bytes) -> tuple[bytes, str]:
    """Build a multipart/form-data body by hand. Returns (body, Content-Type header)."""
    boundary = "----milo" + uuid.uuid4().hex
    out = io.BytesIO()
    for name, value in fields.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8"))
    out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
              f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    out.write(data)
    out.write(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return out.getvalue(), "multipart/form-data; boundary=" + boundary


class OpenAITranscriber:
    """Speech to text through OpenAI's transcription service."""

    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini-transcribe", base_url: str | None = None,
                 timeout: float = 15.0) -> None:
        self.key = (api_key or "").strip()
        self.model = model
        # OPENAI_BASE_URL exists so tests can point this at a fake local server.
        self.base = (base_url or os.environ.get("OPENAI_BASE_URL") or OPENAI_BASE).rstrip("/")
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.key)

    @property
    def why_unavailable(self) -> str:
        return "there is no OPENAI_API_KEY"

    def _clean(self, message: str) -> str:
        return message.replace(self.key, "***") if self.key else message

    def transcribe(self, wav: bytes) -> str:
        if not self.key:
            raise SttError("OpenAI speech to text needs an OPENAI_API_KEY (put it in .env).")
        body, content_type = multipart_form(
            {"model": self.model, "language": "en", "response_format": "json"},
            "file", "utterance.wav", "audio/wav", wav)
        req = urllib.request.Request(
            self.base + "/audio/transcriptions", data=body, method="POST",
            headers={"Authorization": "Bearer " + self.key, "Content-Type": content_type})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = describe_http_error(e)
            if e.code == 401:
                detail = ""                           # OpenAI echoes part of the key back, so say nothing more
            raise SttError(self._clean(friendly_status("OpenAI speech to text", e.code, detail))) from e
        except (TimeoutError, socket.timeout) as e:
            raise SttError(f"OpenAI speech to text took longer than {self.timeout:g} seconds to answer.") from e
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise SttError(f"OpenAI speech to text took longer than {self.timeout:g} seconds to answer.") from e
            raise SttError(self._clean(f"Could not reach OpenAI speech to text ({reason}). "
                                       "Check the internet connection.")) from e
        try:
            text = json.loads(raw).get("text", "")
        except (ValueError, AttributeError) as e:
            raise SttError("OpenAI speech to text sent back something that is not a transcript.") from e
        return str(text or "").strip()


class LocalWhisper:
    """Speech to text on the robot itself, with faster-whisper (optional). The model loads once, on first use."""

    name = "local"

    def __init__(self, model: str = "tiny.en") -> None:
        self.model_name = model
        self._model = None
        self._lock = threading.Lock()

    @staticmethod
    def _installed() -> bool:
        if "faster_whisper" in sys.modules:
            return sys.modules["faster_whisper"] is not None
        try:
            return importlib.util.find_spec("faster_whisper") is not None
        except (ImportError, ValueError):
            return False

    def available(self) -> bool:
        return self._installed()

    @property
    def why_unavailable(self) -> str:
        return "faster-whisper is not installed (pip install faster-whisper)"

    def _load(self):
        with self._lock:
            if self._model is None:
                try:
                    from faster_whisper import WhisperModel         # optional, so imported only here
                except ImportError as e:
                    raise SttError("The local speech model needs faster-whisper (pip install faster-whisper).") from e
                log("hears", f"loading the local speech model '{self.model_name}' (the first time takes a moment)")
                try:
                    self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
                except Exception as e:                              # a download or a bad model name; the library has many error types
                    raise SttError(f"The local speech model '{self.model_name}' could not be loaded ({e}).") from e
            return self._model

    def transcribe(self, wav: bytes) -> str:
        model = self._load()
        try:
            segments, _info = model.transcribe(io.BytesIO(wav), language="en", beam_size=1)
            return " ".join(seg.text.strip() for seg in segments).strip()
        except SttError:
            raise
        except Exception as e:
            raise SttError(f"The local speech model could not read that audio ({e}).") from e


class FakeTranscriber:
    """Returns queued texts, one per call, then empty text. A queued exception is raised instead (for tests)."""

    def __init__(self, texts: list | None = None, name: str = "fake", is_available: bool = True) -> None:
        self.name = name
        self.queue = deque(texts or [])
        self.is_available = is_available
        self.calls: list[bytes] = []

    @property
    def why_unavailable(self) -> str:
        return "it is switched off"

    def available(self) -> bool:
        return self.is_available

    def transcribe(self, wav: bytes) -> str:
        self.calls.append(wav)
        item = self.queue.popleft() if self.queue else ""
        if isinstance(item, BaseException):
            raise item
        return item


class TranscriberChain:
    """Tries each available transcriber in order and returns (text, name of the one that answered)."""

    def __init__(self, items: list[Transcriber]) -> None:
        self.items = list(items)

    def transcribe(self, wav: bytes) -> tuple[str, str]:
        reasons: list[str] = []
        for item in self.items:
            if not item.available():
                reasons.append(f"{item.name}: {getattr(item, 'why_unavailable', 'not available')}")
                continue
            try:
                text = item.transcribe(wav)
            except SttError as e:
                reasons.append(f"{item.name}: {e}")
                log("hears", f"{item.name} speech to text failed, trying the next one: {e}")
                continue
            except Exception as e:                                  # one broken engine must not stop the others
                reasons.append(f"{item.name}: unexpected problem ({e})")
                log("hears", f"{item.name} speech to text had an unexpected problem, trying the next one: {e}")
                continue
            return text.strip(), item.name                          # empty text is an answer too: nothing was heard
        raise SttError("Could not turn the speech into text. " + ("; ".join(reasons) or "No speech to text is set up."))


def build_chain(settings: BodySettings) -> TranscriberChain:
    """Build the chain in the order of settings.stt_order. Unknown names are skipped with a log line."""
    items: list[Transcriber] = []
    for name in settings.stt_order:
        if name == "openai":
            items.append(OpenAITranscriber(settings.openai_api_key, settings.openai_stt_model))
        elif name == "local":
            items.append(LocalWhisper(settings.local_stt_model))
        else:
            log("config", f"MILO_STT_ORDER names '{name}', which is not a speech to text I know (openai, local). Skipping it.")
    return TranscriberChain(items)
