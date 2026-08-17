# QQQ 量化项目：独立验证治理基线

**文档状态：** Normative Baseline v0.1
**建立日期：** 2026-08-12
**适用范围：** QQQ 温度计、历史回测、每日模拟跟踪、paper ledger、未来只读网站/API 及其自动化验证。

本文件把“防伪测试治理”与 QQQ 项目的数据、策略、执行和审计约束合并为一套未来实现程序时必须遵守的边界。它补充 [`QQQ_THERMOMETER_GOVERNANCE_BOUNDARIES.md`](QQQ_THERMOMETER_GOVERNANCE_BOUNDARIES.md) 和 [`qqq_quant_simulation_website_technical_spec.md`](../../docs/qqq_quant_simulation_website_technical_spec.md)；若文件之间出现冲突，以用户明确批准的最新版本、仓库总规则和安全规则为准，本文件不得被用来放宽边界。

## 1. 总原则：产生 Candidate 与判定正确性必须分离

```text
研究候选 ≠ 已批准策略
Builder ≠ Verifier
回测通过 ≠ 未来收益证明
信号 ≠ 订单
盘中观察 ≠ 收盘确认
paper ledger ≠ 真实账户
```

Codex 或其他 Builder 可以读取需求、修改获准范围内的程序、提出测试并生成 Candidate Commit；它不能成为最终“通过”的裁判。最终状态只能由独立、可重复、机械化的验证 Harness 根据 Candidate SHA、受保护测试、固定数据和机器 Evidence 决定。

缺少证据时的结果不是“看起来可以”，而是 `UNVERIFIED`；发现越权或验证标准被改写时是 `BLOCKED`。

## 2. 当前项目的事实边界

第一版产品只允许以下链路：

```text
原始行情快照
  → 标准化数据与质量状态
  → 指标快照
  → 冻结策略状态
  → 下一交易日目标权重
  → paper ledger / 模拟净值
  → QQQ/VOO 等同口径比较
  → 只读展示与审计产物
```

明确不在本阶段范围内：真实券商、API key、下单、撤单、转账、自动再平衡、借贷和把历史回测写成收益承诺。

以下名称不能互相替换：

- `final_regime_ensemble`：现有研究基线名称；
- `v10_preserve_shock_recovery`：研究/产品化候选名称；
- `candidate_096`：现有选择结果中的研究展示候选，已有说明表明它“不应直接实盘”。

在实现前必须确定一个明确的 `strategy_version`、配置哈希和发布状态。未经批准，不得把上述名称静默改名、合并或显示成同一个生产版本。

## 3. 权限与责任边界

| 角色/组件 | 可以做什么 | 不可以做什么 |
|---|---|---|
| Builder / Codex | 修改任务范围内的业务代码、Developer Tests、研究实验的新版本；运行只读检查 | 修改受保护验收标准；把测试改弱；把研究候选自动升级；宣称最终 PASS |
| Independent Harness | 读取 Candidate、固定数据和合同；执行验证；生成 Evidence | 修改业务代码、Golden Dataset 或验收逻辑来让测试通过 |
| Mechanical Gate / CI | 按 SHA 在干净环境运行、校验哈希和状态、发布 `PASS/FAIL/BLOCKED` | 读取自然语言声明代替机器证据；接受 SHA 不匹配的结果 |
| 前端/API | 展示后端正式结果、版本、时点、质量和审计链接 | 自己复制策略公式；接受客户端权重覆盖；提供真实交易端点 |
| 用户/审批者 | 批准策略版本、数据口径、成本、执行规则和外部发布 | 用一次绿色测试替代完整验收 |

建议的未来受保护区域：

```text
FinRL-Trading/verification/**
FinRL-Trading/configs/frozen/**
FinRL-Trading/artifacts/golden/**
FinRL-Trading/tests/replay/**        # 若继续使用现有目录，按同等强度保护
.github/workflows/*verification*.yml
```

