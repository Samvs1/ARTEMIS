"""The face link: serves Arty's face page and tells it what to show.

The body owns the screen. The page (sim/index.html, opened as /?face=1) asks for /events once and
keeps that connection open (Server-Sent Events). Whatever the body passes to `FaceServer.send()` is
copied to every open page. The page sends taps back with POST /input; they wait in `FaceServer.inputs`
for the body to read.

It uses only what comes with Python, so there is nothing to install.
"""
from __future__ import annotations

import json
import queue
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from body.config import log

MAX_INPUT = 10 * 1024            # the page only ever sends a few bytes; anything bigger is refused
KEEPALIVE_SECONDS = 15.0         # a comment line this often tells us (and any proxy) the stream is alive
CLIENT_QUEUE = 200               # events one page may fall behind before we give up on it
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


class _Client:
    """One open page. It has its own queue, so a slow page can never hold up the others."""

    def __init__(self, size: int) -> None:
        self.events: queue.Queue[bytes | None] = queue.Queue(size)

    def offer(self, data: bytes) -> bool:
        try:
            self.events.put_nowait(data)
            return True
        except queue.Full:
            return False

    def close(self) -> None:
        """Throw away what is waiting and tell the page's thread to finish (None means: stop)."""
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                break
        try:
            self.events.put_nowait(None)
        except queue.Full:
            pass


class _Server(ThreadingHTTPServer):
    daemon_threads = True            # a page that is still connected must not keep the program alive

    def handle_error(self, request, client_address):   # noqa: ANN001  (a page going away is normal, not an error)
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


class _Server6(_Server):
    address_family = socket.AF_INET6


class Handler(BaseHTTPRequestHandler):
    server_version = "ArtemisFace/0.1"
    protocol_version = "HTTP/1.0"    # the connection closes after each reply, which is what a stream needs
    timeout = 10                     # a stuck page cannot hold a thread forever

    @property
    def face(self) -> FaceServer:
        return self.server.face          # type: ignore[attr-defined]

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

    def host_ok(self) -> bool:
        """Only answer requests addressed to localhost, so other websites cannot reach this server."""
        if self.face.host not in LOCAL_HOSTS:
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
        if path in ("/", "/index.html"):
            try:
                self.send_bytes(200, self.face.sim_file.read_bytes(), "text/html; charset=utf-8")
            except OSError:
                self.send_json(404, {"error": "sim/index.html was not found."})
        elif path == "/events":
            self.stream_events()
        elif path == "/favicon.ico":
            self.send_bytes(204, b"", "image/x-icon")
        else:
            self.send_json(404, {"error": "Not found."})

    def stream_events(self) -> None:
        face = self.face
        client = face.add_client()           # before the first byte, so nothing sent after the headers is missed
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b"retry: 3000\n\n")      # if the link drops, the page tries again after 3 s
            self.wfile.flush()
            while True:
                try:
                    item = client.events.get(timeout=face.keepalive)
                except queue.Empty:
                    chunk = b": keepalive\n\n"
                else:
                    if item is None:
                        break
                    chunk = b"data: " + item + b"\n\n"
                self.wfile.write(chunk)
                self.wfile.flush()
        except OSError:
            pass                             # the page went away: not a problem
        finally:
            face.remove_client(client)

    # ---- POST ----
    def do_POST(self) -> None:
        if not (self.host_ok() and self.origin_ok()):
            return self.send_json(403, {"error": "This request did not come from the Arty page."})
        path = self.path.split("?", 1)[0]
        if path != "/input":
            return self.send_json(404, {"error": "Not found."})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length > MAX_INPUT:
            try:
                self.rfile.read(min(length, 65536))      # take the body off the wire so the reply is not lost
            except OSError:
                pass
            return self.send_json(413, {"error": "That is too much for one tap."})
        if length <= 0:
            return self.send_json(400, {"error": 'Send JSON like {"type": "touch", "where": "face"}.'})
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return self.send_json(400, {"error": 'Send JSON like {"type": "touch", "where": "face"}.'})
        self.face.accept_input(data)
        self.send_json(200, {"ok": True})


class FaceServer:
    """Serves the face page and streams events to it. Port 0 picks a free port; read it back from `port`."""

    def __init__(self, host: str, port: int, sim_file: Path) -> None:
        self.host = host
        self.port = port
        self.sim_file = Path(sim_file)
        self.inputs: queue.Queue[dict] = queue.Queue(100)     # what the page sent us (taps), oldest first
        self.keepalive = KEEPALIVE_SECONDS
        self.client_queue = CLIENT_QUEUE
        self._clients: set[_Client] = set()
        self._latest: dict[str, bytes] = {}                  # the last state and emote, replayed to new pages
        self._lock = threading.Lock()
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    # ---- the body's side ----
    def start(self) -> None:
        """Start listening in a background thread. Raises OSError if the port is taken."""
        if self._server is not None:
            return
        server_class = _Server6 if ":" in self.host else _Server
        server = server_class((self.host, self.port), Handler)
        server.face = self                       # type: ignore[attr-defined]
        self._server = server
        self.port = server.server_address[1]
        self._thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), name="face-server", daemon=True)
        self._thread.start()
        shown = "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host
        log("face", f"serving the face at http://{shown}:{self.port}/?face=1")

    def send(self, event: dict) -> None:
        """Give an event to every open page. Never waits for a page; one that falls far behind is dropped."""
        try:
            data = json.dumps(event).encode("utf-8")
        except (TypeError, ValueError):
            log("face", "could not send an event that is not plain JSON")
            return
        with self._lock:
            if event.get("type") in ("state", "emote"):
                self._latest[event["type"]] = data       # a page that connects later starts from here
            behind = [c for c in self._clients if not c.offer(data)]
            self._clients.difference_update(behind)
        for client in behind:
            client.close()
        if behind:
            log("face", f"dropped {len(behind)} page(s) that could not keep up")

    def clients(self) -> int:
        """How many pages are connected right now."""
        with self._lock:
            return len(self._clients)

    def stop(self) -> None:
        server, thread = self._server, self._thread
        if server is None:
            return
        self._server = self._thread = None
        with self._lock:
            open_pages = list(self._clients)
            self._clients.clear()
        for client in open_pages:
            client.close()                       # lets each page's thread finish
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(2)
        log("face", "stopped")

    # ---- used by the request handler ----
    def add_client(self) -> _Client:
        client = _Client(self.client_queue)
        with self._lock:
            for key in ("state", "emote"):
                if key in self._latest:
                    client.offer(self._latest[key])
            self._clients.add(client)
            count = len(self._clients)
        log("face", f"a page connected ({count} open)")
        return client

    def remove_client(self, client: _Client) -> None:
        with self._lock:
            self._clients.discard(client)
            count = len(self._clients)
        log("face", f"a page left ({count} open)")

    def accept_input(self, data: dict) -> None:
        while True:
            try:
                self.inputs.put_nowait(data)
                return
            except queue.Full:                   # nobody is reading: forget the oldest tap
                try:
                    self.inputs.get_nowait()
                except queue.Empty:
                    pass
