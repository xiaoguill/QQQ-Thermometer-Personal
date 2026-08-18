"""Refresh scheduling and fail-closed quality evaluation."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .config import RealtimeConfig
from .massive_client import MassiveClient
from .models import ObservationBatch, RealtimeObservation


SERVICE_FAILURE_STATUSES = {"FAILED", "NEEDS_REVIEW", "NOT_ENTITLED", "NOT_FOUND", "RATE_LIMITED"}
MAX_BACKOFF_SECONDS = 3_600


def evaluate_observation_quality(
    observation: RealtimeObservation,
    *,
    now_utc: datetime,
    max_age_seconds: int,
    future_skew_seconds: int,
) -> RealtimeObservation:
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    if observation.quality != "OK" or observation.source_timestamp_utc is None:
        return observation
    age = (now_utc - observation.source_timestamp_utc).total_seconds()
    # A provider timestamp in the future is never publishable. The config field
    # remains part of the versioned contract for compatibility, but tolerance is
    # intentionally not applied to market observations.
    if age < 0:
        return replace(observation, quality="NEEDS_REVIEW", error_code="FUTURE_TIMESTAMP", error_message="source timestamp is ahead of local clock")
    if age > max_age_seconds:
        return replace(observation, quality="STALE", error_code="SOURCE_STALE", error_message="source observation exceeds configured age")
    return observation


class RealtimePoller:
    def __init__(self, config: RealtimeConfig, client: MassiveClient) -> None:
        config.validate()
        if client.config != config:
            raise ValueError("client must use the same realtime config")
        self.config = config
        self.client = client
        self._last_source_timestamp_by_symbol: dict[str, datetime] = {}
        self._last_dedupe_key_by_symbol: dict[str, str] = {}
        self._failure_count = 0

    def poll_once(self, *, now_utc: datetime | None = None) -> ObservationBatch:
        now = now_utc or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("now_utc must be timezone-aware")
        batch = self.client.fetch_batch(fetched_at_utc=now)
        observations: list[RealtimeObservation] = []
        for item in batch.observations:
            evaluated = evaluate_observation_quality(
                item,
                now_utc=now,
                max_age_seconds=self.config.max_source_age_seconds,
                future_skew_seconds=self.config.future_skew_seconds,
            )
            previous_timestamp = self._last_source_timestamp_by_symbol.get(item.symbol)
            if (
                evaluated.quality == "OK"
                and evaluated.source_timestamp_utc is not None
                and previous_timestamp is not None
                and evaluated.source_timestamp_utc < previous_timestamp
            ):
                evaluated = replace(
                    evaluated,
                    quality="NEEDS_REVIEW",
                    error_code="SOURCE_TIME_REGRESSION",
                    error_message="source timestamp moved backwards",
                )

            duplicate = self._last_dedupe_key_by_symbol.get(item.symbol) == evaluated.dedupe_key
            if duplicate:
                evaluated = replace(evaluated, is_duplicate=True)
            else:
                evaluated = replace(evaluated, is_duplicate=False)

            if evaluated.source_timestamp_utc is not None and evaluated.quality == "OK":
                self._last_source_timestamp_by_symbol[item.symbol] = evaluated.source_timestamp_utc
            self._last_dedupe_key_by_symbol[item.symbol] = evaluated.dedupe_key
            observations.append(evaluated)

        result = replace(batch, observations=tuple(observations))
        self.record_result(result)
        return result

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def record_result(self, batch: ObservationBatch) -> None:
        """Update deterministic reconnect backoff without sleeping or spinning."""

        if any(item.quality in SERVICE_FAILURE_STATUSES for item in batch.observations):
            self._failure_count += 1
        else:
            self._failure_count = 0

    def next_retry_delay_seconds(self) -> int:
        """Return the next delay; the caller owns scheduling and cancellation."""

        if self._failure_count == 0:
            return self.config.refresh_interval_seconds
        delay = self.config.refresh_interval_seconds * (2 ** min(self._failure_count, 8))
        return min(delay, MAX_BACKOFF_SECONDS)

    def next_refresh_at(self, *, fetched_at_utc: datetime) -> datetime:
        if fetched_at_utc.tzinfo is None:
            raise ValueError("fetched_at_utc must be timezone-aware")
        return fetched_at_utc + timedelta(seconds=self.config.refresh_interval_seconds)
