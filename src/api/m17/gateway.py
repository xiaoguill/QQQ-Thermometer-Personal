"""M17 application services and the local read-only data facade."""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from src.api.live_api import LiveApiService, create_live_app
from src.api.read_api import ApiResponse
from src.notifications import LiveEventBus
from src.paper.m17.plan import PaperPlanError, build_paper_plan, empty_paper_plan, load_paper_input
from src.realtime.config import RealtimeConfig, load_realtime_config
from src.realtime.massive_client import MissingApiKeyError
from src.realtime.runtime import RuntimeBundle, RealtimeRuntime, RuntimeSnapshot, create_runtime_from_env

from .config import M17Config


READ_ONLY_ROUTES = frozenset(
    {
        "/api/thermometer/latest",
        "/api/thermometer/history",
        "/api/signals/explain",
        "/api/triggers/next",
        "/api/portfolio/targets",
        "/api/portfolio/latest",
        "/api/portfolio/ledger",
        "/api/performance/curve",
        "/api/performance/metrics",
        "/api/data-quality/latest",
        "/api/versions",
    }
)
LEGACY_ROUTES = (
    "/dashboard/index.html",
    "/m14/index.html",
    "/demo/index.html",
    "/shell/index.html",
    "/m16/index.html",
)


class LocalReadApiError(RuntimeError):
    """A safe, non-secret failure from the existing localhost read API."""


