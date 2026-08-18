"""Massive daily aggregate adapter for the M18 M02 input boundary.

The adapter reuses the already validated M16 client for authentication and
transport. It converts the provider's ``results`` records into M02 bars while
preserving the complete provider response inside the immutable raw snapshot.
The API key is never placed in a request object, payload, log, or URL query.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import quote
from zoneinfo import ZoneInfo

from src.realtime.config import RealtimeConfig
from src.realtime.massive_client import MassiveClient, MassiveClientError
from src.storage.market_data import MarketDataRequest, RawSnapshot


class M18MassiveHistoryError(RuntimeError):
    """Raised for a bounded historical provider failure."""


_SENSITIVE_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
)
_SENSITIVE_QUERY_PATTERN = re.compile(r"(?i)(api[_-]?key|authorization|token|secret|password)=[^&\s]+")


def _safe_provider_value(value: Any) -> Any:
    """Keep provider diagnostics while removing credentials from raw records.

    Massive responses normally do not echo the Bearer key, but a provider may
    include a continuation URL or diagnostic field containing credentials.
    M08's storage guard also rejects sensitive field names, so the sanitisation
    is recursive before the response enters the immutable M02 snapshot.
    """

    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            lowered = text_key.lower().replace("-", "_")
            if any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS):
                continue
            safe[text_key] = _safe_provider_value(item)
        return safe
    if isinstance(value, list):
        return [_safe_provider_value(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_provider_value(item) for item in value]
    if isinstance(value, str):
        return _SENSITIVE_QUERY_PATTERN.sub(lambda match: f"{match.group(1)}=<redacted>", value)
    return value


def _timestamp_millis(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise M18MassiveHistoryError("aggregate timestamp is invalid")
    number = float(value)
    if number > 100_000_000_000_000:
        number /= 1_000_000.0
    if number < 0:
        raise M18MassiveHistoryError("aggregate timestamp is invalid")
    return datetime.fromtimestamp(number / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _bar_date(value: Any, timezone_name: str) -> str:
    timestamp = _timestamp_millis(value).replace("Z", "+00:00")
    return datetime.fromisoformat(timestamp).astimezone(ZoneInfo(timezone_name)).date().isoformat()


def _number(value: Any, field_name: str, *, required: bool = True) -> float | None:
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise M18MassiveHistoryError(f"aggregate {field_name} is invalid")
    result = float(value)
    if result != result or result in {float("inf"), float("-inf")}:
        raise M18MassiveHistoryError(f"aggregate {field_name} is invalid")
    return result


def _rows(payload: Mapping[str, Any], *, symbol: str, market_timezone: str) -> list[dict[str, Any]]:
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw_results:
        if not isinstance(item, Mapping):
            continue
        try:
            rows.append(
                {
                    "symbol": symbol,
                    "date": _bar_date(item.get("t"), market_timezone),
                    "open": _number(item.get("o"), "open"),
                    "high": _number(item.get("h"), "high"),
                    "low": _number(item.get("l"), "low"),
                    "close": _number(item.get("c"), "close"),
                    "volume": _number(item.get("v"), "volume", required=False),
                }
            )
        except M18MassiveHistoryError:
            continue
    rows.sort(key=lambda item: item["date"])
    return rows


def _model_symbol(provider_symbol: str) -> str:
    """Map Massive index tickers to the M02 internal symbol vocabulary."""

    normalized = provider_symbol.strip().upper()
    return normalized[2:] if normalized.startswith("I:") else normalized


class MassiveDailyHistoryAdapter:
    """Fetch one immutable M02 snapshot per declared Massive symbol group."""

    def __init__(self, config: RealtimeConfig, client: MassiveClient) -> None:
        if not isinstance(config, RealtimeConfig):
            raise M18MassiveHistoryError("config must be RealtimeConfig")
        if not isinstance(client, MassiveClient):
            raise M18MassiveHistoryError("client must be MassiveClient")
        config.validate()
        self.config = config
        self.client = client

    def fetch(
        self,
        *,
        start_date: str,
        end_date: str,
        symbols: Sequence[str] | None = None,
        retrieved_at: str | None = None,
    ) -> tuple[RawSnapshot, ...]:
        selected = tuple(symbols or tuple(item.symbol for item in self.config.symbols))
        declarations = {item.symbol: item for item in self.config.symbols}
        unknown = sorted(set(selected) - set(declarations))
        if unknown:
            raise M18MassiveHistoryError(f"symbols are not declared in realtime config: {unknown}")
        if start_date > end_date:
            raise M18MassiveHistoryError("start_date must not be after end_date")
        fetched_at = retrieved_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        groups = {
            "stocks": tuple(symbol for symbol in selected if declarations[symbol].asset_class == "stocks"),
            "indices": tuple(symbol for symbol in selected if declarations[symbol].asset_class == "indices"),
        }
        snapshots: list[RawSnapshot] = []
        for asset_class in ("stocks", "indices"):
            for symbol in sorted(groups[asset_class]):
                declaration = declarations[symbol]
                model_symbol = _model_symbol(symbol)
                price_basis = "adjusted_ohlcv" if asset_class == "stocks" else "index_level"
                request = MarketDataRequest(
                    source="massive-aggregates",
                    symbols=(model_symbol,),
                    start_date=start_date,
                    end_date=end_date,
                    price_basis=price_basis,
                    timezone=self.config.market_timezone,
                    exchange="NYSE",
                    provider_params={"adjusted": "true" if price_basis == "adjusted_ohlcv" else "false", "sort": "asc"},
                )
                path_symbol = quote(symbol, safe="")
                try:
                    response = self.client._get(  # type: ignore[attr-defined]  # validated M16 client boundary
                        f"/v2/aggs/ticker/{path_symbol}/range/1/day/{start_date}/{end_date}",
                        {"adjusted": "true" if price_basis == "adjusted_ohlcv" else "false", "sort": "asc", "limit": "50000"},
                    )
                    payload = copy.deepcopy(response.payload)
                    rows = _rows(payload, symbol=model_symbol, market_timezone=self.config.market_timezone)
                    if not rows:
                        snapshots.append(
                            RawSnapshot.failed(
                                source=request.source,
                                request=request,
                                retrieved_at=fetched_at,
                                error_code="EMPTY_PAYLOAD",
                                error_message="Massive aggregate response contained no usable daily bars",
                            )
                        )
                        continue
                    snapshots.append(
                        RawSnapshot.capture(
                            source=request.source,
                            request=request,
                            retrieved_at=fetched_at,
                            payload={"bars": rows, "provider_response": _safe_provider_value(payload)},
                            status="success",
                        )
                    )
                except (MassiveClientError, M18MassiveHistoryError) as exc:
                    code = str(exc).strip().upper() or "PROVIDER_ERROR"
                    if code not in {"NOT_ENTITLED", "NOT_FOUND", "RATE_LIMITED"}:
                        code = "PROVIDER_ERROR"
                    snapshots.append(
                        RawSnapshot.failed(
                            source=request.source,
                            request=request,
                            retrieved_at=fetched_at,
                            error_code=code,
                            error_message="Massive historical aggregate unavailable",
                        )
                    )
        if not snapshots:
            raise M18MassiveHistoryError("no symbols were selected")
        return tuple(snapshots)


__all__ = ["M18MassiveHistoryError", "MassiveDailyHistoryAdapter"]
