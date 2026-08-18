"""Validated, non-secret M16 runtime configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import RealtimeSymbol


MIN_REFRESH_SECONDS = 60
MAX_REFRESH_SECONDS = 86_400
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "realtime" / "massive.json"
MASSIVE_API_HOST = "api.massive.com"


class RealtimeConfigError(ValueError):
    """Raised when a non-secret realtime configuration is invalid."""


@dataclass(frozen=True)
class RealtimeConfig:
    provider: str
    mode: str
    base_url: str
    api_key_env: str
    refresh_interval_seconds: int
    request_timeout_seconds: int
    display_timezone: str
    market_timezone: str
    max_source_age_seconds: int
    future_skew_seconds: int
    symbols: tuple[RealtimeSymbol, ...]
    confirmed_read_model_path: str | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "RealtimeConfig":
        if not isinstance(raw, Mapping):
            raise RealtimeConfigError("config must be an object")
        symbols_raw = raw.get("symbols")
        if not isinstance(symbols_raw, list) or not symbols_raw:
            raise RealtimeConfigError("symbols must be a non-empty list")
        try:
            symbols = tuple(
                RealtimeSymbol(
                    symbol=str(item["symbol"]),
                    asset_class=str(item["asset_class"]),
                    role=str(item["role"]),
                )
                for item in symbols_raw
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RealtimeConfigError("invalid symbol declaration") from exc
        value = cls(
            provider=str(raw.get("provider", "")),
            mode=str(raw.get("mode", "")),
            base_url=str(raw.get("base_url", "")),
            api_key_env=str(raw.get("api_key_env", "")),
            refresh_interval_seconds=raw.get("refresh_interval_seconds", 0),
            request_timeout_seconds=raw.get("request_timeout_seconds", 0),
            display_timezone=str(raw.get("display_timezone", "")),
            market_timezone=str(raw.get("market_timezone", "")),
            max_source_age_seconds=raw.get("max_source_age_seconds", 0),
            future_skew_seconds=raw.get("future_skew_seconds", 0),
            symbols=symbols,
            confirmed_read_model_path=raw.get("confirmed_read_model_path"),
        )
        value.validate()
        return value

    @classmethod
    def from_file(cls, path: str | Path = DEFAULT_CONFIG_PATH) -> "RealtimeConfig":
        source = Path(path)
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RealtimeConfigError(f"unable to load realtime config: {source}") from exc
        return cls.from_mapping(raw)

    def validate(self) -> None:
        if self.provider != "massive":
            raise RealtimeConfigError("provider must be massive")
        if self.mode != "rest_poll":
            raise RealtimeConfigError("M16.1 currently supports rest_poll only")
        parsed = urlparse(self.base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != MASSIVE_API_HOST
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise RealtimeConfigError("base_url must be the canonical Massive HTTPS origin without credentials")
        if not self.api_key_env or self.api_key_env.startswith("$"):
            raise RealtimeConfigError("api_key_env must name an environment variable")
        if isinstance(self.refresh_interval_seconds, bool) or not isinstance(self.refresh_interval_seconds, int):
            raise RealtimeConfigError("refresh_interval_seconds must be an integer")
        if not MIN_REFRESH_SECONDS <= self.refresh_interval_seconds <= MAX_REFRESH_SECONDS:
            raise RealtimeConfigError(
                f"refresh_interval_seconds must be between {MIN_REFRESH_SECONDS} and {MAX_REFRESH_SECONDS}"
            )
        if isinstance(self.request_timeout_seconds, bool) or not isinstance(self.request_timeout_seconds, int):
            raise RealtimeConfigError("request_timeout_seconds must be an integer")
        if not 1 <= self.request_timeout_seconds <= 120:
            raise RealtimeConfigError("request_timeout_seconds must be between 1 and 120")
        if isinstance(self.max_source_age_seconds, bool) or not isinstance(self.max_source_age_seconds, int):
            raise RealtimeConfigError("max_source_age_seconds must be an integer")
        if self.max_source_age_seconds < self.refresh_interval_seconds:
            raise RealtimeConfigError("max_source_age_seconds cannot be less than refresh interval")
        if isinstance(self.future_skew_seconds, bool) or not isinstance(self.future_skew_seconds, int):
            raise RealtimeConfigError("future_skew_seconds must be an integer")
        if self.future_skew_seconds < 0:
            raise RealtimeConfigError("future_skew_seconds cannot be negative")
        if self.confirmed_read_model_path is not None:
            if not isinstance(self.confirmed_read_model_path, str) or not self.confirmed_read_model_path.strip():
                raise RealtimeConfigError("confirmed_read_model_path must be a non-empty local path or null")
            path_text = self.confirmed_read_model_path.strip()
            if path_text == ":memory:" or path_text.lower().startswith(("http://", "https://", "//", "\\\\")):
                raise RealtimeConfigError("confirmed_read_model_path must be a local SQLite path")
        for name in (self.display_timezone, self.market_timezone):
            try:
                ZoneInfo(name)
            except ZoneInfoNotFoundError as exc:
                raise RealtimeConfigError(f"unknown timezone: {name}") from exc
        if self.display_timezone != "Asia/Shanghai":
            raise RealtimeConfigError("display_timezone is fixed to Asia/Shanghai for M16")
        if len({item.symbol for item in self.symbols}) != len(self.symbols):
            raise RealtimeConfigError("symbols must be unique")

    def public_dict(self) -> dict[str, Any]:
        """Return safe configuration metadata; never includes a key value."""

        return {
            "provider": self.provider,
            "mode": self.mode,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "refresh_interval_seconds": self.refresh_interval_seconds,
            "request_timeout_seconds": self.request_timeout_seconds,
            "display_timezone": self.display_timezone,
            "market_timezone": self.market_timezone,
            "max_source_age_seconds": self.max_source_age_seconds,
            "future_skew_seconds": self.future_skew_seconds,
            "confirmed_read_model_path": self.confirmed_read_model_path,
            "symbols": [item.__dict__.copy() for item in self.symbols],
        }


def load_realtime_config(path: str | Path = DEFAULT_CONFIG_PATH) -> RealtimeConfig:
    return RealtimeConfig.from_file(path)
