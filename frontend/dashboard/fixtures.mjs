const metadata = (quality, suffix) => ({
  contract_version: "1.0.0",
  strategy_version: "SIMULATED_FIXTURE",
  as_of: "2026-08-17T20:00:00Z",
  signal_date: "2026-08-17",
  execution_date: "2026-08-18",
  data_quality: quality,
  run_id: `fixture-run-${suffix}`,
  code_version: "fixture-code-v1",
  data_version: "fixture-data-v1",
  evidence_ref: `fixture-evidence-${suffix}`,
});

const response = (suffix, quality, data, indicators, issues = []) => Object.freeze({
  latest: Object.freeze({ meta: metadata(quality, suffix), data: Object.freeze(data) }),
  explanation: Object.freeze({
    meta: metadata(quality, suffix),
    data: Object.freeze({
      reason_codes: Array.isArray(data.reason_codes) ? data.reason_codes : [],
      indicators: Object.freeze(indicators),
    }),
  }),
  quality: Object.freeze({
    meta: metadata(quality, suffix),
    data: Object.freeze({ status: quality, issues }),
  }),
});

export const DASHBOARD_FIXTURES = Object.freeze({
  normal: response(
    "normal",
    "ok",
    {
      state: "normal",
      temperature: 72,
      trend: "bullish",
      signal_agreement: 0.8,
      reason_codes: ["fixture_trend_alignment", "fixture_quality_ok"],
      target_weights: { SIMULATED_ASSET_A: 0.6, SIMULATED_ASSET_B: 0.4 },
    },
    { fixture_trend: "positive", fixture_volatility: "stable" },
  ),
  shock: response(
    "shock",
    "ok",
    {
      state: "shock",
      temperature: 18,
      trend: "bearish",
      signal_agreement: 0.9,
      reason_codes: ["fixture_shock_condition", "fixture_protection_active"],
      target_weights: { SIMULATED_DEFENSIVE: 0.75, SIMULATED_HEDGE: 0.25 },
    },
    { fixture_trend: "negative", fixture_volatility: "elevated" },
  ),
  recovery: response(
    "recovery",
    "ok",
    {
      state: "recovery",
      temperature: 51,
      trend: "mixed",
      signal_agreement: 0.5,
      reason_codes: ["fixture_recovery_observation"],
      target_weights: { SIMULATED_ASSET_A: 0.45, SIMULATED_DEFENSIVE: 0.55 },
    },
    { fixture_trend: "mixed", fixture_volatility: "declining" },
  ),
  needs_review: response(
    "review",
    "needs_review",
    {
      state: "needs_review",
      temperature: null,
      trend: null,
      signal_agreement: null,
      reason_codes: ["fixture_data_quality_needs_review"],
      target_weights: { SIMULATED_CASH: 1 },
    },
    {},
    ["fixture indicator is unavailable"],
  ),
  partial: response(
    "partial",
    "partial",
    {
      state: "normal",
      temperature: 64,
      trend: null,
      signal_agreement: null,
      reason_codes: ["fixture_partial_payload"],
    },
    {},
    ["fixture target weights are missing"],
  ),
  failed: Object.freeze({ kind: "error", message: "SIMULATED fixture request failed" }),
});
