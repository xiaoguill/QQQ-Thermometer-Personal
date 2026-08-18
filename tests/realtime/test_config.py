from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.realtime import RealtimeConfig, RealtimeConfigError, load_realtime_config


ROOT = Path(__file__).resolve().parents[2]


def _raw_config(**overrides):
    value = {
        "provider": "massive",
        "mode": "rest_poll",
        "base_url": "https://api.massive.com",
        "api_key_env": "MASSIVE_API_KEY",
        "refresh_interval_seconds": 900,
        "request_timeout_seconds": 15,
        "display_timezone": "Asia/Shanghai",
        "market_timezone": "America/New_York",
        "max_source_age_seconds": 1800,
        "future_skew_seconds": 60,
        "symbols": [
            {"symbol": "QQQ", "asset_class": "stocks", "role": "strategy_input"},
            {"symbol": "I:VIX", "asset_class": "indices", "role": "strategy_input"},
        ],
    }
    value.update(overrides)
    return value


class RealtimeConfigTests(unittest.TestCase):
    def test_repository_config_has_user_requested_defaults(self):
        config = load_realtime_config(ROOT / "configs" / "realtime" / "massive.json")
        self.assertEqual(config.provider, "massive")
        self.assertEqual(config.refresh_interval_seconds, 900)
        self.assertEqual(config.display_timezone, "Asia/Shanghai")
        self.assertEqual(len(config.symbols), 12)
        self.assertNotIn("apiKey", json.dumps(config.public_dict()))

    def test_interval_and_timezone_are_configurable_but_validated(self):
        config = RealtimeConfig.from_mapping(_raw_config(refresh_interval_seconds=1200))
        self.assertEqual(config.refresh_interval_seconds, 1200)
        with self.assertRaises(RealtimeConfigError):
            RealtimeConfig.from_mapping(_raw_config(refresh_interval_seconds=59))
        with self.assertRaises(RealtimeConfigError):
            RealtimeConfig.from_mapping(_raw_config(display_timezone="America/New_York"))

    def test_credentials_cannot_be_put_in_url_or_config(self):
        with self.assertRaises(RealtimeConfigError):
            RealtimeConfig.from_mapping(_raw_config(base_url="https://api.massive.com?apiKey=secret"))
        with self.assertRaises(RealtimeConfigError):
            RealtimeConfig.from_mapping(_raw_config(base_url="http://api.massive.com"))

    def test_invalid_symbols_and_quality_window_fail_closed(self):
        with self.assertRaises(RealtimeConfigError):
            RealtimeConfig.from_mapping(_raw_config(symbols=[]))
        with self.assertRaises(RealtimeConfigError):
            RealtimeConfig.from_mapping(_raw_config(max_source_age_seconds=899))
        with self.assertRaises(RealtimeConfigError):
            RealtimeConfig.from_mapping(_raw_config(symbols=[
                {"symbol": "QQQ", "asset_class": "stocks", "role": "strategy_input"},
                {"symbol": "QQQ", "asset_class": "stocks", "role": "benchmark"},
            ]))
