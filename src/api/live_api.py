"""M16 local/private SSE application boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.api.read_api import ApiAccessPolicy, ApiError, ApiResponse
from src.notifications.events import LiveEvent, LiveEventBus, SseEventStream
from src.realtime.models import ObservationBatch
from src.realtime.read_model import ConfirmedReadModelError, open_confirmed_repository


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
        confirmed_read_model_path: str | Path | None = None,
        heartbeat_seconds: float = 15.0,
    ) -> None:
        if not isinstance(event_bus, LiveEventBus):
            raise TypeError("event_bus must be LiveEventBus")
        self.event_bus = event_bus
        self.access_policy = access_policy or ApiAccessPolicy()
        self.confirmed_read_model_path = (
            Path(confirmed_read_model_path).expanduser().resolve()
            if confirmed_read_model_path is not None
            else None
        )
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

    def latest_confirmed(
        self,
        *,
        headers: Mapping[str, Any] | None = None,
        client_host: str | None = "127.0.0.1",
    ) -> ApiResponse:
        """Expose only the existing confirmed read model; never calculate or write state."""

        self.access_policy.authorize(client_host, headers)
        if self.confirmed_read_model_path is None:
            raise ApiError(503, "CONFIRMED_UNAVAILABLE", "confirmed read model is not configured")
        path = self.confirmed_read_model_path
        if not path.is_file():
            raise ApiError(503, "CONFIRMED_UNAVAILABLE", "confirmed read model is unavailable")
        from src.api.read_api import ReadApiService

        try:
            with open_confirmed_repository(path) as repository:
                read_api = ReadApiService(repository, access_policy=self.access_policy)
                response = read_api.handle(
                    "GET",
                    "/api/thermometer/latest",
                    headers=headers,
                    client_host=client_host,
                )
                meta = response.body.get("meta", {}) if isinstance(response.body, Mapping) else {}
                data = response.body.get("data", {}) if isinstance(response.body, Mapping) else {}
                if (
                    response.status_code == 200
                    and isinstance(meta, Mapping)
                    and isinstance(data, Mapping)
                    and meta.get("data_quality") == "ok"
                    and isinstance(data.get("state"), str)
                ):
                    self.event_bus.publish_state_candidate(
                        {
                            "state": data["state"],
                            "strategy_version": str(meta.get("strategy_version", "unavailable")),
                            "signal_date": str(meta.get("signal_date", "1970-01-01")),
                            "data_quality": str(meta["data_quality"]),
                            "confirmed": True,
                            "provisional": False,
                        },
                        occurred_at_utc=datetime.now(timezone.utc),
                    )
                return response
        except ConfirmedReadModelError as exc:
            raise ApiError(503, "CONFIRMED_UNAVAILABLE", "confirmed read model is unavailable") from exc


def create_live_app(
    event_bus: LiveEventBus,
    *,
    access_policy: ApiAccessPolicy | None = None,
    confirmed_read_model_path: str | Path | None = None,
    heartbeat_seconds: float = 15.0,
) -> LiveApiService:
    return LiveApiService(
        event_bus,
        access_policy=access_policy,
        confirmed_read_model_path=confirmed_read_model_path,
        heartbeat_seconds=heartbeat_seconds,
    )


__all__ = ["LiveApiService", "LiveStreamHeaders", "create_live_app"]
