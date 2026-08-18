# M16 私有访问与密钥扫描报告

## 结论

M16 只允许本机访问，Massive 密钥只从进程环境读取；前端不访问 Massive，不接触密钥，也没有券商或交易能力。

## 检查项

- `MASSIVE_API_KEY` 不写入版本化配置、HTML、JavaScript、URL、日志或 Evidence。
- Massive 请求只使用 `Authorization: Bearer ...`，不使用查询参数传递密钥。
- Live server 只接受 `127.0.0.1`、`localhost` 或 `::1`；拒绝 `0.0.0.0`。
- SSE 和确认态 API 只读；M16 server 不挂载 `POST /api/paper/confirm` 或任何订单接口。
- 浏览器静态资源不包含外部市场数据 URL、券商 URL 或密钥环境变量。
- read model 若配置，只按请求线程打开已存在的本地 SQLite 文件，响应后关闭，不创建或写入确认记录。

## 证据

- 配置单元测试覆盖凭证 URL、非 Massive host、非法 read model 路径和默认刷新/时区。
- Live server 测试覆盖 localhost 绑定、远程客户端拒绝、静态路径穿越拒绝和确认态只读访问。
- M16 Harness 的 `protected_scope`、`task_scope`、`ownership_scope` 和 `evidence_integrity` 均通过。
- M15/v3.21、`frontend/demo`、`frontend/shell`、`frontend/dashboard` 和 `frontend/m14` 的保护差异为空。

## 使用者责任

API key 必须在启动 M16 的同一进程环境中设置。不要把 key 写进 `massive.json`，也不要把本地服务绑定到公网地址。
