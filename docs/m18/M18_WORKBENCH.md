# M18 全链路工作台使用边界

## 启动

在项目根目录打开 PowerShell，将 key 只放进当前进程环境，然后启动 M18：

```powershell
$env:MASSIVE_API_KEY = "<your Massive key>"
& "C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m src.api.m18 --config configs/m18/workbench.json
```

浏览器打开 `http://127.0.0.1:4180/`。key 不写入配置文件、SQLite、网页、日志、URL 或 Git。

`configs/m18/workbench.json` 是 M18 的非敏感运行配置：

- `refresh_interval_seconds`：页面下一次读取读模型的间隔；默认 900 秒（15 分钟）；
- `history_start_date` / `history_end_date`：Massive 日线聚合数据窗口；`history_end_date=null` 时使用最近一个已结束交易日；
- `history_symbols`：可选的历史标的白名单；`null` 使用 `configs/realtime/massive.json` 中的全部声明；
- `database_path`：默认落在 `%LOCALAPPDATA%/QQQ-Thermometer-Personal/m18.sqlite3`，不污染 Git worktree。

M18 启动时执行一次有界的 M02–M11 链路，然后提供只读页面和 API。M16 仍由原有运行时负责 15 分钟盘中观察；M18 不新增调度器，不在前端重新计算策略。

## 页面数据来源

页面只读取 `GET /api/m18/workbench`。字段对应关系如下：

| 页面区域 | 后端真源 | 发布性质 |
|---|---|---|
| 最新数据质量、指标、状态依据 | M02–M06 快照与事件 | 质量/解释证据 |
| 确认目标仓位、确认策略 | M07 目标 + M10/M18 确认读模型 | `CONFIRMED` |
| 纸上调仓计划 | M09 纸上模拟结果 | 只读、无订单 |
| 实时温度、盘中标的观察 | M16 provisional observation | `PROVISIONAL` |
| M00.5–M17 模块表 | M18 provenance projection | 版本、运行时间、质量和原因 |
| 运行边界 | M18 `RuntimeBoundary` | loopback/paper-only/execution-disabled |

当 Massive 套餐不包含某类历史聚合或指数权限时，页面显示 `NOT_ENTITLED`/`FAILED` 与原因，正式目标保持为空；不会用缓存、估算或前端默认值冒充最新数据。

## 安全与回滚

- 只绑定 `127.0.0.1`；M18 API 只允许 GET；
- `order_created=false`、`broker_connected=false`、`execution_allowed=false` 固定成立；
- M16 的临时观察不会改变 M07 目标仓位；
- 停止 M18 后，M17/v3.21、M16、原 Demo 和旧 API 仍可按原命令使用；
- 删除或移走本地 M18 SQLite 前应先备份；Git worktree 中只保存代码、配置模板和文档，不保存运行数据库。
