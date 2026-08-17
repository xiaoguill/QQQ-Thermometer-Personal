const STATE_LABELS = Object.freeze({
  warming: "预热",
  normal: "正常",
  shock: "冲击",
  recovery: "恢复",
  needs_review: "待复核",
  green: "绿色",
  yellow: "黄色",
  red: "红色",
  fast_guard: "快速防守",
});

const STATE_TONES = Object.freeze({
  warming: "neutral",
  normal: "confirmed",
  shock: "shock",
  recovery: "recovery",
  needs_review: "review",
  green: "confirmed",
  yellow: "recovery",
  red: "shock",
  fast_guard: "shock",
});

const QUALITY_LABELS = Object.freeze({
  ok: "正常",
  stale: "过期",
  partial: "部分数据",
  failed: "失败",
  needs_review: "待复核",
});

const CONFIRMATION_LABELS = Object.freeze({
  confirmed: "已确认",
  provisional: "临时观察",
  needs_review: "待复核",
});

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringValue(value, fallback = "未提供") {
  return typeof value === "string" && value.length > 0 ? value : fallback;
}

function numericDisplay(value, digits = 2) {
  const number = finiteNumber(value);
  return number === null ? "未提供" : number.toFixed(digits);
}

function percentDisplay(value) {
  const number = finiteNumber(value);
  return number === null ? "未提供" : `${(number * 100).toFixed(1)}%`;
}

function clampPercent(value) {
  const number = finiteNumber(value);
  if (number === null) return null;
  return Math.min(100, Math.max(0, number));
}

function safeJson(value) {
  if (value === null || value === undefined) return "未提供";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch (_error) {
    return "无法展示";
  }
}

function normalizeConfirmation(meta) {
  const explicit = isRecord(meta) ? meta.confirmation_status : null;
  if (typeof explicit === "string" && Object.hasOwn(CONFIRMATION_LABELS, explicit)) {
    return {
      key: explicit,
      label: CONFIRMATION_LABELS[explicit],
      note: "确认状态来自后端明确字段。",
    };
  }
  return {
    key: "not_provided",
    label: "未提供",
    note: "当前 API 契约未声明 confirmation_status；前端不根据日期或质量推断。",
  };
}

function normalizeWeights(rawWeights) {
  if (!isRecord(rawWeights)) {
    return { rows: [], invalidCount: 0, missing: true, total: null };
  }
  const rows = [];
  let invalidCount = 0;
  for (const [asset, value] of Object.entries(rawWeights)) {
    const number = finiteNumber(value);
    if (number === null || number < 0 || typeof asset !== "string" || asset.length === 0) {
      invalidCount += 1;
      continue;
    }
    rows.push({ asset, value: number, display: percentDisplay(number), barPercent: clampPercent(number * 100) ?? 0 });
  }
  rows.sort((left, right) => left.asset.localeCompare(right.asset));
  return {
    rows,
    invalidCount,
    missing: false,
    total: rows.reduce((sum, row) => sum + row.value, 0),
  };
}

export function normalizeDashboardPayload({ latest, explanation = null, quality = null, mode = "LIVE" } = {}) {
  const latestRecord = isRecord(latest) ? latest : {};
  const meta = isRecord(latestRecord.meta) ? latestRecord.meta : {};
  const snapshot = isRecord(latestRecord.data) ? latestRecord.data : {};
  const explanationRecord = isRecord(explanation) ? explanation : {};
  const explanationData = isRecord(explanationRecord.data) ? explanationRecord.data : {};
  const qualityRecord = isRecord(quality) ? quality : {};
  const qualityData = isRecord(qualityRecord.data) ? qualityRecord.data : {};
  const stateKey = typeof snapshot.state === "string" ? snapshot.state : "unknown";
  const rawQuality = typeof qualityData.status === "string" ? qualityData.status : meta.data_quality;
  const qualityKey = typeof rawQuality === "string" ? rawQuality.toLowerCase() : "unknown";
  const reasonCodes = Array.isArray(snapshot.reason_codes)
    ? snapshot.reason_codes.filter((item) => typeof item === "string")
    : Array.isArray(explanationData.reason_codes)
      ? explanationData.reason_codes.filter((item) => typeof item === "string")
      : [];
  const indicators = isRecord(explanationData.indicators) ? explanationData.indicators : {};
  const weights = normalizeWeights(snapshot.target_weights);
  const temperature = finiteNumber(snapshot.temperature);
  const agreement = finiteNumber(snapshot.signal_agreement);
  const missing = [];
  if (!stateKey || stateKey === "unknown") missing.push("state");
  if (!Array.isArray(snapshot.reason_codes)) missing.push("reason_codes");
  if (weights.missing) missing.push("target_weights");
  if (!isRecord(explanationData.indicators) || Object.keys(explanationData.indicators).length === 0) {
    missing.push("indicators");
  }

  return Object.freeze({
    mode: mode === "SIMULATED" ? "SIMULATED" : "LIVE",
    state: Object.freeze({
      key: stateKey,
      label: STATE_LABELS[stateKey] || "未提供",
      tone: STATE_TONES[stateKey] || "neutral",
    }),
    temperature: Object.freeze({
      value: temperature,
      display: numericDisplay(temperature, 1),
      gaugePercent: clampPercent(temperature),
    }),
    trend: stringValue(snapshot.trend),
    agreement: Object.freeze({
      value: agreement,
      display: agreement === null ? "未提供" : percentDisplay(agreement),
      barPercent: clampPercent(agreement === null ? null : agreement * 100),
      isProbability: false,
    }),
    confirmation: Object.freeze(normalizeConfirmation(meta)),
    quality: Object.freeze({
      key: qualityKey,
      label: QUALITY_LABELS[qualityKey] || "未提供",
      issues: Array.isArray(qualityData.issues) ? qualityData.issues.filter((item) => typeof item === "string") : [],
    }),
    weights: Object.freeze(weights),
    reasonCodes: Object.freeze(reasonCodes),
    indicators: Object.freeze(indicators),
    metadata: Object.freeze({
      contractVersion: stringValue(meta.contract_version),
      strategyVersion: stringValue(meta.strategy_version),
      asOf: stringValue(meta.as_of),
      signalDate: stringValue(meta.signal_date),
      executionDate: stringValue(meta.execution_date),
      runId: stringValue(meta.run_id),
      codeVersion: stringValue(meta.code_version),
      dataVersion: stringValue(meta.data_version),
      evidenceRef: stringValue(meta.evidence_ref),
    }),
    missing: Object.freeze(missing),
    explanationAvailable: Object.keys(indicators).length > 0 || reasonCodes.length > 0,
    safeJson,
  });
}

export { STATE_LABELS, STATE_TONES, QUALITY_LABELS, normalizeConfirmation, normalizeWeights, percentDisplay };
