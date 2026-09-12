# M21 实现与数据源审计报告

## 结论

M21 已完成第一版实现，状态为 `READY_FOR_VERIFICATION`，但仍需要新的 Trusted baseline 审阅后才能进入远程验证；不能称为 `VERIFIED`、`CI_VERIFIED` 或正式产品基线。

M21 是新增的只读收盘数据链路，不是新策略。它固定调用现有的：

- 策略：`v10_preserve_shock_recovery`；
- 因果回放：`v12.2-causal-walk-forward/v1`；
- 执行边界：`paper_only=true`、`execution_allowed=false`。

## 数据源审计

| 序列 | M21 来源 | 当前验证结果 |
|---|---|---|
| QQQ、QLD、VXX、SVXY、BIL、TLT、IAU、XLU、VOO、SPY | Massive Stocks 日线、`adjusted=true` | 未提供 key 时统一为 `MISSING_API_KEY`；设置 GitHub Secret 后才可验证权限和标的响应 |
| VIX | Cboe 官方历史 CSV | 原始文件覆盖始于 `1990-01-02`；本次滚动请求 Evidence 为 `2024-09-12` 至 `2026-09-11` |
| VIX3M | Cboe 官方历史 CSV | 原始文件覆盖始于 `2009-09-18`；本次滚动请求 Evidence 为 `2024-09-12` 至 `2026-09-11` |

Cboe 的两个序列分开请求、分开 hash、分开记录，不依赖 Massive 指数权限。VIX3M 如果未来不可用，保持缺失并关闭本次调仓；不会用 VIX、SVXY 或 BIL 替代。

## VXX 问题定位

当前真实探测没有暴露 API key，因此 Evidence 中 VXX 的结论是：

```text
failure_class=credentials
failure_code=MISSING_API_KEY
```

这只能说明“尚未完成授权测试”，不能证明 VXX 被供应商拒绝。M21 还通过离线故障注入覆盖了：

- `permission`：Massive 明确返回 `NOT_ENTITLED`；
- `interface_or_symbol`：接口或标的返回 404；
- `symbol_contract`：配置未声明 VXX 或响应 ticker 不匹配；
- `data_quality`：缺收盘、重复或无效数据。

因此，设置 key 后要以 `availability_evidence.json.vxx_diagnostic` 为准，不根据截图或猜测替换标的。

## 调度与失败关闭

- GitHub Action 每个美股交易日只安排一次：北京时间周二至周六 08:23；
- 这个时间对应美国周一至周五收盘后的早晨，周一不运行以避免重复处理上周五；
- 只读取完整日线，不读取盘中实时行情；
- 十个 ETF、VIX、VIX3M 任一个失败、过期或缺少请求收盘日时，目标仓位为空，并写入：`数据不完整，本次不调仓。`；
- 共同有效窗口内的每一个预期 NYSE 交易日都必须有对应序列；共同窗口之前不算缺失，窗口内部缺口会记录在 `session_completeness`，并按数据质量失败关闭；
- Action 仍上传 Evidence，并在最后让失败状态可见；不创建券商订单。

## 测试结果

最后一轮本地回归：

```text
277 passed, 25 subtests passed
```

另外已通过：

- M21 源适配器、VXX 分类、VIX3M 缺失的负向测试；
- M21 专项测试共 17 项，全部通过；
- M21 配置的布尔值和整数边界测试；
- Python 编译检查；
- M21 JSON 配置、任务合同和文档注册表解析；
- GitHub workflow YAML 解析；
- 无 key 的真实 Cboe/Massive 探测；
- 本地 CSV 回放的失败关闭检查。

## GitHub Action 实跑验证

已在独立分支手动运行一次不发邮件测试：

- 运行编号：`34685643553`；
- 分支：`codex/m21-free-close-data`；
- 结果：按预期失败关闭，`failure_code=MISSING_API_KEY`；
- VXX：`credentials / MISSING_API_KEY`，不是 `permission` 或 `symbol_contract`；
- `decision.json.manual_action`：`数据不完整，本次不调仓。`；
- Artifact 已成功上传，包含 `availability_evidence.json`、`decision.json`、`provider_manifest.json` 和 `run_metadata.json`；
- 运行环境显示 `MASSIVE_API_KEY` 为空，说明 GitHub Repository Secret 尚未设置；本次没有进行真实 VXX 授权测试。

