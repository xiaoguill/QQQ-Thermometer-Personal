# M20：GitHub Action 使用手册

这份手册是以后照着做的固定流程。目标是：每天在三个美股节点做一次只读检查，第二天北京时间早上发一封大白话邮件。整个流程仍然是纸上运行，不会自动交易。

## 第 1 步：获取 Massive key

打开 [Massive 官方入口](https://massive.com/)，登录后在账户/API 页面创建 key。key 只复制到 GitHub Secret，不要发到聊天窗口，也不要写入 JSON、`.env`、截图、邮件或代码。

Massive 的免费/基础股票数据额度和 VIX/VIX3M 指数权限是两件事；即使账户能读取股票，指数或 VXX 仍可能返回无权限。M19 遇到这种情况会明确失败，不会用别的标的冒充。

## 第 2 步：在 GitHub 填写 Secrets 和 Variables

进入仓库 `Settings → Secrets and variables → Actions`。

在 **Secrets** 中新增：

| 名称 | 填什么 |
|---|---|
| `MASSIVE_API_KEY` | 第 1 步获得的 Massive key |
| `EMAIL_API_KEY` | 邮件服务商的发送 key；没有邮件服务时可暂时不填 |
| `EMAIL_API_FROM` | 已验证的发件邮箱地址 |
| `QQQ_EMAIL_TO` | 接收邮件的邮箱；多个地址用逗号分隔 |

在 **Variables** 中新增：

| 名称 | 值 |
|---|---|
| `EMAIL_API_PROVIDER` | `resend`、`brevo` 或 `sendgrid` 三选一 |

Massive key 和邮件发送 key 是两种不同的 key。只填 Massive key 可以测试行情和策略；要让次日邮件真正发出，还需要一个允许 HTTPS 发信的邮件服务。

## 第 3 步：先手动测试，不马上等定时

打开仓库的 **Actions → QQQ Read-only Checkpoints → Run workflow**：

1. 第一次选择 `close`，不勾选测试邮件，先确认能读到 QQQ、BIL、VXX、VIX、VIX3M。
2. 再选择 `email`，勾选 `test_email=true`，确认邮箱收到测试信。
3. 下载本次运行的 Artifact，检查 `decision.json`：`status` 应为 `READY`，没有 `MISSING_API_KEY`、`NOT_ENTITLED`、`RATE_LIMITED` 或 `MISSING_SERIES`。
4. 如果第 1 步失败，先看错误代码和数据权限，不要修改策略参数。

手动测试可以填写 `as_of_date` 做历史复现；定时运行不填写它。

## 第 4 步：定时节点

工作流使用 GitHub 的时区字段，因此会自动跟随夏令时：

| 节点 | 时间 | 用途 |
|---|---|---|
| `open` | 纽约 09:37，周一至周五 | 盘中观察，不改目标 |
| `midday` | 纽约 12:17，周一至周五 | 盘中观察，不改目标 |
| `close` | 纽约 16:23，周一至周五 | 收盘后生成下一交易日纸上目标 |
| `email` | 北京 08:23，周一至周六 | 告诉你是否出现目标变化或数据异常 |

周六早上的邮件是有意安排的：它用于发送美国周五收盘后的结果。GitHub 调度可能延迟，程序会检查是否已经过收盘确认时间；没有完整日线就不发布新目标。

## 邮件会长什么样

主题示例：

```text
[QQQ策略] 下一交易日目标有变化｜状态：冲击
```

正文示例：

```text
QQQ 温度计·每日简报

结论：下一交易日的纸上目标有变化，请人工核对后再决定是否调整。

信号日期：2026-08-10
计划执行日：2026-08-11
当前状态：冲击
温度：15/100
趋势：bearish

目标仓位：
- VXX: 25.0%
- BIL: 75.0%

为什么：模型识别到冲击状态，进入防守候选仓位。
数据情况：OK
数据来源：massive
模型版本：v10_preserve_shock_recovery（回放标签 v12.2-causal-walk-forward/v1）

你需要知道：
1. 这是收盘后生成的纸上建议，不是自动下单指令。
2. 程序不会读取券商账户，也不会替你买卖。
3. 目标只使用信号日收盘数据，实际执行日是下一个美股交易日。
4. 如果数据异常，程序会停止发布目标仓位，不会拿 VIX、SVXY 或 BIL 冒充 VXX。
```

如果没有变化，主题会变成“今日无需变更目标”；如果数据异常，主题会变成“数据异常，暂不调仓”，正文只写原因，不给出新仓位。

## 第 5 步：遇到失败时怎么做

先看 Action 的运行摘要和 Artifact 中的 `decision.json`，按下面顺序处理：

1. `MISSING_API_KEY`：检查 Secret 名称是否准确，不能把 key 写进配置。
2. `NOT_ENTITLED`：检查 Massive 账户是否有对应股票/指数历史权限；不要替换 VXX。
3. `RATE_LIMITED`：等待下一次运行，或在配置中降低无必要的重复请求；不要开无限重试。
4. `EMPTY_PAYLOAD`、`MISSING_SERIES`、`INVALID_BAR`：保留 Artifact，先判断数据是否完整。
5. `M20 email failed`：检查邮件服务商、发件地址验证和邮件 Secrets；这不会改变纸上目标。

故障期间的原则是“暂不调仓，先确认数据”，而不是猜一个仓位。修复后先手动跑 `close`，再手动跑测试邮件。

## 第 6 步：以后如何改策略或代码

- 只改数据源、邮箱或时间：修改独立的 M19/M20 配置，重新测试并保留新 commit。
- 改策略指标、状态、阈值或仓位：必须创建新的策略版本和新的历史/样本外报告；不能覆盖 `v10_preserve_shock_recovery`。
- 不修改 M18、M16、旧 Demo、冻结策略合同和旧回测 Artifact。
- 每次完成后检查当前工作区干净，并记录 commit、数据源、数据截止日、成本口径和回滚 commit。
- 不把一次运行的好结果称为“已经验证”或“保证收益”。

## 当前版本和已知回测数字

本流程默认使用：实际策略 `v10_preserve_shock_recovery`，回放标签 `v12.2-causal-walk-forward/v1`。在现有 v12.2 因果审计区间 `2025-01-02` 至 `2026-08-10` 中，5 bps 成本口径的策略 CAGR 为约 `14.72%`、最大回撤约 `-8.35%`；同期 QQQ CAGR 约 `24.66%`、最大回撤约 `-22.77%`。10 bps 和 25 bps 的策略 CAGR 约为 `14.30%` 和 `13.07%`。

这些是有限审计区间的历史结果，不是 2008 年以来的全周期证明，也不是未来收益承诺。M19 的作用是把同一口径每天重新计算并留下证据，防止邮件数字和回测数字混在一起。