普通开发任务对这些区域默认只有 `READ` 权限，不得 `WRITE / DELETE / SKIP / WEAKEN`。

## 4. 测试分层与不可弱化规则

### 4.1 Developer Tests

`tests/unit/`、`tests/integration/`、`tests/contract/`、`tests/frontend/` 用于开发效率。Builder 可以新增、修改和重构，但仍须避免只测试“能够运行”而不验证结果。

### 4.2 Protected Acceptance Tests

以下内容属于验收真相，不与业务开发混在同一权限边界内：

```text
verification/golden/
verification/strategy_fidelity/
verification/lookahead/
verification/signal_timing/
verification/execution_timing/
verification/data_integrity/
verification/portfolio_reconciliation/
verification/acceptance/
```

禁止通过以下方式制造绿色结果：

- 修改 `expected` 使其等于 `actual`；
- 删除失败测试、Golden Case、断言或 Negative Test；
- 增加 `skip` / `xfail`，或降低测试集合覆盖范围；
- 把真实数据替换成未经批准的 mock；
- 修改 fixture、阈值、tolerance 或合同使错误结果成为预期；
- 让回测代码与 expected result 使用同一套算法生成；
- 把精确相等改成宽松的“非空”检查；
- 只报告 coverage，不验证测试能否杀死错误实现。

如果验收标准确需修改，必须单独建立 **Verification Change Task**，单独提交，说明原因、影响的 Golden 数据、前后哈希和用户批准；不能与普通功能开发混在同一 Candidate 中。

## 5. 验证顺序与机器 Evidence

验证必须先检查边界，再运行测试：

```text
Candidate Commit
  → Scope Gate
  → Protected Files / Test Integrity Gate
  → Independent Tests
  → Golden / Quant Integrity Gates
  → Evidence 生成
  → SHA 与哈希校验
  → Mechanical Gate: PASS / FAIL / BLOCKED
```

Evidence 至少包含：

```json
{
  "candidate_sha": "...",
  "base_sha": "...",
  "gate": "strategy_fidelity",
  "command": "...",
  "environment_id": "...",
  "exit_code": 0,
  "tests_collected": 0,
  "tests_passed": 0,
  "tests_failed": 0,
  "tests_skipped": 0,
  "stdout_sha256": "...",
  "golden_dataset_sha256": "...",
  "strategy_contract_sha256": "...",
  "data_manifest_sha256": "...",
  "timestamp": "...",
  "result": "PASS"
}
```

强制规则：

1. `candidate_sha` 必须等于被验证的 Git HEAD；不等于则 `INVALID`。
2. Evidence 必须由验证器生成，不能由 Builder 手写“通过声明”。
3. 受保护验收集默认 `tests_skipped = 0`、`tests_failed = 0`；否则不得发布 `CI_VERIFIED`。
4. 本地结果只能叫 `LOCAL_VERIFIED`；干净环境重新 checkout Candidate SHA 的 CI 结果才可叫 `CI_VERIFIED`。
5. Evidence、输入数据、策略合同和结果产物都要记录哈希，不能只保存一张截图或一段自然语言。

## 6. QQQ 必须具备的量化 Gate

