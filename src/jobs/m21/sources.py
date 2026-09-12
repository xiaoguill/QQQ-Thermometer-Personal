"""Free close-data sources for M21.

Stocks are fetched as daily adjusted aggregates from Massive.  VIX and VIX3M
are fetched as official daily close CSVs from Cboe's public CDN.  The source
objects are deliberately injectable so availability, permission, interface,
symbol-contract, and data-quality failures can be tested without credentials.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from time import sleep
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from src.realtime.config import RealtimeConfig, RealtimeConfigError
from src.realtime.massive_client import MassiveClient, MassiveClientError, MissingApiKeyError
from src.storage.market_data import MarketDataRequest

from .config import INDEX_SYMBOLS, M21Config, STOCK_SYMBOLS


class M21SourceError(RuntimeError):
    """A safe source failure with a machine-readable classification."""

    def __init__(self, code: str, failure_class: str, message: str) -> None:
        self.code = code
        self.failure_class = failure_class
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class SeriesResult:
    """One source result, including enough metadata for an audit manifest."""

    symbol: str
    provider: str
    status: str
    rows: tuple[Mapping[str, Any], ...]
    requested_start: str
    requested_end: str
    first_date: str | None = None
    last_date: str | None = None
    has_end_date: bool = False
    failure_code: str | None = None
    failure_class: str | None = None
    failure_message: str | None = None
    request: Mapping[str, Any] | None = None
    raw_sha256: str | None = None
    raw_row_count: int | None = None
    price_basis: str = "adjusted_ohlcv"

    def manifest_entry(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "provider": self.provider,
            "status": self.status,
            "requested_start_date": self.requested_start,
            "requested_end_date": self.requested_end,
            "first_date": self.first_date,
            "last_date": self.last_date,
            "rows_visible_through_end_date": len(self.rows),
            "raw_row_count": self.raw_row_count,
            "has_requested_end_date": self.has_end_date,
            "failure_code": self.failure_code,
            "failure_class": self.failure_class,
            "failure_message": self.failure_message,
            "request": copy.deepcopy(dict(self.request or {})),
            "raw_sha256": self.raw_sha256,
            "price_basis": self.price_basis,
        }


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _bytes_hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_positive(value: Any, field_name: str) -> float:
    if value in (None, "") or isinstance(value, bool):
        raise M21SourceError("INVALID_BAR", "data_quality", f"{field_name} is missing")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise M21SourceError("INVALID_BAR", "data_quality", f"{field_name} is not numeric") from exc
    if not math.isfinite(result) or result <= 0.0:
        raise M21SourceError("INVALID_BAR", "data_quality", f"{field_name} is not positive and finite")
    return result


def _parse_cboe_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise M21SourceError("INVALID_DATE", "data_quality", "Cboe date is missing")
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise M21SourceError("INVALID_DATE", "data_quality", "Cboe date is not a supported daily date")


def _bar_date(value: Any, market_timezone: str) -> str:
    if value in (None, "") or isinstance(value, bool):
        raise M21SourceError("INVALID_DATE", "interface", "Massive aggregate timestamp is missing")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise M21SourceError("INVALID_DATE", "interface", "Massive aggregate timestamp is invalid") from exc
    if not math.isfinite(number) or number < 0:
        raise M21SourceError("INVALID_DATE", "interface", "Massive aggregate timestamp is invalid")
    if number >= 1e17:
        number /= 1e9
    elif number >= 1e11:
        number /= 1e3
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc).astimezone(ZoneInfo(market_timezone)).date().isoformat()
    except (OverflowError, OSError, ValueError) as exc:
        raise M21SourceError("INVALID_DATE", "interface", "Massive aggregate timestamp is invalid") from exc


def _safe_request(request: MarketDataRequest) -> dict[str, Any]:
    return request.as_dict()


def _failure(
    *,
    symbol: str,
    provider: str,
    start_date: str,
    end_date: str,
    code: str,
    failure_class: str,
    message: str,
    request: Mapping[str, Any] | None = None,
    price_basis: str = "adjusted_ohlcv",
) -> SeriesResult:
    return SeriesResult(
        symbol=symbol,
        provider=provider,
        status="failed",
        rows=(),
        requested_start=start_date,
        requested_end=end_date,
        failure_code=code,
        failure_class=failure_class,
        failure_message=message,
        request=request,
        price_basis=price_basis,
    )


def _classify_error(code: str) -> tuple[str, str]:
    normalized = code.strip().upper()
    if normalized == "MISSING_API_KEY":
        return "credentials", "MISSING_API_KEY"
    if normalized in {"NOT_ENTITLED", "FORBIDDEN", "UNAUTHORIZED"}:
        return "permission", "NOT_ENTITLED"
    if normalized in {"NOT_FOUND", "SYMBOL_NOT_FOUND"}:
        return "interface_or_symbol", "NOT_FOUND"
    if normalized == "RATE_LIMITED":
        return "rate_limit", normalized
    if normalized in {"INVALID_PROVIDER_RESPONSE", "INVALID_DATE", "INVALID_BAR"}:
        return "interface" if normalized == "INVALID_PROVIDER_RESPONSE" else "data_quality", normalized
    if normalized in {"EMPTY_PAYLOAD", "MISSING_SERIES", "STALE_OR_MISSING"}:
        return "data_quality", normalized
    return "network_or_provider", "PROVIDER_ERROR"


def classify_vxx_issue(
    *,
    declared_in_config: bool,
    status: str,
    error_code: str | None = None,
    response_symbol: str | None = None,
) -> dict[str, Any]:
    """Classify the VXX failure without pretending a live test occurred.

    ``symbol_contract`` is reserved for a missing/mismatched declaration;
    ``permission`` is used only for an explicit provider entitlement failure;
    ``interface_or_symbol`` is used for a provider 404 or malformed symbol
    response.  This lets the real run say which layer failed.
    """

    if not declared_in_config:
        return {"outcome": "failed", "failure_class": "symbol_contract", "failure_code": "CONFIG_MISSING_SYMBOL"}
    if response_symbol and response_symbol.strip().upper() != "VXX":
        return {"outcome": "failed", "failure_class": "symbol_contract", "failure_code": "RESPONSE_SYMBOL_MISMATCH"}
    if status == "success":
        return {"outcome": "available", "failure_class": None, "failure_code": None}
    failure_class, normalized = _classify_error(error_code or "PROVIDER_ERROR")
    return {"outcome": "failed", "failure_class": failure_class, "failure_code": normalized}


def parse_cboe_history(content: bytes, *, symbol: str, end_date: str) -> tuple[dict[str, float], dict[str, Any]]:
    """Parse a Cboe daily history file without filling missing observations."""

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise M21SourceError("INVALID_PROVIDER_RESPONSE", "interface", "Cboe response is not UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise M21SourceError("INVALID_PROVIDER_RESPONSE", "interface", "Cboe response has no CSV header")
    fields = {str(field).strip().upper(): str(field) for field in reader.fieldnames if field}
    if "DATE" not in fields or "CLOSE" not in fields:
        raise M21SourceError("INVALID_PROVIDER_RESPONSE", "interface", "Cboe response does not contain DATE and CLOSE")
    values: dict[str, float] = {}
    raw_rows = 0
    for row in reader:
        raw_rows += 1
        session = _parse_cboe_date(row.get(fields["DATE"]))
        if session > end_date:
            continue
        if session in values:
            raise M21SourceError("DUPLICATE_BAR", "data_quality", f"duplicate Cboe {symbol} date")
        values[session] = _parse_positive(row.get(fields["CLOSE"]), f"{symbol}.{session}")
    if not values:
        raise M21SourceError("EMPTY_PAYLOAD", "data_quality", f"Cboe returned no visible {symbol} rows")
    meta = {
        "symbol": symbol,
        "provider": "cboe-official-cdn",
        "columns": list(reader.fieldnames),
        "raw_row_count": raw_rows,
        "rows_visible_through_end_date": len(values),
        "first_date": min(values),
        "last_date": max(values),
        "requested_end_date": end_date,
        "raw_sha256": _bytes_hash(content),
        "price_basis": "official_index_close",
    }
    return values, meta


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _fetch_bytes(url: str, *, timeout: int) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "cdn.cboe.com" or parsed.query or parsed.fragment:
        raise M21SourceError("INVALID_SOURCE_URL", "configuration", "Cboe URL is outside the allowlisted HTTPS source")
    request = Request(url, method="GET", headers={"Accept": "text/csv", "User-Agent": "QQQ-Thermometer-M21/1"})
    opener = build_opener(_NoRedirectHandler)
    try:
        with opener.open(request, timeout=timeout) as response:
            status = int(response.status)
            if not 200 <= status < 300:
                raise M21SourceError("SOURCE_HTTP_ERROR", "interface", "Cboe source returned a non-success status")
            return response.read()
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise M21SourceError("NOT_ENTITLED", "permission", "Cboe source denied access") from exc
        if exc.code == 404:
            raise M21SourceError("NOT_FOUND", "interface_or_symbol", "Cboe source file was not found") from exc
        if exc.code == 429:
            raise M21SourceError("RATE_LIMITED", "rate_limit", "Cboe source rate limited the request") from exc
        raise M21SourceError("SOURCE_HTTP_ERROR", "interface", "Cboe source returned an HTTP error") from exc
    except (URLError, OSError, TimeoutError) as exc:
        raise M21SourceError("SOURCE_UNAVAILABLE", "network_or_provider", "Cboe source could not be reached") from exc


class CboeOfficialSource:
    """Official Cboe daily close source with injectable byte retrieval."""

    def __init__(self, config: M21Config, *, fetcher: Callable[[str, int], bytes] | None = None) -> None:
        self.config = config
        self._fetcher = fetcher or (lambda url, timeout: _fetch_bytes(url, timeout=timeout))

    def fetch_index(self, symbol: str, *, end_date: str) -> SeriesResult:
        normalized = symbol.strip().upper()
        if normalized not in INDEX_SYMBOLS:
            raise ValueError(f"unsupported Cboe index: {symbol}")
        url = self.config.cboe_vix_url if normalized == "VIX" else self.config.cboe_vix3m_url
        request = {"source": "cboe-official-cdn", "symbol": normalized, "url": url, "interval": "1d", "price_basis": "official_index_close"}
        try:
            raw = self._fetcher(url, 30)
            values, meta = parse_cboe_history(raw, symbol=normalized, end_date=end_date)
            if end_date not in values:
                raise M21SourceError("STALE_OR_MISSING", "data_quality", f"Cboe {normalized} has no value on the requested close date")
            rows = tuple({"date": session, "symbol": normalized, "close": value} for session, value in sorted(values.items()))
            return SeriesResult(
                symbol=normalized,
                provider="cboe-official-cdn",
                status="success",
                rows=rows,
                requested_start=self.config.history_floor_date,
                requested_end=end_date,
                first_date=meta["first_date"],
                last_date=meta["last_date"],
                has_end_date=True,
                request=request,
                raw_sha256=meta["raw_sha256"],
                raw_row_count=meta["raw_row_count"],
                price_basis="official_index_close",
            )
        except M21SourceError as exc:
            return _failure(
                symbol=normalized,
                provider="cboe-official-cdn",
                start_date=self.config.history_floor_date,
                end_date=end_date,
                code=exc.code,
                failure_class=exc.failure_class,
                message=exc.message,
                request=request,
                price_basis="official_index_close",
            )


class MassiveFreeStocksSource:
    """Massive daily aggregate source for the declared ETF universe."""

    def __init__(
        self,
        config: M21Config,
        realtime_config: RealtimeConfig,
        client: MassiveClient | None,
        *,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        self.config = config
        self.realtime_config = realtime_config
        self.client = client
        self._sleep = sleep_fn

    @classmethod
    def from_config(cls, config: M21Config, *, sleep_fn: Callable[[float], None] = sleep) -> "MassiveFreeStocksSource":
        path = config.resolve(config.massive_stocks_config_path)
        try:
            realtime_config = RealtimeConfig.from_file(path)
        except (RealtimeConfigError, OSError, ValueError) as exc:
            raise M21SourceError("CONFIG_ERROR", "configuration", "Massive stock configuration is invalid") from exc
        declared = {item.symbol for item in realtime_config.symbols if item.asset_class == "stocks"}
        missing = sorted(set(config.required_stock_symbols) - declared)
        if missing:
            raise M21SourceError("CONFIG_MISSING_SYMBOL", "symbol_contract", "Massive stock configuration does not declare every required ETF")
        try:
            client = MassiveClient.from_env(realtime_config)
        except MissingApiKeyError as exc:
            return cls(config, realtime_config, None, sleep_fn=sleep_fn)
        return cls(config, realtime_config, client, sleep_fn=sleep_fn)

    def _request(self, symbol: str, *, start_date: str, end_date: str) -> MarketDataRequest:
        return MarketDataRequest(
            source="massive-free-stocks",
            symbols=(symbol,),
            start_date=start_date,
            end_date=end_date,
            price_basis="adjusted_ohlcv",
            timezone=self.config.market_timezone,
            exchange="US_EQUITIES",
            provider_params={"adjusted": "true", "sort": "asc", "limit": "50000"},
        )

    def _fetch_one(self, symbol: str, *, start_date: str, end_date: str) -> SeriesResult:
        request = self._request(symbol, start_date=start_date, end_date=end_date)
        public_request = _safe_request(request)
        if self.client is None:
            return _failure(
                symbol=symbol,
                provider="massive-free-stocks",
                start_date=start_date,
                end_date=end_date,
                code="MISSING_API_KEY",
                failure_class="credentials",
                message="Massive API key is not configured",
                request=public_request,
            )
        try:
            response = self.client._get(  # type: ignore[attr-defined]  # reuse validated read-only transport
                f"/v2/aggs/ticker/{quote(symbol, safe='')}/range/1/day/{start_date}/{end_date}",
                {"adjusted": "true", "sort": "asc", "limit": "50000"},
            )
            payload = response.payload
            raw_results = payload.get("results") if isinstance(payload, Mapping) else None
            if not isinstance(raw_results, list) or not raw_results:
                raise M21SourceError("EMPTY_PAYLOAD", "data_quality", "Massive returned no daily aggregate rows")
            values: dict[str, Mapping[str, Any]] = {}
            for item in raw_results:
                if not isinstance(item, Mapping):
                    raise M21SourceError("INVALID_PROVIDER_RESPONSE", "interface", "Massive aggregate row is not an object")
                response_symbol = item.get("T") or item.get("ticker") or item.get("symbol")
                if response_symbol not in (None, "") and str(response_symbol).strip().upper() != symbol:
                    raise M21SourceError("RESPONSE_SYMBOL_MISMATCH", "symbol_contract", "Massive response symbol does not match the requested symbol")
                session = _bar_date(item.get("t"), self.config.market_timezone)
                if session > end_date:
                    continue
                if session in values:
                    raise M21SourceError("DUPLICATE_BAR", "data_quality", "Massive returned duplicate daily dates")
                close = _parse_positive(item.get("c"), f"{symbol}.{session}.close")
                row: dict[str, Any] = {"date": session, "symbol": symbol, "close": close}
                for source_field, output_field in (("o", "open"), ("h", "high"), ("l", "low")):
                    if item.get(source_field) not in (None, ""):
                        row[output_field] = _parse_positive(item.get(source_field), f"{symbol}.{session}.{output_field}")
                if item.get("v") not in (None, ""):
                    volume = float(item["v"])
                    if not math.isfinite(volume) or volume < 0:
                        raise M21SourceError("INVALID_BAR", "data_quality", f"{symbol}.{session}.volume is invalid")
                    row["volume"] = volume
                values[session] = row
            if end_date not in values:
                raise M21SourceError("STALE_OR_MISSING", "data_quality", "Massive has no daily bar on the requested close date")
            rows = tuple(values[session] for session in sorted(values))
            return SeriesResult(
                symbol=symbol,
                provider="massive-free-stocks",
                status="success",
                rows=rows,
                requested_start=start_date,
                requested_end=end_date,
                first_date=min(values),
                last_date=max(values),
                has_end_date=True,
                request=public_request,
                raw_sha256=_canonical_hash(payload),
                raw_row_count=len(raw_results),
                price_basis="adjusted_ohlcv",
            )
        except M21SourceError as exc:
            return _failure(
                symbol=symbol,
                provider="massive-free-stocks",
                start_date=start_date,
                end_date=end_date,
                code=exc.code,
                failure_class=exc.failure_class,
                message=exc.message,
                request=public_request,
            )
        except MassiveClientError as exc:
            failure_class, code = _classify_error(str(exc))
            return _failure(
                symbol=symbol,
                provider="massive-free-stocks",
                start_date=start_date,
                end_date=end_date,
                code=code,
                failure_class=failure_class,
                message="Massive daily aggregate request failed",
                request=public_request,
            )

    def fetch_all(self, *, start_date: str, end_date: str) -> tuple[SeriesResult, ...]:
        results: list[SeriesResult] = []
        for index, symbol in enumerate(self.config.required_stock_symbols):
            if self.client is not None and index and self.config.request_spacing_seconds > 0:
                self._sleep(self.config.request_spacing_seconds)
            results.append(self._fetch_one(symbol, start_date=start_date, end_date=end_date))
        return tuple(results)


def write_price_csv(path: Path, results: Sequence[SeriesResult], *, symbols: Sequence[str] = STOCK_SYMBOLS) -> None:
    by_symbol = {result.symbol: {str(row["date"]): row for row in result.rows} for result in results}
    dates = sorted({session for rows in by_symbol.values() for session in rows})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", *symbols], extrasaction="ignore")
        writer.writeheader()
        for session in dates:
            writer.writerow({
                "date": session,
                **{
                    symbol: by_symbol.get(symbol, {}).get(session, {}).get("close", "")
                    for symbol in symbols
                },
            })


def write_vxx_csv(path: Path, result: SeriesResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "symbol", "close"], extrasaction="ignore")
        writer.writeheader()
        for row in result.rows:
            writer.writerow({"date": row["date"], "symbol": "VXX", "close": row["close"]})


def write_vix_csv(path: Path, results: Sequence[SeriesResult]) -> None:
    by_symbol = {result.symbol: {str(row["date"]): row["close"] for row in result.rows} for result in results}
    dates = sorted({session for rows in by_symbol.values() for session in rows})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "VIX", "VIX3M"], extrasaction="ignore")
        writer.writeheader()
        for session in dates:
            writer.writerow({"date": session, "VIX": by_symbol.get("VIX", {}).get(session, ""), "VIX3M": by_symbol.get("VIX3M", {}).get(session, "")})


__all__ = [
    "CboeOfficialSource",
    "M21SourceError",
    "MassiveFreeStocksSource",
    "SeriesResult",
    "classify_vxx_issue",
    "parse_cboe_history",
    "write_price_csv",
    "write_vix_csv",
    "write_vxx_csv",
]
