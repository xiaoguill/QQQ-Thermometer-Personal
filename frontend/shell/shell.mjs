import { ApiClientError, createApiClient } from "./api-client.mjs";
import { SHELL_FIXTURES, getShellFixture } from "./fixtures.mjs";

const root = document.querySelector("[data-shell-root]");
const fixtureButtons = [...document.querySelectorAll("[data-fixture]")];
const fixtureCards = [...document.querySelectorAll("[data-fixture-card]")];
const statusMessage = document.querySelector("[data-status-message]");
const navButtons = [...document.querySelectorAll("[data-view]")];
const panels = [...document.querySelectorAll("[data-panel]")];

function setText(selector, value) {
  const element = document.querySelector(selector);
  if (element) element.textContent = value;
}

function renderFixture(key) {
  const fixture = getShellFixture(key);
  fixtureCards.forEach((card) => {
    card.hidden = card.dataset.fixtureCard !== key;
  });
  fixtureButtons.forEach((button) => {
    const active = button.dataset.fixture === key;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  root.dataset.fixture = key;
  setText("[data-fixture-label]", `${fixture.label} · ${fixture.id}`);
  setText("[data-status-message]", `当前仅展示 ${fixture.id} 模拟状态；确认状态、策略版本和目标仓位均未由浏览器推断。`);
}

function selectView(view) {
  navButtons.forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  panels.forEach((panel) => {
    panel.hidden = panel.dataset.panel !== view;
  });
  const activePanel = document.querySelector(`[data-panel="${view}"]`);
  if (activePanel) activePanel.focus({ preventScroll: true });
}

async function tryLocalApi() {
  const button = document.querySelector('[data-action="try-api"]');
  if (button) button.disabled = true;
  setText("[data-status-message]", "正在请求同源本地 API；壳层只会读取，不会提交写操作。...");
  try {
    const client = createApiClient({ baseUrl: root.dataset.apiBase || "/api" });
    await client.getLatest();
    renderFixture("empty");
    setText("[data-status-message]", "已收到本地 API 响应；M12 壳层不解释策略字段，后续由专门页面按契约渲染。");
  } catch (error) {
    renderFixture("failed");
    const message = error instanceof ApiClientError ? error.message : "本地 API client 配置无效";
    setText("[data-status-message]", `${message}；当前保持待复核状态。`);
  } finally {
    if (button) button.disabled = false;
  }
}

fixtureButtons.forEach((button) => {
  button.addEventListener("click", () => renderFixture(button.dataset.fixture));
});

navButtons.forEach((button) => {
  button.addEventListener("click", () => selectView(button.dataset.view));
});

document.querySelector('[data-action="try-api"]')?.addEventListener("click", tryLocalApi);

document.querySelector('[data-action="toggle-theme"]')?.addEventListener("click", (event) => {
  const light = root.dataset.theme !== "light";
  root.dataset.theme = light ? "light" : "dark";
  event.currentTarget.setAttribute("aria-pressed", String(light));
  event.currentTarget.textContent = light ? "深色显示" : "浅色显示";
});

setText("[data-fixture-label]", "暂无快照 · shell-empty-v1");
renderFixture("empty");

export { SHELL_FIXTURES, renderFixture, selectView };
