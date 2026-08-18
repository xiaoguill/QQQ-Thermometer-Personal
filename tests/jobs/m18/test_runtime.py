from __future__ import annotations

import unittest

from src.jobs.m18.full_chain import M18PipelineRequest
from src.jobs.m18.runtime import M18RuntimeService
from src.storage.sqlite_store import SQLiteRepository, SQLiteStore
from src.storage.normalization import TradingCalendar
from tests.jobs.m18.test_full_chain import M18FullChainPipelineTests


class M18RuntimeTests(unittest.TestCase):
    def test_m11_run_publishes_full_chain_and_is_idempotent(self) -> None:
        fixture = M18FullChainPipelineTests("test_pipeline_calls_m02_to_m07_and_publishes_only_confirmed_output")
        raw = fixture._snapshots()
        repository = SQLiteRepository(SQLiteStore(":memory:").initialize()).initialize()
        service = M18RuntimeService(repository, calendar=TradingCalendar())
        request = M18PipelineRequest(
            run_id="m18-runtime-request",
            as_of="2020-08-19T20:00:00Z",
            data_version="m18-fixture-v1",
            raw_snapshots=raw,
        )

        first = service.run(request)
        second = service.run(request)

        self.assertEqual(first.job.status, "published")
        self.assertFalse(first.job.idempotent)
        self.assertTrue(second.job.idempotent)
        self.assertIsNotNone(first.snapshot)
        self.assertEqual(first.snapshot.modules[8].status, "READY")  # M08
        self.assertEqual(first.snapshot.modules[10].publication, "CONFIRMED")  # M10
        self.assertEqual(first.snapshot.modules[11].status, "READY")  # M11
        self.assertEqual(first.paper_plan.status, "NEEDS_REVIEW")  # execution-day prices are not in this fixture
        self.assertEqual(first.snapshot.paper_plan.status, "NEEDS_REVIEW")


if __name__ == "__main__":
    unittest.main()
