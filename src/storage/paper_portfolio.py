"""Deterministic paper-portfolio simulation on top of the M08 repositories.

The service accepts a verified candidate target-weight snapshot and explicit
next-session prices.  It never fetches prices, talks to a broker, or accepts a
client-created weight mapping as a substitute for the target snapshot.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Mapping

from src.thermometer.target_weights import TargetWeightSnapshot

from .normalization import TradingCalendar
from .sqlite_store import (
    SQLiteRepository,
    StorageConflictError,
    StorageError,
    StorageValidationError,
    StoredRecord,
)


PAPER_PORTFOLIO_SCHEMA = "qqq-paper-portfolio/v1"
PAPER_EXECUTION_IMPLEMENTATION_VERSION = "m09-paper-portfolio/v1"
PAPER_RECONCILIATION_SCHEMA = "qqq-paper-reconciliation/v1"
PAPER_LEDGER_EVENT_SCHEMA = "qqq-paper-ledger-event/v1"
PAPER_STATUS = "PAPER_SHADOW"
_QUALITY_STATUSES = {"OK", "STALE", "PARTIAL", "FAILED", "NEEDS_REVIEW"}
_PRICE_BASES = {"adjusted_ohlcv", "unadjusted_ohlcv"}


class PaperPortfolioError(StorageError):
    """Base error for the paper-only execution boundary."""


class PaperInputError(PaperPortfolioError, ValueError):
    """Raised when a paper simulation input is invalid or incomplete."""


class PaperReconciliationError(PaperPortfolioError):
    """Raised when the simulated portfolio cannot close its NAV identity."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PaperInputError(message)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PaperInputError("paper payload must be finite JSON data") from exc


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _safe_text(value: Any, field_name: str) -> str:
    _require(isinstance(value, str) and value.strip(), f"{field_name} must be non-empty")
    return value.strip()


def _iso_date(value: Any, field_name: str) -> str:
    _require(isinstance(value, str), f"{field_name} must be an ISO date")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise PaperInputError(f"{field_name} must be an ISO date") from exc


def _iso_timestamp(value: Any, field_name: str) -> str:
    _require(isinstance(value, str) and value.strip(), f"{field_name} must be an ISO timestamp")
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise PaperInputError(f"{field_name} must be an ISO timestamp") from exc
    _require(parsed.tzinfo is not None, f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite(value: Any, field_name: str, *, minimum: float | None = None) -> float:
    _require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{field_name} must be numeric")
    result = float(value)
    _require(math.isfinite(result), f"{field_name} must be finite")
    if minimum is not None:
        _require(result >= minimum, f"{field_name} must be >= {minimum}")
    return result


def _normalise_positions(values: Mapping[str, Any]) -> dict[str, float]:
    _require(isinstance(values, Mapping), "positions must be an object")
    result: dict[str, float] = {}
    for symbol, value in values.items():
        name = _safe_text(symbol, "position symbol").upper()
        quantity = _finite(value, f"position.{name}", minimum=0.0)
        if quantity > 1e-12:
            result[name] = quantity
    return dict(sorted(result.items()))


@dataclass(frozen=True)
class PaperExecutionConfig:
    """Versioned execution assumptions; these are not strategy parameters."""

    initial_cash: float = 100_000.0
    cost_bps: float = 5.0
    slippage_bps: float = 0.0
    price_basis: str = "unadjusted_ohlcv"
    allow_fractional_shares: bool = True

    def __post_init__(self) -> None:
        _finite(self.initial_cash, "initial_cash", minimum=0.0)
        _require(self.initial_cash > 0.0, "initial_cash must be positive")
        _finite(self.cost_bps, "cost_bps", minimum=0.0)
        _finite(self.slippage_bps, "slippage_bps", minimum=0.0)
        _require(self.cost_bps < 10_000.0, "cost_bps must be below 10000 bps")
        _require(self.slippage_bps < 10_000.0, "slippage_bps must be below 10000 bps")
        _require(self.price_basis in _PRICE_BASES, f"unsupported paper price basis: {self.price_basis}")
        _require(isinstance(self.allow_fractional_shares, bool), "allow_fractional_shares must be boolean")

    @property
    def cost_rate(self) -> float:
        return self.cost_bps / 10_000.0

    @property
    def slippage_rate(self) -> float:
        return self.slippage_bps / 10_000.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "qqq-paper-execution-config/v1",
            "implementation_version": PAPER_EXECUTION_IMPLEMENTATION_VERSION,
            "initial_cash": self.initial_cash,
            "cost_bps": self.cost_bps,
            "slippage_bps": self.slippage_bps,
            "price_basis": self.price_basis,
            "allow_fractional_shares": self.allow_fractional_shares,
        }


