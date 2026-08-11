"""Long-bear-market overlay research lab.

This module starts from the frozen v1 target weights and adds one independent
daily overlay for prolonged equity bear markets.  It is intentionally not a
rewrite of ``annual_strategy_lab.py`` and never writes to ``output_annual_v2``.

The overlay is deliberately small and auditable:

* enter only when EMA trend, 126-day momentum, RSI, OBV and VIX agree on
  prolonged weakness;
* cap the existing QQQ weight at 80% in a normal risk-off state;
* cap it at 40% when VIX is above 30 or VIX is above VIX3M;
* move the removed QQQ weight to the v1 gold/cash defensive basket; and
* re-enter when QQQ is above its 50-day average and 21-day momentum is
  positive.

Signals are calculated after a day's close and applied to the next trading
day.  The complete-year objective is evaluated on 2008--2025; 2007 and 2026
are retained as partial-year diagnostics only.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

try:  # import works both from the package directory and from the repo root
    from . import annual_strategy_lab as annual
except ImportError:  # pragma: no cover - exercised when run as a script
    import annual_strategy_lab as annual
try:
    from . import backtest as data_loader
except ImportError:  # pragma: no cover - exercised when run as a script
    import backtest as data_loader


ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "output" / "prices_adj_close.csv"
VIX_PATH = ROOT / "output" / "vix_indices.csv"
BASE_TARGET_PATH = ROOT / "output_annual_v2" / "selected_target_weights.csv"
OUTPUT_DIR = ROOT / "output_long_bear_v2"
ASSETS = annual.ASSETS
DEFENSIVE = "goldcash"
STRATEGY_NAME = "LongBear_EMA_RSI_OBV_VIX_multi_indicator"

PARAMETERS: dict[str, object] = {
    "entry_vix": 20.0,
    "severe_vix": 30.0,
    "normal_qqq_cap": 0.75,
    "severe_qqq_cap": 0.30,
    "entry_confirm_days": 1,
    "ema_fast_days": 50,
    "ema_slow_days": 200,
    "exit_ma_days": 50,
    "exit_momentum_days": 21,
    "exit_confirm_days": 1,
    "rsi_period": 14,
    "rsi_entry_threshold": 50.0,
    "obv_ema_days": 20,
    "exit_indicator": "obv",
    "defensive_basket": DEFENSIVE,
    "cost_bps": 5.0,
}


def load_vix(path: Path = VIX_PATH, index: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    """Load and align VIX/VIX3M without backfilling future observations."""
    frame = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    required = ["VIX", "VIX3M"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"VIX file is missing required columns: {missing}")
    frame = frame[required].replace([np.inf, -np.inf], np.nan)
    if index is not None:
        frame = frame.reindex(index).ffill()
        if frame["VIX"].isna().any():
            raise ValueError("VIX has missing observations at the beginning of the price history")
    return frame


def load_volume(
    prices: pd.DataFrame,
    output_dir: Path = OUTPUT_DIR,
    refresh: bool = False,
) -> pd.Series:
    """Load QQQ volume, downloading it once when the v2 snapshot lacks it."""
    path = output_dir / "qqq_volume.csv"
    if path.exists() and not refresh:
        frame = pd.read_csv(path, parse_dates=["date"]).set_index("date")
        volume = pd.to_numeric(frame["volume"], errors="coerce")
    else:
        raw = data_loader.fetch_yahoo(
            "QQQ",
            prices.index[0].strftime("%Y-%m-%d"),
            prices.index[-1].strftime("%Y-%m-%d"),
        )
        volume = pd.to_numeric(raw["volume"], errors="coerce")
        path.parent.mkdir(parents=True, exist_ok=True)
        volume.rename("volume").rename_axis("date").to_csv(path)
    volume = volume.reindex(prices.index).ffill()
    if volume.isna().any() or (volume <= 0).any():
        raise ValueError("QQQ volume has missing or non-positive observations")
    return volume.astype(float)


def load_base_targets(
    prices: pd.DataFrame,
    path: Path = BASE_TARGET_PATH,
) -> pd.DataFrame:
    """Load the frozen v1 daily targets as the only v2 starting point."""
    base = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
    missing = [asset for asset in ASSETS if asset not in base.columns]
    if missing:
        raise ValueError(f"v1 target file is missing assets: {missing}")
    base = base.reindex(prices.index)[ASSETS].ffill()
    if base.isna().any().any():
        raise ValueError("v1 target weights do not cover the price history")
    sums = base.sum(axis=1)
    if not np.allclose(sums.to_numpy(), 1.0, atol=1e-8):
        raise ValueError("v1 target weights are not fully invested")
    return base


def _confirmed(signal: pd.Series, days: int) -> pd.Series:
    if days < 1:
        raise ValueError("confirmation days must be positive")
    return signal.astype(int).rolling(days, min_periods=days).sum().eq(days)


def rsi_series(prices: pd.Series, period: int = 14) -> pd.Series:
    """Wilder-style RSI calculated from closing prices only."""
    if period < 2:
        raise ValueError("RSI period must be at least 2")
    change = prices.diff()
    gain = change.clip(lower=0.0)
    loss = -change.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    relative_strength = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + relative_strength)
    return rsi.clip(0.0, 100.0)


def obv_series(prices: pd.Series, volume: pd.Series, ema_days: int = 20) -> tuple[pd.Series, pd.Series]:
    """Return OBV and its EMA; both use only same-day close/volume data."""
    direction = np.sign(prices.diff()).fillna(0.0)
    obv = (direction * volume).cumsum()
    obv_ema = obv.ewm(span=ema_days, adjust=False, min_periods=ema_days).mean()
    return obv, obv_ema


def build_signals(
    prices: pd.DataFrame,
    vix: pd.DataFrame,
    volume: pd.Series,
    parameters: Mapping[str, object] = PARAMETERS,
) -> pd.DataFrame:
    """Build a low-dimensional multi-indicator state machine at the close."""
    qqq = prices["QQQ"]
    qqq_returns = annual.daily_returns(prices)["QQQ"]
    ema_fast_days = int(parameters["ema_fast_days"])
    ema_slow_days = int(parameters["ema_slow_days"])
    ema_fast = qqq.ewm(span=ema_fast_days, adjust=False, min_periods=ema_fast_days).mean()
    ema_slow = qqq.ewm(span=ema_slow_days, adjust=False, min_periods=ema_slow_days).mean()
    mom126 = qqq.pct_change(126)
    mom_exit = qqq.pct_change(int(parameters["exit_momentum_days"]))
    rsi = rsi_series(qqq, int(parameters["rsi_period"]))
    obv, obv_ema = obv_series(qqq, volume, int(parameters["obv_ema_days"]))

    trend_bear = (qqq < ema_slow) & (ema_fast < ema_slow)
    momentum_bear = mom126 < 0.0
    rsi_bear = rsi < float(parameters["rsi_entry_threshold"])
    obv_bear = obv < obv_ema

    long_bear = (
        trend_bear
        & momentum_bear
        & rsi_bear
        & obv_bear
        & (vix["VIX"] >= float(parameters["entry_vix"]))
    )
    enter = _confirmed(long_bear, int(parameters["entry_confirm_days"]))
    exit_core = (qqq > ema_fast) & (mom_exit > 0.0)
    exit_indicator = str(parameters["exit_indicator"])
    if exit_indicator == "obv":
        calm_reentry = exit_core & (obv > obv_ema)
    elif exit_indicator == "rsi":
        calm_reentry = exit_core & (rsi > 50.0)
    elif exit_indicator == "either":
        calm_reentry = exit_core & ((rsi > 50.0) | (obv > obv_ema))
    elif exit_indicator == "vote":
        votes = (
            (qqq > ema_fast).astype(int)
            + (mom_exit > 0.0).astype(int)
            + (rsi > 50.0).astype(int)
            + (obv > obv_ema).astype(int)
        )
        calm_reentry = votes >= 3
    else:
        raise ValueError(f"unsupported exit indicator: {exit_indicator}")
    reentry = _confirmed(calm_reentry, int(parameters["exit_confirm_days"]))

    risk_off = np.zeros(len(prices), dtype=bool)
    state = False
    for i in range(len(prices)):
        if not state and bool(enter.iloc[i]):
            state = True
        elif state and bool(reentry.iloc[i]):
            state = False
        risk_off[i] = state

    vix3m = vix["VIX3M"]
    severe = (vix["VIX"] >= float(parameters["severe_vix"])) | (
        vix3m.notna() & (vix["VIX"] > vix3m)
    )
    return pd.DataFrame(
        {
            "QQQ": qqq,
            "QQQ_EMA_FAST": ema_fast,
            "QQQ_EMA_SLOW": ema_slow,
            "momentum_126": mom126,
            "momentum_exit": mom_exit,
            "RSI": rsi,
            "OBV": obv,
            "OBV_EMA": obv_ema,
            "VIX": vix["VIX"],
            "VIX3M": vix3m,
            "trend_bear": trend_bear,
            "momentum_bear": momentum_bear,
            "rsi_bear": rsi_bear,
            "obv_bear": obv_bear,
            "long_bear_entry": long_bear,
            "risk_off": risk_off,
            "severe_risk": severe,
            "daily_qqq_return": qqq_returns,
        },
        index=prices.index,
    )


def apply_overlay(
    base_targets: pd.DataFrame,
    signals: pd.DataFrame,
    parameters: Mapping[str, object] = PARAMETERS,
) -> pd.DataFrame:
    """Apply state decisions to the next trading day's v1 target weights."""
    target = base_targets.copy()
    # A decision at t can only change the portfolio held on t+1.
    active_next_day = np.r_[False, signals["risk_off"].to_numpy()[:-1]]
    severe_next_day = np.r_[False, signals["severe_risk"].to_numpy()[:-1]]
    normal_cap = float(parameters["normal_qqq_cap"])
    severe_cap = float(parameters["severe_qqq_cap"])
    cap = np.where(severe_next_day, severe_cap, normal_cap)

    qqq = base_targets["QQQ"].to_numpy(dtype=float)
    reduction = np.maximum(qqq - cap, 0.0) * active_next_day
    target["QQQ"] = qqq - reduction
    defensive = annual.DEFENSIVES[str(parameters["defensive_basket"])]
    for asset, weight in defensive.items():
        target[asset] = target[asset].to_numpy(dtype=float) + reduction * float(weight)

    sums = target.sum(axis=1)
    if not np.allclose(sums.to_numpy(), 1.0, atol=1e-8):
        raise ValueError("overlay produced weights that are not fully invested")
    if (target < -1e-10).any().any():
        raise ValueError("overlay produced a negative weight")
    return target


