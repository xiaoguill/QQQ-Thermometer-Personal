"""M16 personal runtime: Massive polling -> quality -> local event stream."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Callable

from src.api.live_api import LiveApiService, create_live_app
from src.notifications import LiveEventBus

from .config import DEFAULT_CONFIG_PATH, RealtimeConfig, load_realtime_config
from .massive_client import MassiveClient
from .models import ObservationBatch
from .poller import RealtimePoller


@dataclass(frozen=True)
class RuntimeSnapshot:
    status: str
    last_batch_id: str | None
    last_fetched_at_utc: datetime | None
    next_refresh_at_utc: datetime | None
    consecutive_failures: int


class RealtimeRuntime:
    """A stoppable, non-spinning polling loop for personal use."""

    def __init__(
        self,
        config: RealtimeConfig,
        poller: RealtimePoller,
        live_api: LiveApiService,
        *,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        config.validate()
        if poller.config != config:
            raise ValueError("poller must use the runtime config")
        if live_api.event_bus is None:
            raise ValueError("live_api must have an event bus")
        self.config = config
        self.poller = poller
        self.live_api = live_api
        self._now_factory = now_factory or (lambda: datetime.now(timezone.utc))
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._lock = RLock()
        self._snapshot = RuntimeSnapshot("idle", None, None, None, 0)

    @property
    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            return self._snapshot

    def poll_once(self, *, now_utc: datetime | None = None) -> ObservationBatch:
        now = now_utc or self._now_factory()
        if now.tzinfo is None:
            raise ValueError("now_utc must be timezone-aware")
        batch = self.poller.poll_once(now_utc=now)
        self.live_api.publish_batch(batch)
        if batch.has_quality_failure:
            details = ",".join(f"{item.symbol}:{item.quality}" for item in batch.observations if item.quality != "OK")
            status = "degraded"
            self.live_api.publish_service_status("degraded", occurred_at_utc=batch.fetched_at_utc, detail=details)
        else:
            status = "ready"
            self.live_api.publish_service_status("ready", occurred_at_utc=batch.fetched_at_utc)
        with self._lock:
            self._snapshot = RuntimeSnapshot(
                status=status,
                last_batch_id=batch.batch_id,
                last_fetched_at_utc=batch.fetched_at_utc,
                next_refresh_at_utc=self.poller.next_refresh_at(fetched_at_utc=batch.fetched_at_utc),
                consecutive_failures=self.poller.failure_count,
            )
        return batch

    def run_forever(self, *, stop_event: Event | None = None) -> None:
        stopper = stop_event or self._stop_event
        while not stopper.is_set():
            try:
                self.poll_once()
                delay = self.poller.next_retry_delay_seconds()
            except Exception as exc:  # fail closed and wait; never spin on a provider error
                with self._lock:
                    failures = self._snapshot.consecutive_failures + 1
                    self._snapshot = RuntimeSnapshot(
                        status="failed",
                        last_batch_id=self._snapshot.last_batch_id,
                        last_fetched_at_utc=self._snapshot.last_fetched_at_utc,
                        next_refresh_at_utc=None,
                        consecutive_failures=failures,
                    )
                now = self._now_factory()
                if now.tzinfo is None:
                    now = now.replace(tzinfo=timezone.utc)
                self.live_api.publish_service_status(
                    "failed",
                    occurred_at_utc=now,
                    detail=type(exc).__name__,
                )
                delay = min(self.config.refresh_interval_seconds * (2 ** min(failures, 8)), 3_600)
            stopper.wait(delay)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = Thread(target=self.run_forever, name="qqq-m16-realtime", daemon=True)
            self._thread.start()

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout_seconds)
        with self._lock:
            self._thread = None


@dataclass(frozen=True)
class RuntimeBundle:
    config: RealtimeConfig
    client: MassiveClient
    poller: RealtimePoller
    event_bus: LiveEventBus
    live_api: LiveApiService
    runtime: RealtimeRuntime


def create_runtime_from_env(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    environ: dict[str, str] | None = None,
) -> RuntimeBundle:
    config = load_realtime_config(config_path)
    client = MassiveClient.from_env(config, environ=environ)
    poller = RealtimePoller(config, client)
    event_bus = LiveEventBus()
    live_api = create_live_app(event_bus)
    runtime = RealtimeRuntime(config, poller, live_api)
    return RuntimeBundle(config, client, poller, event_bus, live_api, runtime)


__all__ = ["RealtimeRuntime", "RuntimeBundle", "RuntimeSnapshot", "create_runtime_from_env"]
