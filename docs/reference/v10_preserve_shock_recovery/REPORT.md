# 恢复加速策略独立回测与验证报告

## 结论先行

本实验是独立的 recovery_acceleration_lab，不覆盖此前的策略、脚本或输出目录。
它只针对旧实验已确认的缺陷进行修改：VIX/期限结构在 V 型反弹后仍高，导致恢复状态长期无法释放风险。最终候选 v10 进一步保留旧版冲击走廊，只修改恢复阶段。

推荐候选不是收益承诺，也不是已经替代旧版本的生产策略。v10 在本地核心窗口的主成本口径下改善了长期最大回撤，同时保持 2020 暴跌段的旧冲击走廊；因此结论是“值得进入下一轮验证”，而不是直接实盘替换。

## 规则定义

| 模块 | 固定规则 |
|---|---|
| 冲击入场 | QQQ 5 日收益 ≤ -5%，且 VIX ≥ 30 或 VIX/VIX3M ≥ 1.0 |
| VXX 条件 | QQQ 5 日下跌、VIX ≥ 30、期限倒挂、VIX ≥ 40；仅在最近固定窗口内使用 |
| 恢复确认 | 价格反弹、QQQ 站上 EMA10、20 日实现波动率下降，至少两项成立 |
| 恢复阶段 0 | BIL |
| 恢复阶段 1 | 推荐候选只有在中期闸门连续确认后增加 QQQ |
| 中期闸门 | QQQ > SMA150 且 126 日动量为正，连续 5 个交易日 |
| 重新冲击 | 恢复后必须再次出现 QQQ 实际短期下跌并伴随 VIX/期限压力；单独的 VIX 高位不会打断恢复 |
| 执行 | 收盘计算信号，下一交易日执行；不使用未来价格填充 |

## 版本对照

| 版本 | 主要变化 |
|---|---|
| v6_three_state_fast | 恢复阶段较快增加 QQQ，不等待中期闸门，作为偏进攻对照 |
| v7_vxx_acute_1d | VXX 最近 1 个交易日急性窗口 |
| v8_ordered_qld_recovery | BIL→QQQ→QQQ/QLD，QLD 需要中期闸门 |
| v9_hysteresis_strict | 中期闸门连续 10 日、恢复至少 20 日 |
| recommended_accelerated | VXX 最近 2 个交易日窗口，QQQ 恢复需要中期闸门连续 5 日 |
| v10_preserve_shock_recovery | 冲击状态完全保留原控制组走廊，只在恢复阶段按中期闸门增加 QQQ |

## local_extended：主成本口径

| 版本 | CAGR | 最大回撤 | Sharpe | 日相关性 |
|---|---:|---:|---:|---:|
| control_current_corridor | 16.47% | -18.01% | 1.072 | 0.686 |
| recommended_accelerated | 16.69% | -15.95% | 1.079 | 0.700 |
| v10_preserve_shock_recovery | 16.63% | -16.88% | 1.084 | 0.691 |
| v6_three_state_fast | 16.99% | -18.69% | 1.088 | 0.720 |
| v7_vxx_acute_1d | 16.48% | -17.79% | 1.067 | 0.703 |
| v8_ordered_qld_recovery | 17.05% | -18.69% | 1.090 | 0.720 |
| v9_hysteresis_strict | 16.53% | -15.79% | 1.071 | 0.701 |

## local_core：主成本口径

| 版本 | CAGR | 最大回撤 | Sharpe | 日相关性 |
|---|---:|---:|---:|---:|
| control_current_corridor | 17.66% | -18.01% | 1.134 | 0.723 |
| recommended_accelerated | 18.16% | -15.78% | 1.154 | 0.746 |
| v10_preserve_shock_recovery | 18.06% | -16.88% | 1.160 | 0.730 |
| v6_three_state_fast | 18.49% | -18.69% | 1.165 | 0.761 |
| v7_vxx_acute_1d | 17.89% | -17.79% | 1.138 | 0.749 |
| v8_ordered_qld_recovery | 18.55% | -18.69% | 1.167 | 0.761 |
| v9_hysteresis_strict | 18.06% | -15.78% | 1.149 | 0.747 |

## alpaca_proxy：主成本口径

| 版本 | CAGR | 最大回撤 | Sharpe | 日相关性 |
|---|---:|---:|---:|---:|
| control_current_corridor | 17.62% | -18.14% | 1.132 | 0.689 |
| v10_preserve_shock_recovery | 17.76% | -18.45% | 1.145 | 0.696 |

## 逐年与关键阶段

主成本口径的完整逐年结果在 annual_*.csv，关键阶段在 phases_*.csv。特别关注：

- 2019：检验是否因 VXX 或过度防守而长期滞后；
- 2020-02-19 至 2020-03-23：检验冲击保护；
- 2020-03-24 至 2020-06-30：检验 V 型反弹恢复速度；
- 2022：检验反弹后是否过早加入 QQQ/QLD；
- 2025/2026：只作为最近阶段的补充观察，不作为参数选择依据。

