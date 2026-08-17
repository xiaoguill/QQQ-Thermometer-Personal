import { ApiClientError, createApiClient } from "../shell/api-client.mjs";
import { DASHBOARD_FIXTURES } from "./fixtures.mjs";
import { normalizeDashboardPayload } from "./view-model.mjs";

const root = document.querySelector("[data-dashboard-root]");
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

function appendEmpty(list, message) {
  if (!list) return;
  const item = document.createElement("li");
  item.className = "empty-row";
  item.textContent = message;
  list.appendChild(item);
}

function appendTextList(list, values, emptyMessage) {
  if (!list) return;
  clear(list);
  if (!values.length) {
    appendEmpty(list, emptyMessage);
    return;
  }
  values.forEach((value) => {
    const item = document.createElement("li");
    const code = document.createElement("code");
    code.textContent = value;
    item.appendChild(code);
    list.appendChild(item);
  });
}

function appendIndicatorRows(list, indicators, safeJson) {
  if (!list) return;
  clear(list);
  const entries = Object.entries(indicators);
  if (!entries.length) {
    appendEmpty(list, "后端未提供指标解释");
    return;
  }
  entries.sort(([left], [right]) => left.localeCompare(right));
  entries.forEach(([name, value]) => {
    const item = document.createElement("li");
    const label = document.createElement("span");
    const output = document.createElement("code");
    label.textContent = name;
    output.textContent = safeJson(value);
    item.append(label, output);
    list.appendChild(item);
  });
}

function appendMetadataRows(list, metadata) {
  if (!list) return;
  clear(list);
  const fields = [
    ["Contract", metadata.contractVersion],
    ["Strategy version", metadata.strategyVersion],
    ["As of", metadata.asOf],
    ["Signal date", metadata.signalDate],
    ["Execution date", metadata.executionDate],
    ["Run ID", metadata.runId],
    ["Code version", metadata.codeVersion],
    ["Data version", metadata.dataVersion],
    ["Evidence ref", metadata.evidenceRef],
  ];
  fields.forEach(([labelText, value]) => {
    const item = document.createElement("div");
    const label = document.createElement("dt");
    const output = document.createElement("dd");
    label.textContent = labelText;
    output.textContent = value;
    item.append(label, output);
    list.appendChild(item);
  });
}

function appendWeightRows(list, weights) {
  if (!list) return;
  clear(list);
  if (!weights.rows.length) {
    appendEmpty(list, "后端未提供目标仓位");
    return;
  }
  weights.rows.forEach((row) => {
    const item = document.createElement("li");
    const heading = document.createElement("div");
    const asset = document.createElement("span");
    const value = document.createElement("strong");
    const track = document.createElement("div");
    const fill = document.createElement("span");
    heading.className = "weight-heading";
    asset.textContent = row.asset;
    value.textContent = row.display;
    track.className = "weight-track";
    fill.className = "weight-fill";
    fill.style.setProperty("--weight-value", `${row.barPercent}%`);
    track.appendChild(fill);
    heading.append(asset, value);
    item.append(heading, track);
    list.appendChild(item);
  });
}

function setTone(element, prefix, tone) {
  if (!element) return;
  [...element.classList].filter((name) => name.startsWith(`${prefix}-`)).forEach((name) => element.classList.remove(name));
  element.classList.add(`${prefix}-${tone}`);
}

const UNAVAILABLE_METADATA = Object.freeze({
  contractVersion: "未提供",
  strategyVersion: "未提供",
  asOf: "未提供",
  signalDate: "未提供",
  executionDate: "未提供",
  runId: "未提供",
  codeVersion: "未提供",
  dataVersion: "未提供",
  evidenceRef: "未提供",
});

function clearRenderedCollections() {
  [
    "[data-weight-list]",
    "[data-reason-list]",
    "[data-indicator-list]",
    "[data-quality-issues]",
  ].forEach((selector) => clear(query(selector)));
}

function resetRenderedValues({ mode, status, state, stateKey, quality, qualityKey, missing, explanation }) {
  setText("[data-mode-label]", mode);
  setText("[data-dashboard-status]", status);
  setText("[data-state-label]", state);
  setText("[data-state-key]", stateKey);
  setText("[data-temperature-value]", "未提供");
  setText("[data-trend-value]", "未提供");
  setText("[data-agreement-value]", "未提供");
  setText("[data-confirmation-value]", "未提供");
  setText("[data-confirmation-note]", "当前结果不可用；不根据日期或质量推断确认状态。");
  setText("[data-quality-value]", quality);
  setText("[data-quality-key]", qualityKey);
  setText("[data-weight-total]", "未提供");
  setText("[data-invalid-weight-count]", "未提供");
  setText("[data-missing-fields]", missing);
  setText("[data-explanation-status]", explanation);
  const temperatureFill = query("[data-temperature-fill]");
  if (temperatureFill) temperatureFill.style.setProperty("--temperature-value", "0%");
  const agreementFill = query("[data-agreement-fill]");
  if (agreementFill) agreementFill.style.setProperty("--agreement-value", "0%");
  clearRenderedCollections();
  appendMetadataRows(query("[data-metadata-grid]"), UNAVAILABLE_METADATA);
}

