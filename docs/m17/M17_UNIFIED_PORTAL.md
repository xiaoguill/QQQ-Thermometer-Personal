# M17 统一个人入口与纸上计划

## 1. 目标

M17 在不改变 M15/v3.21、M16、原 Demo、M14 回放和既有只读 API 的前提下，把个人使用所需的页面聚合到一个本地入口：

```text
http://127.0.0.1:4173/
```

统一入口展示：

1. Massive 盘中观察与数据质量；
2. 既有确认 API 返回的策略状态、版本、信号日期和目标仓位；
3. 显式本地纸上持仓与确认目标之间的估值差异；
4. 数据源、确认 API、纸上输入和运行时健康状态；
5. Dashboard、M14、原 Demo、M12 壳层和 M16 页面入口。

## 2. 启动方式

在 PowerShell 中设置进程级 key，然后启动 M17：

```powershell
cd D:\Backup\Documents\量化回测\QQQ-Thermometer-Personal-m16
$env:MASSIVE_API_KEY = "<your Massive key>"
python -m src.api.m17 --config configs/m17/unified.json
```

打开 `http://127.0.0.1:4173/`。如果 4173 已被旧静态服务器占用，应先停止旧服务器；也可以临时使用：

```powershell
python -m src.api.m17 --config configs/m17/unified.json --port 4174
```

API key 只进入启动进程环境，不写入配置、网页、浏览器存储、日志、URL 或 Git。页面只显示“已配置/未配置”。

## 3. 数据流与时点

```text
Massive REST snapshot
        │  M16 / 15 min poll
        ▼
M16 RealtimeRuntime → 本地事件总线 → /api/live/events → M17 页面

既有确认 API :8765 ──GET──▶ M17 gateway ──▶ 确认策略与目标权重

显式 configs/paper/m17.json + 最新合格观察
        │
        ▼
服务器端 M17 paper plan ──GET──▶ 纸上计划预览
```

盘中观察始终标记为 `PROVISIONAL`。它只能展示最新价格和质量，不能改变已确认状态或目标权重。策略状态、策略版本、信号日期和目标权重只从既有确认 API 读取；当确认 API 不可用或质量不是 `ok` 时，M17 显示 `需要复核`，不会用盘中信号代替确认结果。

默认刷新频率来自 `configs/realtime/massive.json` 的 `refresh_interval_seconds`，当前为 900 秒（15 分钟）。只修改该非敏感配置即可调整频率；M17 不在前端硬编码策略参数。

## 4. 统一端点

| 路径 | 方法 | 用途 | 写入/交易 |
|---|---:|---|---|
| `/api/m17/overview` | GET | 统一页面数据 | 否 |
| `/api/m17/paper-plan` | GET | 服务器端纸上差异预览 | 否 |
| `/api/m17/source-status` | GET | 数据源与健康状态 | 否 |
| `/api/live/events` | GET | M16 SSE 观察事件 | 否 |
| `/api/thermometer/latest` 等既有端点 | GET | 旧页面兼容代理 | 否 |

M17 所有写方法均返回 `405 METHOD_NOT_ALLOWED`。它不会调用纸上确认写接口，不会接入券商，不会创建订单、撤单、转账或自动调仓。

## 5. 旧页面与回滚

旧页面仍保持独立 URL：

- `/dashboard/index.html`
- `/m14/index.html`
- `/demo/index.html`
- `/shell/index.html`
- `/m16/index.html`

回滚时可以直接停止 M17，继续使用 M16 私有版本 `m16-private-release-candidate-v2`（基线提交 `41c0da6ab697bf6f434b670d4844b814218948cd`）。M17 没有修改 M16 页面或运行时文件。

## 6. 本地使用边界

- 仅绑定 `127.0.0.1`/`localhost`/`::1`；不允许公网监听。
- 确认 API 地址必须是本机 HTTP origin，默认 `http://127.0.0.1:8765`。
- 页面不保存 API key、local token 或持仓到浏览器。
- 纸上输入必须由用户显式编辑；M17 不读取券商账户。
- 数据质量为 `FAILED`、`STALE`、`PARTIAL` 或 `NEEDS_REVIEW` 时，页面保守展示并停止生成可用估值计划。
