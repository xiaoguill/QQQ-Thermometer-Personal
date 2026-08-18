from __future__ import annotations

import json
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from src.api.m17.config import M17Config
from src.api.m17.gateway import create_m17_application
from src.api.m17.server import create_m17_server
from src.realtime.models import ObservationBatch, RealtimeObservation


ROOT = Path(__file__).resolve().parents[3]


class _ConfirmedHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = {
            "meta": {
                "data_quality": "ok",
                "strategy_version": "v10-test",
                "signal_date": "2026-08-17",
                "contract_version": "1.0.0",
            },
            "data": {
                "state": "normal",
                "temperature": 63,
                "trend": "up",
                "signal_agreement": 0.75,
                "reason_codes": ["confirmed_test"],
                "target_weights": {"QQQ": 0.5, "BIL": 0.5},
            },
        }
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        return


class M17GatewayTests(unittest.TestCase):
    def setUp(self):
        self.confirmed_server = ThreadingHTTPServer(("127.0.0.1", 0), _ConfirmedHandler)
        self.confirmed_thread = threading.Thread(target=self.confirmed_server.serve_forever, daemon=True)
        self.confirmed_thread.start()
        self.temp = TemporaryDirectory()
        paper_path = Path(self.temp.name) / "paper.json"
        paper_path.write_text(
            json.dumps({
                "$schema": "qqq-m17-paper-input/v1",
                "portfolio_id": "test",
                "base_currency": "USD",
                "starting_cash": 1000,
                "positions": {"QQQ": {"quantity": 1}},
            }),
            encoding="utf-8",
        )
        self.config = M17Config.from_mapping(
            {
                "m17_version": "m17-test/v1",
                "host": "127.0.0.1",
                "port": 0,
                "static_root": str(ROOT / "frontend"),
                "m16_config_path": str(ROOT / "configs" / "realtime" / "massive.json"),
                "confirmed_api_base_url": f"http://127.0.0.1:{self.confirmed_server.server_address[1]}",
                "paper_config_path": str(paper_path),
                "display_timezone": "Asia/Shanghai",
                "heartbeat_seconds": 1,
                "confirmed_api_timeout_seconds": 3,
            },
            root=ROOT,
        )
        self.application = create_m17_application(self.config, environ={})

    def tearDown(self):
        self.application.runtime_handle.stop()
        self.confirmed_server.shutdown()
        self.confirmed_server.server_close()
        self.confirmed_thread.join(2)
        self.temp.cleanup()

    def test_confirmed_state_is_read_without_recalculation(self):
        overview = self.application.overview().body
        strategy = overview["confirmed_strategy"]
        self.assertTrue(strategy["confirmed"])
        self.assertEqual(strategy["state"], "normal")
        self.assertEqual(strategy["target_weights"], {"BIL": 0.5, "QQQ": 0.5})
        self.assertTrue(overview["boundaries"]["target_weights_are_not_recomputed"])

    def test_latest_provisional_batch_only_supplies_prices(self):
        now = datetime(2026, 8, 18, 13, 0, tzinfo=timezone.utc)
        observations = tuple(
            RealtimeObservation(
                provider="massive",
                symbol=symbol,
                asset_class="stocks",
                fetched_at_utc=now,
                source_timestamp_utc=now,
                last=100,
                close=100,
                previous_close=99,
                volume=1,
                price_basis="unadjusted_ohlcv",
                quality="OK",
                provisional=True,
            )
            for symbol in ("QQQ", "BIL")
        )
        batch = ObservationBatch("batch-test", now, observations, "Asia/Shanghai")
        self.application.live_api.publish_batch(batch)
        plan = self.application.paper_plan().body
        self.assertEqual(plan["status"], "READY")
        self.assertTrue(plan["paper_only"])
        self.assertFalse(plan["order_created"])
        self.assertTrue(all(action["not_order"] for action in plan["actions"]))

    def test_missing_massive_key_is_fail_closed(self):
        self.assertFalse(self.application.runtime_handle.massive_key_configured)
        self.assertEqual(self.application.runtime_handle.startup_error_code, "MISSING_API_KEY")


if __name__ == "__main__":
    unittest.main()