远程 Artifact 已下载到仓库外的 Evidence archive，并完成 key 暴露扫描：

`D:\Backup\Documents\量化回测\_quant_artifacts\m21-github-run-34685643553`

本地旧 CSV 的额外发现是：VXX 的列名 `adj_close` 已通过配置显式映射，但旧价格文件仍缺少 VOO、SPY。因此本地回放也会正确停止，不会把不完整文件当成完整历史。

### 已设置 Secret 后的首次实跑

随后在 Secret 名称修正为 `MASSIVE_API_KEY` 后，远程运行 `34696224606` 完成了真实数据读取：

- Massive 的十个 ETF 均返回 `status=success`，包括 VXX；VXX 的诊断结果为 `outcome=available`，因此本次已经排除“缺少 key”和“VXX 不被接口识别”这两个原因；
- VIX、VIX3M 均从 Cboe 官方 CDN 返回成功；
- 运行请求区间为 `2024-09-12` 至 `2026-09-11`，数据完整性检查通过，Evidence 已上传；
- 首次回放仍返回 `DATA_ERROR / RUNNER_ERROR`，不是数据权限失败。直接堆栈定位为：首次信号前的上下文少于 126 个 QQQ 交易日，M04 指标在 warm-up 阶段提前计算了 126 日动量并触发 `IndexError`；
- 这说明 Secret 和 VXX 数据链路已经打通，但暴露了“免费滚动窗口没有覆盖策略预热期”的程序边界问题。

已在本 Candidate 中做最小修复：

- 中间验证 Candidate 曾将 M21 免费收盘请求窗口临时调整为 1000 个自然日，以验证预热问题；该版本随后被新的提供方窗口边界规则取代；
- 让 5/10/20/126 日指标在 warm-up 不足时先返回空值，不触发 Python 负索引或越界；这只修正计算边界，不改指标定义、阈值、状态或权重；
- 动态计算 160 个交易日预热后的首个 indicator-ready 日期，不硬编码某个日期；
- 新增短上下文与提供方边界回归测试，验证预热状态显式保留且不读取未来价格。

修复后的本地完整历史回放已通过：

- `status=READY`、`decision_eligible=true`、`data_quality=OK`、`normalization_quality=OK`；
- 信号日期 `2026-08-10`，执行日期 `2026-08-11`；
- 当前信号目标为 `QQQ 60% + BIL 40%`；
- `279 passed, 25 subtests passed`（使用 importlib 模式避免历史测试文件同名造成的收集冲突）。

### “两年免费窗口视为完整数据”的边界修正

随后将免费请求窗口扩大到 1000 个自然日做预热验证时，运行 `34697386124` 暴露了第二个问题：Massive 对十个 ETF 实际只返回 `2024-09-12` 至 `2026-09-11` 的 501 根日线；VIX/VIX3M 能返回更早数据。旧逻辑却把 `2024-01-01` 至 `2024-09-11` 当成必须由每条 ETF 填满的区间，于是错误地产生 `MISSING_SESSION`。

这不是 API key、VXX 权限或 VXX symbol contract 问题，而是“请求窗口”和“提供方实际可用窗口”混用。根据本项目的数据边界，约两年的免费返回窗口应作为完整数据集；但不能隐藏边界。因此 M21 已改为：

- 默认只请求 730 个自然日，避免把更早、实际不可见的日期当成回测样本；
- 保存 `requested_start_date` 与每条序列的 `first_date`；
- 将所有必需序列首日中的最晚日期记录为 `effective_start_date`；
- 只对 `effective_start_date` 至最新收盘做交易日完整性检查；共同窗口内有缺口仍失败关闭；
- 160 个交易日只作 v12.2 的因果预热（150 个滚动指标窗口加 10 个 EMA 状态承载位置），首个可用信号日动态计算，预热期不计入收益、不生成调仓目标；
- 不补充更早本地数据，不把 VIX、SVXY 或 BIL 当成 VXX 替代品。

因此，报告中必须把“免费数据完整”准确表述为“提供方实际返回的约两年共同窗口完整”，不能表述为“从 2025-01-01 起已经拥有足够历史”。若用户要求从 `2025-01-01` 计算收益，免费窗口不足以提供该日之前的 160 个交易日上下文时，程序只能把 2025 年初标记为预热，实际策略统计从动态的首个 indicator-ready 日期开始；否则就需要另一条明确授权的历史数据源。

