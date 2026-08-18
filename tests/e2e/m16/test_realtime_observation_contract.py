from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src.realtime import RealtimeConfig, RealtimePoller, evaluate_observation_quality, load_realtime_config
from src.realtime.models import RealtimeObservation
from src.realtime.massive_client import MassiveClient
from tests.realtime.test_massive_client import FakeTransport, NOW, _config


class M16RealtimeObservationContractTests(unittest.TestCase):
    def test_versioned_runtime_defaults_are_safe_and_east_eight(self):
        config = load_realtime_config()
        self.assertEqual(config.provider, "massive")
        self.assertEqual(config.refresh_interval_seconds, 900)
        self.assertEqual(config.display_timezone, "Asia/Shanghai")
        self.assertEqual(config.base_url, "https://api.massive.com")

    def test_documented_nested_snapshot_is_normalized_without_external_network(self):
        config = _config()
        client = MassiveClient(config, "test-only-key", transport=FakeTransport())
        batch = client.fetch_batch(fetched_at_utc=NOW)
        qqq = next(item for item in batch.observations if item.symbol == "QQQ")
        self.assertEqual(qqq.quality, "OK")
        self.assertEqual(qqq.last, 500.5)
        self.assertEqual(qqq.source_timestamp_utc, NOW - timedelta(minutes=5))

    def test_future_source_time_is_fail_closed_even_with_configured_skew(self):
        item = RealtimeObservation(
            provider="massive",
            symbol="QQQ",
            asset_class="stocks",
            fetched_at_utc=NOW,
            source_timestamp_utc=NOW + timedelta(seconds=1),
            last=500.0,
            close=500.0,
            previous_close=499.0,
            volume=100.0,
            price_basis="unadjusted_ohlcv",
            quality="OK",
            provisional=True,
        )
        result = evaluate_observation_quality(item, now_utc=NOW, max_age_seconds=1800, future_skew_seconds=60)
        self.assertEqual(result.quality, "NEEDS_REVIEW")
        self.assertEqual(result.error_code, "FUTURE_TIMESTAMP")

    def test_repeated_poll_is_duplicate_and_does_not_reduce_retry_safety(self):
        config = RealtimeConfig.from_mapping({
            "provider": "massive",
            "mode": "rest_poll",
            "base_url": "https://api.massive.com",
            "api_key_env": "MASSIVE_API_KEY",
            "refresh_interval_seconds": 900,
            "request_timeout_seconds": 15,
            "display_timezone": "Asia/Shanghai",
            "market_timezone": "America/New_York",
            "max_source_age_seconds": 1800,
            "future_skew_seconds": 0,
            "symbols": [{"symbol": "QQQ", "asset_class": "stocks", "role": "strategy_input"}],
        })
        poller = RealtimePoller(config, MassiveClient(config, "test-only-key", transport=FakeTransport()))
        poller.poll_once(now_utc=NOW)
        repeated = poller.poll_once(now_utc=NOW + timedelta(seconds=15))
        self.assertTrue(repeated.observations[0].is_duplicate)
        self.assertEqual(poller.next_retry_delay_seconds(), 900)