@dataclass(frozen=True)
class PaperPrice:
    """One explicit execution/mark price for one ETF on one session."""

    symbol: str
    session_date: str
    price: float
    price_basis: str
    quality: str = "OK"
    dividend_per_share: float = 0.0
    split_factor: float = 1.0

    def __post_init__(self) -> None:
        symbol = _safe_text(self.symbol, "price.symbol").upper()
        session_date = _iso_date(self.session_date, "price.session_date")
        price = _finite(self.price, "price.price", minimum=0.0)
        _require(price > 0.0, "price.price must be positive")
        _require(self.price_basis in _PRICE_BASES, f"unsupported price.price_basis: {self.price_basis}")
        _require(self.quality in _QUALITY_STATUSES, f"unsupported price quality: {self.quality}")
        dividend = _finite(self.dividend_per_share, "price.dividend_per_share", minimum=0.0)
        split = _finite(self.split_factor, "price.split_factor", minimum=0.0)
        _require(split > 0.0, "price.split_factor must be positive")
        if self.price_basis == "adjusted_ohlcv":
            _require(dividend == 0.0 and split == 1.0, "adjusted prices cannot carry explicit dividends or splits")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "session_date", session_date)
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "dividend_per_share", dividend)
        object.__setattr__(self, "split_factor", split)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "qqq-paper-price/v1",
            "symbol": self.symbol,
            "session_date": self.session_date,
            "price": self.price,
            "price_basis": self.price_basis,
            "quality": self.quality,
            "dividend_per_share": self.dividend_per_share,
            "split_factor": self.split_factor,
        }


@dataclass(frozen=True)
class PaperDayInput:
    """One signal-to-execution step for a paper shadow portfolio."""

    portfolio_id: str
    run_id: str
    target: TargetWeightSnapshot
    prices: tuple[PaperPrice, ...]
    as_of: str | None = None

    def __post_init__(self) -> None:
        portfolio_id = _safe_text(self.portfolio_id, "portfolio_id")
        run_id = _safe_text(self.run_id, "run_id")
        _require("|" not in portfolio_id, "portfolio_id cannot contain the repository key separator")
        _require("|" not in run_id, "run_id cannot contain the repository key separator")
        _require(isinstance(self.target, TargetWeightSnapshot), "paper target must be a TargetWeightSnapshot")
        _require(self.target.candidate_only is True and self.target.execution_eligible is False, "paper target must remain candidate-only")
        _require(isinstance(self.prices, tuple) and self.prices, "paper prices must be a non-empty tuple")
        _require(all(isinstance(item, PaperPrice) for item in self.prices), "paper prices must contain PaperPrice objects")
        symbols = tuple(item.symbol for item in self.prices)
        _require(len(symbols) == len(set(symbols)), "paper prices must have unique symbols")
        _require(symbols == tuple(sorted(symbols)), "paper prices must be sorted by symbol")
        if self.as_of is not None:
            object.__setattr__(self, "as_of", _iso_timestamp(self.as_of, "paper.as_of"))
        object.__setattr__(self, "portfolio_id", portfolio_id)
        object.__setattr__(self, "run_id", run_id)

    @classmethod
    def from_prices(
        cls,
        portfolio_id: str,
        run_id: str,
        target: TargetWeightSnapshot,
        prices: Mapping[str, PaperPrice],
        *,
        as_of: str | None = None,
    ) -> "PaperDayInput":
        _require(isinstance(prices, Mapping) and prices, "prices must be a non-empty mapping")
        values = tuple(prices[key] for key in sorted(prices))
        return cls(portfolio_id, run_id, target, values, as_of=as_of)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "qqq-paper-day-input/v1",
            "portfolio_id": self.portfolio_id,
            "run_id": self.run_id,
            "target": self.target.as_dict(),
            "prices": [item.as_dict() for item in self.prices],
            "as_of": self.as_of,
        }


