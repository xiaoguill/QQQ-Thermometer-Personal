# QQQ Thermometer Personal Workspace

这是个人使用版的隔离工作区，目标是逐步构建一个低频、可解释、可回溯的美股风险温度计与仓位决策系统。

## 当前边界

- 本目录从已提交基线 `ce7f93c` 建立，原 `FinRL-Trading` 目录保持不动。
- 当前只保存治理文件、模块边界、研究基线和 v10 候选的静态参考快照。
- v10 仍是研究候选，不是自动交易默认策略，也没有授权自动下单。
- 运行产生的 `work/`、`artifacts/runs/` 和新的回测输出不应进入 Git。

## 从哪里开始阅读

1. [AGENTS.md](AGENTS.md)：仓库总规则与 Codex 工作方式。
2. [docs/PERSONAL_WORKSPACE_INDEX.md](docs/PERSONAL_WORKSPACE_INDEX.md)：个人版文件索引。
3. [docs/PERSONAL_USE_GOVERNANCE.md](docs/PERSONAL_USE_GOVERNANCE.md)：个人使用边界。
4. [docs/QQQ_THERMOMETER_ARCHITECTURE.md](docs/QQQ_THERMOMETER_ARCHITECTURE.md)：后端、指标、策略和前端的边界。
5. [docs/QQQ_THERMOMETER_MODULE_PLAN.md](docs/QQQ_THERMOMETER_MODULE_PLAN.md)：分模块实施顺序。
6. [docs/V10_CANDIDATE_CONTRACT.md](docs/V10_CANDIDATE_CONTRACT.md)：当前 v10 候选的冻结规则和证据入口。

## 回溯方式

每个可运行阶段都应先创建一个小提交，并在提交信息中写明数据窗口、信号时点、执行延迟、成本假设和验证结果。恢复流程见 [docs/WORKTREE_RESTORE_PROTOCOL.md](docs/WORKTREE_RESTORE_PROTOCOL.md)。

本目录用于研究、观察和人工决策辅助；在没有完成数据校验、前缀稳定性、成本压力测试和纸面观察期前，不应把任何候选策略接入真实下单。
