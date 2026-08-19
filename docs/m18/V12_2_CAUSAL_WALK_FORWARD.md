# v12.2 因果 Walk-Forward 回放边界

## 定位

`v12.2-causal-walk-forward/v1` 是独立的历史验证/审计版本，不是新的策略合同。当前仓库没有正式登记的 `v12.2` 策略，因此：

- 实际策略版本仍为冻结的 `v10_preserve_shock_recovery`；
- M04 指标、M05 状态机、M06 解释和 M07 候选目标仓位全部复用现有服务；
- 本任务只负责本地数据读取、逐日因果前缀、下一交易日执行模拟、净值和审计证据；
- 不修改 `configs/frozen/strategy_contract.json`，不改变 v10 阈值、权重或状态机。

## 数据口径

默认使用：

1. `research/qqq_drawdown_strategy/output/prices_adj_close.csv`：QQQ、BIL 等本地 adjusted close；
2. `research/qqq_drawdown_strategy/output/vix_indices.csv`：本地 CBOE VIX/VIX3M 日线；
3. 单独提供的 VXX 本地 OHLCV 缓存，例如已有 Alpaca 审计缓存。VXX 缺失时 fail-closed，不使用 VIX、SVXY 或 BIL 替代。

本地 QQQ/VIX/VIX3M 文件只有收盘价。为了进入既有 M02→M03 数据边界，回放适配器会把 close 重复为 open/high/low；M04 当前冻结指标只使用 close 派生值，因此不会声称完成完整 OHLC 审计。这个限制会写入 `manifest.json` 和最终报告。

## 因果规则

每个信号日 `t` 单独重建 raw 输入前缀；M04 指标随后按完整历史序列的时间顺序运行。由于 M04 实现对每个快照只读取截至该快照日期的值，完整序列与逐日前缀在信号日上应当相同；首日、中段和末日再用完整前缀逐值复算，结果必须一致：

```text
raw snapshot request.end_date = t
payload 中最大 bar_date      <= t
M04 indicator input dates     <= t
M05 signal_date               = t
execution_date                = t 后第一个 NYSE 交易日
回报周期                     = execution_date 到下一交易日 close-to-close 代理
```

预热上下文从本地共同可用的最早 QQQ/VIX/VIX3M 交易日开始；对外输出从 `2025-01-01` 后的第一个有效交易日开始。首个执行日使用输出窗口前一个信号日的目标仓位，避免人为把首日强行设为现金或满仓。这个运行器不会把“全样本一次性计算出的未来指标”写回过去；未来行只能产生自己的快照，且不能改变前缀复算结果。

## 结果文件

运行器把结果写到仓库外的 artifact archive：

- `manifest.json`：数据文件 hash、策略合同 hash、版本、时点、缺失政策；
- `signals.csv`：逐日温度、状态、指标、状态证据、目标仓位和 prefix hash；
- `weights.csv`：信号与下一交易日执行目标；
- `transactions.csv`：目标变化和换手；
- `equity_curve_5bps.csv`、`equity_curve_10bps.csv`、`equity_curve_25bps.csv`：净值和 QQQ 同期基准；
- `annual_metrics.csv`：逐年/阶段收益和年内最大回撤；
- `checks.json`：无未来 bar、执行滞后、权重总和和 VXX 覆盖检查；
- `REPORT.md`：中文结论和局限。

## 运行示例

在仓库根目录执行，VXX 路径仅作为本机参数，不写入 Git：

```powershell
& "C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m src.jobs.m18.v12_2_walk_forward `
  --config configs/m18/v12_2_walk_forward.json `
  --vxx-csv "D:\Backup\Documents\量化回测\FinRL-Trading\work\alpaca_validation_20260812\alpaca_VXX.csv"
```

如果没有 VXX 文件，仍可以单独扩展为“温度/状态回放”，但不能发布完整策略净值；当前默认设置会拒绝不完整的回报模拟。