| Gate | 必须验证的内容 | 失败处理 |
|---|---|---|
| Scope Gate | 只修改任务允许路径；禁止越权改策略、Golden、合同和 CI 验收配置 | `BLOCKED`，无需先跑业务测试 |
| Test Integrity Gate | 断言、Case 数量、skip/xfail、fixture、tolerance、真实数据依赖没有被弱化 | `BLOCKED` |
| Strategy Fidelity Gate | 固定历史日期的状态、指标、原因码、目标权重和版本完全符合 Golden | `FAIL` |
| Look-ahead Gate | 截断未来数据或替换为噪声时，截断点之前的信号不改变 | `FAIL` |
| Signal Timing Gate | `signal_date=t` 只能使用 `t` 及以前数据；收盘信号不能用于当日成交 | `FAIL` |
| Execution Timing Gate | 执行日是下一个有效交易日，执行价格口径来自冻结合同 | `FAIL` |
| Data Quality Gate | 缺失、过期、重复、非交易日、异常价格或跨源冲突不会被静默填补 | `STALE/PARTIAL/FAILED/NEEDS_REVIEW`，禁止确认发布 |
| Weight Gate | 权重非负、总和为 1、单资产上限和现金下限可重算 | `FAIL` |
| Portfolio Reconciliation Gate | 现金、持仓、成交、成本、净值、峰值和回撤逐日对账；重复账本事件必须幂等或拒绝 | `FAIL` |
| Cost Gate | 绝对换手、手续费、滑点和成本情景进入净收益；提高成本不得无理由提高净收益 | `FAIL` |
| Determinism Gate | 相同输入、版本和数据重跑得到相同状态、权重、账本和结果哈希 | `FAIL` |
| API Contract Gate | API 结果来自正式后端模型，携带版本、`as_of`、质量状态和 `run_id` | `FAIL` |
| Regression Gate | 原有已批准功能、基准曲线和导出能力没有被破坏 | `FAIL` |

## 7. 不可变的时间、数据与执行约束

这些约束应进入机器可读的 Protected Verification Contract：

- 信号日使用当日收盘及以前可获得的数据；执行日严格晚于信号日，并且必须是有效交易日；
- 预热不足时为 `WARMING`，默认 BIL 100% 或合同指定的现金代理，不能把缺失值当作 0/False 继续交易；
- 原始行情快照只追加、不覆盖；供应商修订必须产生新 `data_version` 和差异记录；
- `close`、`adjusted_close`、分红/拆分、代理资产和基准口径必须明确，不能把 VIX、VXX、SVXY、QLD、BIL/现金代理当成同一种资产；
- 目标权重只能由冻结策略服务产生，前端不能提交任意覆盖；
- `signal_date > execution_date`、未知策略版本、非交易日执行、权重不闭合、负现金和重复不可解释账本事件必须被拒绝；
- paper ledger 只追加；更正通过新事件或新运行版本表达，不删除或改写已确认历史；
- 每次结果都必须可追溯到 `run_id`、`strategy_version`、`code_version/git_commit`、`data_version`、`feature_version`、`cost_model_version`、`calendar_version` 和产物哈希。

初版建议把以下容差写入受保护合同，而不是散落在测试代码中：

```yaml
portfolio_weight_tolerance: 1e-8
return_tolerance: 1e-10
price_tolerance: 1e-6
default_cost_bps: 5
stress_cost_bps: [10, 25, 50]
```

执行价格（下一交易日开盘、收盘或其他可交易时点）、total-return 口径、连续权重/整数份额以及基准是否扣成本，必须在 QG-010 前明确；未确定时不得把结果标成同一冻结版本。

## 8. Golden Dataset 与 Negative Tests

### 8.1 Golden Dataset

Golden 数据必须来自：

```text
已冻结的旧策略/规则
+ 独立历史数据快照
+ 人工或第二套实现核对
```

不能由当前 Candidate 自己生成再自我验证。每个 Case 至少包含：

```json
{
  "signal_date": "...",
  "execution_date": "...",
  "strategy_version": "...",
  "state": "...",
  "indicators": {"name": {"value": 0, "ready": true}},
  "target_weights": {"QQQ": 0.0, "BIL": 1.0},
  "reason_codes": [],
  "input_data_version": "..."
}
```

Case 必须覆盖预热、绿色、黄色、红色、快速保护、阈值边界、连续确认、长回撤、V 型反弹、数据缺口和交易日切换等场景。2018Q4、2020、2022 等历史压力阶段可作为代表性样例，但最终日期与期望值必须独立冻结并哈希。

### 8.2 Negative Tests

至少拒绝或安全降级以下状态：

