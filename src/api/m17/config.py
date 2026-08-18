"""Validated, non-secret configuration for the M17 local portal."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "m17" / "unified.json"
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class M17ConfigError(ValueError):
    """Raised when a local portal configuration is unsafe or incomplete."""


def _local_path(raw: Any, *, root: Path, field: str, default: Path | None = None, must_exist: bool = False) -> Path:
    value = default if raw in (None, "") else Path(str(raw).strip())
    if value is None:
        raise M17ConfigError(f"{field} is required")
    text = str(value)
    lowered = text.lower()
    if lowered.startswith(("http://", "https://", "//", "\\\\")):
        raise M17ConfigError(f"{field} must be a local path")
    path = value.expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if must_exist and not path.exists():
        raise M17ConfigError(f"{field} does not exist")
    return path


def _local_http_origin(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise M17ConfigError("confirmed_api_base_url is required")
    text = value.strip()
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as exc:
        raise M17ConfigError("confirmed_api_base_url has an invalid port") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in LOCAL_HOSTS
        or port is not None and not 1 <= port <= 65_535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise M17ConfigError("confirmed_api_base_url must be a loopback HTTP origin without credentials")
    return text.rstrip("/")


def _integer(raw: Any, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or not minimum <= raw <= maximum:
        raise M17ConfigError(f"{field} must be an integer between {minimum} and {maximum}")
    return raw


@dataclass(frozen=True)
class M17Config:
    m17_version: str
    host: str
    port: int
    static_root: Path
    m16_config_path: Path
    confirmed_api_base_url: str
    confirmed_api_token_env: str | None
    paper_config_path: Path
    display_timezone: str
    heartbeat_seconds: int
    confirmed_api_timeout_seconds: int

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, root: str | Path | None = None) -> "M17Config":
        if not isinstance(raw, Mapping):
            raise M17ConfigError("M17 config must be an object")
        base = Path(root).expanduser().resolve() if root is not None else Path(__file__).resolve().parents[3]
        host = str(raw.get("host", "127.0.0.1")).strip().lower()
        if host not in LOCAL_HOSTS:
            raise M17ConfigError("M17 server must bind to localhost")
        token_env = raw.get("confirmed_api_token_env")
        if token_env in (None, ""):
            normalized_token_env = None
        else:
            normalized_token_env = str(token_env).strip()
            if not _ENV_NAME.fullmatch(normalized_token_env):
                raise M17ConfigError("confirmed_api_token_env must be an environment variable name")
        display_timezone = str(raw.get("display_timezone", "")).strip()
        try:
            ZoneInfo(display_timezone)
        except ZoneInfoNotFoundError as exc:
            raise M17ConfigError(f"unknown display_timezone: {display_timezone}") from exc
        if display_timezone != "Asia/Shanghai":
            raise M17ConfigError("display_timezone is fixed to Asia/Shanghai for M17")
        value = cls(
            m17_version=str(raw.get("m17_version", "")),
            host=host,
            port=_integer(raw.get("port", 0), field="port", minimum=0, maximum=65_535),
            static_root=_local_path(raw.get("static_root"), root=base, field="static_root", default=base / "frontend", must_exist=True),
            m16_config_path=_local_path(raw.get("m16_config_path"), root=base, field="m16_config_path", default=base / "configs" / "realtime" / "massive.json", must_exist=True),
            confirmed_api_base_url=_local_http_origin(raw.get("confirmed_api_base_url")),
            confirmed_api_token_env=normalized_token_env,
            paper_config_path=_local_path(raw.get("paper_config_path"), root=base, field="paper_config_path", default=base / "configs" / "paper" / "m17.json"),
            display_timezone=display_timezone,
            heartbeat_seconds=_integer(raw.get("heartbeat_seconds", 15), field="heartbeat_seconds", minimum=1, maximum=120),
            confirmed_api_timeout_seconds=_integer(raw.get("confirmed_api_timeout_seconds", 5), field="confirmed_api_timeout_seconds", minimum=1, maximum=30),
        )
        value.validate()
        return value

    @classmethod
    def from_file(cls, path: str | Path = DEFAULT_CONFIG_PATH) -> "M17Config":
        source = Path(path).expanduser().resolve()
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise M17ConfigError(f"unable to load M17 config: {source}") from exc
        # Versioned repository configs live below configs/m17; custom test or
        # personal configs may pass absolute paths for every dependency.
        root = source.parents[2] if len(source.parents) >= 3 and source.parent.name == "m17" else source.parent
        return cls.from_mapping(raw, root=root)

    def validate(self) -> None:
        if not self.m17_version.startswith("m17-"):
            raise M17ConfigError("m17_version must be a versioned M17 identifier")
        if self.host not in LOCAL_HOSTS:
            raise M17ConfigError("M17 server must bind to localhost")
        if not self.static_root.is_dir():
            raise M17ConfigError("static_root must be an existing directory")
        if not self.m16_config_path.is_file():
            raise M17ConfigError("m16_config_path must be an existing file")
        if not self.paper_config_path.exists():
            # A missing personal holdings file is allowed at startup; the
            # paper-plan endpoint will fail closed with INPUT_REQUIRED.
            return
        if self.paper_config_path.is_dir():
            raise M17ConfigError("paper_config_path must be a file path")

    def public_dict(self) -> dict[str, Any]:
        """Return safe metadata; never include key or token values."""

        return {
            "m17_version": self.m17_version,
            "host": self.host,
            "port": self.port,
            "static_root": self.static_root.name,
            "m16_config": self.m16_config_path.name,
            "confirmed_api_base_url": self.confirmed_api_base_url,
            "confirmed_api_token_env_configured": self.confirmed_api_token_env is not None,
            "paper_config": self.paper_config_path.name,
            "display_timezone": self.display_timezone,
            "heartbeat_seconds": self.heartbeat_seconds,
            "confirmed_api_timeout_seconds": self.confirmed_api_timeout_seconds,
        }


def load_m17_config(path: str | Path = DEFAULT_CONFIG_PATH) -> M17Config:
    return M17Config.from_file(path)


__all__ = ["DEFAULT_CONFIG_PATH", "M17Config", "M17ConfigError", "load_m17_config"]
