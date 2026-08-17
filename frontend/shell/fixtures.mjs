const makeFixture = (id, tone, symbol, label, title, description, metadata) => Object.freeze({
  id,
  tone,
  symbol,
  label,
  title,
  description,
  metadata,
});

export const SHELL_FIXTURES = Object.freeze({
  loading: makeFixture(
    "shell-loading-v1",
    "loading",
    "…",
    "加载中",
    "等待本地数据接口",
    "尚未读取任何状态快照；壳层不会根据日期或颜色推断策略状态。",
    "等待响应",
  ),
  empty: makeFixture(
    "shell-empty-v1",
    "empty",
    "○",
    "暂无快照",
    "尚无可展示的确认快照",
    "请先由本地任务生成可追溯快照；空数据不会被渲染成正常状态。",
    "无记录",
  ),
  stale: makeFixture(
    "shell-stale-v1",
    "stale",
    "!",
    "数据过期",
    "已有数据，但不满足新鲜度要求",
    "壳层会保留问题说明，并要求在继续使用前复核数据时间和质量。",
    "需复核时间",
  ),
  partial: makeFixture(
    "shell-partial-v1",
    "partial",
    "△",
    "部分数据",
    "响应只包含部分字段",
    "缺失字段不会由浏览器补算；目标仓位和策略结论继续显示为未提供。",
    "字段不完整",
  ),
  failed: makeFixture(
    "shell-failed-v1",
    "failed",
    "×",
    "读取失败",
    "本地 API 没有返回可用结果",
    "错误信息会以安全文本展示；壳层不会重试、下单或切换数据源。",
    "请求未确认",
  ),
  needs_review: makeFixture(
    "shell-needs-review-v1",
    "review",
    "?",
    "待复核",
    "数据质量需要人工检查",
    "在质量状态恢复前，页面只展示问题上下文，不把结果标为确认信号。",
    "人工复核",
  ),
});

export function getShellFixture(key) {
  return SHELL_FIXTURES[key] || SHELL_FIXTURES.empty;
}
