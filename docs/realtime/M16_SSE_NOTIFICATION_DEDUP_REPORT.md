# M16 SSE 与通知去重报告

## 事件模型

M16 只允许以下事件类型：

- `observation.batch`：行情观察批次，页面展示，默认不是桌面提醒。
- `quality.changed`：质量异常或恢复相关的质量事件。
- `service.status`：服务连接、降级、失败或恢复事件。
- `state.candidate`：未来接入只读确认态变化时使用的候选状态事件。

`quality.changed`、`service.status` 和 `state.candidate` 才具有通知语义；它们不会变成订单或目标权重写入。

## 去重与断线

- 事件 ID 为 `event_type + dedupe_key` 的稳定哈希，同一语义事件重复发布会被丢弃。
- SSE 使用 `Last-Event-ID` 继续读取；已在游标之后的事件不会重复发送。
- 实际 localhost HTTP 测试验证首次连接与 `Last-Event-ID` 重连只分别读取游标前后事件。
- 有界事件窗口过期时只发一次 `cursor.reset`，不会循环生成伪事件 ID。
- 无新事件时发送 heartbeat；客户端断线后由浏览器重新连接。
- 本地浏览器桌面通知必须由用户主动点击授权，并按事件 ID 再做一次消费侧去重。
- 相同连续服务状态不会重复提醒；`ready → degraded → ready` 的恢复转换会生成新的可通知事件。

## 证据

- `tests/realtime/test_events.py` 覆盖稳定 ID、重复发布、质量事件、游标重连、游标过期和 heartbeat。
- `tests/e2e/m16/test_live_sse_contract.py` 覆盖本地访问边界和 SSE 初始帧。
- `tests/realtime/test_live_server.py` 覆盖实际 localhost HTTP 响应、静态页、SSE、Last-Event-ID 重连和确认态挂载。
- 前端资源检查覆盖 `EventSource`、通知权限请求、通知消费去重和无外部数据连接。

## 通知边界

当前版本的“推送”是本地 SSE 加可选浏览器原生桌面提醒，不发送邮件、微信、Webhook 或其他外部消息。若将来增加外部通道，必须另开任务并新增凭证、隐私、重试和审批边界。
