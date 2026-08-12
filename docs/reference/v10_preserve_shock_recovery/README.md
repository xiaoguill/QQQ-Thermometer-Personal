# v10 静态参考快照

这是一份从原 `FinRL-Trading` 工作区复制而来的研究证据快照，用于让个人版在不依赖原仓库未提交文件的情况下保留 v10 候选的审计入口。

包含：

- `REPORT.md`：候选版本、规则、主要指标和局限。
- `variant_spec.json`：候选参数契约。
- `summary_*.csv`：本地核心与 Alpaca 代理的摘要指标。
- `annual_*.csv`：逐年结果。
- `delay_sensitivity.csv`：执行延迟敏感性。
- `neighbor_stability.csv`：预先声明邻域的稳定性。
- `prefix_stability.csv`：历史前缀稳定性检查。

这些文件是只读参考，不是新的运行输出，也不应被当成实时行情。完整实验脚本、缓存和大规模中间结果仍留在原仓库的独立工作区中；未来若迁移代码，必须先补齐依赖闭包、数据版本和测试。
