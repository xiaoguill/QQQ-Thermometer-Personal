from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from src.api.m18.config import M18Config, load_m18_config
from src.api.m18.http_server import create_http_server
from src.api.m18.main import build_application


ROOT = Path(__file__).resolve().parents[3]


class M18ConfigAndServerTests(unittest.TestCase):
    def test_versioned_config_is_local_and_non_secret(self) -> None:
        config = load_m18_config(ROOT / "configs" / "m18" / "workbench.json")
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.display_timezone, "Asia/Shanghai")
        self.assertEqual(config.refresh_interval_seconds, 900)
        self.assertEqual(config.history_start_date, "2008-01-01")
        self.assertNotIn("api_key", json.dumps(config.public_dict()).lower())

    def test_empty_application_and_static_workbench_are_typed(self) -> None:
        config = M18Config.from_mapping(
            {
                "m18_version": "m18-test/v1",
                "host": "127.0.0.1",
                "port": 0,
                "static_root": str(ROOT / "frontend" / "m18"),
                "database_path": ":memory:",
                "realtime_config_path": str(ROOT / "configs" / "realtime" / "massive.json"),
                "display_timezone": "Asia/Shanghai",
                "refresh_interval_seconds": 900,
                "history_start_date": "2024-01-01",
                "history_end_date": "2024-01-10",
                "paper_portfolio_id": "test-paper",
            },
            root=ROOT,
        )
        application, repository, configured = build_application(config, environ={})
        self.assertFalse(configured)
        self.assertEqual(application.latest().overall_quality, "UNAVAILABLE")
        server = create_http_server(application, port=0, static_root=config.static_root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urlopen(origin + "/", timeout=3) as response:
                page = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertIn("市场温度工作台", page)
            with urlopen(origin + "/api/m18/workbench", timeout=3) as response:
                body = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 200)
                self.assertEqual(len(body["data"]["modules"]), 18)
            request = Request(origin + "/api/paper/confirm", method="POST", data=b"{}")
            with self.assertRaises(Exception) as caught:
                urlopen(request, timeout=3)
            self.assertIn("405", str(caught.exception))
        finally:
            server.shutdown()
            server.server_close()
            repository.store.close()


if __name__ == "__main__":
    unittest.main()
