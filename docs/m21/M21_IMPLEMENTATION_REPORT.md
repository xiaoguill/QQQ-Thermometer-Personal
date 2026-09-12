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
- 请求窗口内的每一个预期 NYSE 交易日都必须有对应序列；内部缺口会记录在 `session_completeness`，并按数据质量失败关闭；
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

- 将 M21 免费收盘请求窗口调整为 1000 个自然日，使 2025 年回放前有足够的 150 日均线与 126 日动量预热数据；
- 让 5/10/20/126 日指标在 warm-up 不足时先返回空值，不触发 Python 负索引或越界；这只修正计算边界，不改指标定义、阈值、状态或权重；
- 新增短上下文回归测试，验证预热状态显式保留且不读取未来价格。

修复后的本地完整历史回放已通过：

- `status=READY`、`decision_eligible=true`、`data_quality=OK`、`normalization_quality=OK`；
- 信号日期 `2026-08-10`，执行日期 `2026-08-11`；
- 当前信号目标为 `QQQ 60% + BIL 40%`；
- `279 passed, 25 subtests passed`（使用 importlib 模式避免历史测试文件同名造成的收集冲突）。

下一步是在新 Candidate 推送后重新运行一次无邮件 Action。只有新运行同时满足“所有源成功、回放 READY、Evidence 上传成功”时，才可把 M21 标记为候选验证通过；在治理审阅和 Trusted baseline 更新前，仍不称为 `CI_VERIFIED`。

## Evidence 位置

无 key 的真实源探测 Evidence（最新窗口、契约分类与交易日缺口检查版）：

`D:\Backup\Documents\量化回测\_quant_artifacts\m21-live-source-probe-20260912-contractfix-v3\availability_evidence.json`

早期无 key 探测的邮件预览仍保留在：

`D:\Backup\Documents\量化回测\_quant_artifacts\m21-live-source-probe-20260912\email_preview.txt`

本地 CSV 失败关闭 Evidence：

`D:\Backup\Documents\量化回测\_quant_artifacts\m21-local-replay-20260810-rerun2\availability_evidence.json`

设置 key 后，GitHub Action 的 Artifact 应至少包含 `availability_evidence.json`、`provider_manifest.json`、`decision.json`、`run_metadata.json`、`replay/` 和 `email_preview.txt`。

## 下一步

1. 重新推送已修复的 M21 Candidate，并手动运行一次不发邮件 Action。
2. 下载 Artifact，确认十个 ETF 和 VIX/VIX3M 的数据区间覆盖预热期，且 `decision.json` 为 `READY`。
3. 如果 VXX 分类是 `permission` 或 `interface_or_symbol`，暂停调仓并按分类处理；不替换 VXX。
4. 只有所有序列完整且回放成功时，才做一次测试邮件。
5. 新 Candidate 通过治理审阅并提升 Trusted baseline 后，再进行远程 Evidence 验证。
