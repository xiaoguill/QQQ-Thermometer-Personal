(function () {
  "use strict";

  const root = document.getElementById("appShell");
  const toast = document.querySelector(".toast");
  let toastTimer;

  function showToast(message) {
    toast.textContent = message;
    toast.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () { toast.hidden = true; }, 2800);
  }

  function selectView(view) {
    document.querySelectorAll(".nav-item").forEach(function (button) {
      const isActive = button.dataset.view === view;
      button.classList.toggle("is-active", isActive);
      if (isActive) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    document.querySelectorAll(".view-panel").forEach(function (panel) {
      panel.classList.toggle("is-active", panel.dataset.panel === view);
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  document.querySelectorAll(".nav-item").forEach(function (button) {
    button.addEventListener("click", function () { selectView(button.dataset.view); });
  });

  document.querySelectorAll('[data-action="back-overview"]').forEach(function (button) {
    button.addEventListener("click", function () { selectView("overview"); });
  });

  document.querySelectorAll(".range-button").forEach(function (button) {
    button.addEventListener("click", function () {
      document.querySelectorAll(".range-button").forEach(function (item) { item.classList.remove("is-active"); });
      button.classList.add("is-active");
      showToast("已切换到 " + button.dataset.range + " 演示窗口");
    });
  });

  document.querySelectorAll(".legend-toggle").forEach(function (button) {
    button.addEventListener("click", function () {
      const pressed = button.getAttribute("aria-pressed") === "true";
      button.setAttribute("aria-pressed", String(!pressed));
      const series = button.dataset.series;
      document.querySelectorAll('[data-series-path="' + series + '"]').forEach(function (path) {
        path.style.opacity = pressed ? "0" : "1";
      });
      document.querySelectorAll(".chart-point-" + series).forEach(function (point) {
        point.style.opacity = pressed ? "0" : "1";
      });
    });
  });

  document.querySelector('[data-action="toggle-theme"]').addEventListener("click", function () {
    root.classList.toggle("is-soft-theme");
    showToast(root.classList.contains("is-soft-theme") ? "演示：浅色阅读层已打开" : "已恢复深色研究台");
  });

  document.querySelectorAll('[data-action="refresh-demo"]').forEach(function (button) {
    button.addEventListener("click", function () { showToast("演示快照已刷新；当前仍使用静态模拟数据"); });
  });
  document.querySelectorAll('[data-action="open-paper-ledger"]').forEach(function (button) {
    button.addEventListener("click", function () { showToast("纸上观察入口：下一步接入本地 append-only ledger"); });
  });
  document.querySelectorAll('[data-action="explain-signal"], [data-action="open-evidence"]').forEach(function (button) {
    button.addEventListener("click", function () { selectView("signals"); showToast("已打开信号证据视图（demo）"); });
  });
  document.querySelector('[data-action="open-audit"]').addEventListener("click", function () { showToast("审计记录入口：版本、数据源、信号日和执行日将在此展开"); });
  document.querySelector('[data-action="go-governance"]').addEventListener("click", function () { showToast("治理边界：只读监控、人工确认、无自动下单"); });

  const themeStyle = document.createElement("style");
  themeStyle.textContent = ".is-soft-theme { --bg: #eaf0f6; --sidebar: #e4ecf5; --surface: #f7faff; --surface-2: #edf3f9; --surface-3: #d7e2ee; --line: #c9d6e5; --text: #16243a; --muted: #53647c; --faint: #7b8ba0; --shadow: 0 18px 44px rgba(36, 59, 93, .13); } .is-soft-theme .sidebar { background: linear-gradient(180deg, #eef4fa 0%, #e4ecf5 100%); } .is-soft-theme .surface-card { background: linear-gradient(145deg, rgba(247,250,255,.96), rgba(237,243,249,.96)); } .is-soft-theme .temperature-gauge::before, .is-soft-theme .donut-chart::before { background: var(--surface); } .is-soft-theme .chart-tooltip rect { fill: rgba(247,250,255,.96); }";
  document.head.appendChild(themeStyle);
}());
