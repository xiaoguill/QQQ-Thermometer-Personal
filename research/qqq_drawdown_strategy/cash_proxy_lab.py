"""BOXX/SGOV cash-proxy sensitivity for the frozen v2 strategy.

The experiment replaces the BIL sleeve with one real ETF at a time.  It does
not add leverage or create an extra portfolio sleeve, so any difference is the
cash-proxy effect only.  Each proxy is evaluated from its first available
adjusted-close bar through the common data end.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

try:
    from . import annual_strategy_lab as annual
    from . import backtest as data_loader
except ImportError:  # pragma: no cover
    import annual_strategy_lab as annual
    import backtest as data_loader


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output_cash_proxy_v1"
V2_TARGET_PATH = ROOT / "output_long_bear_v2" / "selected_target_weights.csv"
PROXIES = ("BOXX", "SGOV")
COST_BPS = 5.0
DATA_END = "2026-08-10"
OFFICIAL_SOURCES = {
    "BOXX": "https://funds.alphaarchitect.com/boxetf/",
    "SGOV": "https://www.ishares.com/us/products/314116/",
}


def load_v2_targets(prices: pd.DataFrame) -> pd.DataFrame:
    targets = pd.read_csv(V2_TARGET_PATH, index_col=0, parse_dates=True).sort_index()
    targets = targets.reindex(prices.index).ffill()[annual.ASSETS]
    if targets.isna().any().any() or not np.allclose(targets.sum(axis=1), 1.0):
        raise ValueError("v2 target weights are incomplete or not fully invested")
    return targets


def load_proxy_prices(output_dir: Path = OUTPUT_DIR, refresh: bool = False) -> pd.DataFrame:
    path = output_dir / "proxy_prices_adj_close.csv"
    if path.exists() and not refresh:
        return pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    frames: dict[str, pd.Series] = {}
    for symbol in PROXIES:
        frame = data_loader.fetch_yahoo(symbol, "2000-01-01", DATA_END)
        frames[symbol] = frame["adj_close"].rename(symbol)
    prices = pd.concat(frames, axis=1, sort=True).sort_index()
    output_dir.mkdir(parents=True, exist_ok=True)
    prices.rename_axis("date").to_csv(path, float_format="%.10f")
    return prices


def _generic_backtest(
    prices: pd.DataFrame,
    targets: pd.DataFrame,
    cost_bps: float = COST_BPS,
) -> dict[str, object]:
    returns = prices.pct_change().fillna(0.0)
    targets = targets.reindex(prices.index).ffill().fillna(0.0)
    turnover = targets.diff().abs().sum(axis=1).fillna(0.0)
    net = (targets * returns).sum(axis=1) - turnover * cost_bps / 10000.0
    return {
        "returns": net,
        "equity": (1.0 + net).cumprod(),
        "turnover": turnover,
        "targets": targets,
    }


def backtest_cash_proxy(
    prices: pd.DataFrame,
    v2_targets: pd.DataFrame,
    proxy_prices: pd.Series,
    proxy: str,
    cost_bps: float = COST_BPS,
) -> tuple[dict[str, object], dict[str, object]]:
    """Return same-interval BIL and proxy portfolios."""
    start = proxy_prices.first_valid_index()
    end = proxy_prices.last_valid_index()
    index = prices.index[(prices.index >= start) & (prices.index <= end)]
    proxy_series = proxy_prices.reindex(index)
    if proxy_series.isna().any():
        raise ValueError(f"{proxy} has missing prices inside its evaluation interval")

    bil_prices = prices.loc[index, annual.ASSETS]
    bil_targets = v2_targets.loc[index, annual.ASSETS]
    bil_result = annual.backtest_targets(bil_prices, bil_targets, cost_bps)

    proxy_frame = bil_prices.copy()
    proxy_frame[proxy] = proxy_series.to_numpy()
    proxy_targets = bil_targets.copy()
    proxy_targets[proxy] = proxy_targets["BIL"]
    proxy_targets["BIL"] = 0.0
    proxy_targets = proxy_targets[annual.ASSETS[:4] + ["BIL", proxy]]
    # The BIL column is retained at zero to make the cash substitution explicit.
    proxy_result = _generic_backtest(proxy_frame, proxy_targets, cost_bps)
    return bil_result, proxy_result


def _cash_asset_result(
    bil: pd.Series,
    proxy: pd.Series,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    index = bil.index[(bil.index >= start) & (bil.index <= end)]
    rows = []
    for name, series in [("BIL", bil), ("proxy", proxy)]:
        returns = series.reindex(index).pct_change().fillna(0.0)
        row = annual.metrics(returns)
        row["asset"] = name
        rows.append(row)
    return pd.DataFrame(rows)


def _summary_row(
    proxy: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    bil_result: Mapping[str, object],
    proxy_result: Mapping[str, object],
    raw_cash: pd.DataFrame,
) -> dict[str, object]:
    bil_metrics = annual.metrics(bil_result["returns"])
    proxy_metrics = annual.metrics(proxy_result["returns"])
    raw_bil = raw_cash.loc[raw_cash["asset"] == "BIL"].iloc[0]
    raw_proxy = raw_cash.loc[raw_cash["asset"] == "proxy"].iloc[0]
    return {
        "proxy": proxy,
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
        "observations": len(bil_result["returns"]),
        "bil_strategy_annual_return": bil_metrics["annual_return"],
        "proxy_strategy_annual_return": proxy_metrics["annual_return"],
        "strategy_annual_return_diff": proxy_metrics["annual_return"] - bil_metrics["annual_return"],
        "bil_strategy_total_return": bil_metrics["total_return"],
        "proxy_strategy_total_return": proxy_metrics["total_return"],
        "strategy_total_return_diff": proxy_metrics["total_return"] - bil_metrics["total_return"],
        "bil_strategy_max_drawdown": bil_metrics["max_drawdown"],
        "proxy_strategy_max_drawdown": proxy_metrics["max_drawdown"],
        "strategy_max_drawdown_diff": proxy_metrics["max_drawdown"] - bil_metrics["max_drawdown"],
        "bil_strategy_sharpe": bil_metrics["sharpe"],
        "proxy_strategy_sharpe": proxy_metrics["sharpe"],
        "bil_cash_annual_return": raw_bil["annual_return"],
        "proxy_cash_annual_return": raw_proxy["annual_return"],
        "cash_annual_return_diff": raw_proxy["annual_return"] - raw_bil["annual_return"],
        "bil_cash_total_return": raw_bil["total_return"],
        "proxy_cash_total_return": raw_proxy["total_return"],
        "cash_total_return_diff": raw_proxy["total_return"] - raw_bil["total_return"],
    }


def annual_comparison(
    bil_result: Mapping[str, object],
    proxy_result: Mapping[str, object],
) -> pd.DataFrame:
    bil = annual.annual_metrics(bil_result["returns"]).rename(
        columns={"return": "bil_return", "max_drawdown": "bil_max_drawdown"}
    )
    proxy = annual.annual_metrics(proxy_result["returns"]).rename(
        columns={"return": "proxy_return", "max_drawdown": "proxy_max_drawdown"}
    )
    out = bil.merge(proxy, on="year", how="inner")
    out["return_diff"] = out["proxy_return"] - out["bil_return"]
    out["max_drawdown_diff"] = out["proxy_max_drawdown"] - out["bil_max_drawdown"]
    return out


def _pct(value: float) -> str:
    return f"{float(value):.4%}"


def write_report(
    output_dir: Path,
    summary: pd.DataFrame,
    annual_frames: Mapping[str, pd.DataFrame],
) -> None:
    lines = [
        "# BOXX / SGOV 现金替代敏感性报告",
        "",
        "本实验使用最新的 v2 多指标策略目标仓位，把目标仓位中的 BIL 全部替换成 BOXX 或 SGOV。没有增加杠杆，也没有额外增加组合风险资产。每只 ETF 都从自己的第一根可用日线开始计算，交易成本按每次绝对换手 5bp。",
        "",
        "## 结论",
        "",
        "现金代理本身的收益差异存在，但因为 v2 平均 BIL 权重很低，传导到整个组合后的年化收益增量非常小。BOXX/SGOV 不能单独解决 QQQ 长熊市问题。",
        "",
        annual.markdown_table(summary),
        "",
        "表中 `strategy_annual_return_diff` 和 `strategy_total_return_diff` 是替换 BIL 后相对同区间 BIL 基线的增量；不是相对 QQQ 的超额收益。",
        "",
        "## 实际历史起点",
        "",
        "- BOXX：官方基金成立日为 2022-12-27，Yahoo 第一根可用调整收盘价为 2022-12-28。",
        "- SGOV：官方基金成立日为 2020-05-26，Yahoo 第一根可用调整收盘价为 2020-06-01。",
        "- BOXX 官方资料：[Alpha Architect BOXX 页面](https://funds.alphaarchitect.com/boxetf/)。",
        "- SGOV 官方资料：[iShares SGOV 页面](https://www.ishares.com/us/products/314116/)。",
        "",
        "## 逐年增量",
        "",
    ]
    for proxy, frame in annual_frames.items():
        lines.extend([f"### {proxy}", "", annual.markdown_table(frame), ""])
    lines.extend(
        [
            "## 解释",
            "",
            "BOXX 使用期权 box spread 获得短期利率暴露，SGOV 直接持有 0—3 个月美国国债，两者都不是保证本金的银行现金账户。实际交易仍会受到费用、买卖价差、税务和基金价格偏离净值影响。",
            "",
            "本报告只回答‘替换 BIL 后能增加多少收益’，不把 BOXX/SGOV 当作 2008 年历史代理；如需比较 2008 年，应使用 BIL 或短期国债指数代理。",
        ]
    )
    (output_dir / "RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(output_dir: Path = OUTPUT_DIR, refresh: bool = False) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prices = annual.load_prices()
    v2_targets = load_v2_targets(prices)
    proxy_prices = load_proxy_prices(output_dir, refresh)
    bil_series = prices["BIL"]
    summary_rows: list[dict[str, object]] = []
    annual_frames: dict[str, pd.DataFrame] = {}
    return_frames: dict[str, pd.DataFrame] = {}
    for proxy in PROXIES:
        bil_result, proxy_result = backtest_cash_proxy(
            prices, v2_targets, proxy_prices[proxy], proxy, COST_BPS
        )
        start = proxy_prices[proxy].first_valid_index()
        end = proxy_prices[proxy].last_valid_index()
        raw_cash = _cash_asset_result(bil_series, proxy_prices[proxy], start, end)
        summary_rows.append(_summary_row(proxy, start, end, bil_result, proxy_result, raw_cash))
        annual_frames[proxy] = annual_comparison(bil_result, proxy_result)
        return_frames[proxy] = pd.DataFrame(
            {
                "bil_return": bil_result["returns"],
                "proxy_return": proxy_result["returns"],
                "bil_equity": bil_result["equity"],
                "proxy_equity": proxy_result["equity"],
            }
        )

    summary = pd.DataFrame(summary_rows)
    summary.to_csv(output_dir / "cash_proxy_summary.csv", index=False)
    for proxy, frame in annual_frames.items():
        frame.to_csv(output_dir / f"annual_{proxy.lower()}_comparison.csv", index=False)
        return_frames[proxy].to_csv(output_dir / f"strategy_returns_{proxy.lower()}.csv")
    (output_dir / "parameters.json").write_text(
        json.dumps(
            {
                "strategy": "LongBear_EMA_RSI_OBV_VIX_multi_indicator",
                "cash_policy": "replace all BIL target weight with one proxy; no leverage",
                "proxies": list(PROXIES),
                "cost_bps": COST_BPS,
                "official_sources": OFFICIAL_SOURCES,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_report(output_dir, summary, annual_frames)
    return {"summary": summary, "annual": annual_frames, "proxy_prices": proxy_prices}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    result = run(args.output_dir, args.refresh)
    print(result["summary"].to_json(orient="records", force_ascii=False))


if __name__ == "__main__":
    main()
