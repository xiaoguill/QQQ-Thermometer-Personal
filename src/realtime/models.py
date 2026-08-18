"""Immutable models for M16 provider observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal


QUALITY_STATUSES = (
    "OK",
    "PARTIAL",
    "STALE",
    "FAILED",
    "NEEDS_REVIEW",
    "NOT_ENTITLED",
    "NOT_FOUND",
    "RATE_LIMITED",
)
QualityStatus = Literal[
    "OK",
    "PARTIAL",
    "STALE",
    "FAILED",
    "NEEDS_REVIEW",
    "NOT_ENTITLED",
    "NOT_FOUND",
    "RATE_LIMITED",
]


@dataclass(frozen=True)
class RealtimeSymbol:
    symbol: str
    asset_class: Literal["stocks", "indices"]
    role: str

    def __post_init__(self) -> None:
        if not self.symbol or self.symbol.strip() != self.symbol:
            raise ValueError("symbol must be a non-empty trimmed string")
        if self.asset_class not in {"stocks", "indices"}:
            raise ValueError("asset_class must be stocks or indices")
        if not self.role or self.role.strip() != self.role:
            raise ValueError("role must be a non-empty trimmed string")


@dataclass(frozen=True)
class RealtimeObservation:
    provider: str
    symbol: str
    asset_class: str
    fetched_at_utc: datetime
    source_timestamp_utc: datetime | None
    last: float | None
    close: float | None
    previous_close: float | None
    volume: float | None
    price_basis: str
    quality: QualityStatus
    provisional: bool
    request_id: str | None = None
    raw_payload_hash: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.fetched_at_utc.tzinfo is None:
            raise ValueError("fetched_at_utc must be timezone-aware")
        if self.source_timestamp_utc is not None and self.source_timestamp_utc.tzinfo is None:
            raise ValueError("source_timestamp_utc must be timezone-aware")
        if self.quality not in QUALITY_STATUSES:
            raise ValueError(f"unsupported quality status: {self.quality}")
        if not self.provisional:
            raise ValueError("M16 observations are always provisional until M04-M07 close confirmation")

    def as_dict(self, *, display_timezone: str = "Asia/Shanghai") -> dict[str, Any]:
        # The service layer supplies a validated timezone; keeping conversion out of
        # the model prevents a provider response from changing the project policy.
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(display_timezone)
        source = self.source_timestamp_utc.astimezone(tz).isoformat() if self.source_timestamp_utc else None
        fetched = self.fetched_at_utc.astimezone(tz).isoformat()
        return {
            "provider": self.provider,
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "fetched_at": fetched,
            "source_timestamp": source,
            "last": self.last,
            "close": self.close,
            "previous_close": self.previous_close,
            "volume": self.volume,
            "price_basis": self.price_basis,
            "quality": self.quality,
            "provisional": self.provisional,
            "request_id": self.request_id,
            "raw_payload_hash": self.raw_payload_hash,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class ObservationBatch:
    batch_id: str
    fetched_at_utc: datetime
    observations: tuple[RealtimeObservation, ...]
    display_timezone: str
    source: str = "massive"

    def __post_init__(self) -> None:
        if self.fetched_at_utc.tzinfo is None:
            raise ValueError("fetched_at_utc must be timezone-aware")
        if not self.batch_id:
            raise ValueError("batch_id is required")

    @property
    def has_quality_failure(self) -> bool:
        return any(item.quality != "OK" for item in self.observations)

    def as_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "source": self.source,
            "fetched_at": self.fetched_at_utc.isoformat(),
            "display_timezone": self.display_timezone,
            "quality": "FAILED" if self.has_quality_failure else "OK",
            "observations": [item.as_dict(display_timezone=self.display_timezone) for item in self.observations],
        }