def build_target_weights(
    prices: pd.DataFrame,
    vix: pd.DataFrame,
    volume: pd.Series,
    parameters: Mapping[str, object] = PARAMETERS,
    base_targets: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    base = load_base_targets(prices) if base_targets is None else base_targets
    signals = build_signals(prices, vix, volume, parameters)
    return apply_overlay(base, signals, parameters), signals


def comparison_row(
    name: str,
    result: Mapping[str, object],
    qqq_annual: pd.DataFrame,
    complete_years: Sequence[int],
) -> tuple[dict[str, object], pd.DataFrame]:
    row, annual_frame = annual.score_candidate(name, result, qqq_annual, complete_years)
    annual_frame = annual_frame.sort_values("year").reset_index(drop=True)
    return row, annual_frame


def stress_windows(returns: pd.Series) -> pd.DataFrame:
    windows = {
        "2008 全年": ("2008-01-01", "2008-12-31"),
        "2008 金融危机段": ("2007-10-01", "2009-03-31"),
        "2022 全年": ("2022-01-01", "2022-12-31"),
        "2022 熊市段": ("2021-12-01", "2022-12-31"),
    }
    rows: list[dict[str, object]] = []
    for label, (start, end) in windows.items():
        series = returns.loc[start:end]
        if series.empty:
            continue
        row = annual.metrics(series)
        row["window"] = label
        row["start"] = str(series.index[0].date())
        row["end"] = str(series.index[-1].date())
        rows.append(row)
    return pd.DataFrame(rows)


def _candidate_parameters(
    entry_vix: float,
    severe_vix: float,
    normal_cap: float,
    severe_cap: float,
    exit_mode: str,
) -> dict[str, object]:
    params = dict(PARAMETERS)
    params.update(
        {
            "entry_vix": entry_vix,
            "severe_vix": severe_vix,
            "normal_qqq_cap": normal_cap,
            "severe_qqq_cap": severe_cap,
            "ema_fast_days": 50 if exit_mode == "fast" else 100,
            "exit_momentum_days": 21 if exit_mode == "fast" else 63,
            "exit_indicator": "obv" if exit_mode == "fast" else "rsi",
        }
    )
    return params


def neighbor_stability(
    prices: pd.DataFrame,
    base_targets: pd.DataFrame,
    vix: pd.DataFrame,
    volume: pd.Series,
    qqq_annual: pd.DataFrame,
    complete_years: Sequence[int],
    cost_bps: float,
) -> pd.DataFrame:
    """Evaluate a fixed, predeclared parameter neighborhood."""
    rows: list[dict[str, object]] = []
    for entry_vix in (15.0, 20.0, 25.0):
        for severe_vix in (25.0, 30.0, 35.0):
            for normal_cap in (0.75, 0.80, 0.85):
                for severe_cap in (0.35, 0.40, 0.45):
                    for exit_mode in ("fast", "medium"):
                        params = _candidate_parameters(
                            entry_vix, severe_vix, normal_cap, severe_cap, exit_mode
                        )
                        targets, _ = build_target_weights(
                            prices, vix, volume, params, base_targets
                        )
                        result = annual.backtest_targets(prices, targets, cost_bps)
                        row, _ = comparison_row(
                            "neighbor", result, qqq_annual, complete_years
                        )
                        row.update(
                            {
                                "entry_vix": entry_vix,
                                "severe_vix": severe_vix,
                                "normal_qqq_cap": normal_cap,
                                "severe_qqq_cap": severe_cap,
                                "exit_mode": exit_mode,
                                "pass_80pct_objective": bool(
                                    row["mdd_no_worse_years"] >= len(complete_years)
                                    and row["both_within_5pp_years"]
                                    >= math.ceil(0.80 * len(complete_years))
                                ),
                            }
                        )
                        rows.append(row)
    return pd.DataFrame(rows)


def indicator_stability(
    prices: pd.DataFrame,
    base_targets: pd.DataFrame,
    vix: pd.DataFrame,
    volume: pd.Series,
    qqq_annual: pd.DataFrame,
    complete_years: Sequence[int],
    cost_bps: float,
) -> pd.DataFrame:
    """Vary RSI/OBV settings around the fixed multi-indicator rule."""
    rows: list[dict[str, object]] = []
    for rsi_threshold in (45.0, 50.0, 55.0):
        for obv_days in (10, 20, 30):
            for exit_indicator in ("obv", "rsi", "either"):
                params = dict(PARAMETERS)
                params.update(
                    {
                        "rsi_entry_threshold": rsi_threshold,
                        "obv_ema_days": obv_days,
                        "exit_indicator": exit_indicator,
                    }
                )
                targets, _ = build_target_weights(
                    prices, vix, volume, params, base_targets
                )
                result = annual.backtest_targets(prices, targets, cost_bps)
                row, _ = comparison_row(
                    "indicator_neighbor", result, qqq_annual, complete_years
                )
                row.update(
                    {
                        "rsi_entry_threshold": rsi_threshold,
                        "obv_ema_days": obv_days,
                        "exit_indicator": exit_indicator,
                        "pass_80pct_objective": bool(
                            row["mdd_no_worse_years"] >= len(complete_years)
                            and row["both_within_5pp_years"]
                            >= math.ceil(0.80 * len(complete_years))
                        ),
                    }
                )
                rows.append(row)
    return pd.DataFrame(rows)


def _summary_table(rows: Sequence[tuple[str, Mapping[str, object]]]) -> pd.DataFrame:
    fields = [
        "annual_return",
        "max_drawdown",
        "sharpe",
        "volatility",
        "mdd_no_worse_years",
        "return_within_5pp_years",
        "both_within_5pp_years",
        "min_return_gap",
    ]
    out = []
    for name, row in rows:
        out.append({"strategy": name, **{field: row.get(field) for field in fields}})
    return pd.DataFrame(out)


def _pct(value: float) -> str:
    return f"{float(value):.2%}"


def write_report(
    output_dir: Path,
    summary: pd.DataFrame,
    annual_frame: pd.DataFrame,
    stress: pd.DataFrame,
    neighbors: pd.DataFrame,
    indicator_neighbors: pd.DataFrame,
    fixed_row: Mapping[str, object],
) -> None:
    display_summary = summary.copy()
    for column in ["annual_return", "max_drawdown", "volatility", "min_return_gap"]:
        display_summary[column] = display_summary[column].map(_pct)
    display_annual = annual_frame.copy()
    for column in [
        "qqq_return",
        "strategy_return",
        "return_gap",
        "qqq_max_drawdown",
        "strategy_max_drawdown",
        "mdd_gap",
    ]:
        display_annual[column] = display_annual[column].map(_pct)
    display_stress = stress.copy()
    for column in ["total_return", "annual_return", "max_drawdown", "volatility"]:
        display_stress[column] = display_stress[column].map(_pct)

    pass_count = int(neighbors["pass_80pct_objective"].sum())
    total_neighbors = int(len(neighbors))
    indicator_pass_count = int(indicator_neighbors["pass_80pct_objective"].sum())
    indicator_total = int(len(indicator_neighbors))
    complete_frame = annual_frame[
        (annual_frame["year"] <= 2025) & (annual_frame["qqq_observations"] >= 200)
    ]
    complete_years = sorted(int(year) for year in complete_frame["year"])
    lines = [
        "# 长熊市防守 v2 回测报告",
        "",
        f"固定候选：`{STRATEGY_NAME}`。本版本建立在已冻结的 v1 目标仓位之上，输出目录独立于 `output_annual_v2`。",
        "",
        "## 结论",
        "",
        f"完整年份（{min(complete_years)}—{max(complete_years)}）中，v2 的年度最大回撤不高于 QQQ 的年份为 "
        f"{int(fixed_row['mdd_no_worse_years'])}/{len(complete_years)}，收益差不超过 5 个百分点且回撤不差于 QQQ 的年份为 "
        f"{int(fixed_row['both_within_5pp_years'])}/{len(complete_years)}。",
        f"预先定义的 {total_neighbors} 个风险参数邻域中，有 {pass_count} 个（{pass_count / max(total_neighbors, 1):.1%}）通过门槛；另有 {indicator_pass_count}/{indicator_total} 个 RSI/OBV 参数邻域通过同一门槛。",
        "这说明候选不是依靠某一个精确参数点才成立，但它仍然不是保证未来收益或回撤的承诺。",
        "",
        "## 固定规则",
        "",
        "- 入场：QQQ 的 EMA50 低于 EMA200 且价格低于 EMA200、126 日动量为负、RSI14<50、OBV 低于 OBV 的 20 日 EMA，同时 VIX≥20。",
        "- 普通风险状态：将 QQQ 权重上限设为 75%。",
        "- 极端风险状态：VIX≥30 或 VIX 高于 VIX3M 时，将 QQQ 权重上限设为 30%。",
        "- 再入场：QQQ 站上 EMA50、21 日动量转正且 OBV 站上其 EMA；信号在下一个交易日执行。",
        "- 被减掉的 QQQ 转入固定防守篮子：TLT 15%、IAU 35%、XLU 10%、BIL 40%。",
        f"- 回测成本：每次绝对换手 {PARAMETERS['cost_bps']:.1f}bp。",
        "",
        "## 全历史与压力窗口",
        "",
        annual.markdown_table(display_summary),
        "",
        annual.markdown_table(display_stress),
        "",
        "## 逐年审计",
        "",
        annual.markdown_table(
            display_annual[
                [
                    "year",
                    "qqq_return",
                    "strategy_return",
                    "return_gap",
                    "qqq_max_drawdown",
                    "strategy_max_drawdown",
                    "mdd_gap",
                    "both_within_5pp",
                ]
            ]
        ),
        "",
        "## 重要边界",
        "",
        "- VIX 是历史波动率风险信号，不是可直接交易的现金替代品；VIX 高并不保证后续股市继续下跌。",
        "- 本回测没有把 SVXY 放进核心仓位：SVXY 是短波动暴露，极端行情可能放大损失，而且其历史无法覆盖 2008 年，因此不能用它证明长熊市防守能力。",
        "- BOXX 的实际历史从 2022 年才有，不能把它当成 2008 年的历史现金代理。全历史结果使用 BIL 作为可回测的短期国债代理；如果未来加入 BOXX，应另做 2022 年以来的同区间实盘可获得性和费用敏感性测试。",
        "- 2007 与 2026 是价格样本的部分年份，仅作为诊断，不参与 80% 完整年份门槛。",
        "- v1 保存在 `output_annual_v2`，本 v2 没有覆盖它；是否采用 v2 仍应经过纸面交易、滑点和实时数据验证。",
        "",
        "## 输出文件",
        "",
        "- `selected_target_weights.csv`：v2 每日目标仓位。",
        "- `selected_signals.csv`：EMA、RSI、OBV、动量、VIX、风险状态和极端状态。",
        "- `annual_comparison.csv`：逐年 QQQ/v2 收益和最大回撤对照。",
        "- `neighbor_stability.csv`：预先定义参数邻域的稳定性检查。",
        "- `indicator_stability.csv`：RSI 阈值、OBV EMA 周期和再入场指标的稳定性检查。",
        "- `stress_windows.csv`：2008、2009Q1 和 2022 压力窗口。",
    ]
    (output_dir / "RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(
    output_dir: Path = OUTPUT_DIR,
    cost_bps: float = float(PARAMETERS["cost_bps"]),
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prices = annual.load_prices(DATA_PATH)
    vix = load_vix(VIX_PATH, prices.index)
    volume = load_volume(prices, output_dir)
    base_targets = load_base_targets(prices, BASE_TARGET_PATH)
    qqq_targets = annual.constant_targets(prices, {"QQQ": 1.0})
    qqq_result = annual.backtest_targets(prices, qqq_targets, cost_bps)
    v1_result = annual.backtest_targets(prices, base_targets, cost_bps)
    targets, signals = build_target_weights(
        prices, vix, volume, PARAMETERS, base_targets
    )
    result = annual.backtest_targets(prices, targets, cost_bps)

    qqq_annual = annual.annual_metrics(qqq_result["returns"])
    complete_years = [
        int(year) for year, observations in zip(qqq_annual["year"], qqq_annual["observations"])
        if int(observations) >= 200 and int(year) <= 2025
    ]
    v1_row, _ = comparison_row("v1_frozen", v1_result, qqq_annual, complete_years)
    selected_row, annual_frame = comparison_row(
        STRATEGY_NAME, result, qqq_annual, complete_years
    )
    qqq_row, _ = comparison_row("QQQ", qqq_result, qqq_annual, complete_years)
    neighbors = neighbor_stability(
        prices, base_targets, vix, volume, qqq_annual, complete_years, cost_bps
    )
    indicator_neighbors = indicator_stability(
        prices, base_targets, vix, volume, qqq_annual, complete_years, cost_bps
    )
    stress = pd.concat(
        [
            stress_windows(qqq_result["returns"]).assign(strategy="QQQ"),
            stress_windows(v1_result["returns"]).assign(strategy="v1_frozen"),
            stress_windows(result["returns"]).assign(strategy=STRATEGY_NAME),
        ],
        ignore_index=True,
    )
    summary = _summary_table(
        [("QQQ", qqq_row), ("v1_frozen", v1_row), (STRATEGY_NAME, selected_row)]
    )

    vix.to_csv(output_dir / "vix_aligned.csv")
    volume.rename("volume").rename_axis("date").to_csv(output_dir / "qqq_volume.csv")
    signals.to_csv(output_dir / "selected_signals.csv")
    targets.to_csv(output_dir / "selected_target_weights.csv")
    pd.DataFrame(
        {
            "QQQ_return": qqq_result["returns"],
            "v1_return": v1_result["returns"],
            "v2_return": result["returns"],
            "QQQ_equity": qqq_result["equity"],
            "v1_equity": v1_result["equity"],
            "v2_equity": result["equity"],
        }
    ).to_csv(output_dir / "strategy_returns.csv")
    annual_frame.to_csv(output_dir / "annual_comparison.csv", index=False)
    neighbors.to_csv(output_dir / "neighbor_stability.csv", index=False)
    indicator_neighbors.to_csv(output_dir / "indicator_stability.csv", index=False)
    stress.to_csv(output_dir / "stress_windows.csv", index=False)
    summary.to_csv(output_dir / "summary.csv", index=False)
    (output_dir / "parameters.json").write_text(
        json.dumps(
            {
                "strategy": STRATEGY_NAME,
                "parameters": PARAMETERS,
                "complete_years": complete_years,
                "data_start": prices.index[0].strftime("%Y-%m-%d"),
                "data_end": prices.index[-1].strftime("%Y-%m-%d"),
                "vix_source": str(VIX_PATH),
                "volume_source": "Yahoo Finance QQQ daily volume, snapshotted in qqq_volume.csv",
                "boXX_policy": "not used as a 2008 proxy; BIL retained for full-history comparability",
                "svxy_policy": "not used in the core candidate because it is short-volatility and unavailable in 2008",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_report(
        output_dir,
        summary,
        annual_frame,
        stress,
        neighbors,
        indicator_neighbors,
        selected_row,
    )
    return {
        "prices": prices,
        "vix": vix,
        "base_targets": base_targets,
        "targets": targets,
        "signals": signals,
        "qqq": qqq_row,
        "v1": v1_row,
        "selected": selected_row,
        "annual": annual_frame,
        "neighbors": neighbors,
        "indicator_neighbors": indicator_neighbors,
        "stress": stress,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--cost-bps", type=float, default=float(PARAMETERS["cost_bps"]))
    args = parser.parse_args()
    result = run(args.output_dir, args.cost_bps)
    selected = result["selected"]
    print(
        json.dumps(
            {
                "strategy": STRATEGY_NAME,
                "annual_return": selected["annual_return"],
                "max_drawdown": selected["max_drawdown"],
                "mdd_no_worse_years": selected["mdd_no_worse_years"],
                "both_within_5pp_years": selected["both_within_5pp_years"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
