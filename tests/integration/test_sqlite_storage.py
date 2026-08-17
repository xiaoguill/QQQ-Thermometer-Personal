import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.storage import (
    INDICATOR_NAMES,
    INDICATOR_VERSION,
    NormalizedBar,
    SQLiteRepository,
    SQLiteStore,
    StorageConflictError,
    StorageImmutableError,
    StorageSchemaError,
    StorageValidationError,
)
from src.thermometer.regime import (
    REGIME_IMPLEMENTATION_VERSION,
    STRATEGY_VERSION,
    RegimeEvidence,
    RegimeSnapshot,
)
from src.thermometer.policy import CANDIDATE_POLICY_PROFILE_ID
from src.thermometer.target_weights import (
    TARGET_WEIGHT_IMPLEMENTATION_VERSION,
    TARGET_WEIGHT_SCHEMA,
    WEIGHT_STATUS_CANDIDATE_ONLY,
    TargetWeightSnapshot,
)
from src.storage.indicators import IndicatorSnapshot


class SQLiteStorageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "runtime" / "thermometer.sqlite"
        self.store = SQLiteStore(self.db_path, allowed_root=self.root).initialize()
        self.repository = SQLiteRepository(self.store)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_empty_store_initializes_idempotently_and_exposes_schema_manifest(self):
        self.assertEqual(self.store.schema_version, 1)
        self.store.initialize()
        manifest = self.store.schema_manifest()
        table_names = {item["name"] for item in manifest["tables"]}
        self.assertEqual(manifest["schema"], "qqq-storage-schema/v1")
        self.assertEqual(manifest["schema_version"], 1)
        self.assertIn("market_snapshots", table_names)
        self.assertIn("indicator_snapshots", table_names)
        self.assertIn("regime_snapshots", table_names)
        self.assertIn("target_weight_snapshots", table_names)
        self.assertIn("ledger_events", table_names)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 1)

    def test_constructor_does_not_create_or_migrate_database(self):
        path = self.root / "not-yet-created.sqlite"
        store = SQLiteStore(path, allowed_root=self.root)
        self.assertFalse(path.exists())
        store.close()
        self.assertFalse(path.exists())

    def test_unknown_future_schema_fails_closed(self):
        path = self.root / "future.sqlite"
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA user_version = 99")
        connection.commit()
        connection.close()
        store = SQLiteStore(path, allowed_root=self.root)
        with self.assertRaises(StorageSchemaError):
            store.initialize()
        store.close()

    def test_same_key_is_idempotent_but_conflicting_content_is_rejected(self):
        payload = {"schema": "run/v1", "status": "SUCCEEDED", "sql_like": "'); DROP TABLE run_records; --"}
        first = self.repository.put_run("run-2024-01-03", payload, run_type="replay", status="SUCCEEDED")
        second = self.repository.put_run("run-2024-01-03", payload, run_type="replay", status="SUCCEEDED")
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(self.repository.count("run"), 1)
        with self.assertRaises(StorageConflictError):
            self.repository.put_run(
                "run-2024-01-03",
                {"schema": "run/v1", "status": "FAILED"},
                run_type="replay",
                status="FAILED",
            )
        self.assertEqual(self.repository.count("run"), 1)
        self.assertIsNotNone(self.repository.get("run", "run-2024-01-03"))

    def test_append_only_methods_reject_mutation(self):
        with self.assertRaises(StorageImmutableError):
            self.repository.delete("run", "run-1")
        with self.assertRaises(StorageImmutableError):
            self.repository.update("run", "run-1", {"status": "FAILED"})

    def test_transaction_rolls_back_all_records_after_later_failure(self):
        with self.assertRaises(StorageValidationError):
            with self.repository.transaction():
                self.repository.put_run("run-before-failure", {"schema": "run/v1"}, run_type="replay", status="RUNNING")
                self.repository.put_run("run-after-failure", {"api_key": "must-not-be-stored"}, run_type="replay", status="RUNNING")
        self.assertEqual(self.repository.count("run"), 0)

    def test_sensitive_payloads_and_invalid_dates_are_rejected(self):
        with self.assertRaises(StorageValidationError):
            self.repository.put_quality_event(
                "quality-secret",
                {"schema": "quality/v1", "nested": {"private_key": "redacted"}},
                event_date="2024-01-03",
                source="fixture",
                severity="ERROR",
                status="FAILED",
            )
        with self.assertRaises(StorageValidationError):
            self.repository.put_market_snapshot(
                "bad-date",
                {"schema": "market/v1"},
                snapshot_kind="normalized",
                symbol="QQQ",
                bar_date="2024-99-99",
                as_of="2024-01-03T22:00:00Z",
                source="fixture",
                price_basis="adjusted_ohlcv",
                quality="OK",
            )
        with self.assertRaises(StorageValidationError):
            SQLiteStore(self.root / "outside.sqlite", allowed_root=self.root / "different-root")

    def test_ledger_idempotency_key_is_append_only_across_event_keys(self):
        payload = {"schema": "paper-ledger/v1", "event_type": "TARGET_CONFIRMED", "quantity": 10}
        first = self.repository.put_ledger_event(
            "event-a",
            payload,
            event_date="2024-01-03",
            event_type="TARGET_CONFIRMED",
            idempotency_key="same-operation",
            status="RECORDED",
            quantity=10,
            price=100,
            cost=0.1,
        )
        second = self.repository.put_ledger_event(
            "event-b",
            payload,
            event_date="2024-01-03",
            event_type="TARGET_CONFIRMED",
            idempotency_key="same-operation",
            status="RECORDED",
            quantity=10,
            price=100,
            cost=0.1,
        )
        self.assertEqual(first.record_key, second.record_key)
        self.assertEqual(self.repository.count("ledger_event"), 1)
        with self.assertRaises(StorageConflictError):
            self.repository.put_ledger_event(
                "event-c",
                {"schema": "paper-ledger/v1", "event_type": "TARGET_CONFIRMED", "quantity": 11},
                event_date="2024-01-03",
                event_type="TARGET_CONFIRMED",
                idempotency_key="same-operation",
                status="RECORDED",
                quantity=11,
                price=100,
                cost=0.1,
            )

    def test_domain_snapshots_round_trip_without_recalculation(self):
        bar = NormalizedBar(
            symbol="QQQ",
            bar_date="2024-01-03",
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000.0,
            sources=("fixture",),
            snapshot_ids=("raw-2024-01-03",),
            retrieved_at_by_source=(("fixture", "2024-01-03T22:00:00Z"),),
            price_basis="adjusted_ohlcv",
            timezone="America/New_York",
            quality="OK",
        )
        indicator = IndicatorSnapshot(
            signal_date="2024-01-03",
            as_of="2024-01-03T22:00:00Z",
            calendar_id="nyse-v1",
            indicator_version=INDICATOR_VERSION,
            quality="OK",
            ready=True,
            values={name: 1.0 for name in INDICATOR_NAMES},
            input_bar_dates={"QQQ": ("2024-01-03",), "VIX": ("2024-01-03",), "VIX3M": ("2024-01-03",)},
            price_basis_by_symbol={"QQQ": "adjusted_ohlcv", "VIX": "index_level", "VIX3M": "index_level"},
            timezone_by_symbol={"QQQ": "America/New_York", "VIX": "America/New_York", "VIX3M": "America/New_York"},
        )
        regime = RegimeSnapshot(
            strategy_version=STRATEGY_VERSION,
            strategy_config_hash="a" * 64,
            regime_version=REGIME_IMPLEMENTATION_VERSION,
            signal_date="2024-01-03",
            execution_date="2024-01-04",
            as_of="2024-01-03T22:00:00Z",
            calendar_id="nyse-v1",
            indicator_version=INDICATOR_VERSION,
            indicator_quality="OK",
            indicator_ready=True,
            qqq_bar_quality="OK",
            state="normal",
            previous_state=None,
            elapsed_state_sessions=2,
            medium_gate_streak=5,
            transition=False,
            confirmed=True,
            reason_codes=("normal_confirmed",),
            evidence=(RegimeEvidence("medium_gate", True, True, True, "medium gate passed"),),
            indicator_snapshot_hash="b" * 64,
            qqq_snapshot_ids=("raw-2024-01-03",),
            qqq_price_basis="adjusted_ohlcv",
            qqq_timezone="America/New_York",
        )
        target = TargetWeightSnapshot(
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
            signal_date="2024-01-03",
            execution_date="2024-01-04",
            as_of="2024-01-03T22:00:00Z",
            calendar_id="nyse-v1",
            data_quality="OK",
            state="normal",
            previous_state=None,
            medium_gate_streak=5,
            target_weights={"QQQ": 0.6, "QLD": 0.0, "XLU": 0.0, "IAU": 0.0, "TLT": 0.0, "BIL": 0.4, "VXX": 0.0},
            change_reason_codes=("normal_profile",),
            regime_reason_codes=("normal_confirmed",),
            regime_snapshot_hash="c" * 64,
        )
        market_record = self.repository.put_market_snapshot(
            "normalized|QQQ|2024-01-03",
            bar,
            snapshot_kind="normalized",
            symbol="QQQ",
            bar_date="2024-01-03",
            as_of="2024-01-03T22:00:00Z",
            source="fixture",
            price_basis="adjusted_ohlcv",
            quality="OK",
        )
        indicator_record = self.repository.put_indicator_snapshot(
            "indicator|2024-01-03",
            indicator,
            signal_date=indicator.signal_date,
            as_of=indicator.as_of,
            indicator_version=indicator.indicator_version,
            quality=indicator.quality,
        )
        regime_record = self.repository.put_regime_snapshot(
            "regime|2024-01-03",
            regime,
            signal_date=regime.signal_date,
            execution_date=regime.execution_date,
            as_of=regime.as_of,
            strategy_version=regime.strategy_version,
            state=regime.state,
            quality=regime.indicator_quality,
        )
        target_record = self.repository.put_target_weight_snapshot(
            "target|2024-01-03",
            target,
            signal_date=target.signal_date,
            execution_date=target.execution_date,
            as_of=target.as_of,
            strategy_version=target.strategy_version,
            state=target.state,
            weight_status=target.weight_status,
            data_quality=target.data_quality,
        )
        self.assertEqual(market_record.payload, bar.as_dict())
        self.assertEqual(indicator_record.payload, indicator.as_dict())
        self.assertEqual(regime_record.payload, regime.as_dict())
        self.assertEqual(target_record.payload, target.as_dict())
        self.assertEqual(self.repository.count("market_snapshot"), 1)
        self.assertEqual(self.repository.count("indicator_snapshot"), 1)
        self.assertEqual(self.repository.count("regime_snapshot"), 1)
        self.assertEqual(self.repository.count("target_weight_snapshot"), 1)

    def test_backup_can_be_reopened_as_an_independent_read_model(self):
        self.repository.put_quality_event(
            "quality-1",
            {"schema": "quality/v1", "status": "OK", "message": "fixture"},
            event_date="2024-01-03",
            source="fixture",
            severity="INFO",
            status="OK",
        )
        backup_path = self.root / "backup" / "thermometer.sqlite"
        returned_path = self.store.backup_to(backup_path)
        self.assertEqual(returned_path, backup_path.resolve())
        self.store.close()
        restored_store = SQLiteStore(backup_path, allowed_root=self.root).initialize()
        try:
            restored_repository = SQLiteRepository(restored_store)
            restored = restored_repository.get("quality_event", "quality-1")
            self.assertIsNotNone(restored)
            self.assertEqual(restored.payload["message"], "fixture")
        finally:
            restored_store.close()


if __name__ == "__main__":
    unittest.main()
