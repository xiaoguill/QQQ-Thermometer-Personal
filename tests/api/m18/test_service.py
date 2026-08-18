from __future__ import annotations

import unittest

from src.api.m18.service import M18ApiService
from src.jobs.m18.full_chain import M18FullChainPipeline, M18PipelineRequest
from src.storage.m18.chain_persistence import M18ChainPersistence
from src.storage.normalization import TradingCalendar
from src.storage.sqlite_store import SQLiteRepository, SQLiteStore
from tests.jobs.m18.test_full_chain import M18FullChainPipelineTests


class M18ApiServiceTests(unittest.TestCase):
    def test_empty_workbench_is_typed_and_fail_closed(self) -> None:
        repository = SQLiteRepository(SQLiteStore(":memory:").initialize()).initialize()
        service = M18ApiService(repository)
        response = service.handle("GET", "/api/m18/workbench")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body["meta"]["data_quality"], "UNAVAILABLE")
        self.assertEqual(len(response.body["data"]["modules"]), 18)
        self.assertEqual(response.body["data"]["confirmed_strategy"]["target_weights"], {})

    def test_workbench_reads_persisted_confirmed_and_compatibility_data(self) -> None:
        fixture = M18FullChainPipelineTests("test_pipeline_calls_m02_to_m07_and_publishes_only_confirmed_output")
        raw = fixture._snapshots()
        calendar = TradingCalendar()
        output = M18FullChainPipeline(calendar=calendar).run(
            M18PipelineRequest(
                run_id="m18-api-run",
                as_of="2020-08-19T20:00:00Z",
                data_version="m18-fixture-v1",
                raw_snapshots=raw,
            )
        )
        repository = SQLiteRepository(SQLiteStore(":memory:").initialize()).initialize()
        M18ChainPersistence(repository).persist(output, raw, orchestration_run_id="m11-api-run")
        service = M18ApiService(repository)

        workbench = service.handle("GET", "/api/m18/workbench")
        modules = service.handle("GET", "/api/m18/modules")
        compatibility = service.handle("GET", "/api/thermometer/latest")
        self.assertEqual(workbench.status_code, 200)
        self.assertEqual(workbench.body["data"]["confirmed_strategy"]["status"], "READY")
        self.assertAlmostEqual(sum(workbench.body["data"]["target_weights"].values()), 1.0)
        self.assertEqual(len(modules.body["data"]["modules"]), 18)
        self.assertEqual(compatibility.status_code, 200)

    def test_external_host_is_rejected(self) -> None:
        repository = SQLiteRepository(SQLiteStore(":memory:").initialize()).initialize()
        service = M18ApiService(repository)
        response = service.handle("GET", "/api/m18/workbench", client_host="192.0.2.1")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.body["error"]["code"], "FORBIDDEN")


if __name__ == "__main__":
    unittest.main()
