"""Small, injectable Massive REST client for read-only observations."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import quote, urlencode, urljoin
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import RealtimeConfig
from .models import ObservationBatch, RealtimeObservation, RealtimeSymbol


class MassiveClientError(RuntimeError):
    """Base error for provider access or response validation."""


class MissingApiKeyError(MassiveClientError):
    """Raised before any provider request when the configured env var is absent."""


@dataclass(frozen=True)
class TransportResponse:
    status_code: int
    payload: dict[str, Any]
    headers: dict[str, str]


class JsonTransport(Protocol):
    def request(self, url: str, *, headers: dict[str, str], timeout: int) -> TransportResponse:
        ...


class _NoRedirectHandler(HTTPRedirectHandler):
    """Do not follow provider redirects, especially to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _open_request(request: Request, *, timeout: int):
    # Build lazily so importing the read-only adapter never initializes TLS.
    # This also keeps isolated verification processes able to run without a
    # network or certificate subsystem being touched.
    opener = build_opener(_NoRedirectHandler)
    return opener.open(request, timeout=timeout)


def _decode_payload(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _decode_success_payload(body: bytes) -> dict[str, Any]:
    """Decode a successful provider response without downgrading bad JSON to PARTIAL."""

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MassiveClientError("INVALID_PROVIDER_PAYLOAD") from exc
    if not isinstance(payload, dict):
        raise MassiveClientError("INVALID_PROVIDER_PAYLOAD")
    return payload


class UrllibJsonTransport:
    def request(self, url: str, *, headers: dict[str, str], timeout: int) -> TransportResponse:
        request = Request(url, method="GET", headers=headers)
        try:
            with _open_request(request, timeout=timeout) as response:  # noqa: S310 - HTTPS origin is validated
                payload = _decode_success_payload(response.read())
                return TransportResponse(
                    status_code=int(response.status),
                    payload=payload,
                    headers={key.lower(): value for key, value in response.headers.items()},
                )
        except HTTPError as exc:
            # HTTPError is also a response. Preserve only the status and a parsed
            # provider body so MassiveClient can classify 401/403/404/429 without
            # leaking credentials or raw response text to callers.
            try:
                body = exc.read()
            except OSError:
                body = b""
            response_headers = {
                key.lower(): value for key, value in exc.headers.items()
            } if exc.headers else {}
            return TransportResponse(
                status_code=int(exc.code),
                payload=_decode_payload(body),
                headers=response_headers,
            )
        except (URLError, OSError, TimeoutError) as exc:  # provider/network errors become an explicit failed observation
            raise MassiveClientError("Massive request failed") from exc


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: Any) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    # Massive stock messages commonly use milliseconds; index snapshots may use ns.
    if number >= 1e17:
        number /= 1e9
    elif number >= 1e14:
        number /= 1e3
    elif number >= 1e11:
        number /= 1e3
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _snapshot_quality(
    *,
    last: float | None,
    source_timestamp: datetime | None,
    prices: tuple[float | None, ...],
    volume: float | None,
) -> str:
    if any(value is not None and value <= 0 for value in prices):
        return "NEEDS_REVIEW"
    if volume is not None and volume < 0:
        return "NEEDS_REVIEW"
    if last is None or source_timestamp is None:
        return "PARTIAL"
    return "OK"


def _safe_request_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("request_id")
    return value if isinstance(value, str) and value else None


