# M17 统一个人入口

M17 是一个新增的本地统一入口，聚合 M16 Massive 盘中观察、既有确认策略、目标权重、纸上调仓预览、数据质量和历史页面链接。它不替换 M15/M16 或已有 Demo。

## 页面边界

- 浏览器只请求同源 `/api/m17/*`、`/api/live/events` 和既有只读 GET API。
- Massive API key 只由启动 M16 轮询的本地 Python 进程读取；页面只显示“已配置/未配置”。
- 目标权重从既有确认 API 读取；盘中观察不会在浏览器中重新计算状态或权重。
- 纸上计划需要 `configs/paper/m17.json` 中明确填写个人现金或持仓；计划是估值预览，不是订单，不连接券商。
- 显示时区固定为 `Asia/Shanghai`；内部事件仍保留 UTC 时间。
- 旧页面继续通过 `/dashboard/index.html`、`/m14/index.html`、`/demo/index.html`、`/shell/index.html` 和 `/m16/index.html` 独立访问。

## 启动

```powershell
$env:MASSIVE_API_KEY = "<your key>"
python -m src.api.m17 --config configs/m17/unified.json
```

打开 `http://127.0.0.1:4173/`。确认策略卡片默认读取 `http://127.0.0.1:8765` 的既有本地只读 API；如果该服务没有运行，页面会明确显示确认不可用，但不会用盘中数据冒充确认结果。刷新频率由 `configs/realtime/massive.json` 中的 `refresh_interval_seconds` 控制，默认 900 秒（15 分钟）。统一入口相关本地配置在 `configs/m17/unified.json`。

## 纸上持仓输入

只编辑本地纸上配置，不填写券商凭证。例如：

```json
{
  "$schema": "qqq-m17-paper-input/v1",
  "portfolio_id": "personal-paper-default",
  "base_currency": "USD",
  "starting_cash": 10000,
  "positions": {
    "QQQ": {"quantity": 10, "average_cost": 400}
  }
}
```

M17 使用 Massive 最新质量为 `OK` 的观察价格做估值；它不会读取、写入或修改券商账户，也不会发出订单或调用既有纸上确认写入接口。
