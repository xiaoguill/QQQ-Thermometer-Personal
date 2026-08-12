"""Machine-readable strategy and verification contracts.

This module deliberately contains no market-data access, persistence, UI, or
order execution.  It validates the boundary objects that later independent
verification code will consume.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT_PATH = PROJECT_ROOT / "configs" / "frozen" / "strategy_contract.json"

_ALLOWED_STATUSES = {
    "research_candidate",
    "legacy_reference",
    "approved_product",
}
_REQUIRED_ASSET_FIELDS = {"asset_type", "roles", "tradable", "notes"}
_REQUIRED_VERSION_FIELDS = {
    "version",
    "display_name",
    "status",
    "implementation_state",
    "strategy_family",
    "states",
    "strategy_assets",
    "benchmark_assets",
    "weight_schema",
    "timing",
    "parameters",
    "notes",
}


class ContractError(ValueError):
    """Raised when a contract is malformed or a candidate violates it."""


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _as_finite_number(value: Any, field_name: str) -> float:
    _require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{field_name} must be numeric")
    result = float(value)
    _require(math.isfinite(result), f"{field_name} must be finite")
    return result


def _as_date(value: Any, field_name: str) -> date:
    _require(isinstance(value, str), f"{field_name} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"{field_name} must be YYYY-MM-DD") from exc


def _validate_registry(raw: Mapping[str, Any]) -> None:
    required = {
        "$schema",
        "contract_id",
        "contract_schema_version",
        "product",
        "verification_contract",
        "assets",
        "strategy_versions",
    }
    _require(isinstance(raw, Mapping), "strategy contract root must be an object")
    _require(required.issubset(raw), f"strategy contract missing fields: {sorted(required - set(raw))}")
    _require(raw["$schema"] == "qqq-thermometer-strategy-contract/v1", "unsupported contract schema")
    _require(isinstance(raw["contract_id"], str) and raw["contract_id"], "contract_id must be non-empty")
    _require(isinstance(raw["contract_schema_version"], str), "contract_schema_version must be a string")

    product = raw["product"]
    _require(isinstance(product, Mapping), "product must be an object")
    _require(product.get("mode") == "local_paper_only", "product mode must remain local_paper_only")
    _require(product.get("default_version_status") == "pending_user_approval", "default version approval status is invalid")
    default_version = product.get("product_default_strategy_version")
    _require(default_version is None or isinstance(default_version, str), "product default version must be null or a string")

    verification = raw["verification_contract"]
    _require(isinstance(verification, Mapping), "verification_contract must be an object")
    for field_name in ("portfolio_weight_tolerance", "return_tolerance", "price_tolerance", "default_cost_bps"):
        _require(_as_finite_number(verification.get(field_name), field_name) >= 0, f"{field_name} must be non-negative")
    stress_costs = verification.get("stress_cost_bps")
    _require(isinstance(stress_costs, list) and stress_costs, "stress_cost_bps must be a non-empty list")
    _require(all(_as_finite_number(value, "stress_cost_bps") >= 0 for value in stress_costs), "stress costs must be non-negative")
    _require(verification.get("signal_cutoff") == "close", "signal cutoff must be close")
    _require(verification.get("signal_delay_trading_days") == 1, "signal delay must remain one trading day")
    _require(verification.get("execution_must_be_next_trading_day") is True, "execution must be next trading day")
    _require(verification.get("allow_short") is False, "short positions are not enabled by this contract")
    _require(isinstance(verification.get("unresolved_decisions"), list), "unresolved_decisions must be a list")

    assets = raw["assets"]
    _require(isinstance(assets, Mapping) and assets, "assets must be a non-empty object")
    for symbol, asset in assets.items():
        _require(isinstance(symbol, str) and symbol, "asset symbols must be non-empty strings")
        _require(isinstance(asset, Mapping), f"asset {symbol} must be an object")
        _require(_REQUIRED_ASSET_FIELDS.issubset(asset), f"asset {symbol} missing fields: {sorted(_REQUIRED_ASSET_FIELDS - set(asset))}")
        _require(isinstance(asset["roles"], list) and asset["roles"], f"asset {symbol} roles must be non-empty")
        _require(isinstance(asset["tradable"], bool), f"asset {symbol} tradable must be boolean")

    versions = raw["strategy_versions"]
    _require(isinstance(versions, list) and versions, "strategy_versions must be a non-empty list")
    version_names: list[str] = []
    for strategy in versions:
        _validate_strategy_version(strategy, assets)
        version_names.append(strategy["version"])
    _require(len(version_names) == len(set(version_names)), "strategy_versions must not contain duplicate versions")
    _require(default_version is None or default_version in version_names, "product default version is not registered")

    research_version = product.get("active_research_strategy_version")
    _require(research_version in version_names, "active research strategy version is not registered")


def _validate_strategy_version(strategy: Mapping[str, Any], assets: Mapping[str, Any]) -> None:
    _require(isinstance(strategy, Mapping), "each strategy version must be an object")
    _require(_REQUIRED_VERSION_FIELDS.issubset(strategy), f"strategy version missing fields: {sorted(_REQUIRED_VERSION_FIELDS - set(strategy))}")
    version = strategy["version"]
    _require(isinstance(version, str) and version, "strategy version must be non-empty")
    _require(strategy["status"] in _ALLOWED_STATUSES, f"unsupported status for {version}")
    _require(isinstance(strategy["states"], list) and strategy["states"], f"states must be non-empty for {version}")
    _require(len(strategy["states"]) == len(set(strategy["states"])), f"states must be unique for {version}")
    _require(all(isinstance(state, str) and state for state in strategy["states"]), f"states must be strings for {version}")

    strategy_assets = strategy["strategy_assets"]
    benchmark_assets = strategy["benchmark_assets"]
    _require(isinstance(strategy_assets, list) and strategy_assets, f"strategy_assets must be non-empty for {version}")
    _require(len(strategy_assets) == len(set(strategy_assets)), f"strategy_assets must be unique for {version}")
    _require(all(symbol in assets for symbol in strategy_assets), f"unknown strategy asset in {version}")
    _require(all(assets[symbol]["tradable"] for symbol in strategy_assets), f"non-tradable strategy asset in {version}")
    _require(isinstance(benchmark_assets, list), f"benchmark_assets must be a list for {version}")
    _require(all(symbol in assets for symbol in benchmark_assets), f"unknown benchmark asset in {version}")

    weight_schema = strategy["weight_schema"]
    _require(isinstance(weight_schema, Mapping), f"weight_schema must be an object for {version}")
    _require(_as_finite_number(weight_schema.get("sum_target"), f"{version}.sum_target") == 1.0, f"{version} sum_target must be 1")
    _require(weight_schema.get("missing_asset_policy") == "missing_means_zero", f"{version} missing asset policy is invalid")
    _require(weight_schema.get("unknown_asset_policy") == "reject", f"{version} unknown asset policy is invalid")
    _require(isinstance(weight_schema.get("max_weights"), Mapping), f"{version} max_weights must be an object")
    _require(set(weight_schema["max_weights"]).issubset(set(strategy_assets)), f"{version} max_weights contains unknown asset")
    for symbol, limit in weight_schema["max_weights"].items():
        limit_value = _as_finite_number(limit, f"{version}.max_weights.{symbol}")
        _require(0.0 <= limit_value <= 1.0, f"{version}.max_weights.{symbol} must be between 0 and 1")
    warmup = weight_schema.get("warmup_default_weights")
    _require(isinstance(warmup, Mapping), f"{version} warmup_default_weights must be an object")
    _require(set(warmup).issubset(set(strategy_assets)), f"{version} warmup contains unknown asset")
    _require(abs(sum(_as_finite_number(value, f"{version}.warmup_default_weights") for value in warmup.values()) - 1.0) <= 1e-12, f"{version} warmup weights must sum to 1")

    timing = strategy["timing"]
    _require(isinstance(timing, Mapping), f"timing must be an object for {version}")
    _require(timing.get("signal_cutoff") == "close", f"{version} signal cutoff must be close")
    _require(timing.get("signal_uses_data_through_signal_date") is True, f"{version} may not use future data")
    _require(timing.get("execution_delay_trading_days") == 1, f"{version} execution delay must be one trading day")
    _require("execution_price_basis" in timing, f"{version} execution price basis must be explicit, even if pending")


@dataclass(frozen=True)
class StrategyContractRegistry:
    """Validated read-only view over the strategy contract registry."""

    _raw: Mapping[str, Any]

    @property
    def contract_hash(self) -> str:
        return _sha256(self._raw)

    @property
    def verification(self) -> Mapping[str, Any]:
        return self._raw["verification_contract"]

    @property
    def unresolved_decisions(self) -> tuple[str, ...]:
        return tuple(self.verification["unresolved_decisions"])

    @property
    def product_default_strategy_version(self) -> str | None:
        return self._raw["product"]["product_default_strategy_version"]

    def as_dict(self) -> dict[str, Any]:
        """Return a defensive copy suitable for hashing or serialization."""

        return copy.deepcopy(dict(self._raw))

    def get_strategy(self, version: str) -> Mapping[str, Any]:
        for strategy in self._raw["strategy_versions"]:
            if strategy["version"] == version:
                return strategy
        raise ContractError(f"unknown strategy version: {version}")

    def validate_weights(self, version: str, weights: Mapping[str, Any]) -> dict[str, float]:
        strategy = self.get_strategy(version)
        _require(isinstance(weights, Mapping) and weights, "target weights must be a non-empty object")
        strategy_assets = tuple(strategy["strategy_assets"])
        unknown = sorted(set(weights) - set(strategy_assets))
        _require(not unknown, f"target weights contain unknown assets: {unknown}")

        normalized = {symbol: 0.0 for symbol in strategy_assets}
        for symbol, raw_value in weights.items():
            value = _as_finite_number(raw_value, f"target_weights.{symbol}")
            _require(value >= 0.0, f"target_weights.{symbol} cannot be negative")
            max_weight = strategy["weight_schema"]["max_weights"].get(symbol)
            if max_weight is not None:
                _require(value <= float(max_weight) + float(self.verification["portfolio_weight_tolerance"]), f"target_weights.{symbol} exceeds max weight")
            normalized[symbol] = value

        total = sum(normalized.values())
        tolerance = float(self.verification["portfolio_weight_tolerance"])
        _require(abs(total - float(strategy["weight_schema"]["sum_target"])) <= tolerance, f"target weights must sum to 1 within {tolerance}")
        return normalized

    def validate_target_snapshot(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "strategy_version",
            "signal_date",
            "execution_date",
            "state",
            "target_weights",
            "indicators",
            "reason_codes",
            "input_data_version",
        }
        _require(isinstance(snapshot, Mapping), "target snapshot must be an object")
        _require(required.issubset(snapshot), f"target snapshot missing fields: {sorted(required - set(snapshot))}")
        strategy = self.get_strategy(snapshot["strategy_version"])
        _require(snapshot["state"] in strategy["states"], f"unknown state for {snapshot['strategy_version']}: {snapshot['state']}")
        signal_date = _as_date(snapshot["signal_date"], "signal_date")
        execution_date = _as_date(snapshot["execution_date"], "execution_date")
        _require(execution_date > signal_date, "execution_date must be after signal_date")
        _require(isinstance(snapshot["indicators"], Mapping), "indicators must be an object")
        _require(isinstance(snapshot["reason_codes"], list), "reason_codes must be a list")
        _require(all(isinstance(code, str) and code for code in snapshot["reason_codes"]), "reason_codes must contain non-empty strings")
        _require(isinstance(snapshot["input_data_version"], str) and snapshot["input_data_version"], "input_data_version must be non-empty")
        normalized_weights = self.validate_weights(snapshot["strategy_version"], snapshot["target_weights"])
        result = copy.deepcopy(dict(snapshot))
        result["target_weights"] = normalized_weights
        return result


def load_contract(path: str | Path = DEFAULT_CONTRACT_PATH) -> StrategyContractRegistry:
    """Load and validate the frozen contract from disk."""

    contract_path = Path(path)
    _require(contract_path.exists(), f"strategy contract does not exist: {contract_path}")
    try:
        raw = json.loads(contract_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid JSON in strategy contract: {contract_path}") from exc
    _validate_registry(raw)
    return StrategyContractRegistry(copy.deepcopy(raw))
