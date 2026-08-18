from __future__ import annotations

import unittest

from src.api.live_api import LiveApiService
from src.api.read_api import ApiAccessPolicy, ApiError
from src.notifications import LiveEventBus, SseEventStream
from tests.realtime.test_massive_client import NOW


class M16LiveSseContractTests(unittest.TestCase):
    def test_private_stream_emits_connected_and_service_event_without_socket(self):
        bus = LiveEventBus()
        service = LiveApiService(
            bus,
            access_policy=ApiAccessPolicy(access_token="test-token"),
            heartbeat_seconds=0.05,
        )
        bus.publish_service_status("ready", occurred_at_utc=NOW)
        stream = service.open_events(
            headers={"X-QQQ-Local-Token": "test-token"},
            client_host="127.0.0.1",
        )
        self.assertIsInstance(stream, SseEventStream)
        self.assertEqual(next(stream.iter_frames()), b": connected\n\n")
        self.assertTrue(stream.next_frame(timeout_seconds=0).startswith(b"id: evt-"))

    def test_external_client_is_rejected_before_stream_body(self):
        service = LiveApiService(
            LiveEventBus(),
            access_policy=ApiAccessPolicy(access_token="test-token"),
        )
        with self.assertRaises(ApiError):
            service.open_events(headers={"X-QQQ-Local-Token": "test-token"}, client_host="192.0.2.10")
