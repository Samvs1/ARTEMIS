"""The body's link to the mind server (mind/server.py), using only the standard library.

chat() streams the mind's NDJSON events as they arrive, cancel() stops a stream from another thread,
tts() fetches the voice for a sentence and health() asks whether the mind is there.
"""
from __future__ import annotations

import http.client
import json
import socket
import sys
import threading
import urllib.parse
from typing import Iterator

CONNECT_TIMEOUT = 5.0


class MindError(Exception):
    """The mind could not be reached or refused the request. The message is written for a person to read."""


class _Stream:
    """One open chat request, so that cancel() can close exactly that one."""

    def __init__(self, conn: http.client.HTTPConnection) -> None:
        self.conn = conn
        self.sock: socket.socket | None = None       # kept here because http.client lets go of conn.sock once the reply starts
        self.cancelled = threading.Event()

    def cancel(self) -> None:
        self.cancelled.set()
        sock = self.sock or self.conn.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)          # close() alone does not wake a thread that is waiting to read
            except OSError:
                pass
            if sys.platform == "win32":
                # On Windows shutdown() does not wake a waiting read either; closing the handle itself does.
                # detach() first so the socket object (still held by the response) never closes it twice.
                try:
                    socket.close(sock.detach())
                except OSError:
                    pass
        try:
            self.conn.close()
        except OSError:
            pass


class MindClient:
    def __init__(self, base_url: str, read_timeout: float = 60.0) -> None:
        parts = urllib.parse.urlsplit(base_url.strip().rstrip("/"))
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise MindError(f"The mind address '{base_url}' is not a web address like http://127.0.0.1:8000.")
        self.base_url = f"{parts.scheme}://{parts.netloc}"
        self._parts = parts
        self.read_timeout = read_timeout
        self._stream: _Stream | None = None
        self._lock = threading.Lock()

    # ---- plumbing ----
    def _connect(self, timeout: float) -> http.client.HTTPConnection:
        cls = http.client.HTTPSConnection if self._parts.scheme == "https" else http.client.HTTPConnection
        return cls(self._parts.hostname, self._parts.port, timeout=timeout)

    def _headers(self, body: bool) -> dict:
        # The mind only answers pages from its own address, so it wants an Origin equal to its URL.
        headers = {"Origin": self.base_url}
        if body:
            headers["Content-Type"] = "application/json"
        return headers

    def _unreachable(self, err: Exception) -> MindError:
        if isinstance(err, (TimeoutError, socket.timeout)):
            return MindError(f"Milo's mind at {self.base_url} took too long to answer.")
        return MindError(f"Could not reach Milo's mind at {self.base_url} ({getattr(err, 'strerror', None) or err}). "
                         "Is the mind server running?")

    @staticmethod
    def _error_message(resp: http.client.HTTPResponse) -> str:
        try:
            raw = resp.read(4000).decode("utf-8", "replace").strip()
        except (OSError, http.client.HTTPException):
            raw = ""
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict) and obj.get("error"):
                return str(obj["error"])
        except ValueError:
            pass
        return " ".join(raw.split())[:200] or f"The mind answered HTTP {resp.status}."

    def _post(self, path: str, payload: dict, timeout: float) -> tuple[bytes, str]:
        """A whole (not streamed) POST. Returns (body, content type) or raises MindError."""
        conn = self._connect(timeout)
        try:
            conn.request("POST", path, body=json.dumps(payload).encode("utf-8"), headers=self._headers(True))
            resp = conn.getresponse()
            if resp.status != 200:
                raise MindError(self._error_message(resp))
            return resp.read(), resp.getheader("Content-Type", "")
        except (OSError, http.client.HTTPException) as e:
            raise self._unreachable(e) from e
        finally:
            conn.close()

    # ---- the interface ----
    def health(self) -> dict | None:
        conn = self._connect(3.0)
        try:
            conn.request("GET", "/api/health")
            resp = conn.getresponse()
            if resp.status != 200:
                return None
            data = json.loads(resp.read())
            return data if isinstance(data, dict) else None
        except (OSError, http.client.HTTPException, ValueError):
            return None
        finally:
            conn.close()

    def chat(self, text: str = "", event: str = "", state: dict | None = None, detail: str = "") -> Iterator[dict]:
        """Stream the mind's events (emote, look, sound, timer, focus, say, error, done), each as soon as its line arrives.

        `event` (with an optional `detail`, such as a timer's label) asks for a reply to something that happened
        instead of something the person said. The request is registered right away, so cancel() works even
        before the first event is read.
        """
        conn = self._connect(CONNECT_TIMEOUT)
        stream = _Stream(conn)
        with self._lock:
            self._stream = stream
        body: dict = {"text": text, "event": event, "state": state or {}}
        if detail:
            body["detail"] = detail
        return self._events(stream, json.dumps(body).encode("utf-8"))

    def _events(self, stream: _Stream, payload: bytes) -> Iterator[dict]:
        conn = stream.conn
        try:
            try:
                if stream.cancelled.is_set():
                    return
                conn.request("POST", "/api/chat", body=payload, headers=self._headers(True))
                stream.sock = conn.sock
                if stream.cancelled.is_set():
                    return
                if conn.sock is not None:
                    conn.sock.settimeout(self.read_timeout)
                resp = conn.getresponse()
                if resp.status != 200:
                    raise MindError(self._error_message(resp))
                while not stream.cancelled.is_set():
                    line = resp.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue                                  # a damaged line is not worth stopping for
                    if isinstance(event, dict):
                        yield event
            except (OSError, http.client.HTTPException, ValueError) as e:
                if stream.cancelled.is_set():
                    return                                        # we closed it ourselves, so this is not a failure
                raise self._unreachable(e) from e
        finally:
            conn.close()
            with self._lock:
                if self._stream is stream:
                    self._stream = None

    def tts(self, text: str, fmt: str = "wav") -> bytes:
        body, _ctype = self._post("/api/tts", {"text": text, "format": fmt}, timeout=40.0)
        if not body:
            raise MindError("The mind sent back no audio.")
        return body

    def cancel(self) -> None:
        """Close the open chat stream, if any. The server sees the page go away and treats it as an interrupt."""
        with self._lock:
            stream = self._stream
        if stream is not None:
            stream.cancel()
