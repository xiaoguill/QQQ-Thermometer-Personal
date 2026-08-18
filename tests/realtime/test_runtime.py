from __future__ import annotations

import unittest
from datetime import timedelta

from src.api.live_api import create_live_app
from src.notifications import LiveEventBus
from src.realtime.massive_client import MassiveClient, MissingApiKeyError
from src.realtime.poller import RealtimePoller
from src.realtime.runtime import RealtimeRuntime, create_runtime_from_env
from tests.realtime.test_massive_client import FakeTransport, NOW, _config


class RealtimeRuntimeTests(unittest.TestCase):
    def test_poll_once_publishes_batch_and_service_status_without_repeating_duplicates(self):
        config = _config()
        bus = LiveEventBus()
        poller = RealtimePoller(config, MassiveClient(config, "test-only-key", transport=FakeTransport()))
        runtime = RealtimeRuntime(config, poller, create_live_app(bus))
        first = runtime.poll_once(now_utc=NOW)
        self.assertEqual(runtime.snapshot.status, "ready")
        self.assertEqual(runtime.snapshot.last_batch_id, first.batch_id)
        self.assertEqual(bus.size, 2)
        runtime.poll_once(now_utc=NOW + timedelta(seconds=15))
        self.assertEqual(bus.size, 2)
        self.assertEqual(runtime.snapshot.consecutive_failures, 0)

    def test_quality_failure_is_published_and_next_delay_backs_off(self):
        config = _config()
        bus = LiveEventBus()
        poller = RealtimePoller(config, MassiveClient(config, "test-only-key", transport=FakeTransport(index_status=403)))
        runtime = RealtimeRuntime(config, poller, create_live_app(bus))
        runtime.poll_once(now_utc=NOW)
        self.assertEqual(runtime.snapshot.status, "degraded")
        self.assertEqual(runtime.snapshot.consecutive_failures, 1)
        self.assertEqual(poller.next_retry_delay_seconds(), 1800)
        self.assertEqual(bus.size, 3)

    def test_runtime_factory_requires_environment_key_before_starting(self):
        with self.assertRaises(MissingApiKeyError):
            create_runtime_from_env(environ={})

