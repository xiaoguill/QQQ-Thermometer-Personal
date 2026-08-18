"""Start the additive M18 full-chain personal workbench."""

from __future__ import annotations

import argparse
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.jobs.m18.full_chain import M18PipelineRequest
from src.jobs.m18.massive_history import MassiveDailyHistoryAdapter
from src.jobs.m18.read_model import ProvisionalObservation, RuntimeBoundary
from src.jobs.m18.runtime import M18RuntimeService
from src.realtime.config import load_realtime_config
from src.realtime.massive_client import MassiveClient, MassiveClientError, MissingApiKeyError
from src.storage.normalization import TradingCalendar
from src.storage.sqlite_store import SQLiteRepository, SQLiteStore

from .config import DEFAULT_CONFIG_PATH, M18Config, load_m18_config
from .http_server import create_http_server
from .service import M18ApiService, create_empty_snapshot


_SENSITIVE_ERROR_PATTERN = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|token|secret|password)"
    r"(?:\s*[:=]\s*|\s+)[^,;\s]+"
)


def _safe_startup_error(exc: BaseException) -> str:
    """Return a bounded diagnostic without exposing credentials or payloads."""

    message = str(exc).strip() or "no_message"
    message = _SENSITIVE_ERROR_PATTERN.sub(
        lambda match: f"{match.group(1)}=<redacted>",
        message,
    )
    return f"{type(exc).__name__}: {message[:240]}"


def _latest_session(calendar: TradingCalendar, start_date: str, end_date: str) -> str:
    sessions = calendar.sessions(start_date, end_date)
    if not sessions:
        raise ValueError("history range contains no trading sessions")
    return sessions[-1]


def _history_end_date(config: M18Config, calendar: TradingCalendar, *, now: datetime) -> str:
    market_now = now.astimezone(ZoneInfo("America/New_York"))
    configured = config.history_end_date or (market_now.date() - timedelta(days=1)).isoformat()
    return _latest_session(calendar, config.history_start_date, configured)


def _source_status(batch: Any) -> tuple[str, tuple[str, ...]]:
    observations = getattr(batch, "observations", ())
    failed = tuple(
        f"{item.symbol}:{item.quality}{':' + item.error_code if item.error_code else ''}"
        for item in observations
        if item.quality != "OK"
    )
    return ("READY" if not failed else "DEGRADED", failed)


def _provisional_from_batch(batch: Any, *, source_version: str) -> ProvisionalObservation:
    observations = tuple(getattr(batch, "observations", ()))
    status, failures = _source_status(batch)
    as_of = batch.fetched_at_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    observation_values = {
        item.symbol: item.as_dict(display_timezone="Asia/Shanghai")
        for item in observations
    }
    return ProvisionalObservation(
        status="READY" if status == "READY" else "NEEDS_REVIEW",
        quality="OK" if status == "READY" else "NEEDS_REVIEW",
        as_of=as_of,
        signal_date=None,
        # M16 owns observations only. It does not invent or recompute the
        # confirmed strategy temperature; the UI therefore shows this as an
        # explicit unavailable provisional temperature until a temperature
        # producer publishes one under the M16 contract.
        temperature=None,
        state=None,
        source_version=source_version,
        run_id=batch.batch_id,
        reason_codes=("m16_observation_only_temperature_not_published", *failures),
        source_symbols=tuple(item.symbol for item in observations),
        observations=observation_values,
    )


def _runtime_boundary(config: M18Config, *, configured: bool, status: str, last_refresh_at: str | None, reasons: tuple[str, ...]) -> RuntimeBoundary:
    return RuntimeBoundary(
        source="Massive",
        source_configured=configured,
        refresh_interval_seconds=config.refresh_interval_seconds,
        display_timezone=config.display_timezone,
        source_status=status,
        last_refresh_at=last_refresh_at,
        reason_codes=reasons,
    )


