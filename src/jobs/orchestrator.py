"""Manual, idempotent orchestration for the personal QQQ Thermometer.

M11 owns run coordination only.  The four stage functions are injected by
the caller so this module never fetches data, recalculates indicators,
changes target weights, starts a scheduler, or creates a broker order.
Every durable state is an append-only ``run`` record in the M08 repository.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from src.storage.sqlite_store import SQLiteRepository, StoredRecord


JOB_RUN_SCHEMA = "qqq-job-run/v1"
JOB_QUALITY_SCHEMA = "qqq-job-quality/v1"
JOB_IMPLEMENTATION_VERSION = "m11-job-orchestrator/v1"
JOB_RUN_TYPE = "refresh_calculate_simulate_publish"
STAGE_NAMES = ("refresh", "calculate", "simulate", "publish")
QUALITY_VALUES = ("OK", "STALE", "PARTIAL", "FAILED", "NEEDS_REVIEW")
TERMINAL_STATES = frozenset(("published", "partial", "stale", "failed", "needs_review"))
_STAGE_READY_STATES = {
    "refresh": "data_ready",
    "calculate": "signal_ready",
    "simulate": "simulated",
}
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_MANIFEST_ENTRIES = 128
_MAX_SCAN_RECORDS = 10_000
_MAX_TIMEOUT_SECONDS = 3_600.0
_RUN_LOCK_TIMEOUT_SECONDS = 30.0


class JobError(RuntimeError):
    """Base class for fail-closed orchestration errors."""


class JobValidationError(JobError, ValueError):
    """Raised when a request or stage output violates the M11 contract."""


class JobConflictError(JobError):
    """Raised when an idempotency key is reused for different input."""


class JobNotFoundError(JobError):
    """Raised when a requested run cannot be found."""


class JobConcurrencyError(JobError):
    """Raised when a duplicate request cannot safely wait for its owner."""


class _JobTimeout(JobError):
    """Internal timeout marker; it is persisted as a failed run."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise JobValidationError("value must be finite JSON data") from exc


def _content_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, field_name: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise JobValidationError(f"{field_name} must be a non-empty string")
    result = value.strip()
    if len(result) > maximum:
        raise JobValidationError(f"{field_name} is too long")
    return result


def _iso_date(value: Any, field_name: str) -> str:
    candidate = _text(value, field_name, maximum=32)
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError as exc:
        raise JobValidationError(f"{field_name} must be an ISO date") from exc


def _iso_timestamp(value: Any, field_name: str) -> str:
    candidate = _text(value, field_name, maximum=64).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise JobValidationError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise JobValidationError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise JobValidationError(f"{field_name} must be a JSON object")
    result = copy.deepcopy(dict(value))
    if any(not isinstance(key, str) or not key.strip() for key in result):
        raise JobValidationError(f"{field_name} keys must be non-empty strings")
    _canonical_json(result)
    return result


