# FinRL-Trading 仓库清查报告（2026-08-12）

## 1. 结论先行

当前 worktree **不是干净状态**，但当前不能把所有未跟踪文件都称为“多余文件”。仓库混合了三类内容：原始 FinRL 基础项目、QQQ/QLD 研究实验、研究运行生成物。直接执行 `git clean` 或批量删除会丢失可复现证据，当前不应执行。

明确可以重建、优先清理的内容：

- `src/**/__pycache__/` 中已经被 Git 跟踪的 `.pyc` 文件；
- `research/qqq_drawdown_strategy/__pycache__/`；
- 仓库内 `work/` 的运行时产物；
- `one_year_test/` 等临时测试输出，但要先确认没有作为报告证据。

暂时不能直接删除的内容：

- `research/qqq_drawdown_strategy/output/` 与 70 个 `output_*` 目录；
- `archive/20260811_before_dd20/`；
- `work/recovery_acceleration_lab_v2_20260812/`；
- 三个已跟踪但被修改的行情文件；
- 原 FinRL 的 `data/`、`src/trading/`、Docker/部署和示例文件。

## 2. 清查快照

检查对象：`D:\Backup\Documents\量化回测\FinRL-Trading`

| 项目 | 结果 |
|---|---:|
| 当前分支 | `codex/long-bear-v2` |
| HEAD | `ce7f93c add boxx and sgov cash proxy sensitivity` |
| 已跟踪文件总数 | 126 |
| 已修改的已跟踪文件 | 3 |
| 未跟踪文件 | 1,646 |
| Git 状态中显示的未跟踪路径 | 224（目录会折叠显示） |
| 研究目录内 `output*` 目录 | 70 个，约 101.6 MB |
| 仓库内 `work/` | 约 635 个文件，约 161.4 MB |
| `archive/20260811_before_dd20` | 约 143 个文件，约 28.4 MB |
| `one_year_test/` | 约 20 个文件，约 2.3 MB |
| 研究目录 `__pycache__` | 约 112 个文件，约 2.1 MB |

当前三个已跟踪修改为：

```text
research/qqq_drawdown_strategy/output/data_metadata.json
research/qqq_drawdown_strategy/output/prices_adj_close.csv
research/qqq_drawdown_strategy/output/vix_indices.csv
```

它们不是普通临时文件，可能代表上一轮数据刷新。未核对 Alpaca/本地基线前，不应覆盖、回退或提交。

## 3. 文件分类

| 类别 | 典型路径 | 当前判断 | 建议 |
|---|---|---|---|
| 个人温度计核心 | `src/web/`、未来 `src/thermometer/`、回放测试、冻结配置 | 保留 | 作为最终个人版核心 |
| 当前研究证据 | `output/`、候选报告、v10 `work/recovery_acceleration_lab_v2_20260812/` | 保留但需登记 | 通过 manifest 和 hash 管理 |
| 历史回滚证据 | `archive/20260811_before_dd20/` | 保留 | 可迁移到仓库外归档，不直接删除 |
| 可重建缓存 | `__pycache__/`、部分 `work/`、`one_year_test/` | 清理候选 | 备份/核对后删除并加入规则 |
| 大量候选输出 | 70 个 `output_*` | 不能整体视为多余 | 建立引用图和 current/baseline/archive 分类后再迁移 |
| 原 FinRL 基础项目 | `src/data/`、`src/strategies/`、`src/trading/`、`examples/`、`figs/`、`data/` | 对个人温度计非必需 | 暂存为 legacy，不在本任务删除 |
| 产品化预留骨架 | `src/api/`、`frontend/`、`src/jobs/`、`src/observability/` | 个人版暂不需要 | 保留文档占位，延期生成业务代码 |
| 私密配置 | `research/.../email_config.json` | 非仓库资产 | 永不提交，保留在本机私密位置 |

## 4. 为什么 70 个 output 不能直接删除

脚本和 Markdown 报告会直接引用这些目录。检查显示 `output/` 被大量研究脚本或报告引用；其他输出目录也被相应审计脚本、候选报告或盲样本报告引用。即使某个目录只被一个脚本引用，也不能仅凭引用数量判断它无价值，因为它可能是该审计唯一的结果证据。

正确做法是建立一个小型证据登记表：

```text
目录 → 生成脚本 → 配置/参数 → 数据 manifest → 报告 → 是否 current/baseline/archive
```

未完成登记前，删除输出目录会让报告中的链接失效，且无法区分“旧候选”与“支持当前结论的反例”。

## 5. 优先级分级

### A 级：明确可重建

- 所有 `__pycache__` 与 `.pyc`；
- `work/` 中没有被登记为当前候选的临时中间文件；
- 已确认不再使用的 `one_year_test/`。

处理前仍需保存路径清单和 hash；不能用宽范围删除命令。

### B 级：迁移到仓库外

- v10 的完整回测与 Alpaca 复核产物；
- 历史候选输出和压力测试；
- `archive/20260811_before_dd20/`。

迁移目标建议为：

```text
D:\Backup\Documents\量化回测\_quant_artifacts\FinRL-Trading\<run_id>\
```

迁移后仓库只保留报告、配置、manifest、hash 和可复现脚本。

### C 级：遗留项目隔离

原 FinRL 的 ML/DRL、Alpaca、Docker、示例和基础数据对个人温度计不是必需内容，但它们属于原项目基线。应通过独立 legacy 分支或单独备份隔离，不能在没有确认依赖的情况下直接删除。

### D 级：当前不处理

- 三个行情文件的改动；
- `email_config.json` 等私密配置；
- 与当前候选报告存在引用关系的输出。

## 6. 本次没有执行的操作

- 没有删除文件或目录；
- 没有执行 `git clean`、`git reset --hard` 或 `git checkout --`；
- 没有回退三个已跟踪行情文件；
- 没有提交 1,646 个未跟踪文件；
- 没有把任何密钥或邮件配置加入 Git。

## 7. 下一次安全清理顺序

1. 先根据 [`WORKTREE_RESTORE_PROTOCOL.md`](WORKTREE_RESTORE_PROTOCOL.md) 记录当前 checkpoint 和文件清单。
2. 为 current、baseline、archive 建立 manifest 和 SHA-256 hash。
3. 把 v10 和历史回测产物复制到仓库外 artifact archive，并抽样核对可读性。
4. 将新实验的默认输出路径改为仓库外，避免 worktree 再次变脏。
5. 单独处理 `.pyc`、`__pycache__` 和明确过期的临时目录。
6. 通过 `git diff --check`、测试和 `git status --porcelain` 验证，再创建一个小而清晰的 checkpoint 提交。

本报告只判断“是否可以安全分类”，不替用户决定删除哪些历史研究证据。
