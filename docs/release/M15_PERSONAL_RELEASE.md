# M15 个人私有版发布与全链路验收

本文件是 M15 的发布运行手册，不是公网部署说明，也不是实盘授权。M15 只验证现有 M01–M14 组件在个人本地、低频、纸上组合边界内可以被重放、备份、恢复、暂停和回滚。

## 发布范围

| 项目 | 冻结口径 |
| --- | --- |
| 策略版本 | `v10_preserve_shock_recovery`；当前仍是 `research_candidate`，不能改称产品默认 |
| 数据方式 | 版本化的本地/已审计快照；不在 M15 增加外部行情源 |
| 运行频率 | 美股交易日收盘后手动刷新，下一有效交易日只做 paper 模拟 |
| 访问范围 | 本机 `localhost` 或明确的私有网络；可选 `X-QQQ-Local-Token` |
| 交易边界 | `paper_only=true`；不连接券商、不生成订单、不转账、不保存凭证 |
| 页面边界 | 只读展示后端结果；不在浏览器重算策略、收益、回撤或成本 |
| 回滚引用 | `verification-baseline-v3.20`，必须解析到不可变 Git commit |
| 发布状态 | `PAPER_SHADOW` / `CI_VERIFIED` 只能表示验证等级，不代表未来收益或实盘许可 |

## 外部发布清单

每次候选提交都必须在仓库外生成带 SHA 的 release manifest，至少包含：

```json
{
  "release_schema": "qqq-private-release/v1",
  "candidate_sha": "<exact-candidate-commit-sha>",
  "trusted_baseline": "verification-baseline-v3.20",
  "strategy_version": "v10_preserve_shock_recovery",
  "strategy_status": "research_candidate",
  "data_source": "versioned-local-snapshots",
  "frequency": "daily-close-manual-refresh-next-session-paper",
  "access_scope": "localhost-or-private-network",
  "paper_only": true,
  "public_deployment": false,
  "broker_enabled": false,
  "rollback_ref": "verification-baseline-v3.20",
  "disclaimer": "Research and paper-shadow monitoring only; no future-return guarantee or trading authorization."
}
```

`candidate_sha`、Evidence SHA-256、数据 manifest、策略合同 hash 和回滚 commit 必须由发布流程填入，不能用截图或手工改写结果替代。凭证、账户标识、访问 token 的值不得写入 manifest、日志或仓库。

## M15 验收顺序

1. 用同一组版本化输入重放：指标快照 → v10 状态 → 解释 → candidate-only 目标仓位 → paper portfolio → ledger/NAV → read API。
2. 运行至少 20 个连续有效交易日的 paper shadow；每天记录 signal date、execution date、target、模拟成交、成本、持仓、NAV、质量和 reconciliation。
3. 将本地 SQLite store 备份到独立路径，关闭源库后重新打开备份；比较记录内容 hash 和数量。恢复后对源库追加事件，不得改变备份读模型。
4. 验证 API 只绑定 localhost/私有范围；启用 token 时无 token 或错误 token 必须拒绝；禁止绑定 `0.0.0.0`。
5. 注入计算失败、部分质量和进程中断；失败运行不能发布成功快照，重启只能从已持久化阶段恢复，重复请求必须幂等。
6. 验证回滚引用解析到之前的 Trusted commit；回滚通过切换版本/manifest 实现，不删除或覆盖历史账本。
7. 通过本地 Harness 与 GitHub Actions 的精确 SHA 验证后，才可以把候选标记为 `CI_VERIFIED`。这仍不等于 `PRODUCT_BASELINE`，最终个人使用批准必须保留独立记录。

## 停止条件

- 任一日使用未来数据、信号日不早于执行日、数据质量失败却继续发布；
- 备份不是独立可读模型，或恢复后内容不一致；
- 非本地访问、缺失 token、日志泄露凭证；
- paper 结果进入真实订单、券商、转账或自动调度；
- 回滚没有明确的 Trusted ref，或需要删除历史记录；
- 任一受保护测试跳过、失败、SHA 或 Evidence sidecar 不匹配。

M15 完成后只保留可回溯的代码提交、外部 Evidence、release manifest 和报告；不把临时数据库、账户数据或密钥复制进仓库。
