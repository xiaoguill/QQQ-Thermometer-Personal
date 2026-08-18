(function () {
  "use strict";

  const config = Object.assign({
    eventEndpoint: "/api/live/events",
    confirmedEndpoint: "/api/thermometer/latest",
    refreshIntervalSeconds: 900
  }, window.__QQQ_LIVE_CONFIG__ || {});
  const state = { observations: {}, events: [], eventIds: new Set(), source: "massive", lastEventId: null, stream: null, reconnectTimer: null, notifiedEventIds: new Set() };
  const $ = (selector) => document.querySelector(selector);

  function escapeText(value) { return value == null ? "--" : String(value); }
  function formatTime(value, withSeconds) {
    if (!value) return "--";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "--";
    return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: withSeconds ? "2-digit" : undefined, hour12: false }).format(date);
  }
  function showToast(message) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.hidden = false;
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => { toast.hidden = true; }, 3000);
  }
  function restoreSessionState() {
    try {
      state.lastEventId = sessionStorage.getItem("qqq-live-last-event-id") || null;
      const saved = JSON.parse(sessionStorage.getItem("qqq-live-notified-event-ids") || "[]");
      if (Array.isArray(saved)) state.notifiedEventIds = new Set(saved.filter((item) => typeof item === "string").slice(-512));
    } catch (_) {}
  }
  function persistNotificationIds() {
    try { sessionStorage.setItem("qqq-live-notified-event-ids", JSON.stringify(Array.from(state.notifiedEventIds).slice(-512))); } catch (_) {}
  }
  function notificationLabel() {
    if (!("Notification" in window)) return "浏览器不支持";
    if (Notification.permission === "granted") return "桌面提醒已启用";
    if (Notification.permission === "denied") return "桌面提醒已拒绝";
    return "启用桌面提醒";
  }
  function renderNotificationState() {
    const button = $("#notificationButton");
    if (!button) return;
    button.textContent = notificationLabel();
    button.disabled = !("Notification" in window) || Notification.permission === "denied";
  }
  async function enableNotifications() {
    if (!("Notification" in window)) { showToast("当前浏览器不支持桌面提醒"); return; }
    try {
      await Notification.requestPermission();
      renderNotificationState();
      showToast(Notification.permission === "granted" ? "桌面提醒已启用" : "未启用桌面提醒");
    } catch (_) { showToast("桌面提醒权限请求失败"); }
  }
  function notifyLocal(eventType, envelope) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    if (!["quality.changed", "service.status", "state.candidate"].includes(eventType)) return;
    if (envelope.notification !== true) return;
    const eventId = envelope.event_id || (eventType + ":" + (envelope.occurred_at || ""));
    if (state.notifiedEventIds.has(eventId)) return;
    state.notifiedEventIds.add(eventId);
    if (state.notifiedEventIds.size > 512) state.notifiedEventIds.delete(state.notifiedEventIds.values().next().value);
    persistNotificationIds();
    const payload = envelope.payload || {};
    const title = eventType === "quality.changed" ? "QQQ 温度计：数据质量需要关注" : eventType === "service.status" ? "QQQ 温度计：服务状态变化" : "QQQ 温度计：确认态候选变化";
    const detail = eventType === "quality.changed" ? ((payload.symbols || []).map((item) => item.symbol + ":" + item.quality + (item.error_code ? " (" + item.error_code + ")" : "")).join(" · ") || "质量状态变化") : eventType === "state.candidate" ? ((payload.state || "状态") + " · " + (payload.signal_date || "")) : (payload.detail || payload.status || "只读状态变化");
    try { new Notification(title, { body: detail, tag: "qqq-m16-" + eventId }); } catch (_) { showToast(detail); }
  }
  function setConnection(kind, label) {
    const chip = $("#connectionChip");
    chip.classList.remove("is-live", "is-warn", "is-error", "is-pending");
    chip.classList.add(kind === "live" ? "is-live" : kind === "error" ? "is-error" : "is-warn");
    $("#connectionText").textContent = label;
    [$("#railStatusDot"), $("#heroStatusDot"), chip.querySelector(".live-dot")].forEach((dot) => {
      dot.classList.remove("is-live", "is-warn", "is-error");
      dot.classList.add(kind === "live" ? "is-live" : kind === "error" ? "is-error" : "is-warn");
    });
  }
  function renderClock() { $("#clockText").textContent = "UTC+8 " + new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date()); }
  function qualityClass(quality) { return quality === "OK" ? "is-ok" : ["PARTIAL", "STALE", "NEEDS_REVIEW"].includes(quality) ? "is-warn" : "is-error"; }
  function displayQuality(quality) { return quality === "OK" ? "OK" : quality || "UNKNOWN"; }
  function renderMetrics() {
    Object.keys(state.observations).forEach((symbol) => {
      const item = state.observations[symbol];
      const value = item.last == null ? "--" : Number(item.last).toLocaleString("en-US", { maximumFractionDigits: 2 });
      const valueNode = document.querySelector('[data-last="' + CSS.escape(symbol) + '"]');
      const sourceNode = document.querySelector('[data-source="' + CSS.escape(symbol) + '"]');
      const dot = document.querySelector('[data-quality-dot="' + CSS.escape(symbol) + '"]');
      if (valueNode) valueNode.textContent = value;
      if (sourceNode) sourceNode.textContent = displayQuality(item.quality) + " · 源 " + formatTime(item.source_timestamp, false);
      if (dot) { dot.classList.remove("is-ok", "is-warn", "is-error"); dot.classList.add(qualityClass(item.quality)); }
    });
  }
  function resetObservations() {
    state.observations = {};
    document.querySelectorAll("[data-last]").forEach((node) => { node.textContent = "--"; });
    document.querySelectorAll("[data-source]").forEach((node) => { node.textContent = "等待源时间"; });
    document.querySelectorAll("[data-quality-dot]").forEach((node) => { node.classList.remove("is-ok", "is-warn", "is-error"); });
    $("#observationState").textContent = "等待新的观察快照";
    $("#observationDescription").textContent = "当前观察游标已重置或链路已断开；旧读数不再视为 OK。";
    renderQuality();
  }
  function markObservationsStale(errorCode) {
    Object.values(state.observations).forEach((item) => {
      if (item.quality === "OK") { item.quality = "STALE"; item.error_code = errorCode; }
    });
    renderMetrics();
    renderQuality();
  }
  function renderQuality() {
    const items = Object.values(state.observations);
    const ok = items.filter((item) => item.quality === "OK").length;
    $("#qualitySummary").textContent = ok + " / " + items.length + " OK";
    $("#healthPercent").textContent = items.length ? Math.round(ok / items.length * 100) + "%" : "--";
    $("#healthTitle").textContent = !items.length ? "等待数据" : ok === items.length ? "观察链路健康" : "需要复核";
    $("#healthRing").style.borderTopColor = !items.length ? "var(--faint)" : ok === items.length ? "var(--green)" : "var(--amber)";
    const list = $("#qualityList");
    if (!items.length) { list.innerHTML = '<div class="empty-row">尚未收到行情观察。</div>'; return; }
    list.innerHTML = items.sort((a, b) => a.symbol.localeCompare(b.symbol)).map((item) => '<div class="quality-row"><strong>' + escapeText(item.symbol) + '</strong><div><span class="quality-status ' + qualityClass(item.quality) + '">' + displayQuality(item.quality) + '</span><small>' + escapeText(item.error_code || item.price_basis || "unadjusted_ohlcv") + '</small></div><span class="quality-time">' + formatTime(item.source_timestamp, false) + '</span></div>').join("");
  }
  function renderObservationBatch(batch) {
    (batch.observations || []).forEach((item) => { state.observations[item.symbol] = item; });
    $("#lastBatchText").textContent = "最近批次 " + formatTime(batch.fetched_at, true) + " · " + (batch.observations || []).length + " 个标的";
    $("#observationState").textContent = "盘中观察已到达";
    $("#observationDescription").textContent = "当前数据仍为 provisional；页面只展示源数据与质量，不把盘中快照转成确认仓位。";
    renderMetrics(); renderQuality();
  }
  function renderEvent(eventType, envelope) {
    if (envelope.event_id && state.eventIds.has(envelope.event_id)) return;
    if (envelope.event_id) state.eventIds.add(envelope.event_id);
    const title = eventType === "quality.changed" ? "数据质量需要关注" : eventType === "service.status" ? "服务状态变化" : eventType === "state.candidate" ? "确认态候选变化" : eventType === "cursor.reset" ? "重连游标已重置" : "行情观察批次";
    const payload = envelope.payload || envelope;
    const detail = eventType === "observation.batch" ? ((payload.observations || []).filter((item) => !item.is_duplicate).map((item) => item.symbol + "=" + (item.last == null ? "--" : Number(item.last).toFixed(2))).join(" · ") || "重复批次") : eventType === "quality.changed" ? ((payload.symbols || []).map((item) => item.symbol + ":" + item.quality + (item.error_code ? " (" + item.error_code + ")" : "")).join(" · ") || "质量状态变化") : eventType === "state.candidate" ? ((payload.state || "状态") + " · " + (payload.signal_date || "")) : payload.status || payload.reset_to || "只读事件";
    state.events.unshift({ title, detail, occurredAt: envelope.occurred_at || new Date().toISOString(), alert: eventType === "quality.changed" || eventType === "cursor.reset" });
    state.events = state.events.slice(0, 12);
    $("#eventList").innerHTML = state.events.map((item) => '<div class="event-row"><span class="event-mark ' + (item.alert ? "is-alert" : "") + '"></span><div><strong>' + escapeText(item.title) + '</strong><p>' + escapeText(item.detail) + '</p></div><time>' + formatTime(item.occurredAt, false) + '</time></div>').join("");
  }
  function handleEvent(eventType, message) {
    try {
      const envelope = JSON.parse(message.data);
      if (message.lastEventId) { state.lastEventId = message.lastEventId; try { sessionStorage.setItem("qqq-live-last-event-id", state.lastEventId); } catch (_) {} }
      if (eventType === "observation.batch") { renderObservationBatch(envelope.payload || {}); loadConfirmed(); }
      if (eventType === "service.status" && ["failed", "degraded"].includes((envelope.payload || {}).status)) markObservationsStale("SSE_SERVICE_" + String((envelope.payload || {}).status).toUpperCase());
      if (eventType === "cursor.reset") { state.events = []; state.eventIds.clear(); $("#eventList").innerHTML = '<div class="empty-row">重连游标已重置，等待新事件。</div>'; resetObservations(); }
      renderEvent(eventType, envelope);
      notifyLocal(eventType, envelope);
    } catch (_) { showToast("收到无法解析的实时事件，已保持当前读数"); }
  }
  function connect() {
    if (!window.EventSource || !/^https?:$/.test(window.location.protocol)) { setConnection("error", "需要本地 HTTP"); showToast("请通过本地 HTTP 服务打开页面，不能使用 file:// 实时连接"); return; }
    if (state.stream) state.stream.close();
    setConnection("warn", "正在连接 SSE");
    let endpoint = config.eventEndpoint;
    try { const url = new URL(config.eventEndpoint, window.location.href); if (state.lastEventId) url.searchParams.set("after", state.lastEventId); endpoint = url.toString(); } catch (_) {}
    try { state.stream = new EventSource(endpoint); } catch (_) { setConnection("error", "SSE 不可用"); return; }
    state.stream.onopen = () => setConnection("live", "SSE 已连接");
    state.stream.onerror = () => { setConnection("warn", "等待重连"); markObservationsStale("SSE_DISCONNECTED"); };
    ["observation.batch", "quality.changed", "service.status", "state.candidate", "cursor.reset"].forEach((type) => state.stream.addEventListener(type, (message) => handleEvent(type, message)));
  }
  async function loadConfirmed() {
    try {
      const response = await fetch(config.confirmedEndpoint, { headers: { Accept: "application/json" }, cache: "no-store" });
      if (!response.ok) throw new Error("confirmed API unavailable");
      const body = await response.json(); const meta = body.meta || {}; const data = body.data || {};
      $("#confirmedState").textContent = data.state || "等待收盘确认";
      $("#confirmedReason").textContent = (data.reason_codes || ["没有确认理由"]).join(" · ");
      $("#confirmedQuality").textContent = (meta.data_quality || "needs_review").toUpperCase();
      $("#confirmedQuality").classList.toggle("is-ok", meta.data_quality === "ok");
      $("#strategyVersion").textContent = meta.strategy_version || "--"; $("#signalDate").textContent = meta.signal_date || "--"; $("#confirmedDataQuality").textContent = meta.data_quality || "--"; $("#runId").textContent = meta.run_id || "--";
      const stateClass = String(data.state || "").toLowerCase(); const orb = $("#confirmedOrb"); orb.classList.remove("is-green", "is-amber", "is-red"); orb.classList.add(stateClass.includes("shock") || stateClass === "red" ? "is-red" : stateClass.includes("recover") || stateClass === "yellow" ? "is-amber" : stateClass === "normal" || stateClass === "green" ? "is-green" : "");
    } catch (_) { $("#confirmedQuality").textContent = "不可用"; $("#confirmedReason").textContent = "既有确认 API 当前不可用；盘中观察仍不会替代确认逻辑。"; }
  }
  $("#refreshSeconds").textContent = String(config.refreshIntervalSeconds);
  $("#reconnectButton").addEventListener("click", () => { connect(); loadConfirmed(); showToast("已请求重新连接本地观察链路"); });
  $("#notificationButton").addEventListener("click", enableNotifications);
  $("#clearEvents").addEventListener("click", () => { state.events = []; $("#eventList").innerHTML = '<div class="empty-row">事件视图已清空；不会删除服务端事件。</div>'; });
  window.setInterval(renderClock, 1000); renderClock(); restoreSessionState(); renderNotificationState(); loadConfirmed(); connect();
}());
