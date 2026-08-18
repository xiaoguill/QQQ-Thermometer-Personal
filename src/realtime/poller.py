"""Refresh scheduling and fail-closed quality evaluation."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .config import RealtimeConfig
from .massive_client import MassiveClient
from .models import ObservationBatch, RealtimeObservation


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
    if age < -future_skew_seconds:
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

    def poll_once(self, *, now_utc: datetime | None = None) -> ObservationBatch:
        now = now_utc or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("now_utc must be timezone-aware")
        batch = self.client.fetch_batch(fetched_at_utc=now)
        observations = tuple(
            evaluate_observation_quality(
                item,
                now_utc=now,
                max_age_seconds=self.config.max_source_age_seconds,
                future_skew_seconds=self.config.future_skew_seconds,
            )
            for item in batch.observations
        )
        return replace(batch, observations=observations)

    def next_refresh_at(self, *, fetched_at_utc: datetime) -> datetime:
        if fetched_at_utc.tzinfo is None:
            raise ValueError("fetched_at_utc must be timezone-aware")
        return fetched_at_utc + timedelta(seconds=self.config.refresh_interval_seconds)
