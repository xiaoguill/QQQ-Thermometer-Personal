# Independent Verification Runtime

本目录不是产品运行时，也不是 Builder 的测试目录。它是用于决定任务是否可以从 `IMPLEMENTED` 晋级为 `VERIFIED` 的受保护验证运行时。

## 正常运行

正常验证必须使用两个干净 checkout：

```text
trusted_repo   = 受保护的 verification baseline checkout
candidate_repo = 待验证的精确 Candidate Commit checkout
candidate_role = 由任务声明的 Builder 角色或 integrator
```

由 trusted checkout 中的 `verification/cli.py` 启动：

```powershell
$python = "C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$candidate = (git -C . rev-parse HEAD)
& $python -I verification/cli.py run `
  --trusted-repo <trusted-checkout> `
  --candidate-repo <candidate-checkout> `
  --trusted-ref <protected-baseline-sha> `
  --candidate-sha $candidate `
  --candidate-role integrator `
  --output <external-evidence-directory>
```

Candidate 代码只在隔离 Python 子进程中运行。Golden、Negative、受保护清单和 Gate 逻辑来自 trusted checkout，Candidate 不能提供 expected 值，也不能修改这些文件后继续通过。

## 状态规则

只有以下事实同时成立，运行器才返回 0 并在 Evidence 中写入 `status_transition=VERIFIED`：

- candidate SHA 是 candidate checkout 的完整 HEAD SHA；
- trusted 与 candidate checkout 都干净；
- Candidate 角色在 trusted `OWNERSHIP.yaml` 中定义，且实际变更路径没有越权；
- 受保护路径与 trusted baseline 字节级一致；
- Developer Tests 真实执行并有非零测试数量、零失败、零跳过；
- Golden、Negative、Fault Injection 和三层验证门全部通过；
- 受控环境信息、标准输出哈希、受保护 Artifact 哈希和 Evidence 哈希已记录。

任何声明、总结、截图或“已完成”文字都不会进入晋级逻辑。

状态晋级只能通过哈希校验后的 Evidence bundle：

```powershell
python -I verification/cli.py promote `
  --current-status IMPLEMENTED `
  --evidence <external-evidence-directory>/evidence.json `
  --checksum <external-evidence-directory>/evidence.sha256
```

直接修改 `evidence.json`、缺少 sidecar checksum、修改 Candidate SHA、失败测试数或 Gate 结果，均返回 `UNVERIFIED` 和非零退出码。

## 本地与 CI 状态

本地 Trusted Harness 通过只代表 `LOCAL_VERIFIED`；只有在 CI 的干净 checkout 中按 Candidate SHA 运行并生成保留的 Evidence，外部流程才可以把它标记为 `CI_VERIFIED`。本运行时默认只签发机械状态 `VERIFIED`，不会把本地结果冒充为 `CI_VERIFIED`。

## Bootstrap 限制

第一次建立 baseline 时可以使用 `--bootstrap`，但这不是独立验收的替代品。Bootstrap commit 必须由仓库外的用户/分支保护/CI 规则确认并固定为受保护 ref。正常运行拒绝 `trusted_ref == candidate_sha`，避免 Candidate 自己信任自己。

本地 Evidence 具备内容哈希、Git blob 可追溯和只读文件属性；真正不可由 Builder 改写的长期 Evidence 必须由 CI artifact retention、受保护分支或外部审计存储保存。
