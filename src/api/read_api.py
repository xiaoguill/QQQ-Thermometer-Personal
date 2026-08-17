"""Versioned local read API over M08 repository read models.

The application is deliberately framework-free for the personal edition.  It
can be called directly in tests or mounted by :mod:`http_server`; it never
fetches market data and never recalculates strategy state in a route.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, urlsplit

from src.storage.sqlite_store import (
    SQLiteRepository,
    StorageConflictError,
    StorageError,
    StorageValidationError,
    StoredRecord,
)


API_CONTRACT_VERSION = "1.0.0"
API_IMPLEMENTATION_VERSION = "m10-read-api/v1"
API_EMPTY_DATE = "1970-01-01"
API_EMPTY_TIMESTAMP = "1970-01-01T00:00:00Z"
API_EMPTY_STRATEGY = "unavailable"
API_EMPTY_RUN = "read-model-unavailable"
_QUALITY_VALUES = {"ok", "stale", "partial", "failed", "needs_review"}
_THERMOMETER_STATES = {"warming", "normal", "shock", "recovery", "needs_review", "green", "yellow", "red", "fast_guard"}
_DECISIONS = {"confirm", "pause", "review"}
_ENDPOINTS = {
    ("GET", "/api/thermometer/latest"),
    ("GET", "/api/thermometer/history"),
    ("GET", "/api/signals/explain"),
    ("GET", "/api/triggers/next"),
    ("GET", "/api/portfolio/targets"),
    ("GET", "/api/portfolio/latest"),
    ("GET", "/api/portfolio/ledger"),
    ("GET", "/api/performance/curve"),
    ("GET", "/api/performance/metrics"),
    ("GET", "/api/data-quality/latest"),
    ("GET", "/api/versions"),
    ("POST", "/api/paper/confirm"),
}


class ApiError(Exception):
    """Safe, serializable API error."""

    def __init__(self, status_code: int, code: str, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = dict(details or {})


@dataclass(frozen=True)
class ApiResponse:
    status_code: int
    body: Mapping[str, Any]
    headers: Mapping[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.headers is None:
            object.__setattr__(self, "headers", {"Content-Type": "application/json; charset=utf-8"})

    def json_bytes(self) -> bytes:
        return json.dumps(
            self.body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")


class ApiAccessPolicy:
    """Local-only access policy with an optional in-memory token."""

    def __init__(
        self,
        *,
        allowed_hosts: Sequence[str] = ("127.0.0.1", "localhost", "::1"),
        access_token: str | None = None,
    ) -> None:
        hosts = tuple(str(value).strip().lower() for value in allowed_hosts if str(value).strip())
        if not hosts:
            raise ValueError("allowed_hosts must not be empty")
        if access_token is not None and not isinstance(access_token, str):
            raise ValueError("access_token must be a string or None")
        self.allowed_hosts = frozenset(hosts)
        self._access_token = access_token

    def authorize(self, client_host: str | None, headers: Mapping[str, Any] | None = None) -> None:
        host = "" if client_host is None else str(client_host).strip().lower()
        if host not in self.allowed_hosts:
            raise ApiError(403, "FORBIDDEN", "local API access is restricted")
        if self._access_token is not None:
            presented = ""
            for key, value in (headers or {}).items():
                if str(key).lower() == "x-qqq-local-token":
                    presented = str(value)
                    break
            if not hmac.compare_digest(presented, self._access_token):
                raise ApiError(403, "FORBIDDEN", "local API access is restricted")


class ApiReadRepository:
    """Repository adapter used by the API; route code never issues SQL."""

    _DATE_FIELD_BY_ENTITY = {
        "regime_snapshot": "signal_date",
        "indicator_snapshot": "signal_date",
        "target_weight_snapshot": "signal_date",
        "portfolio_snapshot": "execution_date",
        "ledger_event": "event_date",
        "quality_event": "event_date",
        "run": "execution_date",
        "strategy_version": "approved_at",
    }

    def __init__(self, repository: SQLiteRepository) -> None:
        if not isinstance(repository, SQLiteRepository):
            raise StorageValidationError("ApiReadRepository requires SQLiteRepository")
        if not repository.store.initialized:
            raise StorageValidationError("API repository requires an initialized store")
        self.repository = repository

    @staticmethod
    def _date_value(entity: str, record: StoredRecord) -> str:
        field = ApiReadRepository._DATE_FIELD_BY_ENTITY.get(entity, "as_of")
        value = record.payload.get(field)
        if value is None:
            value = record.metadata.get(field)
        if value is None and field != "as_of":
            value = record.payload.get("as_of") or record.metadata.get("as_of")
        return "" if value is None else str(value)

    def records(
        self,
        entity: str,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = 100,
    ) -> tuple[StoredRecord, ...]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ApiError(400, "INVALID_REQUEST", "limit must be between 1 and 500")
        all_records = self.repository.list(entity, limit=1000)
        filtered = []
        for record in all_records:
            value = self._date_value(entity, record)
            if from_date is not None and value[:10] < from_date:
                continue
            if to_date is not None and value[:10] > to_date:
                continue
            filtered.append(record)
        filtered.sort(key=lambda item: (self._date_value(entity, item), item.record_key))
        return tuple(filtered[-limit:])

    def latest(self, entity: str, *, as_of_date: str | None = None) -> StoredRecord | None:
        values = self.records(entity, to_date=as_of_date, limit=500)
        return values[-1] if values else None

    def by_signal_date(self, entity: str, signal_date: str) -> StoredRecord | None:
        values = self.records(entity, limit=500)
        matching = [item for item in values if self._date_value(entity, item)[:10] == signal_date]
        return matching[-1] if matching else None

    def latest_run_id(self) -> str:
        record = self.latest("run")
        if record is None:
            return API_EMPTY_RUN
        value = record.payload.get("run_id")
        return str(value) if value else record.record_key


def _safe_date(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ApiError(400, "INVALID_REQUEST", f"{field_name} must be an ISO date")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ApiError(400, "INVALID_REQUEST", f"{field_name} must be an ISO date") from exc


def _optional_date(value: Any, field_name: str) -> str | None:
    if value is None or value == "":
        return None
    return _safe_date(value, field_name)


def _quality(value: Any) -> str:
    if value is None:
        return "needs_review"
    normalized = str(value).strip().lower()
    return normalized if normalized in _QUALITY_VALUES else "needs_review"


def _finite_or_none(value: Any, *, minimum: float | None = None, maximum: float | None = None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    number = float(value)
    if minimum is not None and number < minimum:
        return None
    if maximum is not None and number > maximum:
        return None
    return number


class ReadApiService:
    """OpenAPI-shaped application service for local paper read models."""

    def __init__(self, repository: SQLiteRepository, *, access_policy: ApiAccessPolicy | None = None) -> None:
        self.read = ApiReadRepository(repository)
        self.repository = repository
        self.access_policy = access_policy or ApiAccessPolicy()

    def _metadata(
        self,
        records: Sequence[StoredRecord],
        *,
        quality_override: str | None = None,
    ) -> dict[str, Any]:
        ordered = list(records)
        payloads = [record.payload for record in ordered]

        def pick(*names: str) -> Any:
            for payload in reversed(payloads):
                for name in names:
                    value = payload.get(name)
                    if value not in (None, ""):
                        return value
            for record in reversed(ordered):
                for name in names:
                    value = record.metadata.get(name)
                    if value not in (None, ""):
                        return value
            return None

        signal_date = pick("signal_date") or API_EMPTY_DATE
        execution_date = pick("execution_date")
        as_of = pick("as_of") or API_EMPTY_TIMESTAMP
        strategy_version = pick("strategy_version") or API_EMPTY_STRATEGY
        run_id = pick("run_id") or self.read.latest_run_id()
        code_version = pick("code_version", "implementation_version", "regime_version") or API_IMPLEMENTATION_VERSION
        data_version = pick("data_version", "indicator_version", "normalization_version") or API_EMPTY_STRATEGY
        evidence_ref = pick("evidence_ref")
        quality_value = quality_override or pick("data_quality", "quality", "indicator_quality", "qqq_bar_quality")
        if not ordered and quality_override is None:
            quality_value = "failed"
        return {
            "contract_version": API_CONTRACT_VERSION,
            "strategy_version": str(strategy_version),
            "as_of": str(as_of),
            "signal_date": str(signal_date),
            "execution_date": None if execution_date in (None, "") else str(execution_date),
            "data_quality": _quality(quality_value),
            "run_id": str(run_id),
            "code_version": str(code_version),
            "data_version": str(data_version),
            "evidence_ref": None if evidence_ref in (None, "") else str(evidence_ref),
        }

    def _response(self, data: Any, records: Sequence[StoredRecord], *, quality: str | None = None, status: int = 200) -> ApiResponse:
        return ApiResponse(status, {"meta": self._metadata(records, quality_override=quality), "data": data})

    def _thermometer_data(self, regime: StoredRecord | None, target: StoredRecord | None) -> dict[str, Any]:
        payload = {} if regime is None else regime.payload
        target_payload = {} if target is None else target.payload
        state = str(payload.get("state") or "needs_review")
        if state not in _THERMOMETER_STATES:
            state = "needs_review"
        reasons = payload.get("reason_codes")
        if not isinstance(reasons, list) or not all(isinstance(item, str) and item for item in reasons):
            reasons = ["no_confirmed_regime"] if regime is None else []
        weights = target_payload.get("target_weights")
        if not isinstance(weights, Mapping):
            weights = {}
        normalized_weights = {}
        for symbol, value in weights.items():
            numeric = _finite_or_none(value, minimum=0.0)
            if numeric is not None:
                normalized_weights[str(symbol)] = numeric
        return {
            "state": state,
            "temperature": _finite_or_none(payload.get("temperature"), minimum=0.0, maximum=100.0),
            "trend": payload.get("trend") if isinstance(payload.get("trend"), str) else None,
            "signal_agreement": _finite_or_none(payload.get("signal_agreement"), minimum=0.0, maximum=1.0),
            "reason_codes": reasons,
            "target_weights": dict(sorted(normalized_weights.items())),
        }

    def thermometer_latest(self, *, as_of_date: str | None = None) -> ApiResponse:
        regime = self.read.latest("regime_snapshot", as_of_date=as_of_date)
        target = self.read.latest("target_weight_snapshot", as_of_date=as_of_date)
        records = tuple(item for item in (regime, target) if item is not None)
        quality = None if records else "failed"
        return self._response(self._thermometer_data(regime, target), records, quality=quality)

    def thermometer_history(self, *, from_date: str | None, to_date: str | None, limit: int) -> ApiResponse:
        regimes = self.read.records("regime_snapshot", from_date=from_date, to_date=to_date, limit=limit)
        data = []
        for regime in regimes:
            target = self.read.by_signal_date("target_weight_snapshot", self.read._date_value("regime_snapshot", regime)[:10])
            data.append(self._thermometer_data(regime, target))
        return self._response(data, regimes, quality=None if regimes else "failed")

    def explain_signals(self, *, as_of_date: str | None) -> ApiResponse:
        regime = self.read.latest("regime_snapshot", as_of_date=as_of_date)
        if regime is None:
            return self._response({"reason_codes": ["no_confirmed_regime"], "indicators": {}}, (), quality="failed")
        signal_date = self.read._date_value("regime_snapshot", regime)[:10]
        indicator = self.read.by_signal_date("indicator_snapshot", signal_date)
        indicators = {} if indicator is None else indicator.payload.get("values", indicator.payload)
        if not isinstance(indicators, Mapping):
            indicators = {}
        reasons = regime.payload.get("reason_codes", [])
        if not isinstance(reasons, list):
            reasons = []
        records = (regime,) if indicator is None else (regime, indicator)
        return self._response({"reason_codes": reasons, "indicators": copy.deepcopy(dict(indicators))}, records)

    def next_triggers(self) -> ApiResponse:
        regime = self.read.latest("regime_snapshot")
        if regime is None:
            return self._response([], (), quality="failed")
        triggers = regime.payload.get("next_triggers", [])
        if not isinstance(triggers, list):
            triggers = []
        return self._response(copy.deepcopy(triggers), (regime,))

    def target_snapshot(self, *, as_of_date: str | None) -> ApiResponse:
        target = self.read.latest("target_weight_snapshot", as_of_date=as_of_date)
        if target is None:
            return self._response({"target_weights": {}}, (), quality="failed")
        weights = target.payload.get("target_weights", {})
        if not isinstance(weights, Mapping):
            weights = {}
        normalized = {str(symbol): float(value) for symbol, value in weights.items() if _finite_or_none(value, minimum=0.0) is not None}
        return self._response({"target_weights": dict(sorted(normalized.items()))}, (target,))

    def portfolio_latest(self) -> ApiResponse:
        portfolio = self.read.latest("portfolio_snapshot")
        if portfolio is None:
            return self._response({}, (), quality="failed")
        return self._response(copy.deepcopy(dict(portfolio.payload)), (portfolio,))

    def paper_ledger(self, *, from_date: str | None, to_date: str | None, limit: int) -> ApiResponse:
        records = self.read.records("ledger_event", from_date=from_date, to_date=to_date, limit=limit)
        data = [copy.deepcopy(dict(record.payload)) for record in records]
        return self._response(data, records, quality=None if records else "failed")

    def performance_curve(self, *, as_of_date: str | None) -> ApiResponse:
        records = self.read.records("portfolio_snapshot", to_date=as_of_date, limit=500)
        data = [
            {
                "execution_date": record.payload.get("execution_date"),
                "nav": record.payload.get("nav"),
                "cash": record.payload.get("cash"),
                "data_quality": record.payload.get("data_quality", "OK"),
            }
            for record in records
        ]
        return self._response(data, records, quality=None if records else "failed")

    def performance_metrics(self, *, as_of_date: str | None) -> ApiResponse:
        records = self.read.records("run", to_date=as_of_date, limit=500)
        for record in reversed(records):
            metrics = record.payload.get("performance_metrics")
            if isinstance(metrics, Mapping):
                return self._response(copy.deepcopy(dict(metrics)), (record,))
        return self._response({}, records[-1:] if records else (), quality="needs_review")

    def data_quality_latest(self) -> ApiResponse:
        records = self.read.records("quality_event", limit=500)
        if records:
            latest = records[-1]
            status = _quality(latest.payload.get("status") or latest.metadata.get("status"))
            issues = []
            for record in records:
                message = record.payload.get("message") or record.payload.get("reason")
                if isinstance(message, str) and message:
                    issues.append(message)
            return self._response({"status": status, "issues": issues}, records[-1:])
        target = self.read.latest("target_weight_snapshot")
        if target is None:
            return self._response({"status": "failed", "issues": ["no quality record available"]}, (), quality="failed")
        status = _quality(target.payload.get("data_quality"))
        return self._response({"status": status, "issues": []}, (target,))

    def versions(self) -> ApiResponse:
        records = self.read.records("strategy_version", limit=500)
        if records:
            return self._response([copy.deepcopy(dict(record.payload)) for record in records], records)
        candidates = [self.read.latest("target_weight_snapshot"), self.read.latest("regime_snapshot")]
        candidates = [item for item in candidates if item is not None]
        data = [
            {
                "strategy_version": record.payload.get("strategy_version", API_EMPTY_STRATEGY),
                "implementation_version": record.payload.get("implementation_version", API_IMPLEMENTATION_VERSION),
                "status": record.payload.get("strategy_status", "research_candidate"),
            }
            for record in candidates
        ]
        return self._response(data, candidates, quality=None if candidates else "failed")

    def confirm_paper(self, body: Mapping[str, Any]) -> ApiResponse:
        if not isinstance(body, Mapping):
            raise ApiError(400, "INVALID_REQUEST", "request body must be an object")
        allowed = {"idempotency_key", "observation_date", "decision", "note"}
        unknown = sorted(set(body) - allowed)
        if unknown:
            raise ApiError(400, "INVALID_REQUEST", "request contains unsupported fields")
        key = body.get("idempotency_key")
        if not isinstance(key, str) or not key.strip() or "|" in key or len(key.strip()) > 200:
            raise ApiError(400, "INVALID_REQUEST", "idempotency_key is invalid")
        observation_date = _safe_date(body.get("observation_date"), "observation_date")
        decision = body.get("decision")
        if decision not in _DECISIONS:
            raise ApiError(400, "INVALID_REQUEST", "decision is invalid")
        note = body.get("note")
        if note is not None and (not isinstance(note, str) or len(note) > 2000):
            raise ApiError(400, "INVALID_REQUEST", "note is invalid")
        event_key = f"api-confirm|{key.strip()}"
        payload = {
            "schema": "qqq-paper-confirmation/v1",
            "implementation_version": API_IMPLEMENTATION_VERSION,
            "event_type": "PAPER_CONFIRMATION",
            "event_key": event_key,
            "idempotency_key": key.strip(),
            "observation_date": observation_date,
            "decision": decision,
            "note": note,
            "paper_only": True,
            "order_created": False,
        }
        existing = self.repository.get("ledger_event", event_key)
        if existing is not None and existing.payload != payload:
            raise StorageConflictError("paper confirmation idempotency key contains different content")
        record = self.repository.put_ledger_event(
            event_key,
            payload,
            event_date=observation_date,
            event_type="PAPER_CONFIRMATION",
            idempotency_key=key.strip(),
            status="RECORDED",
            cost=0.0,
        )
        data = {
            "event_key": event_key,
            "idempotency_key": key.strip(),
            "observation_date": observation_date,
            "decision": decision,
            "status": "RECORDED",
            "idempotent": existing is not None,
            "paper_only": True,
            "order_created": False,
        }
        return self._response(data, (record,), status=201)

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
            split = urlsplit(str(path))
            route = split.path or "/"
            query_map = self._query_map(split.query, query)
            if (method, route) not in _ENDPOINTS:
                if any(item[1] == route for item in _ENDPOINTS):
                    raise ApiError(405, "METHOD_NOT_ALLOWED", "method is not allowed")
                raise ApiError(404, "NOT_FOUND", "endpoint was not found")
            if method == "POST":
                parsed_body = self._json_body(body)
                return self.confirm_paper(parsed_body)
            if route == "/api/thermometer/latest":
                self._check_query(query_map, set())
                return self.thermometer_latest(as_of_date=None)
            if route == "/api/thermometer/history":
                values = self._range_query(query_map)
                return self.thermometer_history(**values)
            if route == "/api/signals/explain":
                return self._as_of_endpoint(route, query_map, self.explain_signals)
            if route == "/api/triggers/next":
                self._check_query(query_map, set())
                return self.next_triggers()
            if route == "/api/portfolio/targets":
                return self._as_of_endpoint(route, query_map, self.target_snapshot)
            if route == "/api/portfolio/latest":
                self._check_query(query_map, set())
                return self.portfolio_latest()
            if route == "/api/portfolio/ledger":
                values = self._range_query(query_map)
                return self.paper_ledger(**values)
            if route == "/api/performance/curve":
                return self._as_of_endpoint(route, query_map, self.performance_curve)
            if route == "/api/performance/metrics":
                return self._as_of_endpoint(route, query_map, self.performance_metrics)
            if route == "/api/data-quality/latest":
                self._check_query(query_map, set())
                return self.data_quality_latest()
            if route == "/api/versions":
                self._check_query(query_map, set())
                return self.versions()
            raise ApiError(404, "NOT_FOUND", "endpoint was not found")
        except ApiError as exc:
            return self._error_response(exc)
        except StorageConflictError:
            return self._error_response(ApiError(409, "CONFLICT", "request conflicts with an existing immutable record"))
        except (StorageValidationError, ValueError) as exc:
            return self._error_response(ApiError(400, "INVALID_REQUEST", "request could not be validated"))
        except StorageError:
            return self._error_response(ApiError(503, "UNAVAILABLE", "read model is unavailable"))
        except Exception:
            return self._error_response(ApiError(500, "INTERNAL_ERROR", "request failed"))

    @staticmethod
    def _query_map(raw_query: str, provided: Mapping[str, Any] | None) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {key: list(values) for key, values in parse_qs(raw_query, keep_blank_values=True).items()}
        if provided is not None:
            for key, value in provided.items():
                if isinstance(value, (list, tuple)):
                    result[str(key)] = [str(item) for item in value]
                else:
                    result[str(key)] = [str(value)]
        return result

    @staticmethod
    def _check_query(query: Mapping[str, list[str]], allowed: set[str]) -> None:
        unknown = sorted(set(query) - allowed)
        if unknown:
            raise ApiError(400, "INVALID_REQUEST", "unsupported query parameter")

    @classmethod
    def _one(cls, query: Mapping[str, list[str]], key: str) -> str | None:
        values = query.get(key, [])
        if len(values) > 1:
            raise ApiError(400, "INVALID_REQUEST", f"query parameter {key} must appear once")
        return values[0] if values else None

    @classmethod
    def _range_query(cls, query: Mapping[str, list[str]]) -> dict[str, Any]:
        cls._check_query(query, {"from", "to", "limit"})
        from_date = _optional_date(cls._one(query, "from"), "from")
        to_date = _optional_date(cls._one(query, "to"), "to")
        if from_date is not None and to_date is not None and from_date > to_date:
            raise ApiError(400, "INVALID_REQUEST", "from must not be after to")
        raw_limit = cls._one(query, "limit")
        limit = 100 if raw_limit in (None, "") else int(raw_limit)
        if not 1 <= limit <= 500:
            raise ApiError(400, "INVALID_REQUEST", "limit must be between 1 and 500")
        return {"from_date": from_date, "to_date": to_date, "limit": limit}

    @classmethod
    def _as_of_endpoint(cls, route: str, query: Mapping[str, list[str]], handler):
        cls._check_query(query, {"as_of"})
        as_of_date = _optional_date(cls._one(query, "as_of"), "as_of")
        return handler(as_of_date=as_of_date)

    @staticmethod
    def _json_body(body: Any) -> Mapping[str, Any]:
        if isinstance(body, Mapping):
            return body
        if isinstance(body, bytes):
            try:
                body = body.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ApiError(400, "INVALID_REQUEST", "request body must be UTF-8 JSON") from exc
        if not isinstance(body, str) or len(body.encode("utf-8")) > 65_536:
            raise ApiError(400, "INVALID_REQUEST", "request body is invalid")
        try:
            value = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ApiError(400, "INVALID_REQUEST", "request body must be valid JSON") from exc
        if not isinstance(value, Mapping):
            raise ApiError(400, "INVALID_REQUEST", "request body must be an object")
        return value

    def _error_response(self, error: ApiError) -> ApiResponse:
        body = {
            "error": {
                "code": error.code,
                "message": error.message,
                "details": copy.deepcopy(error.details),
            },
            "meta": self._metadata((), quality_override="failed"),
        }
        return ApiResponse(error.status_code, body)


def create_app(repository: SQLiteRepository, *, access_policy: ApiAccessPolicy | None = None) -> ReadApiService:
    """Create an API application without opening sockets or starting workers."""

    return ReadApiService(repository, access_policy=access_policy)


__all__ = [
    "API_CONTRACT_VERSION",
    "API_IMPLEMENTATION_VERSION",
    "ApiAccessPolicy",
    "ApiError",
    "ApiReadRepository",
    "ApiResponse",
    "ReadApiService",
    "create_app",
]
