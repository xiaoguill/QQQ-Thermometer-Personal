from __future__ import annotations

import http.client
import threading
import unittest

from src.api.live_api import LiveApiService
from src.api.live_server import create_live_server
from src.api.read_api import ApiAccessPolicy, ApiError
from src.notifications import LiveEventBus
from tests.realtime.test_massive_client import NOW


class M16LiveSseContractTests(unittest.TestCase):
    def test_local_http_stream_emits_connected_and_service_event(self):
        bus = LiveEventBus()
        service = LiveApiService(
            bus,
            access_policy=ApiAccessPolicy(access_token="test-token"),
            heartbeat_seconds=0.05,
        )
        bus.publish_service_status("ready", occurred_at_utc=NOW)
        server = create_live_server(service, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
        try:
            connection.request(
                "GET",
                "/api/live/events",
                headers={"X-QQQ-Local-Token": "test-token"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "text/event-stream; charset=utf-8")
            first = response.readline()
            second = response.readline()
            third = response.readline()
            self.assertEqual(first, b": connected\n")
            self.assertEqual(second, b"\n")
            self.assertTrue(third.startswith(b"id: evt-"))
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_external_client_is_rejected_before_stream_body(self):
        service = LiveApiService(
            LiveEventBus(),
            access_policy=ApiAccessPolicy(access_token="test-token"),
        )
        with self.assertRaises(ApiError):
            service.open_events(headers={"X-QQQ-Local-Token": "test-token"}, client_host="192.0.2.10")
