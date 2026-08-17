# QQQ 温度计：Codex 分阶段模块生成计划

## 1. 使用方式

本文件是后续让 Codex 逐步生成代码的执行清单。当前只建立计划，不执行其中的业务代码。

严格规则：一次只执行一个模块；完成后停止，等待用户审阅或明确继续。任何模块发现策略规则、时点、数据口径或权限边界需要改变，都必须停下报告，不得自行决定。

每个模块的标准提示词：

```text
只执行 QQQ_THERMOMETER_MODULE_PLAN.md 中的 Mxx。
先读取根 AGENTS.md 和目标目录 AGENTS.md，检查当前工作区。
本模块之外不要改文件，不重构，不新增未批准依赖，不连接真实券商。
先写确定性测试，再写最小实现；完成后运行本模块验收命令并停止。
报告修改文件、测试证据、已知风险、未完成项和回滚方式。
```

## 2. 总体顺序

```text
M00 治理冻结
  ↓
M00.5 Context Governance Hardening
  ↓
M01 策略/版本合同
  ↓
M02 数据源适配与原始快照
  ↓
M03 标准化与数据质量
  ↓
M04 指标计算
  ↓
M05 v10 状态机回放
  ↓
M06 温度计解释模型
  ↓
M07 目标权重服务
  ↓
M08 存储与迁移
  ↓
M09 paper portfolio / ledger
  ↓
M10 只读 API
  ↓
M11 幂等任务与运行状态
  ↓
M12 前端壳与设计令牌
  ↓
M13 Dashboard / Explanation
  ↓
M14 Replay / Performance / Audit
  ↓
M15 全链路验证与私有发布
```

## 3. 模块明细

### M00 — 治理冻结（本次完成）

目标：确认目录、AGENTS、版本命名、策略真相、数据时点和停止条件。

允许：文档、目录占位、审阅清单。

禁止：任何业务代码、策略参数修改、数据刷新、外部部署。

验收：根规则与局部规则存在；模块计划和治理边界可被 Codex 按顺序执行；现有脏工作区未被覆盖。

交付：本目录中的治理文档。

### M00.5 — Context Governance Hardening（独立治理任务）

目标：把“任务 → 上下文 → 权限 → 验收”连接为机器可读、可验证的链路，不改变策略规则、API 语义、数据源或回测结果。

机器真源与执行合同：

- `AI_CONTEXT_ROUTER.json`：按路径和任务类型选择必读上下文；不定义策略规则。
- `tasks/M00.5.json`：当前治理变更的任务合同；普通 Candidate 不得自行扩大任务范围。
- `docs/DOCUMENT_REGISTRY.json`：登记文档类型、状态、权威等级和对应机器真源。
- `OWNERSHIP.yaml`：维护最终路径权限；Router 不重复取代它。
- `verification/` 与 workflow：由 Trusted ref 的 Task Contract 检查实际 diff 和验收门槛。

允许：新增或更新 Router、Task Contract、文档注册表、任务启动索引和验证器的 task scope gate。

禁止：修改 `configs/frozen/`、`contracts/`、研究策略、数据、指标、权重、回测结果或真实交易能力。

验收：Router、Task Contract 和文档注册表可由标准库解析；缺少 task_id、未知 task、缺失必读上下文、角色不匹配或越过 allowed write paths 时 fail closed；当前治理候选通过本地证据检查，并在新 Trusted baseline 审阅后才能进入 CI 验证。

交付：独立治理 Candidate、task scope 验收、保护清单更新、Evidence 和治理变更报告。完成后停止，等待用户确认，不自动开始 M01。

### M01 — 策略与版本合同

目标：把 `v10_preserve_shock_recovery`、历史基线和状态/权重字段写成机器可读合同，但不改变现有回测逻辑。

输入/输出：策略版本元数据、配置 hash、状态枚举、资产与权重 schema、信号日/执行日 schema。

禁止：在 UI 中复制规则；自动把 `final_regime_ensemble` 改名为 v10；增加新指标或新参数。

测试：schema 完整性、权重总和、版本唯一性、未知版本拒绝、warmup 状态。

