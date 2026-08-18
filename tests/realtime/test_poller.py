from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from src.realtime import RealtimePoller, evaluate_observation_quality
from src.realtime.models import RealtimeObservation
from tests.realtime.test_massive_client import FakeTransport, NOW, _config
from src.realtime.massive_client import MassiveClient


class PollerTests(unittest.TestCase):
    def test_stale_and_future_source_times_fail_closed(self):
        client = MassiveClient(_config(), "secret-value", transport=FakeTransport())
        poller = RealtimePoller(_config(), client)
        stale = poller.poll_once(now_utc=NOW + timedelta(minutes=30))
        self.assertTrue(all(item.quality == "STALE" for item in stale.observations))

        future = poller.poll_once(now_utc=NOW - timedelta(minutes=10))
        self.assertTrue(all(item.quality == "NEEDS_REVIEW" for item in future.observations))

    def test_next_refresh_uses_configured_seconds_and_display_is_east_eight(self):
        client = MassiveClient(_config(), "secret-value", transport=FakeTransport())
        poller = RealtimePoller(_config(), client)
        self.assertEqual(poller.next_refresh_at(fetched_at_utc=NOW), NOW + timedelta(seconds=900))
        batch = poller.poll_once(now_utc=NOW)
        qqq = next(item for item in batch.observations if item.symbol == "QQQ")
        rendered = qqq.as_dict(display_timezone="Asia/Shanghai")
        self.assertTrue(rendered["fetched_at"].endswith("+08:00"))

    def test_non_ok_input_remains_non_ok(self):
        item = RealtimeObservation(
            provider="massive",
            symbol="QQQ",
            asset_class="stocks",
            fetched_at_utc=NOW,
            source_timestamp_utc=NOW,
            last=None,
            close=None,
            previous_close=None,
            volume=None,
            price_basis="unknown",
            quality="FAILED",
            provisional=True,
        )
        result = evaluate_observation_quality(item, now_utc=NOW, max_age_seconds=1800, future_skew_seconds=60)
        self.assertEqual(result.quality, "FAILED")
        self.assertEqual(result.error_code, None)
