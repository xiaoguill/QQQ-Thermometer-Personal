# QQQ 并行开发与独立验收边界

本项目允许多个 Codex 聊天框同时推进不同模块，但并行的最小单位是“独立 Worktree + 独立 Candidate Commit”，不是同一个文件夹中的多个会话。

## 角色

| 角色 | 可写范围 | 结果状态 |
| --- | --- | --- |
| `frontend_builder` | `frontend/`、`tests/frontend/` | `READY_FOR_VERIFICATION` |
| `backend_builder` | `src/api/`、`src/storage/`、`src/jobs/`、后端测试目录 | `READY_FOR_VERIFICATION` |
| `domain_builder` | `src/thermometer/`、领域测试 | `READY_FOR_VERIFICATION` |
| `integrator` | 合并候选所需的业务目录和测试，但不能写 Contract、Verification 或 frozen config | `READY_FOR_VERIFICATION` |

`OWNERSHIP.yaml` 是机器可读的路径所有权合同。普通 Builder 不能修改它、`contracts/`、`verification/`、`configs/frozen/` 或验证 workflow。Harness 会根据 Candidate 相对 Trusted ref 的实际内容变化执行 `ownership_scope`；越权直接 `BLOCKED`。

## Worktree 操作

从个人仓库的 Trusted baseline 开始，每个聊天使用独立目录：

```powershell
git worktree add ..\QQQ-Thermometer-Personal-frontend -b codex/frontend verification-baseline-v1
git worktree add ..\QQQ-Thermometer-Personal-backend -b codex/backend verification-baseline-v1
git worktree add ..\QQQ-Thermometer-Personal-integration -b codex/integration verification-baseline-v1
```

Builder 读取 `contracts/openapi.json`，但不能自行修改接口语义。完成后提交 Candidate SHA，只报告改动范围和测试命令。Integrator 取得前端和后端 Candidate SHA 后合并到 integration worktree，再交给独立 Harness。

## 冻结 Contract

`contracts/openapi.json` 是前后端协作接口，当前版本为 `1.0.0`，服务模式为 `local_paper_only`。响应必须包含 `contract_version`、`strategy_version`、`as_of`、`signal_date`、`execution_date`、`data_quality` 和 `run_id`。

前端只能展示后端结果，不复制策略公式、阈值或权重；后端必须实现 Contract，不把客户端传入的颜色、温度或目标权重当作事实；`/api/paper/confirm` 只能记录纸上确认，不得创建真实订单。

Contract 变更必须单独建立 Verification Change Task，同时更新版本、Golden/Negative 数据、受保护 Manifest 和远端 Trusted baseline。不能由普通前端或后端 Candidate 顺手修改。

## 验收顺序

```text
Frontend Candidate SHA ─┐
                        ├─ Integration Candidate SHA
Backend Candidate SHA ──┘
                                  ↓
                       Scope / Ownership Gate
                                  ↓
                    Protected Integrity + Contract Gate
                                  ↓
                 Developer + Independent Unit/Integration/E2E
                                  ↓
                    Golden + Negative + Fault Injection
                                  ↓
                    Candidate SHA + CI Evidence Artifact
                                  ↓
                       VERIFIED / REJECTED
```

Builder 不能声明 `VERIFIED`。本地 Harness 通过只能记为 `LOCAL_VERIFIED`；只有远端干净 checkout 按完整 Candidate SHA 运行、Evidence Artifact 被保留并可追溯，才允许记为 `CI_VERIFIED`。

当前 `frontend/demo` 是可访问的 Northstar 纯前端演示（`http://127.0.0.1:8765/`），它不代表后端 API 已经实现。`src/api/` 仍是后端预留边界，因此当前先冻结 Contract 和治理机制，再分别实现前端和后端。
