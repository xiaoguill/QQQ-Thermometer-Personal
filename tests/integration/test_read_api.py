import tempfile
import unittest
from pathlib import Path

from src.api import ApiAccessPolicy, ReadApiService, create_app, create_http_server
from src.storage.sqlite_store import SQLiteRepository, SQLiteStore


class ReadApiIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = SQLiteStore(self.root / "api.sqlite", allowed_root=self.root).initialize()
        self.repository = SQLiteRepository(self.store)
        self.app = create_app(self.repository)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def _seed(self):
        as_of = "2024-01-03T22:00:00Z"
        target_weights = {"QQQ": 0.6, "QLD": 0.0, "XLU": 0.0, "IAU": 0.0, "TLT": 0.0, "BIL": 0.4, "VXX": 0.0}
        self.repository.put_indicator_snapshot(
            "indicator|2024-01-03",
            {
                "schema": "qqq-indicator-snapshot/v1",
                "signal_date": "2024-01-03",
                "as_of": as_of,
                "indicator_version": "m04-indicators/v27",
                "quality": "OK",
                "values": {"qqq_ema10": 101.0, "qqq_rv20": 0.2, "vix": 16.0},
            },
            signal_date="2024-01-03",
            as_of=as_of,
            indicator_version="m04-indicators/v27",
            quality="OK",
        )
        self.repository.put_regime_snapshot(
            "regime|2024-01-03",
            {
                "schema": "qqq-regime-snapshot/v1",
                "strategy_version": "v10_preserve_shock_recovery",
                "regime_version": "m05-regime/v1",
                "signal_date": "2024-01-03",
                "execution_date": "2024-01-04",
                "as_of": as_of,
                "data_quality": "OK",
                "indicator_quality": "OK",
                "state": "normal",
                "temperature": 68.0,
                "trend": "improving",
                "signal_agreement": 0.75,
                "reason_codes": ["medium_gate_confirmed"],
                "next_triggers": [{"code": "shock_entry", "status": "watch"}],
                "run_id": "paper-run-2024-01-04",
            },
            signal_date="2024-01-03",
            execution_date="2024-01-04",
            as_of=as_of,
            strategy_version="v10_preserve_shock_recovery",
            state="normal",
            quality="OK",
        )
        self.repository.put_target_weight_snapshot(
            "target|2024-01-03",
            {
                "schema": "qqq-target-weight-snapshot/v1",
                "implementation_version": "m07-target-weights/v1",
                "strategy_version": "v10_preserve_shock_recovery",
                "signal_date": "2024-01-03",
                "execution_date": "2024-01-04",
                "as_of": as_of,
                "data_quality": "OK",
                "target_weights": target_weights,
                "candidate_only": True,
                "execution_eligible": False,
                "weight_status": "CANDIDATE_ONLY",
                "run_id": "paper-run-2024-01-04",
            },
            signal_date="2024-01-03",
            execution_date="2024-01-04",
            as_of=as_of,
            strategy_version="v10_preserve_shock_recovery",
            state="normal",
            weight_status="CANDIDATE_ONLY",
            data_quality="OK",
        )
        self.repository.put_portfolio_snapshot(
            "portfolio|personal-paper|2024-01-04",
            {
                "schema": "qqq-paper-portfolio/v1",
                "portfolio_id": "personal-paper",
                "signal_date": "2024-01-03",
                "execution_date": "2024-01-04",
                "as_of": as_of,
                "strategy_version": "v10_preserve_shock_recovery",
                "data_quality": "OK",
                "status": "PAPER_SHADOW",
                "cash": 12.5,
                "nav": 99_998.0,
                "positions": {"QQQ": 599.0, "BIL": 400.0},
                "run_id": "paper-run-2024-01-04",
            },
            portfolio_id="personal-paper",
            as_of=as_of,
            status="PAPER_SHADOW",
            nav=99_998.0,
            cash=12.5,
        )
        self.repository.put_run(
            "paper-run|personal-paper|2024-01-04",
            {
                "schema": "qqq-paper-shadow-run/v1",
                "run_id": "paper-run-2024-01-04",
                "execution_date": "2024-01-04",
                "as_of": as_of,
                "strategy_version": "v10_preserve_shock_recovery",
                "performance_metrics": {"cagr": 0.12, "max_drawdown": -0.18},
            },
            run_type="paper_shadow",
            started_at=as_of,
            finished_at=as_of,
            status="SIMULATED",
            strategy_version="v10_preserve_shock_recovery",
            data_version="m04-indicators/v27",
        )
        self.repository.put_quality_event(
            "quality|2024-01-03",
            {"schema": "quality/v1", "status": "OK", "message": "all required fixtures available"},
            event_date="2024-01-03",
            source="fixture",
            symbol="QQQ",
            severity="INFO",
            status="OK",
        )
        self.repository.put_strategy_version(
            "v10_preserve_shock_recovery",
            {
                "schema": "strategy-version/v1",
                "version": "v10_preserve_shock_recovery",
                "status": "research_candidate",
                "config_hash": "a" * 64,
            },
            status="research_candidate",
            config_hash="a" * 64,
            approved_by="personal-review",
            approved_at=as_of,
        )
        self.repository.put_ledger_event(
            "ledger|personal-paper|2024-01-04|001|BUY",
            {
                "schema": "qqq-paper-ledger-event/v1",
                "event_type": "BUY",
                "event_date": "2024-01-04",
                "symbol": "QQQ",
                "quantity": 599.0,
                "price": 100.0,
                "cost": 3.0,
                "run_id": "paper-run-2024-01-04",
            },
            event_date="2024-01-04",
            event_type="BUY",
            idempotency_key="ledger|personal-paper|2024-01-04|001|BUY",
            status="RECORDED",
            quantity=599.0,
            price=100.0,
            cost=3.0,
        )

    def _assert_meta(self, body):
        self.assertIn("meta", body)
        required = {"contract_version", "strategy_version", "as_of", "signal_date", "execution_date", "data_quality", "run_id"}
        self.assertTrue(required.issubset(body["meta"]))
        self.assertEqual(body["meta"]["contract_version"], "1.0.0")
        self.assertIn(body["meta"]["data_quality"], {"ok", "stale", "partial", "failed", "needs_review"})

    def test_all_frozen_get_endpoints_return_versioned_json_envelopes(self):
        self._seed()
        cases = [
            ("/api/thermometer/latest", None),
            ("/api/thermometer/history", {"limit": "10"}),
            ("/api/signals/explain", {"as_of": "2024-01-04"}),
            ("/api/triggers/next", None),
            ("/api/portfolio/targets", {"as_of": "2024-01-04"}),
            ("/api/portfolio/latest", None),
            ("/api/portfolio/ledger", {"from": "2024-01-01", "to": "2024-01-05", "limit": "10"}),
            ("/api/performance/curve", {"as_of": "2024-01-05"}),
            ("/api/performance/metrics", {"as_of": "2024-01-05"}),
            ("/api/data-quality/latest", None),
            ("/api/versions", None),
        ]
        for path, query in cases:
            with self.subTest(path=path):
                response = self.app.handle("GET", path, query=query)
                self.assertEqual(response.status_code, 200)
                self._assert_meta(response.body)
                self.assertIn("data", response.body)

    def test_read_models_are_returned_without_strategy_recalculation(self):
        self._seed()
        latest = self.app.handle("GET", "/api/thermometer/latest")
        self.assertEqual(latest.body["data"]["state"], "normal")
        self.assertEqual(latest.body["data"]["temperature"], 68.0)
        self.assertEqual(latest.body["data"]["target_weights"]["QQQ"], 0.6)
        explanation = self.app.handle("GET", "/api/signals/explain", query={"as_of": "2024-01-04"})
        self.assertEqual(explanation.body["data"]["indicators"]["qqq_ema10"], 101.0)
        target = self.app.handle("GET", "/api/portfolio/targets")
        self.assertEqual(target.body["data"]["target_weights"]["BIL"], 0.4)
        portfolio = self.app.handle("GET", "/api/portfolio/latest")
        self.assertEqual(portfolio.body["data"]["status"], "PAPER_SHADOW")
        metrics = self.app.handle("GET", "/api/performance/metrics")
        self.assertEqual(metrics.body["data"]["max_drawdown"], -0.18)

    def test_empty_read_models_are_explicitly_failed_not_silently_confirmed(self):
        response = self.app.handle("GET", "/api/thermometer/latest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body["meta"]["data_quality"], "failed")
        self.assertEqual(response.body["data"]["state"], "needs_review")
        self.assertEqual(response.body["data"]["target_weights"], {})
        quality = self.app.handle("GET", "/api/data-quality/latest")
        self.assertEqual(quality.body["data"]["status"], "failed")

    def test_pagination_date_filters_and_query_validation_are_bounded(self):
        self._seed()
        valid = self.app.handle("GET", "/api/portfolio/ledger", query={"from": "2024-01-04", "to": "2024-01-04", "limit": "1"})
        self.assertEqual(valid.status_code, 200)
        self.assertLessEqual(len(valid.body["data"]), 1)
        for query in (
            {"limit": "0"},
            {"limit": "501"},
            {"from": "2024-01-05", "to": "2024-01-04"},
            {"unexpected": "1"},
            {"as_of": "not-a-date"},
        ):
            response = self.app.handle("GET", "/api/portfolio/ledger", query=query)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.body["error"]["code"], "INVALID_REQUEST")

    def test_local_access_policy_and_optional_token_are_enforced(self):
        self._seed()
        remote = self.app.handle("GET", "/api/versions", client_host="10.0.0.4")
        self.assertEqual(remote.status_code, 403)
        protected = create_app(self.repository, access_policy=ApiAccessPolicy(access_token="local-secret"))
        self.assertEqual(protected.handle("GET", "/api/versions", headers={"X-QQQ-Local-Token": "wrong"}).status_code, 403)
        self.assertEqual(protected.handle("GET", "/api/versions", headers={"X-QQQ-Local-Token": "local-secret"}).status_code, 200)

    def test_paper_confirmation_is_the_only_api_write_and_is_idempotent(self):
        body = {
            "idempotency_key": "confirm-2024-01-04",
            "observation_date": "2024-01-04",
            "decision": "confirm",
            "note": "paper only",
        }
        first = self.app.handle("POST", "/api/paper/confirm", body=body)
        self.assertEqual(first.status_code, 201)
        self.assertFalse(first.body["data"]["idempotent"])
        self.assertTrue(first.body["data"]["paper_only"])
        self.assertFalse(first.body["data"]["order_created"])
        second = self.app.handle("POST", "/api/paper/confirm", body=body)
        self.assertEqual(second.status_code, 201)
        self.assertTrue(second.body["data"]["idempotent"])
        self.assertEqual(self.repository.count("ledger_event"), 1)
        conflict = self.app.handle("POST", "/api/paper/confirm", body={**body, "decision": "pause"})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(self.repository.count("portfolio_snapshot"), 0)

    def test_http_methods_errors_and_server_binding(self):
        self.assertEqual(self.app.handle("POST", "/api/versions", body={}).status_code, 405)
        self.assertEqual(self.app.handle("GET", "/api/unknown").status_code, 404)
        invalid_body = self.app.handle("POST", "/api/paper/confirm", body="not json")
        self.assertEqual(invalid_body.status_code, 400)
        server = create_http_server(self.app, host="127.0.0.1", port=0)
        try:
            self.assertEqual(server.server_address[0], "127.0.0.1")
        finally:
            server.server_close()
        with self.assertRaises(ValueError):
            create_http_server(self.app, host="0.0.0.0", port=8765)

    def test_confirm_rejects_extra_fields_and_invalid_decisions(self):
        extra = self.app.handle(
            "POST",
            "/api/paper/confirm",
            body={"idempotency_key": "x", "observation_date": "2024-01-04", "decision": "confirm", "weights": {"QQQ": 1.0}},
        )
        self.assertEqual(extra.status_code, 400)
        invalid = self.app.handle(
            "POST",
            "/api/paper/confirm",
            body={"idempotency_key": "x", "observation_date": "2024-01-04", "decision": "trade"},
        )
        self.assertEqual(invalid.status_code, 400)


if __name__ == "__main__":
    unittest.main()
