# M14 Replay / Performance / Audit

M14 是独立的只读展示页，消费冻结 API client 已提供的 GET 响应：

- `getHistory(from, to, limit)`：历史温度计状态；
- `getPerformanceCurve(as_of)`：后端净值曲线字段；
- `getPerformanceMetrics(as_of)`：后端表现指标、逐年字段、基准和成本场景（如果存在）；
- `getLedger(from, to, limit)`：纸上账本只读行；
- `getVersions()`：策略/实现/数据版本；
- `getDataQuality()`：数据质量和问题。

页面不在浏览器计算 CAGR、年收益、最大回撤、回撤持续时间、QQQ 基准或成本压力。字段缺失、空响应、失败、部分数据和待复核都保持可见并显示“未提供”或“未验证”。历史行不会从顶层 `meta` 推断逐行日期、版本或 run id。

当前 OpenAPI 对 performance、benchmark、cost stress 和 audit manifest 只提供通用 object/array 响应，且冻结策略合同的成本口径仍有未决项。因此，M14 页面不会把模拟 fixture 或后端透传数字描述为已验证回测结论，也不会覆盖冻结合同的成本设置。

## 运行

在 `frontend/` 目录启动静态服务器后打开 `m14/index.html`。默认是明确标注的 `SIMULATED` fixture；点击“读取同源 API”才会请求本地同源 GET 接口。页面没有 POST、paper confirm、订单、券商、外部市场数据或密钥能力。
