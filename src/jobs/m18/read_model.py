"""M18 full-chain read-model contracts.

This module is an integration boundary, not a second strategy implementation.
It records provenance and fail-closed publication states for M00.5 through M17,
keeps M16 observations provisional, and permits confirmed targets only when a
formal M07/M10 read model is healthy.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence


M18_READ_MODEL_SCHEMA = "qqq-m18-full-chain-workbench/v1"
M18_READ_MODEL_VERSION = "m18-full-chain/v1"
CONFIRMED = "CONFIRMED"
PROVISIONAL = "PROVISIONAL"
DISPLAY_ONLY = "DISPLAY_ONLY"
NONE_PUBLICATION = "NONE"

QUALITY_VALUES = (
    "OK",
    "PARTIAL",
    "STALE",
    "FAILED",
    "NEEDS_REVIEW",
    "NOT_RUN",
    "UNAVAILABLE",
)
MODULE_STATUS_VALUES = (
    "NOT_RUN",
    "READY",
    "DEGRADED",
    "PARTIAL",
    "STALE",
    "FAILED",
    "NEEDS_REVIEW",
)
PAPER_PLAN_STATUS_VALUES = ("NOT_GENERATED", "READY", "NEEDS_REVIEW", "FAILED")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MODULE_RE = re.compile(r"^M(?:00\.5|0[1-9]|1[0-8])$")

MODULE_IDS = (
    "M00.5",
    "M01",
    "M02",
    "M03",
    "M04",
    "M05",
    "M06",
    "M07",
    "M08",
    "M09",
    "M10",
    "M11",
    "M12",
    "M13",
    "M14",
    "M15",
    "M16",
    "M17",
)

_MODULE_CATALOGUE: dict[str, tuple[str, str, str]] = {
    "M00.5": ("治理与任务边界", "上下文路由、权限、验收和回滚边界", NONE_PUBLICATION),
    "M01": ("版本与策略契约", "冻结策略版本、执行时点和参数真源", NONE_PUBLICATION),
    "M02": ("原始市场数据", "保存不可变原始快照及来源元数据", NONE_PUBLICATION),
    "M03": ("标准化与数据质量", "交易日对齐、缺失检测和质量事件", NONE_PUBLICATION),
    "M04": ("指标快照", "从标准化数据计算声明的指标快照", NONE_PUBLICATION),
    "M05": ("复合趋势状态机", "根据正式指标输出状态和证据", NONE_PUBLICATION),
    "M06": ("信号解释", "为状态提供可审计的解释和信号一致性", NONE_PUBLICATION),
    "M07": ("目标仓位", "由正式状态生成候选目标仓位", CONFIRMED),
    "M08": ("SQLite 读模型", "持久化版本化、不可变和可重放的记录", NONE_PUBLICATION),
    "M09": ("纸上组合与账本", "仅模拟执行、持仓和调仓差异", NONE_PUBLICATION),
    "M10": ("只读 API", "向页面提供确认策略和审计数据", CONFIRMED),
    "M11": ("运行编排", "幂等地串联刷新、计算、模拟和发布", NONE_PUBLICATION),
    "M12": ("前端壳层", "提供本地页面框架和导航", DISPLAY_ONLY),
    "M13": ("解释与目标页面", "展示后端返回的解释和目标来源", DISPLAY_ONLY),
    "M14": ("回放与绩效审计", "展示历史回放、回撤和审计产物", DISPLAY_ONLY),
    "M15": ("个人私有发布", "保存、回溯和个人使用边界", DISPLAY_ONLY),
    "M16": ("Massive 实时观察", "读取盘中数据并生成不改变目标的临时观察", PROVISIONAL),
    "M17": ("统一旧版入口", "保留既有 M17 页面和纸上计划展示", DISPLAY_ONLY),
}


class M18ReadModelError(ValueError):
    """Raised when a full-chain payload violates the M18 contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise M18ReadModelError(message)