@dataclass(frozen=True)
class PaperReconciliation:
    """Daily NAV, cost, turnover, and target-to-actual reconciliation."""

    portfolio_id: str
    signal_date: str
    execution_date: str
    nav_before: float
    nav_after: float
    cash: float
    fees: float
    slippage_cost: float
    turnover: float
    target_weights: Mapping[str, float]
    actual_weights: Mapping[str, float]
    weight_errors: Mapping[str, float]
    trade_count: int
    scaled_for_cash: bool
    identity_error: float
    status: str = "RECONCILED"

    def __post_init__(self) -> None:
        _safe_text(self.portfolio_id, "reconciliation.portfolio_id")
        _iso_date(self.signal_date, "reconciliation.signal_date")
        _iso_date(self.execution_date, "reconciliation.execution_date")
        for field_name in ("nav_before", "nav_after", "cash", "fees", "slippage_cost", "turnover", "identity_error"):
            _finite(getattr(self, field_name), f"reconciliation.{field_name}", minimum=0.0 if field_name != "identity_error" else None)
        _require(self.cash >= -1e-8, "reconciliation cash cannot be negative")
        _require(isinstance(self.trade_count, int) and self.trade_count >= 0, "reconciliation.trade_count must be non-negative")
        _require(isinstance(self.scaled_for_cash, bool), "reconciliation.scaled_for_cash must be boolean")
        _require(self.status in {"RECONCILED", "NEEDS_REVIEW"}, "unsupported reconciliation status")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": PAPER_RECONCILIATION_SCHEMA,
            "implementation_version": PAPER_EXECUTION_IMPLEMENTATION_VERSION,
            "portfolio_id": self.portfolio_id,
            "signal_date": self.signal_date,
            "execution_date": self.execution_date,
            "nav_before": self.nav_before,
            "nav_after": self.nav_after,
            "cash": self.cash,
            "fees": self.fees,
            "slippage_cost": self.slippage_cost,
            "turnover": self.turnover,
            "target_weights": dict(sorted(self.target_weights.items())),
            "actual_weights": dict(sorted(self.actual_weights.items())),
            "weight_errors": dict(sorted(self.weight_errors.items())),
            "trade_count": self.trade_count,
            "scaled_for_cash": self.scaled_for_cash,
            "identity_error": self.identity_error,
            "status": self.status,
        }


@dataclass(frozen=True)
class PaperPortfolioState:
    """Persisted daily state; the payload is sufficient for restart recovery."""

    portfolio_id: str
    run_id: str
    signal_date: str
    execution_date: str
    as_of: str
    strategy_version: str
    strategy_config_hash: str
    target_snapshot_hash: str
    input_hash: str
    initial_cash: float
    cash: float
    positions: Mapping[str, float]
    nav: float
    fees_paid: float
    slippage_cost: float
    turnover: float
    price_basis: str
    data_quality: str
    status: str
    target_weights: Mapping[str, float]
    reconciliation: PaperReconciliation

    def __post_init__(self) -> None:
        _safe_text(self.portfolio_id, "state.portfolio_id")
        _safe_text(self.run_id, "state.run_id")
        _iso_date(self.signal_date, "state.signal_date")
        _iso_date(self.execution_date, "state.execution_date")
        _iso_timestamp(self.as_of, "state.as_of")
        _safe_text(self.strategy_version, "state.strategy_version")
        _require(isinstance(self.strategy_config_hash, str) and len(self.strategy_config_hash) == 64, "state.strategy_config_hash must be SHA-256")
        _require(isinstance(self.target_snapshot_hash, str) and len(self.target_snapshot_hash) == 64, "state.target_snapshot_hash must be SHA-256")
        _require(isinstance(self.input_hash, str) and len(self.input_hash) == 64, "state.input_hash must be SHA-256")
        for field_name in ("initial_cash", "cash", "nav", "fees_paid", "slippage_cost", "turnover"):
            _finite(getattr(self, field_name), f"state.{field_name}", minimum=0.0)
        _require(self.price_basis in _PRICE_BASES, "state.price_basis is unsupported")
        _require(self.data_quality in _QUALITY_STATUSES, "state.data_quality is unsupported")
        _require(self.status == PAPER_STATUS, "state must remain paper shadow")
        _require(isinstance(self.reconciliation, PaperReconciliation), "state.reconciliation is invalid")
        object.__setattr__(self, "positions", _normalise_positions(self.positions))
        object.__setattr__(self, "target_weights", dict(sorted((str(k), float(v)) for k, v in self.target_weights.items())))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": PAPER_PORTFOLIO_SCHEMA,
            "implementation_version": PAPER_EXECUTION_IMPLEMENTATION_VERSION,
            "portfolio_id": self.portfolio_id,
            "run_id": self.run_id,
            "signal_date": self.signal_date,
            "execution_date": self.execution_date,
            "as_of": self.as_of,
            "strategy_version": self.strategy_version,
            "strategy_config_hash": self.strategy_config_hash,
            "target_snapshot_hash": self.target_snapshot_hash,
            "input_hash": self.input_hash,
            "initial_cash": self.initial_cash,
            "cash": self.cash,
            "positions": dict(sorted(self.positions.items())),
            "nav": self.nav,
            "fees_paid": self.fees_paid,
            "slippage_cost": self.slippage_cost,
            "turnover": self.turnover,
            "price_basis": self.price_basis,
            "data_quality": self.data_quality,
            "status": self.status,
            "target_weights": dict(sorted(self.target_weights.items())),
            "reconciliation": self.reconciliation.as_dict(),
        }


