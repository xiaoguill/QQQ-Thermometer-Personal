# Frozen API Contract

`openapi.json` 是 QQQ Thermometer Personal 的前后端协作契约，版本 `1.0.0`。

它定义的是个人本地、paper-only、只读优先的结果接口。前端 Builder 只能消费契约中的字段；后端 Builder 必须实现契约；任何一方都不能在普通 Candidate 中修改契约、策略阈值、权重或错误语义。

契约要求响应携带 `contract_version`、`strategy_version`、`as_of`、`signal_date`、`execution_date`、`data_quality` 和 `run_id`。前端不得复制策略公式、根据颜色推断状态或提交任意目标权重。`POST /api/paper/confirm` 只记录纸上观察，不创建真实订单。

契约变更必须是独立的 Verification Change Task，并同步更新受保护引用、Golden/Negative 测试、版本号和 Evidence；不能由前端或后端 Builder 顺手修改。
