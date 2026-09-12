# M21：免费收盘数据优先运行手册

## 目的

M21 是独立于 M18、M19、M20 的只读运行版本。它每天只运行一次，在北京时间早上读取上一美股完整收盘，重新执行同一条 v12.2 因果回放链，并生成纸上目标与白话邮件。

M21 不改变策略。实际策略仍是 `v10_preserve_shock_recovery`，回放标签仍是 `v12.2-causal-walk-forward/v1`。

## 数据来源

| 数据 | 来源 | 处理方式 |
|---|---|---|
| QQQ、QLD、VXX、SVXY、BIL、TLT、IAU、XLU、VOO、SPY | Massive Stocks 日线聚合 | `adjusted=true`，只接受完整收盘日 |
| VIX | Cboe 官方历史 CSV | 读取 `CLOSE`，不需要 Massive 指数权限 |
| VIX3M | Cboe 官方历史 CSV | 能读取就使用；不能读取或缺少当日值就保持缺失 |

Cboe 官方页面说明其 VIX 历史页面提供每日收盘值；M21 使用其公开 CDN 文件，并在每次运行保存 URL、文件 hash、首末日期和可见行数：

- <https://www.cboe.com/tradable_products/vix/vix_historical_data>
- <https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv>
- <https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX3M_History.csv>

Massive 的股票 Basic 计划是否能读取每一个 ETF、历史窗口和当日收盘，以账户实际返回为准。免费计划的调用限制也可能导致 `RATE_LIMITED`；M21 会用固定请求间隔降低频率，但不会无限重试或切换到付费数据源。

当前默认请求窗口为 1000 个自然日。原因不是扩大回测样本，而是给 2025-01-01 开始的逐日回放提供 150 日均线和 126 日动量的因果预热；最终回放和邮件仍从配置的 `replay.start_date` 开始。实际请求起止日期会写入 Evidence。

## 第一次配置

1. 在 Massive 账户创建 API key。
2. 在 GitHub 仓库的 `Settings → Secrets and variables → Actions` 中新增 Secret：

   | 名称 | 内容 |
   |---|---|
   | `MASSIVE_API_KEY` | Massive API key |

3. 不要把 key 发到聊天、写入 JSON、代码、截图、日志或 Artifact。
4. 如果要发邮件，再配置 M20 已定义的邮件 Secret：`EMAIL_API_KEY`、`EMAIL_API_FROM`、`QQQ_EMAIL_TO`，并将 Variable `EMAIL_API_PROVIDER` 设置为 `resend`、`brevo` 或 `sendgrid`。

本地 CSV 回放的 VXX 列名在 `configs/m21/free_close.json` 中明确写为 `adj_close`；这只是列名映射，不是把其他标的当成 VXX。请求窗口之外的历史行不会参与本次检查，窗口内的空值仍会导致失败关闭。
窗口内的每一个预期 NYSE 交易日也必须存在；内部缺口会记录在 `session_completeness`，并按数据不完整处理。

## 手动测试顺序

在 GitHub 的 `Actions → QQQ Free Close Daily Read-only → Run workflow`：

1. 先不发送邮件，填写一个 `as_of_date` 或留空，运行一次。
2. 下载 Artifact，先看 `availability_evidence.json`。
3. 确认十个股票标的和 VIX/VIX3M 都有 `status=success`、`has_requested_end_date=true`。
4. 再检查 `decision.json`：成功必须是 `status=READY`、`decision_eligible=true`；失败必须是空目标。
5. 最后再勾选 `send_email` 和 `test_email` 做一次邮件测试。

## VXX 问题如何判断

`availability_evidence.json` 的 `vxx_diagnostic` 是判断 VXX 问题的唯一入口：

| 分类 | 含义 | 应该怎么做 |
|---|---|---|
| `credentials` | 没有提供 Massive key，本次没有完成权限测试 | 设置 Secret 后重跑 |
| `permission` | Massive 明确返回 401/403 或 `NOT_ENTITLED` | 检查账户股票历史权限；不替换 VXX |
| `interface_or_symbol` | 404、接口返回找不到标的 | 检查 endpoint、ticker 和供应商返回格式 |
| `symbol_contract` | 配置没有声明 VXX，或返回的 ticker 不是 VXX | 修复配置/响应契约；不把别的标的当 VXX |
| `data_quality` | 有响应但缺收盘日、重复、无效或过期 | 保留 Artifact，数据完整前不调仓 |

因此，旧版出现的 `unsupported symbols: ['VXX']` 可以被归入程序 symbol contract 层；如果新版本拿到的是 `NOT_ENTITLED`，才是供应商权限层。没有真实 key 的运行，只能标记“未测试”，不能臆断是哪一层。

## 失败关闭规则

只要十个股票序列、VIX 或 VIX3M 任意一个缺失、过期、接口失败或没有请求收盘日：

```text
数据不完整，本次不调仓。
```

目标仓位必须为空。特别是：

- VIX3M 缺失不能用 VIX 替代；
- VXX 缺失不能用 VIX、SVXY 或 BIL 替代；
- 不能用上一天旧值冒充今天收盘；
- 不能用盘中价格确认收盘策略；
- 不能自动下单。

## 每日调度

工作流只有一个定时点：

| 时间 | 作用 |
|---|---|
| 北京时间周二至周六 08:23 | 读取上一美股完整收盘，计算目标并发送早间邮件 |

周六早上用于发送美国周五收盘结果；周一不运行，避免把周五收盘重复处理一次。运行窗口仍以 `configs/m21/free_close.json` 为准；修改时间只改配置并产生新 commit，不改策略。

## Evidence 必须包含

- `availability_evidence.json`：逐标的结果和 VXX 分类；
- `provider_manifest.json`：来源、请求区间、hash、价格口径；
- `decision.json`：状态、温度、策略版本、数据质量、目标仓位；
- `run_metadata.json`：运行时间、commit 和配置摘要；
- `replay/`：v12.2 因果回放报告与检查；
- `email_preview.txt`：实际邮件正文，不包含任何 key。

## 修改和回溯

- 只改数据源 URL、邮箱或时间：创建新的 M21 commit，重新跑手动测试。
- 改指标、状态、阈值、资产权重或执行时点：必须创建新的策略版本和完整历史报告。
- 不覆盖 M18、M19、M20、冻结策略或旧 Artifact。
- 每次结束检查 `git status --porcelain` 为空，并记录 commit、数据截止日和回滚点。
- M21 在新 Candidate 通过独立测试和远程 Evidence 之前，不称为 `CI_VERIFIED` 或正式产品基线。
