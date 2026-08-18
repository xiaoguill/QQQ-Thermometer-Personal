from __future__ import annotations

import unittest
from pathlib import Path

from src.realtime import load_realtime_config
from src.realtime.__main__ import DEFAULT_STATIC_ROOT
from src.realtime.runtime import create_runtime_from_env


class M16RuntimeContractTests(unittest.TestCase):
    def test_factory_connects_configured_massive_client_to_local_runtime_without_fetching(self):
        bundle = create_runtime_from_env(environ={"MASSIVE_API_KEY": "test-only-key"})
        self.assertEqual(bundle.config.provider, "massive")
        self.assertEqual(bundle.config.refresh_interval_seconds, 900)
        self.assertEqual(bundle.config.display_timezone, "Asia/Shanghai")
        self.assertEqual(bundle.runtime.snapshot.status, "idle")

    def test_runtime_static_root_is_the_independent_m16_page(self):
        self.assertEqual(DEFAULT_STATIC_ROOT.name, "m16")
        self.assertTrue((Path(DEFAULT_STATIC_ROOT) / "index.html").is_file())