def _manifest(value: Any, field_name: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise JobValidationError(f"{field_name} must be a non-empty object")
    if len(value) > _MAX_MANIFEST_ENTRIES:
        raise JobValidationError(f"{field_name} has too many entries")
    result: dict[str, str] = {}
    for key, digest in value.items():
        name = _text(key, f"{field_name}.key", maximum=256)
        if not isinstance(digest, str) or not _SHA256_PATTERN.fullmatch(digest):
            raise JobValidationError(f"{field_name}.{name} must be a lowercase SHA256")
        result[name] = digest
    return dict(sorted(result.items()))


def _quality(value: Any) -> str:
    if not isinstance(value, str) or value.upper() not in QUALITY_VALUES:
        raise JobValidationError(f"quality must be one of {QUALITY_VALUES}")
    return value.upper()


def _reason_codes(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise JobValidationError("reason_codes must be a sequence of strings")
    if len(value) > 64:
        raise JobValidationError("reason_codes has too many entries")
    result: list[str] = []
    for item in value:
        code = _text(item, "reason_code", maximum=128)
        if code not in result:
            result.append(code)
    return tuple(result)


@dataclass(frozen=True)
class JobRequest:
    """Immutable input for one manual pipeline run."""

    idempotency_key: str
    strategy_version: str
    data_version: str
    signal_date: str
    execution_date: str
    as_of: str
    input_manifest: Mapping[str, str]
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        key = _text(self.idempotency_key, "idempotency_key")
        strategy = _text(self.strategy_version, "strategy_version")
        data = _text(self.data_version, "data_version")
        signal = _iso_date(self.signal_date, "signal_date")
        execution = _iso_date(self.execution_date, "execution_date")
        if execution <= signal:
            raise JobValidationError("execution_date must be after signal_date")
        as_of = _iso_timestamp(self.as_of, "as_of")
        manifest = _manifest(self.input_manifest, "input_manifest")
        timeout = self.timeout_seconds
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(float(timeout)):
                raise JobValidationError("timeout_seconds must be a finite number")
            timeout = float(timeout)
            if not 0.0 < timeout <= _MAX_TIMEOUT_SECONDS:
                raise JobValidationError(f"timeout_seconds must be between 0 and {_MAX_TIMEOUT_SECONDS}")
        object.__setattr__(self, "idempotency_key", key)
        object.__setattr__(self, "strategy_version", strategy)
        object.__setattr__(self, "data_version", data)
        object.__setattr__(self, "signal_date", signal)
        object.__setattr__(self, "execution_date", execution)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "input_manifest", manifest)
        object.__setattr__(self, "timeout_seconds", timeout)

    def as_dict(self) -> dict[str, Any]:
        return {
            "idempotency_key": self.idempotency_key,
            "strategy_version": self.strategy_version,
            "data_version": self.data_version,
            "signal_date": self.signal_date,
            "execution_date": self.execution_date,
            "as_of": self.as_of,
            "input_manifest": dict(self.input_manifest),
            "timeout_seconds": self.timeout_seconds,
        }

    @property
    def request_hash(self) -> str:
        return _content_hash(self.as_dict())


@dataclass(frozen=True)
class StageResult:
    """Validated output returned by one injected pipeline stage."""

    stage: str
    quality: str
    manifest: Mapping[str, str]
    snapshot: Mapping[str, Any]
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        stage = _text(self.stage, "stage", maximum=32).lower()
        if stage not in STAGE_NAMES:
            raise JobValidationError(f"unsupported stage: {stage}")
        quality = _quality(self.quality)
        manifest = _manifest(self.manifest, "stage manifest")
        snapshot = _json_object(self.snapshot, "snapshot")
        reasons = _reason_codes(self.reason_codes)
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "quality", quality)
        object.__setattr__(self, "manifest", manifest)
        object.__setattr__(self, "snapshot", snapshot)
        object.__setattr__(self, "reason_codes", reasons)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "quality": self.quality,
            "manifest": dict(self.manifest),
            "snapshot": copy.deepcopy(dict(self.snapshot)),
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class StageContext:
    """Read-only context passed to a stage callback."""

    run_id: str
    request: JobRequest
    stage: str
    completed: tuple[StageResult, ...]


StageCallable = Callable[[StageContext], StageResult]


@dataclass(frozen=True)
class JobStages:
    """The only four callbacks an M11 run may execute."""

    refresh: StageCallable
    calculate: StageCallable
    simulate: StageCallable
    publish: StageCallable

    def __post_init__(self) -> None:
        for name in STAGE_NAMES:
            if not callable(getattr(self, name)):
                raise JobValidationError(f"stage callback is not callable: {name}")

    def callback(self, stage: str) -> StageCallable:
        if stage not in STAGE_NAMES:
            raise JobValidationError(f"unsupported stage: {stage}")
        return getattr(self, stage)


@dataclass(frozen=True)
class RunTransition:
    sequence: int
    state: str
    stage: str | None
    quality: str
    started_at: str
    finished_at: str | None
    reason_codes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "state": self.state,
            "stage": self.stage,
            "quality": self.quality,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class JobRunResult:
    schema: str
    run_id: str
    idempotency_key: str
    request_hash: str
    status: str
    transitions: tuple[RunTransition, ...]
    stage_results: tuple[StageResult, ...]
    published_snapshot: Mapping[str, Any] | None
    idempotent: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "implementation_version": JOB_IMPLEMENTATION_VERSION,
            "run_id": self.run_id,
            "idempotency_key": self.idempotency_key,
            "request_hash": self.request_hash,
            "status": self.status,
            "idempotent": self.idempotent,
            "transitions": [item.as_dict() for item in self.transitions],
            "stage_results": [item.as_dict() for item in self.stage_results],
            "published_snapshot": None if self.published_snapshot is None else copy.deepcopy(dict(self.published_snapshot)),
        }


