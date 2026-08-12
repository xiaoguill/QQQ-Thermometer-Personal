# QQQ 温度计：目标架构与目录设计

## 1. 文档目的

本文件把现有 `FinRL-Trading` 研究仓库和未来个人使用的美股温度计拆成清晰的边界。当前阶段只建立目录和契约位置，不生成业务代码，不改变已有策略或回测结果。

## 2. 当前状态与目标状态

### 当前状态

- `research/qqq_drawdown_strategy/`：已有大量策略实验、数据、回测和报告，是研究历史区。
- `src/web/`：已有 Streamlit 原型，继续维护兼容性。
- `final_regime_ensemble`：历史基线，不能被静默替换。
- `v10_preserve_shock_recovery`：当前候选的冲击—恢复版本，仍需运行时契约与 paper shadow 验证。

### 目标状态

```text
数据源
  ↓
原始快照与数据清单
  ↓
标准化行情 + 数据质量
  ↓
指标快照（QQQ/VIX/VIX3M/VOO/SPY 等）
  ↓
v10 状态机（纯函数、可回放）
  ↓
温度计展示模型（解释层，不改变策略）
  ↓
目标权重服务（策略版本产生）
  ↓
纸上组合与追加账本
  ↓
API 只读模型
  ↓
Streamlit 兼容页面 / 未来 React 前端
```

关键原则：温度计读取状态和证据；它不反过来修改状态机。

## 3. 目标目录

```text
FinRL-Trading/
├── AGENTS.md
├── docs/
│   ├── QQQ_THERMOMETER_ARCHITECTURE.md
│   ├── QQQ_THERMOMETER_MODULE_PLAN.md
│   └── QQQ_THERMOMETER_GOVERNANCE_BOUNDARIES.md
├── research/
│   └── qqq_drawdown_strategy/
│       ├── AGENTS.md
│       └── <versioned experiments and reports>
├── src/
│   ├── thermometer/       # 领域模型、指标合同、状态机、温度计解释模型
│   ├── api/               # FastAPI 路由、schema、应用服务编排
│   ├── storage/           # 原始快照、数据库、repositories、migrations、ledger
│   ├── jobs/              # 刷新、计算、发布、健康检查；必须幂等
│   ├── observability/     # 数据质量、审计、运行状态、告警
│   ├── web/               # 当前 Streamlit 原型；保持兼容
│   └── <existing modules> # 现有回测、策略和数据模块，暂不大规模迁移
├── frontend/              # 未来 React/Next.js 独立前端
├── tests/
│   ├── unit/              # 领域纯函数
│   ├── integration/       # 数据源与存储
│   ├── contract/          # API/schema
│   ├── replay/            # 历史回放、防前视、黄金样例
│   └── frontend/          # 页面状态与组件
├── configs/               # 非敏感、可版本化配置
└── artifacts/
    ├── manifests/         # 数据与运行清单
    ├── reports/           # 审计报告和回测报告
    └── schemas/           # 版本化 schema
```

本次已创建目录占位文件，后续每个模块按模块计划逐步填充；不要求一次性生成所有代码。

## 4. 层间职责

### `src/thermometer/`

只放可测试的领域逻辑：指标定义、状态机、滞回、最短持有期、信号解释和目标权重合同。输入是带时间戳和质量状态的标准化数据，输出是不可歧义的状态对象。不得直接访问网络、数据库或 Streamlit。

### `src/storage/`

负责保存原始行情快照、标准化数据、指标快照、状态、目标权重、运行记录和 paper ledger。历史账本追加写入；更正通过事件表达，不物理删除原记录。

### `src/api/`

负责鉴权边界、schema、分页、错误状态和读模型组装。API 不复制策略公式，也不允许客户端提交任意权重覆盖策略输出。

### `src/jobs/`

按明确的状态机运行：`scheduled → running → data_ready → signal_ready → simulated → published`，出现异常时转为 `partial/stale/failed/needs_review`。每个运行必须有幂等键。

### `src/web/` 与 `frontend/`

两者都是展示层。当前 Streamlit 继续作为兼容原型；未来前端独立演进。两者都只能消费后端合同，不持有策略真相。

## 5. 建议的数据实体

| 实体 | 用途 | 必须可追溯的字段 |
|---|---|---|
| `market_snapshot` | 原始/标准化行情 | symbol、bar_date、source、price_basis、retrieved_at、quality |
| `indicator_snapshot` | MA/EMA/RSI/MACD/KDJ/OBV/VIX 结构等 | indicator_version、as_of、warmup、source_snapshot |
| `regime_snapshot` | v10 状态机结果 | strategy_version、signal_date、state、evidence、quality |
| `target_weight_snapshot` | 目标仓位 | strategy_version、signal_date、execution_date、weights |
| `paper_portfolio` | 纸上持仓与净值 | run_id、as_of、nav、cash、positions |
| `paper_ledger` | 追加式模拟交易记录 | idempotency_key、event_type、quantity、price、cost |
| `backtest_run` | 一次回测/回放 | config_hash、data_manifest、code_version、status |
| `data_quality_event` | 缺失、延迟、冲突 | source、symbol、window、severity、resolution |
| `strategy_version` | 规则与发布状态 | version、status、config_hash、approved_by、approved_at |

## 6. 前端页面边界

第一版建议页面：

1. `Dashboard`：当前确认状态、温度、趋势、信号一致度、目标仓位、数据更新时间。
2. `Signal Explanation`：各指标当前值、阈值方向、触发/未触发原因、版本和信号日。
3. `Portfolio`：目标仓位、纸上持仓、净值、账本和待确认事项。
4. `Historical Replay`：选择历史日期，重放当时可见信息，禁止显示未来数据。
5. `Performance`：净值、逐年收益、回撤、成本敏感性、QQQ 基准。
6. `Audit / Data Health`：数据源、缺失、延迟、价格差异、运行状态和版本链。

## 7. API 初始方向

接口名称仅作为后续契约草案，未在本次生成：

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

所有响应至少要带 `strategy_version`、`as_of`、`signal_date`、`execution_date`（如适用）、`data_quality` 和 `run_id`。

## 8. 为什么不把所有东西放进现有 `src/web/`

现有 Streamlit 页面已经承担原型展示和多个历史功能。把抓取、指标、策略、数据库和实时刷新继续堆到页面中，会让策略真相、页面状态和运行状态互相耦合，也难以进行确定性回放。因此保留 `src/web/` 作为兼容层，把未来领域逻辑、API、存储和前端拆开；迁移可以渐进，不要求一次性重写。
