"""Validated local configuration for the additive M18 workbench."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.storage.paper_portfolio import PaperExecutionConfig


LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "m18" / "workbench.json"
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class M18ConfigError(ValueError):
    """Raised when the local M18 configuration is unsafe or incomplete."""


def _integer(value: Any, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise M18ConfigError(f"{field} must be an integer between {minimum} and {maximum}")
    return value


def _date(value: Any, *, field: str, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise M18ConfigError(f"{field} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise M18ConfigError(f"{field} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise M18ConfigError(f"{field} must be YYYY-MM-DD")
    return value


def _local_path(
    raw: Any,
    *,
    root: Path,
    field: str,
    default: Path | None = None,
    must_exist: bool = False,
    allow_memory: bool = False,
) -> str | Path:
    value = default if raw in (None, "") else raw
    if value is None:
        raise M18ConfigError(f"{field} is required")
    text = os.path.expandvars(str(value).strip())
    if allow_memory and text == ":memory:":
        return text
    lowered = text.lower()
    if lowered.startswith(("http://", "https://", "//", "\\\\")):
        raise M18ConfigError(f"{field} must be a local path")
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    if must_exist and not resolved.exists():
        raise M18ConfigError(f"{field} does not exist: {resolved}")
    return resolved


@dataclass(frozen=True)
class M18Config:
    m18_version: str
    host: str
    port: int
    static_root: Path
    database_path: str | Path
    realtime_config_path: Path
    display_timezone: str
    refresh_interval_seconds: int
    history_start_date: str
    history_end_date: str | None
    history_symbols: tuple[str, ...] | None
    paper_portfolio_id: str
    paper_execution: PaperExecutionConfig

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, root: str | Path | None = None) -> "M18Config":
        if not isinstance(raw, Mapping):
            raise M18ConfigError("M18 config must be an object")
        base = Path(root).expanduser().resolve() if root is not None else Path(__file__).resolve().parents[3]
        host = str(raw.get("host", "127.0.0.1")).strip().lower()
        if host not in LOCAL_HOSTS:
            raise M18ConfigError("M18 server must bind to localhost")
        display_timezone = str(raw.get("display_timezone", "")).strip()
        try:
            ZoneInfo(display_timezone)
        except ZoneInfoNotFoundError as exc:
            raise M18ConfigError(f"unknown display_timezone: {display_timezone}") from exc
        if display_timezone != "Asia/Shanghai":
            raise M18ConfigError("display_timezone is fixed to Asia/Shanghai for M18")

        history_start = _date(raw.get("history_start_date", "2008-01-01"), field="history_start_date")
        history_end = _date(raw.get("history_end_date"), field="history_end_date", optional=True)
        if history_end is not None and history_start > history_end:
            raise M18ConfigError("history_start_date must not be after history_end_date")

        symbols_raw = raw.get("history_symbols")
        if symbols_raw in (None, []):
            history_symbols = None
        else:
            if not isinstance(symbols_raw, list) or not symbols_raw:
                raise M18ConfigError("history_symbols must be a non-empty list or null")
            history_symbols = tuple(str(item).strip().upper() for item in symbols_raw)
            if any(not item for item in history_symbols) or len(set(history_symbols)) != len(history_symbols):
                raise M18ConfigError("history_symbols must contain unique non-empty symbols")

        paper_raw = raw.get("paper", {})
        if not isinstance(paper_raw, Mapping):
            raise M18ConfigError("paper must be an object")
        try:
            paper_execution = PaperExecutionConfig(
                initial_cash=paper_raw.get("initial_cash", 100_000.0),
                cost_bps=paper_raw.get("cost_bps", 5.0),
                slippage_bps=paper_raw.get("slippage_bps", 0.0),
                price_basis=paper_raw.get("price_basis", "adjusted_ohlcv"),
                allow_fractional_shares=paper_raw.get("allow_fractional_shares", True),
            )
        except (TypeError, ValueError) as exc:
            raise M18ConfigError("paper execution configuration is invalid") from exc

        portfolio_id = str(raw.get("paper_portfolio_id", "m18-paper")).strip()
        if not portfolio_id or "|" in portfolio_id:
            raise M18ConfigError("paper_portfolio_id is invalid")
        value = cls(
            m18_version=str(raw.get("m18_version", "")),
            host=host,
            port=_integer(raw.get("port", 0), field="port", minimum=0, maximum=65_535),
            static_root=Path(_local_path(raw.get("static_root"), root=base, field="static_root", default=base / "frontend" / "m18", must_exist=True)),
            database_path=_local_path(
                raw.get("database_path"),
                root=base,
                field="database_path",
                default=Path("%LOCALAPPDATA%") / "QQQ-Thermometer-Personal" / "m18.sqlite3",
                allow_memory=True,
            ),
            realtime_config_path=Path(_local_path(raw.get("realtime_config_path"), root=base, field="realtime_config_path", default=base / "configs" / "realtime" / "massive.json", must_exist=True)),
            display_timezone=display_timezone,
            refresh_interval_seconds=_integer(raw.get("refresh_interval_seconds", 900), field="refresh_interval_seconds", minimum=60, maximum=86_400),
            history_start_date=history_start,  # type: ignore[arg-type]
            history_end_date=history_end,
            history_symbols=history_symbols,
            paper_portfolio_id=portfolio_id,
            paper_execution=paper_execution,
        )
        value.validate()
        return value

    @classmethod
    def from_file(cls, path: str | Path = DEFAULT_CONFIG_PATH) -> "M18Config":
        source = Path(path).expanduser().resolve()
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise M18ConfigError(f"unable to load M18 config: {source}") from exc
        root = source.parents[2] if source.parent.name == "m18" else source.parent
        return cls.from_mapping(raw, root=root)

    def validate(self) -> None:
        if not self.m18_version.startswith("m18-"):
            raise M18ConfigError("m18_version must be a versioned M18 identifier")
        if self.host not in LOCAL_HOSTS:
            raise M18ConfigError("M18 server must bind to localhost")
        if not self.static_root.is_dir():
            raise M18ConfigError("static_root must be a directory")
        if not self.realtime_config_path.is_file():
            raise M18ConfigError("realtime_config_path must be a file")
        if self.database_path != ":memory:" and not isinstance(self.database_path, Path):
            raise M18ConfigError("database_path must be a local path")
        if self.history_end_date is not None and self.history_start_date > self.history_end_date:
            raise M18ConfigError("history_start_date must not be after history_end_date")
        if self.refresh_interval_seconds < 60:
            raise M18ConfigError("refresh_interval_seconds must be at least 60 seconds")

    def public_dict(self) -> dict[str, Any]:
        return {
            "m18_version": self.m18_version,
            "host": self.host,
            "port": self.port,
            "static_root": self.static_root.name,
            "database_path": ":memory:" if self.database_path == ":memory:" else str(self.database_path),
            "realtime_config": self.realtime_config_path.name,
            "display_timezone": self.display_timezone,
            "refresh_interval_seconds": self.refresh_interval_seconds,
            "history_start_date": self.history_start_date,
            "history_end_date": self.history_end_date,
            "history_symbols": None if self.history_symbols is None else list(self.history_symbols),
            "paper_portfolio_id": self.paper_portfolio_id,
            "paper_execution": self.paper_execution.as_dict(),
        }


def load_m18_config(path: str | Path = DEFAULT_CONFIG_PATH) -> M18Config:
    return M18Config.from_file(path)


__all__ = ["DEFAULT_CONFIG_PATH", "M18Config", "M18ConfigError", "load_m18_config"]
