# Northstar 前端壳层

这是 M12 的独立前端壳层，不是完整 Dashboard，也不替代现有 `frontend/demo/` 概念稿。它只提供布局、设计令牌、响应式状态表达和同源只读 API client。

## 本地查看

直接打开 `index.html` 可以查看静态状态夹具；也可以在本目录启动任意静态文件服务器。默认不会自动请求后端，点击“尝试本地只读 API”才会发起同源 `GET /api/...` 请求。

## 边界

- `api-client.mjs` 只允许冻结 OpenAPI 中的只读 GET 查询端点，不包含纸上确认 POST。
- API base URL 必须是同源路径；不接受绝对 URL、CDN、远程行情源或券商地址。
- 页面不会根据日期、数据质量、颜色或缺失字段推断 `CONFIRMED`/`PROVISIONAL`，缺少明确字段时显示“未提供”。
- 所有状态夹具都带有 `shell-*-v1` ID，并明确标注为 `SIMULATED`。
- 任何来自 API 的文本都必须通过 `textContent` 或安全的结构化渲染进入页面；不使用 `innerHTML`。
- 没有 token、secret、cookie、localStorage、订单、撤单、转账或账户权限能力。

## 模块边界

当前只实现 M12 壳层。真实温度、信号证据、目标仓位、历史回放和表现指标分别由后续模块按照各自合同接入；本目录不复制策略参数或权重。