def _text(value: Any, field_name: str, *, maximum: int = 512) -> str:
    _require(isinstance(value, str) and value.strip(), f"{field_name} must be a non-empty string")
    result = value.strip()
    _require(len(result) <= maximum, f"{field_name} is too long")
    return result


def _optional_text(value: Any, field_name: str, *, maximum: int = 512) -> str | None:
    if value is None:
        return None
    return _text(value, field_name, maximum=maximum)


def _iso_timestamp(value: Any, field_name: str, *, optional: bool = True) -> str | None:
    if value is None and optional:
        return None
    candidate = _text(value, field_name, maximum=64).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise M18ReadModelError(f"{field_name} must be an ISO timestamp") from exc
    _require(parsed.tzinfo is not None, f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_date(value: Any, field_name: str, *, optional: bool = True) -> str | None:
    if value is None and optional:
        return None
    candidate = _text(value, field_name, maximum=32)
    _require(bool(_DATE_RE.fullmatch(candidate)), f"{field_name} must be an ISO date")
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError as exc:
        raise M18ReadModelError(f"{field_name} must be an ISO date") from exc


def _finite(value: Any, field_name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    _require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{field_name} must be numeric")
    result = float(value)
    _require(math.isfinite(result), f"{field_name} must be finite")
    if minimum is not None:
        _require(result >= minimum, f"{field_name} must be at least {minimum}")
    if maximum is not None:
        _require(result <= maximum, f"{field_name} must be at most {maximum}")
    return result


def _unique_strings(values: Sequence[Any], field_name: str) -> tuple[str, ...]:
    _require(isinstance(values, Sequence) and not isinstance(values, (str, bytes)), f"{field_name} must be a sequence")
    result = tuple(_text(item, field_name, maximum=256) for item in values)
    _require(len(result) == len(set(result)), f"{field_name} cannot contain duplicates")
    return result


def _json_copy(value: Any, field_name: str) -> Any:
    try:
        result = copy.deepcopy(value)
        json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise M18ReadModelError(f"{field_name} must be finite JSON data") from exc
    return result


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _weights(value: Mapping[str, Any], field_name: str, *, allow_empty: bool = True) -> dict[str, float]:
    _require(isinstance(value, Mapping), f"{field_name} must be an object")
    result: dict[str, float] = {}
    for symbol, weight in value.items():
        key = _text(symbol, f"{field_name}.symbol", maximum=32)
        result[key] = _finite(weight, f"{field_name}.{key}", minimum=0.0, maximum=1.0)
    _require(allow_empty or bool(result), f"{field_name} cannot be empty")
    if result:
        _require(abs(sum(result.values()) - 1.0) <= 1e-6, f"{field_name} must sum to 1.0")
    return dict(sorted(result.items()))


def _amounts(value: Mapping[str, Any], field_name: str) -> dict[str, float]:
    """Validate non-negative quantities without treating them as percentages."""

    _require(isinstance(value, Mapping), f"{field_name} must be an object")
    result: dict[str, float] = {}
    for symbol, amount in value.items():
        key = _text(symbol, f"{field_name}.symbol", maximum=32)
        result[key] = _finite(amount, f"{field_name}.{key}", minimum=0.0)
    return dict(sorted(result.items()))


@dataclass(frozen=True)
class ModuleStatus:
    """One module's provenance and publication state."""

    module_id: str
    name: str
    responsibility: str
    status: str
    publication: str = NONE_PUBLICATION
    version: str | None = None
    run_id: str | None = None
    as_of: str | None = None
    signal_date: str | None = None
    execution_date: str | None = None
    quality: str = "NOT_RUN"
    artifact_ref: str | None = None
    reason_codes: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(bool(_MODULE_RE.fullmatch(self.module_id)), "module_id is invalid")
        _require(self.module_id in MODULE_IDS, "module_id is not registered")
        _text(self.name, "name")
        _text(self.responsibility, "responsibility", maximum=1024)
        _require(self.status in MODULE_STATUS_VALUES, "unsupported module status")
        _require(self.publication in {NONE_PUBLICATION, PROVISIONAL, CONFIRMED, DISPLAY_ONLY}, "unsupported publication")
        _require(self.quality in QUALITY_VALUES, "unsupported module quality")
        _optional_text(self.version, "version")
        _optional_text(self.run_id, "run_id")
        _iso_timestamp(self.as_of, "as_of")
        signal = _iso_date(self.signal_date, "signal_date")
        execution = _iso_date(self.execution_date, "execution_date")
        if signal is not None and execution is not None:
            _require(execution > signal, "execution_date must be after signal_date")
        _optional_text(self.artifact_ref, "artifact_ref", maximum=1024)
        reasons = _unique_strings(self.reason_codes, "reason_codes")
        dependencies = _unique_strings(self.depends_on, "depends_on")
        _require(all(item in MODULE_IDS for item in dependencies), "depends_on contains an unknown module")
        if self.status == "NOT_RUN":
            _require(self.quality == "NOT_RUN", "NOT_RUN module must have NOT_RUN quality")
        if self.status == "READY":
            _require(self.quality == "OK", "READY module must have OK quality")
        if self.publication == CONFIRMED:
            _require(self.status == "READY" and self.quality == "OK", "CONFIRMED module must be ready with OK quality")
        if self.publication == PROVISIONAL:
            _require(self.module_id == "M16", "only M16 may publish PROVISIONAL observations")
        object.__setattr__(self, "signal_date", signal)
        object.__setattr__(self, "execution_date", execution)
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "depends_on", dependencies)

    def as_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "name": self.name,
            "responsibility": self.responsibility,
            "status": self.status,
            "publication": self.publication,
            "version": self.version,
            "run_id": self.run_id,
            "as_of": self.as_of,
            "signal_date": self.signal_date,
            "execution_date": self.execution_date,
            "quality": self.quality,
            "artifact_ref": self.artifact_ref,
            "reason_codes": list(self.reason_codes),
            "depends_on": list(self.depends_on),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ModuleStatus":
        _require(isinstance(value, Mapping), "module status must be an object")
        return cls(
            module_id=value.get("module_id"),
            name=value.get("name"),
            responsibility=value.get("responsibility"),
            status=value.get("status"),
            publication=value.get("publication", NONE_PUBLICATION),
            version=value.get("version"),
            run_id=value.get("run_id"),
            as_of=value.get("as_of"),
            signal_date=value.get("signal_date"),
            execution_date=value.get("execution_date"),
            quality=value.get("quality", "NOT_RUN"),
            artifact_ref=value.get("artifact_ref"),
            reason_codes=tuple(value.get("reason_codes", ())),
            depends_on=tuple(value.get("depends_on", ())),
        )


def default_module_statuses() -> tuple[ModuleStatus, ...]:
    """Return a complete, explicitly not-run M00.5–M17 module inventory."""

    return tuple(
        ModuleStatus(
            module_id=module_id,
            name=_MODULE_CATALOGUE[module_id][0],
            responsibility=_MODULE_CATALOGUE[module_id][1],
            status="NOT_RUN",
            publication=NONE_PUBLICATION,
            quality="NOT_RUN",
        )
        for module_id in MODULE_IDS
    )


@dataclass(frozen=True)
class ProvisionalObservation:
    """M16 observation; it can never carry target weights."""

    status: str
    quality: str
    as_of: str | None
    signal_date: str | None
    temperature: float | None
    state: str | None
    source_version: str | None
    run_id: str | None
    reason_codes: tuple[str, ...] = ()
    source_symbols: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(self.status in MODULE_STATUS_VALUES, "unsupported provisional status")
        _require(self.quality in QUALITY_VALUES, "unsupported provisional quality")
        _iso_timestamp(self.as_of, "provisional.as_of")
        _iso_date(self.signal_date, "provisional.signal_date")
        _optional_text(self.source_version, "provisional.source_version")
        _optional_text(self.run_id, "provisional.run_id")
        if self.temperature is not None:
            _finite(self.temperature, "provisional.temperature", minimum=0.0, maximum=100.0)
            _require(self.status == "READY" and self.quality == "OK", "a provisional temperature requires READY/OK data")
            _require(self.state is not None and str(self.state).strip(), "a provisional temperature requires state")
        if self.status != "READY" or self.quality != "OK":
            _require(self.temperature is None, "degraded provisional data cannot publish a temperature")
        _optional_text(self.state, "provisional.state", maximum=128)
        object.__setattr__(self, "reason_codes", _unique_strings(self.reason_codes, "provisional.reason_codes"))
        object.__setattr__(self, "source_symbols", _unique_strings(self.source_symbols, "provisional.source_symbols"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "publication": PROVISIONAL,
            "status": self.status,
            "quality": self.quality,
            "as_of": self.as_of,
            "signal_date": self.signal_date,
            "temperature": self.temperature,
            "state": self.state,
            "source_version": self.source_version,
            "run_id": self.run_id,
            "reason_codes": list(self.reason_codes),
            "source_symbols": list(self.source_symbols),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProvisionalObservation":
        _require(isinstance(value, Mapping), "provisional observation must be an object")
        return cls(
            status=value.get("status"),
            quality=value.get("quality"),
            as_of=value.get("as_of"),
            signal_date=value.get("signal_date"),
            temperature=value.get("temperature"),
            state=value.get("state"),
            source_version=value.get("source_version"),
            run_id=value.get("run_id"),
            reason_codes=tuple(value.get("reason_codes", ())),
            source_symbols=tuple(value.get("source_symbols", ())),
        )


@dataclass(frozen=True)
class ConfirmedStrategy:
    """Formal strategy read model; only this object may publish targets."""

    status: str
    quality: str
    as_of: str | None
    signal_date: str | None
    execution_date: str | None
    temperature: float | None
    state: str | None
    strategy_version: str | None
    run_id: str | None
    target_weights: Mapping[str, float]
    reason_codes: tuple[str, ...] = ()
    evidence_ref: str | None = None

    def __post_init__(self) -> None:
        _require(self.status in {"READY", "UNAVAILABLE", "NEEDS_REVIEW", "FAILED"}, "unsupported confirmed status")
        _require(self.quality in QUALITY_VALUES, "unsupported confirmed quality")
        _iso_timestamp(self.as_of, "confirmed.as_of")
        signal = _iso_date(self.signal_date, "confirmed.signal_date")
        execution = _iso_date(self.execution_date, "confirmed.execution_date")
        if signal is not None and execution is not None:
            _require(execution > signal, "confirmed.execution_date must be after signal_date")
        _optional_text(self.strategy_version, "confirmed.strategy_version")
        _optional_text(self.run_id, "confirmed.run_id")
        _optional_text(self.evidence_ref, "confirmed.evidence_ref", maximum=1024)
        if self.temperature is not None:
            _finite(self.temperature, "confirmed.temperature", minimum=0.0, maximum=100.0)
        if self.status == "READY":
            _require(self.quality == "OK", "READY confirmed strategy must have OK quality")
            _require(self.temperature is not None and self.state is not None, "READY confirmed strategy must have temperature and state")
            _require(signal is not None and execution is not None, "READY confirmed strategy must have signal and execution dates")
            _require(bool(self.strategy_version) and bool(self.run_id), "READY confirmed strategy must have provenance")
            weights = _weights(self.target_weights, "confirmed.target_weights", allow_empty=False)
        else:
            _require(not self.target_weights, "unavailable confirmed strategy cannot publish target weights")
            weights = {}
        object.__setattr__(self, "signal_date", signal)
        object.__setattr__(self, "execution_date", execution)
        object.__setattr__(self, "target_weights", weights)
        object.__setattr__(self, "reason_codes", _unique_strings(self.reason_codes, "confirmed.reason_codes"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "publication": CONFIRMED,
            "status": self.status,
            "quality": self.quality,
            "as_of": self.as_of,
            "signal_date": self.signal_date,
            "execution_date": self.execution_date,
            "temperature": self.temperature,
            "state": self.state,
            "strategy_version": self.strategy_version,
            "run_id": self.run_id,
            "target_weights": dict(sorted(self.target_weights.items())),
            "reason_codes": list(self.reason_codes),
            "evidence_ref": self.evidence_ref,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConfirmedStrategy":
        _require(isinstance(value, Mapping), "confirmed strategy must be an object")
        return cls(
            status=value.get("status"),
            quality=value.get("quality"),
            as_of=value.get("as_of"),
            signal_date=value.get("signal_date"),
            execution_date=value.get("execution_date"),
            temperature=value.get("temperature"),
            state=value.get("state"),
            strategy_version=value.get("strategy_version"),
            run_id=value.get("run_id"),
            target_weights=value.get("target_weights", {}),
            reason_codes=tuple(value.get("reason_codes", ())),
            evidence_ref=value.get("evidence_ref"),
        )


@dataclass(frozen=True)
class PaperPlanView:
    """Paper-only preview; it never represents a broker order."""

    status: str = "NOT_GENERATED"
    signal_date: str | None = None
    execution_date: str | None = None
    target_weights: Mapping[str, float] = None  # type: ignore[assignment]
    current_positions: Mapping[str, float] = None  # type: ignore[assignment]
    order_created: bool = False
    broker_connected: bool = False
    plan_ref: str | None = None
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(self.status in PAPER_PLAN_STATUS_VALUES, "unsupported paper plan status")
        signal = _iso_date(self.signal_date, "paper.signal_date")
        execution = _iso_date(self.execution_date, "paper.execution_date")
        if signal is not None and execution is not None:
            _require(execution > signal, "paper.execution_date must be after signal_date")
        _require(self.order_created is False, "M18 paper plan cannot create an order")
        _require(self.broker_connected is False, "M18 paper plan cannot connect to a broker")
        targets = _weights(self.target_weights or {}, "paper.target_weights")
        positions = _amounts(self.current_positions or {}, "paper.current_positions")
        _optional_text(self.plan_ref, "paper.plan_ref", maximum=1024)
        object.__setattr__(self, "signal_date", signal)
        object.__setattr__(self, "execution_date", execution)
        object.__setattr__(self, "target_weights", targets)
        object.__setattr__(self, "current_positions", positions)
        object.__setattr__(self, "reason_codes", _unique_strings(self.reason_codes, "paper.reason_codes"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "signal_date": self.signal_date,
            "execution_date": self.execution_date,
            "target_weights": dict(sorted(self.target_weights.items())),
            "current_positions": dict(sorted(self.current_positions.items())),
            "order_created": False,
            "broker_connected": False,
            "plan_ref": self.plan_ref,
            "reason_codes": list(self.reason_codes),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PaperPlanView":
        _require(isinstance(value, Mapping), "paper plan must be an object")
        return cls(
            status=value.get("status", "NOT_GENERATED"),
            signal_date=value.get("signal_date"),
            execution_date=value.get("execution_date"),
            target_weights=value.get("target_weights", {}),
            current_positions=value.get("current_positions", {}),
            order_created=value.get("order_created", False),
            broker_connected=value.get("broker_connected", False),
            plan_ref=value.get("plan_ref"),
            reason_codes=tuple(value.get("reason_codes", ())),
        )


@dataclass(frozen=True)
class RuntimeBoundary:
    """Visible local-runtime limits and source freshness."""

    source: str
    source_configured: bool
    refresh_interval_seconds: int
    display_timezone: str
    source_status: str
    last_refresh_at: str | None
    loopback_only: bool = True
    paper_only: bool = True
    execution_allowed: bool = False
    broker_connected: bool = False
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.source, "runtime.source")
        _require(isinstance(self.source_configured, bool), "runtime.source_configured must be boolean")
        _require(isinstance(self.refresh_interval_seconds, int) and not isinstance(self.refresh_interval_seconds, bool), "runtime.refresh_interval_seconds must be an integer")
        _require(self.refresh_interval_seconds >= 60, "runtime.refresh_interval_seconds must be at least 60 seconds")
        _text(self.display_timezone, "runtime.display_timezone", maximum=128)
        _require(self.source_status in {"READY", "DEGRADED", "FAILED", "NOT_CONFIGURED", "UNAVAILABLE"}, "unsupported runtime source status")
        _iso_timestamp(self.last_refresh_at, "runtime.last_refresh_at")
        _require(self.loopback_only is True, "M18 runtime must remain loopback-only")
        _require(self.paper_only is True, "M18 runtime must remain paper-only")
        _require(self.execution_allowed is False, "M18 runtime cannot enable execution")
        _require(self.broker_connected is False, "M18 runtime cannot connect to a broker")
        object.__setattr__(self, "reason_codes", _unique_strings(self.reason_codes, "runtime.reason_codes"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_configured": self.source_configured,
            "refresh_interval_seconds": self.refresh_interval_seconds,
            "display_timezone": self.display_timezone,
            "source_status": self.source_status,
            "last_refresh_at": self.last_refresh_at,
            "loopback_only": True,
            "paper_only": True,
            "execution_allowed": False,
            "broker_connected": False,
            "reason_codes": list(self.reason_codes),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RuntimeBoundary":
        _require(isinstance(value, Mapping), "runtime boundary must be an object")
        return cls(
            source=value.get("source"),
            source_configured=value.get("source_configured", False),
            refresh_interval_seconds=value.get("refresh_interval_seconds"),
            display_timezone=value.get("display_timezone"),
            source_status=value.get("source_status"),
            last_refresh_at=value.get("last_refresh_at"),
            loopback_only=value.get("loopback_only", True),
            paper_only=value.get("paper_only", True),
            execution_allowed=value.get("execution_allowed", False),
            broker_connected=value.get("broker_connected", False),
            reason_codes=tuple(value.get("reason_codes", ())),
        )


@dataclass(frozen=True)
class FullChainSnapshot:
    """One immutable M18 publication assembled from all module outputs."""

    run_id: str
    run_status: str
    created_at: str
    as_of: str | None
    signal_date: str | None
    execution_date: str | None
    strategy_version: str | None
    data_version: str
    overall_quality: str
    modules: tuple[ModuleStatus, ...]
    provisional_observation: ProvisionalObservation
    confirmed_strategy: ConfirmedStrategy
    paper_plan: PaperPlanView
    runtime_boundary: RuntimeBoundary
    latest_data_quality: tuple[Mapping[str, Any], ...] = ()
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.run_id, "run_id")
        _require(self.run_status in {"scheduled", "running", "published", "partial", "stale", "failed", "needs_review"}, "unsupported run_status")
        created = _iso_timestamp(self.created_at, "created_at", optional=False)
        as_of = _iso_timestamp(self.as_of, "as_of")
        signal = _iso_date(self.signal_date, "signal_date")
        execution = _iso_date(self.execution_date, "execution_date")
        if signal is not None and execution is not None:
            _require(execution > signal, "execution_date must be after signal_date")
        _optional_text(self.strategy_version, "strategy_version")
        _text(self.data_version, "data_version")
        _require(self.overall_quality in QUALITY_VALUES, "unsupported overall_quality")
        _require(all(isinstance(item, ModuleStatus) for item in self.modules), "modules must contain ModuleStatus")
        _require(tuple(item.module_id for item in self.modules) == MODULE_IDS, "module inventory must contain M00.5 through M17 in order")
        _require(isinstance(self.provisional_observation, ProvisionalObservation), "invalid provisional observation")
        _require(isinstance(self.confirmed_strategy, ConfirmedStrategy), "invalid confirmed strategy")
        _require(isinstance(self.paper_plan, PaperPlanView), "invalid paper plan")
        _require(isinstance(self.runtime_boundary, RuntimeBoundary), "invalid runtime boundary")
        _require(isinstance(self.latest_data_quality, tuple), "latest_data_quality must be a tuple")
        for item in self.latest_data_quality:
            _json_copy(item, "latest_data_quality item")
        reasons = _unique_strings(self.reason_codes, "reason_codes")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "signal_date", signal)
        object.__setattr__(self, "execution_date", execution)
        object.__setattr__(self, "reason_codes", reasons)

    @property
    def target_weights(self) -> dict[str, float]:
        """Confirmed targets only; provisional observations have no target field."""

        return dict(self.confirmed_strategy.target_weights)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "schema": M18_READ_MODEL_SCHEMA,
            "implementation_version": M18_READ_MODEL_VERSION,
            "run_id": self.run_id,
            "run_status": self.run_status,
            "created_at": self.created_at,
            "as_of": self.as_of,
            "signal_date": self.signal_date,
            "execution_date": self.execution_date,
            "strategy_version": self.strategy_version,
            "data_version": self.data_version,
            "overall_quality": self.overall_quality,
            "modules": [item.as_dict() for item in self.modules],
            "provisional_observation": self.provisional_observation.as_dict(),
            "confirmed_strategy": self.confirmed_strategy.as_dict(),
            "target_weights": self.target_weights,
            "paper_plan": self.paper_plan.as_dict(),
            "runtime_boundary": self.runtime_boundary.as_dict(),
            "latest_data_quality": [_json_copy(item, "latest_data_quality item") for item in self.latest_data_quality],
            "reason_codes": list(self.reason_codes),
        }
        _json_copy(payload, "full_chain_snapshot")
        return payload

    @property
    def content_hash(self) -> str:
        return _sha256(self.as_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FullChainSnapshot":
        _require(isinstance(value, Mapping), "full-chain snapshot must be an object")
        _require(value.get("schema") == M18_READ_MODEL_SCHEMA, "unsupported M18 read-model schema")
        _require(value.get("implementation_version") == M18_READ_MODEL_VERSION, "unsupported M18 read-model version")
        modules_value = value.get("modules")
        _require(isinstance(modules_value, Sequence) and not isinstance(modules_value, (str, bytes)), "modules must be a list")
        return cls(
            run_id=value.get("run_id"),
            run_status=value.get("run_status"),
            created_at=value.get("created_at"),
            as_of=value.get("as_of"),
            signal_date=value.get("signal_date"),
            execution_date=value.get("execution_date"),
            strategy_version=value.get("strategy_version"),
            data_version=value.get("data_version"),
            overall_quality=value.get("overall_quality"),
            modules=tuple(ModuleStatus.from_dict(item) for item in modules_value),
            provisional_observation=ProvisionalObservation.from_dict(value.get("provisional_observation")),
            confirmed_strategy=ConfirmedStrategy.from_dict(value.get("confirmed_strategy")),
            paper_plan=PaperPlanView.from_dict(value.get("paper_plan")),
            runtime_boundary=RuntimeBoundary.from_dict(value.get("runtime_boundary")),
            latest_data_quality=tuple(value.get("latest_data_quality", ())),
            reason_codes=tuple(value.get("reason_codes", ())),
        )


__all__ = [
    "CONFIRMED",
    "DISPLAY_ONLY",
    "M18_READ_MODEL_SCHEMA",
    "M18_READ_MODEL_VERSION",
    "MODULE_IDS",
    "M18ReadModelError",
    "MODULE_STATUS_VALUES",
    "NONE_PUBLICATION",
    "PROVISIONAL",
    "ConfirmedStrategy",
    "FullChainSnapshot",
    "ModuleStatus",
    "PaperPlanView",
    "ProvisionalObservation",
    "RuntimeBoundary",
    "default_module_statuses",
]
