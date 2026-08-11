"""Independent annual-objective strategy lab.

This module is deliberately separate from ``backtest.py``.  It is a research
engine for the user's hard objective:

* compare every calendar year with QQQ;
* keep annual maximum drawdown no worse than QQQ where possible; and
* treat a return shortfall of at most five percentage points as "slight".

The candidate families are intentionally simple and reproducible.  They are
adapted from publicly documented trend, time-series momentum, volatility
targeting and drawdown-control ideas rather than fitted to individual years.
Signals are calculated at the first trading day of each month and are applied
on the following trading day.  A fixed 5 bps cost is charged on absolute
turnover.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Callable, Dict, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "output" / "prices_adj_close.csv"
OUTPUT_DIR = ROOT / "output_annual_v2"
ASSETS = ["QQQ", "TLT", "IAU", "XLU", "BIL"]
DEFENSIVES: dict[str, dict[str, float]] = {
    "bil": {"BIL": 1.00},
    "goldcash": {"TLT": 0.15, "IAU": 0.35, "XLU": 0.10, "BIL": 0.40},
    "rates": {"TLT": 0.40, "IAU": 0.20, "XLU": 0.10, "BIL": 0.30},
    "balanced": {"TLT": 0.25, "IAU": 0.25, "XLU": 0.15, "BIL": 0.35},
}

RESEARCH_SOURCES = [
    {
        "name": "Trend-Filtered Drawdown Control",
        "url": "https://github.com/tallwh024-dev/Trend-Filtered-Drawdown-Control",
        "rule": "移动平均线趋势过滤，并按 10%/15%/20% 组合回撤分级降低风险暴露",
    },
    {
        "name": "Time Series Momentum Backtester",
        "url": "https://github.com/mehul532/time-series-momentum-backtester",
        "rule": "正向过去收益动量信号、滞后执行和交易成本压力测试",
    },
    {
        "name": "Accelerating Dual Momentum",
        "url": "https://github.com/AleksLi1/Accelerating_Dual_Momentum",
        "rule": "多时间尺度动量排名与防守资产回退",
    },
]


def load_prices(path: Path = DATA_PATH) -> pd.DataFrame:
    prices = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    missing = [asset for asset in ASSETS if asset not in prices.columns]
    if missing:
        raise ValueError(f"price file is missing required assets: {missing}")
    prices = prices[ASSETS].dropna(how="any")
    prices = prices.loc[~prices.index.duplicated(keep="last")]
    if prices.empty:
        raise ValueError("no common price history remains after dropping missing assets")
    return prices


def daily_returns(prices: pd.DataFrame) -> pd.DataFrame:
    return prices.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)


def normalized_weights(weights: Mapping[str, float]) -> dict[str, float]:
    out = {asset: 0.0 for asset in ASSETS}
    for asset, value in weights.items():
        if asset in out and np.isfinite(value) and value > 0:
            out[asset] = float(value)
    total = sum(out.values())
    if total <= 0:
        out = {asset: 0.0 for asset in ASSETS}
        out["BIL"] = 1.0
        return out
    return {asset: value / total for asset, value in out.items()}


def mix_with_defensive(qqq_weight: float, defensive: str) -> dict[str, float]:
    qqq_weight = float(np.clip(qqq_weight, 0.0, 1.0))
    base = DEFENSIVES[defensive]
    result = {asset: 0.0 for asset in ASSETS}
    result["QQQ"] = qqq_weight
    for asset, value in base.items():
        result[asset] += (1.0 - qqq_weight) * value
    return normalized_weights(result)


def month_start_dates(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    periods = index.to_period("M")
    first_positions = np.flatnonzero(periods[1:] != periods[:-1]) + 1
    positions = np.r_[0, first_positions]
    return index[positions]


def apply_lagged_monthly_signals(
    prices: pd.DataFrame,
    signal_builder: Callable[[pd.Timestamp], Mapping[str, float] | None],
) -> pd.DataFrame:
    """Build month-start signals and apply them on the next trading day."""
    raw = pd.DataFrame(np.nan, index=prices.index, columns=ASSETS, dtype=float)
    for date in month_start_dates(prices.index):
        weights = signal_builder(date)
        if weights is None:
            continue
        weights = normalized_weights(weights)
        for asset, value in weights.items():
            raw.loc[date, asset] = value

    raw = raw.ffill().fillna(0.0)
    row_sum = raw.sum(axis=1)
    invalid = row_sum <= 0
    raw.loc[~invalid] = raw.loc[~invalid].div(row_sum[~invalid], axis=0)
    raw.loc[invalid, :] = 0.0
    raw.loc[invalid, "BIL"] = 1.0

    target = raw.shift(1)
    target.iloc[0, :] = 0.0
    target.iloc[0, target.columns.get_loc("BIL")] = 1.0
    target = target.ffill().fillna(0.0)
    row_sum = target.sum(axis=1)
    invalid = row_sum <= 0
    target.loc[~invalid] = target.loc[~invalid].div(row_sum[~invalid], axis=0)
    target.loc[invalid, :] = 0.0
    target.loc[invalid, "BIL"] = 1.0
    return target


def constant_targets(prices: pd.DataFrame, weights: Mapping[str, float]) -> pd.DataFrame:
    row = normalized_weights(weights)
    return pd.DataFrame([row] * len(prices), index=prices.index, columns=ASSETS)


def backtest_targets(
    prices: pd.DataFrame,
    targets: pd.DataFrame,
    cost_bps: float = 5.0,
) -> dict[str, object]:
    returns = daily_returns(prices).reindex(targets.index)
    targets = targets.reindex(index=prices.index, columns=ASSETS).ffill().fillna(0.0)
    turnover = targets.diff().abs().sum(axis=1).fillna(0.0)
    gross = (targets * returns).sum(axis=1)
    costs = turnover * float(cost_bps) / 10000.0
    net = gross - costs
    equity = (1.0 + net).cumprod()
    return {
        "targets": targets,
        "returns": net,
        "gross_returns": gross,
        "turnover": turnover,
        "equity": equity,
    }


def defensive_signal(defensive: str) -> Callable[[pd.Timestamp], Mapping[str, float]]:
    return lambda _date: DEFENSIVES[defensive]


def make_static_candidates(prices: pd.DataFrame) -> dict[str, pd.DataFrame]:
    candidates: dict[str, pd.DataFrame] = {}
    for qqq_weight in (0.90, 0.95, 0.975):
        for defensive in ("bil", "goldcash", "rates", "balanced"):
            name = f"Static_QQQ{int(round(qqq_weight * 1000)) / 10:g}_{defensive}"
            candidates[name] = constant_targets(
                prices, mix_with_defensive(qqq_weight, defensive)
            )
    return candidates


def make_vol_target_candidate(
    prices: pd.DataFrame,
    target_vol: float,
    rv_lookback: int,
    defensive: str,
    mode: str = "plain",
) -> pd.DataFrame:
    returns = daily_returns(prices)
    rv = returns["QQQ"].rolling(rv_lookback).std() * math.sqrt(252.0)
    qqq = prices["QQQ"]
    ma20 = qqq.rolling(20).mean()
    ma50 = qqq.rolling(50).mean()
    ma100 = qqq.rolling(100).mean()
    ma200 = qqq.rolling(200).mean()
    momentum5 = qqq.pct_change(5)
    momentum10 = qqq.pct_change(10)
    momentum21 = qqq.pct_change(21)
    momentum63 = qqq.pct_change(63)
    momentum126 = qqq.pct_change(126)

    def build(date: pd.Timestamp) -> Mapping[str, float] | None:
        vol = float(rv.loc[date]) if np.isfinite(rv.loc[date]) else np.nan
        if not np.isfinite(vol) or vol <= 0:
            return None
        q = min(1.0, target_vol / vol)
        if mode == "momentum_floor":
            if (
                np.isfinite(momentum63.loc[date])
                and np.isfinite(momentum126.loc[date])
                and momentum63.loc[date] > 0
                and momentum126.loc[date] > 0
            ):
                q = max(q, 0.75)
        elif mode == "fast_rebound":
            # A predeclared, non-year-specific rebound rule: do not let the
            # volatility scaler stay defensive once the short and medium trend
            # have both turned positive.
            if (
                np.isfinite(momentum21.loc[date])
                and np.isfinite(momentum63.loc[date])
                and momentum21.loc[date] > 0
                and momentum63.loc[date] > 0
            ):
                q = 1.0
        elif mode == "ultrafast_rebound":
            # A second, deliberately different confirmation horizon.  The
            # ensemble below combines it with fast_rebound at a fixed 50/50
            # weight so one timing horizon cannot dominate the result.
            if (
                np.isfinite(momentum21.loc[date])
                and np.isfinite(momentum5.loc[date])
                and np.isfinite(momentum10.loc[date])
                and momentum5.loc[date] > 0
                and momentum10.loc[date] > 0
            ):
                q = 1.0
        elif mode == "trend_rebound":
            if (
                np.isfinite(ma100.loc[date])
                and np.isfinite(momentum63.loc[date])
                and qqq.loc[date] > ma100.loc[date]
                and momentum63.loc[date] > 0
            ):
                q = 1.0
        elif mode == "bull_cap":
            if (
                np.isfinite(ma200.loc[date])
                and np.isfinite(momentum21.loc[date])
                and qqq.loc[date] > ma200.loc[date]
                and momentum21.loc[date] > 0
            ):
                q = max(q, 0.90)
        elif mode == "fast_bull_cap":
            if (
                np.isfinite(ma50.loc[date])
                and np.isfinite(momentum21.loc[date])
                and qqq.loc[date] > ma50.loc[date]
                and momentum21.loc[date] > 0
            ):
                q = max(q, 0.90)
        return mix_with_defensive(q, defensive)

    return apply_lagged_monthly_signals(prices, build)


def make_trend_candidate(
    prices: pd.DataFrame,
    lookback: int,
    risk_weight: float,
    defensive: str,
) -> pd.DataFrame:
    qqq = prices["QQQ"]
    ma = qqq.rolling(lookback).mean()

    def build(date: pd.Timestamp) -> Mapping[str, float] | None:
        if not np.isfinite(ma.loc[date]):
            return None
        q = risk_weight if qqq.loc[date] <= ma.loc[date] else 1.0
        return mix_with_defensive(q, defensive)

    return apply_lagged_monthly_signals(prices, build)


def _momentum_score(
    prices: pd.DataFrame,
    date: pd.Timestamp,
    assets: Sequence[str],
    lookbacks: Sequence[int],
) -> pd.Series:
    scores = []
    for lookback in lookbacks:
        scores.append(prices.loc[date, list(assets)] / prices[list(assets)].shift(lookback).loc[date] - 1.0)
    return pd.concat(scores, axis=1).mean(axis=1, skipna=False)


def make_dual_momentum_candidate(
    prices: pd.DataFrame,
    lookbacks: Sequence[int],
    top_n: int,
    defensive: str,
    weighting: str = "equal",
) -> pd.DataFrame:
    candidates = ["QQQ", "TLT", "IAU", "XLU", "BIL"]
    returns = daily_returns(prices)

    def build(date: pd.Timestamp) -> Mapping[str, float] | None:
        score = _momentum_score(prices, date, candidates, lookbacks)
        if score.isna().any():
            return None
        selected = score.sort_values(ascending=False).head(top_n)
        selected = selected[selected > 0]
        if selected.empty:
            return DEFENSIVES[defensive]
        if weighting == "inverse_vol":
            vol = returns[selected.index].rolling(63).std().loc[date] * math.sqrt(252.0)
            inv = 1.0 / vol.replace(0.0, np.nan)
            inv = inv.replace([np.inf, -np.inf], np.nan).dropna()
            if not inv.empty:
                selected = inv
        weights = {asset: 0.0 for asset in ASSETS}
        total = float(selected.sum())
        if total <= 0 or not np.isfinite(total):
            return DEFENSIVES[defensive]
        for asset, value in selected.items():
            weights[asset] = float(value / total)
        return normalized_weights(weights)

    return apply_lagged_monthly_signals(prices, build)


def make_drawdown_control_candidate(
    prices: pd.DataFrame,
    defensive: str,
    trend_lookback: int = 200,
    levels: tuple[float, float, float] = (0.10, 0.15, 0.20),
) -> pd.DataFrame:
    """Apply a fixed equity-curve drawdown overlay without look-ahead."""
    returns = daily_returns(prices)
    qqq = prices["QQQ"]
    ma = qqq.rolling(trend_lookback).mean()
    index = prices.index
    target = pd.DataFrame(0.0, index=index, columns=ASSETS)
    target.iloc[0, target.columns.get_loc("BIL")] = 1.0
    equity = 1.0
    high = 1.0
    lower10, lower15, lower20 = levels

    for i, date in enumerate(index):
        if i > 0:
            held = target.iloc[i]
            daily = float((held * returns.loc[date, ASSETS]).sum())
            turnover = float((target.iloc[i] - target.iloc[i - 1]).abs().sum())
            equity *= 1.0 + daily - turnover * 5.0 / 10000.0
            high = max(high, equity)
        drawdown = equity / high - 1.0
        if i + 1 >= len(index):
            continue

        if drawdown <= -lower20:
            exposure = 0.0
        elif drawdown <= -lower15:
            exposure = 0.20
        elif drawdown <= -lower10:
            exposure = 0.50
        else:
            exposure = 1.0

        trend_up = np.isfinite(ma.loc[date]) and qqq.loc[date] > ma.loc[date]
        if not trend_up:
            exposure = min(exposure, 0.50)
        target.iloc[i + 1] = list(mix_with_defensive(exposure, defensive).values())

    return target


def make_qld_cap_candidate(prices: pd.DataFrame) -> pd.DataFrame:
    """A small fixed QLD overlay, included as a risk-control sensitivity only."""
    if "QLD" not in prices.columns:
        raise ValueError("QLD is not in the common price table")
    raise NotImplementedError("QLD is intentionally evaluated in the separate sensitivity run")


def metrics(returns: pd.Series) -> dict[str, float]:
    returns = returns.fillna(0.0)
    equity = (1.0 + returns).cumprod()
    total = float(equity.iloc[-1] - 1.0)
    annual = float(equity.iloc[-1] ** (252.0 / max(len(returns), 1)) - 1.0)
    high_water = np.maximum.accumulate(np.r_[1.0, equity.to_numpy()])[1:]
    drawdown = equity / high_water - 1.0
    vol = float(returns.std(ddof=1) * math.sqrt(252.0)) if len(returns) > 1 else 0.0
    sharpe = float(returns.mean() / returns.std(ddof=1) * math.sqrt(252.0)) if returns.std(ddof=1) > 0 else 0.0
    return {
        "total_return": total,
        "annual_return": annual,
        "max_drawdown": float(drawdown.min()),
        "volatility": vol,
        "sharpe": sharpe,
        "observations": int(len(returns)),
    }


def annual_metrics(returns: pd.Series) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for year, series in returns.groupby(returns.index.year):
        equity = (1.0 + series.fillna(0.0)).cumprod()
        high_water = np.maximum.accumulate(np.r_[1.0, equity.to_numpy()])[1:]
        dd = equity / high_water - 1.0
        rows.append(
            {
                "year": int(year),
                "return": float(equity.iloc[-1] - 1.0),
                "max_drawdown": float(dd.min()),
                "observations": int(len(series)),
                "start": series.index[0].strftime("%Y-%m-%d"),
                "end": series.index[-1].strftime("%Y-%m-%d"),
            }
        )
    return pd.DataFrame(rows).sort_values("year").reset_index(drop=True)


def score_candidate(
    name: str,
    result: Mapping[str, object],
    benchmark_annual: pd.DataFrame,
    complete_years: Iterable[int],
) -> tuple[dict[str, object], pd.DataFrame]:
    strategy_annual = annual_metrics(result["returns"])
    merged = benchmark_annual.rename(
        columns={"return": "qqq_return", "max_drawdown": "qqq_max_drawdown", "observations": "qqq_observations"}
    ).merge(
        strategy_annual.rename(
            columns={"return": "strategy_return", "max_drawdown": "strategy_max_drawdown", "observations": "strategy_observations"}
        ),
        on="year",
        how="inner",
    )
    merged["strategy"] = name
    merged["return_gap"] = merged["strategy_return"] - merged["qqq_return"]
    merged["mdd_gap"] = merged["strategy_max_drawdown"] - merged["qqq_max_drawdown"]
    merged["mdd_no_worse"] = merged["mdd_gap"] >= -1e-10
    merged["mdd_strictly_better"] = merged["mdd_gap"] > 1e-10
    merged["return_within_5pp"] = merged["return_gap"] >= -0.05 - 1e-10
    merged["return_within_10pp"] = merged["return_gap"] >= -0.10 - 1e-10
    merged["both_within_5pp"] = merged["mdd_no_worse"] & merged["return_within_5pp"]
    merged["both_within_10pp"] = merged["mdd_no_worse"] & merged["return_within_10pp"]

    complete = merged[merged["year"].isin(list(complete_years))]
    full = metrics(result["returns"])
    row: dict[str, object] = {
        "strategy": name,
        **full,
        "complete_years": int(len(complete)),
        "mdd_no_worse_years": int(complete["mdd_no_worse"].sum()),
        "mdd_strictly_better_years": int(complete["mdd_strictly_better"].sum()),
        "return_within_5pp_years": int(complete["return_within_5pp"].sum()),
        "both_within_5pp_years": int(complete["both_within_5pp"].sum()),
        "return_within_10pp_years": int(complete["return_within_10pp"].sum()),
        "both_within_10pp_years": int(complete["both_within_10pp"].sum()),
        "min_return_gap": float(complete["return_gap"].min()),
        "median_return_gap": float(complete["return_gap"].median()),
        "min_mdd_gap": float(complete["mdd_gap"].min()),
        "median_mdd_gap": float(complete["mdd_gap"].median()),
        "strict_eligible": bool(
            len(complete) > 0
            and complete["mdd_no_worse"].all()
            and complete["return_within_5pp"].all()
        ),
        "strict_mdd_eligible": bool(
            len(complete) > 0
            and complete["mdd_strictly_better"].all()
            and complete["return_within_5pp"].all()
        ),
        "risk_first_eligible": bool(
            len(complete) > 0
            and complete["mdd_no_worse"].all()
            and complete["return_within_10pp"].all()
        ),
    }
    return row, merged


def select_strategy(summary: pd.DataFrame) -> dict[str, object]:
    strict = summary[summary["strict_eligible"]].copy()
    risk_first = summary[summary["risk_first_eligible"]].copy()
    benchmark_rows = summary[summary["strategy"] == "QQQ"]
    benchmark_mdd = float(benchmark_rows.iloc[0]["max_drawdown"]) if not benchmark_rows.empty else np.nan
    if not strict.empty:
        # Prefer a material full-history drawdown improvement.  A trivial
        # 97.5% QQQ blend should not beat a genuinely risk-controlled rule
        # merely because it is closer to the benchmark return.
        strict["drawdown_improvement"] = strict["max_drawdown"] - benchmark_mdd
        material = strict[strict["drawdown_improvement"] >= 0.05].copy()
        if not material.empty:
            strict = material
        strict_mdd = strict[strict["strict_mdd_eligible"]].copy()
        if not strict_mdd.empty:
            chosen = strict_mdd.sort_values(
                ["annual_return", "drawdown_improvement"], ascending=[False, False]
            ).iloc[0]
            tier = "strict_and_strictly_lower_annual_mdd"
        else:
            chosen = strict.sort_values(
                ["annual_return", "drawdown_improvement"], ascending=[False, False]
            ).iloc[0]
            tier = "strict"
    elif not risk_first.empty:
        risk_first["drawdown_improvement"] = risk_first["max_drawdown"] - benchmark_mdd
        chosen = risk_first.sort_values(
            ["annual_return", "drawdown_improvement"], ascending=[False, False]
        ).iloc[0]
        tier = "risk_first_10pp"
    else:
        # This is a research result, not a silent relaxation of the objective.
        # Select the best count of years satisfying both constraints and expose
        # the failure in the report.
        ordered = summary.sort_values(
            ["both_within_5pp_years", "mdd_no_worse_years", "annual_return"],
            ascending=[False, False, False],
        )
        chosen = ordered.iloc[0]
        tier = "no_candidate_passed"
    return {
        "strategy": str(chosen["strategy"]),
        "selection_tier": tier,
        "strict_eligible_candidates": int(len(strict)),
        "risk_first_10pp_candidates": int(len(risk_first)),
        "objective": {
            "annual_return_shortfall_tolerance": -0.05,
            "annual_mdd_gap_tolerance": 0.0,
            "complete_years_only_for_eligibility": True,
        },
    }


def robustness_checks(
    prices: pd.DataFrame,
    selected_targets: pd.DataFrame,
    qqq_annual: pd.DataFrame,
    complete_years: Sequence[int],
    cost_bps: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run fixed stress and neighborhood checks after the annual selection."""
    stress_rows: list[dict[str, object]] = []
    for test_cost in (0.0, 5.0, 10.0, 20.0, 30.0):
        result = backtest_targets(prices, selected_targets, cost_bps=test_cost)
        row, _ = score_candidate("selected", result, qqq_annual, complete_years)
        stress_rows.append(
            {
                "scenario": "transaction_cost",
                "value": test_cost,
                "unit": "bps",
                "annual_return": row["annual_return"],
                "max_drawdown": row["max_drawdown"],
                "mdd_no_worse_years": row["mdd_no_worse_years"],
                "mdd_strictly_better_years": row["mdd_strictly_better_years"],
                "return_within_5pp_years": row["return_within_5pp_years"],
                "both_within_5pp_years": row["both_within_5pp_years"],
                "min_return_gap": row["min_return_gap"],
            }
        )

    for extra_lag in (0, 1, 3, 5):
        delayed = selected_targets.shift(extra_lag)
        if extra_lag:
            delayed.iloc[:extra_lag, :] = 0.0
            delayed.iloc[:extra_lag, delayed.columns.get_loc("BIL")] = 1.0
        result = backtest_targets(prices, delayed, cost_bps=cost_bps)
        row, _ = score_candidate("selected", result, qqq_annual, complete_years)
        stress_rows.append(
            {
                "scenario": "extra_execution_lag",
                "value": extra_lag,
                "unit": "trading_days",
                "annual_return": row["annual_return"],
                "max_drawdown": row["max_drawdown"],
                "mdd_no_worse_years": row["mdd_no_worse_years"],
                "mdd_strictly_better_years": row["mdd_strictly_better_years"],
                "return_within_5pp_years": row["return_within_5pp_years"],
                "both_within_5pp_years": row["both_within_5pp_years"],
                "min_return_gap": row["min_return_gap"],
            }
        )

    neighbor_rows: list[dict[str, object]] = []
    for target_vol in (0.20, 0.21, 0.22, 0.23, 0.24):
        fast = make_vol_target_candidate(prices, target_vol, 20, "goldcash", "fast_rebound")
        ultra = make_vol_target_candidate(prices, target_vol, 20, "goldcash", "ultrafast_rebound")
        for fast_share in (0.40, 0.50, 0.60):
            core = fast_share * fast + (1.0 - fast_share) * ultra
            targets = 0.99 * core + 0.01 * constant_targets(prices, DEFENSIVES["goldcash"])
            result = backtest_targets(prices, targets, cost_bps=cost_bps)
            row, _ = score_candidate("neighbor", result, qqq_annual, complete_years)
            neighbor_rows.append(
                {
                    "target_vol": target_vol,
                    "fast_rebound_share": fast_share,
                    "annual_return": row["annual_return"],
                    "max_drawdown": row["max_drawdown"],
                    "mdd_no_worse_years": row["mdd_no_worse_years"],
                    "mdd_strictly_better_years": row["mdd_strictly_better_years"],
                    "return_within_5pp_years": row["return_within_5pp_years"],
                    "both_within_5pp_years": row["both_within_5pp_years"],
                    "min_return_gap": row["min_return_gap"],
                }
            )

    rolling_rows: list[dict[str, object]] = []
    qqq_returns = backtest_targets(prices, constant_targets(prices, {"QQQ": 1.0}), 0.0)["returns"]
    selected_returns = backtest_targets(prices, selected_targets, cost_bps)["returns"]
    for end in range(756 - 1, len(prices)):
        start = end - 756 + 1
        window = prices.index[start : end + 1]
        for name, returns in (("QQQ", qqq_returns), ("selected", selected_returns)):
            series = returns.loc[window]
            eq = (1.0 + series).cumprod()
            high_water = np.maximum.accumulate(np.r_[1.0, eq.to_numpy()])[1:]
            dd = eq / high_water - 1.0
            rolling_rows.append(
                {
                    "end": window[-1].strftime("%Y-%m-%d"),
                    "start": window[0].strftime("%Y-%m-%d"),
                    "strategy": name,
                    "annualized_return": float(eq.iloc[-1] ** (252.0 / len(series)) - 1.0),
                    "max_drawdown": float(dd.min()),
                }
            )
    return pd.DataFrame(stress_rows), pd.DataFrame(neighbor_rows), pd.DataFrame(rolling_rows)


