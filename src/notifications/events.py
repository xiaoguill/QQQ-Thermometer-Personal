"""Bounded, idempotent in-memory events for the private M16 observer."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from threading import Condition, RLock
from typing import Any, Mapping

from src.realtime.models import ObservationBatch


EVENT_TYPES = frozenset({
    "observation.batch",
    "quality.changed",
    "service.status",
    "state.candidate",
})
DECLARED_NOTIFICATION_TYPES = frozenset({"quality.changed", "service.status", "state.candidate"})


def should_notify(event_type: str) -> bool:
    return event_type in DECLARED_NOTIFICATION_TYPES


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _event_id(event_type: str, dedupe_key: str) -> str:
    digest = hashlib.sha256(f"{event_type}|{dedupe_key}".encode("utf-8")).hexdigest()
    return f"evt-{digest[:32]}"


def _validate_event_id(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 200 or any(ord(char) < 32 for char in value):
        raise ValueError("last_event_id is invalid")
    return value


@dataclass(frozen=True)
class LiveEvent:
    event_id: str
    event_type: str
    occurred_at_utc: datetime
    payload: Mapping[str, Any]
    dedupe_key: str
    notification: bool

    def __post_init__(self) -> None:
        if not self.event_id or any(ord(char) < 32 for char in self.event_id):
            raise ValueError("event_id is invalid")
        if self.event_type not in EVENT_TYPES:
            raise ValueError("event_type is not declared")
        if self.occurred_at_utc.tzinfo is None:
            raise ValueError("occurred_at_utc must be timezone-aware")
        if not self.dedupe_key or len(self.dedupe_key) > 500:
            raise ValueError("dedupe_key is invalid")
        # Validate at construction time so an SSE serializer cannot emit NaN,
        # a non-JSON object, or a mutable reference to provider internals.
        _canonical_json(self.payload)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at_utc.astimezone(timezone.utc).isoformat(),
            "payload": deepcopy(dict(self.payload)),
            "notification": self.notification,
        }

    def sse_frame(self) -> bytes:
        data = _canonical_json(self.as_dict())
        return (
            f"id: {self.event_id}\n"
            f"event: {self.event_type}\n"
            f"data: {data}\n\n"
        ).encode("utf-8")


@dataclass(frozen=True)
class EventCursor:
    events: tuple[LiveEvent, ...]
    cursor_expired: bool
    reset_to: str | None


class LiveEventBus:
    """Thread-safe bounded event log with semantic idempotency."""

    def __init__(self, *, max_events: int = 256, max_seen_ids: int = 2048) -> None:
        if isinstance(max_events, bool) or not isinstance(max_events, int) or not 1 <= max_events <= 10_000:
            raise ValueError("max_events must be between 1 and 10000")
        if isinstance(max_seen_ids, bool) or not isinstance(max_seen_ids, int) or max_seen_ids < max_events:
            raise ValueError("max_seen_ids must be an integer at least max_events")
        self._events: deque[LiveEvent] = deque(maxlen=max_events)
        self._seen_ids: deque[str] = deque(maxlen=max_seen_ids)
        self._seen_set: set[str] = set()
        self._condition = Condition(RLock())

    @property
    def size(self) -> int:
        with self._condition:
            return len(self._events)

    def publish(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        dedupe_key: str,
        occurred_at_utc: datetime,
        notification: bool | None = None,
    ) -> LiveEvent | None:
        if event_type not in EVENT_TYPES:
            raise ValueError("event_type is not declared")
        if not isinstance(payload, Mapping):
            raise ValueError("payload must be an object")
        if not isinstance(dedupe_key, str) or not dedupe_key.strip() or len(dedupe_key) > 500:
            raise ValueError("dedupe_key is invalid")
        normalized_payload = deepcopy(dict(payload))
        _canonical_json(normalized_payload)
        event_id = _event_id(event_type, dedupe_key.strip())
        event = LiveEvent(
            event_id=event_id,
            event_type=event_type,
            occurred_at_utc=occurred_at_utc,
            payload=normalized_payload,
            dedupe_key=dedupe_key.strip(),
            notification=should_notify(event_type) if notification is None else bool(notification),
        )
        with self._condition:
            if event_id in self._seen_set:
                return None
            # deque silently drops the oldest id when full; mirror that
            # eviction explicitly so the set remains bounded as well.
            if len(self._seen_ids) == self._seen_ids.maxlen:
                self._seen_set.discard(self._seen_ids[0])
            self._events.append(event)
            self._seen_ids.append(event_id)
            self._seen_set.add(event_id)
            self._condition.notify_all()
        return event

    def events_after(self, last_event_id: str | None = None) -> EventCursor:
        last_event_id = _validate_event_id(last_event_id)
        with self._condition:
            events = tuple(self._events)
            if last_event_id is None:
                return EventCursor(events=events, cursor_expired=False, reset_to=events[-1].event_id if events else None)
            ids = [item.event_id for item in events]
            if last_event_id in ids:
                index = ids.index(last_event_id)
                return EventCursor(events=events[index + 1 :], cursor_expired=False, reset_to=events[-1].event_id if events else last_event_id)
            return EventCursor(events=(), cursor_expired=True, reset_to=events[-1].event_id if events else None)

    def wait_after(self, last_event_id: str | None, *, timeout_seconds: float) -> EventCursor:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds cannot be negative")
        with self._condition:
            current = self.events_after(last_event_id)
            if current.events or current.cursor_expired or timeout_seconds == 0:
                return current
            self._condition.wait(timeout_seconds)
            return self.events_after(last_event_id)

    def publish_batch(self, batch: ObservationBatch) -> tuple[LiveEvent, ...]:
        """Publish only new observation content and declared quality alerts."""

        changed = tuple(item for item in batch.observations if not item.is_duplicate)
        if not changed:
            return ()
        batch_key = ":".join([batch.batch_id, *sorted(f"{item.symbol}:{item.dedupe_key}" for item in changed)])
        events: list[LiveEvent] = []
        observation = self.publish(
            "observation.batch",
            batch.as_dict(),
            dedupe_key=f"observation:{batch_key}",
            occurred_at_utc=batch.fetched_at_utc,
            notification=False,
        )
        if observation is not None:
            events.append(observation)
        failures = [item for item in changed if item.quality != "OK"]
        if failures:
            quality_payload = {
                "source": batch.source,
                "batch_id": batch.batch_id,
                "symbols": [
                    {"symbol": item.symbol, "quality": item.quality, "error_code": item.error_code}
                    for item in failures
                ],
                "provisional": True,
            }
            quality = self.publish(
                "quality.changed",
                quality_payload,
                dedupe_key=f"quality:{batch_key}",
                occurred_at_utc=batch.fetched_at_utc,
                notification=True,
            )
            if quality is not None:
                events.append(quality)
        return tuple(events)

    def publish_service_status(self, status: str, *, occurred_at_utc: datetime, detail: str | None = None) -> LiveEvent | None:
        if not isinstance(status, str) or not status.strip():
            raise ValueError("service status is required")
        payload: dict[str, Any] = {"status": status.strip()}
        if detail:
            payload["detail"] = detail[:500]
        return self.publish(
            "service.status",
            payload,
            dedupe_key=f"service:{status.strip()}:{detail or ''}",
            occurred_at_utc=occurred_at_utc,
            notification=True,
        )


class SseEventStream:
    """Reconnectable SSE cursor; no background worker and no busy loop."""

    def __init__(self, bus: LiveEventBus, *, last_event_id: str | None = None, heartbeat_seconds: float = 15.0) -> None:
        self.bus = bus
        self.last_event_id = _validate_event_id(last_event_id)
        if heartbeat_seconds <= 0:
            raise ValueError("heartbeat_seconds must be positive")
        self.heartbeat_seconds = float(heartbeat_seconds)
        self._pending: deque[LiveEvent] = deque()
        self._closed = False

    def close(self) -> None:
        self._closed = True

    def next_frame(self, *, timeout_seconds: float | None = None) -> bytes:
        if self._closed:
            return b""
        if self._pending:
            event = self._pending.popleft()
            self.last_event_id = event.event_id
            return event.sse_frame()
        timeout = self.heartbeat_seconds if timeout_seconds is None else float(timeout_seconds)
        previous_id = self.last_event_id
        cursor = self.bus.wait_after(self.last_event_id, timeout_seconds=timeout)
        if cursor.cursor_expired:
            self.last_event_id = cursor.reset_to
            payload = {"reset_required": True, "last_event_id": previous_id, "reset_to": self.last_event_id}
            data = _canonical_json(payload)
            reset_id = cursor.reset_to or "cursor-reset"
            return f"id: {reset_id}\nevent: cursor.reset\ndata: {data}\n\n".encode("utf-8")
        self._pending.extend(cursor.events)
        if self._pending:
            event = self._pending.popleft()
            self.last_event_id = event.event_id
            return event.sse_frame()
        return b": heartbeat\n\n"

    def iter_frames(self):
        yield b": connected\n\n"
        while not self._closed:
            frame = self.next_frame()
            if not frame:
                return
            yield frame
