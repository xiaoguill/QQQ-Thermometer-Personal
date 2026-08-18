"""Deterministic paper-plan calculations with no broker or order side effects."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "qqq-m17-paper-plan/v1"
INPUT_SCHEMA = "qqq-m17-paper-input/v1"
_TARGET_TOLERANCE = 0.005


class PaperPlanError(ValueError):
    """Raised for invalid local holdings input or unsafe plan data."""

    def __init__(self, message: str, *, code: str = "INVALID_INPUT") -> None:
        super().__init__(message)
        self.code = code


def _number(value: Any, *, field: str, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        raise PaperPlanError(f"{field} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PaperPlanError(f"{field} must be a number") from exc
    if not math.isfinite(number) or minimum is not None and number < minimum:
        raise PaperPlanError(f"{field} is outside the permitted range")
    return number


@dataclass(frozen=True)
class PaperHolding:
    symbol: str
    quantity: float
    average_cost: float | None = None

    @classmethod
    def from_value(cls, symbol: str, value: Any) -> "PaperHolding":
        if not isinstance(symbol, str) or not symbol.strip():
            raise PaperPlanError("position symbol is required")
        if isinstance(value, Mapping):
            quantity = value.get("quantity")
            average_cost = value.get("average_cost")
        else:
            quantity = value
            average_cost = None
        return cls(
            symbol=symbol.strip().upper(),
            quantity=_number(quantity, field=f"positions.{symbol}.quantity", minimum=0.0),
            average_cost=None if average_cost in (None, "") else _number(average_cost, field=f"positions.{symbol}.average_cost", minimum=0.0),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "average_cost": self.average_cost,
        }


@dataclass(frozen=True)
class PaperInput:
    portfolio_id: str
    base_currency: str
    starting_cash: float
    holdings: tuple[PaperHolding, ...]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PaperInput":
        if not isinstance(raw, Mapping):
            raise PaperPlanError("paper input must be an object")
        if raw.get("$schema") not in (None, INPUT_SCHEMA):
            raise PaperPlanError("unsupported paper input schema")
        portfolio_id = str(raw.get("portfolio_id", "")).strip()
        base_currency = str(raw.get("base_currency", "")).strip().upper()
        if not portfolio_id or len(portfolio_id) > 120:
            raise PaperPlanError("portfolio_id is required")
        if len(base_currency) != 3 or not base_currency.isalpha():
            raise PaperPlanError("base_currency must be a three-letter code")
        positions = raw.get("positions")
        if not isinstance(positions, Mapping):
            raise PaperPlanError("positions must be an object")
        holdings = tuple(PaperHolding.from_value(str(symbol), value) for symbol, value in sorted(positions.items()))
        symbols = [item.symbol for item in holdings]
        if len(set(symbols)) != len(symbols):
            raise PaperPlanError("positions must not contain duplicate symbols")
        return cls(
            portfolio_id=portfolio_id,
            base_currency=base_currency,
            starting_cash=_number(raw.get("starting_cash", 0), field="starting_cash", minimum=0.0),
            holdings=holdings,
        )

    @property
    def has_explicit_holdings(self) -> bool:
        return self.starting_cash > 0 or any(item.quantity > 0 for item in self.holdings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "portfolio_id": self.portfolio_id,
            "base_currency": self.base_currency,
            "starting_cash": self.starting_cash,
            "positions": {item.symbol: item.as_dict() for item in self.holdings},
        }


def load_paper_input(path: str | Path) -> PaperInput:
    source = Path(path).expanduser()
    if not source.is_file():
        raise PaperPlanError("paper holdings input is not configured", code="INPUT_REQUIRED")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaperPlanError("paper holdings input could not be read") from exc
    try:
        return PaperInput.from_mapping(raw)
    except PaperPlanError:
        raise
    except Exception as exc:
        raise PaperPlanError("paper holdings input is invalid") from exc


def empty_paper_plan(status: str, *, reason: str, as_of: str | None = None, target_weights: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return an explicit fail-closed plan envelope for the web layer."""

    return {
        "schema": SCHEMA,
        "status": status,
        "reason": reason,
        "as_of": as_of,
        "paper_only": True,
        "execution_allowed": False,
        "order_created": False,
        "target_weights": dict(sorted((target_weights or {}).items())),
        "current_positions": [],
        "actions": [],
        "warnings": ["该结果只是纸上计划预览，不是订单，也不会写入券商账户。"],
    }


def _price_info(prices: Mapping[str, Any], symbol: str) -> tuple[float | None, str | None, bool]:
    raw = prices.get(symbol)
    if isinstance(raw, Mapping):
        value = raw.get("price", raw.get("last", raw.get("close")))
        quality = raw.get("quality")
        provisional = raw.get("provisional") is True
    else:
        value = raw
        quality = "OK"
        provisional = True
    if value in (None, ""):
        return None, str(quality) if quality is not None else None, provisional
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, str(quality) if quality is not None else None, provisional
    if not math.isfinite(number) or number <= 0:
        return None, str(quality) if quality is not None else None, provisional
    return number, str(quality) if quality is not None else None, provisional


