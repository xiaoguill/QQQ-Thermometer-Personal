(() => {
  "use strict";

  const state = { eventSource: null, overview: null, plan: null, toastTimer: null };
  const $ = (id) => document.getElementById(id);
  const text = (id, value) => { const node = $(id); if (node) node.textContent = value == null || value === "" ? "--" : String(value); };
  const clamp = (value, low, high) => Math.max(low, Math.min(high, Number(value)));
  const percent = (value) => Number.isFinite(Number(value)) ? `${(Number(value) * 100).toFixed(1)}%` : "--";
  const money = (value) => Number.isFinite(Number(value)) ? Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 }) : "--";
  const displayTime = (value) => value ? String(value).replace("T", " ").replace(/:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2})?$/, "") : "--";
  const stateLabel = { warming: "升温", normal: "正常", shock: "冲击", recovery: "恢复", green: "绿色", yellow: "黄色", red: "红色", fast_guard: "快速防守", needs_review: "需要复核" };
  const actionLabel = { increase: "增加", decrease: "减少", hold: "维持" };

  function showToast(message) {
    const node = $("toast");
    node.textContent = message;
    node.hidden = false;
    window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(() => { node.hidden = true; }, 3600);
  }

  function setConnection(kind, label) {
    const chip = $("connectionChip");
    const dot = $("connectionDot");
    chip.className = `connection-chip ${kind || ""}`;
    dot.className = `status-dot ${kind || ""}`;
    text("connectionText", label);
  }

  function renderTemperature(overview) {
    const observation = overview.latest_observation;
    const confirmed = overview.confirmed_strategy || {};
    const runtime = overview.runtime || {};
    const source = overview.source_status?.massive || {};
    const temperature = confirmed.temperature == null ? Number.NaN : Number(confirmed.temperature);
    const safeTemperature = Number.isFinite(temperature) ? clamp(temperature, 0, 100) : null;
    const dial = $("temperatureDial");
    dial.style.setProperty("--temperature", safeTemperature == null ? "0%" : `${safeTemperature}%`);
    text("temperatureValue", safeTemperature == null ? "--" : safeTemperature.toFixed(0));
    const liveState = source.status === "MASSIVE_API_KEY_UNAVAILABLE" ? "等待 API Key" : observation ? "收到盘中观察" : "等待数据";
    text("liveState", liveState);
    const quality = observation?.quality || runtime.status || "未连接";
    text("liveQuality", `质量 ${quality}`);
    text("liveAsOf", `更新时间 ${displayTime(observation?.fetched_at)}`);
    text("liveSummary", observation ? "盘中数据只用于观察和数据质量提示，不会覆盖已确认目标。" : "尚未收到 Massive 盘中观察。缺失数据时不会推断行情状态。");
    const marker = $("temperatureMarker");
    marker.style.left = safeTemperature == null ? "0%" : `${safeTemperature}%`;
    const stateName = String(confirmed.state || "needs_review");
    dial.className = `temperature-dial ${stateName}`;
    $("temperatureDial").setAttribute("aria-label", `实时观察 ${safeTemperature == null ? "不可用" : safeTemperature.toFixed(0)}`);
  }

  function renderConfirmed(overview) {
    const strategy = overview.confirmed_strategy || {};
    const meta = overview.confirmed_meta || {};
    const confirmed = strategy.confirmed === true;
    const stateName = String(strategy.state || "needs_review");
    const stateText = confirmed ? (stateLabel[stateName] || stateName) : "需要复核";
    const reasons = Array.isArray(strategy.reason_codes) ? strategy.reason_codes.slice(0, 3).join(" · ") : "确认结果不可用";
    text("confirmedState", stateText);
    text("confirmedReason", confirmed ? `确认状态 · ${reasons || "无额外原因码"}` : reasons);
    text("strategyVersion", confirmed ? (meta.strategy_version || "unavailable") : "--");
    text("signalDate", confirmed ? (meta.signal_date || "--") : "--");
    text("strategyTrend", confirmed ? (strategy.trend || "--") : "--");
    text("signalAgreement", confirmed && strategy.signal_agreement != null ? percent(strategy.signal_agreement) : "--");
    const tag = $("confirmedTag");
    tag.className = `tag confirmed ${confirmed ? "ok" : ""}`;
    tag.textContent = confirmed ? "已确认" : "尚未确认";
    const orb = $("stateOrb");
    orb.className = `state-orb ${confirmed ? stateName : "needs_review"}`;
  }

  function makeCell(value, className) {
    const cell = document.createElement("td");
    if (className) cell.className = className;
    cell.textContent = value == null || value === "" ? "--" : String(value);
    return cell;
  }

  function renderTargets(overview) {
    const body = $("targetRows");
    body.replaceChildren();
    const strategy = overview.confirmed_strategy || {};
    const weights = strategy.target_weights && typeof strategy.target_weights === "object" ? strategy.target_weights : {};
    const entries = Object.entries(weights);
    if (!strategy.confirmed || entries.length === 0) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 3;
      cell.className = "empty-row";
      cell.textContent = "暂无已确认目标权重";
      row.appendChild(cell); body.appendChild(row);
      text("targetNote", "确认 API 不可用或目标权重为空；不会使用盘中数据替代。");
      return;
    }
    entries.forEach(([symbol, weight]) => {
      const row = document.createElement("tr");
      row.appendChild(makeCell(symbol, "symbol-cell"));
      row.appendChild(makeCell(percent(weight), "weight-cell"));
      row.appendChild(makeCell("confirmed API", "muted-cell"));
      body.appendChild(row);
    });
    text("targetNote", `共 ${entries.length} 个目标标的 · 策略版本 ${overview.confirmed_meta?.strategy_version || "unavailable"}`);
  }

  function renderPlan(plan) {
    state.plan = plan;
    const status = String(plan?.status || "UNAVAILABLE");
    const statusNode = $("planStatus");
    statusNode.className = `plan-status ${status === "READY" ? "ready" : "warn"}`;
    statusNode.replaceChildren();
    const dot = document.createElement("span"); dot.className = "status-dot";
    const strong = document.createElement("strong"); strong.textContent = status === "READY" ? "预览已生成" : status;
    statusNode.append(dot, strong);
    text("planReason", plan?.reason || "尚未生成纸上计划。");
    const body = $("planRows"); body.replaceChildren();
    const actions = Array.isArray(plan?.actions) ? plan.actions : [];
    if (actions.length === 0) {
      const row = document.createElement("tr"); const cell = document.createElement("td"); cell.colSpan = 3; cell.className = "empty-row"; cell.textContent = "暂无可展示的计划差异"; row.appendChild(cell); body.appendChild(row);
    } else {
      actions.slice().sort((a, b) => Math.abs(Number(b.delta_value || 0)) - Math.abs(Number(a.delta_value || 0))).forEach((action) => {
        const row = document.createElement("tr");
        row.appendChild(makeCell(action.symbol, "symbol-cell"));
        const direction = actionLabel[action.action] || action.action || "--";
        row.appendChild(makeCell(direction, `action-cell ${action.action || ""}`));
        row.appendChild(makeCell(`${money(action.delta_value)} USD`, "delta-cell"));
        body.appendChild(row);
      });
    }
    const warning = Array.isArray(plan?.warnings) && plan.warnings.length ? plan.warnings[plan.warnings.length - 1] : "纸上预览不会写入券商账户。";
    text("planNote", `${warning}${plan?.portfolio?.estimated_nav ? ` · 估算 NAV ${money(plan.portfolio.estimated_nav)} USD` : ""}`);
  }

  function renderObservations(overview) {
    const root = $("observationRows"); root.replaceChildren();
    const batch = overview.latest_observation;
    text("batchTime", batch ? displayTime(batch.fetched_at) : "--");
    const observations = Array.isArray(batch?.observations) ? batch.observations : [];
    if (!observations.length) {
      const empty = document.createElement("p"); empty.className = "empty-row"; empty.textContent = "等待新的 Massive 观察批次。"; root.appendChild(empty); return;
    }
    observations.forEach((item) => {
      const row = document.createElement("div"); row.className = "observation-row";
      const name = document.createElement("strong"); name.textContent = item.symbol || "--";
      const detail = document.createElement("span"); detail.textContent = `${item.last == null ? "--" : money(item.last)} · ${item.quality || "--"}`;
      const time = document.createElement("small"); time.textContent = item.source_timestamp ? displayTime(item.source_timestamp) : "无源时间";
      row.append(name, detail, time); root.appendChild(row);
    });
  }

  function renderHealth(overview) {
    const massive = overview.source_status?.massive || {};
    const confirmed = overview.source_status?.confirmed_api || {};
    const paper = overview.source_status?.paper_input || {};
    text("massiveKey", massive.api_key_configured ? "已配置" : "未配置");
    text("massiveStatus", massive.status || "--");
    text("refreshInterval", massive.refresh_interval_seconds ? `${Math.round(Number(massive.refresh_interval_seconds) / 60)} min` : "--");
    text("confirmedApi", confirmed.available ? "可用" : "不可用");
    text("confirmedQuality", confirmed.data_quality || "failed");
    text("paperInput", paper.status || "--");
    text("paperInputDetail", paper.file_configured ? `${paper.position_count || 0} 个持仓` : "需要本地输入");
    text("failureCount", overview.runtime?.consecutive_failures == null ? "--" : overview.runtime.consecutive_failures);
    const root = $("healthRows"); root.replaceChildren();
    const rows = [
      ["Massive", massive.status || "--", massive.last_batch_quality || "等待批次"],
      ["确认 API", confirmed.available ? "可用" : "不可用", confirmed.strategy_version || "unavailable"],
      ["纸上输入", paper.status || "--", "不读取券商"],
      ["时间", overview.display_timezone || "Asia/Shanghai", "UTC+8 展示"],
    ];
    rows.forEach(([label, value, detail]) => {
      const row = document.createElement("div"); row.className = "health-row";
      const left = document.createElement("span"); left.textContent = label;
      const middle = document.createElement("strong"); middle.textContent = value;
      const right = document.createElement("small"); right.textContent = detail;
      row.append(left, middle, right); root.appendChild(row);
    });
  }

  function render(overview) {
    state.overview = overview;
    renderTemperature(overview); renderConfirmed(overview); renderTargets(overview); renderObservations(overview); renderHealth(overview);
    const status = overview.source_status?.massive?.status || "";
    if (status === "ready") setConnection("live", "实时链路正常");
    else if (status === "MASSIVE_API_KEY_UNAVAILABLE") setConnection("warn", "等待 API Key");
    else if (status === "failed" || status === "degraded") setConnection("warn", "数据需复核");
    else setConnection("", "连接中");
  }

  async function loadOverview({ quiet = false } = {}) {
    try {
      const response = await fetch("/api/m17/overview", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
      if (!quiet) showToast("观察已刷新；确认策略仍由既有确认 API 提供。");
    } catch (error) {
      setConnection("error", "统一入口不可用");
      showToast("无法读取统一入口，请检查 M17 本地服务。");
    }
  }

  async function loadPlan() {
    try {
      const response = await fetch("/api/m17/paper-plan", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      renderPlan(await response.json());
      showToast("纸上计划已更新；没有创建订单。");
    } catch (error) {
      renderPlan({ status: "UNAVAILABLE", reason: "纸上计划接口不可用", actions: [], warnings: ["不会创建订单。"] });
      showToast("纸上计划接口不可用。");
    }
  }

  function connectEvents() {
    if (state.eventSource) state.eventSource.close();
    try {
      const events = new EventSource("/api/live/events");
      state.eventSource = events;
      events.onopen = () => setConnection("live", "实时链路正常");
      events.onerror = () => setConnection("warn", "等待重连");
      ["observation.batch", "quality.changed", "service.status", "state.candidate"].forEach((type) => events.addEventListener(type, () => loadOverview({ quiet: true })));
    } catch (error) {
      setConnection("error", "SSE 不可用");
    }
  }

  function renderClock() {
    text("clock", new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date()) + " UTC+8");
  }

  $("refreshButton").addEventListener("click", () => loadOverview());
  $("planButton").addEventListener("click", loadPlan);
  window.setInterval(renderClock, 1000); renderClock();
  window.setInterval(() => loadOverview({ quiet: true }), 60 * 1000);
  loadOverview({ quiet: true }); connectEvents();
})();