function renderView(view) {
  root.dataset.mode = view.mode;
  root.dataset.stage = view.missing.length ? "partial" : "ready";
  setTone(query("[data-state-panel]"), "state-tone", view.state.tone);
  setTone(query("[data-quality-panel]"), "quality-tone", view.quality.key);
  setText("[data-mode-label]", view.mode);
  setText("[data-state-label]", view.state.label);
  setText("[data-state-key]", view.state.key);
  setText("[data-temperature-value]", view.temperature.display);
  setText("[data-trend-value]", view.trend);
  setText("[data-agreement-value]", view.agreement.display);
  setText("[data-confirmation-value]", view.confirmation.label);
  setText("[data-confirmation-note]", view.confirmation.note);
  setText("[data-quality-value]", view.quality.label);
  setText("[data-quality-key]", view.quality.key);
  setText("[data-weight-total]", view.weights.total === null ? "未提供" : view.weights.total.toFixed(3));
  setText("[data-invalid-weight-count]", view.weights.invalidCount ? `无效项：${view.weights.invalidCount}` : "无效项：0");
  setText("[data-missing-fields]", view.missing.length ? `缺失字段：${view.missing.join(", ")}` : "必需字段已返回");
  setText("[data-explanation-status]", view.explanationAvailable ? "后端证据已返回" : "解释未提供");

  const temperatureFill = query("[data-temperature-fill]");
  if (temperatureFill) temperatureFill.style.setProperty("--temperature-value", `${view.temperature.gaugePercent ?? 0}%`);
  const agreementFill = query("[data-agreement-fill]");
  if (agreementFill) agreementFill.style.setProperty("--agreement-value", `${view.agreement.barPercent ?? 0}%`);

  appendWeightRows(query("[data-weight-list]"), view.weights);
  appendTextList(query("[data-reason-list]"), view.reasonCodes, "后端未提供 reason codes");
  appendIndicatorRows(query("[data-indicator-list]"), view.indicators, view.safeJson);
  appendMetadataRows(query("[data-metadata-grid]"), view.metadata);
  appendTextList(query("[data-quality-issues]"), view.quality.issues, "没有额外质量问题");
  setText("[data-dashboard-status]", `${view.mode} · 状态字段来自后端响应；确认状态不由前端推断。`);
}

function renderLoading() {
  root.dataset.mode = "LIVE";
  root.dataset.stage = "loading";
  setTone(query("[data-state-panel]"), "state-tone", "neutral");
  setTone(query("[data-quality-panel]"), "quality-tone", "neutral");
  resetRenderedValues({
    mode: "LIVE",
    status: "正在读取同源本地 API……",
    state: "读取中",
    stateKey: "loading",
    quality: "读取中",
    qualityKey: "loading",
    missing: "等待 API 返回",
    explanation: "等待 API 返回",
  });
  appendEmpty(query("[data-weight-list]"), "正在读取目标仓位……");
  appendEmpty(query("[data-reason-list]"), "正在读取 reason codes……");
  appendEmpty(query("[data-indicator-list]"), "正在读取指标证据……");
  appendEmpty(query("[data-quality-issues]"), "正在读取数据质量……");
  if (liveButton) liveButton.disabled = true;
}

function renderFailure(error) {
  root.dataset.mode = "LIVE";
  root.dataset.stage = "failed";
  setTone(query("[data-state-panel]"), "state-tone", "failed");
  setTone(query("[data-quality-panel]"), "quality-tone", "failed");
  resetRenderedValues({
    mode: "LIVE",
    status: error instanceof ApiClientError ? error.message : "本地 API 返回不可用结果",
    state: "读取失败",
    stateKey: "failed",
    quality: "失败",
    qualityKey: "failed",
    missing: "最新状态不可用",
    explanation: "解释未提供",
  });
  appendEmpty(query("[data-weight-list]"), "读取失败；不展示旧目标仓位为新结果");
  appendEmpty(query("[data-reason-list]"), "读取失败；不展示旧 reason codes");
  appendEmpty(query("[data-indicator-list]"), "读取失败；不展示旧指标");
  appendEmpty(query("[data-quality-issues]"), "读取失败；质量问题未提供");
  if (liveButton) liveButton.disabled = false;
}

function markFixture(key) {
  fixtureButtons.forEach((button) => {
    const active = button.dataset.fixture === key;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function renderFixture(key) {
  const fixture = DASHBOARD_FIXTURES[key];
  if (!fixture) return;
  markFixture(key);
  if (fixture.kind === "error") {
    renderFailure(new ApiClientError(fixture.message));
    root.dataset.mode = "SIMULATED";
    setText("[data-mode-label]", "SIMULATED");
    setText("[data-dashboard-status]", fixture.message);
    return;
  }
  renderView(normalizeDashboardPayload({ ...fixture, mode: "SIMULATED" }));
}

async function loadLive() {
  markFixture("");
  renderLoading();
  try {
    const client = createApiClient({ baseUrl: root.dataset.apiBase || "/api" });
    const latest = await client.getLatest();
    let explanation = null;
    let quality = null;
    const secondaryErrors = [];
    try {
      explanation = await client.explainSignals({ as_of: latest?.meta?.signal_date });
    } catch (error) {
      secondaryErrors.push(error);
    }
    try {
      quality = await client.getDataQuality();
    } catch (error) {
      secondaryErrors.push(error);
    }
    renderView(normalizeDashboardPayload({ latest, explanation, quality, mode: "LIVE" }));
    if (secondaryErrors.length) {
      setText("[data-dashboard-status]", `最新状态已返回，但 ${secondaryErrors.length} 个辅助接口未完整返回；页面保持部分数据。`);
      root.dataset.stage = "partial";
    }
  } catch (error) {
    renderFailure(error);
  } finally {
    if (liveButton) liveButton.disabled = false;
  }
}

fixtureButtons.forEach((button) => button.addEventListener("click", () => renderFixture(button.dataset.fixture)));
liveButton?.addEventListener("click", loadLive);

renderFixture("normal");

export { loadLive, normalizeDashboardPayload, renderFixture, renderView };
