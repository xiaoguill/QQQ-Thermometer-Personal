"""M16 local/private SSE application boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.api.read_api import ApiAccessPolicy, ApiError
from src.notifications.events import LiveEvent, LiveEventBus, SseEventStream
from src.realtime.models import ObservationBatch


@dataclass(frozen=True)
class LiveStreamHeaders:
    content_type: str = "text/event-stream; charset=utf-8"
    cache_control: str = "no-store"
    connection: str = "keep-alive"


class LiveApiService:
    """Read-only event gateway; it owns no provider key and no trading action."""

    def __init__(
        self,
        event_bus: LiveEventBus,
        *,
        access_policy: ApiAccessPolicy | None = None,
        heartbeat_seconds: float = 15.0,
    ) -> None:
        if not isinstance(event_bus, LiveEventBus):
            raise TypeError("event_bus must be LiveEventBus")
        self.event_bus = event_bus
        self.access_policy = access_policy or ApiAccessPolicy()
        self.heartbeat_seconds = heartbeat_seconds

    def open_events(
        self,
        *,
        last_event_id: str | None = None,
        headers: Mapping[str, Any] | None = None,
        client_host: str | None = "127.0.0.1",
    ) -> SseEventStream:
        self.access_policy.authorize(client_host, headers)
        try:
            return SseEventStream(
                self.event_bus,
                last_event_id=last_event_id,
                heartbeat_seconds=self.heartbeat_seconds,
            )
        except ValueError as exc:
            raise ApiError(400, "INVALID_REQUEST", "Last-Event-ID is invalid") from exc

    def publish_batch(self, batch: ObservationBatch) -> tuple[LiveEvent, ...]:
        return self.event_bus.publish_batch(batch)

    def publish_service_status(self, status: str, *, occurred_at_utc, detail: str | None = None) -> LiveEvent | None:
        return self.event_bus.publish_service_status(status, occurred_at_utc=occurred_at_utc, detail=detail)


def create_live_app(
    event_bus: LiveEventBus,
    *,
    access_policy: ApiAccessPolicy | None = None,
    heartbeat_seconds: float = 15.0,
) -> LiveApiService:
    return LiveApiService(event_bus, access_policy=access_policy, heartbeat_seconds=heartbeat_seconds)


__all__ = ["LiveApiService", "LiveStreamHeaders", "create_live_app"]
