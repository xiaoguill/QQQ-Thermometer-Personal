(() => {
  const $ = (id) => document.getElementById(id);
  const stateText = { normal: "正常", warming: "升温", shock: "冲击", recovery: "恢复", green: "绿色", yellow: "黄色", red: "红色", needs_review: "需要复核", unavailable: "不可用" };
  const qualityText = { OK: "正常", PARTIAL: "部分可用", STALE: "过期", FAILED: "失败", NEEDS_REVIEW: "待复核", NOT_RUN: "未运行", UNAVAILABLE: "不可用" };
  const statusText = { READY: "就绪", DEGRADED: "降级", PARTIAL: "部分完成", FAILED: "失败", NEEDS_REVIEW: "待复核", NOT_RUN: "未运行", UNAVAILABLE: "不可用", NOT_GENERATED: "未生成", CONFIRMED: "已确认" };
  const escape = (value) => String(value == null ? "—" : value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
  const text = (value, fallback = "—") => value == null || value === "" ? fallback : escape(value);
  const list = (value) => Array.isArray(value) ? value : [];
  const fmtDate = (value) => value ? escape(value.replace("T", " ").replace("Z", "")) : "—";
  const fmtNumber = (value, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "—";
  const human = (value, map) => map[String(value)] || String(value == null ? "—" : value);
  const className = (value) => String(value || "unknown").toLowerCase().replace(/[^a-z0-9_-]/g, "_");

  function setStatus(node, value, map = statusText) {
    node.textContent = human(value, map);
    node.className = `status-chip ${className(value)}`;
  }

  function renderProvisional(data) {
    const item = data.provisional_observation || {};
    const temperature = item.temperature == null ? Number.NaN : Number(item.temperature);
    const available = Number.isFinite(temperature);
    $("provisionalTemperature").textContent = available ? Math.round(temperature) : "—";
    $("provisionalLabel").textContent = available ? "临时观察值" : "温度未发布";
    $("provisionalDial").style.setProperty("--value", available ? `${Math.max(0, Math.min(100, temperature))}%` : "0%");
    $("provisionalState").textContent = available ? human(item.state, stateText) : (item.status === "READY" ? "观察已更新" : human(item.status, statusText));
    $("provisionalReason").textContent = list(item.reason_codes).join(" · ") || "盘中数据仅用于观察。";
    $("provisionalTime").textContent = fmtDate(item.as_of);
    $("provisionalVersion").textContent = text(item.source_version);
    $("provisionalSymbols").textContent = list(item.source_symbols).join(" · ") || "—";
    const marker = $("provisionalMarker");
    marker.style.left = available ? `${Math.max(0, Math.min(100, temperature))}%` : "0%";
    const observations = item.observations || {};
    const rows = Object.entries(observations).map(([symbol, value]) => `<span class="observation-item"><strong>${escape(symbol)}</strong> ${fmtNumber(value.last != null ? value.last : value.close)}</span>`);
    $("observationStrip").innerHTML = rows.length ? rows.join("") : `<span class="empty">暂无盘中观察值</span>`;
  }

  function renderConfirmed(data) {
    const item = data.confirmed_strategy || {};
    const ready = item.status === "READY" && item.quality === "OK";
    setStatus($("confirmedChip"), ready ? "CONFIRMED" : item.status, { ...statusText, CONFIRMED: "已确认" });
    $("confirmedState").textContent = human(item.state, stateText);
    $("confirmedReason").textContent = list(item.reason_codes).join(" · ") || (ready ? "正式目标由确认链路提供。" : "正式策略尚未可用。 ");
    $("confirmedTemperature").textContent = item.temperature == null ? "—" : fmtNumber(item.temperature, 0);
    $("signalDate").textContent = text(item.signal_date);
    $("executionDate").textContent = text(item.execution_date);
    $("strategyVersion").textContent = text(item.strategy_version);
    $("dataVersion").textContent = text(data.data_version);
    const orb = $("confirmedOrb");
    orb.className = `state-orb ${className(item.state)}`;
  }

  function renderSummary(data) {
    const runtime = data.runtime_boundary || {};
    const paper = data.paper_plan || {};
    const weights = data.target_weights || {};
    $("overallQuality").textContent = human(data.overall_quality, qualityText);
    $("qualityCaption").textContent = `${text(data.as_of)} · ${text(data.run_id)}`;
    $("runtimeStatus").textContent = human(runtime.source_status, statusText);
    $("runtimeCaption").textContent = `${runtime.source || "Massive"} · ${runtime.refresh_interval_seconds || "—"} 秒 · ${runtime.display_timezone || "—"}`;
    $("paperStatus").textContent = human(paper.status, statusText);
    $("paperCaption").textContent = list(paper.reason_codes).join(" · ") || "paper only / order_created=false";
    $("targetCount").textContent = Object.keys(weights).length ? `${Object.keys(weights).length} 个标的` : "—";
    $("targetCaption").textContent = data.confirmed_strategy && data.confirmed_strategy.status === "READY" ? "CONFIRMED / 只读" : "正式目标不可用";
  }

  function renderPaper(data) {
    const paper = data.paper_plan || {};
    setStatus($("paperChip"), paper.status, statusText);
    const reasons = list(paper.reason_codes).map((item) => `<div class="paper-row"><div><strong>${escape(item)}</strong><span>服务器端状态说明</span></div><b class="paper-flag">${paper.order_created === false ? "NO ORDER" : "REVIEW"}</b></div>`);
    $("paperBody").innerHTML = reasons.length ? reasons.join("") : `<div class="empty">没有纸上计划。确认目标或执行价格尚未满足生成条件。</div>`;
  }

  function renderWeights(data) {
    const weights = data.target_weights || {};
    const entries = Object.entries(weights);
    $("targetBody").innerHTML = entries.length ? entries.map(([symbol, weight]) => `<div class="weight-row"><span>${escape(symbol)}</span><i><b style="width:${Math.max(0, Math.min(100, Number(weight) * 100))}%"></b></i><strong>${fmtNumber(Number(weight) * 100, 1)}%</strong></div>`).join("") : `<div class="empty">正式确认目标为空；页面不会从温度或指标自行推导仓位。</div>`;
  }

  function renderQuality(data) {
    const modules = list(data.modules);
    const focus = modules.filter((item) => ["M02", "M03", "M04", "M05", "M06", "M07", "M08", "M09", "M10", "M11"].includes(item.module_id));
    $("qualityAsOf").textContent = text(data.as_of);
    const events = list(data.latest_data_quality);
    const rows = focus.map((item) => `<div class="evidence-item"><span>${escape(item.module_id)} · ${escape(item.name)}</span><b class="${item.quality === "OK" ? "" : "warn"}">${escape(human(item.quality, qualityText))}</b></div>`);
    if (events.length) rows.push(...events.slice(0, 5).map((item) => `<div class="evidence-item"><span>${escape(item.event_type || item.reason || "quality event")}</span><b class="warn">${escape(item.status || item.severity || "需要关注")}</b></div>`));
    $("qualityBody").innerHTML = rows.length ? rows.join("") : `<div class="empty">暂无质量记录。</div>`;
    const indicator = data.latest_indicator || {};
    $("indicatorDate").textContent = text(indicator.signal_date);
    const values = indicator.values || {};
    const names = Object.keys(values);
    $("indicatorBody").innerHTML = names.length ? names.map((name) => `<div class="indicator-item"><span>${escape(name)}</span><strong>${fmtNumber(values[name], 4)}</strong></div>`).join("") : `<div class="empty">暂无指标快照。</div>`;
  }

  function renderExplanation(data) {
    const explanation = data.explanation || {};
    $("explanationQuality").textContent = human(explanation.data_quality, qualityText);
    if (!Object.keys(explanation).length) {
      $("explanationBody").innerHTML = `<div class="empty">暂无 M06 解释；请先完成数据与指标确认。</div>`;
      return;
    }
    const summary = `<div class="explanation-summary"><div><span>趋势</span><strong>${escape(human(explanation.trend, stateText))}</strong></div><div><span>信号一致性</span><strong>${explanation.signal_agreement == null ? "—" : fmtNumber(Number(explanation.signal_agreement) * 100, 1) + "%"}</strong></div><div><span>置信状态</span><strong>${escape(explanation.confidence_label || "—")}</strong></div><div><span>发布</span><strong>${escape(explanation.publication_status || "—")}</strong></div></div>`;
    const evidence = list(explanation.evidence).slice(0, 8).map((item) => `<div class="reason-line"><strong>${escape(item.message || item.code || "evidence")}</strong></div>`);
    $("explanationBody").innerHTML = summary + `<div class="reason-list">${evidence.join("") || `<div class="empty">暂无证据条目。</div>`}</div>`;
  }

  function renderModules(data) {
    const modules = list(data.modules);
    $("moduleCount").textContent = `${modules.length} 个模块 · ${text(data.run_id)}`;
    $("moduleBody").innerHTML = modules.map((item) => `<tr><td class="module-id">${escape(item.module_id)}</td><td>${escape(item.name)}<br><small>${escape(item.responsibility)}</small></td><td><span class="module-status ${className(item.status)}">${escape(human(item.status, statusText))}</span></td><td>${escape(human(item.quality, qualityText))}</td><td>${escape(item.publication || "NONE")}</td><td><span title="${escape(item.run_id || "")}">${escape(item.version || "—")}</span><br><small>${fmtDate(item.as_of)}</small></td><td class="module-reason">${escape(list(item.reason_codes).join(" · ") || "—")}</td></tr>`).join("") || `<tr><td colspan="7" class="empty">暂无模块状态。</td></tr>`;
  }

  function showError(message) {
    $("notice").hidden = false;
    $("notice").textContent = message;
    $("refreshPill").textContent = "读取失败";
    $("refreshPill").className = "pill warn";
  }

  function render(data, meta) {
    $("notice").hidden = true;
    renderProvisional(data); renderConfirmed(data); renderSummary(data); renderPaper(data); renderWeights(data); renderQuality(data); renderExplanation(data); renderModules(data);
    $("refreshPill").textContent = human(data.overall_quality, qualityText);
    $("refreshPill").className = `pill ${data.overall_quality === "OK" ? "ready" : "warn"}`;
    $("lastUpdated").textContent = `最后读取：${new Date().toLocaleString("zh-CN", { hour12: false })}`;
    const interval = Number(data.runtime_boundary && data.runtime_boundary.refresh_interval_seconds);
    window.clearTimeout(window.__m18RefreshTimer);
    window.__m18RefreshTimer = window.setTimeout(load, (Number.isFinite(interval) && interval > 0 ? interval : 900) * 1000);
  }

  async function load() {
    try {
      const response = await fetch("/api/m18/workbench", { cache: "no-store" });
      const body = await response.json();
      if (!response.ok || !body.data) throw new Error((body.error && body.error.message) || "M18 读模型不可用");
      render(body.data, body.meta || {});
    } catch (error) {
      showError(`M18 读模型暂时不可用：${error.message || error}`);
      window.clearTimeout(window.__m18RefreshTimer);
      window.__m18RefreshTimer = window.setTimeout(load, 30_000);
    }
  }

  $("refreshButton").addEventListener("click", load);
  load();
})();
