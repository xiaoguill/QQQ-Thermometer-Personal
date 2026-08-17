const QUALITY_LABELS = Object.freeze({
  ok: "正常",
  stale: "过期",
  partial: "部分数据",
  failed: "失败",
  needs_review: "待复核",
  unknown: "未提供",
});

const ENDPOINT_LABELS = Object.freeze({
  history: "历史温度计",
  curve: "净值曲线",
  metrics: "表现指标",
  ledger: "纸上账本",
  versions: "版本记录",
  quality: "数据质量",
});

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasOwn(record, key) {
  return isRecord(record) && Object.prototype.hasOwnProperty.call(record, key);
}

function direct(record, key) {
  return hasOwn(record, key) ? record[key] : null;
}

function safeJson(value) {
  if (value === null || value === undefined || value === "") return "未提供";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch (_error) {
    return "无法展示";
  }
}

function directDisplay(value) {
  return value === null || value === undefined || value === "" ? "未提供" : safeJson(value);
}

function qualityKey(value) {
  return typeof value === "string" && value.length > 0 ? value.toLowerCase() : "unknown";
}

function normalizeMeta(meta) {
  const source = isRecord(meta) ? meta : {};
  return Object.freeze({
    contractVersion: direct(source, "contract_version"),
    strategyVersion: direct(source, "strategy_version"),
    asOf: direct(source, "as_of"),
    signalDate: direct(source, "signal_date"),
    executionDate: direct(source, "execution_date"),
    dataQuality: direct(source, "data_quality"),
    runId: direct(source, "run_id"),
    codeVersion: direct(source, "code_version"),
    dataVersion: direct(source, "data_version"),
    evidenceRef: direct(source, "evidence_ref"),
  });
}

function endpointEnvelope(payload, key) {
  if (isRecord(payload) && payload.kind === "error") {
    return Object.freeze({
      key,
      label: ENDPOINT_LABELS[key] || key,
      kind: "error",
      message: typeof payload.message === "string" ? payload.message : "接口返回失败",
      meta: normalizeMeta(null),
      data: null,
      quality: "failed",
    });
  }
  const source = isRecord(payload) ? payload : {};
  const meta = normalizeMeta(source.meta);
  return Object.freeze({
    key,
    label: ENDPOINT_LABELS[key] || key,
    kind: "response",
    message: "",
    meta,
    data: hasOwn(source, "data") ? source.data : null,
    quality: qualityKey(meta.dataQuality),
  });
}

function normalizeHistory(payload) {
  const envelope = endpointEnvelope(payload, "history");
  const rawRows = Array.isArray(envelope.data) ? envelope.data : [];
  const rows = rawRows.map((raw, index) => {
    const row = isRecord(raw) ? raw : {};
    return Object.freeze({
      index: index + 1,
      signalDate: direct(row, "signal_date"),
      executionDate: direct(row, "execution_date"),
      asOf: direct(row, "as_of"),
      dataQuality: direct(row, "data_quality"),
      runId: direct(row, "run_id"),
      evidenceRef: direct(row, "evidence_ref"),
      strategyVersion: direct(row, "strategy_version"),
      state: direct(row, "state"),
      temperature: direct(row, "temperature"),
      trend: direct(row, "trend"),
      agreement: direct(row, "signal_agreement"),
      reasonCodes: direct(row, "reason_codes"),
      targetWeights: direct(row, "target_weights"),
    });
  });
  return Object.freeze({ ...envelope, rows, missingRows: rows.length === 0 });
}

function normalizeCurve(payload) {
  const envelope = endpointEnvelope(payload, "curve");
  const rawRows = Array.isArray(envelope.data) ? envelope.data : [];
  const rows = rawRows.map((raw, index) => {
    const row = isRecord(raw) ? raw : {};
    return Object.freeze({
      index: index + 1,
      executionDate: direct(row, "execution_date"),
      nav: direct(row, "nav"),
      cash: direct(row, "cash"),
      dataQuality: direct(row, "data_quality"),
      strategyReturn: direct(row, "strategy_return"),
      qqqNav: direct(row, "qqq_nav"),
      qqqReturn: direct(row, "qqq_return"),
      drawdown: direct(row, "drawdown"),
      costBps: direct(row, "cost_bps"),
      costAdjustedNav: direct(row, "cost_adjusted_nav"),
    });
  });
  return Object.freeze({ ...envelope, rows, missingRows: rows.length === 0 });
}

const METRIC_FIELDS = Object.freeze([
  ["cagr", "CAGR"],
  ["total_return", "总收益"],
  ["max_drawdown", "最大回撤"],
  ["max_drawdown_duration_days", "最大回撤持续天数"],
  ["sharpe", "Sharpe"],
  ["sortino", "Sortino"],
  ["turnover", "换手"],
  ["total_cost", "总成本"],
  ["period_start", "期间开始"],
  ["period_end", "期间结束"],
]);

function firstField(record, keys) {
  for (const key of keys) {
    if (hasOwn(record, key)) return { provided: true, key, value: record[key] };
  }
  return { provided: false, key: null, value: null };
}

function normalizeNamedRows(value) {
  if (Array.isArray(value)) {
    return value.map((item, index) => {
      if (isRecord(item)) {
        const label = firstField(item, ["year", "label", "period", "cost_bps", "scenario"]).value;
        return Object.freeze({ label: label === null ? String(index + 1) : label, value: item });
      }
      return Object.freeze({ label: String(index + 1), value: item });
    });
  }
  if (isRecord(value)) {
    return Object.entries(value).map(([label, item]) => Object.freeze({ label, value: item }));
  }
  return [];
}