验收门槛：同一输入可得到同一合同对象；v10 与 legacy 的显示名称和配置 hash 明确不同。

### M02 — 数据源适配与原始快照

目标：建立 QQQ、QLD、VOO、SPY、BIL、TLT、IAU、XLU、SVXY、VIX/VIX3M 所需数据的只读适配层与原始快照清单。

输入/输出：供应商响应 → 不可变 raw snapshot + source manifest。

禁止：把实时数据直接送进策略；无来源地使用代理价格；覆盖原始数据；读取账户私有数据。

测试：字段映射、日期/时区、重复抓取、供应商异常、限流和缺失记录。

验收门槛：能说明每个价格的来源、时间、调整口径和缺失状态；抓取失败不会伪装成成功。

### M03 — 标准化与数据质量

目标：统一交易日历、字段、价格口径和数据质量状态。

输入/输出：raw snapshots → normalized bars + quality events。

禁止：静默填补长缺口；把不同时区的日期直接合并；用未来数据填充历史；隐藏供应商价格差异。

测试：缺失/重复/异常价格、非交易日、上市前日期、跨源差异、陈旧窗口、调整因子。

验收门槛：`OK/PARTIAL/STALE/FAILED/NEEDS_REVIEW` 可解释；质量未通过时下游不会产生 `CONFIRMED` 信号。

### M04 — 指标计算

目标：实现版本化、可回放的指标快照。第一批只实现策略实际需要的指标：QQQ 5/10/20 日收益、EMA10、SMA150、126 日动量、20 日实现波动率、VIX/VIX3M 结构；其他 MA/RSI/MACD/KDJ/OBV 只能作为隔离研究指标。

输入/输出：标准化 bars → indicator snapshot。

禁止：为了页面好看无边界增加指标；用指标名称代替经济假设；rolling 结果右移错误；指标计算读取未来行。

测试：手算小样本、warmup、NaN、边界日期、右移/执行日、防前视、与独立实现抽样比对。

验收门槛：每个指标有定义、窗口、价格口径、warmup 规则、测试和版本号。

### M05 — v10 状态机与历史回放

目标：把 v10 的冲击入口、恢复解锁、中期门槛、最短持有期、滞回和重新冲击写成纯函数。

输入/输出：截至 signal_date 的指标快照 + 上一状态 → state snapshot + evidence。

禁止：用年度结果优化阈值；加入未批准的 RSI/MACD/KDJ/OBV；在状态机中访问行情 API；让 VIX 单日变化直接触发买卖。

测试：2019 反弹、2020 V 型、2022 反复下跌、2008 长回撤等代表阶段；状态边界；连续确认天数；最短持有期；信号/执行错位；重复回放。

验收门槛：历史回放确定性；每个状态有触发证据和未触发原因；没有未来字段；同日重复计算幂等。

### M06 — 温度计解释模型

目标：把策略状态转换成页面可读的 `temperature`、`trend`、`signal_agreement`、`confidence_label`（如适用）和证据列表。

输入/输出：regime snapshot + indicator snapshot → presentation model。

禁止：把 agreement 映射成收益概率；温度计重新决定资产权重；显示未确认的盘中信号为最终结论。

测试：状态—文案映射、颜色与文字一致、数据陈旧、盘中 provisional、收盘 confirmed、缺指标。

验收门槛：前端无需知道策略阈值；模型能显示“为什么是这个状态”和“哪些条件尚未确认”。

### M07 — 目标权重服务

目标：由冻结策略版本生成目标权重、执行日期和变更原因，并明确区分温度计与实际策略输出。

输入/输出：regime + version config → target weights。

禁止：客户端提交权重覆盖；自动优化；把 VXX/SVXY/现金当成相同资产；将温度计颜色直接当交易命令。

测试：权重总和 100%、非负/允许空头边界、warmup、状态转换、交易日历、次日执行、成本计算输入。

验收门槛：策略结果可与现有研究回测逐日对账；差异必须有解释。

### M08 — 存储与迁移

目标：建立 SQLite 优先的本地存储、schema 版本和 repository；未来可迁移 Postgres。