### 本地重放验证结果

使用远程运行 `34698326136` 下载的真实输入文件进行本地重放，验证了边界修正后的完整链路：

- 共同窗口：`2024-09-12` 至 `2026-09-11`，501 个 NYSE 交易日；
- 首个 indicator-ready 日期：`2025-05-02`，由 160 个交易日预热动态计算；
- 结果：`status=READY`、`decision_eligible=true`、`data_quality=OK`、`normalization_quality=OK`；
- 最新信号：`2026-09-11`，下一执行日 `2026-09-14`；
- 目标仓位：`QQQ 60% + BIL 40%`；
- 输出的 5/10/25 bps 成本情景与 QQQ 基准均已写入本地 Evidence，未改变 v10/v12.2 策略规则。

本地重放只用于验证程序逻辑；随后已在 GitHub 上用同一 Candidate 重新运行无邮件 Action，确认 Linux Runner 与真实 Secret 环境一致。

### 最终 GitHub 真实数据验证

同一 Candidate `75908d3` 已在 GitHub Action `34698880318` 完成一次不发邮件的真实验证：

- `MASSIVE_API_KEY` 已被实际使用，且没有写入日志或 Artifact；
- 十个 ETF（含 VXX）均为 `success`，共同窗口为 `2024-09-12` 至 `2026-09-11`，每个 ETF 返回 501 根日线；
- VIX 与 VIX3M 均从 Cboe 官方源成功读取；
- `effective_start_date=2024-09-12`，共同窗口预期 501 个 NYSE 交易日，QQQ 等 ETF 的 `missing_session_count=0`；
- `vxx_diagnostic.outcome=available`，没有发生 VXX 替代；
- `decision.status=READY`、`decision_eligible=true`、`data_quality=OK`、`normalization_quality=OK`；
- 首个 indicator-ready 日期为 `2025-05-02`，最新信号日期为 `2026-09-11`，下一执行日为 `2026-09-14`；
- 最新状态为 `normal`、温度 80，纸上目标为 `QQQ 60% + BIL 40%`；
- 邮件步骤按手动输入正确跳过，Artifact 上传成功，失败可见性检查通过。

本次远程 Artifact 已下载并保存在：

`D:\Backup\Documents\量化回测\_quant_artifacts\m21-github-run-34698880318`

这证明“约两年的免费数据作为完整数据窗口”的实现、真实 Secret、VXX 契约、预热处理和 v12.2 回放链路在 GitHub Runner 上能够连通。它仍然只是 M21 Candidate 的一次运行证据，不等于策略收益保证，也不等于已经提升为 Trusted baseline。

下一步不是继续修改策略，而是按治理流程审阅 Candidate `75908d3` 和运行 `34698880318` 的 Evidence；审阅通过后才能提升新的 Trusted baseline，再运行正式 GitHub CI。当前 M21 已满足“数据源成功、共同窗口完整、回放 READY、Evidence 上传成功”的 Candidate 验证条件，但在此之前仍不称为 `CI_VERIFIED`。

## Evidence 位置

无 key 的真实源探测 Evidence（最新窗口、契约分类与交易日缺口检查版）：

`D:\Backup\Documents\量化回测\_quant_artifacts\m21-live-source-probe-20260912-contractfix-v3\availability_evidence.json`

早期无 key 探测的邮件预览仍保留在：

`D:\Backup\Documents\量化回测\_quant_artifacts\m21-live-source-probe-20260912\email_preview.txt`

本地 CSV 失败关闭 Evidence：

`D:\Backup\Documents\量化回测\_quant_artifacts\m21-local-replay-20260810-rerun2\availability_evidence.json`

设置 key 后，GitHub Action 的 Artifact 应至少包含 `availability_evidence.json`、`provider_manifest.json`、`decision.json`、`run_metadata.json`、`replay/` 和 `email_preview.txt`。本次成功 Artifact 为 `m21-github-run-34698880318`。

## 下一步

1. 审阅 Candidate `75908d3`、运行 `34698880318` 及其 Artifact。
2. 如治理审阅通过，再单独提升新的 Trusted baseline；不覆盖旧基线。
3. 提升基线后再运行正式 GitHub CI；在此之前 M21 只作为 Candidate 使用。
4. 需要发邮件时另行配置邮件 Secret，并先做测试邮件；当前仍不自动下单。
