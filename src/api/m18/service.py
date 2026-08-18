"""Read-only M18 workbench API plus explicit M10 compatibility delegation."""

from __future__ import annotations

import copy
import threading
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlsplit

from src.api.read_api import ApiAccessPolicy, ApiError, ApiResponse, ReadApiService
from src.storage.m18.read_model_store import M18ReadModelStore
from src.storage.sqlite_store import SQLiteRepository, StorageError, StorageValidationError
from src.jobs.m18.read_model import (
    ConfirmedStrategy,
    FullChainSnapshot,
    PaperPlanView,
    ProvisionalObservation,
    RuntimeBoundary,
    default_module_statuses,
)


M18_API_VERSION = "m18-api/v1"
_M18_ENDPOINTS = {
    ("GET", "/api/m18/workbench"),
    ("GET", "/api/m18/modules"),
    ("GET", "/api/m18/health"),
    ("GET", "/api/m18/runs"),
}


def _default_runtime() -> RuntimeBoundary:
    return RuntimeBoundary(
        source="Massive",
        source_configured=False,
        refresh_interval_seconds=900,
        display_timezone="Asia/Shanghai",
        source_status="NOT_CONFIGURED",
        last_refresh_at=None,
        reason_codes=("no_published_m18_run",),
    )


def create_empty_snapshot(runtime_boundary: RuntimeBoundary | None = None) -> FullChainSnapshot:
    """Return a typed empty state so the UI never has to invent fields."""

    runtime = runtime_boundary or _default_runtime()
    return FullChainSnapshot(
        run_id="m18-read-model-unavailable",
        run_status="needs_review",
        created_at="1970-01-01T00:00:00Z",
        as_of=None,
        signal_date=None,
        execution_date=None,
        strategy_version=None,
        data_version="unavailable",
        overall_quality="UNAVAILABLE",
        modules=default_module_statuses(),
        provisional_observation=ProvisionalObservation(
            status="NOT_RUN",
            quality="NOT_RUN",
            as_of=None,
            signal_date=None,
            temperature=None,
            state=None,
            source_version=None,
            run_id=None,
            reason_codes=("m16_observation_not_run",),
        ),
        confirmed_strategy=ConfirmedStrategy(
            status="UNAVAILABLE",
            quality="UNAVAILABLE",
            as_of=None,
            signal_date=None,
            execution_date=None,
            temperature=None,
            state=None,
            strategy_version=None,
            run_id=None,
            target_weights={},
            reason_codes=("no_confirmed_read_model",),
        ),
        paper_plan=PaperPlanView(status="NOT_GENERATED", reason_codes=("no_confirmed_read_model",)),
        runtime_boundary=runtime,
        latest_data_quality=({"status": "UNAVAILABLE", "reason": "no_published_m18_run"},),
        reason_codes=("no_published_m18_run",),
    )