### local_extended：年度双目标统计

版本的 both 表示该年最大回撤不差于 QQQ，且收益不低于 QQQ-5 个百分点。

- v6_three_state_fast：年度 MDD 不差于 QQQ 12/19；收益与 QQQ 差距不超过 5pp 且 MDD 更好 8/19。
- v7_vxx_acute_1d：年度 MDD 不差于 QQQ 12/19；收益与 QQQ 差距不超过 5pp 且 MDD 更好 8/19。
- v8_ordered_qld_recovery：年度 MDD 不差于 QQQ 12/19；收益与 QQQ 差距不超过 5pp 且 MDD 更好 7/19。
- v9_hysteresis_strict：年度 MDD 不差于 QQQ 12/19；收益与 QQQ 差距不超过 5pp 且 MDD 更好 7/19。
- recommended_accelerated：年度 MDD 不差于 QQQ 12/19；收益与 QQQ 差距不超过 5pp 且 MDD 更好 8/19。
- v10_preserve_shock_recovery：年度 MDD 不差于 QQQ 11/19；收益与 QQQ 差距不超过 5pp 且 MDD 更好 8/19。

### local_core：年度双目标统计

版本的 both 表示该年最大回撤不差于 QQQ，且收益不低于 QQQ-5 个百分点。

- v6_three_state_fast：年度 MDD 不差于 QQQ 9/15；收益与 QQQ 差距不超过 5pp 且 MDD 更好 7/15。
- v7_vxx_acute_1d：年度 MDD 不差于 QQQ 9/15；收益与 QQQ 差距不超过 5pp 且 MDD 更好 7/15。
- v8_ordered_qld_recovery：年度 MDD 不差于 QQQ 9/15；收益与 QQQ 差距不超过 5pp 且 MDD 更好 6/15。
- v9_hysteresis_strict：年度 MDD 不差于 QQQ 9/15；收益与 QQQ 差距不超过 5pp 且 MDD 更好 6/15。
- recommended_accelerated：年度 MDD 不差于 QQQ 9/15；收益与 QQQ 差距不超过 5pp 且 MDD 更好 7/15。
- v10_preserve_shock_recovery：年度 MDD 不差于 QQQ 8/15；收益与 QQQ 差距不超过 5pp 且 MDD 更好 7/15。

### alpaca_proxy：年度双目标统计

版本的 both 表示该年最大回撤不差于 QQQ，且收益不低于 QQQ-5 个百分点。

- v10_preserve_shock_recovery：年度 MDD 不差于 QQQ 7/11；收益与 QQQ 差距不超过 5pp 且 MDD 更好 6/11。

## 成本压力测试与稳健性

同时计算 uniform 5/10/25 bps 与 liquid core 5 bps、其他资产 10 bps、VXX/SVXY 25 bps 的主压力口径。
成本不是免费假设：VXX/SVXY 的实际滑点、价差、极端行情跳空和产品结构损耗可能高于这里的固定 bps。

固定邻域共 18 组，包含 VXX 急性窗口 1/2/3 日、中期闸门连续 3/5/10 日、恢复阶段 5/10 日；不从邻域中挑选年度最优点。

执行延迟敏感性包含 3 组，覆盖 1/2/3 个交易日。

前缀稳定性检查结果：
split_date  prefix_rows  state_mismatches  lookahead_check_passed
2022-07-06         2644                 0                    True

## 数据与回测限制

- local_core 与 local_extended 使用项目中已审计的本地复权价格；local_extended 只用于扩展历史窗口。
- Alpaca proxy 是缓存的拆股调整代理窗口，不等于 Alpaca 原始行情全部历史；VXX 缺失期自动路由至 BIL，并单独记录。Alpaca 交叉核验只复核控制组与 v10 最终候选，本地数据才完整比较全部版本。
- 这是日线收盘后、下一交易日执行的低频回测，不模拟盘中成交、真实滑点、税费、融资利息、借券、限价单未成交或账户风控。
- VXX 是波动率保险工具，不是长期收益资产；即使滚动窗口限制了持有期，也不能消除结构性损耗。
- CAGR、MDD 和逐年收益不能证明未来一定跑赢 QQQ；最重要的负面结果是 2020 暴跌段保护可能弱于旧版本，这也是保留旧版本并继续走样本外验证的原因。

## 文件索引

- summary_*.csv：各数据源、成本口径和版本的总指标。
- annual_*.csv：逐年收益与逐年最大回撤。
- phases_*.csv：2008、2019、2020、2022、2025、2026 关键阶段。
- transitions_*.csv：状态切换日期及信号。
- fallback_*.csv：VXX/SVXY 历史缺失导致的 BIL 路由。
- neighbor_stability.csv：预先定义的参数邻域。
- delay_sensitivity.csv：执行延迟压力。
- prefix_stability.csv：未来数据不改变历史状态的检查。
- versions/：每个版本的独立结果目录。