"""Deterministic normalization and data-quality checks for M03.

M03 consumes the immutable :class:`RawSnapshot` objects produced by M02 and
returns normalized daily bars plus explicit quality events.  It deliberately
does not fill gaps, change price bases, fetch data, calculate indicators, or
make a strategy decision.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .market_data import (
    DEFAULT_PRICE_FIELD_MAPPING,
    DataContractError,
    PriceFieldMapping,
    RawSnapshot,
    SUPPORTED_SYMBOLS,
    _as_iso_date,
    _as_utc_timestamp,
    _canonical_json,
    map_price_record,
)


QUALITY_STATUSES = ("OK", "PARTIAL", "STALE", "FAILED", "NEEDS_REVIEW")
_QUALITY_SET = frozenset(QUALITY_STATUSES)
_QUALITY_PRIORITY = {
    "OK": 0,
    "PARTIAL": 1,
    "STALE": 2,
    "FAILED": 3,
    "NEEDS_REVIEW": 4,
}
_EVENT_SEVERITIES = frozenset({"info", "warning", "error"})


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DataContractError(message)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _quality_max(*statuses: str) -> str:
    present = [status for status in statuses if status]
    return max(present, key=lambda status: _QUALITY_PRIORITY[status]) if present else "OK"


def _as_number(value: Any, field_name: str, *, positive: bool = False) -> float:
    _require(not isinstance(value, bool), f"{field_name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DataContractError(f"{field_name} must be numeric") from exc
    _require(isfinite(number), f"{field_name} must be finite")
    if positive:
        _require(number > 0.0, f"{field_name} must be positive")
    return number


def _date_range(start: date, end: date) -> tuple[date, ...]:
    days = (end - start).days
    return tuple(start + timedelta(days=offset) for offset in range(days + 1))


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (occurrence - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last = next_month - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    """Return Gregorian Easter using the Meeus/Jones/Butcher algorithm."""

    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    actual = date(year, month, day)
    if actual.weekday() == 5:
        return actual - timedelta(days=1)
    if actual.weekday() == 6:
        return actual + timedelta(days=1)
    return actual


def _nyse_holidays(year: int) -> frozenset[date]:
    holidays: set[date] = set()
    # Include adjacent years so observed New Year's/Christmas closures that
    # cross a calendar boundary are represented in a session query.
    for holiday_year in (year - 1, year, year + 1):
        holidays.add(_observed_fixed_holiday(holiday_year, 1, 1))
        holidays.add(_observed_fixed_holiday(holiday_year, 7, 4))
        holidays.add(_observed_fixed_holiday(holiday_year, 12, 25))
        if holiday_year >= 2022:
            holidays.add(_observed_fixed_holiday(holiday_year, 6, 19))
    holidays.update(
        {
            _nth_weekday(year, 1, 0, 3),   # Martin Luther King Jr. Day
            _nth_weekday(year, 2, 0, 3),   # Washington's Birthday
            _easter_sunday(year) - timedelta(days=2),  # Good Friday
            _last_weekday(year, 5, 0),      # Memorial Day
            _nth_weekday(year, 9, 0, 1),    # Labor Day
            _nth_weekday(year, 11, 3, 4),  # Thanksgiving Day
        }
    )
    return frozenset(holidays)


@dataclass(frozen=True)
class TradingCalendar:
    """A deterministic NYSE session calendar with explicit extra closures."""

    exchange: str = "NYSE"
    timezone: str = "America/New_York"
    extra_closed_dates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(isinstance(self.exchange, str) and self.exchange.strip(), "calendar.exchange must be non-empty")
        _require(self.exchange.strip().upper() == "NYSE", "M03 currently supports the NYSE calendar only")
        _require(isinstance(self.timezone, str) and self.timezone.strip(), "calendar.timezone must be non-empty")
        try:
            ZoneInfo(self.timezone.strip())
        except ZoneInfoNotFoundError as exc:
            raise DataContractError(f"unknown calendar timezone: {self.timezone}") from exc
        normalized = tuple(sorted({_as_iso_date(value, "calendar.extra_closed_dates") for value in self.extra_closed_dates}))
        object.__setattr__(self, "exchange", self.exchange.strip().upper())
        object.__setattr__(self, "timezone", self.timezone.strip())
        object.__setattr__(self, "extra_closed_dates", normalized)

    @property
    def calendar_id(self) -> str:
        return f"{self.exchange}-daily-v1"

    def is_trading_day(self, value: date | str) -> bool:
        day = date.fromisoformat(value) if isinstance(value, str) else value
        _require(isinstance(day, date), "calendar date must be a date or YYYY-MM-DD")
        if day.weekday() >= 5:
            return False
        if day.isoformat() in self.extra_closed_dates:
            return False
        return day not in _nyse_holidays(day.year)

    def sessions(self, start_date: str, end_date: str) -> tuple[str, ...]:
        start = date.fromisoformat(_as_iso_date(start_date, "calendar.start_date"))
        end = date.fromisoformat(_as_iso_date(end_date, "calendar.end_date"))
        _require(start <= end, "calendar.start_date must not be after end_date")
        return tuple(day.isoformat() for day in _date_range(start, end) if self.is_trading_day(day))


DEFAULT_LISTING_DATES = {
    "QQQ": "1999-03-10",
    "QLD": "2006-06-21",
    "VOO": "2010-09-09",
    "SPY": "1993-01-29",
    "BIL": "2007-05-25",
    "TLT": "2002-07-22",
    "IAU": "2005-01-21",
    "XLU": "1998-12-16",
    "SVXY": "2011-10-03",
    "VIX": "1990-01-02",
    "VIX3M": "2007-01-22",
}


@dataclass(frozen=True)
class ListingRegistry:
    """Explicit first-available dates; dates before them are never filled."""

    first_available: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_LISTING_DATES))

    def __post_init__(self) -> None:
        _require(isinstance(self.first_available, Mapping), "listing registry must be an object")
        normalized: dict[str, str] = {}
        for symbol, first_date in self.first_available.items():
            normalized[str(symbol).strip().upper()] = _as_iso_date(first_date, f"listing.{symbol}")
        unknown = sorted(set(normalized) - set(SUPPORTED_SYMBOLS))
        _require(not unknown, f"listing registry contains unsupported symbols: {unknown}")
        object.__setattr__(self, "first_available", normalized)

    def first_date(self, symbol: str) -> str:
        normalized = symbol.strip().upper()
        _require(normalized in self.first_available, f"listing date is missing for {normalized}")
        return self.first_available[normalized]


@dataclass(frozen=True)
class NormalizationConfig:
    """Fixed inputs controlling a normalization run."""

    as_of: str | datetime
    calendar: TradingCalendar = field(default_factory=TradingCalendar)
    listing_registry: ListingRegistry = field(default_factory=ListingRegistry)
    mapping: PriceFieldMapping = field(default_factory=lambda: DEFAULT_PRICE_FIELD_MAPPING)
    max_staleness: timedelta = timedelta(days=3)
    max_missing_sessions: int = 3
    price_tolerance: float = 1e-6
    require_volume: bool = False
    normalization_version: str = "m03-normalized-bars/v1"

    def __post_init__(self) -> None:
        normalized_as_of = _as_utc_timestamp(self.as_of, "as_of")
        _require(isinstance(self.calendar, TradingCalendar), "calendar must be a TradingCalendar")
        _require(isinstance(self.listing_registry, ListingRegistry), "listing_registry must be a ListingRegistry")
        _require(isinstance(self.mapping, PriceFieldMapping), "mapping must be a PriceFieldMapping")
        _require(isinstance(self.max_staleness, timedelta) and self.max_staleness >= timedelta(0), "max_staleness must be non-negative")
        _require(isinstance(self.max_missing_sessions, int) and self.max_missing_sessions >= 1, "max_missing_sessions must be positive")
        _require(isinstance(self.price_tolerance, (int, float)) and self.price_tolerance >= 0, "price_tolerance must be non-negative")
        _require(isinstance(self.require_volume, bool), "require_volume must be boolean")
        _require(isinstance(self.normalization_version, str) and self.normalization_version.strip(), "normalization_version must be non-empty")
        object.__setattr__(self, "as_of", normalized_as_of)
        object.__setattr__(self, "normalization_version", self.normalization_version.strip())


@dataclass(frozen=True)
class QualityEvent:
    event_type: str
    status: str
    severity: str
    message: str
    source: str | None = None
    symbol: str | None = None
    bar_date: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    snapshot_ids: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require(isinstance(self.event_type, str) and self.event_type.strip(), "quality event type must be non-empty")
        _require(self.status in _QUALITY_SET, f"unsupported quality event status: {self.status}")
        _require(self.severity in _EVENT_SEVERITIES, f"unsupported quality event severity: {self.severity}")
        _require(isinstance(self.message, str) and self.message.strip(), "quality event message must be non-empty")
        if self.bar_date is not None:
            _as_iso_date(self.bar_date, "quality event bar_date")
        if self.window_start is not None:
            _as_iso_date(self.window_start, "quality event window_start")
        if self.window_end is not None:
            _as_iso_date(self.window_end, "quality event window_end")
        _require(isinstance(self.snapshot_ids, Sequence) and not isinstance(self.snapshot_ids, (str, bytes)), "snapshot_ids must be a sequence")
        _require(isinstance(self.details, Mapping), "quality event details must be an object")

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "status": self.status,
            "severity": self.severity,
            "message": self.message,
            "source": self.source,
            "symbol": self.symbol,
            "bar_date": self.bar_date,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "snapshot_ids": list(self.snapshot_ids),
            "details": copy.deepcopy(dict(self.details)),
        }


@dataclass(frozen=True)
class NormalizedBar:
    symbol: str
    bar_date: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    sources: tuple[str, ...]
    snapshot_ids: tuple[str, ...]
    retrieved_at_by_source: tuple[tuple[str, str], ...]
    price_basis: str
    timezone: str
    quality: str

    def __post_init__(self) -> None:
        _require(self.symbol in set(SUPPORTED_SYMBOLS), f"unsupported normalized symbol: {self.symbol}")
        _as_iso_date(self.bar_date, "normalized.bar_date")
        for field_name in ("open", "high", "low", "close"):
            _as_number(getattr(self, field_name), f"normalized.{field_name}", positive=True)
        if self.volume is not None:
            _as_number(self.volume, "normalized.volume")
        _require(self.sources and all(isinstance(source, str) and source for source in self.sources), "normalized sources are required")
        _require(self.snapshot_ids and all(isinstance(value, str) and value for value in self.snapshot_ids), "normalized snapshot ids are required")
        _require(self.quality in _QUALITY_SET, f"unsupported normalized quality: {self.quality}")
        _require(self.price_basis in {"adjusted_ohlcv", "unadjusted_ohlcv", "index_level"}, "normalized price basis is invalid")
        _require(isinstance(self.retrieved_at_by_source, Sequence) and self.retrieved_at_by_source, "normalized retrieval provenance is required")
        for source, retrieved_at in self.retrieved_at_by_source:
            _require(source in self.sources, "retrieval provenance source is not represented in sources")
            _as_utc_timestamp(retrieved_at, "normalized.retrieved_at")

    @property
    def source(self) -> str:
        return self.sources[0] if len(self.sources) == 1 else ",".join(self.sources)

    @property
    def allows_confirmed(self) -> bool:
        return self.quality == "OK"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "qqq-normalized-bar/v1",
            "symbol": self.symbol,
            "bar_date": self.bar_date,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "source": self.source,
            "sources": list(self.sources),
            "snapshot_ids": list(self.snapshot_ids),
            "retrieved_at_by_source": [list(item) for item in self.retrieved_at_by_source],
            "price_basis": self.price_basis,
            "timezone": self.timezone,
            "quality": self.quality,
            "allows_confirmed": self.allows_confirmed,
        }


@dataclass(frozen=True)
class NormalizationResult:
    as_of: str
    calendar_id: str
    normalization_version: str
    quality: str
    bars: tuple[NormalizedBar, ...]
    quality_events: tuple[QualityEvent, ...]

    def __post_init__(self) -> None:
        _as_utc_timestamp(self.as_of, "result.as_of")
        _require(self.quality in _QUALITY_SET, f"unsupported result quality: {self.quality}")
        _require(isinstance(self.bars, Sequence), "result.bars must be a sequence")
        _require(isinstance(self.quality_events, Sequence), "result.quality_events must be a sequence")
        _require(isinstance(self.calendar_id, str) and self.calendar_id, "result.calendar_id must be non-empty")
        _require(isinstance(self.normalization_version, str) and self.normalization_version, "result.normalization_version must be non-empty")

    @property
    def allows_confirmed(self) -> bool:
        return self.quality == "OK" and all(bar.allows_confirmed for bar in self.bars)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "qqq-normalization-result/v1",
            "as_of": self.as_of,
            "calendar_id": self.calendar_id,
            "normalization_version": self.normalization_version,
            "quality": self.quality,
            "allows_confirmed": self.allows_confirmed,
            "bars": [bar.as_dict() for bar in self.bars],
            "quality_events": [event.as_dict() for event in self.quality_events],
        }

    @property
    def content_hash(self) -> str:
        return _sha256_text(_canonical_json(self.as_dict()))

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.as_dict()).encode("utf-8")


@dataclass(frozen=True)
class _Candidate:
    row: Mapping[str, Any]
    source: str
    snapshot_id: str
    retrieved_at: str
    quality: str
    price_basis: str
    timezone: str


def _as_of_local_date(as_of: str, timezone_name: str) -> date:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise DataContractError(f"unknown request timezone: {timezone_name}") from exc
    candidate = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    return candidate.astimezone(zone).date()


def _extract_records(payload: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(payload, Mapping):
        _require("bars" in payload, "raw payload must contain an explicit bars field")
        records = payload["bars"]
    else:
        records = payload
    _require(isinstance(records, Sequence) and not isinstance(records, (str, bytes)), "raw bars must be a sequence")
    return tuple(records)


def _adjustment_issue(record: Mapping[str, Any], mapping: PriceFieldMapping, tolerance: float) -> str | None:
    alternate_close_names = ("adj_close", "adjusted_close", "adjustedClose")
    if any(name in record and mapping.close != name for name in alternate_close_names):
        return "provider supplied an alternate adjusted close without an explicit mapping"
    for factor_name in ("adjustment_factor", "adjustmentFactor", "split_factor", "dividend_factor"):
        if factor_name not in record or record[factor_name] is None:
            continue
        factor = _as_number(record[factor_name], f"provider.{factor_name}", positive=True)
        if abs(factor - 1.0) > tolerance:
            return "provider supplied a non-neutral adjustment factor without an approved normalization rule"
    return None


def _price_issue(row: Mapping[str, Any]) -> str | None:
    values = {field_name: _as_number(row[field_name], field_name, positive=True) for field_name in ("open", "high", "low", "close")}
    if values["high"] < max(values["open"], values["close"], values["low"]):
        return "high is below another OHLC value"
    if values["low"] > min(values["open"], values["close"], values["high"]):
        return "low is above another OHLC value"
    if "volume" in row and row["volume"] is not None:
        _as_number(row["volume"], "volume")
        if float(row["volume"]) < 0:
            return "volume cannot be negative"
    return None


def _same_price(left: Mapping[str, Any], right: Mapping[str, Any], tolerance: float) -> bool:
    return all(abs(float(left[field]) - float(right[field])) <= tolerance for field in ("open", "high", "low", "close"))


def _contiguous_runs(values: Sequence[str], calendar: TradingCalendar) -> tuple[tuple[str, str, int], ...]:
    if not values:
        return ()
    ordered = tuple(sorted(values))
    runs: list[tuple[str, str, int]] = []
    run_start = ordered[0]
    previous = ordered[0]
    count = 1
    for current in ordered[1:]:
        previous_sessions = calendar.sessions(previous, current)
        if len(previous_sessions) == 2 and previous_sessions[0] == previous and previous_sessions[1] == current:
            previous = current
            count += 1
            continue
        runs.append((run_start, previous, count))
        run_start = current
        previous = current
        count = 1
    runs.append((run_start, previous, count))
    return tuple(runs)


def _event_sort_key(event: QualityEvent) -> tuple[str, str, str, str, str]:
    return (
        event.event_type,
        event.symbol or "",
        event.bar_date or event.window_start or "",
        event.source or "",
        ",".join(event.snapshot_ids),
    )


def normalize_snapshots(
    snapshots: Sequence[RawSnapshot],
    *,
    as_of: str | datetime,
    calendar: TradingCalendar | None = None,
    listing_registry: ListingRegistry | Mapping[str, str] | None = None,
    mapping: PriceFieldMapping = DEFAULT_PRICE_FIELD_MAPPING,
    max_staleness: timedelta = timedelta(days=3),
    max_missing_sessions: int = 3,
    price_tolerance: float = 1e-6,
    require_volume: bool = False,
) -> NormalizationResult:
    """Normalize raw snapshots without filling or silently selecting bad data."""

    if listing_registry is None:
        normalized_listing = ListingRegistry()
    elif isinstance(listing_registry, ListingRegistry):
        normalized_listing = listing_registry
    else:
        normalized_listing = ListingRegistry(listing_registry)
    config = NormalizationConfig(
        as_of=as_of,
        calendar=calendar or TradingCalendar(),
        listing_registry=normalized_listing,
        mapping=mapping,
        max_staleness=max_staleness,
        max_missing_sessions=max_missing_sessions,
        price_tolerance=price_tolerance,
        require_volume=require_volume,
    )
    snapshot_tuple = tuple(snapshots)
    _require(all(isinstance(snapshot, RawSnapshot) for snapshot in snapshot_tuple), "snapshots must contain RawSnapshot objects")
    events: list[QualityEvent] = []
    candidates: list[_Candidate] = []

    if not snapshot_tuple:
        events.append(QualityEvent("no_snapshots", "FAILED", "error", "no raw snapshots were supplied"))
        return NormalizationResult(config.as_of, config.calendar.calendar_id, config.normalization_version, "FAILED", (), tuple(events))

    request_fingerprint: tuple[Any, ...] | None = None
    request_symbols: tuple[str, ...] = ()
    request_start = request_end = ""
    request_timezone = ""
    request_price_basis = ""

    for snapshot in snapshot_tuple:
        request = dict(snapshot.request)
        try:
            symbols = tuple(sorted(str(symbol).strip().upper() for symbol in request["symbols"]))
            start_date = _as_iso_date(request["start_date"], "request.start_date")
            end_date = _as_iso_date(request["end_date"], "request.end_date")
            timezone_name = str(request["timezone"])
            price_basis = str(request["price_basis"])
            fingerprint = (symbols, start_date, end_date, request.get("interval"), price_basis, timezone_name, request.get("exchange"))
        except (KeyError, TypeError, DataContractError) as exc:
            events.append(QualityEvent("request_metadata_invalid", "NEEDS_REVIEW", "error", "raw snapshot request metadata is invalid", source=snapshot.source, snapshot_ids=(snapshot.snapshot_id,), details={"error": str(exc)}))
            continue

        if request_fingerprint is None:
            request_fingerprint = fingerprint
            request_symbols = symbols
            request_start, request_end = start_date, end_date
            request_timezone = timezone_name
            request_price_basis = price_basis
            if request_timezone != config.calendar.timezone:
                events.append(QualityEvent("calendar_timezone_mismatch", "NEEDS_REVIEW", "error", "request timezone does not match the trading calendar timezone", source=snapshot.source, snapshot_ids=(snapshot.snapshot_id,), details={"request_timezone": request_timezone, "calendar_timezone": config.calendar.timezone}))
        elif fingerprint != request_fingerprint:
            events.append(QualityEvent("request_mismatch", "NEEDS_REVIEW", "error", "raw snapshots cannot be merged because request semantics differ", source=snapshot.source, snapshot_ids=(snapshot.snapshot_id,), details={"expected": request_fingerprint, "actual": fingerprint}))
            continue

        if snapshot.price_basis != request_price_basis or snapshot.timezone != request_timezone:
            events.append(QualityEvent("snapshot_metadata_mismatch", "NEEDS_REVIEW", "error", "snapshot price basis or timezone does not match its request", source=snapshot.source, snapshot_ids=(snapshot.snapshot_id,), details={"request_price_basis": request_price_basis, "snapshot_price_basis": snapshot.price_basis, "request_timezone": request_timezone, "snapshot_timezone": snapshot.timezone}))
            continue

        if snapshot.status == "failed":
            events.append(QualityEvent("snapshot_failed", "FAILED", "error", "raw provider snapshot failed", source=snapshot.source, snapshot_ids=(snapshot.snapshot_id,), details={"error_code": snapshot.error_code}))
            continue

        retrieved_at = _as_utc_timestamp(snapshot.retrieved_at, "snapshot.retrieved_at")
        as_of_dt = datetime.fromisoformat(config.as_of.replace("Z", "+00:00"))
        retrieved_dt = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
        if retrieved_dt > as_of_dt:
            events.append(QualityEvent("future_retrieval", "NEEDS_REVIEW", "error", "snapshot retrieval time is after the normalization as_of", source=snapshot.source, snapshot_ids=(snapshot.snapshot_id,), details={"retrieved_at": retrieved_at, "as_of": config.as_of}))
            continue
        snapshot_quality = snapshot.quality
        age = as_of_dt - retrieved_dt
        if age > config.max_staleness:
            snapshot_quality = _quality_max(snapshot_quality, "STALE")
            events.append(QualityEvent("stale_snapshot", "STALE", "warning", "raw snapshot is older than the freshness window", source=snapshot.source, snapshot_ids=(snapshot.snapshot_id,), details={"age_seconds": int(age.total_seconds()), "max_staleness_seconds": int(config.max_staleness.total_seconds())}))
        elif snapshot.status == "partial":
            events.append(QualityEvent("partial_snapshot", "PARTIAL", "warning", "raw snapshot is explicitly partial", source=snapshot.source, snapshot_ids=(snapshot.snapshot_id,)))

        try:
            records = _extract_records(snapshot.payload)
        except DataContractError as exc:
            events.append(QualityEvent("payload_invalid", "NEEDS_REVIEW", "error", "raw payload does not contain a valid bars collection", source=snapshot.source, snapshot_ids=(snapshot.snapshot_id,), details={"error": str(exc)}))
            continue
        if not records:
            events.append(QualityEvent("payload_empty", "FAILED", "error", "raw snapshot contains no bars", source=snapshot.source, snapshot_ids=(snapshot.snapshot_id,)))
            continue

        local_candidates: list[_Candidate] = []
        local_keys: dict[tuple[str, str], int] = {}
        for record in records:
            if not isinstance(record, Mapping):
                events.append(QualityEvent("record_invalid", "NEEDS_REVIEW", "error", "raw bar record is not an object", source=snapshot.source, snapshot_ids=(snapshot.snapshot_id,)))
                continue
            try:
                adjustment_issue = _adjustment_issue(record, config.mapping, config.price_tolerance)
                if adjustment_issue:
                    raise DataContractError(adjustment_issue)
                row = map_price_record(record, mapping=config.mapping)
                symbol = row["symbol"]
                _require(symbol in request_symbols, f"provider record symbol {symbol} is not in the request")
                bar_date = row["bar_date"]
                local_date = date.fromisoformat(bar_date)
                as_of_local = _as_of_local_date(config.as_of, request_timezone)
                _require(request_start <= bar_date <= request_end, "bar date is outside the request window")
                _require(local_date <= as_of_local, "bar date is after the normalization as_of")
                if not config.calendar.is_trading_day(local_date):
                    raise DataContractError("bar date is not an NYSE trading day")
                if local_date < date.fromisoformat(config.listing_registry.first_date(symbol)):
                    raise DataContractError("bar date is before the asset first-available date")
                price_issue = _price_issue(row)
                if price_issue:
                    raise DataContractError(price_issue)
                volume = row.get("volume")
                if config.require_volume and volume is None:
                    snapshot_quality = _quality_max(snapshot_quality, "PARTIAL")
                    events.append(QualityEvent("volume_missing", "PARTIAL", "warning", "bar has no volume while volume is required", source=snapshot.source, symbol=symbol, bar_date=bar_date, snapshot_ids=(snapshot.snapshot_id,)))
                key = (symbol, bar_date)
                local_keys[key] = local_keys.get(key, 0) + 1
                local_candidates.append(_Candidate(row, snapshot.source, snapshot.snapshot_id, retrieved_at, snapshot_quality, request_price_basis, request_timezone))
            except (DataContractError, KeyError, TypeError, ValueError) as exc:
                message = str(exc)
                event_type = "record_invalid"
                if "not an NYSE trading day" in message:
                    event_type = "non_trading_day"
                elif "before the asset first-available date" in message:
                    event_type = "pre_listing_date"
                elif "after the normalization as_of" in message:
                    event_type = "future_bar"
                elif "adjustment" in message or "adjusted close" in message:
                    event_type = "adjustment_metadata_conflict"
                elif "OHLC" in message or "volume" in message or "positive" in message or "finite" in message:
                    event_type = "abnormal_price"
                events.append(QualityEvent(event_type, "NEEDS_REVIEW", "error", message or "raw bar record is invalid", source=snapshot.source, snapshot_ids=(snapshot.snapshot_id,), details={"record_keys": sorted(str(key) for key in record.keys())}))

        duplicate_keys = {key for key, count in local_keys.items() if count > 1}
        for symbol, bar_date in sorted(duplicate_keys):
            events.append(QualityEvent("duplicate_bar", "NEEDS_REVIEW", "error", "a source returned more than one bar for the same symbol and date", source=snapshot.source, symbol=symbol, bar_date=bar_date, snapshot_ids=(snapshot.snapshot_id,)))
        candidates.extend(candidate for candidate in local_candidates if (candidate.row["symbol"], candidate.row["bar_date"]) not in duplicate_keys)

    grouped: dict[tuple[str, str], list[_Candidate]] = {}
    for candidate in candidates:
        grouped.setdefault((candidate.row["symbol"], candidate.row["bar_date"]), []).append(candidate)

    bars: list[NormalizedBar] = []
    for (symbol, bar_date), group in sorted(grouped.items()):
        sources = tuple(sorted({candidate.source for candidate in group}))
        if len(sources) != len(group):
            events.append(QualityEvent("duplicate_bar", "NEEDS_REVIEW", "error", "the same source supplied duplicate bars across snapshots", symbol=symbol, bar_date=bar_date, snapshot_ids=tuple(sorted(candidate.snapshot_id for candidate in group)), details={"sources": list(sources)}))
            continue
        reference = sorted(group, key=lambda candidate: (candidate.source, candidate.snapshot_id))[0]
        if any(candidate.price_basis != reference.price_basis or candidate.timezone != reference.timezone for candidate in group):
            events.append(QualityEvent("cross_source_basis_conflict", "NEEDS_REVIEW", "error", "cross-source bars use different price basis or timezone", symbol=symbol, bar_date=bar_date, snapshot_ids=tuple(sorted(candidate.snapshot_id for candidate in group))))
            continue
        if any(not _same_price(reference.row, candidate.row, config.price_tolerance) for candidate in group[1:]):
            events.append(QualityEvent("cross_source_price_conflict", "NEEDS_REVIEW", "error", "cross-source OHLC values differ beyond the configured tolerance", symbol=symbol, bar_date=bar_date, snapshot_ids=tuple(sorted(candidate.snapshot_id for candidate in group)), details={"price_tolerance": config.price_tolerance, "sources": list(sources)}))
            continue
        retrieved_by_source = tuple((candidate.source, candidate.retrieved_at) for candidate in sorted(group, key=lambda candidate: candidate.source))
        volume = reference.row.get("volume")
        bar_quality = _quality_max(*(candidate.quality for candidate in group))
        bars.append(NormalizedBar(symbol, bar_date, float(reference.row["open"]), float(reference.row["high"]), float(reference.row["low"]), float(reference.row["close"]), None if volume is None else float(volume), sources, tuple(sorted(candidate.snapshot_id for candidate in group)), retrieved_by_source, reference.price_basis, reference.timezone, bar_quality))

    if request_fingerprint is not None:
        observed = {(bar.symbol, bar.bar_date) for bar in bars}
        for symbol in request_symbols:
            listing_date = date.fromisoformat(config.listing_registry.first_date(symbol))
            expected = [
                session
                for session in config.calendar.sessions(request_start, request_end)
                if date.fromisoformat(session) >= listing_date
            ]
            missing = [session for session in expected if (symbol, session) not in observed]
            for window_start, window_end, count in _contiguous_runs(missing, config.calendar):
                status = "NEEDS_REVIEW" if count >= config.max_missing_sessions else "PARTIAL"
                severity = "error" if status == "NEEDS_REVIEW" else "warning"
                events.append(QualityEvent("missing_window" if count > 1 else "missing_bar", status, severity, "expected trading sessions are missing; no values were filled", symbol=symbol, window_start=window_start, window_end=window_end, details={"missing_sessions": count, "expected_sessions": len(expected)}))

    ordered_events = tuple(sorted(events, key=_event_sort_key))
    event_quality = _quality_max(*(event.status for event in ordered_events))
    if not bars and event_quality == "OK":
        event_quality = "FAILED"
    result_quality = event_quality
    ordered_bars = tuple(sorted(bars, key=lambda bar: (bar.symbol, bar.bar_date)))
    return NormalizationResult(config.as_of, config.calendar.calendar_id, config.normalization_version, result_quality, ordered_bars, ordered_events)


__all__ = [
    "DEFAULT_LISTING_DATES",
    "ListingRegistry",
    "NormalizedBar",
    "NormalizationConfig",
    "NormalizationResult",
    "QualityEvent",
    "QUALITY_STATUSES",
    "TradingCalendar",
    "normalize_snapshots",
]