class MassiveClient:
    def __init__(self, config: RealtimeConfig, api_key: str, *, transport: JsonTransport | None = None) -> None:
        config.validate()
        if not isinstance(api_key, str) or not api_key.strip():
            raise MissingApiKeyError("Massive API key is not configured")
        self.config = config
        self._api_key = api_key.strip()
        self._transport = transport or UrllibJsonTransport()

    @classmethod
    def from_env(cls, config: RealtimeConfig, *, environ: dict[str, str] | None = None, transport: JsonTransport | None = None) -> "MassiveClient":
        values = os.environ if environ is None else environ
        key = values.get(config.api_key_env)
        if not key:
            raise MissingApiKeyError(f"environment variable {config.api_key_env} is not set")
        return cls(config, key, transport=transport)

    def _get(self, path: str, query: dict[str, str] | None = None) -> TransportResponse:
        if not path.startswith("/"):
            raise MassiveClientError("provider path must be absolute")
        url = urljoin(self.config.base_url.rstrip("/") + "/", path.lstrip("/"))
        if query:
            url = f"{url}?{urlencode(query)}"
        response = self._transport.request(
            url,
            headers={"Accept": "application/json", "Authorization": f"Bearer {self._api_key}"},
            timeout=self.config.request_timeout_seconds,
        )
        if response.status_code in {401, 403}:
            raise MassiveClientError("NOT_ENTITLED")
        if response.status_code == 404:
            raise MassiveClientError("NOT_FOUND")
        if response.status_code == 429:
            raise MassiveClientError("RATE_LIMITED")
        if response.status_code < 200 or response.status_code >= 300:
            raise MassiveClientError("PROVIDER_HTTP_ERROR")
        if not isinstance(response.payload, dict):
            raise MassiveClientError("INVALID_PROVIDER_PAYLOAD")
        return response

    def _stock(self, declaration: RealtimeSymbol, fetched_at: datetime) -> RealtimeObservation:
        response = self._get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{quote(declaration.symbol, safe='')}")
        payload = response.payload
        ticker = payload.get("ticker") if isinstance(payload.get("ticker"), dict) else {}
        day = ticker.get("day") if isinstance(ticker.get("day"), dict) else {}
        previous = ticker.get("prevDay") if isinstance(ticker.get("prevDay"), dict) else {}
        last_trade = ticker.get("lastTrade") if isinstance(ticker.get("lastTrade"), dict) else {}
        minute = ticker.get("min") if isinstance(ticker.get("min"), dict) else {}
        last = _first_number(last_trade.get("p"), minute.get("c"), day.get("c"))
        # Massive uses nanoseconds for lastTrade.t, milliseconds for min.t, and
        # nanoseconds for ticker.updated in the documented snapshot shape.
        source_timestamp = (
            _timestamp(last_trade.get("t"))
            or _timestamp(minute.get("t"))
            or _timestamp(ticker.get("updated"))
        )
        close = _number(day.get("c"))
        previous_close = _number(previous.get("c"))
        volume = _number(day.get("v"))
        quality = _snapshot_quality(
            last=last,
            source_timestamp=source_timestamp,
            prices=(last, close, previous_close),
            volume=volume,
        )
        return RealtimeObservation(
            provider="massive",
            symbol=declaration.symbol,
            asset_class=declaration.asset_class,
            fetched_at_utc=fetched_at,
            source_timestamp_utc=source_timestamp,
            last=last,
            close=close,
            previous_close=previous_close,
            volume=volume,
            price_basis="unadjusted_ohlcv",
            quality=quality,
            provisional=True,
            request_id=_safe_request_id(payload),
            raw_payload_hash=_payload_hash(payload),
        )

    def _index(self, declaration: RealtimeSymbol, fetched_at: datetime) -> RealtimeObservation:
        response = self._get("/v3/snapshot/indices", {"ticker": declaration.symbol})
        payload = response.payload
        results = payload.get("results") if isinstance(payload.get("results"), list) else []
        item = next((candidate for candidate in results if isinstance(candidate, dict) and candidate.get("ticker") == declaration.symbol), None)
        if item is None:
            raise MassiveClientError("NOT_FOUND")
        provider_error = item.get("error")
        if provider_error:
            normalized_error = str(provider_error).strip().upper()
            if normalized_error in {"NOT_ENTITLED", "NOT_FOUND", "RATE_LIMITED"}:
                raise MassiveClientError(normalized_error)
            raise MassiveClientError("PROVIDER_RESPONSE_ERROR")
        session = item.get("session") if isinstance(item.get("session"), dict) else {}
        last = _first_number(item.get("value"), session.get("close"))
        source_timestamp = _timestamp(item.get("last_updated"))
        close = _number(session.get("close"))
        previous_close = _number(session.get("previous_close"))
        quality = _snapshot_quality(
            last=last,
            source_timestamp=source_timestamp,
            prices=(last, close, previous_close),
            volume=None,
        )
        return RealtimeObservation(
            provider="massive",
            symbol=declaration.symbol,
            asset_class=declaration.asset_class,
            fetched_at_utc=fetched_at,
            source_timestamp_utc=source_timestamp,
            last=last,
            close=close,
            previous_close=previous_close,
            volume=None,
            price_basis="index_level",
            quality=quality,
            provisional=True,
            request_id=_safe_request_id(payload),
            raw_payload_hash=_payload_hash(payload),
        )

    @staticmethod
    def _failed(declaration: RealtimeSymbol, fetched_at: datetime, error: str) -> RealtimeObservation:
        status = error if error in {"NOT_ENTITLED", "NOT_FOUND", "RATE_LIMITED"} else "FAILED"
        return RealtimeObservation(
            provider="massive",
            symbol=declaration.symbol,
            asset_class=declaration.asset_class,
            fetched_at_utc=fetched_at,
            source_timestamp_utc=None,
            last=None,
            close=None,
            previous_close=None,
            volume=None,
            price_basis="unknown",
            quality=status,  # type: ignore[arg-type]
            provisional=True,
            error_code=status,
            error_message="provider observation unavailable",
        )

    def fetch_batch(self, *, fetched_at_utc: datetime | None = None) -> ObservationBatch:
        fetched_at = fetched_at_utc or _utc_now()
        if fetched_at.tzinfo is None:
            raise ValueError("fetched_at_utc must be timezone-aware")
        observations: list[RealtimeObservation] = []
        for declaration in self.config.symbols:
            try:
                observation = self._stock(declaration, fetched_at) if declaration.asset_class == "stocks" else self._index(declaration, fetched_at)
            except MassiveClientError as exc:
                observation = self._failed(declaration, fetched_at, str(exc))
            observations.append(observation)
        # A polling batch is identified by provider observation content, not by
        # the local fetch time or request id. Re-reading unchanged data therefore
        # produces the same id and can be safely deduplicated by M16.2.
        material = json.dumps([item.dedupe_key for item in observations], sort_keys=True, separators=(",", ":"))
        batch_id = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
        return ObservationBatch(
            batch_id=batch_id,
            fetched_at_utc=fetched_at,
            observations=tuple(observations),
            display_timezone=self.config.display_timezone,
        )