def build_application(config: M18Config, *, environ: dict[str, str] | None = None) -> tuple[M18ApiService, SQLiteRepository, bool]:
    """Open the local store and run at most one full-chain refresh.

    M18 intentionally has no new scheduler. M16 remains the existing 15-minute
    observation runtime; the M18 invocation performs one bounded daily
    M02–M11 publication and then serves the immutable read model.
    """

    config.validate()
    realtime_config = load_realtime_config(config.realtime_config_path)
    environment = os.environ if environ is None else environ
    key_configured = bool(environment.get(realtime_config.api_key_env))
    store = SQLiteStore(config.database_path)
    repository = SQLiteRepository(store).initialize()
    runtime = _runtime_boundary(
        config,
        configured=key_configured,
        status="UNAVAILABLE" if not key_configured else "DEGRADED",
        last_refresh_at=None,
        reasons=("massive_api_key_unavailable",) if not key_configured else ("initial_refresh_pending",),
    )

    if key_configured:
        try:
            client = MassiveClient.from_env(realtime_config, environ=environment)
            calendar = TradingCalendar()
            now = datetime.now(timezone.utc)
            end_date = _history_end_date(config, calendar, now=now)
            adapter = MassiveDailyHistoryAdapter(realtime_config, client)
            raw = adapter.fetch(
                start_date=config.history_start_date,
                end_date=end_date,
                symbols=config.history_symbols,
                retrieved_at=now.isoformat().replace("+00:00", "Z"),
            )
            batch = client.fetch_batch(fetched_at_utc=now)
            provisional = _provisional_from_batch(batch, source_version="m16-massive-runtime/v1")
            source_state, source_reasons = _source_status(batch)
            timestamp = now.isoformat().replace("+00:00", "Z")
            boundary = _runtime_boundary(config, configured=True, status=source_state, last_refresh_at=timestamp, reasons=source_reasons)
            service = M18RuntimeService(
                repository,
                calendar=calendar,
                paper_config=config.paper_execution,
                portfolio_id=config.paper_portfolio_id,
            )
            service.run(
                M18PipelineRequest(
                    run_id=f"m18-{end_date}-{int(now.timestamp())}",
                    as_of=timestamp,
                    data_version="massive-daily-aggregates/v1",
                    raw_snapshots=tuple(raw),
                    runtime_boundary=boundary,
                    provisional_observation=provisional,
                )
            )
        except (MassiveClientError, MissingApiKeyError, ValueError, OSError) as exc:
            # Keep the server available with a typed fail-closed state. The
            # exception type is safe to expose locally as a reason code; the
            # key and provider response are never included.
            print(f"M18 startup refresh failed: {_safe_startup_error(exc)}", flush=True)
            runtime = _runtime_boundary(
                config,
                configured=key_configured,
                status="FAILED",
                last_refresh_at=None,
                reasons=(type(exc).__name__,),
            )
        else:
            runtime = boundary
    application = M18ApiService(repository, empty_snapshot=create_empty_snapshot(runtime))
    return application, repository, key_configured


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local M18 full-chain QQQ Thermometer workbench.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="versioned non-secret M18 configuration")
    parser.add_argument("--host", default=None, help="optional localhost bind override")
    parser.add_argument("--port", type=int, default=None, help="optional localhost port override")
    args = parser.parse_args()
    config = load_m18_config(args.config)
    application, repository, key_configured = build_application(config)
    server = create_http_server(
        application,
        host=args.host or config.host,
        port=config.port if args.port is None else args.port,
        static_root=config.static_root,
    )
    print(f"M18 full-chain workbench: http://{server.server_address[0]}:{server.server_address[1]}/")
    print(f"Massive key configured={key_configured} refresh_interval_seconds={config.refresh_interval_seconds} display_timezone={config.display_timezone}")
    print("paper_only=True execution_allowed=False broker_connected=False")
    print("M18 performs one bounded full-chain refresh at startup; M16 remains the existing live observation runtime.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
        repository.store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
