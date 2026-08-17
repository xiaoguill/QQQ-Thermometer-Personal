const READ_ONLY_ENDPOINTS = Object.freeze({
  latest: "/thermometer/latest",
  history: "/thermometer/history",
  signalExplanation: "/signals/explain",
  nextTriggers: "/triggers/next",
  portfolioTargets: "/portfolio/targets",
  portfolioLatest: "/portfolio/latest",
  ledger: "/portfolio/ledger",
  performanceCurve: "/performance/curve",
  performanceMetrics: "/performance/metrics",
  dataQuality: "/data-quality/latest",
  versions: "/versions",
});

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

export class ApiClientError extends Error {
  constructor(message, { status = 0, payload = null, path = "" } = {}) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
    this.payload = payload;
    this.path = path;
  }
}

function normalizeBaseUrl(value) {
  const raw = String(value ?? "/api").trim();
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.includes("?") || raw.includes("#")) {
    throw new TypeError("API baseUrl must be a same-origin path");
  }
  return raw.replace(/\/+$/, "") || "/";
}

function validateDate(name, value) {
  if (value == null) return;
  if (typeof value !== "string" || !DATE_PATTERN.test(value)) {
    throw new TypeError(`${name} must use YYYY-MM-DD`);
  }
}

function buildQuery(values) {
  const query = new URLSearchParams();
  for (const [name, value] of Object.entries(values || {})) {
    if (value == null || value === "") continue;
    if (name === "from" || name === "to" || name === "as_of") validateDate(name, value);
    if (name === "limit") {
      if (!Number.isInteger(value) || value < 1 || value > 500) {
        throw new TypeError("limit must be an integer from 1 to 500");
      }
    }
    query.set(name, String(value));
  }
  return query.toString();
}

function joinPath(baseUrl, endpoint, query) {
  const base = baseUrl === "/" ? "" : baseUrl;
  const path = `${base}${endpoint}`;
  return query ? `${path}?${query}` : path;
}

export function createApiClient({ baseUrl = "/api", fetchImpl = globalThis.fetch } = {}) {
  const normalizedBaseUrl = normalizeBaseUrl(baseUrl);
  if (typeof fetchImpl !== "function") {
    throw new TypeError("a fetch implementation is required");
  }

  async function get(endpoint, queryValues = {}) {
    const query = buildQuery(queryValues);
    const path = joinPath(normalizedBaseUrl, endpoint, query);
    let response;
    try {
      response = await fetchImpl(path, {
        method: "GET",
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        cache: "no-store",
      });
    } catch (error) {
      throw new ApiClientError("本地 API 请求未完成", { path, payload: null });
    }

    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      payload = null;
    }
    if (!response.ok) {
      throw new ApiClientError(`本地 API 返回 HTTP ${response.status}`, {
        status: response.status,
        payload,
        path,
      });
    }
    return payload;
  }

  return Object.freeze({
    getLatest: () => get(READ_ONLY_ENDPOINTS.latest),
    getHistory: ({ from, to, limit } = {}) => get(READ_ONLY_ENDPOINTS.history, { from, to, limit }),
    explainSignals: ({ as_of: asOf } = {}) => get(READ_ONLY_ENDPOINTS.signalExplanation, { as_of: asOf }),
    getNextTriggers: () => get(READ_ONLY_ENDPOINTS.nextTriggers),
    getPortfolioTargets: ({ as_of: asOf } = {}) => get(READ_ONLY_ENDPOINTS.portfolioTargets, { as_of: asOf }),
    getPortfolioLatest: () => get(READ_ONLY_ENDPOINTS.portfolioLatest),
    getLedger: ({ from, to, limit } = {}) => get(READ_ONLY_ENDPOINTS.ledger, { from, to, limit }),
    getPerformanceCurve: ({ as_of: asOf } = {}) => get(READ_ONLY_ENDPOINTS.performanceCurve, { as_of: asOf }),
    getPerformanceMetrics: ({ as_of: asOf } = {}) => get(READ_ONLY_ENDPOINTS.performanceMetrics, { as_of: asOf }),
    getDataQuality: () => get(READ_ONLY_ENDPOINTS.dataQuality),
    getVersions: () => get(READ_ONLY_ENDPOINTS.versions),
  });
}

export { READ_ONLY_ENDPOINTS };
