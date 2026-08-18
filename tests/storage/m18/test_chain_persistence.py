from __future__ import annotations

import unittest

from src.jobs.m18.full_chain import M18FullChainPipeline, M18PipelineRequest
from src.storage.m18.chain_persistence import M18ChainPersistence
from src.storage.m18.read_model_store import M18ReadModelStore
from src.storage.sqlite_store import SQLiteRepository, SQLiteStore
from src.storage.normalization import TradingCalendar
from tests.jobs.m18.test_full_chain import M18FullChainPipelineTests


class M18ChainPersistenceTests(unittest.TestCase):
    def test_persists_m02_to_m10_and_is_idempotent(self) -> None:
        fixture = M18FullChainPipelineTests("test_pipeline_calls_m02_to_m07_and_publishes_only_confirmed_output")
        raw = fixture._snapshots()
        output = M18FullChainPipeline(calendar=TradingCalendar()).run(
            M18PipelineRequest(
                run_id="m18-persisted",
                as_of="2020-08-19T20:00:00Z",
                data_version="m18-fixture-v1",
                raw_snapshots=raw,
            )
        )
        repository = SQLiteRepository(SQLiteStore(":memory:").initialize()).initialize()
        persistence = M18ChainPersistence(repository)
        first = persistence.persist(output, raw, orchestration_run_id="m11-run-1")
        second = persistence.persist(output, raw, orchestration_run_id="m11-run-1")

        self.assertEqual(first.snapshot.confirmed_strategy.status, "READY")
        self.assertEqual(first.snapshot.modules[8].status, "READY")  # M08
        self.assertEqual(first.snapshot.modules[10].publication, "CONFIRMED")  # M10
        self.assertEqual(first.snapshot.content_hash, second.snapshot.content_hash)
        self.assertEqual(M18ReadModelStore(repository).latest().run_id, "m18-persisted")
        self.assertGreater(repository.count("market_snapshot"), 0)
        self.assertGreater(repository.count("indicator_snapshot"), 0)
        self.assertGreater(repository.count("regime_snapshot"), 0)
        self.assertGreater(repository.count("target_weight_snapshot"), 0)


if __name__ == "__main__":
    unittest.main()
