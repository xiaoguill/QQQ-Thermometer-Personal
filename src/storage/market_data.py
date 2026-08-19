"""Read-only provider boundary and immutable raw market-data snapshots.

M02 deliberately stops at the raw-data boundary.  It does not calculate
indicators, alter strategy state, persist to a database, or place orders.
Network access is supplied by an injected transport so imports and tests are
deterministic and cannot accidentally contact a provider.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping, Protocol, Sequence


SUPPORTED_SYMBOLS = (
    "QQQ",
    "QLD",
    "VOO",
    "SPY",
    "BIL",
    "TLT",
    "IAU",
    "XLU",
    "SVXY",
    "VXX",
    "VIX",
    "VIX3M",
)
_SUPPORTED_SYMBOL_SET = frozenset(SUPPORTED_SYMBOLS)
_INDEX_SYMBOLS = frozenset({"VIX", "VIX3M"})
_PRICE_BASES = frozenset({"adjusted_ohlcv", "unadjusted_ohlcv", "index_level"})
_SNAPSHOT_STATUSES = frozenset({"success", "partial", "failed"})
_QUALITY_BY_STATUS = {"success": "OK", "partial": "PARTIAL", "failed": "FAILED"}


class DataContractError(ValueError):
    """Raised when a data request, response, or snapshot violates the contract."""


class DuplicateSnapshotError(DataContractError):
    """Raised instead of silently replacing or deduplicating a raw snapshot."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DataContractError(message)


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
        raise DataContractError("value must be JSON-serializable without NaN or Infinity") from exc


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_iso_date(value: Any, field_name: str) -> str:
    _require(isinstance(value, str), f"{field_name} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise DataContractError(f"{field_name} must be YYYY-MM-DD") from exc
    _require(parsed.isoformat() == value, f"{field_name} must be YYYY-MM-DD")
    return value


def _as_utc_timestamp(value: Any, field_name: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        _require(isinstance(value, str), f"{field_name} must be an ISO-8601 timestamp")
        candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise DataContractError(f"{field_name} must be an ISO-8601 timestamp") from exc
    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None, f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_non_empty(value: Any, field_name: str) -> str:
    _require(isinstance(value, str) and value.strip(), f"{field_name} must be non-empty")
    return value.strip()


def _is_empty_payload(value: Any) -> bool:
    return value is None or value == [] or value == {}


def _reject_sensitive_parameter_names(parameters: Mapping[str, Any]) -> None:
    sensitive_fragments = ("api_key", "apikey", "token", "password", "secret", "authorization")
    for key in parameters:
        normalized = str(key).lower().replace("-", "_")
        _require(
            not any(fragment in normalized for fragment in sensitive_fragments),
            "provider_params cannot contain credentials or authorization material",
        )


@dataclass(frozen=True)
class MarketDataRequest:
    """A bounded daily-data request with explicit source and price semantics."""

    source: str
    symbols: tuple[str, ...]
    start_date: str
    end_date: str
    interval: str = "1d"
    price_basis: str = "adjusted_ohlcv"
    timezone: str = "America/New_York"
    exchange: str = "NYSE"
    provider_params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        source = _safe_non_empty(self.source, "source")
        _require(isinstance(self.symbols, Sequence) and not isinstance(self.symbols, (str, bytes)), "symbols must be a non-empty sequence")
        normalized_symbols = tuple(sorted({str(symbol).strip().upper() for symbol in self.symbols}))
        _require(normalized_symbols, "symbols must be non-empty")
        unknown = sorted(set(normalized_symbols) - _SUPPORTED_SYMBOL_SET)
        _require(not unknown, f"unsupported symbols: {unknown}")
        start_date = _as_iso_date(self.start_date, "start_date")
        end_date = _as_iso_date(self.end_date, "end_date")
        _require(start_date <= end_date, "start_date must not be after end_date")
        _require(self.interval == "1d", "M02 only permits daily interval 1d")
        _require(self.price_basis in _PRICE_BASES, f"unsupported price_basis: {self.price_basis}")
        _safe_non_empty(self.timezone, "timezone")
        _safe_non_empty(self.exchange, "exchange")
        _require(isinstance(self.provider_params, Mapping), "provider_params must be an object")
        provider_params = copy.deepcopy(dict(self.provider_params))
        _reject_sensitive_parameter_names(provider_params)
        _canonical_json(provider_params)

        includes_index = bool(set(normalized_symbols) & _INDEX_SYMBOLS)
        includes_etf = bool(set(normalized_symbols) - _INDEX_SYMBOLS)
        _require(not (includes_index and includes_etf), "one request cannot mix index and ETF price bases")
        if includes_index:
            _require(self.price_basis == "index_level", "VIX/VIX3M requests must use index_level")
        else:
            _require(self.price_basis != "index_level", "ETF requests cannot use index_level")

        object.__setattr__(self, "source", source)
        object.__setattr__(self, "symbols", normalized_symbols)
        object.__setattr__(self, "start_date", start_date)
        object.__setattr__(self, "end_date", end_date)
        object.__setattr__(self, "timezone", self.timezone.strip())
        object.__setattr__(self, "exchange", self.exchange.strip())
        object.__setattr__(self, "provider_params", provider_params)

    def as_dict(self, *, include_request_id: bool = True) -> dict[str, Any]:
        result = {
            "source": self.source,
            "symbols": list(self.symbols),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "interval": self.interval,
            "price_basis": self.price_basis,
            "timezone": self.timezone,
            "exchange": self.exchange,
            "provider_params": copy.deepcopy(dict(self.provider_params)),
        }
        if include_request_id:
            result["request_id"] = _sha256_text(_canonical_json(self.as_dict(include_request_id=False)))
        return result

    @property
    def request_id(self) -> str:
        return self.as_dict()["request_id"]


@dataclass(frozen=True)
class SourceResponse:
    """Provider response supplied by an injected transport."""

    status_code: int
    payload: Any = None
    content_type: str = "application/json"
    retrieved_at: str | None = None
    provider_request_id: str | None = None

    def __post_init__(self) -> None:
        _require(
            isinstance(self.status_code, int) and not isinstance(self.status_code, bool),
            "status_code must be an integer",
        )
        _require(100 <= self.status_code <= 599, "status_code must be between 100 and 599")
        _safe_non_empty(self.content_type, "content_type")
        if self.retrieved_at is not None:
            _as_utc_timestamp(self.retrieved_at, "retrieved_at")
        if self.provider_request_id is not None:
            _safe_non_empty(self.provider_request_id, "provider_request_id")

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300


@dataclass(frozen=True)
class PriceFieldMapping:
    """Provider-to-raw-record field names; values are not adjusted or inferred."""

    symbol: str = "symbol"
    bar_date: str = "date"
    open: str = "open"
    high: str = "high"
    low: str = "low"
    close: str = "close"
    volume: str | None = "volume"

    def __post_init__(self) -> None:
        for field_name in ("symbol", "bar_date", "open", "high", "low", "close"):
            _safe_non_empty(getattr(self, field_name), f"mapping.{field_name}")
        if self.volume is not None:
            _safe_non_empty(self.volume, "mapping.volume")


DEFAULT_PRICE_FIELD_MAPPING = PriceFieldMapping()


def map_price_record(
    record: Mapping[str, Any],
    *,
    mapping: PriceFieldMapping = DEFAULT_PRICE_FIELD_MAPPING,
    expected_symbol: str | None = None,
) -> dict[str, Any]:
    """Map provider keys without changing numeric values or price basis."""

    _require(isinstance(record, Mapping), "provider price record must be an object")

    def required_value(field_name: str) -> Any:
        source_key = getattr(mapping, field_name)
        _require(source_key in record and record[source_key] is not None, f"provider record missing {source_key}")
        return copy.deepcopy(record[source_key])

    symbol = str(required_value("symbol")).strip().upper()
    _require(symbol in _SUPPORTED_SYMBOL_SET, f"unsupported symbols: ['{symbol}']")
    if expected_symbol is not None:
        _require(symbol == expected_symbol.strip().upper(), "provider record symbol does not match request")
    result = {
        "symbol": symbol,
        "bar_date": _as_iso_date(required_value("bar_date"), "provider.bar_date"),
        "open": required_value("open"),
        "high": required_value("high"),
        "low": required_value("low"),
        "close": required_value("close"),
    }
    if mapping.volume is not None and mapping.volume in record and record[mapping.volume] is not None:
        result["volume"] = copy.deepcopy(record[mapping.volume])
    return result


@dataclass(frozen=True)
class RawSnapshot:
    """Immutable raw provider response with provenance and content hash."""

    snapshot_id: str
    source: str
    request: Mapping[str, Any]
    retrieved_at: str
    status: str
    quality: str
    price_basis: str
    timezone: str
    content_type: str
    payload_json: str | None
    payload_sha256: str | None
    error_code: str | None = None
    error_message: str | None = None
    provider_request_id: str | None = None

    def __post_init__(self) -> None:
        _require(self.status in _SNAPSHOT_STATUSES, f"unsupported snapshot status: {self.status}")
        _require(self.quality == _QUALITY_BY_STATUS[self.status], "snapshot quality does not match status")
        _safe_non_empty(self.source, "source")
        _as_utc_timestamp(self.retrieved_at, "retrieved_at")
        _require(isinstance(self.request, Mapping), "snapshot request must be an object")
        _canonical_json(self.request)
        _require(self.price_basis in _PRICE_BASES, f"unsupported price_basis: {self.price_basis}")
        _safe_non_empty(self.timezone, "timezone")
        _safe_non_empty(self.content_type, "content_type")
        if self.payload_json is None:
            _require(self.payload_sha256 is None, "payload_sha256 requires payload_json")
        else:
            parsed = json.loads(self.payload_json)
            canonical = _canonical_json(parsed)
            _require(canonical == self.payload_json, "payload_json must be canonical JSON")
            _require(self.payload_sha256 == _sha256_text(self.payload_json), "payload_sha256 does not match payload_json")
        if self.status == "success":
            _require(self.payload_json is not None, "successful snapshot must contain payload")
            _require(self.error_code is None and self.error_message is None, "successful snapshot cannot contain an error")
        if self.status == "failed":
            _require(bool(self.error_code) and bool(self.error_message), "failed snapshot must contain an error")
        _require(self.snapshot_id == _sha256_text(_canonical_json(self._identity_record())), "snapshot_id does not match snapshot content")

    @classmethod
    def capture(
        cls,
        *,
        source: str,
        request: MarketDataRequest | Mapping[str, Any],
        retrieved_at: str | datetime,
        payload: Any = None,
        status: str = "success",
        price_basis: str | None = None,
        timezone_name: str | None = None,
        content_type: str = "application/json",
        error_code: str | None = None,
        error_message: str | None = None,
        provider_request_id: str | None = None,
    ) -> "RawSnapshot":
        _require(status in _SNAPSHOT_STATUSES, f"unsupported snapshot status: {status}")
        request_record = request.as_dict() if isinstance(request, MarketDataRequest) else copy.deepcopy(dict(request))
        _require(isinstance(request_record, Mapping), "snapshot request must be an object")
        payload_json = None if payload is None else _canonical_json(payload)
        payload_sha256 = None if payload_json is None else _sha256_text(payload_json)
        normalized_retrieved_at = _as_utc_timestamp(retrieved_at, "retrieved_at")
        normalized_price_basis = price_basis or str(request_record.get("price_basis") or "")
        normalized_timezone = timezone_name or str(request_record.get("timezone") or "")
        quality = _QUALITY_BY_STATUS[status]
        identity = {
            "schema": "qqq-raw-snapshot/v1",
            "source": source.strip(),
            "request": request_record,
            "retrieved_at": normalized_retrieved_at,
            "status": status,
            "quality": quality,
            "price_basis": normalized_price_basis,
            "timezone": normalized_timezone,
            "content_type": content_type,
            "payload_json": payload_json,
            "payload_sha256": payload_sha256,
            "error_code": error_code,
            "error_message": error_message,
            "provider_request_id": provider_request_id,
        }
        snapshot_id = _sha256_text(_canonical_json(identity))
        return cls(snapshot_id=snapshot_id, **{key: value for key, value in identity.items() if key != "schema"})

    @classmethod
    def failed(
        cls,
        *,
        source: str,
        request: MarketDataRequest,
        retrieved_at: str | datetime,
        error_code: str,
        error_message: str,
        payload: Any = None,
        provider_request_id: str | None = None,
    ) -> "RawSnapshot":
        return cls.capture(
            source=source,
            request=request,
            retrieved_at=retrieved_at,
            payload=payload,
            status="failed",
            error_code=error_code,
            error_message=error_message,
            provider_request_id=provider_request_id,
        )

    def _identity_record(self) -> dict[str, Any]:
        return {
            "schema": "qqq-raw-snapshot/v1",
            "source": self.source,
            "request": copy.deepcopy(dict(self.request)),
            "retrieved_at": _as_utc_timestamp(self.retrieved_at, "retrieved_at"),
            "status": self.status,
            "quality": self.quality,
            "price_basis": self.price_basis,
            "timezone": self.timezone,
            "content_type": self.content_type,
            "payload_json": self.payload_json,
            "payload_sha256": self.payload_sha256,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "provider_request_id": self.provider_request_id,
        }

    @property
    def payload(self) -> Any:
        return None if self.payload_json is None else copy.deepcopy(json.loads(self.payload_json))

    def as_record(self, *, include_payload: bool = True) -> dict[str, Any]:
        result = {
            "schema": "qqq-raw-snapshot/v1",
            "snapshot_id": self.snapshot_id,
            "source": self.source,
            "request": copy.deepcopy(dict(self.request)),
            "retrieved_at": self.retrieved_at,
            "status": self.status,
            "quality": self.quality,
            "price_basis": self.price_basis,
            "timezone": self.timezone,
            "content_type": self.content_type,
            "payload_sha256": self.payload_sha256,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "provider_request_id": self.provider_request_id,
        }
        if include_payload:
            result["payload"] = self.payload
        return result

    def manifest_entry(self) -> dict[str, Any]:
        request = copy.deepcopy(dict(self.request))
        return {
            "snapshot_id": self.snapshot_id,
            "source": self.source,
            "request_id": request.get("request_id"),
            "symbols": request.get("symbols", []),
            "start_date": request.get("start_date"),
            "end_date": request.get("end_date"),
            "price_basis": self.price_basis,
            "timezone": self.timezone,
            "retrieved_at": self.retrieved_at,
            "status": self.status,
            "quality": self.quality,
            "payload_sha256": self.payload_sha256,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class RawSnapshotManifest:
    """Append-only immutable references to raw snapshots."""

    created_at: str
    snapshots: tuple[RawSnapshot, ...]
    manifest_version: str = "1.0.0"

    def __post_init__(self) -> None:
        _as_utc_timestamp(self.created_at, "manifest.created_at")
        _require(self.snapshots, "manifest must contain at least one snapshot")
        ids = [snapshot.snapshot_id for snapshot in self.snapshots]
        _require(len(ids) == len(set(ids)), "manifest cannot contain duplicate snapshot ids")

    @classmethod
    def from_snapshots(
        cls,
        snapshots: Sequence[RawSnapshot],
        *,
        created_at: str | datetime,
    ) -> "RawSnapshotManifest":
        _require(isinstance(snapshots, Sequence) and not isinstance(snapshots, (str, bytes)), "snapshots must be a sequence")
        snapshot_tuple = tuple(snapshots)
        _require(all(isinstance(snapshot, RawSnapshot) for snapshot in snapshot_tuple), "manifest entries must be RawSnapshot objects")
        ordered = tuple(sorted(snapshot_tuple, key=lambda item: item.snapshot_id))
        return cls(created_at=_as_utc_timestamp(created_at, "manifest.created_at"), snapshots=ordered)

    @property
    def snapshot_ids(self) -> tuple[str, ...]:
        return tuple(snapshot.snapshot_id for snapshot in self.snapshots)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "qqq-raw-source-manifest/v1",
            "manifest_version": self.manifest_version,
            "created_at": self.created_at,
            "snapshot_count": len(self.snapshots),
            "snapshots": [snapshot.manifest_entry() for snapshot in self.snapshots],
        }

    @property
    def manifest_hash(self) -> str:
        return _sha256_text(_canonical_json(self.as_dict()))

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.as_dict()).encode("utf-8")

    def append(self, snapshot: RawSnapshot, *, created_at: str | datetime | None = None) -> "RawSnapshotManifest":
        _require(isinstance(snapshot, RawSnapshot), "manifest append requires a RawSnapshot")
        if snapshot.snapshot_id in self.snapshot_ids:
            raise DuplicateSnapshotError(f"snapshot already exists: {snapshot.snapshot_id}")
        timestamp = self.created_at if created_at is None else _as_utc_timestamp(created_at, "manifest.created_at")
        return RawSnapshotManifest.from_snapshots(self.snapshots + (snapshot,), created_at=timestamp)


class SourceTransport(Protocol):
    def __call__(self, request: MarketDataRequest) -> SourceResponse:
        """Return a provider response without exposing credentials to this module."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class JsonDataSourceAdapter:
    """A provider-neutral adapter with injected transport and no automatic retry."""

    source_name: str
    transport: SourceTransport
    clock: Callable[[], datetime | str] = _utc_now

    def __post_init__(self) -> None:
        _safe_non_empty(self.source_name, "source_name")
        _require(callable(self.transport), "transport must be callable")
        _require(callable(self.clock), "clock must be callable")

    def _capture_failure(
        self,
        request: MarketDataRequest,
        retrieved_at: str,
        error_code: str,
        error_message: str,
        *,
        payload: Any = None,
        provider_request_id: str | None = None,
    ) -> RawSnapshot:
        return RawSnapshot.failed(
            source=self.source_name,
            request=request,
            retrieved_at=retrieved_at,
            error_code=error_code,
            error_message=error_message,
            payload=payload,
            provider_request_id=provider_request_id,
        )

    def fetch(self, request: MarketDataRequest) -> RawSnapshot:
        _require(isinstance(request, MarketDataRequest), "fetch requires a MarketDataRequest")
        _require(request.source == self.source_name, "request source does not match adapter source")
        retrieved_at = _as_utc_timestamp(self.clock(), "adapter clock")
        try:
            response = self.transport(request)
        except Exception:
            return self._capture_failure(request, retrieved_at, "transport_error", "provider transport failed")
        if not isinstance(response, SourceResponse):
            return self._capture_failure(request, retrieved_at, "invalid_transport_response", "provider response was invalid")

        provider_request_id = response.provider_request_id
        response_time = retrieved_at
        if response.retrieved_at is not None:
            try:
                response_time = _as_utc_timestamp(response.retrieved_at, "provider retrieved_at")
            except DataContractError:
                return self._capture_failure(request, retrieved_at, "invalid_retrieved_at", "provider retrieval timestamp was invalid", provider_request_id=provider_request_id)

        if response.status_code == 429:
            return self._capture_failure(request, response_time, "rate_limited", "provider rate limit response", provider_request_id=provider_request_id)
        if not response.is_success:
            error_code = "provider_server_error" if response.status_code >= 500 else "provider_http_error"
            return self._capture_failure(request, response_time, error_code, "provider returned an unsuccessful response", payload=response.payload, provider_request_id=provider_request_id)
        if _is_empty_payload(response.payload):
            return self._capture_failure(request, response_time, "empty_payload", "provider returned no market-data payload", provider_request_id=provider_request_id)

        status = "partial" if response.status_code == 206 else "success"
        return RawSnapshot.capture(
            source=self.source_name,
            request=request,
            retrieved_at=response_time,
            payload=response.payload,
            status=status,
            content_type=response.content_type,
            provider_request_id=provider_request_id,
        )

    def fetch_many(self, requests: Sequence[MarketDataRequest]) -> tuple[RawSnapshot, ...]:
        _require(isinstance(requests, Sequence) and not isinstance(requests, (str, bytes)), "requests must be a sequence")
        return tuple(self.fetch(request) for request in requests)

    def fetch_manifest(
        self,
        requests: Sequence[MarketDataRequest],
        *,
        created_at: str | datetime,
    ) -> RawSnapshotManifest:
        return RawSnapshotManifest.from_snapshots(self.fetch_many(requests), created_at=created_at)
