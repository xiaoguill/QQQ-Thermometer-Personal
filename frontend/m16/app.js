(function () {
  "use strict";

  const config = Object.assign({
    eventEndpoint: "/api/live/events",
    confirmedEndpoint: "/api/thermometer/latest",
    refreshIntervalSeconds: 900
  }, window.__QQQ_LIVE_CONFIG__ || {});
  const state = { observations: {}, events: [], source: "massive", lastEventId: null, stream: null, reconnectTimer: null };
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
    const title = eventType === "quality.changed" ? "数据质量需要关注" : eventType === "service.status" ? "服务状态变化" : eventType === "cursor.reset" ? "重连游标已重置" : "行情观察批次";
    const payload = envelope.payload || envelope;
    const detail = eventType === "observation.batch" ? ((payload.observations || []).filter((item) => !item.is_duplicate).map((item) => item.symbol + "=" + (item.last == null ? "--" : Number(item.last).toFixed(2))).join(" · ") || "重复批次") : eventType === "quality.changed" ? ((payload.symbols || []).map((item) => item.symbol + ":" + item.quality).join(" · ") || "质量状态变化") : payload.status || payload.reset_to || "只读事件";
    state.events.unshift({ title, detail, occurredAt: envelope.occurred_at || new Date().toISOString(), alert: eventType === "quality.changed" || eventType === "cursor.reset" });
    state.events = state.events.slice(0, 12);
    $("#eventList").innerHTML = state.events.map((item) => '<div class="event-row"><span class="event-mark ' + (item.alert ? "is-alert" : "") + '"></span><div><strong>' + escapeText(item.title) + '</strong><p>' + escapeText(item.detail) + '</p></div><time>' + formatTime(item.occurredAt, false) + '</time></div>').join("");
  }
  function handleEvent(eventType, message) {
    try {
      const envelope = JSON.parse(message.data);
      if (message.lastEventId) { state.lastEventId = message.lastEventId; try { sessionStorage.setItem("qqq-live-last-event-id", state.lastEventId); } catch (_) {} }
      if (eventType === "observation.batch") renderObservationBatch(envelope.payload || {});
      if (eventType === "cursor.reset") { state.events = []; $("#eventList").innerHTML = '<div class="empty-row">重连游标已重置，等待新事件。</div>'; }
      renderEvent(eventType, envelope);
    } catch (_) { showToast("收到无法解析的实时事件，已保持当前读数"); }
  }
  function connect() {
    if (!window.EventSource || !/^https?:$/.test(window.location.protocol)) { setConnection("error", "需要本地 HTTP"); showToast("请通过本地 HTTP 服务打开页面，不能使用 file:// 实时连接"); return; }
    if (state.stream) state.stream.close();
    setConnection("warn", "正在连接 SSE");
    try { state.stream = new EventSource(config.eventEndpoint); } catch (_) { setConnection("error", "SSE 不可用"); return; }
    state.stream.onopen = () => setConnection("live", "SSE 已连接");
    state.stream.onerror = () => { setConnection("warn", "等待重连"); };
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
  $("#clearEvents").addEventListener("click", () => { state.events = []; $("#eventList").innerHTML = '<div class="empty-row">事件视图已清空；不会删除服务端事件。</div>'; });
  window.setInterval(renderClock, 1000); renderClock(); loadConfirmed(); connect();
}());
