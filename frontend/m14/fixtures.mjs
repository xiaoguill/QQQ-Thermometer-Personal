const metadata = (quality, suffix, overrides = {}) => ({
  contract_version: "1.0.0",
  strategy_version: "SIMULATED_FIXTURE",
  as_of: "2026-08-18T20:00:00Z",
  signal_date: "2026-08-18",
  execution_date: "2026-08-19",
  data_quality: quality,
  run_id: `m14-fixture-run-${suffix}`,
  code_version: "m14-fixture-code-v1",
  data_version: "m14-fixture-data-v1",
  evidence_ref: `m14-fixture-evidence-${suffix}`,
  ...overrides,
});

const response = (suffix, quality, data, overrides = {}) => Object.freeze({
  meta: metadata(quality, suffix, overrides),
  data,
});

const failed = (message) => Object.freeze({ kind: "error", message });

export const M14_FIXTURES = Object.freeze({
  complete: Object.freeze({
    history: response(
      "ok",
      "ok",
      [
        {
          signal_date: "2024-01-02",
          execution_date: "2024-01-03",
          as_of: "2024-01-02T21:00:00Z",
          data_quality: "ok",
          run_id: "fixture-run-2024-01-03",
          evidence_ref: "fixture-history-evidence-2024-01-02",
          strategy_version: "v10_preserve_shock_recovery",
          state: "normal",
          temperature: 72,
          trend: "bullish",
          signal_agreement: 0.8,
          reason_codes: ["fixture_trend_alignment"],
          target_weights: { QQQ: 0.6, QLD: 0.4 },
        },
        {
          signal_date: "2024-03-11",
          execution_date: "2024-03-12",
          as_of: "2024-03-11T21:00:00Z",
          data_quality: "ok",
          run_id: "fixture-run-2024-03-12",
          evidence_ref: "fixture-history-evidence-2024-03-11",
          strategy_version: "v10_preserve_shock_recovery",
          state: "shock",
          temperature: 24,
          trend: "bearish",
          signal_agreement: 0.9,
          reason_codes: ["fixture_shock_condition"],
          target_weights: { BIL: 0.75, VXX: 0.25 },
        },
      ],
      { signal_date: "2024-03-11" },
    ),
    curve: response(
      "curve",
      "ok",
      [
        { execution_date: "2024-01-03", nav: 100000, cash: 12000, data_quality: "ok", strategy_return: 0, qqq_nav: 100000, qqq_return: 0, drawdown: 0, cost_bps: 5, cost_adjusted_nav: 100000 },
        { execution_date: "2024-03-12", nav: 101250, cash: 32000, data_quality: "ok", strategy_return: 0.0125, qqq_nav: 100800, qqq_return: 0.008, drawdown: 0, cost_bps: 5, cost_adjusted_nav: 101180 },
        { execution_date: "2024-06-28", nav: 109400, cash: 30000, data_quality: "ok", strategy_return: 0.094, qqq_nav: 111100, qqq_return: 0.111, drawdown: -0.018, cost_bps: 5, cost_adjusted_nav: 109100 },
      ],
      { signal_date: "2024-06-28" },
    ),
    metrics: response(
      "metrics",
      "ok",
      {
        period_start: "2024-01-03",
        period_end: "2024-06-28",
        cagr: 0.204,
        total_return: 0.094,
        max_drawdown: -0.018,
        max_drawdown_duration_days: 14,
        sharpe: 1.12,
        sortino: 1.58,
        turnover: 0.42,
        total_cost: 0.0017,
        annual_returns: { "2024": 0.094 },
        annual_drawdowns: { "2024": -0.018 },
        qqq_benchmark: {
          symbol: "QQQ",
          period_start: "2024-01-03",
          period_end: "2024-06-28",
          total_return: 0.111,
          max_drawdown: -0.027,
          price_basis: "SIMULATED_ADJUSTED_CLOSE",
          alignment: "same_execution_dates_fixture",
        },
        cost_stress: {
          policy: "fixture_only; backend-returned named scenarios",
          scenarios: [
            { cost_bps: 5, cagr: 0.204, max_drawdown: -0.018 },
            { cost_bps: 10, cagr: 0.199, max_drawdown: -0.019 },
            { cost_bps: 25, cagr: 0.187, max_drawdown: -0.021 },
          ],
          frozen_contract_note: "页面不覆盖冻结策略合同的成本口径。",
        },
        audit: {
          data_manifest: "fixture-manifest-m14-001",
          input_snapshot_hash: "fixture-snapshot-hash",
          future_data_check: "backend_reported_pass",
          report_hash: "fixture-report-hash",
          price_basis: "SIMULATED_ADJUSTED_CLOSE",
          execution_price_basis: "SIMULATED_NEXT_SESSION",
          benchmark_cost_policy: "backend_returned_only",
        },
      },
    ),
    ledger: response(
      "ledger",
      "ok",
      [
        { event_date: "2024-01-03", event_type: "BUY", symbol: "QQQ", quantity: 10, price: 100, cost: 0.5, status: "RECORDED", data_quality: "ok", run_id: "fixture-run-2024-01-03", idempotency_key: "fixture-ledger-001", evidence_ref: "fixture-ledger-evidence-001" },
        { event_date: "2024-03-12", event_type: "SELL", symbol: "QLD", quantity: 2, price: 60, cost: 0.3, status: "RECORDED", data_quality: "ok", run_id: "fixture-run-2024-03-12", idempotency_key: "fixture-ledger-002", evidence_ref: "fixture-ledger-evidence-002" },
      ],
    ),
    versions: response(
      "versions",
      "ok",
      [
        { version: "v10_preserve_shock_recovery", strategy_version: "v10_preserve_shock_recovery", implementation_version: "m07-target-weights/v1", status: "research_candidate", config_hash: "fixture-config-hash", code_version: "fixture-code-v1", data_version: "fixture-data-v1", approved_at: "2026-08-18T20:00:00Z", evidence_ref: "fixture-version-evidence" },
      ],
    ),
    quality: response("quality", "ok", { status: "ok", issues: [] }),
  }),

  missing_evidence: Object.freeze({
    history: response("missing-history", "partial", [{ state: "normal", temperature: 60, trend: "mixed", signal_agreement: 0.5, reason_codes: [], target_weights: {} }]),
    curve: response("missing-curve", "partial", [{ execution_date: "2024-01-03", nav: 100000, cash: 100000, data_quality: "partial" }]),
    metrics: response("missing-metrics", "needs_review", { cagr: 0.1, max_drawdown: -0.2 }),
    ledger: response("missing-ledger", "partial", [{ event_date: "2024-01-03", event_type: "BUY", symbol: "QQQ", quantity: 1 }]),
    versions: response("missing-versions", "partial", [{ version: "v10_preserve_shock_recovery", status: "research_candidate" }]),
    quality: response("missing-quality", "needs_review", { status: "needs_review", issues: ["fixture provenance is incomplete"] }),
  }),

  empty_failed: Object.freeze({
    history: response("empty-history", "failed", []),
    curve: response("empty-curve", "failed", []),
    metrics: response("empty-metrics", "needs_review", {}),
    ledger: response("empty-ledger", "failed", []),
    versions: response("empty-versions", "failed", []),
    quality: response("empty-quality", "failed", { status: "failed", issues: ["no backend audit data"] }),
  }),

  failed: Object.freeze({
    history: failed("SIMULATED history request failed"),
    curve: failed("SIMULATED performance curve request failed"),
    metrics: failed("SIMULATED performance metrics request failed"),
    ledger: failed("SIMULATED ledger request failed"),
    versions: failed("SIMULATED versions request failed"),
    quality: failed("SIMULATED data quality request failed"),
  }),
});