输入/输出：snapshots、states、weights、runs、quality、versions、paper ledger → 可查询读模型。

禁止：破坏性迁移无审批；删除历史账本；把 secrets 写库；让页面绕过 repository 直连表。

测试：迁移前后 schema、唯一键、幂等写入、事务回滚、追加账本、备份恢复。

验收门槛：空库可初始化；重复运行不重复记账；失败可恢复；schema 版本可追溯。

### M09 — Paper portfolio 与追加账本

目标：按目标权重和冻结执行口径模拟纸上组合，不触碰券商。

输入/输出：target weights + next-day prices + costs → positions、NAV、ledger、reconciliation。

禁止：真实订单、券商凭证、自动转账；用当前行情改写历史执行；覆盖账本事件。

测试：现金、手续费、滑点、分红/拆分口径、部分数据、重启恢复、重复运行、与独立回测对账。

验收门槛：至少完成 20 个交易日 paper shadow；每天可说明信号、目标、模拟执行、账面持仓和差异。

### M10 — 只读 API

目标：提供温度计、信号说明、目标权重、组合、表现、数据质量和版本查询。

输入/输出：repository read models → versioned JSON responses。

禁止：路由中重新计算策略；未鉴权公开敏感数据；提供真实交易 endpoint；忽略质量状态。

测试：schema、分页、错误状态、陈旧状态、权限、idempotency（仅 paper confirm）、兼容字段。

验收门槛：API 响应包含版本、时间、质量和 run_id；前端可完全基于 API 渲染。

### M11 — 幂等任务与运行状态

目标：实现手动触发优先的 refresh → calculate → simulate → publish 流程，后续再加 Windows Scheduler。

输入/输出：job request → run state、manifest、quality、published snapshot。

禁止：没有审计的无限重试；任务失败后发布旧结果为新结果；默认启动真实交易。

测试：重跑、并发、断点、超时、部分成功、数据过期、发布前检查。

验收门槛：每次运行有唯一 run_id 和状态转移；失败可见且不会污染确认信号。

### M12 — 前端壳与设计令牌

目标：建立独立前端的布局、响应式容器、状态颜色/图标语义和 API client，不接入全部页面。

禁止：复制参考网站；硬编码策略规则；一次性引入大型 UI 依赖；加入下单按钮。

测试：桌面/窄屏、键盘可达、对比度、加载/空/错误/过期状态。

验收门槛：无真实数据时也能展示完整状态骨架；视觉语义与数据状态一致。

### M13 — Dashboard 与 Signal Explanation

目标：展示当前确认温度、趋势、agreement、目标仓位和逐项证据。

禁止：把 provisional 当 confirmed；用颜色替代文字；前端二次判断是否买卖。

测试：各状态 fixture、缺指标、版本切换、数据更新时间、解释完整性。

验收门槛：用户能回答“当前是什么状态、依据是什么、什么时候确认、目标权重从哪来”。

### M14 — Replay、Performance 与 Audit

目标：提供历史日期回放、净值/回撤/逐年收益、QQQ 基准、成本压力测试和数据审计页面。

禁止：回放页面显示当时不可见数据；隐藏失败年份；把回测表现写成未来承诺。

测试：历史日期冻结、指标可见窗口、基准同日、5/10/25 bps、报告与 API 对账。

验收门槛：页面数字可由 manifest、run_id 和版本复现；不依赖手工截图。

### M15 — 全链路验证与私有发布

目标：在本地完成全链路、隐私、安全、备份和私有访问验收。

禁止：没有用户批准就公网部署；没有 kill switch 就接入 broker；把“通过回测”当成实盘授权。

测试：全链路重放、备份恢复、密钥扫描、访问控制、故障恢复、回滚、20 个交易日 paper shadow。

验收门槛：用户明确确认版本、数据源、运行频率、访问范围、免责声明和回滚方式。

## 4. 每个模块的固定交付模板

```text
模块：Mxx
范围：
不变项：
修改文件：
新增依赖：无 / 列明原因
测试与命令：
测试结果：
数据/策略结果是否变化：
已知风险：
回滚方式：
下一步：等待用户确认，不自动执行
```