@dataclass(frozen=True)
class PaperDayResult:
    state: PaperPortfolioState
    reconciliation: PaperReconciliation
    ledger_events: tuple[StoredRecord, ...]
    idempotent: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.as_dict(),
            "reconciliation": self.reconciliation.as_dict(),
            "ledger_events": [item.as_dict() for item in self.ledger_events],
            "idempotent": self.idempotent,
        }


class PaperPortfolioService:
    """Paper-only daily rebalance service using M08 repositories."""

    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        config: PaperExecutionConfig | None = None,
        calendar: TradingCalendar | None = None,
    ) -> None:
        _require(isinstance(repository, SQLiteRepository), "repository must be SQLiteRepository")
        _require(repository.store.initialized, "repository store must be initialized")
        self.repository = repository
        self.config = config or PaperExecutionConfig()
        self.calendar = calendar or TradingCalendar()

    def simulate_day(self, request: PaperDayInput) -> PaperDayResult:
        self._validate_request(request)
        target = request.target
        execution_date = target.execution_date
        snapshot_key = self._portfolio_key(request.portfolio_id, execution_date)
        input_hash = _hash({"request": request.as_dict(), "config": self.config.as_dict()})
        existing = self.repository.get("portfolio_snapshot", snapshot_key)
        if existing is not None:
            return self._replay_existing(existing, input_hash)

        latest = self._latest_state(request.portfolio_id)
        if latest is not None and latest.execution_date >= execution_date:
            raise PaperInputError("historical or same-day paper execution cannot be rewritten")
        prices = {item.symbol: item for item in request.prices}
        previous_cash = self.config.initial_cash if latest is None else latest.cash
        previous_positions = {} if latest is None else dict(latest.positions)
        if latest is not None:
            _require(abs(latest.initial_cash - self.config.initial_cash) <= 1e-8, "initial_cash differs from persisted portfolio")
            _require(latest.price_basis == self.config.price_basis, "price basis differs from persisted portfolio")
        required_symbols = {
            symbol
            for symbol, weight in target.target_weights.items()
            if float(weight) > 1e-12
        } | {symbol for symbol, quantity in previous_positions.items() if quantity > 1e-12}
        missing = sorted(required_symbols - set(prices))
        if missing:
            raise PaperInputError(f"execution prices are missing for required symbols: {missing}")

        positions = dict(previous_positions)
        cash = previous_cash
        fees = 0.0
        slippage_cost = 0.0
        turnover = 0.0
        ledger_actions: list[dict[str, Any]] = []
        if latest is None:
            ledger_actions.append(
                {
                    "event_type": "INITIAL_CASH",
                    "symbol": "CASH",
                    "quantity": self.config.initial_cash,
                    "price": 1.0,
                    "cost": 0.0,
                    "cash_delta": self.config.initial_cash,
                }
            )

        for symbol in sorted(previous_positions):
            price = prices.get(symbol)
            if price is None:
                continue
            quantity = positions.get(symbol, 0.0)
            if price.split_factor != 1.0 and quantity > 1e-12:
                adjusted_quantity = quantity * price.split_factor
                positions[symbol] = adjusted_quantity
                ledger_actions.append(
                    {
                        "event_type": "SPLIT_ADJUSTMENT",
                        "symbol": symbol,
                        "quantity": adjusted_quantity - quantity,
                        "price": price.price,
                        "cost": 0.0,
                        "cash_delta": 0.0,
                        "split_factor": price.split_factor,
                    }
                )
            if price.dividend_per_share > 0.0 and positions.get(symbol, 0.0) > 1e-12:
                dividend_cash = positions[symbol] * price.dividend_per_share
                cash += dividend_cash
                ledger_actions.append(
                    {
                        "event_type": "DIVIDEND",
                        "symbol": symbol,
                        "quantity": positions[symbol],
                        "price": price.dividend_per_share,
                        "cost": 0.0,
                        "cash_delta": dividend_cash,
                    }
                )

        nav_before = cash + sum(positions.get(symbol, 0.0) * prices[symbol].price for symbol in positions if symbol in prices)
        _require(math.isfinite(nav_before) and nav_before > 0.0, "paper NAV before trade must be positive")

        desired_quantities: dict[str, float] = {}
        for symbol, weight in sorted(target.target_weights.items()):
            numeric_weight = _finite(weight, f"target weight {symbol}", minimum=0.0)
            if numeric_weight <= 1e-12:
                desired_quantities[symbol] = 0.0
                continue
            _require(symbol in prices, f"target price missing for positive-weight symbol {symbol}")
            desired = nav_before * numeric_weight / prices[symbol].price
            if not self.config.allow_fractional_shares:
                desired = math.floor(desired + 1e-12)
            desired_quantities[symbol] = max(0.0, desired)

        for symbol in sorted(set(positions) | set(desired_quantities)):
            positions.setdefault(symbol, 0.0)
            desired_quantities.setdefault(symbol, 0.0)

        trades: list[tuple[str, float]] = []
        for symbol in sorted(set(positions) | set(desired_quantities)):
            delta = desired_quantities[symbol] - positions.get(symbol, 0.0)
            if abs(delta) > 1e-12:
                _require(symbol in prices, f"execution price missing for trade symbol {symbol}")
                trades.append((symbol, delta))

        for symbol, delta in (item for item in trades if item[1] < 0.0):
            price = prices[symbol]
            quantity = min(-delta, positions.get(symbol, 0.0))
            execution_price = price.price * (1.0 - self.config.slippage_rate)
            notional = quantity * execution_price
            fee = notional * self.config.cost_rate
            cash += notional - fee
            fees += fee
            slippage_cost += quantity * (price.price - execution_price)
            turnover += quantity * price.price
            positions[symbol] = max(0.0, positions.get(symbol, 0.0) - quantity)
            ledger_actions.append(
                {
                    "event_type": "SELL",
                    "symbol": symbol,
                    "quantity": -quantity,
                    "price": execution_price,
                    "cost": fee,
                    "cash_delta": notional - fee,
                    "mid_price": price.price,
                }
            )

        scaled_for_cash = False
        for symbol, delta in (item for item in trades if item[1] > 0.0):
            price = prices[symbol]
            requested_quantity = delta
            execution_price = price.price * (1.0 + self.config.slippage_rate)
            unit_cash = execution_price * (1.0 + self.config.cost_rate)
            if cash + 1e-10 < requested_quantity * unit_cash:
                scaled_for_cash = True
                if self.config.allow_fractional_shares:
                    quantity = max(0.0, cash / unit_cash)
                else:
                    quantity = max(0.0, math.floor(cash / unit_cash + 1e-12))
            else:
                quantity = requested_quantity
            if quantity <= 1e-12:
                continue
            notional = quantity * execution_price
            fee = notional * self.config.cost_rate
            cash -= notional + fee
            fees += fee
            slippage_cost += quantity * (execution_price - price.price)
            turnover += quantity * price.price
            positions[symbol] = positions.get(symbol, 0.0) + quantity
            ledger_actions.append(
                {
                    "event_type": "BUY",
                    "symbol": symbol,
                    "quantity": quantity,
                    "price": execution_price,
                    "cost": fee,
                    "cash_delta": -(notional + fee),
                    "mid_price": price.price,
                    "requested_quantity": requested_quantity,
                }
            )

        positions = _normalise_positions(positions)
        nav_after = cash + sum(quantity * prices[symbol].price for symbol, quantity in positions.items())
        identity_error = nav_after - (nav_before - fees - slippage_cost)
        if abs(identity_error) > 1e-7 * max(1.0, nav_before):
            raise PaperReconciliationError(f"paper NAV reconciliation failed: {identity_error}")
        _require(cash >= -1e-8, "paper cash became negative")
        cash = max(0.0, cash)
        actual_weights = {
            symbol: (quantity * prices[symbol].price / nav_after if nav_after > 0.0 else 0.0)
            for symbol, quantity in sorted(positions.items())
        }
        actual_weights["CASH"] = cash / nav_after if nav_after > 0.0 else 1.0
        target_weights = {symbol: float(weight) for symbol, weight in sorted(target.target_weights.items())}
        weight_errors = {
            symbol: target_weights.get(symbol, 0.0) - actual_weights.get(symbol, 0.0)
            for symbol in sorted(target_weights)
        }
        reconciliation = PaperReconciliation(
            portfolio_id=request.portfolio_id,
            signal_date=target.signal_date,
            execution_date=target.execution_date,
            nav_before=nav_before,
            nav_after=nav_after,
            cash=cash,
            fees=fees,
            slippage_cost=slippage_cost,
            turnover=turnover,
            target_weights=target_weights,
            actual_weights=actual_weights,
            weight_errors=weight_errors,
            trade_count=sum(1 for action in ledger_actions if action["event_type"] in {"BUY", "SELL"}),
            scaled_for_cash=scaled_for_cash,
            identity_error=identity_error,
        )
        as_of = request.as_of or target.as_of
        as_of = _iso_timestamp(as_of, "paper.as_of")
        previous_fees = 0.0 if latest is None else latest.fees_paid
        previous_slippage = 0.0 if latest is None else latest.slippage_cost
        previous_turnover = 0.0 if latest is None else latest.turnover
        state = PaperPortfolioState(
            portfolio_id=request.portfolio_id,
            run_id=request.run_id,
            signal_date=target.signal_date,
            execution_date=target.execution_date,
            as_of=as_of,
            strategy_version=target.strategy_version,
            strategy_config_hash=target.strategy_config_hash,
            target_snapshot_hash=target.content_hash,
            input_hash=input_hash,
            initial_cash=self.config.initial_cash,
            cash=cash,
            positions=positions,
            nav=nav_after,
            fees_paid=previous_fees + fees,
            slippage_cost=previous_slippage + slippage_cost,
            turnover=previous_turnover + turnover,
            price_basis=self.config.price_basis,
            data_quality="OK",
            status=PAPER_STATUS,
            target_weights=target_weights,
            reconciliation=reconciliation,
        )

        ledger_records: list[StoredRecord] = []
        snapshot_payload = state.as_dict()
        run_key = self._run_key(request.portfolio_id, execution_date)
        run_payload = {
            "schema": "qqq-paper-shadow-run/v1",
            "implementation_version": PAPER_EXECUTION_IMPLEMENTATION_VERSION,
            "run_id": request.run_id,
            "portfolio_id": request.portfolio_id,
            "signal_date": target.signal_date,
            "execution_date": execution_date,
            "target_snapshot_hash": target.content_hash,
            "input_hash": input_hash,
            "status": "SIMULATED",
            "reconciliation": reconciliation.as_dict(),
        }
        with self.repository.transaction():
            for index, action in enumerate(ledger_actions):
                event_key = self._ledger_key(request.portfolio_id, execution_date, index, action["event_type"])
                event_payload = {
                    "schema": PAPER_LEDGER_EVENT_SCHEMA,
                    "implementation_version": PAPER_EXECUTION_IMPLEMENTATION_VERSION,
                    "event_key": event_key,
                    "event_type": action["event_type"],
                    "portfolio_id": request.portfolio_id,
                    "run_id": request.run_id,
                    "signal_date": target.signal_date,
                    "execution_date": execution_date,
                    "strategy_version": target.strategy_version,
                    "target_snapshot_hash": target.content_hash,
                    "input_hash": input_hash,
                    "action": copy.deepcopy(action),
                }
                ledger_records.append(
                    self.repository.put_ledger_event(
                        event_key,
                        event_payload,
                        event_date=execution_date,
                        event_type=action["event_type"],
                        idempotency_key=event_key,
                        status="RECORDED",
                        quantity=action.get("quantity"),
                        price=action.get("price"),
                        cost=action.get("cost", 0.0),
                    )
                )
            mark_key = self._ledger_key(request.portfolio_id, execution_date, 999, "NAV_MARK")
            mark_payload = {
                "schema": PAPER_LEDGER_EVENT_SCHEMA,
                "implementation_version": PAPER_EXECUTION_IMPLEMENTATION_VERSION,
                "event_key": mark_key,
                "event_type": "NAV_MARK",
                "portfolio_id": request.portfolio_id,
                "run_id": request.run_id,
                "signal_date": target.signal_date,
                "execution_date": execution_date,
                "strategy_version": target.strategy_version,
                "target_snapshot_hash": target.content_hash,
                "input_hash": input_hash,
                "nav": nav_after,
                "cash": cash,
                "positions": dict(sorted(positions.items())),
                "reconciliation": reconciliation.as_dict(),
            }
            ledger_records.append(
                self.repository.put_ledger_event(
                    mark_key,
                    mark_payload,
                    event_date=execution_date,
                    event_type="NAV_MARK",
                    idempotency_key=mark_key,
                    status="RECORDED",
                    price=nav_after,
                    cost=0.0,
                )
            )
            self.repository.put_portfolio_snapshot(
                self._portfolio_key(request.portfolio_id, execution_date),
                snapshot_payload,
                portfolio_id=request.portfolio_id,
                as_of=as_of,
                status=PAPER_STATUS,
                nav=nav_after,
                cash=cash,
            )
            self.repository.put_run(
                run_key,
                run_payload,
                run_type="paper_shadow",
                started_at=as_of,
                finished_at=as_of,
                status="SIMULATED",
                strategy_version=target.strategy_version,
                data_version=target.implementation_version,
            )
        return PaperDayResult(state, reconciliation, tuple(ledger_records), False)

    def record_manual_skip(self, request: PaperDayInput, reason: str) -> StoredRecord:
        """Record a user skip without changing strategy output or portfolio state."""

        _require(isinstance(request, PaperDayInput), "request must be PaperDayInput")
        reason = _safe_text(reason, "manual_skip.reason")
        self._validate_target_timing(request.target)
        input_hash = _hash({"request": request.as_dict(), "config": self.config.as_dict()})
        event_key = self._ledger_key(request.portfolio_id, request.target.execution_date, 998, "MANUAL_SKIP")
        payload = {
            "schema": PAPER_LEDGER_EVENT_SCHEMA,
            "implementation_version": PAPER_EXECUTION_IMPLEMENTATION_VERSION,
            "event_key": event_key,
            "event_type": "MANUAL_SKIP",
            "portfolio_id": request.portfolio_id,
            "run_id": request.run_id,
            "signal_date": request.target.signal_date,
            "execution_date": request.target.execution_date,
            "strategy_version": request.target.strategy_version,
            "target_snapshot_hash": request.target.content_hash,
            "input_hash": input_hash,
            "reason": reason,
            "data_quality": request.target.data_quality,
            "strategy_output_changed": False,
            "portfolio_changed": False,
        }
        as_of = _iso_timestamp(request.as_of or request.target.as_of, "manual_skip.as_of")
        with self.repository.transaction():
            return self.repository.put_ledger_event(
                event_key,
                payload,
                event_date=request.target.execution_date,
                event_type="MANUAL_SKIP",
                idempotency_key=event_key,
                status="RECORDED",
                cost=0.0,
            )

    def _validate_request(self, request: PaperDayInput) -> None:
        _require(isinstance(request, PaperDayInput), "request must be PaperDayInput")
        target = request.target
        self._validate_target_timing(target)
        _require(target.data_quality == "OK", "paper execution requires an OK target data quality")
        for price in request.prices:
            _require(price.price_basis == self.config.price_basis, "price basis differs from execution config")
            _require(price.session_date == target.execution_date, "all prices must be for target execution_date")
            _require(price.quality == "OK", f"price quality is not OK for {price.symbol}")
            _require(price.symbol in target.target_weights, f"unknown execution symbol: {price.symbol}")

    def _validate_target_timing(self, target: TargetWeightSnapshot) -> None:
        _require(isinstance(target, TargetWeightSnapshot), "target must be a TargetWeightSnapshot")
        _require(target.candidate_only is True and target.execution_eligible is False, "target must remain candidate-only")
        sessions = self.calendar.sessions(target.signal_date, target.execution_date)
        _require(sessions == (target.signal_date, target.execution_date), "target execution_date must be the next trading session")

    def _latest_state(self, portfolio_id: str) -> PaperPortfolioState | None:
        records = self.repository.list("portfolio_snapshot", limit=1000)
        candidates = [record for record in records if record.metadata.get("portfolio_id") == portfolio_id]
        if not candidates:
            return None
        latest = max(candidates, key=lambda record: record.payload.get("execution_date", ""))
        return self._state_from_payload(latest.payload)

    def _replay_existing(self, record: StoredRecord, input_hash: str) -> PaperDayResult:
        existing_hash = record.payload.get("input_hash")
        if existing_hash != input_hash:
            raise StorageConflictError("same paper execution date contains different input")
        state = self._state_from_payload(record.payload)
        return PaperDayResult(state, state.reconciliation, (), True)

    def _state_from_payload(self, payload: Mapping[str, Any]) -> PaperPortfolioState:
        try:
            reconciliation_payload = payload["reconciliation"]
            reconciliation = PaperReconciliation(
                portfolio_id=reconciliation_payload["portfolio_id"],
                signal_date=reconciliation_payload["signal_date"],
                execution_date=reconciliation_payload["execution_date"],
                nav_before=reconciliation_payload["nav_before"],
                nav_after=reconciliation_payload["nav_after"],
                cash=reconciliation_payload["cash"],
                fees=reconciliation_payload["fees"],
                slippage_cost=reconciliation_payload["slippage_cost"],
                turnover=reconciliation_payload["turnover"],
                target_weights=reconciliation_payload["target_weights"],
                actual_weights=reconciliation_payload["actual_weights"],
                weight_errors=reconciliation_payload["weight_errors"],
                trade_count=reconciliation_payload["trade_count"],
                scaled_for_cash=reconciliation_payload["scaled_for_cash"],
                identity_error=reconciliation_payload["identity_error"],
                status=reconciliation_payload["status"],
            )
            return PaperPortfolioState(
                portfolio_id=payload["portfolio_id"],
                run_id=payload["run_id"],
                signal_date=payload["signal_date"],
                execution_date=payload["execution_date"],
                as_of=payload["as_of"],
                strategy_version=payload["strategy_version"],
                strategy_config_hash=payload["strategy_config_hash"],
                target_snapshot_hash=payload["target_snapshot_hash"],
                input_hash=payload["input_hash"],
                initial_cash=payload["initial_cash"],
                cash=payload["cash"],
                positions=payload["positions"],
                nav=payload["nav"],
                fees_paid=payload["fees_paid"],
                slippage_cost=payload["slippage_cost"],
                turnover=payload["turnover"],
                price_basis=payload["price_basis"],
                data_quality=payload["data_quality"],
                status=payload["status"],
                target_weights=payload["target_weights"],
                reconciliation=reconciliation,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PaperPortfolioError("stored paper portfolio snapshot is invalid") from exc

    @staticmethod
    def _portfolio_key(portfolio_id: str, execution_date: str) -> str:
        return f"portfolio|{portfolio_id}|{execution_date}"

    @staticmethod
    def _run_key(portfolio_id: str, execution_date: str) -> str:
        return f"paper-run|{portfolio_id}|{execution_date}"

    @staticmethod
    def _ledger_key(portfolio_id: str, execution_date: str, index: int, event_type: str) -> str:
        return f"ledger|{portfolio_id}|{execution_date}|{index:03d}|{event_type}"


__all__ = [
    "PAPER_EXECUTION_IMPLEMENTATION_VERSION",
    "PAPER_LEDGER_EVENT_SCHEMA",
    "PAPER_PORTFOLIO_SCHEMA",
    "PAPER_RECONCILIATION_SCHEMA",
    "PAPER_STATUS",
    "PaperDayInput",
    "PaperDayResult",
    "PaperExecutionConfig",
    "PaperInputError",
    "PaperPortfolioError",
    "PaperPortfolioService",
    "PaperPortfolioState",
    "PaperPrice",
    "PaperReconciliation",
    "PaperReconciliationError",
]