@dataclass(frozen=True)
class _ExistingRun:
    run_id: str
    request_hash: str
    started_at: str
    status: str
    stage: str | None
    sequence: int
    transitions: tuple[RunTransition, ...]
    stage_results: tuple[StageResult, ...]
    published_snapshot: Mapping[str, Any] | None
    last_payload: Mapping[str, Any]

    def result(self, idempotent: bool) -> JobRunResult:
        return JobRunResult(
            schema=JOB_RUN_SCHEMA,
            run_id=self.run_id,
            idempotency_key=str(self.last_payload["idempotency_key"]),
            request_hash=self.request_hash,
            status=self.status,
            transitions=self.transitions,
            stage_results=self.stage_results,
            published_snapshot=self.published_snapshot,
            idempotent=idempotent,
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _new_run_id() -> str:
    return f"run-{uuid.uuid4().hex}"


class _RepositoryRunLock:
    """Bounded local file lock for cross-instance SQLite idempotency."""

    def __init__(self, database_path: str, *, timeout_seconds: float = _RUN_LOCK_TIMEOUT_SECONDS) -> None:
        self._database_path = database_path
        self._timeout_seconds = timeout_seconds
        self._lock_path = None if database_path == ":memory:" else Path(f"{database_path}.m11.lock")
        self._file_descriptor: int | None = None
        self._windows_locked = False

    def __enter__(self) -> "_RepositoryRunLock":
        if self._lock_path is None:
            return self
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_descriptor = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR)
        descriptor = self._file_descriptor
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        deadline = time.monotonic() + self._timeout_seconds
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    self._windows_locked = True
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        self._close()
                        raise JobConcurrencyError("local run lock was not acquired within the bounded wait") from exc
                    time.sleep(0.01)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        self._close()
                        raise JobConcurrencyError("local run lock was not acquired within the bounded wait") from exc
                    time.sleep(0.01)
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        if self._file_descriptor is None:
            return
        descriptor = self._file_descriptor
        try:
            if os.name == "nt" and self._windows_locked:
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            elif os.name != "nt":
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            self._close()

    def _close(self) -> None:
        if self._file_descriptor is not None:
            os.close(self._file_descriptor)
            self._file_descriptor = None