@dataclass(frozen=True)
class LocalReadResponse:
    status_code: int
    body: Mapping[str, Any]


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class LocalReadApiClient:
    """GET-only adapter for the existing localhost-confirmed read model."""

    def __init__(
        self,
        base_url: str,
        *,
        token_env: str | None = None,
        environ: Mapping[str, str] | None = None,
        timeout_seconds: int = 5,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("confirmed API must be a loopback HTTP origin")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("confirmed API origin must not contain credentials, path, query, or fragment")
        self.base_url = base_url.rstrip("/")
        self.token_env = token_env
        self.environ = os.environ if environ is None else environ
        self.timeout_seconds = timeout_seconds
        self._opener = build_opener(_NoRedirectHandler)

    def _url(self, path: str, query: Mapping[str, Any] | None = None) -> str:
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise ValueError("read API path must be local and relative")
        route = parsed.path or "/"
        if route not in READ_ONLY_ROUTES:
            raise ValueError("read API route is not in the frozen read-only allowlist")
        query_values: list[tuple[str, str]] = []
        if parsed.query:
            query_values.extend((key, value) for key, values in _parse_query(parsed.query).items() for value in values)
        if query:
            query_values.extend((str(key), str(value)) for key, value in query.items())
        encoded_query = urlencode(query_values)
        return urlunsplit(("http", urlsplit(self.base_url).netloc, route, encoded_query, ""))

    def get(self, path: str, *, query: Mapping[str, Any] | None = None) -> LocalReadResponse:
        url = self._url(path, query)
        headers = {"Accept": "application/json"}
        if self.token_env:
            token = self.environ.get(self.token_env, "")
            if token:
                # The value is read only inside this process and never returned
                # or included in an exception message.
                headers["X-QQQ-Local-Token"] = token
        request = Request(url, method="GET", headers=headers)
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:  # noqa: S310 - origin is validated
                return LocalReadResponse(int(response.status), _decode_json(response.read()))
        except HTTPError as exc:
            try:
                body = _decode_json(exc.read())
            except OSError:
                body = {}
            return LocalReadResponse(int(exc.code), body)
        except (URLError, OSError, TimeoutError) as exc:
            raise LocalReadApiError("confirmed read API is unavailable") from exc


def _parse_query(raw_query: str) -> dict[str, list[str]]:
    from urllib.parse import parse_qs

    return parse_qs(raw_query, keep_blank_values=True)


def _decode_json(raw: bytes) -> dict[str, Any]:
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalReadApiError("confirmed read API returned invalid JSON") from exc
    if not isinstance(body, dict):
        raise LocalReadApiError("confirmed read API returned an invalid object")
    return body


@dataclass
class M17RuntimeHandle:
    """Owns the optional M16 worker while keeping the HTTP layer stoppable."""

    realtime_config: RealtimeConfig
    live_api: LiveApiService
    runtime: RealtimeRuntime | None
    bundle: RuntimeBundle | None
    massive_key_configured: bool
    startup_status: str
    startup_error_code: str | None = None

    @property
    def event_bus(self) -> LiveEventBus:
        return self.live_api.event_bus

    @property
    def snapshot(self) -> RuntimeSnapshot:
        if self.runtime is None:
            return RuntimeSnapshot(self.startup_status, None, None, None, 0)
        return self.runtime.snapshot

    def start(self) -> None:
        if self.runtime is not None:
            self.runtime.start()

    def stop(self) -> None:
        if self.runtime is not None:
            self.runtime.stop()


def _create_runtime_handle(config: M17Config) -> M17RuntimeHandle:
    realtime_config = load_realtime_config(config.m16_config_path)
    key_configured = bool(os.environ.get(realtime_config.api_key_env, "").strip())
    try:
        bundle = create_runtime_from_env(config.m16_config_path)
    except MissingApiKeyError:
        event_bus = LiveEventBus()
        live_api = create_live_app(event_bus, heartbeat_seconds=config.heartbeat_seconds)
        live_api.publish_service_status("MASSIVE_API_KEY_UNAVAILABLE", occurred_at_utc=datetime.now(timezone.utc))
        return M17RuntimeHandle(realtime_config, live_api, None, None, False, "MASSIVE_API_KEY_UNAVAILABLE", "MISSING_API_KEY")
    return M17RuntimeHandle(realtime_config, bundle.live_api, bundle.runtime, bundle, key_configured, "ready")


def _display_time(value: datetime | str | None, timezone_name: str = "Asia/Shanghai") -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return value
    else:
        parsed = value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(ZoneInfo(timezone_name)).isoformat()


def _utc_time(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return value
    else:
        parsed = value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _runtime_snapshot(snapshot: RuntimeSnapshot, display_timezone: str) -> dict[str, Any]:
    return {
        "status": snapshot.status,
        "last_batch_id": snapshot.last_batch_id,
        "last_fetched_at_utc": _utc_time(snapshot.last_fetched_at_utc),
        "last_fetched_at": _display_time(snapshot.last_fetched_at_utc, display_timezone),
        "next_refresh_at_utc": _utc_time(snapshot.next_refresh_at_utc),
        "next_refresh_at": _display_time(snapshot.next_refresh_at_utc, display_timezone),
        "consecutive_failures": snapshot.consecutive_failures,
    }


def _safe_meta(meta: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(meta, Mapping):
        return {"data_quality": "failed", "strategy_version": "unavailable"}
    names = (
        "contract_version",
        "strategy_version",
        "as_of",
        "signal_date",
        "execution_date",
        "data_quality",
        "run_id",
        "code_version",
        "data_version",
        "evidence_ref",
    )
    return {name: copy.deepcopy(meta.get(name)) for name in names if name in meta}


def _normalized_weights(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, float] = {}
    for symbol, weight in value.items():
        try:
            number = float(weight)
        except (TypeError, ValueError):
            continue
        if number >= 0 and number == number and number not in {float("inf"), float("-inf")}:
            result[str(symbol).strip().upper()] = number
    return dict(sorted((key, value) for key, value in result.items() if key))


class M17Application:
    """Read-only façade consumed by the M17 web adapter."""

    def __init__(self, config: M17Config, runtime_handle: M17RuntimeHandle, read_client: LocalReadApiClient) -> None:
        self.config = config
        self.runtime_handle = runtime_handle
        self.read_client = read_client

    @property
    def live_api(self) -> LiveApiService:
        return self.runtime_handle.live_api

    def _confirmed_snapshot(self) -> tuple[dict[str, Any] | None, dict[str, Any], str | None]:
        try:
            result = self.read_client.get("/api/thermometer/latest")
        except LocalReadApiError:
            # M16 can optionally mount an existing local SQLite read model. It
            # is a read-only fallback for a personal single-process setup; it
            # never creates a second strategy calculation.
            if self.runtime_handle.live_api.confirmed_read_model_path is None:
                return None, {}, "CONFIRMED_API_UNAVAILABLE"
            try:
                mounted = self.runtime_handle.live_api.latest_confirmed(client_host="127.0.0.1")
            except Exception:
                return None, {}, "CONFIRMED_API_UNAVAILABLE"
            result_status = mounted.status_code
            body = mounted.body
        else:
            result_status = result.status_code
            body = result.body
        meta = _safe_meta(body.get("meta"))
        data = body.get("data") if isinstance(body.get("data"), Mapping) else {}
        quality = str(meta.get("data_quality", "failed")).lower()
        state = data.get("state") if isinstance(data.get("state"), str) else None
        if result_status != 200 or quality != "ok" or not state:
            return None, meta, str(body.get("error", {}).get("code", "CONFIRMED_UNAVAILABLE")) if isinstance(body.get("error"), Mapping) else "CONFIRMED_UNAVAILABLE"
        weights = _normalized_weights(data.get("target_weights"))
        if not weights:
            try:
                target_result = self.read_client.get("/api/portfolio/targets")
                target_body = target_result.body
                target_meta = _safe_meta(target_body.get("meta"))
                if target_result.status_code == 200 and str(target_meta.get("data_quality", "failed")).lower() == "ok":
                    target_data = target_body.get("data") if isinstance(target_body.get("data"), Mapping) else {}
                    weights = _normalized_weights(target_data.get("target_weights"))
            except LocalReadApiError:
                pass
        confirmed = {
            "confirmed": True,
            "provisional": False,
            "state": state,
            "temperature": data.get("temperature"),
            "trend": data.get("trend") if isinstance(data.get("trend"), str) else None,
            "signal_agreement": data.get("signal_agreement"),
            "reason_codes": list(data.get("reason_codes", [])) if isinstance(data.get("reason_codes"), list) else [],
            "target_weights": weights,
        }
        return confirmed, meta, None

    def _latest_batch(self) -> dict[str, Any] | None:
        cursor = self.runtime_handle.event_bus.events_after(None)
        for event in reversed(cursor.events):
            if event.event_type != "observation.batch":
                continue
            payload = copy.deepcopy(dict(event.payload))
            fetched_at = payload.get("fetched_at")
            payload["fetched_at_utc"] = _utc_time(fetched_at)
            payload["fetched_at"] = _display_time(fetched_at, self.config.display_timezone)
            observations = payload.get("observations")
            if not isinstance(observations, list):
                payload["observations"] = []
            else:
                for item in observations:
                    if isinstance(item, Mapping):
                        item["source_timestamp_utc"] = _utc_time(item.get("source_timestamp"))
                        item["source_timestamp"] = _display_time(item.get("source_timestamp"), self.config.display_timezone)
            return payload
        return None

    def _latest_service_status(self) -> dict[str, Any] | None:
        cursor = self.runtime_handle.event_bus.events_after(None)
        for event in reversed(cursor.events):
            if event.event_type == "service.status":
                payload = dict(event.payload)
                return {
                    "status": payload.get("status"),
                    "detail": payload.get("detail"),
                    "occurred_at": _display_time(event.occurred_at_utc, self.config.display_timezone),
                }
        return None

    def _paper_input_status(self) -> dict[str, Any]:
        if not self.config.paper_config_path.is_file():
            return {"status": "INPUT_REQUIRED", "file_configured": False}
        try:
            paper_input = load_paper_input(self.config.paper_config_path)
        except PaperPlanError as exc:
            return {"status": "INVALID_INPUT", "file_configured": True, "reason": str(exc)}
        return {
            "status": "READY" if paper_input.has_explicit_holdings else "INPUT_REQUIRED",
            "file_configured": True,
            "portfolio_id": paper_input.portfolio_id,
            "position_count": len([item for item in paper_input.holdings if item.quantity > 0]),
        }

    def source_status(self, *, confirmed: dict[str, Any] | None, meta: Mapping[str, Any]) -> dict[str, Any]:
        snapshot = self.runtime_handle.snapshot
        latest_batch = self._latest_batch()
        batch_quality = latest_batch.get("quality") if latest_batch else None
        confirmed_available = confirmed is not None
        return {
            "massive": {
                "provider": self.runtime_handle.realtime_config.provider,
                "api_key_configured": self.runtime_handle.massive_key_configured,
                "status": snapshot.status,
                "refresh_interval_seconds": self.runtime_handle.realtime_config.refresh_interval_seconds,
                "symbols": [item.symbol for item in self.runtime_handle.realtime_config.symbols],
                "last_batch_quality": batch_quality,
                "last_batch_id": snapshot.last_batch_id,
                "consecutive_failures": snapshot.consecutive_failures,
            },
            "confirmed_api": {
                "base_url": self.config.confirmed_api_base_url,
                "available": confirmed_available,
                "data_quality": meta.get("data_quality", "failed"),
                "strategy_version": meta.get("strategy_version", "unavailable"),
            },
            "paper_input": self._paper_input_status(),
        }

    def overview(self) -> ApiResponse:
        confirmed, meta, confirmed_error = self._confirmed_snapshot()
        latest_batch = self._latest_batch()
        strategy = confirmed or {
            "confirmed": False,
            "provisional": False,
            "state": "needs_review",
            "temperature": None,
            "trend": None,
            "signal_agreement": None,
            "reason_codes": [confirmed_error or "CONFIRMED_UNAVAILABLE"],
            "target_weights": {},
        }
        return ApiResponse(
            200,
            {
                "schema": "qqq-m17-overview/v1",
                "generated_at": _display_time(datetime.now(timezone.utc), self.config.display_timezone),
                "display_timezone": self.config.display_timezone,
                "paper_only": True,
                "confirmed_strategy": strategy,
                "confirmed_meta": meta,
                "latest_observation": latest_batch,
                "latest_service_status": self._latest_service_status(),
                "runtime": _runtime_snapshot(self.runtime_handle.snapshot, self.config.display_timezone),
                "source_status": self.source_status(confirmed=confirmed, meta=meta),
                "legacy_routes": list(LEGACY_ROUTES),
                "boundaries": {
                    "intraday_observations_are_provisional": True,
                    "target_weights_are_not_recomputed": True,
                    "paper_plan_is_not_an_order": True,
                    "broker_access": False,
                },
            },
        )

    def paper_plan(self) -> ApiResponse:
        confirmed, meta, confirmed_error = self._confirmed_snapshot()
        latest_batch = self._latest_batch()
        as_of = latest_batch.get("fetched_at") if latest_batch else _display_time(datetime.now(timezone.utc), self.config.display_timezone)
        if confirmed is None:
            return ApiResponse(200, empty_paper_plan("CONFIRMED_UNAVAILABLE", reason=confirmed_error or "confirmed strategy is unavailable", as_of=as_of))
        try:
            paper_input = load_paper_input(self.config.paper_config_path)
        except PaperPlanError as exc:
            return ApiResponse(200, empty_paper_plan(exc.code, reason=str(exc), as_of=as_of, target_weights=confirmed.get("target_weights")))
        prices: dict[str, Any] = {}
        if latest_batch:
            for observation in latest_batch.get("observations", []):
                if isinstance(observation, Mapping):
                    price = observation.get("last") if observation.get("last") is not None else observation.get("close")
                    prices[str(observation.get("symbol", "")).upper()] = {
                        "price": price,
                        "quality": observation.get("quality"),
                        "provisional": observation.get("provisional") is True,
                    }
        try:
            plan = build_paper_plan(
                target_weights=confirmed.get("target_weights", {}),
                paper_input=paper_input,
                prices=prices,
                as_of=as_of,
                strategy_meta={
                    "state": confirmed.get("state"),
                    "signal_date": meta.get("signal_date"),
                    "strategy_version": meta.get("strategy_version"),
                    "confirmed": True,
                    "provisional": False,
                },
            )
        except PaperPlanError as exc:
            plan = empty_paper_plan(exc.code, reason=str(exc), as_of=as_of, target_weights=confirmed.get("target_weights"))
        return ApiResponse(200, plan)

    def proxy_read_api(self, path: str, *, query: Mapping[str, Any] | None = None) -> LocalReadResponse:
        return self.read_client.get(path, query=query)


def create_m17_application(config: M17Config, *, environ: Mapping[str, str] | None = None) -> M17Application:
    if environ is not None:
        # Runtime construction reads from os.environ by design so the provider
        # key never becomes part of an application object serializable to the UI.
        # Tests can temporarily set the process environment around this factory.
        original = os.environ.copy()
        try:
            for key, value in environ.items():
                os.environ[str(key)] = str(value)
            runtime_handle = _create_runtime_handle(config)
        finally:
            os.environ.clear()
            os.environ.update(original)
    else:
        runtime_handle = _create_runtime_handle(config)
    read_client = LocalReadApiClient(
        config.confirmed_api_base_url,
        token_env=config.confirmed_api_token_env,
        environ=environ,
        timeout_seconds=config.confirmed_api_timeout_seconds,
    )
    return M17Application(config, runtime_handle, read_client)


__all__ = [
    "LEGACY_ROUTES",
    "LocalReadApiClient",
    "LocalReadApiError",
    "LocalReadResponse",
    "M17Application",
    "M17RuntimeHandle",
    "READ_ONLY_ROUTES",
    "create_m17_application",
]
