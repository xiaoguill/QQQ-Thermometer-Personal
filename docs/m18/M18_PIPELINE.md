# M18.2 M02–M07 串联边界

`src/jobs/m18/full_chain.py` 只接收已经由 M02 捕获并校验的 `RawSnapshot`。它按以下顺序调用既有实现：

```text
M02 RawSnapshot
  ├─ 按请求语义分组
  └─ M03 normalize_snapshots
       └─ M04 calculate_indicator_snapshots
            └─ M05 replay_regimes / evaluate_regime
                 └─ M06 build_explanation
                      └─ M07 build_target_weights
```

QQQ ETF 与 VIX/VIX3M 指数请求的价格基础不同，M03 必须分别标准化，M04 再按同一交易日合并；M18 不绕过这个边界。

即使 M07 返回候选目标，M18 也只有在 M05 状态确认、M06 发布状态确认且质量为 `OK` 时，才把目标写入 `confirmed_strategy.target_weights`。质量失败、预热、缺失、过期和未授权输入只保留诊断结果。

M18.2 不访问网络、不写数据库、不启动调度器。M08 持久化、M09 纸上组合、M10 API 和 M11 运行状态由后续 M18.3 负责。
