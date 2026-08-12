# Worktree 清洁、Checkpoint 与回溯协议

## 1. 定义

### CLEAN

```text
git status --porcelain=v1 为空
```

并且没有把临时回测输出、密钥和本机账户数据藏在 Git 忽略规则里冒充“干净”。生成物必须有仓库外 artifact 目录和 manifest。

### CHECKPOINT

一个可解释的 Git commit，包含源代码、规则、配置、测试和文档；不包含密钥、`.pyc`、临时缓存和没有登记的全量回测输出。

### ARTIFACT RUN

一次独立的回测、数据刷新或 paper ledger 运行，使用唯一 `run_id` 写入仓库外目录，不覆盖其他运行。

## 2. 分支规则

```text
稳定分支：master/main（只接收已确认 checkpoint）
工作分支：codex/<topic>
研究版本：strategy/<version> 或配置中的显式 version
```

- 每个任务从一个已知 commit 开始。
- 一个任务只完成一个逻辑模块，完成后创建一个小提交。
- 不在有未解释脏修改的分支上继续刷新数据或修改策略。
- 不使用 `git reset --hard`、`git checkout --` 或宽范围 `git clean` 作为“清理”手段。

## 3. 开始任务前

执行以下只读检查：

```powershell
git status --porcelain=v1
git branch --show-current
git rev-parse HEAD
git diff --check
```

若状态不为空：

1. 把现有修改列为 `BASELINE_DIRTY`；
2. 不覆盖、不格式化、不刷新与其相关的文件；
3. 选择以下一种方式：
   - 用户确认后创建 checkpoint commit；
   - 将未跟踪生成物复制到仓库外并记录 manifest；
   - 由用户明确指定只处理某个路径；
4. 在完成前不要声称 worktree clean。

## 4. 生成物规则

默认输出根目录：

```text
D:\Backup\Documents\量化回测\_quant_artifacts\FinRL-Trading\<run_id>\
```

每个运行至少保存：

```text
manifest.json
config.json 或配置 hash
data_manifest.json
git_commit.txt
command.txt
REPORT.md
关键 signals/weights/returns CSV
```

`manifest.json` 至少记录：策略版本、代码 commit、数据源、数据区间、价格口径、信号/执行规则、成本、运行时间、输出文件 SHA-256 和状态。

仓库内的 `research/.../output*` 与 `work/` 是历史兼容区。迁移完成前不删除；新代码不得默认写入这些目录。

## 5. 任务结束前

至少执行：

```powershell
git diff --check
git status --short
git diff --stat
```

并完成：

- 运行本模块的单元测试或历史回放测试；
- 检查权重合计、信号日/执行日和数据质量状态；
- 确认没有新增密钥、账户数据、`.pyc` 或临时输出；
- 生成或更新 artifact manifest；
- 创建一个只包含本模块的 checkpoint commit，或明确报告为什么暂时不能提交。

最终报告必须写明：

```text
结束状态：CLEAN / BASELINE_DIRTY / BLOCKED
HEAD：<commit>
修改文件：<列表>
生成物：<外部 artifact 路径>
测试：<命令与结果>
回滚：<commit/tag/manifest>
```

## 6. 回滚规则

优先级从低风险到高风险：

1. 回到上一个已批准版本：切换到对应 branch 或 tag；
2. 撤销一个明确的逻辑提交：使用 `git revert <commit>`；
3. 从 artifact manifest 重新加载同一版本的回测/纸上结果；
4. 只有用户明确要求且已经确认备份后，才考虑更复杂的历史重写。

不要删除历史 commit 来“变干净”。干净意味着当前工作区没有未解释变化，不意味着历史不存在。

## 7. 清理命令的安全边界

允许先做预览：

```powershell
git clean -nd -- <明确目录>
```

但在本项目中，`git clean -fd` 默认禁止。任何物理删除前必须同时满足：

- 目标路径是明确列出的目录，不是仓库根目录、用户目录或通配的宽范围路径；
- 已经复制到外部 artifact archive；
- manifest 和 hash 已保存；
- 报告中说明了删除内容和是否可恢复；
- 用户明确确认。

## 8. 本仓库当前状态

本协议建立时，仓库仍为 `BASELINE_DIRTY`：三个已跟踪行情文件被修改，研究与工作目录存在大量未跟踪文件。它们由清查报告登记，尚未被删除、提交或隐藏。下一步应先建立安全 checkpoint 和 artifact 归档，再执行任何清理。
