import hashlib
import tempfile
import threading
import unittest
from pathlib import Path

from src.jobs import (
    JOB_RUN_SCHEMA,
    JobConflictError,
    JobRequest,
    JobStages,
    JobValidationError,
    JobOrchestrator,
    StageResult,
)
from src.storage.sqlite_store import SQLiteRepository, SQLiteStore


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class JobOrchestratorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = SQLiteStore(self.root / "jobs.sqlite", allowed_root=self.root).initialize()
        self.repository = SQLiteRepository(self.store)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def _request(self, key: str = "job-2024-01-03", **overrides) -> JobRequest:
        values = {
            "idempotency_key": key,
            "strategy_version": "v10-preserve-shock-recovery",
            "data_version": "alpaca-daily/v1",
            "signal_date": "2024-01-03",
            "execution_date": "2024-01-04",
            "as_of": "2024-01-03T22:00:00Z",
            "input_manifest": {"market:QQQ:2024-01-03": _digest("qqq-2024-01-03")},
        }
        values.update(overrides)
        return JobRequest(**values)

    def _stages(self, calls, *, quality_by_stage=None, snapshots=None):
        quality_by_stage = quality_by_stage or {}
        snapshots = snapshots or {}

        def make(stage):
            def callback(context):
                calls.append((stage, context.run_id, tuple(item.stage for item in context.completed)))
                return StageResult(
                    stage=stage,
                    quality=quality_by_stage.get(stage, "OK"),
                    manifest=context.request.input_manifest,
                    snapshot=snapshots.get(stage, {"stage": stage, "signal_date": context.request.signal_date}),
                )

            return callback

        return JobStages(
            refresh=make("refresh"),
            calculate=make("calculate"),
            simulate=make("simulate"),
            publish=make("publish"),
        )

    def test_pipeline_order_persists_versioned_run_states_and_publishes(self):
        calls = []
        runner = JobOrchestrator(
            self.repository,
            self._stages(calls, snapshots={"publish": {"nav": 101.25, "published": True}}),
            run_id_factory=lambda: "run-m11-001",
        )

        result = runner.run(self._request())

        self.assertEqual(result.schema, JOB_RUN_SCHEMA)
        self.assertEqual(result.status, "published")
        self.assertEqual(result.run_id, "run-m11-001")
        self.assertFalse(result.idempotent)
        self.assertEqual([item[0] for item in calls], ["refresh", "calculate", "simulate", "publish"])
        self.assertEqual(
            [item.state for item in result.transitions],
            ["scheduled", "running", "data_ready", "running", "signal_ready", "running", "simulated", "running", "published"],
        )
        self.assertEqual(result.published_snapshot["nav"], 101.25)
        self.assertEqual(self.repository.count("run"), 9)
        self.assertEqual(self.repository.count("quality_event"), 0)
        stored = self.repository.list("run", limit=20)
        self.assertTrue(all(item.payload["strategy_version"] == "v10-preserve-shock-recovery" for item in stored))
        self.assertTrue(all(item.payload["input_manifest"] for item in stored))

    def test_same_idempotency_replay_is_read_only_and_conflicting_input_is_rejected(self):
        calls = []
        runner = JobOrchestrator(self.repository, self._stages(calls), run_id_factory=lambda: "run-m11-replay")
        request = self._request()

        first = runner.run(request)
        before_count = self.repository.count("run")
        replay = runner.run(request)

        self.assertTrue(replay.idempotent)
        self.assertEqual(replay.run_id, first.run_id)
        self.assertEqual(replay.status, "published")
        self.assertEqual(len(calls), 4)
        self.assertEqual(self.repository.count("run"), before_count)
        with self.assertRaises(JobConflictError):
            runner.run(self._request(data_version="alpaca-daily/v2"))

    def test_quality_failure_is_visible_and_never_calls_publish(self):
        calls = []
        runner = JobOrchestrator(
            self.repository,
            self._stages(calls, quality_by_stage={"calculate": "STALE"}),
            run_id_factory=lambda: "run-m11-stale",
        )

        result = runner.run(self._request())

        self.assertEqual(result.status, "stale")
        self.assertEqual([item[0] for item in calls], ["refresh", "calculate"])
        self.assertIsNone(result.published_snapshot)
        self.assertNotIn("published", [item.state for item in result.transitions])
        self.assertEqual(self.repository.count("quality_event"), 1)
        quality = self.repository.list("quality_event", limit=5)[0]
        self.assertEqual(quality.payload["run_id"], result.run_id)
        self.assertEqual(quality.metadata["status"], "STALE")

    def test_restart_resumes_current_stage_without_duplicate_transition(self):
        calls = []
        fail_once = {"value": True}

        def calculate(context):
            calls.append("calculate")
            if fail_once["value"]:
                fail_once["value"] = False
                raise KeyboardInterrupt("simulated process stop")
            return StageResult("calculate", "OK", context.request.input_manifest, {"stage": "calculate"})

        stages = JobStages(
            refresh=lambda context: (calls.append("refresh") or StageResult("refresh", "OK", context.request.input_manifest, {})),
            calculate=calculate,
            simulate=lambda context: (calls.append("simulate") or StageResult("simulate", "OK", context.request.input_manifest, {})),
            publish=lambda context: (calls.append("publish") or StageResult("publish", "OK", context.request.input_manifest, {"published": True})),
        )
        request = self._request(key="job-restart")
        first_runner = JobOrchestrator(self.repository, stages, run_id_factory=lambda: "run-m11-restart-1")
        with self.assertRaises(KeyboardInterrupt):
            first_runner.run(request)

        last = self.repository.list("run", limit=20)[-1]
        self.assertEqual(last.payload["state"], "running")
        self.assertEqual(last.payload["stage"], "calculate")

        second_runner = JobOrchestrator(self.repository, stages, run_id_factory=lambda: "run-m11-never-used")
        result = second_runner.run(request)

        self.assertEqual(result.status, "published")
        self.assertEqual(result.run_id, "run-m11-restart-1")
        self.assertEqual(calls, ["refresh", "calculate", "calculate", "simulate", "publish"])
        running_calculate = [item for item in result.transitions if item.stage == "calculate" and item.state == "running"]
        self.assertEqual(len(running_calculate), 1)

    def test_timeout_and_partial_failure_are_terminal_and_not_published(self):
        calls = []
        ticks = iter((0.0, 0.0, 0.0, 2.0))
        runner = JobOrchestrator(
            self.repository,
            self._stages(calls),
            run_id_factory=lambda: "run-m11-timeout",
            monotonic_clock=lambda: next(ticks),
        )

        timeout_result = runner.run(self._request(key="job-timeout", timeout_seconds=1.0))
        self.assertEqual(timeout_result.status, "failed")
        self.assertIsNone(timeout_result.published_snapshot)
        self.assertEqual([item[0] for item in calls], ["refresh"])

        partial_calls = []
        partial_runner = JobOrchestrator(
            self.repository,
            self._stages(partial_calls, quality_by_stage={"simulate": "PARTIAL"}),
            run_id_factory=lambda: "run-m11-partial",
        )
        partial_result = partial_runner.run(self._request(key="job-partial"))
        self.assertEqual(partial_result.status, "partial")
        self.assertEqual([item[0] for item in partial_calls], ["refresh", "calculate", "simulate"])
        self.assertIsNone(partial_result.published_snapshot)

    def test_concurrent_duplicate_requests_share_one_execution(self):
        calls = []
        second_result = []
        second_started = threading.Event()
        second_thread = []

        def refresh(context):
            calls.append("refresh")

            def replay():
                second_started.set()
                second_result.append(runner.run(request))

            thread = threading.Thread(target=replay)
            second_thread.append(thread)
            thread.start()
            self.assertTrue(second_started.wait(1.0))
            return StageResult("refresh", "OK", context.request.input_manifest, {})

        stages = JobStages(
            refresh=refresh,
            calculate=lambda context: (calls.append("calculate") or StageResult("calculate", "OK", context.request.input_manifest, {})),
            simulate=lambda context: (calls.append("simulate") or StageResult("simulate", "OK", context.request.input_manifest, {})),
            publish=lambda context: (calls.append("publish") or StageResult("publish", "OK", context.request.input_manifest, {"published": True})),
        )
        request = self._request(key="job-concurrent")
        runner = JobOrchestrator(self.repository, stages, run_id_factory=lambda: "run-m11-concurrent")
        result = runner.run(request)
        second_thread[0].join(2.0)

        self.assertEqual(result.status, "published")
        self.assertFalse(second_thread[0].is_alive())
        self.assertEqual(second_result[0].run_id, result.run_id)
        self.assertTrue(second_result[0].idempotent)
        self.assertEqual(calls, ["refresh", "calculate", "simulate", "publish"])

    def test_request_validation_is_bounded_and_requires_manifest_hashes(self):
        with self.assertRaises(JobValidationError):
            self._request(input_manifest={})
        with self.assertRaises(JobValidationError):
            self._request(input_manifest={"market": "not-a-sha256"})
        with self.assertRaises(JobValidationError):
            self._request(signal_date="2024-01-04", execution_date="2024-01-04")
        with self.assertRaises(JobValidationError):
            self._request(timeout_seconds=0)


if __name__ == "__main__":
    unittest.main()
