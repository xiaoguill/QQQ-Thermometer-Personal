"""Explicit localhost-only HTTP adapter for the M10 read API."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from .read_api import ReadApiService


MAX_REQUEST_BYTES = 65_536


def _handler_for(application: ReadApiService):
    class ApiRequestHandler(BaseHTTPRequestHandler):
        server_version = "QQQ-Thermometer-Personal/1"
        sys_version = ""

        def _dispatch(self) -> None:
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

        def log_message(self, format: str, *args: Any) -> None:
            # Do not log query strings, headers, notes, or local access tokens.
            return

    return ApiRequestHandler


def create_http_server(application: ReadApiService, *, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    """Create, but do not start, a localhost HTTP server."""

    if not isinstance(application, ReadApiService):
        raise TypeError("application must be ReadApiService")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("personal API server must bind to localhost")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65_535:
        raise ValueError("port must be between 0 and 65535")
    return ThreadingHTTPServer((host, port), _handler_for(application))


def serve(application: ReadApiService, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Explicit blocking entry point; never called at import time."""

    server = create_http_server(application, host=host, port=port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


__all__ = ["MAX_REQUEST_BYTES", "create_http_server", "serve"]
