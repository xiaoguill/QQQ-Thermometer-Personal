# Task Contracts

`tasks/<task_id>.json` 是当前工作单元的机器可读合同。它不是策略真源，也不能覆盖 `AGENTS.md`、`OWNERSHIP.yaml`、冻结策略契约、API 契约或 Verification Policy。

## 启动顺序

```text
AGENTS.md
  → AI_CONTEXT_ROUTER.json
  → tasks/<task_id>.json（来自 Trusted ref）
  → 局部 AGENTS.md
  → required_context
  → git status / HEAD
  → allowed_write_paths
  → acceptance_gates
```

## 关键字段

- `task_id`：稳定的任务标识，必须由 CI/验证命令显式传入。
- `role`：必须与 `OWNERSHIP.yaml` 中的角色一致。
- `required_context`：任务开始前必须存在并可读取的文件。
- `allowed_write_paths`：本任务允许的精确路径或目录前缀。
- `forbidden_write_paths`：即使角色允许，也不能修改的路径。
- `invariants`：本任务不能改变的策略、时点、权限或运行边界。
- `acceptance_gates`：任务完成时必须通过的机械门槛。
- `stop_conditions`：遇到冲突、缺失或越权时必须停止。

## 信任规则

正常验证时，Task Contract 必须从 Trusted ref 读取，Candidate 不能用自己修改后的合同扩大权限。新增或修改 Task Contract 属于治理变更，需要单独的治理任务和新的 Trusted baseline。

普通模块不允许修改 `tasks/`、`AI_CONTEXT_ROUTER.json`、`docs/DOCUMENT_REGISTRY.json` 或 `verification/`。治理变更也必须保留策略真源、API 真源和真实交易边界不变。