class JobOrchestrator:
    """Run the fixed M11 pipeline with durable, append-only state."""

    def __init__(
        self,
        repository: SQLiteRepository,
        stages: JobStages,
        *,
        clock: Callable[[], str] = _utc_now,
        monotonic_clock: Callable[[], float] = time.monotonic,
        run_id_factory: Callable[[], str] = _new_run_id,
    ) -> None:
        if not isinstance(repository, SQLiteRepository):
            raise JobValidationError("repository must be SQLiteRepository")
        if not repository.store.initialized:
            raise JobValidationError("repository store must be initialized")
        if not isinstance(stages, JobStages):
            raise JobValidationError("stages must be JobStages")
        if not callable(clock) or not callable(monotonic_clock) or not callable(run_id_factory):
            raise JobValidationError("clock, monotonic_clock, and run_id_factory must be callable")
        self.repository = repository
        self.stages = stages
        self.clock = clock
        self.monotonic_clock = monotonic_clock
        self.run_id_factory = run_id_factory
        self._condition = threading.Condition(threading.RLock())
        self._active: dict[str, threading.Event] = {}
        self._active_hashes: dict[str, str] = {}
        self._completed: dict[str, JobRunResult] = {}
        self._active_errors: dict[str, BaseException] = {}

    def run(self, request: JobRequest) -> JobRunResult:
        if not isinstance(request, JobRequest):
            raise JobValidationError("request must be JobRequest")
        key = request.idempotency_key
        while True:
            with self._condition:
                cached = self._completed.get(key)
                if cached is not None:
                    if cached.request_hash != request.request_hash:
                        raise JobConflictError("idempotency_key is already bound to different input")
                    return replace(cached, idempotent=True)
                event = self._active.get(key)
                if event is None:
                    event = threading.Event()
                    self._active[key] = event
                    self._active_hashes[key] = request.request_hash
                    break
                if self._active_hashes.get(key) != request.request_hash:
                    raise JobConflictError("idempotency_key is already bound to different input")
            if not event.wait(30.0):
                raise JobConcurrencyError("duplicate request did not finish within the bounded wait")
            with self._condition:
                error = self._active_errors.pop(key, None)
                if error is not None:
                    raise JobConcurrencyError("duplicate request owner failed before returning a result") from error

        result: JobRunResult | None = None
        try:
            with _RepositoryRunLock(self.repository.store.path):
                result = self._execute(request)
            return result
        finally:
            with self._condition:
                self._active.pop(key, None)
                self._active_hashes.pop(key, None)
                if result is not None:
                    self._completed[key] = result
                event.set()

    def get_run_status(self, *, run_id: str | None = None, idempotency_key: str | None = None) -> JobRunResult:
        provided = [value for value in (run_id, idempotency_key) if value is not None]
        if len(provided) != 1:
            raise JobValidationError("provide exactly one of run_id or idempotency_key")
        if idempotency_key is not None:
            key = _text(idempotency_key, "idempotency_key")
            with self._condition:
                cached = self._completed.get(key)
            if cached is not None:
                return replace(cached, idempotent=True)
            existing = self._load_existing(idempotency_key=key)
        else:
            existing = self._load_existing(run_id=_text(run_id, "run_id"))
        if existing is None:
            raise JobNotFoundError("run was not found")
        return existing.result(idempotent=True)

    def _execute(self, request: JobRequest) -> JobRunResult:
        existing = self._load_existing(idempotency_key=request.idempotency_key)
        if existing is not None:
            if existing.request_hash != request.request_hash:
                raise JobConflictError("idempotency_key is already bound to different input")
            if existing.status in TERMINAL_STATES:
                return existing.result(idempotent=True)
            run_id = existing.run_id
            started_at = existing.started_at
            sequence = existing.sequence + 1
            completed = {item.stage: item for item in existing.stage_results}
            start_index, resume_running = self._resume_position(existing)
        else:
            run_id = self._allocate_run_id()
            started_at = _iso_timestamp(self.clock(), "clock")
            sequence = 0
            completed = {}
            start_index = 0
            resume_running = False
            self._persist_transition(
                request,
                run_id=run_id,
                request_hash=request.request_hash,
                sequence=sequence,
                state="scheduled",
                stage=None,
                started_at=started_at,
                finished_at=None,
                completed=completed,
                quality="OK",
                reason_codes=(),
                error=None,
                published_snapshot=None,
            )
            sequence += 1

        started_mono = self.monotonic_clock()
        for index in range(start_index, len(STAGE_NAMES)):
            stage_name = STAGE_NAMES[index]
            if not (resume_running and index == start_index):
                try:
                    self._check_timeout(request, started_mono, stage_name)
                except _JobTimeout:
                    return self._finish_failure(
                        request,
                        run_id,
                        request.request_hash,
                        sequence,
                        started_at,
                        completed,
                        stage_name,
                        "failed",
                        "TIMEOUT",
                        ("timeout",),
                        None,
                    )
                self._persist_transition(
                    request,
                    run_id=run_id,
                    request_hash=request.request_hash,
                    sequence=sequence,
                    state="running",
                    stage=stage_name,
                    started_at=started_at,
                    finished_at=None,
                    completed=completed,
                    quality="OK",
                    reason_codes=(),
                    error=None,
                    published_snapshot=None,
                )
                sequence += 1

            try:
                self._check_timeout(request, started_mono, stage_name)
                context = StageContext(run_id, request, stage_name, tuple(completed[name] for name in STAGE_NAMES if name in completed))
                output = self.stages.callback(stage_name)(context)
                if not isinstance(output, StageResult):
                    raise JobValidationError(f"stage {stage_name} did not return StageResult")
                self._validate_stage_output(request, output, stage_name)
                self._check_timeout(request, started_mono, stage_name)
            except _JobTimeout:
                return self._finish_failure(
                    request,
                    run_id,
                    request.request_hash,
                    sequence,
                    started_at,
                    completed,
                    stage_name,
                    "failed",
                    "TIMEOUT",
                    ("timeout",),
                    None,
                )
            except KeyboardInterrupt:
                # A process interruption deliberately leaves the durable
                # ``running`` state so a new process can resume it.
                raise
            except Exception:
                return self._finish_failure(
                    request,
                    run_id,
                    request.request_hash,
                    sequence,
                    started_at,
                    completed,
                    stage_name,
                    "failed",
                    "STAGE_FAILED",
                    (f"stage_failed:{stage_name}",),
                    None,
                )

            if output.quality != "OK":
                completed[stage_name] = output
                terminal = self._terminal_for_quality(output.quality)
                return self._finish_failure(
                    request,
                    run_id,
                    request.request_hash,
                    sequence,
                    started_at,
                    completed,
                    stage_name,
                    terminal,
                    f"QUALITY_{output.quality}",
                    tuple(output.reason_codes) + (f"quality:{output.quality}",),
                    None,
                )

            completed[stage_name] = output
            if stage_name != "publish":
                self._persist_transition(
                    request,
                    run_id=run_id,
                    request_hash=request.request_hash,
                    sequence=sequence,
                    state=_STAGE_READY_STATES[stage_name],
                    stage=stage_name,
                    started_at=started_at,
                    finished_at=None,
                    completed=completed,
                    quality="OK",
                    reason_codes=output.reason_codes,
                    error=None,
                    published_snapshot=None,
                )
                sequence += 1
            else:
                self._persist_transition(
                    request,
                    run_id=run_id,
                    request_hash=request.request_hash,
                    sequence=sequence,
                    state="published",
                    stage="publish",
                    started_at=started_at,
                    finished_at=_iso_timestamp(self.clock(), "clock"),
                    completed=completed,
                    quality="OK",
                    reason_codes=output.reason_codes,
                    error=None,
                    published_snapshot=output.snapshot,
                )
                final = self._load_existing(idempotency_key=request.idempotency_key)
                if final is None:
                    raise JobError("published run disappeared from repository")
                return final.result(idempotent=False)

            resume_running = False

        raise JobError("pipeline ended without a terminal state")

    def _validate_stage_output(self, request: JobRequest, output: StageResult, expected_stage: str) -> None:
        if output.stage != expected_stage:
            raise JobValidationError(f"stage output mismatch: expected {expected_stage}")
        if dict(output.manifest) != dict(request.input_manifest):
            raise JobValidationError("stage output manifest does not match request manifest")
        if expected_stage == "publish" and not output.snapshot:
            raise JobValidationError("publish stage must return a non-empty snapshot")

    def _check_timeout(self, request: JobRequest, started_mono: float, stage: str) -> None:
        if request.timeout_seconds is None:
            return
        elapsed = float(self.monotonic_clock()) - float(started_mono)
        if elapsed > request.timeout_seconds:
            raise _JobTimeout(f"stage timed out: {stage}")

    @staticmethod
    def _terminal_for_quality(quality: str) -> str:
        if quality == "FAILED":
            return "failed"
        return quality.lower()

    def _finish_failure(
        self,
        request: JobRequest,
        run_id: str,
        request_hash: str,
        sequence: int,
        started_at: str,
        completed: Mapping[str, StageResult],
        stage: str,
        state: str,
        error_code: str,
        reason_codes: tuple[str, ...],
        output: StageResult | None,
    ) -> JobRunResult:
        if output is not None:
            completed = dict(completed)
            completed[stage] = output
        quality = {
            "stale": "STALE",
            "partial": "PARTIAL",
            "needs_review": "NEEDS_REVIEW",
            "failed": "FAILED",
        }[state]
        self._persist_transition(
            request,
            run_id=run_id,
            request_hash=request_hash,
            sequence=sequence,
            state=state,
            stage=stage,
            started_at=started_at,
            finished_at=_iso_timestamp(self.clock(), "clock"),
            completed=completed,
            quality=quality,
            reason_codes=reason_codes,
            error={"code": error_code, "stage": stage},
            published_snapshot=None,
        )
        self._persist_quality_event(request, run_id, stage, quality, reason_codes)
        final = self._load_existing(idempotency_key=request.idempotency_key)
        if final is None:
            raise JobError("failed run disappeared from repository")
        return final.result(idempotent=False)

    def _persist_transition(
        self,
        request: JobRequest,
        *,
        run_id: str,
        request_hash: str,
        sequence: int,
        state: str,
        stage: str | None,
        started_at: str,
        finished_at: str | None,
        completed: Mapping[str, StageResult],
        quality: str,
        reason_codes: tuple[str, ...],
        error: Mapping[str, Any] | None,
        published_snapshot: Mapping[str, Any] | None,
    ) -> StoredRecord:
        stage_results = {name: completed[name].as_dict() for name in STAGE_NAMES if name in completed}
        payload = {
            "schema": JOB_RUN_SCHEMA,
            "implementation_version": JOB_IMPLEMENTATION_VERSION,
            "run_id": run_id,
            "idempotency_key": request.idempotency_key,
            "request_hash": request_hash,
            "run_type": JOB_RUN_TYPE,
            "sequence": sequence,
            "state": state,
            "stage": stage,
            "strategy_version": request.strategy_version,
            "data_version": request.data_version,
            "signal_date": request.signal_date,
            "execution_date": request.execution_date,
            "as_of": request.as_of,
            "input_manifest": dict(request.input_manifest),
            "completed_stages": list(stage_results),
            "stage_results": stage_results,
            "quality": quality,
            "reason_codes": list(reason_codes),
            "error": None if error is None else copy.deepcopy(dict(error)),
            "published_snapshot": None if published_snapshot is None else copy.deepcopy(dict(published_snapshot)),
        }
        return self.repository.put_run(
            f"{run_id}|{sequence:04d}|{state}",
            payload,
            run_type=JOB_RUN_TYPE,
            started_at=started_at,
            finished_at=finished_at,
            status=state,
            strategy_version=request.strategy_version,
            data_version=request.data_version,
        )

    def _persist_quality_event(
        self,
        request: JobRequest,
        run_id: str,
        stage: str,
        quality: str,
        reason_codes: tuple[str, ...],
    ) -> StoredRecord:
        payload = {
            "schema": JOB_QUALITY_SCHEMA,
            "implementation_version": JOB_IMPLEMENTATION_VERSION,
            "run_id": run_id,
            "stage": stage,
            "quality": quality,
            "reason_codes": list(reason_codes),
            "signal_date": request.signal_date,
            "execution_date": request.execution_date,
        }
        return self.repository.put_quality_event(
            f"{run_id}|quality|{stage}",
            payload,
            event_date=request.signal_date,
            source="m11-job",
            symbol="SYSTEM",
            severity=quality,
            status=quality,
        )

    def _allocate_run_id(self) -> str:
        for _ in range(8):
            candidate = _text(self.run_id_factory(), "run_id", maximum=128)
            if not self._load_existing(run_id=candidate):
                return candidate
        raise JobConflictError("run_id_factory returned an existing run repeatedly")

    def _resume_position(self, existing: _ExistingRun) -> tuple[int, bool]:
        if existing.status == "scheduled":
            return 0, False
        if existing.status == "running":
            if existing.stage not in STAGE_NAMES:
                raise JobConflictError("running state has no valid stage")
            return STAGE_NAMES.index(existing.stage), True
        if existing.status in _STAGE_READY_STATES.values():
            for stage, state in _STAGE_READY_STATES.items():
                if state == existing.status:
                    return STAGE_NAMES.index(stage) + 1, False
        raise JobConflictError(f"run cannot resume from state {existing.status}")

    def _load_existing(
        self,
        *,
        idempotency_key: str | None = None,
        run_id: str | None = None,
    ) -> _ExistingRun | None:
        if (idempotency_key is None) == (run_id is None):
            raise JobValidationError("internal lookup requires exactly one selector")
        records = self._run_records()
        selected = []
        for record in records:
            payload = record.payload
            if payload.get("schema") != JOB_RUN_SCHEMA:
                continue
            if idempotency_key is not None and payload.get("idempotency_key") == idempotency_key:
                selected.append(record)
            elif run_id is not None and payload.get("run_id") == run_id:
                selected.append(record)
        if not selected:
            return None
        run_ids = {str(item.payload.get("run_id")) for item in selected}
        if len(run_ids) != 1:
            raise JobConflictError("one idempotency key maps to multiple run IDs")
        ordered = sorted(selected, key=lambda item: (int(item.payload.get("sequence", -1)), item.record_key))
        hashes = {str(item.payload.get("request_hash")) for item in ordered}
        if len(hashes) != 1 or None in hashes:
            raise JobConflictError("run history contains inconsistent request hashes")
        first = ordered[0].payload
        last = ordered[-1].payload
        transitions = tuple(self._transition_from_record(item) for item in ordered)
        stage_results = self._decode_stage_results(last.get("stage_results", {}))
        started_at = ordered[0].metadata.get("started_at")
        if not isinstance(started_at, str):
            raise JobConflictError("run history is missing started_at")
        published = last.get("published_snapshot")
        published_snapshot = None if not isinstance(published, Mapping) or not published else _json_object(published, "published_snapshot")
        return _ExistingRun(
            run_id=str(first["run_id"]),
            request_hash=next(iter(hashes)),
            started_at=started_at,
            status=str(last.get("state")),
            stage=last.get("stage") if isinstance(last.get("stage"), str) else None,
            sequence=int(last.get("sequence", -1)),
            transitions=transitions,
            stage_results=stage_results,
            published_snapshot=published_snapshot,
            last_payload=last,
        )

    def _run_records(self) -> tuple[StoredRecord, ...]:
        records: list[StoredRecord] = []
        offset = 0
        while offset < _MAX_SCAN_RECORDS:
            limit = min(1_000, _MAX_SCAN_RECORDS - offset)
            page = self.repository.list("run", limit=limit, offset=offset)
            records.extend(page)
            if len(page) < limit:
                break
            offset += len(page)
        if len(records) >= _MAX_SCAN_RECORDS:
            raise JobConcurrencyError("run history exceeded the bounded lookup window")
        return tuple(records)

    @staticmethod
    def _decode_stage_results(value: Any) -> tuple[StageResult, ...]:
        if not isinstance(value, Mapping):
            raise JobConflictError("run history stage_results is not an object")
        result: list[StageResult] = []
        for stage in STAGE_NAMES:
            raw = value.get(stage)
            if raw is None:
                continue
            if not isinstance(raw, Mapping):
                raise JobConflictError("run history contains an invalid stage result")
            result.append(
                StageResult(
                    stage=raw.get("stage"),
                    quality=raw.get("quality"),
                    manifest=raw.get("manifest"),
                    snapshot=raw.get("snapshot"),
                    reason_codes=raw.get("reason_codes", ()),
                )
            )
        return tuple(result)

    @staticmethod
    def _transition_from_record(record: StoredRecord) -> RunTransition:
        payload = record.payload
        return RunTransition(
            sequence=int(payload.get("sequence", -1)),
            state=str(payload.get("state")),
            stage=payload.get("stage") if isinstance(payload.get("stage"), str) else None,
            quality=_quality(payload.get("quality")),
            started_at=str(record.metadata.get("started_at")),
            finished_at=record.metadata.get("finished_at") if isinstance(record.metadata.get("finished_at"), str) else None,
            reason_codes=_reason_codes(payload.get("reason_codes", ())),
        )


__all__ = [
    "JOB_IMPLEMENTATION_VERSION",
    "JOB_QUALITY_SCHEMA",
    "JOB_RUN_SCHEMA",
    "JOB_RUN_TYPE",
    "QUALITY_VALUES",
    "STAGE_NAMES",
    "JobConcurrencyError",
    "JobConflictError",
    "JobError",
    "JobNotFoundError",
    "JobOrchestrator",
    "JobRequest",
    "JobRunResult",
    "JobStages",
    "JobValidationError",
    "RunTransition",
    "StageContext",
    "StageResult",
]
