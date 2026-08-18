from __future__ import annotations

import unittest
from pathlib import Path

from src.api.m17.config import M17Config, M17ConfigError, load_m17_config


ROOT = Path(__file__).resolve().parents[3]


class M17ConfigTests(unittest.TestCase):
    def test_repository_config_is_local_and_15_minute_default(self):
        config = load_m17_config(ROOT / "configs" / "m17" / "unified.json")
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 4173)
        self.assertEqual(config.display_timezone, "Asia/Shanghai")
        self.assertEqual(config.heartbeat_seconds, 15)
        self.assertEqual(config.confirmed_api_timeout_seconds, 5)
        self.assertEqual(config.m16_config_path.name, "massive.json")

    def test_non_loopback_confirmed_api_is_rejected(self):
        raw = {
            "m17_version": "m17-test/v1",
            "host": "127.0.0.1",
            "port": 4173,
            "static_root": str(ROOT / "frontend"),
            "m16_config_path": str(ROOT / "configs" / "realtime" / "massive.json"),
            "confirmed_api_base_url": "https://example.test/api",
            "paper_config_path": str(ROOT / "configs" / "paper" / "m17.json"),
            "display_timezone": "Asia/Shanghai",
            "heartbeat_seconds": 15,
            "confirmed_api_timeout_seconds": 5,
        }
        with self.assertRaises(M17ConfigError):
            M17Config.from_mapping(raw, root=ROOT)

    def test_server_bind_host_is_local_only(self):
        raw = {
            "m17_version": "m17-test/v1",
            "host": "0.0.0.0",
            "port": 4173,
            "static_root": str(ROOT / "frontend"),
            "m16_config_path": str(ROOT / "configs" / "realtime" / "massive.json"),
            "confirmed_api_base_url": "http://127.0.0.1:8765",
            "paper_config_path": str(ROOT / "configs" / "paper" / "m17.json"),
            "display_timezone": "Asia/Shanghai",
            "heartbeat_seconds": 15,
            "confirmed_api_timeout_seconds": 5,
        }
        with self.assertRaises(M17ConfigError):
            M17Config.from_mapping(raw, root=ROOT)


if __name__ == "__main__":
    unittest.main()
