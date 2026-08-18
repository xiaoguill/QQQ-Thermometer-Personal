"""Loopback-only HTTP adapter for the M18 API."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .service import M18ApiService


MAX_REQUEST_BYTES = 65_536


def _handler_for(application: M18ApiService, static_root: Path | None):
    class M18RequestHandler(BaseHTTPRequestHandler):
        server_version = "QQQ-Thermometer-M18/1"
        sys_version = ""

        def _dispatch(self) -> None:
            route = urlsplit(self.path).path or "/"
            if self.command == "GET" and not route.startswith("/api/"):
                self._send_static(route)
                return
            content_length = self.headers.get("Content-Length", "0")
            try:
                length = int(content_length)
            except ValueError:
                length = -1
            body = self.rfile.read(length) if 0 <= length <= MAX_REQUEST_BYTES else None
            response = application.handle(
                self.command,
                self.path,
                body=body,
                headers=self.headers,
                client_host=self.client_address[0] if self.client_address else None,
            )
            encoded = response.json_bytes()
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch()

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch()

        def _send_json(self, status_code: int, body: dict[str, Any]) -> None:
            encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def _send_static(self, route: str) -> None:
            if static_root is None:
                self._send_json(404, {"error": {"code": "NOT_FOUND", "message": "static resource was not configured", "details": {}}})
                return
            if route in {"/", "/m18", "/m18/"}:
                relative = Path("index.html")
            else:
                text = unquote(route.lstrip("/"))
                if text.startswith("m18/"):
                    text = text[4:]
                if not text or "\x00" in text:
                    self._send_json(404, {"error": {"code": "NOT_FOUND", "message": "static resource was not found", "details": {}}})
                    return
                relative = Path(text)
            if any(part in {"", ".", ".."} for part in relative.parts):
                self._send_json(404, {"error": {"code": "NOT_FOUND", "message": "static resource was not found", "details": {}}})
                return
            try:
                candidate = (static_root / relative).resolve()
                candidate.relative_to(static_root)
                body = candidate.read_bytes() if candidate.is_file() else None
            except (OSError, ValueError):
                body = None
                candidate = static_root / relative
            if body is None:
                self._send_json(404, {"error": {"code": "NOT_FOUND", "message": "static resource was not found", "details": {}}})
                return
            content_type = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".json": "application/json; charset=utf-8",
                ".svg": "image/svg+xml",
            }.get(candidate.suffix.lower(), "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return M18RequestHandler


def create_http_server(
    application: M18ApiService,
    *,
    host: str = "127.0.0.1",
    port: int = 4180,
    static_root: str | Path | None = None,
) -> ThreadingHTTPServer:
    if not isinstance(application, M18ApiService):
        raise TypeError("application must be M18ApiService")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("M18 API must bind to localhost")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65_535:
        raise ValueError("port must be between 0 and 65535")
    root = None if static_root is None else Path(static_root).expanduser().resolve()
    return ThreadingHTTPServer((host, port), _handler_for(application, root))


def serve(
    application: M18ApiService,
    *,
    host: str = "127.0.0.1",
    port: int = 4180,
    static_root: str | Path | None = None,
) -> None:
    server = create_http_server(application, host=host, port=port, static_root=static_root)
    try:
        server.serve_forever()
    finally:
        server.server_close()


__all__ = ["MAX_REQUEST_BYTES", "create_http_server", "serve"]
