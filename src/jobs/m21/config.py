"""Non-secret configuration for the M21 free-close runner.

M21 is a new data and scheduling boundary.  The strategy remains pinned to
the existing v12.2 causal replay and frozen v10 candidate; this module does
not define indicators, regimes, weights, or thresholds.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


M21_RUNTIME_VERSION = "m21-free-close-readonly/v1"
M21_DECISION_SCHEMA = "qqq-m21-decision/v1"
M21_CONFIG_SCHEMA = "qqq-m21-free-close-readonly-config/v1"
EXPECTED_REPLAY_VERSION = "v12.2-causal-walk-forward/v1"
EXPECTED_STRATEGY_VERSION = "v10_preserve_shock_recovery"
SUPPORTED_PROVIDERS = frozenset({"free_close", "local_csv"})
STOCK_SYMBOLS = ("QQQ", "QLD", "VXX", "SVXY", "BIL", "TLT", "IAU", "XLU", "VOO", "SPY")
INDEX_SYMBOLS = ("VIX", "VIX3M")
DECISION_SYMBOLS = ("QQQ", "BIL", "VXX", "VIX", "VIX3M")


class M21ConfigError(ValueError):
    """Raised when an M21 configuration is unsafe or invalid."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise M21ConfigError(message)


def _iso_date(value: Any, field_name: str) -> str:
    _require(isinstance(value, str), f"{field_name} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise M21ConfigError(f"{field_name} must be YYYY-MM-DD") from exc
    _require(parsed.isoformat() == value, f"{field_name} must be YYYY-MM-DD")
    return value


def _positive_number(value: Any, field_name: str) -> float:
    _require(not isinstance(value, bool), f"{field_name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise M21ConfigError(f"{field_name} must be numeric") from exc
    _require(math.isfinite(number) and number > 0.0, f"{field_name} must be finite and positive")
    return number


def _non_negative_number(value: Any, field_name: str) -> float:
    _require(not isinstance(value, bool), f"{field_name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise M21ConfigError(f"{field_name} must be numeric") from exc
    _require(math.isfinite(number) and number >= 0.0, f"{field_name} must be finite and non-negative")
    return number


def _integer(value: Any, field_name: str, *, minimum: int = 0) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool), f"{field_name} must be an integer")
    _require(value >= minimum, f"{field_name} must be at least {minimum}")
    return value


def _boolean(value: Any, field_name: str) -> bool:
    _require(isinstance(value, bool), f"{field_name} must be true or false")
    return value


def _hhmm(value: Any, field_name: str) -> str:
    _require(isinstance(value, str), f"{field_name} must be HH:MM")
    parts = value.split(":")
    _require(len(parts) == 2 and all(part.isdigit() for part in parts), f"{field_name} must be HH:MM")
    hour, minute = (int(part) for part in parts)
    _require(0 <= hour <= 23 and 0 <= minute <= 59, f"{field_name} must be HH:MM")
    return f"{hour:02d}:{minute:02d}"


def _timezone(value: Any, field_name: str) -> str:
    _require(isinstance(value, str) and value.strip(), f"{field_name} is required")
    try:
        ZoneInfo(value.strip())
    except ZoneInfoNotFoundError as exc:
        raise M21ConfigError(f"unknown timezone: {value}") from exc
    return value.strip()


def _strings(value: Any, field_name: str, *, expected: Sequence[str] | None = None) -> tuple[str, ...]:
    _require(isinstance(value, Sequence) and not isinstance(value, (str, bytes)), f"{field_name} must be a list")
    result = tuple(str(item).strip().upper() for item in value)
    _require(result and all(result), f"{field_name} must not be empty")
    _require(len(result) == len(set(result)), f"{field_name} must not contain duplicates")
    if expected is not None:
        _require(result == tuple(expected), f"{field_name} is fixed to {list(expected)}")
    return result


def _reject_secrets(value: Any, path: str = "config") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in {"api_key", "api_secret", "password", "secret", "token", "authorization"}:
                raise M21ConfigError(f"{path}.{key} must not contain a credential; use GitHub Secrets")
            _reject_secrets(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secrets(item, f"{path}[{index}]")


def _https_url(value: Any, field_name: str, *, host: str) -> str:
    _require(isinstance(value, str) and value.strip(), f"{field_name} is required")
    parsed = urlparse(value.strip())
    _require(
        parsed.scheme == "https"
        and parsed.hostname == host
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment,
        f"{field_name} must be an HTTPS URL on {host} without credentials or query parameters",
    )
    return value.strip()


@dataclass(frozen=True)
class M21Config:
    """Validated M21 configuration containing no credential values."""

    source_path: Path
    project_root: Path
    provider: str
    massive_stocks_config_path: str
    cboe_vix_url: str
    cboe_vix3m_url: str
    local_prices_csv: str | None
    local_vix_csv: str | None
    local_vxx_csv: str | None
    local_vxx_value_field: str
    history_floor_date: str
    free_history_days: int
    replay_start_date: str
    replay_config_path: str
    display_timezone: str
    market_timezone: str
    close_confirmation_time: str
    required_stock_symbols: tuple[str, ...]
    required_index_symbols: tuple[str, ...]
    decision_symbols: tuple[str, ...]
    request_spacing_seconds: float
    max_source_age_seconds: int
    initial_capital: float
    cost_bps: tuple[float, ...]
    require_vxx: bool
    paper_only: bool
    execution_allowed: bool
    email_window_start: str
    email_window_end: str

    @classmethod
    def from_file(cls, path: str | Path) -> "M21Config":
        source = Path(path).resolve()
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise M21ConfigError(f"unable to load M21 config: {source}") from exc
        _require(isinstance(raw, Mapping), "M21 config root must be an object")
        _reject_secrets(raw)
        data = raw.get("data")
        replay = raw.get("replay")
        schedule = raw.get("schedule")
        local = data.get("local_csv") if isinstance(data, Mapping) else None
        cboe = data.get("cboe") if isinstance(data, Mapping) else None
        _require(isinstance(data, Mapping), "data must be an object")
        _require(isinstance(replay, Mapping), "replay must be an object")
        _require(isinstance(schedule, Mapping), "schedule must be an object")
        _require(isinstance(cboe, Mapping), "data.cboe must be an object")
        costs_raw = replay.get("cost_bps", [5.0, 10.0, 25.0])
        _require(isinstance(costs_raw, Sequence) and not isinstance(costs_raw, (str, bytes)) and costs_raw, "replay.cost_bps must be a non-empty list")
        costs = tuple(_positive_number(item, "replay.cost_bps") for item in costs_raw)
        window = schedule.get("email_window")
        _require(isinstance(window, Mapping), "schedule.email_window must be an object")
        window_start = _hhmm(window.get("start"), "schedule.email_window.start")
        window_end = _hhmm(window.get("end"), "schedule.email_window.end")
        _require(window_start < window_end, "schedule.email_window.start must be before end")
        project_root = source.parents[1].parent
        value = cls(
            source_path=source,
            project_root=project_root,
            provider=str(raw.get("provider", "")).strip(),
            massive_stocks_config_path=str(data.get("massive_stocks_config_path", "")),
            cboe_vix_url=_https_url(cboe.get("vix_url"), "data.cboe.vix_url", host="cdn.cboe.com"),
            cboe_vix3m_url=_https_url(cboe.get("vix3m_url"), "data.cboe.vix3m_url", host="cdn.cboe.com"),
            local_prices_csv=None if not isinstance(local, Mapping) or local.get("prices_adj_close_csv") in (None, "") else str(local.get("prices_adj_close_csv")),
            local_vix_csv=None if not isinstance(local, Mapping) or local.get("vix_indices_csv") in (None, "") else str(local.get("vix_indices_csv")),
            local_vxx_csv=None if not isinstance(local, Mapping) or local.get("vxx_csv") in (None, "") else str(local.get("vxx_csv")),
            local_vxx_value_field=str(local.get("vxx_value_field", "adj_close")) if isinstance(local, Mapping) else "adj_close",
            history_floor_date=_iso_date(data.get("history_floor_date"), "data.history_floor_date"),
            free_history_days=_integer(data.get("free_history_days", 730), "data.free_history_days", minimum=1),
            replay_start_date=_iso_date(replay.get("start_date"), "replay.start_date"),
            replay_config_path=str(replay.get("base_config_path", "")),
            display_timezone=_timezone(data.get("display_timezone"), "data.display_timezone"),
            market_timezone=_timezone(data.get("market_timezone"), "data.market_timezone"),
            close_confirmation_time=_hhmm(data.get("close_confirmation_time"), "data.close_confirmation_time"),
            required_stock_symbols=_strings(data.get("required_stock_symbols"), "data.required_stock_symbols"),
            required_index_symbols=_strings(data.get("required_index_symbols"), "data.required_index_symbols"),
            decision_symbols=_strings(data.get("decision_symbols"), "data.decision_symbols"),
            request_spacing_seconds=_non_negative_number(data.get("request_spacing_seconds", 13), "data.request_spacing_seconds"),
            max_source_age_seconds=_integer(data.get("max_source_age_seconds", 172800), "data.max_source_age_seconds", minimum=1),
            initial_capital=_positive_number(replay.get("initial_capital", 1_000_000.0), "replay.initial_capital"),
            cost_bps=costs,
            require_vxx=_boolean(data.get("require_vxx", True), "data.require_vxx"),
            paper_only=_boolean(raw.get("paper_only", True), "paper_only"),
            execution_allowed=_boolean(raw.get("execution_allowed", False), "execution_allowed"),
            email_window_start=window_start,
            email_window_end=window_end,
        )
        value.validate(raw)
        return value

    def validate(self, raw: Mapping[str, Any] | None = None) -> None:
        _require(self.provider in SUPPORTED_PROVIDERS, f"provider must be one of {sorted(SUPPORTED_PROVIDERS)}")
        _require(self.display_timezone == "Asia/Shanghai", "display_timezone must remain Asia/Shanghai")
        _require(self.market_timezone == "America/New_York", "market_timezone must remain America/New_York")
        _require(self.required_stock_symbols == STOCK_SYMBOLS, "M21 stock universe is fixed to the ten declared ETFs")
        _require(self.required_index_symbols == INDEX_SYMBOLS, "M21 index universe is fixed to VIX/VIX3M")
        _require(self.decision_symbols == DECISION_SYMBOLS, "M21 decision symbols are fixed to QQQ/BIL/VXX/VIX/VIX3M")
        _require(self.free_history_days >= 366, "data.free_history_days must cover at least one year")
        _require(self.max_source_age_seconds >= 86400, "data.max_source_age_seconds must cover a daily source")
        _require(self.replay_start_date >= self.history_floor_date, "replay.start_date cannot precede data.history_floor_date")
        _require(self.massive_stocks_config_path.strip(), "data.massive_stocks_config_path is required")
        _require(self.replay_config_path.strip(), "replay.base_config_path is required")
        _require(self.require_vxx, "M21 must fail closed when VXX is missing")
        _require(self.paper_only and not self.execution_allowed, "M21 is paper-only and execution is disabled")
        if self.provider == "local_csv":
            _require(self.local_prices_csv and self.local_vix_csv and self.local_vxx_csv, "local_csv provider requires all three CSV paths")
            _require(self.local_vxx_value_field in {"close", "adj_close"}, "data.local_csv.vxx_value_field must be close or adj_close")
        if raw is not None:
            _require(raw.get("runtime_version") == M21_RUNTIME_VERSION, "runtime_version must remain m21-free-close-readonly/v1")
            _require(raw.get("replay_version") == EXPECTED_REPLAY_VERSION, "replay_version must remain v12.2-causal-walk-forward/v1")
            _require(raw.get("strategy_version") == EXPECTED_STRATEGY_VERSION, "strategy_version must remain the frozen v10 source")

    def resolve(self, value: str) -> Path:
        candidate = Path(value)
        return candidate if candidate.is_absolute() else self.project_root / candidate

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": M21_CONFIG_SCHEMA,
            "runtime_version": M21_RUNTIME_VERSION,
            "provider": self.provider,
            "massive_stocks_config_path": self.massive_stocks_config_path,
            "cboe_vix_url": self.cboe_vix_url,
            "cboe_vix3m_url": self.cboe_vix3m_url,
            "local_prices_csv": self.local_prices_csv,
            "local_vix_csv": self.local_vix_csv,
            "local_vxx_csv": self.local_vxx_csv,
            "local_vxx_value_field": self.local_vxx_value_field,
            "history_floor_date": self.history_floor_date,
            "free_history_days": self.free_history_days,
            "replay_start_date": self.replay_start_date,
            "replay_config_path": self.replay_config_path,
            "display_timezone": self.display_timezone,
            "market_timezone": self.market_timezone,
            "close_confirmation_time": self.close_confirmation_time,
            "required_stock_symbols": list(self.required_stock_symbols),
            "required_index_symbols": list(self.required_index_symbols),
            "decision_symbols": list(self.decision_symbols),
            "request_spacing_seconds": self.request_spacing_seconds,
            "max_source_age_seconds": self.max_source_age_seconds,
            "initial_capital": self.initial_capital,
            "cost_bps": list(self.cost_bps),
            "require_vxx": self.require_vxx,
            "paper_only": self.paper_only,
            "execution_allowed": self.execution_allowed,
            "email_window": {"start": self.email_window_start, "end": self.email_window_end},
        }


__all__ = [
    "DECISION_SYMBOLS",
    "EXPECTED_REPLAY_VERSION",
    "EXPECTED_STRATEGY_VERSION",
    "INDEX_SYMBOLS",
    "M21Config",
    "M21ConfigError",
    "M21_CONFIG_SCHEMA",
    "M21_DECISION_SCHEMA",
    "M21_RUNTIME_VERSION",
    "STOCK_SYMBOLS",
]
