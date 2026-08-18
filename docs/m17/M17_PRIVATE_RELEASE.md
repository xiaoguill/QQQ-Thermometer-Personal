# M17 私有候选版本说明

## 版本定位

M17 是在 `verification-baseline-v3.27` 之上的新增候选版本，提供统一本地入口和纸上调仓计划。v3.26 与 M16 的历史标签保持不变；v3.27 是为了登记 M17 Task Contract 的新的 additive Trusted baseline，不覆盖旧边界。

## 变更范围

本版本只新增以下目录：

```text
configs/m17/
configs/paper/
src/api/m17/
src/paper/m17/
frontend/m17/
tests/api/m17/
tests/paper/m17/
tests/frontend/m17/
tests/e2e/m17/
docs/m17/
```

M15、M16、旧 Demo、策略源码、冻结 API contract、Massive 配置和既有回测结果没有被修改。

## 验收摘要

候选版本必须在本地和 GitHub CI 分别运行现有 18 个治理 Gate，并保存 Evidence。最终状态只能报告为 `READY_FOR_VERIFICATION` 或 `BASELINE_PROMOTION_REQUIRED`；在新的 Trusted baseline 被明确提升前，不报告 `VERIFIED`、`CI_VERIFIED` 或 `PRODUCT_BASELINE`。

## 回滚

1. 停止 M17 进程；
2. 继续使用 M16 标签 `m16-private-release-candidate-v2`；
3. 如需恢复 M17，只需切回 M17 候选提交，不需要迁移数据库或删除历史文件。

## 安全承诺

M17 只允许本机监听、只读访问和纸上计算。Massive key 只从进程环境读取；浏览器不接触 key；纸上计划不连接券商；所有写请求拒绝。
