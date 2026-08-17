from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from src.api import ApiAccessPolicy, create_app, create_http_server
import src.api.http_server as http_server_module
from src.jobs import JobRequest, JobOrchestrator, JobStages, StageResult
from src.storage import (
    INDICATOR_VERSION,
    IndicatorSnapshot,
    NormalizedBar,
    PaperDayInput,
    PaperExecutionConfig,
    PaperInputError,
    PaperPortfolioService,
    PaperPrice,
    SQLiteRepository,
    SQLiteStore,
    TradingCalendar,
)
from src.thermometer.contracts import load_contract
from src.thermometer.explanation import ExplanationInput, build_explanation
from src.thermometer.regime import (
    RegimeConfig,
    RegimeInput,
    RegimeState,
    replay_regimes,
)
from src.thermometer.target_weights import build_target_weights


ROOT = Path(__file__).resolve().parents[2]
RELEASE_DOC = ROOT / "docs" / "release" / "M15_PERSONAL_RELEASE.md"


class M15PrivateReleaseE2ETests(unittest.TestCase):
    def _inputs(self, calendar: TradingCalendar, count: int = 1) -> tuple[RegimeInput, ...]:
        sessions = calendar.sessions("2024-01-03", "2024-02-15")[:count]
        result: list[RegimeInput] = []
        for index, signal_date in enumerate(sessions):
            as_of = f"{signal_date}T22:00:00Z"
            values = {
                "qqq_return_5d": 0.03,
                "qqq_return_10d": 0.04,
                "qqq_return_20d": 0.05,
                "qqq_ema10": 99.0,
                "qqq_sma150": 90.0,
                "qqq_momentum126": 0.1,
                "qqq_rv20": 0.2,
                "vix": 20.0,
                "vix3m": 22.0,
                "vix_term_ratio": 20.0 / 22.0,
            }
            indicators = IndicatorSnapshot(
                signal_date=signal_date,
                as_of=as_of,
                calendar_id=calendar.calendar_id,
                indicator_version=INDICATOR_VERSION,
                quality="OK",
                ready=True,
                values=values,
                input_bar_dates={
                    "QQQ": (signal_date,),
                    "VIX": (signal_date,),
                    "VIX3M": (signal_date,),
                },
                price_basis_by_symbol={
                    "QQQ": "adjusted_ohlcv",
                    "VIX": "index_level",
                    "VIX3M": "index_level",
                },
                timezone_by_symbol={
                    "QQQ": "America/New_York",
                    "VIX": "America/New_York",
                    "VIX3M": "America/New_York",
                },
            )
            bar = NormalizedBar(
                symbol="QQQ",
                bar_date=signal_date,
                open=100.0 + index,
                high=100.0 + index,
                low=100.0 + index,
                close=100.0 + index,
                volume=None,
                sources=("m15-versioned-fixture",),
                snapshot_ids=(f"m15-qqq-{signal_date}",),
                retrieved_at_by_source=(("m15-versioned-fixture", as_of),),
                price_basis="adjusted_ohlcv",
                timezone="America/New_York",
                quality="OK",
            )
            result.append(RegimeInput(indicators, bar))
        return tuple(result)

    def _base_target(self, calendar: TradingCalendar):
        registry = load_contract()
        replay = replay_regimes(
            self._inputs(calendar),
            config=RegimeConfig.from_registry(registry),
            calendar=calendar,
            initial_state=RegimeState("normal", elapsed_state_sessions=4, medium_gate_streak=4),
        )
        target = build_target_weights(replay.snapshots[0], registry=registry, calendar=calendar)
        self.assertEqual(target.state, "normal")
        self.assertTrue(target.candidate_only)
        self.assertFalse(target.execution_eligible)
        self.assertEqual(set(target.target_weights), {"QQQ", "QLD", "XLU", "IAU", "TLT", "BIL", "VXX"})
        return replay.snapshots[0], self._inputs(calendar)[0].indicators, target

    @staticmethod
    def _prices(execution_date: str, index: int = 0) -> tuple[PaperPrice, ...]:
        return (
            PaperPrice("BIL", execution_date, 100.0 + index * 0.01, "unadjusted_ohlcv"),
            PaperPrice("QQQ", execution_date, 100.0 + index, "unadjusted_ohlcv"),
        )

    def test_full_chain_reaches_read_api_without_leaving_paper_boundary(self):
        calendar = TradingCalendar()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            store = SQLiteStore(root / "full-chain.sqlite", allowed_root=root).initialize()
            try:
                repository = SQLiteRepository(store)
                regime, indicators, target = self._base_target(calendar)
                repository.put_indicator_snapshot(
                    f"indicator|{indicators.signal_date}",
                    indicators.as_dict(),
                    signal_date=indicators.signal_date,
                    as_of=indicators.as_of,
                    indicator_version=indicators.indicator_version,
                    quality=indicators.quality,
                )
                repository.put_regime_snapshot(
                    f"regime|{regime.signal_date}",
                    regime.as_dict(),
                    signal_date=regime.signal_date,
                    execution_date=regime.execution_date,
                    as_of=regime.as_of,
                    strategy_version=regime.strategy_version,
                    state=regime.state,
                    quality=regime.indicator_quality,
                )
                repository.put_target_weight_snapshot(
                    f"target|{target.signal_date}",
                    target.as_dict(),
                    signal_date=target.signal_date,
                    execution_date=target.execution_date,
                    as_of=target.as_of,
                    strategy_version=target.strategy_version,
                    state=target.state,
                    weight_status=target.weight_status,
                    data_quality=target.data_quality,
                )
                explanation = build_explanation(ExplanationInput(regime, indicators))
                self.assertTrue(explanation.confirmed)

                paper = PaperPortfolioService(
                    repository,
                    config=PaperExecutionConfig(cost_bps=5.0, slippage_bps=10.0, price_basis="unadjusted_ohlcv"),
                    calendar=calendar,
                )
                result = paper.simulate_day(
                    PaperDayInput(
                        portfolio_id="m15-personal-paper",
                        run_id="m15-full-chain",
                        target=target,
                        prices=self._prices(target.execution_date),
                    )
                )
                self.assertEqual(result.state.status, "PAPER_SHADOW")
                self.assertEqual(result.reconciliation.status, "RECONCILED")

                app = create_app(repository)
                latest = app.handle("GET", "/api/thermometer/latest")
                portfolio = app.handle("GET", "/api/portfolio/latest")
                curve = app.handle("GET", "/api/performance/curve")
                ledger = app.handle("GET", "/api/portfolio/ledger", query={"limit": "100"})
                self.assertEqual(latest.status_code, 200)
                self.assertEqual(latest.body["data"]["state"], "normal")
                self.assertEqual(latest.body["data"]["target_weights"]["QQQ"], 0.6)
                self.assertEqual(portfolio.body["data"]["status"], "PAPER_SHADOW")
                self.assertEqual(curve.body["data"][0]["execution_date"], target.execution_date)
                self.assertGreaterEqual(len(ledger.body["data"]), 2)
                self.assertNotIn("order_id", json.dumps(ledger.body, ensure_ascii=False))
            finally:
                store.close()

    def test_twenty_session_shadow_backup_restore_is_independent(self):
        calendar = TradingCalendar()
        sessions = calendar.sessions("2024-01-03", "2024-02-15")[:21]
        self.assertEqual(len(sessions), 21)
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            database = root / "paper.sqlite"
            store = SQLiteStore(database, allowed_root=root).initialize()
            repository = SQLiteRepository(store)
            service = PaperPortfolioService(
                repository,
                config=PaperExecutionConfig(cost_bps=5.0, slippage_bps=10.0, price_basis="unadjusted_ohlcv"),
                calendar=calendar,
            )
            _, _, base_target = self._base_target(calendar)
            results = []
            for index in range(20):
                target = replace(
                    base_target,
                    signal_date=sessions[index],
                    execution_date=sessions[index + 1],
                    as_of=f"{sessions[index]}T22:00:00Z",
                    regime_snapshot_hash=hashlib.sha256(sessions[index].encode("utf-8")).hexdigest(),
                )
                results.append(
                    service.simulate_day(
                        PaperDayInput(
                            portfolio_id="m15-shadow",
                            run_id=f"m15-shadow-{index:02d}",
                            target=target,
                            prices=self._prices(target.execution_date, index),
                        )
                    )
                )
            self.assertEqual(len(results), 20)
            self.assertEqual(repository.count("portfolio_snapshot"), 20)
            self.assertEqual(repository.count("run"), 20)
            self.assertTrue(all(item.reconciliation.status == "RECONCILED" for item in results))
            self.assertTrue(all(item.state.data_quality == "OK" for item in results))

            backup = root / "backup" / "paper.sqlite"
            returned = store.backup_to(backup)
            self.assertEqual(returned, backup.resolve())
            backup_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
            self.assertEqual(len(backup_hash), 64)
            source_counts = {entity: repository.count(entity) for entity in ("portfolio_snapshot", "ledger_event", "run")}
            store.close()

            restored_store = SQLiteStore(backup, allowed_root=root).initialize()
            restored_repository = SQLiteRepository(restored_store)
            try:
                for entity, count in source_counts.items():
                    self.assertEqual(restored_repository.count(entity), count)
                source_store = SQLiteStore(database, allowed_root=root).initialize()
                source_repository = SQLiteRepository(source_store)
                source_repository.put_quality_event(
                    "m15-post-backup-event",
                    {"schema": "m15-quality/v1", "status": "OK", "message": "source changed after backup"},
                    event_date="2024-02-16",
                    source="m15-fixture",
                    symbol="QQQ",
                    severity="INFO",
                    status="OK",
                )
                source_store.close()
                self.assertEqual(restored_repository.count("quality_event"), 0)
            finally:
                restored_store.close()

    def test_private_access_control_and_local_bind_are_enforced(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            store = SQLiteStore(root / "access.sqlite", allowed_root=root).initialize()
            try:
                repository = SQLiteRepository(store)
                app = create_app(repository)
                remote = app.handle("GET", "/api/versions", client_host="10.0.0.4")
                self.assertEqual(remote.status_code, 403)

                protected = create_app(repository, access_policy=ApiAccessPolicy(access_token="m15-local-token"))
                self.assertEqual(protected.handle("GET", "/api/versions").status_code, 403)
                self.assertEqual(
                    protected.handle("GET", "/api/versions", headers={"X-QQQ-Local-Token": "wrong"}).status_code,
                    403,
                )
                self.assertEqual(
                    protected.handle("GET", "/api/versions", headers={"X-QQQ-Local-Token": "m15-local-token"}).status_code,
                    200,
                )
                with self.assertRaises(ValueError):
                    create_http_server(app, host="0.0.0.0", port=0)
                class FakeServer:
                    def __init__(self, address, _handler):
                        self.server_address = address

                    def server_close(self):
                        return None

                with patch.object(http_server_module, "ThreadingHTTPServer", FakeServer):
                    server = create_http_server(app, host="127.0.0.1", port=0)
                    try:
                        self.assertEqual(server.server_address[0], "127.0.0.1")
                    finally:
                        server.server_close()
            finally:
                store.close()

    def test_fault_recovery_is_terminal_or_resumable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            store = SQLiteStore(root / "jobs.sqlite", allowed_root=root).initialize()
            try:
                repository = SQLiteRepository(store)
                manifest = {"market:QQQ:2024-01-03": hashlib.sha256(b"m15-qqq").hexdigest()}
                request = JobRequest(
                    idempotency_key="m15-restart",
                    strategy_version="v10_preserve_shock_recovery",
                    data_version="m15-versioned-fixture/v1",
                    signal_date="2024-01-03",
                    execution_date="2024-01-04",
                    as_of="2024-01-03T22:00:00Z",
                    input_manifest=manifest,
                )
                calls: list[str] = []
                fail_once = {"value": True}

                def stage(name: str):
                    def callback(context):
                        calls.append(name)
                        if name == "calculate" and fail_once["value"]:
                            fail_once["value"] = False
                            raise KeyboardInterrupt("m15 simulated interruption")
                        return StageResult(name, "OK", context.request.input_manifest, {"stage": name})

                    return callback

                stages = JobStages(
                    refresh=stage("refresh"),
                    calculate=stage("calculate"),
                    simulate=stage("simulate"),
                    publish=stage("publish"),
                )
                with self.assertRaises(KeyboardInterrupt):
                    JobOrchestrator(repository, stages, run_id_factory=lambda: "m15-run").run(request)
                running = repository.list("run", limit=20)[-1]
                self.assertEqual(running.payload["state"], "running")
                self.assertEqual(running.payload["stage"], "calculate")

                resumed = JobOrchestrator(repository, stages, run_id_factory=lambda: "never-used").run(request)
                self.assertEqual(resumed.status, "published")
                self.assertEqual(calls, ["refresh", "calculate", "calculate", "simulate", "publish"])
                replay = JobOrchestrator(repository, stages, run_id_factory=lambda: "also-never-used").run(request)
                self.assertTrue(replay.idempotent)
                self.assertEqual(replay.run_id, resumed.run_id)
            finally:
                store.close()

    def test_release_manifest_is_pinned_sanitized_and_has_rollback_ref(self):
        text = RELEASE_DOC.read_text(encoding="utf-8")
        for marker in (
            "v10_preserve_shock_recovery",
            "versioned-local-snapshots",
            "daily-close-manual-refresh-next-session-paper",
            "localhost-or-private-network",
            "paper_only",
            "verification-baseline-v3.20",
            "candidate_sha",
            "disclaimer",
            "20 个连续有效交易日",
        ):
            self.assertIn(marker, text)
        for pattern in (
            r"-----BEGIN [A-Z ]+ KEY-----",
            r"AKIA[0-9A-Z]{16}",
            r"(?:ghp|sk_live|xoxb)-[A-Za-z0-9_-]{12,}",
            r"(?i)(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*['\"][^<>{}]+",
        ):
            self.assertIsNone(re.search(pattern, text))

        rollback = subprocess.run(
            ["git", "rev-parse", "verification-baseline-v3.20^{commit}"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertEqual(len(rollback.stdout.strip()), 40)
        self.assertNotEqual(rollback.stdout.strip(), subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip())

    def test_release_e2e_surface_has_no_external_network_or_trading_import(self):
        source = Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        forbidden = {"requests", "urllib", "httpx", "socket", "alpaca", "boto3", "broker"}
        self.assertTrue(imported.isdisjoint(forbidden), sorted(imported & forbidden))
        self.assertNotIn("POST /api/paper/confirm", RELEASE_DOC.read_text(encoding="utf-8"))

    def test_bad_quality_cannot_enter_paper_shadow(self):
        calendar = TradingCalendar()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            store = SQLiteStore(root / "quality.sqlite", allowed_root=root).initialize()
            try:
                repository = SQLiteRepository(store)
                _, _, target = self._base_target(calendar)
                bad_target = replace(target, data_quality="STALE", state="needs_review")
                service = PaperPortfolioService(
                    repository,
                    config=PaperExecutionConfig(price_basis="unadjusted_ohlcv"),
                    calendar=calendar,
                )
                with self.assertRaises(PaperInputError):
                    service.simulate_day(
                        PaperDayInput(
                            portfolio_id="m15-quality",
                            run_id="m15-quality-run",
                            target=bad_target,
                            prices=self._prices(bad_target.execution_date),
                        )
                    )
                self.assertEqual(repository.count("portfolio_snapshot"), 0)
                self.assertEqual(repository.count("ledger_event"), 0)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
