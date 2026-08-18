# M18 全链路读模型契约

M18 是 M00.5–M17 的运行投影层，不创建第二套策略。页面只读取本契约的后端结果，不在浏览器计算状态、温度或目标权重。

## 两类温度

- `provisional_observation`：来源为 M16，发布标记为 `PROVISIONAL`。它可以反映盘中观察，但不能生成或修改确认目标仓位。
- `confirmed_strategy`：来源为正式的 M05–M07 状态/目标链路及 M10 只读模型，发布标记为 `CONFIRMED`。只有它可以带 `target_weights`。

当来源缺失、过期、未授权或质量不是 `OK` 时，温度和目标仓位必须为空，并显示失败原因；不能用旧值静默补齐。

## 模块状态

`modules` 按固定顺序包含 `M00.5` 至 `M17`。每项至少包含：

- `module_id`、`name`、`responsibility`
- `status`、`publication`、`quality`
- `version`、`run_id`、`as_of`、`signal_date`、`execution_date`
- `artifact_ref`、`reason_codes`、`depends_on`

`READY` 只能与 `OK` 同时出现；`CONFIRMED` 只能与 `READY/OK` 同时出现。`NOT_RUN` 必须使用 `NOT_RUN` 质量，便于区分“没有执行”和“执行失败”。

## 运行边界

`runtime_boundary` 只记录是否配置了数据源、刷新周期、来源状态、东八区展示时区和安全边界。它不保存 API key 本身；运行仍然是 loopback-only、paper-only、execution-disabled、broker-disconnected。

## 持久化

M18 复用 M08 已有 SQLite `run` 表，不修改 M08 schema。每个 `run_id` 使用 `m18|<run_id>` 记录键，重复写入相同内容幂等，内容不同则由 M08 不可变存储拒绝。
