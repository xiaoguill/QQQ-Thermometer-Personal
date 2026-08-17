import { ApiClientError, createApiClient } from "../shell/api-client.mjs";
import { M14_FIXTURES } from "./fixtures.mjs";
import { normalizeM14Payloads } from "./view-model.mjs";

const root = document.querySelector("[data-m14-root]");
const liveButton = document.querySelector('[data-action="load-live"]');
const fixtureButtons = [...document.querySelectorAll("[data-fixture]")];

function query(selector) {
  return document.querySelector(selector);
}

function setText(selector, value) {
  document.querySelectorAll(selector).forEach((element) => {
    element.textContent = value;
  });
}

function clear(element) {
  while (element?.firstChild) element.removeChild(element.firstChild);
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

function appendEmptyRow(body, colspan, message) {
  if (!body) return;
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = colspan;
  cell.className = "empty-row";
  cell.textContent = message;
  row.appendChild(cell);
  body.appendChild(row);
}

function renderTable(selector, rows, columns, emptyMessage) {
  const body = query(selector);
  if (!body) return;
  clear(body);
  if (!rows.length) {
    appendEmptyRow(body, columns.length, emptyMessage);
    return;
  }
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    columns.forEach(([key, value]) => {
      const td = document.createElement("td");
      td.dataset.field = key;
      td.textContent = safeJson(typeof value === "function" ? value(row) : row[value]);
      tr.appendChild(td);
    });
    body.appendChild(tr);
  });
}

function setTone(element, prefix, tone) {
  if (!element) return;
  [...element.classList].filter((name) => name.startsWith(`${prefix}-`)).forEach((name) => element.classList.remove(name));
  element.classList.add(`${prefix}-${tone}`);
}

function renderScalarFields(metrics) {
  const list = query("[data-metric-list]");
  if (!list) return;
  clear(list);
  metrics.scalarFields.forEach((field) => {
    const item = document.createElement("li");
    const label = document.createElement("span");
    const value = document.createElement("code");
    label.textContent = field.label;
    value.textContent = field.display;
    if (!field.provided) item.dataset.missing = "true";
    item.append(label, value);
    list.appendChild(item);
  });
}

function renderNamedRows(selector, collection, emptyMessage) {
  const list = query(selector);
  if (!list) return;
  clear(list);
  if (!collection.provided || !collection.rows.length) {
    const item = document.createElement("li");
    item.className = "empty-row";
    item.textContent = emptyMessage;
    list.appendChild(item);
    return;
  }
  collection.rows.forEach((row) => {
    const item = document.createElement("li");
    const label = document.createElement("span");
    const value = document.createElement("code");
    label.textContent = safeJson(row.label);
    value.textContent = safeJson(row.value);
    item.append(label, value);
    list.appendChild(item);
  });
}

function renderEndpointProvenance(endpoints) {
  const rows = Object.values(endpoints).map((endpoint) => ({
    endpoint: endpoint.label,
    quality: endpoint.quality,
    asOf: endpoint.meta.asOf,
    signalDate: endpoint.meta.signalDate,
    executionDate: endpoint.meta.executionDate,
    strategyVersion: endpoint.meta.strategyVersion,
    runId: endpoint.meta.runId,
    codeVersion: endpoint.meta.codeVersion,
    dataVersion: endpoint.meta.dataVersion,
    evidenceRef: endpoint.meta.evidenceRef,
  }));
  renderTable(
    "[data-provenance-body]",
    rows,
    [
      ["endpoint", "endpoint"],
      ["quality", "quality"],
      ["as_of", "asOf"],
      ["signal_date", "signalDate"],
      ["execution_date", "executionDate"],
      ["strategy_version", "strategyVersion"],
      ["run_id", "runId"],
      ["code_version", "codeVersion"],
      ["data_version", "dataVersion"],
      ["evidence_ref", "evidenceRef"],
    ],
    "未提供接口审计记录",
  );
}

