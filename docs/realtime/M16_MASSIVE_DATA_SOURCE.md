# M16 Massive 实时观察数据源

本模块使用 Massive 的只读市场数据能力，不使用账户、订单或券商接口。默认模式是 REST 轮询，刷新间隔由 `configs/realtime/massive.json` 的 `refresh_interval_seconds` 控制，默认 900 秒（15 分钟）。

## 本地启动链路

M16.4 提供一个个人版启动入口：它读取版本化配置、从环境变量读取 `MASSIVE_API_KEY`、轮询 Massive、将变化发布到内存事件总线，并在本机提供 `frontend/m16` 页面与 SSE。PowerShell 示例：

```powershell
$env:MASSIVE_API_KEY = "<只在本机环境设置，不写入文件>"
python -m src.realtime --config configs/realtime/massive.json --host 127.0.0.1 --port 8766
```

然后打开 `http://127.0.0.1:8766/`。停止进程即可停止轮询；不要把 host 改成 `0.0.0.0`，也不要把 API key 放入 HTML、URL、日志或提交记录。配置文件只修改非秘密参数，例如 `refresh_interval_seconds`，修改后应创建新的 Candidate 版本并重新验证。

## 时点与时区

- Massive 返回的市场时间保留原始时间戳；内部统一转换为 UTC。
- 股票 Single Ticker Snapshot 按 `ticker.day`、`ticker.prevDay`、`ticker.lastTrade`、`ticker.min` 和 `ticker.updated` 解析；`lastTrade.t` 按纳秒、`min.t` 按毫秒处理，不把本地抓取时间冒充成行情时间。
- 任何非正价格、负成交量、未来时间戳、时间回退、过期或不完整输入都不会被标为可用的 `OK` 数据；未来时间戳严格零容忍。
- 页面显示时使用 `Asia/Shanghai`（东八区）。
- 美股市场日历和交易时段仍按 `America/New_York` 解释。
- 盘中观察标记为 `PROVISIONAL`，不改变确认状态或目标仓位。
- 收盘数据经过既有 M04–M07 链路和质量门后，才可以成为 `CONFIRMED`。

## 认证与安全

Massive API key 只从环境变量 `MASSIVE_API_KEY` 读取，并通过 Authorization header 发送；不写入配置文件、URL、前端、SQLite 或日志。官方文档同时支持查询参数和 Authorization header，本项目只采用 header 方式。

## 端点与资产

- 股票观察使用 Massive Stocks Snapshot 的逐标的只读端点。
- 指数观察使用 Massive Indices Snapshot；`I:VIX` 和 `I:VIX3M` 的可用性由启动时的供应商响应确认，未找到或未授权时必须显示数据质量错误。
- 连接使用固定的 `https://api.massive.com` origin，并关闭 HTTP 重定向；HTTP 401/403/404/429 会被分类为授权、标的或限流错误。
- 轮询批次使用不含本地抓取时间和请求 ID 的语义去重键；重复批次可被后续事件层抑制。连续服务失败只增加下一次调度延迟，不在错误时自旋。
- 配置中的股票/指数清单只用于观察和策略输入准备，不会改变冻结策略资产集合或权重。

## 数据质量

每个观察值保留 provider、symbol、asset class、source timestamp、fetch timestamp、request id、price basis、raw payload hash 和 quality。HTTP 401/403、404、429、缺字段、未来时间、超出最大源年龄和解析失败都不能伪装成有效行情。

## 参考资料

- https://massive.com/docs/rest/quickstart
- https://massive.com/docs/rest/stocks
- https://massive.com/docs/rest/indices
- https://massive.com/docs/rest/indices/snapshots/indices-snapshot
