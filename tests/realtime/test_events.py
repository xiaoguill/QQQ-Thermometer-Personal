from __future__ import annotations

import unittest
from datetime import timedelta

from src.api.live_api import LiveApiService
from src.api.live_server import create_live_server
from src.api.read_api import ApiAccessPolicy, ApiError
from src.notifications import LiveEventBus, SseEventStream
from src.realtime.massive_client import MassiveClient
from src.realtime.poller import RealtimePoller
from tests.realtime.test_massive_client import FakeTransport, NOW, _config


class LiveEventTests(unittest.TestCase):
    def test_publish_is_semantically_idempotent_and_replay_is_ordered(self):
        bus = LiveEventBus(max_events=4)
        first = bus.publish_service_status("connected", occurred_at_utc=NOW)
        self.assertIsNotNone(first)
        self.assertIsNone(bus.publish_service_status("connected", occurred_at_utc=NOW + timedelta(seconds=1)))
        second = bus.publish_service_status("degraded", occurred_at_utc=NOW + timedelta(seconds=2))
        self.assertIsNotNone(second)
        cursor = bus.events_after(first.event_id if first else None)
        self.assertEqual([item.event_id for item in cursor.events], [second.event_id])
        self.assertTrue(all(item.notification for item in cursor.events))

    def test_service_recovery_after_degradation_is_a_new_transition(self):
        bus = LiveEventBus()
        first = bus.publish_service_status("ready", occurred_at_utc=NOW)
        self.assertIsNotNone(first)
        self.assertIsNone(bus.publish_service_status("ready", occurred_at_utc=NOW + timedelta(seconds=1)))
        degraded = bus.publish_service_status("degraded", occurred_at_utc=NOW + timedelta(seconds=2))
        recovered = bus.publish_service_status("ready", occurred_at_utc=NOW + timedelta(seconds=3))
        self.assertIsNotNone(degraded)
        self.assertIsNotNone(recovered)
        self.assertNotEqual(first.event_id, recovered.event_id)

    def test_expired_cursor_fails_closed_without_replaying_unknown_events(self):
        bus = LiveEventBus(max_events=2)
        old = bus.publish_service_status("one", occurred_at_utc=NOW)
        bus.publish_service_status("two", occurred_at_utc=NOW + timedelta(seconds=1))
        bus.publish_service_status("three", occurred_at_utc=NOW + timedelta(seconds=2))
        self.assertIsNotNone(old)
        cursor = bus.events_after(old.event_id if old else None)
        self.assertTrue(cursor.cursor_expired)
        self.assertEqual(cursor.events, ())
        self.assertIsNotNone(cursor.reset_to)

    def test_sse_frame_contains_only_declared_event_data_and_heartbeat(self):
        bus = LiveEventBus()
        event = bus.publish_service_status("connected", occurred_at_utc=NOW, detail="read-only")
        stream = SseEventStream(bus, heartbeat_seconds=0.01)
        self.assertEqual(next(stream.iter_frames()), b": connected\n\n")
        frame = stream.next_frame(timeout_seconds=0)
        self.assertIn(f"id: {event.event_id}".encode("utf-8"), frame)
        self.assertIn(b"event: service.status", frame)
        self.assertIn(b"read-only", frame)
        self.assertNotIn(b"Authorization", frame)
        self.assertEqual(stream.next_frame(timeout_seconds=0), b": heartbeat\n\n")

    def test_sse_reconnect_uses_last_event_id_without_duplicate(self):
        bus = LiveEventBus()
        event = bus.publish_service_status("connected", occurred_at_utc=NOW)
        stream = SseEventStream(bus, last_event_id=event.event_id if event else None)
        self.assertEqual(stream.next_frame(timeout_seconds=0), b": heartbeat\n\n")
        new_event = bus.publish_service_status("degraded", occurred_at_utc=NOW + timedelta(seconds=1))
        frame = stream.next_frame(timeout_seconds=0)
        self.assertIn(f"id: {new_event.event_id}".encode("utf-8"), frame)

    def test_empty_replay_window_does_not_create_a_reset_loop(self):
        stream = SseEventStream(LiveEventBus(), last_event_id="evt-old")
        frame = stream.next_frame(timeout_seconds=0)
        self.assertIn(b"event: cursor.reset", frame)
        self.assertNotIn(b"id: cursor-reset", frame)
        self.assertEqual(stream.next_frame(timeout_seconds=0), b": heartbeat\n\n")

    def test_batch_publishing_suppresses_unchanged_poll_and_emits_quality_event(self):
        config = _config()
        client = MassiveClient(config, "test-only-key", transport=FakeTransport())
        poller = RealtimePoller(config, client)
        bus = LiveEventBus()
        first = poller.poll_once(now_utc=NOW)
        self.assertEqual(len(bus.publish_batch(first)), 1)
        repeated = poller.poll_once(now_utc=NOW + timedelta(seconds=15))
        self.assertEqual(bus.publish_batch(repeated), ())

        failed_client = MassiveClient(config, "test-only-key", transport=FakeTransport(index_status=403))
        failed_batch = RealtimePoller(config, failed_client).poll_once(now_utc=NOW)
        events = LiveEventBus().publish_batch(failed_batch)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1].event_type, "quality.changed")
        self.assertTrue(events[1].notification)

    def test_state_candidate_is_confirmed_only_and_semantically_deduplicated(self):
        bus = LiveEventBus()
        payload = {
            "state": "normal",
            "strategy_version": "v10_preserve_shock_recovery",
            "signal_date": "2024-01-03",
            "data_quality": "ok",
            "confirmed": True,
            "provisional": False,
        }
        event = bus.publish_state_candidate(payload, occurred_at_utc=NOW)
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "state.candidate")
        self.assertTrue(event.notification)
        self.assertIsNone(bus.publish_state_candidate(payload, occurred_at_utc=NOW))
        with self.assertRaises(ValueError):
            bus.publish_state_candidate({**payload, "provisional": True}, occurred_at_utc=NOW)

    def test_live_service_and_server_remain_private(self):
        bus = LiveEventBus()
        service = LiveApiService(bus, access_policy=ApiAccessPolicy(access_token="token"))
        with self.assertRaises(ApiError) as denied:
            service.open_events(headers={"X-QQQ-Local-Token": "wrong"}, client_host="127.0.0.1")
        self.assertEqual(denied.exception.status_code, 403)
        with self.assertRaises(ApiError):
            service.open_events(headers={"X-QQQ-Local-Token": "token"}, client_host="10.0.0.5")
        with self.assertRaises(ValueError):
            create_live_server(service, host="0.0.0.0")
        server = create_live_server(service, host="127.0.0.1", port=0)
        try:
            self.assertEqual(server.server_address[0], "127.0.0.1")
        finally:
            server.server_close()
