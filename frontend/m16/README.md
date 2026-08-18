# Northstar Live · M16 实时温度计

这是一个独立的实时观察页面，不替换 `frontend/demo`、`frontend/shell`、`frontend/dashboard` 或 `frontend/m14`。

## 边界

- 浏览器只访问同源的本地 `/api/live/events` SSE 和既有只读 API。
- 浏览器不访问 Massive，不包含 API key，不连接券商，也不能下单。
- 盘中数据标记为 `PROVISIONAL`；页面不会根据盘中事件自行计算或改变目标仓位。
- 已确认的状态只从既有只读 API 展示，实时观察与收盘确认分栏显示。
- 页面可由用户主动点击启用浏览器原生桌面提醒；通知只在本机消费，不发送到外部服务。
- 时间展示固定为 `Asia/Shanghai`（UTC+8）。

## 运行

从 M16 本地 HTTP 服务打开页面，不要直接把 `file://` 页面当作实时连接。默认 SSE 地址为 `/api/live/events`，默认刷新窗口显示为 900 秒（15 分钟）。如果要显示既有收盘确认态，可在配置文件中设置一个已经存在的本地 SQLite read model 路径；M16 只读挂载 `/api/thermometer/latest`，不会创建或写入策略记录。页面可在打开前由本地未提交配置覆盖：

```html
<script>
  window.__QQQ_LIVE_CONFIG__ = {
    eventEndpoint: "/api/live/events",
    confirmedEndpoint: "/api/thermometer/latest",
    refreshIntervalSeconds: 900
  };
</script>
```

该配置不应包含密钥或 token；认证属于本地服务边界。
