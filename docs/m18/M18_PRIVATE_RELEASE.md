# M18 私有发布清单

## 发布身份

| 字段 | 值 |
|---|---|
| 发布名称 | `m18-full-chain-workbench-v1` |
| 任务 | `M18` |
| 角色 | `integrator` |
| 受保护基线 | `verification-baseline-v3.31` |
| 候选分支 | `codex/m18-full-chain-workbench` |
| 运行页面 | `http://127.0.0.1:4180/` |
| 主配置 | `configs/m18/workbench.json` |
| 数据库 | `%LOCALAPPDATA%/QQQ-Thermometer-Personal/m18.sqlite3` |
| 数据时区 | `Asia/Shanghai`（内部事件时间仍为 UTC） |

发布标签必须指向通过最终本地 Harness 和 GitHub CI 的候选提交。使用以下命令核对标签没有漂移：

```powershell
git rev-parse m18-full-chain-workbench-v1
git show-ref --tags m18-full-chain-workbench-v1
```

## 回滚参考

回滚时只允许回到已经存在的受保护版本或基线，不允许改写历史：

```powershell
git switch --detach m18-full-chain-workbench-v1
# 如需停用 M18，回到既有 M17 私有发布：
git switch --detach m17-private-release-candidate-v2
```

回滚只影响代码引用，不会删除本地 SQLite 数据库；需要保留审计记录时，应先复制数据库文件和对应 Evidence 目录。M17、M16、M15、原有 Demo 与历史研究产物不属于 M18 回滚操作的删除范围。

## 运行边界

- 只监听 loopback；只读展示、回放和纸上账本。
- 不连接券商，不创建真实订单，不转移资金，不新增后台调度器。
- Massive API key 只能通过进程环境变量提供，不写入仓库、日志、浏览器响应、SQLite 或 URL。
- 页面只读取 M18 read model；不在前端复制指标、状态机、解释或目标仓位规则。
- M16 的实时观察保持 `PROVISIONAL`，M04–M07 的正式策略结果保持 `CONFIRMED`；前者不能改写后者。

## 证据位置

- 本地 Evidence：`../../evidence/m18-local-27c83bd/evidence.json`
- GitHub CI Evidence：由 `verification.yml` 运行产生，并按候选 SHA 下载到本地 Evidence 目录。
- 设计与边界：`M18_WORKBENCH.md`、`M18_PIPELINE.md`、`M18_READ_MODEL.md`

发布前必须再次确认候选提交、发布标签、Evidence 中的候选 SHA 三者相同；任何不一致都停止发布，不得把页面可访问性当作策略或数据质量验收。
