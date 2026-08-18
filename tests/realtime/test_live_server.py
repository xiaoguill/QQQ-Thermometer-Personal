from __future__ import annotations

import http.client
from pathlib import Path
import threading
import unittest

from src.api.live_api import LiveApiService
from src.api.live_server import create_live_server
from src.api.read_api import ApiAccessPolicy
from src.notifications import LiveEventBus
from tests.realtime.test_massive_client import NOW


ROOT = Path(__file__).resolve().parents[2]


class LiveServerTests(unittest.TestCase):
    def _start(self):
        bus = LiveEventBus()
        service = LiveApiService(bus, access_policy=ApiAccessPolicy(access_token="test-token"), heartbeat_seconds=0.05)
        server = create_live_server(service, port=0, static_root=ROOT / "frontend" / "m16")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, bus

    def test_static_page_is_served_only_from_local_root(self):
        server, thread, _ = self._start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
            connection.request("GET", "/")
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertIn("Northstar Live", body)

            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
            connection.request("GET", "/..%2FREADME.md")
            traversal = connection.getresponse()
            traversal.read()
            connection.close()
            self.assertEqual(traversal.status, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_sse_http_response_is_private_and_replayable(self):
        server, thread, bus = self._start()
        bus.publish_service_status("ready", occurred_at_utc=NOW)
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
        try:
            connection.request("GET", "/api/live/events", headers={"X-QQQ-Local-Token": "test-token"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "text/event-stream; charset=utf-8")
            self.assertEqual(response.readline(), b": connected\n")
            self.assertEqual(response.readline(), b"\n")
            self.assertTrue(response.readline().startswith(b"id: evt-"))
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

