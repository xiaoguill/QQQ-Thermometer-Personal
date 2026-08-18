from __future__ import annotations

import unittest
from datetime import timedelta
import json
from pathlib import Path
import tempfile

from src.api.live_api import create_live_app
from src.notifications import LiveEventBus
from src.realtime.massive_client import MassiveClient, MissingApiKeyError
from src.realtime.poller import RealtimePoller
from src.realtime.runtime import RealtimeRuntime, create_runtime_from_env, create_unavailable_live_app
from src.storage import SQLiteStore
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

    def test_missing_key_can_still_serve_a_fail_closed_local_page(self):
        config, live_api = create_unavailable_live_app(reason="MASSIVE_API_KEY_UNAVAILABLE")
        self.assertEqual(config.refresh_interval_seconds, 900)
        cursor = live_api.event_bus.events_after()
        self.assertEqual(len(cursor.events), 1)
        self.assertEqual(cursor.events[0].event_type, "service.status")
        self.assertEqual(cursor.events[0].payload["status"], "MASSIVE_API_KEY_UNAVAILABLE")

    def test_runtime_factory_mounts_existing_confirmed_read_model(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            database = root / "confirmed.sqlite"
            store = SQLiteStore(database, allowed_root=root).initialize()
            store.close()
            config_path = root / "massive.json"
            config = _config().public_dict()
            config["confirmed_read_model_path"] = str(database)
            config_path.write_text(json.dumps(config), encoding="utf-8")
            bundle = create_runtime_from_env(config_path, environ={"MASSIVE_API_KEY": "test-only-key"})
            try:
                self.assertEqual(bundle.confirmed_read_model_path, str(database))
                self.assertIsNotNone(bundle.live_api.confirmed_read_model_path)
                response = bundle.live_api.latest_confirmed()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.body["meta"]["data_quality"], "failed")
            finally:
                bundle.close()
