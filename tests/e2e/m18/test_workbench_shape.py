from __future__ import annotations

import unittest
from pathlib import Path

from src.api.m18.config import M18Config
from src.api.m18.main import build_application


ROOT = Path(__file__).resolve().parents[3]


class M18WorkbenchShapeTests(unittest.TestCase):
    def test_empty_runtime_exposes_all_m00_5_to_m17_fields(self) -> None:
        config = M18Config.from_mapping(
            {
                "m18_version": "m18-e2e/v1",
                "host": "127.0.0.1",
                "port": 0,
                "static_root": str(ROOT / "frontend" / "m18"),
                "database_path": ":memory:",
                "realtime_config_path": str(ROOT / "configs" / "realtime" / "massive.json"),
                "display_timezone": "Asia/Shanghai",
                "refresh_interval_seconds": 900,
                "history_start_date": "2024-01-01",
                "history_end_date": "2024-01-10",
                "paper_portfolio_id": "e2e-paper",
            },
            root=ROOT,
        )
        application, repository, configured = build_application(config, environ={})
        try:
            response = application.handle("GET", "/api/m18/workbench")
            data = response.body["data"]
            self.assertFalse(configured)
            self.assertEqual(response.status_code, 200)
            self.assertEqual([item["module_id"] for item in data["modules"]], ["M00.5", *[f"M{index:02d}" for index in range(1, 18)]])
            for key in ("latest_data_quality", "runtime_boundary", "paper_plan", "confirmed_strategy", "provisional_observation", "target_weights"):
                self.assertIn(key, data)
            self.assertEqual(data["confirmed_strategy"]["target_weights"], {})
            self.assertFalse(data["paper_plan"]["order_created"])
        finally:
            repository.store.close()


if __name__ == "__main__":
    unittest.main()