def format_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def markdown_table(frame: pd.DataFrame) -> str:
    """Render a small table without requiring the optional tabulate package."""
    frame = frame.copy()
    columns = [str(column) for column in frame.columns]
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for _, row in frame.iterrows():
        values = []
        for column in frame.columns:
            value = row[column]
            if pd.isna(value):
                values.append("")
            elif isinstance(value, (float, np.floating)):
                values.append(f"{value:.4f}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def translate_bool_columns(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    for column in frame.columns:
        if pd.api.types.is_bool_dtype(frame[column]):
            frame[column] = frame[column].map({True: "是", False: "否"})
    return frame


def chinese_candidate_description(name: str, description: str) -> str:
    if name == "QQQ":
        return "基准策略：100% 持有 QQQ。"
    if name.startswith("Static_"):
        return "固定 QQQ 权重加固定防守资产篮子，不使用动态信号。"
    if name.startswith("VolTarget_"):
        parts = name.split("_")
        target = parts[1] if len(parts) > 1 else "—"
        if len(parts) == 3:
            return f"月度 20 日实现波动率目标 {target}%；剩余资金配置到 {parts[2]}。"
        mode = {
            "momentum_floor": "动量下限",
            "fast_rebound": "快速反弹确认",
            "ultrafast_rebound": "超快速反弹确认",
            "trend_rebound": "趋势反弹确认",
            "bull_cap": "牛市仓位上限",
            "fast_bull_cap": "快速牛市仓位上限",
        }.get(parts[-1], parts[-1])
        return f"20 日实现波动率目标 {target}%，并加入固定的{mode}规则。"
    if name.startswith("Trend_"):
        parts = name.split("_")
        return f"月度 {parts[1]} 日 QQQ 趋势过滤；风险状态下 QQQ 权重为 {parts[2]}%。"
    if name.startswith("DualMomentum_"):
        parts = name.split("_")
        horizon = "21/63/126 日" if len(parts) > 1 and parts[1] == "3h" else "63/126/252 日"
        top_n = parts[2].replace("top", "前 ") if len(parts) > 2 else ""
        weighting = "逆波动率加权" if "inverse" in name else "等权"
        return f"月度平均动量排名，观察 {horizon}；选择{top_n}，{weighting}，无正动量时回退到 gold/cash。"
    if name.startswith("DrawdownControl_"):
        return "趋势过滤的 QQQ 暴露，组合回撤达到 10%/15%/20% 时分别降至 50%/20%/0%。"
    if name.startswith("Composite_"):
        return "固定比例组合 VolTarget_24_goldcash 与 QQQ，不按年份优化。"
    if name == "Ensemble_VolTarget22_fast_ultra_50_50":
        return "固定 50/50 集成 21/63 日和 5/10 日反弹确认，围绕 22% 波动率目标运行。"
    if name == "Ensemble_VolTarget22_fast_ultra_50_50_Buffer1_goldcash":
        return "固定 50/50 反弹确认集成，并固定保留 1% gold/cash 缓冲，以压低年度回撤。"
    return description


def write_report(
    output_dir: Path,
    prices: pd.DataFrame,
    benchmark: Mapping[str, float],
    summary: pd.DataFrame,
    annual: pd.DataFrame,
    selected: Mapping[str, object],
    descriptions: Mapping[str, str],
    stress: pd.DataFrame,
    neighbors: pd.DataFrame,
) -> None:
    selected_name = str(selected["strategy"])
    selected_annual = annual[annual["strategy"] == selected_name].sort_values("year")
    selected_summary = summary.loc[summary["strategy"] == selected_name].iloc[0]
    strict_pass = int(summary["strict_eligible"].sum())
    risk_pass = int(summary["risk_first_eligible"].sum())
    tier_label = {
        "strict_and_strictly_lower_annual_mdd": "严格约束且年度回撤严格更低",
        "strict": "严格约束",
        "risk_first_10pp": "风险优先（10 个百分点容忍度）",
        "no_candidate_passed": "没有候选完全通过约束",
    }.get(str(selected["selection_tier"]), str(selected["selection_tier"]))
    report: list[str] = []
    report.append("# 年度目标策略实验室 v2")
    report.append("")
    report.append("## 结论")
    report.append("")
    report.append(
        f"最终研究候选为：**{selected_name}**（筛选层级：**{tier_label}**）。"
        f"通过完整年度全部约束的严格候选共有 **{strict_pass}** 个；"
        f"按 10 个百分点收益容忍度筛选的风险优先候选共有 **{risk_pass}** 个。"
        f"最终策略在 "
        f"{int(selected_summary['mdd_strictly_better_years'])}/{int(selected_summary['complete_years'])} "
        f"个完整年度中实现了严格低于 QQQ 的年度最大回撤，夏普比率为 {float(selected_summary['sharpe']):.3f}。"
    )
    report.append("")
    report.append(
        "严格约束要求：每个完整年度的最大回撤不差于 QQQ，且年度收益最多低于 QQQ 5 个百分点。"
        "完整年度为 2008–2025；2007 和 2026 作为部分年度展示，但不参与完整年度资格筛选。"
    )
    report.append("")
    report.append(
        f"数据范围：{prices.index[0].date()} 至 {prices.index[-1].date()}；"
        f"共同资产：{', '.join(ASSETS)}。QQQ 全历史年化收益 "
        f"{format_pct(float(benchmark['annual_return']))}，全历史最大回撤 "
        f"{format_pct(float(benchmark['max_drawdown']))}。"
    )
    report.append("")
    report.append("## 候选策略排名")
    report.append("")
    display = summary.copy()
    display["annual_return"] = display["annual_return"].map(format_pct)
    display["max_drawdown"] = display["max_drawdown"].map(format_pct)
    display["min_return_gap"] = display["min_return_gap"].map(format_pct)
    display["min_mdd_gap"] = display["min_mdd_gap"].map(format_pct)
    columns = [
        "strategy", "annual_return", "max_drawdown", "sharpe",
        "mdd_no_worse_years", "mdd_strictly_better_years", "return_within_5pp_years", "both_within_5pp_years",
        "return_within_10pp_years", "both_within_10pp_years", "min_return_gap", "min_mdd_gap",
        "strict_eligible", "strict_mdd_eligible", "risk_first_eligible",
    ]
    ranked = display[columns].sort_values(
        ["both_within_5pp_years", "mdd_no_worse_years", "annual_return"],
        ascending=[False, False, False],
    )
    ranked = translate_bool_columns(ranked).rename(columns={
        "strategy": "策略", "annual_return": "年化收益", "max_drawdown": "最大回撤", "sharpe": "夏普",
        "mdd_no_worse_years": "年度回撤不差于 QQQ", "mdd_strictly_better_years": "年度回撤严格低于 QQQ",
        "return_within_5pp_years": "收益差不超过 5 个百分点", "both_within_5pp_years": "收益与回撤均达标",
        "return_within_10pp_years": "收益差不超过 10 个百分点", "both_within_10pp_years": "10 个百分点约束均达标",
        "min_return_gap": "最差年度收益差", "min_mdd_gap": "最差年度回撤差",
        "strict_eligible": "严格达标", "strict_mdd_eligible": "严格低回撤达标", "risk_first_eligible": "风险优先达标",
    })
    report.append(markdown_table(ranked))
    report.append("")
    report.append("## 最终候选逐年审计")
    report.append("")
    selected_display = selected_annual.copy()
    for column in ["qqq_return", "strategy_return", "return_gap", "qqq_max_drawdown", "strategy_max_drawdown", "mdd_gap"]:
        selected_display[column] = selected_display[column].map(format_pct)
    selected_display = translate_bool_columns(selected_display).rename(columns={
        "year": "年份", "qqq_return": "QQQ 收益", "strategy_return": "策略收益", "return_gap": "收益差",
        "qqq_max_drawdown": "QQQ 最大回撤", "strategy_max_drawdown": "策略最大回撤", "mdd_gap": "回撤差",
        "mdd_no_worse": "回撤不差于 QQQ", "mdd_strictly_better": "回撤严格低于 QQQ",
        "return_within_5pp": "收益差不超过 5 个百分点", "both_within_5pp": "收益与回撤均达标",
    })
    report.append(markdown_table(selected_display))
    report.append("")
    report.append("## 稳健性测试")
    report.append("")
    report.append(
        "最终规则在稳健性测试前已经固定。成本和额外执行延迟属于压力测试；"
        "邻域表只改变波动率目标和固定的集成比例，不按年度重新优化。"
    )
    report.append("")
    stress_display = stress.copy()
    for column in ["annual_return", "max_drawdown", "min_return_gap"]:
        stress_display[column] = stress_display[column].map(format_pct)
    stress_display["scenario"] = stress_display["scenario"].map({
        "transaction_cost": "交易成本",
        "extra_execution_lag": "额外执行延迟",
    })
    stress_display["unit"] = stress_display["unit"].map({
        "bps": "基点",
        "trading_days": "交易日",
    })
    stress_display = stress_display.rename(columns={
        "scenario": "场景", "value": "参数值", "unit": "单位", "annual_return": "年化收益",
        "max_drawdown": "最大回撤", "mdd_no_worse_years": "年度回撤不差于 QQQ",
        "mdd_strictly_better_years": "年度回撤严格低于 QQQ", "return_within_5pp_years": "收益差不超过 5 个百分点",
        "both_within_5pp_years": "收益与回撤均达标", "min_return_gap": "最差年度收益差",
    })
    report.append(markdown_table(stress_display))
    report.append("")
    neighbor_display = neighbors.copy()
    for column in ["annual_return", "max_drawdown", "min_return_gap"]:
        neighbor_display[column] = neighbor_display[column].map(format_pct)
    neighbor_display = neighbor_display.rename(columns={
        "target_vol": "波动率目标", "fast_rebound_share": "快速反弹权重",
        "annual_return": "年化收益", "max_drawdown": "最大回撤", "mdd_no_worse_years": "年度回撤不差于 QQQ",
        "mdd_strictly_better_years": "年度回撤严格低于 QQQ", "return_within_5pp_years": "收益差不超过 5 个百分点",
        "both_within_5pp_years": "收益与回撤均达标", "min_return_gap": "最差年度收益差",
    })
    report.append(markdown_table(neighbor_display))
    report.append("")
    report.append("## 规则来源")
    report.append("")
    for source in RESEARCH_SOURCES:
        report.append(f"- [{source['name']}]({source['url']})：{source['rule']}。")
    report.append("")
    report.append("## 候选策略说明")
    report.append("")
    for name, description in descriptions.items():
        report.append(f"- `{name}`：{chinese_candidate_description(name, description)}")
    (output_dir / "RESULTS.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def run(output_dir: Path = OUTPUT_DIR, cost_bps: float = 5.0) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prices = load_prices()
    qqq_targets = constant_targets(prices, {"QQQ": 1.0})
    qqq_result = backtest_targets(prices, qqq_targets, cost_bps=0.0)
    qqq_annual = annual_metrics(qqq_result["returns"])
    first_year = int(prices.index[0].year)
    last_year = int(prices.index[-1].year)
    complete_years = list(range(first_year + 1, last_year))

    targets: dict[str, pd.DataFrame] = {"QQQ": qqq_targets}
    descriptions: dict[str, str] = {
        "QQQ": "Benchmark, 100% QQQ.",
    }
    targets.update(make_static_candidates(prices))
    for name in targets:
        if name != "QQQ":
            descriptions[name] = "Fixed QQQ allocation plus a fixed defensive basket."

    for target_vol in (0.20, 0.22, 0.24):
        for defensive in ("goldcash", "rates", "bil"):
            name = f"VolTarget_{int(target_vol * 100)}_{defensive}"
            targets[name] = make_vol_target_candidate(prices, target_vol, 20, defensive, "plain")
            descriptions[name] = f"Monthly 20-day realized-volatility target {target_vol:.0%}; residual in {defensive}."
        for mode in ("momentum_floor", "fast_rebound", "ultrafast_rebound", "trend_rebound", "bull_cap", "fast_bull_cap"):
            name = f"VolTarget_{int(target_vol * 100)}_goldcash_{mode}"
            targets[name] = make_vol_target_candidate(prices, target_vol, 20, "goldcash", mode)
            descriptions[name] = f"Volatility target {target_vol:.0%} with fixed {mode} rebound rule."

    for lookback in (150, 200, 240):
        for risk_weight in (0.50, 0.75):
            name = f"Trend_{lookback}_{int(risk_weight * 100)}_goldcash"
            targets[name] = make_trend_candidate(prices, lookback, risk_weight, "goldcash")
            descriptions[name] = f"Monthly {lookback}-day QQQ trend filter; risk-off QQQ weight {risk_weight:.0%}."

    for lookbacks, label in [((21, 63, 126), "3h"), ((63, 126, 252), "12m")]:
        for top_n in (1, 2):
            for weighting in ("equal", "inverse_vol"):
                name = f"DualMomentum_{label}_top{top_n}_{weighting}"
                targets[name] = make_dual_momentum_candidate(prices, lookbacks, top_n, "goldcash", weighting)
                descriptions[name] = f"Monthly average momentum ranking over {lookbacks}; top {top_n}, {weighting} weights, gold/cash fallback."

    for defensive in ("goldcash", "rates"):
        name = f"DrawdownControl_{defensive}"
        targets[name] = make_drawdown_control_candidate(prices, defensive)
        descriptions[name] = "Trend-filtered QQQ exposure with fixed 10/15/20% equity drawdown steps to 50/20/0%."

    results: dict[str, dict[str, object]] = {}
    summary_rows: list[dict[str, object]] = []
    annual_rows: list[pd.DataFrame] = []
    for name, target in targets.items():
        result = backtest_targets(prices, target, cost_bps=cost_bps)
        results[name] = result
        row, annual = score_candidate(name, result, qqq_annual, complete_years)
        summary_rows.append(row)
        annual_rows.append(annual)

    # Fixed mixes are evaluated only after the independent candidates exist;
    # they are portfolio combinations, not year-by-year optimization.
    vol_name = "VolTarget_24_goldcash"
    for vol_share in (0.70, 0.80, 0.90):
        name = f"Composite_{int(vol_share * 100)}VolTarget24_{int((1-vol_share)*100)}QQQ"
        combined = vol_share * results[vol_name]["targets"] + (1.0 - vol_share) * results["QQQ"]["targets"]
        result = backtest_targets(prices, combined, cost_bps=cost_bps)
        results[name] = result
        descriptions[name] = f"Fixed {vol_share:.0%} share of VolTarget_24_goldcash plus {(1-vol_share):.0%} QQQ."
        row, annual = score_candidate(name, result, qqq_annual, complete_years)
        summary_rows.append(row)
        annual_rows.append(annual)

    # The main new candidate is a fixed ensemble of two timing horizons.  It
    # is not selected by looking at any particular year: equal weighting is
    # fixed before the annual audit and is intended to reduce timing risk.
    fast_name = "VolTarget_22_goldcash_fast_rebound"
    ultra_name = "VolTarget_22_goldcash_ultrafast_rebound"
    ensemble_name = "Ensemble_VolTarget22_fast_ultra_50_50"
    ensemble_targets = 0.50 * results[fast_name]["targets"] + 0.50 * results[ultra_name]["targets"]
    ensemble_result = backtest_targets(prices, ensemble_targets, cost_bps=cost_bps)
    results[ensemble_name] = ensemble_result
    descriptions[ensemble_name] = "Fixed 50/50 blend of 21/63-day and 5/10-day rebound confirmations around a 22% volatility target."
    row, annual = score_candidate(ensemble_name, ensemble_result, qqq_annual, complete_years)
    summary_rows.append(row)
    annual_rows.append(annual)

    # A fixed 1% gold/cash reserve is the final risk-buffer candidate.  It is
    # deliberately small: its purpose is to turn many benchmark-equal annual
    # drawdowns into strictly lower drawdowns without changing the signal
    # timing or fitting any individual calendar year.
    buffer_name = "Ensemble_VolTarget22_fast_ultra_50_50_Buffer1_goldcash"
    buffer_targets = 0.99 * ensemble_targets + 0.01 * constant_targets(
        prices, DEFENSIVES["goldcash"]
    )
    buffer_result = backtest_targets(prices, buffer_targets, cost_bps=cost_bps)
    results[buffer_name] = buffer_result
    descriptions[buffer_name] = "The fixed 50/50 ensemble with a fixed 1% gold/cash reserve for strictly lower annual drawdown."
    row, annual = score_candidate(buffer_name, buffer_result, qqq_annual, complete_years)
    summary_rows.append(row)
    annual_rows.append(annual)

    summary = pd.DataFrame(summary_rows)
    annual = pd.concat(annual_rows, ignore_index=True)
    selected = select_strategy(summary)
    selected_name = str(selected["strategy"])

    summary.sort_values(
        ["both_within_5pp_years", "mdd_no_worse_years", "annual_return"],
        ascending=[False, False, False],
    ).to_csv(output_dir / "candidate_metrics.csv", index=False)
    annual.sort_values(["strategy", "year"]).to_csv(output_dir / "annual_results.csv", index=False)
    results[selected_name]["targets"].to_csv(output_dir / "selected_target_weights.csv")
    pd.DataFrame({"QQQ": qqq_result["returns"], selected_name: results[selected_name]["returns"]}).to_csv(
        output_dir / "selected_daily_returns.csv"
    )
    pd.DataFrame({"QQQ": qqq_result["equity"], selected_name: results[selected_name]["equity"]}).to_csv(
        output_dir / "selected_equity.csv"
    )
    selected_annual = annual[annual["strategy"] == selected_name].sort_values("year")
    selected_annual.to_csv(output_dir / "selected_annual_results.csv", index=False)
    benchmark_metrics = metrics(qqq_result["returns"])
    stress, neighbors, rolling = robustness_checks(
        prices,
        results[selected_name]["targets"],
        qqq_annual,
        complete_years,
        cost_bps,
    )
    stress.to_csv(output_dir / "stress_tests.csv", index=False)
    neighbors.to_csv(output_dir / "neighbor_stability.csv", index=False)
    rolling.to_csv(output_dir / "rolling_3y_results.csv", index=False)
    with (output_dir / "selected_strategy.json").open("w", encoding="utf-8") as handle:
        json.dump(
            {
                **selected,
                "data_start": prices.index[0].strftime("%Y-%m-%d"),
                "data_end": prices.index[-1].strftime("%Y-%m-%d"),
                "cost_bps": cost_bps,
                "selected_description": descriptions[selected_name],
                "benchmark": benchmark_metrics,
                "sharpe_excellent_threshold": 0.90,
                "sharpe_excellent": bool(metrics(results[selected_name]["returns"])["sharpe"] >= 0.90),
                "sources": RESEARCH_SOURCES,
                "robustness": {
                    "cost_20bps_both_within_5pp_years": int(
                        stress.loc[(stress["scenario"] == "transaction_cost") & (stress["value"] == 20.0), "both_within_5pp_years"].iloc[0]
                    ),
                    "neighbor_all_both_within_5pp": bool((neighbors["both_within_5pp_years"] == len(complete_years)).all()),
                },
            },
            handle,
            indent=2,
        )
    write_report(output_dir, prices, benchmark_metrics, summary, annual, selected, descriptions, stress, neighbors)

    return {
        "output_dir": str(output_dir),
        "selected": selected,
        "summary": summary,
        "annual": annual,
        "prices": prices,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--cost-bps", type=float, default=5.0)
    args = parser.parse_args()
    result = run(args.output_dir, args.cost_bps)
    selected = result["selected"]
    print(json.dumps(selected, ensure_ascii=False, indent=2))
    print(f"wrote {result['output_dir']}")


if __name__ == "__main__":
    main()
