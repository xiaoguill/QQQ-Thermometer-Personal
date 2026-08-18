"""Local-only HTTP adapter for the M17 unified portal."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from src.api.live_api import LiveStreamHeaders
from src.api.read_api import ApiError, ApiResponse

from .gateway import M17Application, READ_ONLY_ROUTES


class _M17HTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def _error_body(code: str, message: str) -> dict[str, Any]:
    return {
        "error": {"code": code, "message": message, "details": {}},
        "meta": {
            "contract_version": "m17-unified-portal/v1",
            "data_quality": "failed",
            "paper_only": True,
        },
    }


def _handler_for(application: M17Application):
    static_root = application.config.static_root

    class M17RequestHandler(BaseHTTPRequestHandler):
        server_version = "QQQ-Thermometer-Personal-M17/1"
        sys_version = ""

        def _send_json(self, status_code: int, body: dict[str, Any]) -> None:
            encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def _send_response(self, response: ApiResponse) -> None:
            self._send_json(response.status_code, dict(response.body))

        def _send_error(self, status_code: int, code: str, message: str) -> None:
            self._send_json(status_code, _error_body(code, message))

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            route = parsed.path or "/"
            if route == "/api/live/events":
                self._stream_events(parsed.query)
                return
            if route == "/api/m17/overview":
                if parsed.query:
                    self._send_error(400, "INVALID_REQUEST", "unsupported query parameter")
                    return
                try:
                    self._send_response(application.overview())
                except Exception:
                    self._send_error(500, "INTERNAL_ERROR", "M17 overview failed")
                return
            if route == "/api/m17/paper-plan":
                if parsed.query:
                    self._send_error(400, "INVALID_REQUEST", "unsupported query parameter")
                    return
                try:
                    self._send_response(application.paper_plan())
                except Exception:
                    self._send_error(500, "INTERNAL_ERROR", "paper plan preview failed")
                return
            if route == "/api/m17/source-status":
                if parsed.query:
                    self._send_error(400, "INVALID_REQUEST", "unsupported query parameter")
                    return
                try:
                    overview = application.overview()
                    body = dict(overview.body)
                    self._send_response(ApiResponse(200, {
                        "schema": "qqq-m17-source-status/v1",
                        "generated_at": body.get("generated_at"),
                        "paper_only": True,
                        "source_status": body.get("source_status", {}),
                        "runtime": body.get("runtime", {}),
                    }))
                except Exception:
                    self._send_error(500, "INTERNAL_ERROR", "source status failed")
                return
            if route in READ_ONLY_ROUTES:
                try:
                    response = application.proxy_read_api(self.path)
                except ValueError:
                    self._send_error(400, "INVALID_REQUEST", "read-only route is not allowed")
                    return
                except Exception:
                    self._send_error(503, "CONFIRMED_UNAVAILABLE", "confirmed read API is unavailable")
                    return
                self._send_json(response.status_code, dict(response.body))
                return
            if route.startswith("/api/"):
                self._send_error(404, "NOT_FOUND", "endpoint was not found")
                return
            self._send_static(route)

        def _stream_events(self, raw_query: str) -> None:
            if raw_query:
                from urllib.parse import parse_qs

                query = parse_qs(raw_query, keep_blank_values=True)
                unknown = set(query) - {"after"}
                if unknown or len(query.get("after", [])) > 1:
                    self._send_error(400, "INVALID_REQUEST", "invalid SSE cursor query")
                    return
                cursor = query.get("after", [None])[0]
            else:
                cursor = None
            try:
                stream = application.live_api.open_events(
                    last_event_id=self.headers.get("Last-Event-ID") or cursor,
                    headers=self.headers,
                    client_host=self.client_address[0] if self.client_address else None,
                )
            except ApiError as exc:
                self._send_error(exc.status_code, exc.code, exc.message)
                return
            headers = LiveStreamHeaders()
            self.send_response(200)
            self.send_header("Content-Type", headers.content_type)
            self.send_header("Cache-Control", headers.cache_control)
            self.send_header("Connection", headers.connection)
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = True
            try:
                for frame in stream.iter_frames():
                    self.wfile.write(frame)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                stream.close()

        def _send_static(self, route: str) -> None:
            if route in {"/", "/m17", "/m17/"}:
                relative = Path("m17/index.html")
            else:
                text = unquote(route.lstrip("/"))
                if not text or "\x00" in text:
                    self._send_error(404, "NOT_FOUND", "static resource was not found")
                    return
                relative = Path(text)
            if any(part in {"", ".", ".."} for part in relative.parts):
                self._send_error(404, "NOT_FOUND", "static resource was not found")
                return
            try:
                candidate = (static_root / relative).resolve()
                candidate.relative_to(static_root)
                body = candidate.read_bytes() if candidate.is_file() else None
            except (OSError, ValueError):
                body = None
                candidate = static_root / relative
            if body is None:
                self._send_error(404, "NOT_FOUND", "static resource was not found")
                return
            content_type = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".mjs": "text/javascript; charset=utf-8",
                ".json": "application/json; charset=utf-8",
                ".svg": "image/svg+xml",
            }.get(candidate.suffix.lower(), "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            self._send_error(405, "METHOD_NOT_ALLOWED", "M17 is read-only; no order or confirmation write is available")

        def do_PUT(self) -> None:  # noqa: N802
            self._send_error(405, "METHOD_NOT_ALLOWED", "M17 is read-only")

        def log_message(self, format: str, *args: Any) -> None:
            # Do not log query parameters, local tokens, holdings, or provider data.
            return

    return M17RequestHandler


def create_m17_server(
    application: M17Application,
    *,
    host: str | None = None,
    port: int | None = None,
) -> ThreadingHTTPServer:
    if not isinstance(application, M17Application):
        raise TypeError("application must be M17Application")
    bind_host = application.config.host if host is None else host
    bind_port = application.config.port if port is None else port
    if bind_host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("M17 server must bind to localhost")
    if isinstance(bind_port, bool) or not isinstance(bind_port, int) or not 0 <= bind_port <= 65_535:
        raise ValueError("port must be between 0 and 65535")
    return _M17HTTPServer((bind_host, bind_port), _handler_for(application))


def serve_m17(application: M17Application, *, host: str | None = None, port: int | None = None) -> None:
    server = create_m17_server(application, host=host, port=port)
    application.runtime_handle.start()
    try:
        server.serve_forever()
    finally:
        application.runtime_handle.stop()
        server.server_close()


__all__ = ["create_m17_server", "serve_m17"]
