import hashlib
import tempfile
import unittest
from pathlib import Path

from src.storage import (
    PaperDayInput,
    PaperExecutionConfig,
    PaperInputError,
    PaperPortfolioService,
    PaperPrice,
    SQLiteRepository,
    SQLiteStore,
    StorageConflictError,
)
from src.storage.normalization import TradingCalendar
from src.thermometer.policy import CANDIDATE_POLICY_PROFILE_ID
from src.thermometer.regime import STRATEGY_VERSION
from src.thermometer.target_weights import (
    TARGET_WEIGHT_IMPLEMENTATION_VERSION,
    TARGET_WEIGHT_SCHEMA,
    WEIGHT_STATUS_CANDIDATE_ONLY,
    TargetWeightSnapshot,
)


class PaperPortfolioIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = SQLiteStore(self.root / "paper.sqlite", allowed_root=self.root).initialize()
        self.repository = SQLiteRepository(self.store)
        self.calendar = TradingCalendar()
        self.config = PaperExecutionConfig(
            initial_cash=100_000.0,
            cost_bps=5.0,
            slippage_bps=10.0,
            price_basis="unadjusted_ohlcv",
            allow_fractional_shares=True,
        )
        self.service = PaperPortfolioService(self.repository, config=self.config, calendar=self.calendar)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def _target(self, signal_date: str, execution_date: str, *, qqq_weight: float = 0.6, quality: str = "OK") -> TargetWeightSnapshot:
        return TargetWeightSnapshot(
            schema=TARGET_WEIGHT_SCHEMA,
            implementation_version=TARGET_WEIGHT_IMPLEMENTATION_VERSION,
            strategy_version=STRATEGY_VERSION,
            strategy_config_hash="a" * 64,
            strategy_status="research_candidate",
            strategy_implementation_state="contract_only",
            product_default_strategy_version=None,
            weight_status=WEIGHT_STATUS_CANDIDATE_ONLY,
            candidate_only=True,
            execution_eligible=False,
            profile_id=CANDIDATE_POLICY_PROFILE_ID,
            signal_date=signal_date,
            execution_date=execution_date,
            as_of=f"{signal_date}T22:00:00Z",
            calendar_id=self.calendar.calendar_id,
            data_quality=quality,
            state="normal" if quality == "OK" else "needs_review",
            previous_state=None,
            medium_gate_streak=5,
            target_weights={
                "QQQ": qqq_weight,
                "QLD": 0.0,
                "XLU": 0.0,
                "IAU": 0.0,
                "TLT": 0.0,
                "BIL": 1.0 - qqq_weight,
                "VXX": 0.0,
            },
            change_reason_codes=("paper_fixture",),
            regime_reason_codes=("normal_confirmed",),
            regime_snapshot_hash=hashlib.sha256(signal_date.encode()).hexdigest(),
        )

    def _prices(
        self,
        execution_date: str,
        *,
        qqq: float = 100.0,
        bil: float = 100.0,
        qqq_quality: str = "OK",
        qqq_dividend: float = 0.0,
        qqq_split: float = 1.0,
        bil_dividend: float = 0.0,
    ) -> tuple[PaperPrice, ...]:
        return (
            PaperPrice(
                "BIL",
                execution_date,
                bil,
                "unadjusted_ohlcv",
                dividend_per_share=bil_dividend,
            ),
            PaperPrice(
                "QQQ",
                execution_date,
                qqq,
                "unadjusted_ohlcv",
                quality=qqq_quality,
                dividend_per_share=qqq_dividend,
                split_factor=qqq_split,
            ),
        )

    def _request(
        self,
        signal_date: str,
        execution_date: str,
        *,
        qqq_weight: float = 0.6,
        qqq: float = 100.0,
        bil: float = 100.0,
        qqq_quality: str = "OK",
        qqq_dividend: float = 0.0,
        qqq_split: float = 1.0,
        bil_dividend: float = 0.0,
    ) -> PaperDayInput:
        return PaperDayInput(
            portfolio_id="personal-paper",
            run_id=f"run-{execution_date}",
            target=self._target(signal_date, execution_date, qqq_weight=qqq_weight),
            prices=self._prices(
                execution_date,
                qqq=qqq,
                bil=bil,
                qqq_quality=qqq_quality,
                qqq_dividend=qqq_dividend,
                qqq_split=qqq_split,
                bil_dividend=bil_dividend,
            ),
        )

    def test_first_day_records_cash_trades_nav_and_reconciliation(self):
        result = self.service.simulate_day(self._request("2024-01-03", "2024-01-04"))
        self.assertFalse(result.idempotent)
        self.assertEqual(result.state.status, "PAPER_SHADOW")
        self.assertGreater(result.state.nav, 0.0)
        self.assertGreaterEqual(result.state.cash, 0.0)
        self.assertGreater(len(result.ledger_events), 1)
        self.assertEqual(result.reconciliation.status, "RECONCILED")
        self.assertLessEqual(abs(result.reconciliation.identity_error), 1e-7 * result.reconciliation.nav_before)
        self.assertEqual(self.repository.count("portfolio_snapshot"), 1)
        self.assertEqual(self.repository.count("run"), 1)

    def test_same_day_replay_is_idempotent_and_conflicting_input_is_rejected(self):
        request = self._request("2024-01-03", "2024-01-04")
        first = self.service.simulate_day(request)
        before_ledger = self.repository.count("ledger_event")
        replay = self.service.simulate_day(request)
        self.assertTrue(replay.idempotent)
        self.assertEqual(replay.state.as_dict(), first.state.as_dict())
        self.assertEqual(self.repository.count("ledger_event"), before_ledger)

        conflicting = self._request("2024-01-03", "2024-01-04", qqq=101.0)
        with self.assertRaises(StorageConflictError):
            self.service.simulate_day(conflicting)
        self.assertEqual(self.repository.count("portfolio_snapshot"), 1)

    def test_restart_recovers_previous_positions_and_rejects_backdating(self):
        first = self.service.simulate_day(self._request("2024-01-03", "2024-01-04"))
        self.store.close()
        self.store = SQLiteStore(self.root / "paper.sqlite", allowed_root=self.root).initialize()
        self.repository = SQLiteRepository(self.store)
        self.service = PaperPortfolioService(self.repository, config=self.config, calendar=self.calendar)
        second = self.service.simulate_day(self._request("2024-01-04", "2024-01-05", qqq=102.0, bil=100.1))
        self.assertEqual(second.state.execution_date, "2024-01-05")
        self.assertGreaterEqual(second.state.fees_paid, first.state.fees_paid)
        with self.assertRaises(PaperInputError):
            self.service.simulate_day(self._request("2024-01-02", "2024-01-03"))

    def test_twenty_trading_session_paper_shadow_is_reconcilable(self):
        sessions = self.calendar.sessions("2024-01-03", "2024-02-15")[:21]
        self.assertEqual(len(sessions), 21)
        results = []
        for index in range(20):
            results.append(
                self.service.simulate_day(
                    self._request(
                        sessions[index],
                        sessions[index + 1],
                        qqq_weight=0.6 if index % 2 == 0 else 0.7,
                        qqq=100.0 + index,
                        bil=100.0 + index * 0.01,
                    )
                )
            )
        self.assertEqual(len(results), 20)
        self.assertEqual(self.repository.count("portfolio_snapshot"), 20)
        self.assertEqual(self.repository.count("run"), 20)
        self.assertTrue(all(item.reconciliation.status == "RECONCILED" for item in results))
        self.assertTrue(all(item.state.data_quality == "OK" for item in results))
        self.assertTrue(all(item.state.cash >= -1e-8 for item in results))

    def test_unadjusted_dividend_and_split_are_explicitly_ledgered(self):
        self.service.simulate_day(self._request("2024-01-03", "2024-01-04"))
        result = self.service.simulate_day(
            self._request(
                "2024-01-04",
                "2024-01-05",
                qqq=52.0,
                bil=100.01,
                qqq_split=2.0,
                bil_dividend=1.0,
            )
        )
        event_types = [record.metadata["event_type"] for record in self.repository.list("ledger_event", limit=100)]
        self.assertIn("SPLIT_ADJUSTMENT", event_types)
        self.assertIn("DIVIDEND", event_types)
        self.assertEqual(result.reconciliation.status, "RECONCILED")

    def test_quality_and_price_basis_fail_closed(self):
        with self.assertRaises(PaperInputError):
            self.service.simulate_day(self._request("2024-01-03", "2024-01-04", qqq_quality="STALE"))
        with self.assertRaises(PaperInputError):
            PaperPrice("QQQ", "2024-01-04", 100.0, "adjusted_ohlcv", dividend_per_share=1.0)
        with self.assertRaises(PaperInputError):
            self.service.simulate_day(self._request("2024-01-03", "2024-01-05"))
        self.assertEqual(self.repository.count("portfolio_snapshot"), 0)

    def test_target_snapshot_is_required_instead_of_arbitrary_weights(self):
        with self.assertRaises(PaperInputError):
            PaperDayInput(
                portfolio_id="personal-paper",
                run_id="run-1",
                target={"QQQ": 1.0},
                prices=self._prices("2024-01-04"),
            )

    def test_manual_skip_is_ledgered_without_creating_execution_or_changing_strategy(self):
        request = self._request(
            "2024-01-03",
            "2024-01-04",
            qqq_quality="STALE",
        )
        event = self.service.record_manual_skip(request, "price quality requires review")
        self.assertEqual(event.metadata["event_type"], "MANUAL_SKIP")
        self.assertFalse(event.payload["strategy_output_changed"])
        self.assertFalse(event.payload["portfolio_changed"])
        self.assertEqual(self.repository.count("portfolio_snapshot"), 0)
        replay = self.service.record_manual_skip(request, "price quality requires review")
        self.assertEqual(replay.content_hash, event.content_hash)
        self.assertEqual(self.repository.count("ledger_event"), 1)


if __name__ == "__main__":
    unittest.main()