- 输入未来日期或使用未来字段；
- VIX/价格缺失、过期或质量不合格；
- `signal_date >= execution_date` 或执行日不是交易日；
- `strategy_version` 不存在或配置哈希不匹配；
- 权重之和不为 1、出现负权重/越过上限；
- 现金为负、持仓/成交无法对账；
- 重复 ledger event、重复运行或并发写入导致重复记账；
- 基准共同日期为空、上市前价格被补齐或调整口径冲突；
- 任务没有推进数据日期却被标成成功更新。

## 9. 三层验证与发布状态

“代码已实现”“规则已验证”“策略已批准”是三个不同事实：

| 状态 | 含义 | 不代表什么 |
|---|---|---|
| `RESEARCH_ONLY` | 研究实验或候选输出 | 不是产品默认，更不是实盘授权 |
| `IMPLEMENTED` | lint/unit/typecheck 等开发检查通过 | 不代表量化行为正确 |
| `LOCAL_VERIFIED` | 独立本地 Harness、Golden 和量化 Gate 通过 | 不代表干净环境或持续运行通过 |
| `CI_VERIFIED` | CI 在干净环境按 Candidate SHA 通过并有 Evidence | 不代表未来收益或策略经济有效 |
| `PAPER_SHADOW` | 固定版本连续至少 20 个交易日记录信号、目标、模拟执行、成本、净值和质量 | 不代表真实成交 |
| `PRODUCT_BASELINE` | 用户明确批准的产品展示/模拟版本 | 不等于允许真实交易 |
| `BLOCKED` / `UNVERIFIED` | 越权、失败、缺证据或口径未定 | 禁止发布为确认结果 |

`CI_VERIFIED` 是程序验证等级，不是投资结论；研究报告仍必须报告失败年份、成本敏感性、样本外范围、数据局限和 QQQ 对比，不能用它承诺未来收益。

## 10. 变更审批与停止条件

以下变更必须创建新版本、新配置、新输出目录和新报告，不能覆盖旧结果：

- 策略规则、阈值、资产池、权重、状态机、信号延迟或执行价；
- 数据供应商、价格调整口径、交易日历或代理资产；
- 成本、滑点、风险自由利率或基准定义；
- API schema、数据库迁移、账本规则、调度任务或外部服务；
- 任何真实券商、密钥、订单或公网部署能力。

遇到以下情况必须停止当前任务并报告：

- Candidate 修改了受保护路径；
- Golden、合同或 Evidence 哈希不匹配；
- 发现未来函数、信号/执行错位、权重不闭合、非幂等或重复账本；
- 数据缺失/冲突无法解释，或任务只能靠静默填补继续运行；
- 当前代码无法区分 `final_regime_ensemble`、v10 和 `candidate_096` 的真实版本；
- 用户要求用单一年份、最近行情、截图或一次排名直接选择参数；
- 需要真实账户、外部发送或超出当前 paper 范围的权限。

## 11. QG 实施顺序

```text
QG-000  Verification Governance Baseline
QG-010  Protected Verification Contract
QG-020  独立冻结的 Golden Dataset
QG-030  Independent Verification Harness
QG-040  Quant Integrity Gates（含核心 Negative Tests）
QG-050  CI Evidence 与 Candidate SHA 绑定
QG-060  Mutation Testing / 扩展 Negative Tests
```

第一阶段至少完成 QG-000 至 QG-050，才允许把某个实现称为 `CI_VERIFIED`；QG-060 用于进一步证明测试能杀死错误代码，但不能替代 Golden、时间因果、数据质量和账本对账。

## 12. 最终裁决句

> **Builder 负责产生可追溯的 Candidate；独立验证系统负责决定 Candidate 是否满足冻结合同；用户负责批准策略版本和产品边界。**

可信度来自不可变数据、独立期望值、受保护测试、干净环境、SHA 绑定 Evidence 和可复现账本，而不是来自更多 Agent 的互相评价或“所有测试均通过”的自然语言声明。