def build_paper_plan(
    *,
    target_weights: Mapping[str, Any],
    paper_input: PaperInput,
    prices: Mapping[str, Any],
    as_of: str | None,
    strategy_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Value explicit holdings against confirmed target weights.

    This function accepts target weights from the already-confirmed strategy
    boundary. It deliberately contains no market-state, indicator, or strategy
    rule; it only turns a user-supplied paper snapshot into a read-only delta.
    """

    if not isinstance(target_weights, Mapping) or not target_weights:
        return empty_paper_plan("TARGET_UNAVAILABLE", reason="confirmed target weights are unavailable", as_of=as_of)
    if not paper_input.has_explicit_holdings:
        return empty_paper_plan("INPUT_REQUIRED", reason="explicit local cash or positions are required", as_of=as_of, target_weights=target_weights)

    normalized_targets: dict[str, float] = {}
    for symbol, raw_weight in target_weights.items():
        name = str(symbol).strip().upper()
        if not name:
            raise PaperPlanError("target symbol is invalid", code="TARGET_INVALID")
        normalized_targets[name] = _number(raw_weight, field=f"target_weights.{name}", minimum=0.0)
    weight_total = sum(normalized_targets.values())
    if not math.isfinite(weight_total) or abs(weight_total - 1.0) > _TARGET_TOLERANCE:
        return empty_paper_plan("TARGET_INVALID", reason="confirmed target weights do not sum to 100%", as_of=as_of, target_weights=normalized_targets)

    current_by_symbol = {holding.symbol: holding for holding in paper_input.holdings if holding.quantity > 0}
    symbols_requiring_prices = set(current_by_symbol) | {symbol for symbol, weight in normalized_targets.items() if weight > 0}
    resolved_prices: dict[str, float] = {}
    quality_issues: list[dict[str, Any]] = []
    provisional_seen = False
    for symbol in sorted(symbols_requiring_prices):
        price, quality, provisional = _price_info(prices, symbol)
        provisional_seen = provisional_seen or provisional
        if price is None or quality != "OK" or not provisional:
            quality_issues.append({"symbol": symbol, "quality": quality or "MISSING", "provisional": provisional})
        else:
            resolved_prices[symbol] = price
    if quality_issues:
        failed = empty_paper_plan("DATA_QUALITY_FAILED", reason="one or more required prices are unavailable or not quality OK", as_of=as_of, target_weights=normalized_targets)
        failed["quality_issues"] = quality_issues
        return failed

    current_positions: list[dict[str, Any]] = []
    current_value = 0.0
    for symbol, holding in sorted(current_by_symbol.items()):
        value = holding.quantity * resolved_prices[symbol]
        current_value += value
        current_positions.append({
            "symbol": symbol,
            "quantity": holding.quantity,
            "price": resolved_prices[symbol],
            "value": value,
            "average_cost": holding.average_cost,
        })
    nav = paper_input.starting_cash + current_value
    if nav <= 0 or not math.isfinite(nav):
        raise PaperPlanError("paper portfolio value must be positive", code="INPUT_REQUIRED")

    actions: list[dict[str, Any]] = []
    for symbol in sorted(set(current_by_symbol) | set(normalized_targets)):
        current = current_by_symbol.get(symbol)
        quantity = current.quantity if current is not None else 0.0
        price = resolved_prices.get(symbol)
        target_weight = normalized_targets.get(symbol, 0.0)
        target_value = nav * target_weight
        current_asset_value = quantity * price if price is not None else 0.0
        delta_value = target_value - current_asset_value
        delta_units = delta_value / price if price is not None else 0.0
        if abs(delta_value) < 0.005:
            action = "hold"
        elif delta_value > 0:
            action = "increase"
        else:
            action = "decrease"
        actions.append({
            "symbol": symbol,
            "action": action,
            "current_quantity": quantity,
            "current_value": current_asset_value,
            "current_weight": current_asset_value / nav,
            "target_weight": target_weight,
            "target_value": target_value,
            "delta_value": delta_value,
            "estimated_delta_units": delta_units,
            "estimated_price": price,
            "not_order": True,
        })

    return {
        "schema": SCHEMA,
        "status": "READY",
        "reason": "confirmed target weights valued against explicit local paper holdings",
        "as_of": as_of,
        "paper_only": True,
        "execution_allowed": False,
        "order_created": False,
        "strategy": dict(strategy_meta or {}),
        "portfolio": {
            "portfolio_id": paper_input.portfolio_id,
            "base_currency": paper_input.base_currency,
            "starting_cash": paper_input.starting_cash,
            "estimated_nav": nav,
            "price_basis": "latest_massive_observation",
        },
        "target_weights": dict(sorted(normalized_targets.items())),
        "current_positions": current_positions,
        "actions": actions,
        "warnings": [
            "该结果只是纸上计划预览，不是订单，也不会写入券商账户。",
            "估值使用当前盘中临时观察；盘中观察不会改变已确认策略目标。" if provisional_seen else "估值数据不是盘中临时观察，仍需人工核对价格口径。",
        ],
    }


__all__ = [
    "PaperHolding",
    "PaperInput",
    "PaperPlanError",
    "build_paper_plan",
    "empty_paper_plan",
    "load_paper_input",
]