function renderQuality(quality) {
  const panel = query("[data-quality-panel]");
  setTone(panel, "quality-tone", quality.status);
  setText("[data-quality-value]", quality.statusLabel);
  setText("[data-quality-key]", quality.status);
  const list = query("[data-quality-issues]");
  clear(list);
  if (quality.issues.length === 0) {
    appendEmptyRow(list, 1, quality.kind === "error" ? quality.message : "没有额外质量问题");
    return;
  }
  quality.issues.forEach((issue) => {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.textContent = safeJson(issue);
    row.appendChild(cell);
    list.appendChild(row);
  });
}

function renderView(view, statusOverride = "") {
  root.dataset.mode = view.mode;
  root.dataset.stage = view.hasAnyFailure ? "partial" : "ready";
  setText("[data-mode-label]", view.mode);
  setText("[data-dashboard-status]", statusOverride || `${view.mode} · 所有数字均为后端字段透传；前端不计算收益或回撤。`);

  const { history, curve, metrics, ledger, versions, quality } = view.endpoints;
  setText("[data-history-status]", history.kind === "error" ? history.message : `${history.quality} · ${history.rows.length} 行`);
  setText("[data-curve-status]", curve.kind === "error" ? curve.message : `${curve.quality} · ${curve.rows.length} 行`);
  setText("[data-metrics-status]", metrics.kind === "error" ? metrics.message : `${metrics.quality} · 后端指标原值`);
  setText("[data-ledger-status]", ledger.kind === "error" ? ledger.message : `${ledger.quality} · ${ledger.rows.length} 行`);
  setText("[data-versions-status]", versions.kind === "error" ? versions.message : `${versions.quality} · ${versions.rows.length} 行`);
  setText("[data-missing-notice]", view.failedCount ? `有 ${view.failedCount} 个接口失败；不展示旧结果为当前结果。` : "缺失字段保持未提供；不从日期、质量或其他接口推断。");

  renderTable("[data-history-body]", history.rows, [
    ["index", "index"],
    ["signal_date", "signalDate"],
    ["execution_date", "executionDate"],
    ["state", "state"],
    ["temperature", "temperature"],
    ["trend", "trend"],
    ["agreement", "agreement"],
    ["data_quality", "dataQuality"],
    ["run_id", "runId"],
    ["reason_codes", "reasonCodes"],
    ["target_weights", "targetWeights"],
  ], "后端未提供历史回放行；不能从顶层 meta 补齐逐行日期。");
  renderTable("[data-curve-body]", curve.rows, [
    ["index", "index"],
    ["execution_date", "executionDate"],
    ["nav", "nav"],
    ["cash", "cash"],
    ["strategy_return", "strategyReturn"],
    ["qqq_nav", "qqqNav"],
    ["qqq_return", "qqqReturn"],
    ["drawdown", "drawdown"],
    ["cost_bps", "costBps"],
    ["cost_adjusted_nav", "costAdjustedNav"],
    ["data_quality", "dataQuality"],
  ], "后端未提供净值曲线；不自行计算收益、回撤或 QQQ 基准。");

  renderScalarFields(metrics);
  renderNamedRows("[data-annual-returns]", metrics.annualReturns, "后端未提供逐年收益；不由净值曲线计算。");
  renderNamedRows("[data-annual-drawdowns]", metrics.annualDrawdowns, "后端未提供逐年回撤；不由净值曲线计算。");
  renderNamedRows("[data-benchmark-list]", metrics.benchmark, "后端未提供 QQQ 基准对齐字段。");
  renderNamedRows("[data-cost-list]", metrics.costStress, "后端未提供成本压力场景；不自行套用 bps 或冻结合同口径。");
  setText("[data-audit-field-status]", metrics.audit.provided ? `后端字段：${metrics.audit.key}` : "未提供审计 manifest / 可复现性字段");
  setText("[data-audit-fields]", metrics.audit.provided ? safeJson(metrics.audit.value) : "未提供；不能把本页数字称为已验证回测结论。");

  renderTable("[data-ledger-body]", ledger.rows, [
    ["index", "index"],
    ["event_date", "eventDate"],
    ["event_type", "eventType"],
    ["symbol", "symbol"],
    ["quantity", "quantity"],
    ["price", "price"],
    ["cost", "cost"],
    ["status", "status"],
    ["data_quality", "dataQuality"],
    ["run_id", "runId"],
    ["idempotency_key", "idempotencyKey"],
    ["evidence_ref", "evidenceRef"],
  ], "后端未提供纸上账本行；此页面没有写入能力。");
  renderTable("[data-versions-body]", versions.rows, [
    ["index", "index"],
    ["version", "version"],
    ["strategy_version", "strategyVersion"],
    ["implementation_version", "implementationVersion"],
    ["status", "status"],
    ["config_hash", "configHash"],
    ["code_version", "codeVersion"],
    ["data_version", "dataVersion"],
    ["approved_at", "approvedAt"],
    ["evidence_ref", "evidenceRef"],
  ], "后端未提供版本记录；不能从页面标题猜测版本。");
  renderEndpointProvenance(view.endpoints);
  renderQuality(quality);
}

