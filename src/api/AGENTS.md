# AGENTS.md — 未来 API 后端

## 定位

`src/api/` 预留给未来 FastAPI 后端。当前只建立边界，不在本次任务中生成业务 API 或启动服务。

## 分层规则

```text
route/controller  ->  application service  ->  domain / repository adapter
```

- 路由层只负责鉴权、参数校验、状态码、分页和响应模型。
- 策略、指标、状态机和目标权重属于 `src/thermometer/` 等可测试领域模块，不写在路由函数中。
- 数据库访问属于 `src/storage/`；数据源访问属于 adapter；定时刷新属于 `src/jobs/`。
- API 返回必须携带策略版本、数据质量、`as_of`、信号日、执行日和计算运行标识。

## API 边界

初期只允许只读与纸上确认相关接口，例如：

```text
GET  /api/thermometer/latest
GET  /api/thermometer/history
GET  /api/signals/explain
GET  /api/triggers/next
GET  /api/portfolio/targets
GET  /api/portfolio/latest
GET  /api/portfolio/ledger
GET  /api/performance/curve
GET  /api/performance/metrics
GET  /api/data-quality/latest
GET  /api/versions
POST /api/paper/confirm
```

`POST /api/paper/confirm` 只能记录用户对纸上目标或观察结果的确认，不能转换为券商订单。

## 安全与可靠性

- 禁止在代码、响应、日志和示例中出现密钥、账户凭证或完整个人持仓信息。
- 任何写接口必须有幂等键、审计事件、权限边界和失败重试规则。
- 服务不可用或数据陈旧时返回明确状态，不回退到未知的旧数据。
- API schema 变更必须新增版本或提供兼容期；数据库破坏性迁移必须单独审批。
- 不能把前端传入的颜色、温度或权重当作事实写入账本。
