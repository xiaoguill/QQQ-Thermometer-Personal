"""Local-only HTTP adapter for the M16 Server-Sent Events stream."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from typing import Any
from urllib.parse import urlsplit

from .live_api import LiveApiService, LiveStreamHeaders
from src.api.read_api import ApiError


class _LiveHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def _handler_for(application: LiveApiService):
    class LiveRequestHandler(BaseHTTPRequestHandler):
        server_version = "QQQ-Thermometer-Personal-Live/1"
        sys_version = ""

        def _send_error(self, error: ApiError) -> None:
            body = json.dumps(
                {"error": {"code": error.code, "message": error.message}},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(error.status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if urlsplit(self.path).path != "/api/live/events":
                self._send_error(ApiError(404, "NOT_FOUND", "endpoint was not found"))
                return
            try:
                stream = application.open_events(
                    last_event_id=self.headers.get("Last-Event-ID"),
                    headers=self.headers,
                    client_host=self.client_address[0] if self.client_address else None,
                )
            except ApiError as exc:
                self._send_error(exc)
                return
            headers = LiveStreamHeaders()
            self.send_response(200)
            self.send_header("Content-Type", headers.content_type)
            self.send_header("Cache-Control", headers.cache_control)
            self.send_header("Connection", headers.connection)
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            # One SSE request owns one stream. The browser reconnects with
            # Last-Event-ID; do not let BaseHTTPRequestHandler wait for a
            # second request on a socket the client may already have closed.
            self.close_connection = True
            try:
                for frame in stream.iter_frames():
                    self.wfile.write(frame)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                stream.close()

        def do_POST(self) -> None:  # noqa: N802
            self._send_error(ApiError(405, "METHOD_NOT_ALLOWED", "method is not allowed"))

        def log_message(self, format: str, *args: Any) -> None:
            return

    return LiveRequestHandler


def create_live_server(application: LiveApiService, *, host: str = "127.0.0.1", port: int = 8766) -> ThreadingHTTPServer:
    if not isinstance(application, LiveApiService):
        raise TypeError("application must be LiveApiService")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("M16 live server must bind to localhost")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65_535:
        raise ValueError("port must be between 0 and 65535")
    return _LiveHTTPServer((host, port), _handler_for(application))


def serve_live(application: LiveApiService, *, host: str = "127.0.0.1", port: int = 8766) -> None:
    server = create_live_server(application, host=host, port=port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


__all__ = ["create_live_server", "serve_live"]