class M18ApiService:
    """Local-only read service; all strategy results come from M18 storage."""

    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        access_policy: ApiAccessPolicy | None = None,
        empty_snapshot: FullChainSnapshot | None = None,
    ) -> None:
        if not isinstance(repository, SQLiteRepository) or not repository.store.initialized:
            raise StorageValidationError("M18 API requires an initialized SQLiteRepository")
        self.repository = repository
        self.read_model = M18ReadModelStore(repository)
        self.access_policy = access_policy or ApiAccessPolicy()
        self.compatibility = ReadApiService(repository, access_policy=self.access_policy)
        self.empty_snapshot = empty_snapshot or create_empty_snapshot()
        # The underlying M08 connection intentionally remains single-threaded.
        # M18 is a startup-refresh/read-only portal, so capture the immutable
        # projection on the owner thread and let HTTP worker threads serve that
        # projection without crossing SQLite's thread boundary.
        self._owner_thread_id = threading.get_ident()
        recent = self.read_model.recent(limit=100)
        self._recent_cache = recent
        self._snapshot_cache = recent[-1] if recent else self.empty_snapshot

    def latest(self) -> FullChainSnapshot:
        return self._snapshot_cache

    @staticmethod
    def _meta(snapshot: FullChainSnapshot) -> dict[str, Any]:
        return {
            "api_version": M18_API_VERSION,
            "read_model_schema": "qqq-m18-full-chain-workbench/v1",
            "read_model_version": "m18-full-chain/v1",
            "run_id": snapshot.run_id,
            "data_quality": snapshot.overall_quality,
            "as_of": snapshot.as_of or "1970-01-01T00:00:00Z",
            "signal_date": snapshot.signal_date or "1970-01-01",
            "execution_date": snapshot.execution_date,
            "strategy_version": snapshot.strategy_version or "unavailable",
            "data_version": snapshot.data_version,
            "provisional_publication": "PROVISIONAL",
            "confirmed_publication": "CONFIRMED",
            "source_of_truth": "M18 SQLite read model",
        }

    def _response(self, data: Any, snapshot: FullChainSnapshot) -> ApiResponse:
        return ApiResponse(200, {"data": copy.deepcopy(data), "meta": self._meta(snapshot)})

    def handle(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Any = None,
        headers: Mapping[str, Any] | None = None,
        client_host: str | None = "127.0.0.1",
    ) -> ApiResponse:
        try:
            self.access_policy.authorize(client_host, headers)
            method = str(method).upper()
            route = urlsplit(str(path)).path or "/"
            if (method, route) in _M18_ENDPOINTS:
                if query:
                    raise ApiError(400, "INVALID_REQUEST", "M18 endpoint does not accept query parameters")
                snapshot = self.latest()
                if route == "/api/m18/workbench":
                    return self._response(snapshot.as_dict(), snapshot)
                if route == "/api/m18/modules":
                    return self._response({"modules": [item.as_dict() for item in snapshot.modules]}, snapshot)
                if route == "/api/m18/health":
                    return self._response(
                        {
                            "runtime_boundary": snapshot.runtime_boundary.as_dict(),
                            "overall_quality": snapshot.overall_quality,
                            "confirmed_status": snapshot.confirmed_strategy.status,
                            "provisional_status": snapshot.provisional_observation.status,
                            "paper_plan_status": snapshot.paper_plan.status,
                        },
                        snapshot,
                    )
                return self._response(
                    {
                        "runs": [
                            {
                                "run_id": item.run_id,
                                "run_status": item.run_status,
                                "created_at": item.created_at,
                                "as_of": item.as_of,
                                "overall_quality": item.overall_quality,
                                "confirmed_status": item.confirmed_strategy.status,
                            }
                            for item in self._recent_cache
                        ]
                    },
                    snapshot,
                )
            if route.startswith("/api/m18/"):
                raise ApiError(404, "NOT_FOUND", "M18 endpoint was not found")
            if method != "GET":
                raise ApiError(405, "METHOD_NOT_ALLOWED", "M18 workbench is read-only; paper and broker writes are unavailable")
            return self.compatibility.handle(
                method,
                path,
                query=query,
                body=body,
                headers=headers,
                client_host=client_host,
            )
        except ApiError as exc:
            return ApiResponse(
                exc.status_code,
                {
                    "error": {"code": exc.code, "message": exc.message, "details": copy.deepcopy(exc.details)},
                    "meta": self._meta(self.empty_snapshot),
                },
            )
        except (StorageError, StorageValidationError, ValueError):
            return ApiResponse(
                503,
                {
                    "error": {"code": "UNAVAILABLE", "message": "M18 read model is unavailable", "details": {}},
                    "meta": self._meta(self.empty_snapshot),
                },
            )
        except Exception:
            return ApiResponse(
                500,
                {
                    "error": {"code": "INTERNAL_ERROR", "message": "M18 request failed", "details": {}},
                    "meta": self._meta(self.empty_snapshot),
                },
            )


__all__ = ["M18_API_VERSION", "M18ApiService", "create_empty_snapshot"]
