# 个人温度计工作区索引

## 规则与治理

- `AGENTS.md`：全局协作、验证和安全规则。
- `AI_CONTEXT_ROUTER.json`：根据任务路径选择必读上下文和默认角色；不作为策略真源。
- `tasks/`：每个模块的机器可读 Task Contract；当前治理变更为 `tasks/M00.5.json`。
- `docs/DOCUMENT_REGISTRY.json`：文档类型、状态、权威等级和机器真源索引。
- M00.5 smoke candidate：仅验证允许路径与 Task Contract scope，不改变业务代码或策略合同。
- `docs/PERSONAL_USE_GOVERNANCE.md`：个人使用边界、人工确认和数据处理规则。
- `docs/QQQ_THERMOMETER_GOVERNANCE_BOUNDARIES.md`：温度计、策略、订单和数据层的责任边界。
- `docs/QQQ_VERIFICATION_GOVERNANCE_BASELINE.md`：回测和数据复核的最低证据要求。
- `docs/WORKTREE_RESTORE_PROTOCOL.md`：如何保持 worktree 干净并恢复到检查点。
- `docs/REPO_CLEANUP_AUDIT_20260812.md`：原仓库清理审计记录；本目录不覆盖原记录。

## 设计与实施

- `docs/QQQ_THERMOMETER_ARCHITECTURE.md`：目标系统的分层结构。
- `docs/QQQ_THERMOMETER_MODULE_PLAN.md`：按 M00–M15 拆解的实施顺序。
- `src/thermometer/`：温度计核心规则的未来落点。
- `src/api/`：未来的只读查询接口边界。
- `src/web/` 与 `frontend/`：当前旧 Web 入口与未来前端边界，暂不混写。
- `tests/`：数据契约、策略规则和接口测试的预留目录。

## 研究证据

- `research/qqq_drawdown_strategy/`：已提交的历史研究基线和原有测试。
- `docs/reference/v10_preserve_shock_recovery/`：从原仓库研究结果复制的 v10 静态参考快照。
- `artifacts/runs/`：未来运行输出目录，已加入忽略规则，不应提交。

## 文件进入 Git 的条件

只有以下内容可以进入个人版主分支：可复现的源代码、冻结的规则契约、测试、数据字典、摘要级证据和明确的变更记录。单次实验产生的大型 CSV、缓存、临时日志和含密钥文件留在外部运行目录。