function failedPayload(message) {
  return { kind: "error", message };
}

function renderLoading() {
  const message = "正在读取同源本地 API……";
  renderView(normalizeM14Payloads({
    history: failedPayload(message),
    curve: failedPayload(message),
    metrics: failedPayload(message),
    ledger: failedPayload(message),
    versions: failedPayload(message),
    quality: failedPayload(message),
    mode: "LIVE",
  }), message);
  root.dataset.stage = "loading";
  if (liveButton) liveButton.disabled = true;
}

function controlValues() {
  const from = query("[data-control-from]")?.value || undefined;
  const to = query("[data-control-to]")?.value || undefined;
  const asOf = query("[data-control-as-of]")?.value || undefined;
  const rawLimit = query("[data-control-limit]")?.value || "100";
  const limit = Number(rawLimit);
  if (!Number.isInteger(limit) || limit < 1 || limit > 500) {
    return { error: "limit 必须是 1 到 500 的整数；尚未请求 API。" };
  }
  return { from, to, asOf, limit };
}

async function loadLive() {
  const controls = controlValues();
  if (controls.error) {
    setText("[data-dashboard-status]", controls.error);
    return;
  }
  markFixture("");
  renderLoading();
  try {
    const client = createApiClient({ baseUrl: root.dataset.apiBase || "/api" });
    const requests = {
      history: () => client.getHistory({ from: controls.from, to: controls.to, limit: controls.limit }),
      curve: () => client.getPerformanceCurve({ as_of: controls.asOf }),
      metrics: () => client.getPerformanceMetrics({ as_of: controls.asOf }),
      ledger: () => client.getLedger({ from: controls.from, to: controls.to, limit: controls.limit }),
      versions: () => client.getVersions(),
      quality: () => client.getDataQuality(),
    };
    const entries = await Promise.all(Object.entries(requests).map(async ([key, request]) => {
      try {
        return [key, await request()];
      } catch (error) {
        return [key, failedPayload(error instanceof ApiClientError ? error.message : "本地 API 返回不可用结果")];
      }
    }));
    renderView(normalizeM14Payloads({ ...Object.fromEntries(entries), mode: "LIVE" }));
  } catch (error) {
    const message = error instanceof ApiClientError ? error.message : "本地 API 返回不可用结果";
    renderView(normalizeM14Payloads({
      history: failedPayload(message),
      curve: failedPayload(message),
      metrics: failedPayload(message),
      ledger: failedPayload(message),
      versions: failedPayload(message),
      quality: failedPayload(message),
      mode: "LIVE",
    }), message);
    root.dataset.stage = "failed";
  } finally {
    if (liveButton) liveButton.disabled = false;
  }
}

function markFixture(key) {
  fixtureButtons.forEach((button) => {
    const active = button.dataset.fixture === key;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function renderFixture(key) {
  const fixture = M14_FIXTURES[key];
  if (!fixture) return;
  markFixture(key);
  renderView(normalizeM14Payloads({ ...fixture, mode: "SIMULATED" }));
}

fixtureButtons.forEach((button) => button.addEventListener("click", () => renderFixture(button.dataset.fixture)));
liveButton?.addEventListener("click", loadLive);
renderFixture("complete");

export { loadLive, renderFixture, renderView, normalizeM14Payloads };
