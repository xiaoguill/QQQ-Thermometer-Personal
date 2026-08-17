# M13 Dashboard 与 Signal Explanation

此目录实现 M13 的展示层：从冻结 OpenAPI 的只读响应中展示状态、温度、趋势、信号一致度、reason codes、指标证据、目标仓位、数据质量和可追溯元数据。

## 运行

在本目录启动静态服务器后打开 `index.html`。页面默认使用明确标注的 `SIMULATED` fixture；点击“读取同源 API”才会请求 `/api/thermometer/latest`、`/api/signals/explain` 和 `/api/data-quality/latest`。

M13 的接口白名单只有 `getLatest`、`explainSignals` 和 `getDataQuality`。客户端的 history、ledger、performance、portfolio snapshot 等方法属于后续模块，Dashboard 不调用它们。

## 重要边界

- 浏览器不重新计算温度、趋势、agreement、指标、状态或目标权重。
- 目标仓位只从后端响应的 `target_weights` 渲染；页面不会归一化、覆盖或生成仓位。
- reason codes 和 indicators 只作为证据展示，不会在前端变成新的交易规则。
- API v1.0.0 没有 `confirmation_status` 字段，因此页面显示“未提供”；不会用 signal date、execution date 或 data quality 猜测 confirmed/provisional。
- 如果请求失败或处于加载中，页面会清空先前的仓位、指标、质量和版本内容，不把旧结果伪装成当前结果。
- 所有 API 文本通过安全 DOM 文本节点渲染，不使用 `innerHTML`。
- 本模块不提供回放、逐年收益、回撤、成本压力测试、纸上确认或任何券商能力；这些属于后续合同。
