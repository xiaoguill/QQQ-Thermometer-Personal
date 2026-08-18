"""Local-only HTTP adapter for the M16 Server-Sent Events stream."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .live_api import LiveApiService, LiveStreamHeaders
from src.api.read_api import ApiError


class _LiveHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def _handler_for(application: LiveApiService, static_root: Path | None):
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
            route = urlsplit(self.path).path
            if route == "/api/live/events":
                self._stream_events()
                return
            if route.startswith("/api/") or static_root is None:
                self._send_error(ApiError(404, "NOT_FOUND", "endpoint was not found"))
                return
            self._send_static(route)

        def _stream_events(self) -> None:
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

        def _send_static(self, route: str) -> None:
            try:
                relative = unquote(route.lstrip("/")) or "index.html"
                if "\x00" in relative or any(part in {"", ".", ".."} for part in Path(relative).parts if part in {".", "..", ""}):
                    raise ValueError("invalid static path")
                candidate = (static_root / Path(relative)).resolve()
                candidate.relative_to(static_root)
                if not candidate.is_file():
                    raise FileNotFoundError
                body = candidate.read_bytes()
            except (OSError, ValueError):
                self._send_error(ApiError(404, "NOT_FOUND", "static resource was not found"))
                return
            content_type = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".json": "application/json; charset=utf-8",
            }.get(candidate.suffix.lower(), "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            self._send_error(ApiError(405, "METHOD_NOT_ALLOWED", "method is not allowed"))

        def log_message(self, format: str, *args: Any) -> None:
            return

    return LiveRequestHandler


def create_live_server(
    application: LiveApiService,
    *,
    host: str = "127.0.0.1",
    port: int = 8766,
    static_root: str | Path | None = None,
) -> ThreadingHTTPServer:
    if not isinstance(application, LiveApiService):
        raise TypeError("application must be LiveApiService")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("M16 live server must bind to localhost")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65_535:
        raise ValueError("port must be between 0 and 65535")
    resolved_root: Path | None = None
    if static_root is not None:
        resolved_root = Path(static_root).resolve()
        if not resolved_root.is_dir():
            raise ValueError("static_root must be an existing directory")
    return _LiveHTTPServer((host, port), _handler_for(application, resolved_root))


def serve_live(
    application: LiveApiService,
    *,
    host: str = "127.0.0.1",
    port: int = 8766,
    static_root: str | Path | None = None,
) -> None:
    server = create_live_server(application, host=host, port=port, static_root=static_root)
    try:
        server.serve_forever()
    finally:
        server.server_close()


__all__ = ["create_live_server", "serve_live"]
