"""Non-secret configuration for the M19 scheduled read-only runner.

M19 is intentionally a thin orchestration layer.  It does not define a new
strategy: the replay and strategy versions are pinned to the existing v12.2
causal wrapper and the frozen v10 candidate contract.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


M19_RUNTIME_VERSION = "m19-readonly-data-replay/v1"
M19_SCHEMA = "qqq-m19-readonly-config/v1"
EXPECTED_REPLAY_VERSION = "v12.2-causal-walk-forward/v1"
EXPECTED_STRATEGY_VERSION = "v10_preserve_shock_recovery"
SUPPORTED_PROVIDERS = frozenset({"massive", "local_csv"})
REQUIRED_PROVIDER_SYMBOLS = ("QQQ", "BIL", "VXX", "I:VIX", "I:VIX3M")
REQUIRED_INTERNAL_SYMBOLS = ("QQQ", "BIL", "VXX", "VIX", "VIX3M")
CHECKPOINTS = ("open", "midday", "close", "email")


class M19ConfigError(ValueError):
    """Raised when the scheduled runner configuration is unsafe or invalid."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise M19ConfigError(message)


def _iso_date(value: Any, field_name: str) -> str:
    _require(isinstance(value, str), f"{field_name} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise M19ConfigError(f"{field_name} must be YYYY-MM-DD") from exc
    _require(parsed.isoformat() == value, f"{field_name} must be YYYY-MM-DD")
    return value


def _positive_number(value: Any, field_name: str) -> float:
    _require(not isinstance(value, bool), f"{field_name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise M19ConfigError(f"{field_name} must be numeric") from exc
    _require(math.isfinite(number) and number > 0.0, f"{field_name} must be finite and positive")
    return number


def _hhmm(value: Any, field_name: str) -> str:
    _require(isinstance(value, str), f"{field_name} must be HH:MM")
    parts = value.split(":")
    _require(len(parts) == 2 and all(part.isdigit() for part in parts), f"{field_name} must be HH:MM")
    hour, minute = (int(part) for part in parts)
    _require(0 <= hour <= 23 and 0 <= minute <= 59, f"{field_name} must be HH:MM")
    return f"{hour:02d}:{minute:02d}"


def _validate_timezone(value: Any, field_name: str) -> str:
    _require(isinstance(value, str) and value.strip(), f"{field_name} is required")
    try:
        ZoneInfo(value.strip())
    except ZoneInfoNotFoundError as exc:
        raise M19ConfigError(f"unknown timezone: {value}") from exc
    return value.strip()


def _reject_secret_values(value: Any, path: str = "config") -> None:
    """Reject accidental secret material without rejecting safe env-var names."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in {"api_key", "api_secret", "password", "secret", "token", "authorization"}:
                raise M19ConfigError(f"{path}.{key} must not contain a credential; use GitHub Secrets")
            _reject_secret_values(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_values(item, f"{path}[{index}]")


def _as_tuple_of_strings(value: Any, field_name: str) -> tuple[str, ...]:
    _require(isinstance(value, Sequence) and not isinstance(value, (str, bytes)), f"{field_name} must be a list")
    result = tuple(str(item).strip() for item in value)
    _require(result and all(result), f"{field_name} must not be empty")
    _require(len(result) == len(set(result)), f"{field_name} must not contain duplicates")
    return result


@dataclass(frozen=True)
class M19Config:
    """Validated M19 configuration with no credential fields."""

    source_path: Path
    project_root: Path
    provider: str
    massive_config_path: str
    local_prices_csv: str | None
    local_vix_csv: str | None
    local_vxx_csv: str | None
    history_start_date: str
    replay_start_date: str
    replay_config_path: str
    display_timezone: str
    market_timezone: str
    close_confirmation_time: str
    email_window_start: str
    email_window_end: str
    required_provider_symbols: tuple[str, ...]
    initial_capital: float
    cost_bps: tuple[float, ...]
    require_vxx: bool
    paper_only: bool
    execution_allowed: bool
    checkpoints: Mapping[str, str]

    @classmethod
    def from_file(cls, path: str | Path) -> "M19Config":
        source = Path(path).resolve()
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise M19ConfigError(f"unable to load M19 config: {source}") from exc
        _require(isinstance(raw, Mapping), "M19 config root must be an object")
        _reject_secret_values(raw)
        data = raw.get("data")
        replay = raw.get("replay")
        schedule = raw.get("schedule")
        local = data.get("local_csv") if isinstance(data, Mapping) else None
        _require(isinstance(data, Mapping), "data must be an object")
        _require(isinstance(replay, Mapping), "replay must be an object")
        _require(isinstance(schedule, Mapping), "schedule must be an object")
        cost_raw = replay.get("cost_bps", [5.0, 10.0, 25.0])
        _require(isinstance(cost_raw, Sequence) and not isinstance(cost_raw, (str, bytes)) and cost_raw, "replay.cost_bps must be a non-empty list")
        costs = tuple(_positive_number(item, "replay.cost_bps") for item in cost_raw)
        provider = str(raw.get("provider", "")).strip()
        required = _as_tuple_of_strings(data.get("required_provider_symbols", REQUIRED_PROVIDER_SYMBOLS), "data.required_provider_symbols")
        checkpoints_raw = schedule.get("checkpoints")
        _require(isinstance(checkpoints_raw, Mapping), "schedule.checkpoints must be an object")
        checkpoints = {name: _hhmm(checkpoints_raw.get(name), f"schedule.checkpoints.{name}") for name in CHECKPOINTS}
        window = schedule.get("email_window")
        _require(isinstance(window, Mapping), "schedule.email_window must be an object")
        window_start = _hhmm(window.get("start"), "schedule.email_window.start")
        window_end = _hhmm(window.get("end"), "schedule.email_window.end")
        _require(window_start < window_end, "schedule.email_window.start must be before end")
        project_root = source.parents[1].parent
        value = cls(
            source_path=source,
            project_root=project_root,
            provider=provider,
            massive_config_path=str(data.get("massive_config_path", "")),
            local_prices_csv=None if not isinstance(local, Mapping) or local.get("prices_adj_close_csv") in (None, "") else str(local.get("prices_adj_close_csv")),
            local_vix_csv=None if not isinstance(local, Mapping) or local.get("vix_indices_csv") in (None, "") else str(local.get("vix_indices_csv")),
            local_vxx_csv=None if not isinstance(local, Mapping) or local.get("vxx_csv") in (None, "") else str(local.get("vxx_csv")),
            history_start_date=_iso_date(data.get("history_start_date"), "data.history_start_date"),
            replay_start_date=_iso_date(replay.get("start_date"), "replay.start_date"),
            replay_config_path=str(replay.get("base_config_path", "")),
            display_timezone=_validate_timezone(data.get("display_timezone"), "data.display_timezone"),
            market_timezone=_validate_timezone(data.get("market_timezone"), "data.market_timezone"),
            close_confirmation_time=_hhmm(data.get("close_confirmation_time"), "data.close_confirmation_time"),
            email_window_start=window_start,
            email_window_end=window_end,
            required_provider_symbols=required,
            initial_capital=_positive_number(replay.get("initial_capital", 1_000_000.0), "replay.initial_capital"),
            cost_bps=costs,
            require_vxx=bool(data.get("require_vxx", True)),
            paper_only=bool(raw.get("paper_only", True)),
            execution_allowed=bool(raw.get("execution_allowed", False)),
            checkpoints=checkpoints,
        )
        value.validate(raw)
        return value

    def validate(self, raw: Mapping[str, Any] | None = None) -> None:
        _require(self.provider in SUPPORTED_PROVIDERS, f"provider must be one of {sorted(SUPPORTED_PROVIDERS)}")
        _require(self.display_timezone == "Asia/Shanghai", "display_timezone must remain Asia/Shanghai")
        _require(self.market_timezone == "America/New_York", "market_timezone must remain America/New_York")
        _require(self.required_provider_symbols == REQUIRED_PROVIDER_SYMBOLS, "M19 required symbols are fixed to QQQ/BIL/VXX/VIX/VIX3M")
        _require(self.replay_start_date >= self.history_start_date, "replay.start_date cannot precede data.history_start_date")
        _require(self.replay_config_path.strip(), "replay.base_config_path is required")
        _require(self.massive_config_path.strip(), "data.massive_config_path is required")
        if self.provider == "local_csv":
            _require(self.local_prices_csv and self.local_vix_csv and self.local_vxx_csv, "local_csv provider requires all three CSV paths")
        _require(self.require_vxx, "M19 must fail closed when VXX is missing")
        _require(self.paper_only and not self.execution_allowed, "M19 is paper-only and execution is disabled")
        if raw is not None:
            _require(raw.get("replay_version") == EXPECTED_REPLAY_VERSION, "replay_version must remain v12.2-causal-walk-forward/v1")
            _require(raw.get("strategy_version") == EXPECTED_STRATEGY_VERSION, "strategy_version must remain the frozen v10 source")

    def resolve(self, value: str) -> Path:
        candidate = Path(value)
        return candidate if candidate.is_absolute() else (self.project_root / candidate)

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": M19_SCHEMA,
            "runtime_version": M19_RUNTIME_VERSION,
            "provider": self.provider,
            "massive_config_path": self.massive_config_path,
            "local_prices_csv": self.local_prices_csv,
            "local_vix_csv": self.local_vix_csv,
            "local_vxx_csv": self.local_vxx_csv,
            "history_start_date": self.history_start_date,
            "replay_start_date": self.replay_start_date,
            "replay_config_path": self.replay_config_path,
            "display_timezone": self.display_timezone,
            "market_timezone": self.market_timezone,
            "close_confirmation_time": self.close_confirmation_time,
            "email_window": {"start": self.email_window_start, "end": self.email_window_end},
            "required_provider_symbols": list(self.required_provider_symbols),
            "initial_capital": self.initial_capital,
            "cost_bps": list(self.cost_bps),
            "require_vxx": self.require_vxx,
            "paper_only": self.paper_only,
            "execution_allowed": self.execution_allowed,
            "checkpoints": dict(self.checkpoints),
        }


__all__ = [
    "CHECKPOINTS",
    "EXPECTED_REPLAY_VERSION",
    "EXPECTED_STRATEGY_VERSION",
    "M19Config",
    "M19ConfigError",
    "M19_RUNTIME_VERSION",
    "REQUIRED_INTERNAL_SYMBOLS",
    "REQUIRED_PROVIDER_SYMBOLS",
]
