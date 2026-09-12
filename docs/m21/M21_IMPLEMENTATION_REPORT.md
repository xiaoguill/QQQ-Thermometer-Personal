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
| VIX | Cboe 官方历史 CSV | 真实读取成功，首日 `1990-01-02`，末日 `2026-09-11` |
| VIX3M | Cboe 官方历史 CSV | 真实读取成功，首日 `2009-09-18`，末日 `2026-09-11` |

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
- Action 仍上传 Evidence，并在最后让失败状态可见；不创建券商订单。

## 测试结果

最后一轮本地回归：

```text
274 passed, 25 subtests passed
```

另外已通过：

- M21 源适配器、VXX 分类、VIX3M 缺失的负向测试；
- M21 配置的布尔值和整数边界测试；
- Python 编译检查；
- M21 JSON 配置、任务合同和文档注册表解析；
- GitHub workflow YAML 解析；
- 无 key 的真实 Cboe/Massive 探测；
- 本地 CSV 回放的失败关闭检查。

本地旧 CSV 的额外发现是：VXX 的列名 `adj_close` 已通过配置显式映射，但旧价格文件仍缺少 VOO、SPY。因此本地回放也会正确停止，不会把不完整文件当成完整历史。

## Evidence 位置

无 key 的真实源探测 Evidence：

`D:\Backup\Documents\量化回测\_quant_artifacts\m21-live-source-probe-20260912\availability_evidence.json`

本地 CSV 失败关闭 Evidence：

`D:\Backup\Documents\量化回测\_quant_artifacts\m21-local-replay-20260810-rerun2\availability_evidence.json`

设置 key 后，GitHub Action 的 Artifact 应至少包含 `availability_evidence.json`、`provider_manifest.json`、`decision.json`、`run_metadata.json`、`replay/` 和 `email_preview.txt`。

## 下一步

1. 在 GitHub Actions Secret 中设置 `MASSIVE_API_KEY`，不要发到聊天。
2. 手动运行 M21，先不发邮件，下载 Artifact 检查十个 ETF 和 VIX/VIX3M。
3. 如果 VXX 分类是 `permission` 或 `interface_or_symbol`，暂停调仓并按分类处理；不替换 VXX。
4. 只有所有序列完整且 `decision.json` 为 `READY` 时，才做一次测试邮件。
5. 新 Candidate 通过治理审阅并提升 Trusted baseline 后，再进行远程 Evidence 验证。