function normalizeScenarioRows(value) {
  const scenarios = isRecord(value) && hasOwn(value, "scenarios") ? value.scenarios : value;
  return normalizeNamedRows(scenarios);
}

function directFieldRows(record, specs) {
  return specs.map(([key, label]) => Object.freeze({
    key,
    label,
    provided: hasOwn(record, key),
    value: direct(record, key),
    display: directDisplay(direct(record, key)),
  }));
}

function normalizeMetrics(payload) {
  const envelope = endpointEnvelope(payload, "metrics");
  const data = isRecord(envelope.data) ? envelope.data : {};
  const annual = firstField(data, ["annual_returns", "yearly_returns"]);
  const annualDrawdown = firstField(data, ["annual_drawdowns", "yearly_drawdowns"]);
  const benchmark = firstField(data, ["qqq_benchmark", "benchmark", "benchmarks"]);
  const costs = firstField(data, ["cost_stress", "cost_scenarios", "cost_sensitivity"]);
  const audit = firstField(data, ["data_manifest", "audit", "audit_manifest", "provenance", "reproducibility"]);
  return Object.freeze({
    ...envelope,
    data,
    scalarFields: Object.freeze(directFieldRows(data, METRIC_FIELDS)),
    annualReturns: Object.freeze({ provided: annual.provided, key: annual.key, rows: normalizeNamedRows(annual.value) }),
    annualDrawdowns: Object.freeze({ provided: annualDrawdown.provided, key: annualDrawdown.key, rows: normalizeNamedRows(annualDrawdown.value) }),
    benchmark: Object.freeze({ provided: benchmark.provided, key: benchmark.key, rows: normalizeNamedRows(benchmark.value) }),
    costStress: Object.freeze({ provided: costs.provided, key: costs.key, rows: normalizeScenarioRows(costs.value) }),
    audit: Object.freeze({ provided: audit.provided, key: audit.key, value: audit.value }),
  });
}

function normalizeLedger(payload) {
  const envelope = endpointEnvelope(payload, "ledger");
  const rawRows = Array.isArray(envelope.data) ? envelope.data : [];
  const rows = rawRows.map((raw, index) => {
    const row = isRecord(raw) ? raw : {};
    return Object.freeze({
      index: index + 1,
      eventDate: direct(row, "event_date"),
      eventType: direct(row, "event_type"),
      symbol: direct(row, "symbol"),
      quantity: direct(row, "quantity"),
      price: direct(row, "price"),
      cost: direct(row, "cost"),
      status: direct(row, "status"),
      dataQuality: direct(row, "data_quality"),
      runId: direct(row, "run_id"),
      idempotencyKey: direct(row, "idempotency_key"),
      evidenceRef: direct(row, "evidence_ref"),
    });
  });
  return Object.freeze({ ...envelope, rows, missingRows: rows.length === 0 });
}

function normalizeVersions(payload) {
  const envelope = endpointEnvelope(payload, "versions");
  const rawRows = Array.isArray(envelope.data) ? envelope.data : [];
  const rows = rawRows.map((raw, index) => {
    const row = isRecord(raw) ? raw : {};
    return Object.freeze({
      index: index + 1,
      version: direct(row, "version"),
      strategyVersion: direct(row, "strategy_version"),
      implementationVersion: direct(row, "implementation_version"),
      status: direct(row, "status"),
      configHash: direct(row, "config_hash"),
      codeVersion: direct(row, "code_version"),
      dataVersion: direct(row, "data_version"),
      approvedAt: direct(row, "approved_at"),
      evidenceRef: direct(row, "evidence_ref"),
      dataQuality: direct(row, "data_quality"),
    });
  });
  return Object.freeze({ ...envelope, rows, missingRows: rows.length === 0 });
}

function normalizeQuality(payload) {
  const envelope = endpointEnvelope(payload, "quality");
  const data = isRecord(envelope.data) ? envelope.data : {};
  const status = qualityKey(direct(data, "status"));
  const issues = Array.isArray(direct(data, "issues")) ? direct(data, "issues") : [];
  return Object.freeze({ ...envelope, status, statusLabel: QUALITY_LABELS[status] || "未提供", issues });
}

export function normalizeM14Payloads({ history, curve, metrics, ledger, versions, quality, mode = "LIVE" } = {}) {
  const endpoints = Object.freeze({
    history: normalizeHistory(history),
    curve: normalizeCurve(curve),
    metrics: normalizeMetrics(metrics),
    ledger: normalizeLedger(ledger),
    versions: normalizeVersions(versions),
    quality: normalizeQuality(quality),
  });
  const failed = Object.values(endpoints).filter((item) => item.kind === "error").length;
  return Object.freeze({
    mode: mode === "SIMULATED" ? "SIMULATED" : "LIVE",
    endpoints,
    failedCount: failed,
    hasAnyFailure: failed > 0,
    safeJson,
  });
}

export {
  ENDPOINT_LABELS,
  QUALITY_LABELS,
  directDisplay,
  normalizeHistory,
  normalizeCurve,
  normalizeMetrics,
  normalizeLedger,
  normalizeVersions,
  normalizeQuality,
  normalizeNamedRows,
};
