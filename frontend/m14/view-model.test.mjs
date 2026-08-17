import assert from "node:assert/strict";
import { M14_FIXTURES } from "./fixtures.mjs";
import { normalizeM14Payloads, normalizeNamedRows } from "./view-model.mjs";

const complete = normalizeM14Payloads({ ...M14_FIXTURES.complete, mode: "SIMULATED" });
assert.equal(complete.mode, "SIMULATED");
assert.equal(complete.failedCount, 0);
assert.equal(complete.endpoints.history.rows.length, 2);
assert.equal(complete.endpoints.history.rows[0].signalDate, "2024-01-02");
assert.equal(complete.endpoints.history.rows[0].executionDate, "2024-01-03");
assert.equal(complete.endpoints.curve.rows[1].qqqNav, 100800);
assert.equal(complete.endpoints.metrics.annualReturns.rows[0].label, "2024");
assert.equal(complete.endpoints.metrics.annualDrawdowns.rows[0].value, -0.018);
assert.equal(complete.endpoints.metrics.benchmark.provided, true);
assert.equal(complete.endpoints.metrics.costStress.rows.length, 3);
assert.equal(complete.endpoints.metrics.costStress.rows[0].label, 5);
assert.equal(complete.endpoints.ledger.rows[0].idempotencyKey, "fixture-ledger-001");
assert.equal(complete.endpoints.versions.rows[0].configHash, "fixture-config-hash");
assert.equal(complete.endpoints.quality.status, "ok");

const missing = normalizeM14Payloads({ ...M14_FIXTURES.missing_evidence, mode: "SIMULATED" });
assert.equal(missing.endpoints.history.rows[0].signalDate, null);
assert.equal(missing.endpoints.history.rows[0].executionDate, null);
assert.equal(missing.endpoints.metrics.annualReturns.provided, false);
assert.equal(missing.endpoints.metrics.benchmark.provided, false);
assert.equal(missing.endpoints.metrics.costStress.provided, false);
assert.equal(missing.endpoints.quality.status, "needs_review");

const empty = normalizeM14Payloads({ ...M14_FIXTURES.empty_failed, mode: "SIMULATED" });
assert.equal(empty.endpoints.history.missingRows, true);
assert.equal(empty.endpoints.curve.missingRows, true);
assert.equal(empty.endpoints.metrics.scalarFields[0].provided, false);
assert.equal(empty.endpoints.quality.status, "failed");

const failed = normalizeM14Payloads({ ...M14_FIXTURES.failed, mode: "LIVE" });
assert.equal(failed.mode, "LIVE");
assert.equal(failed.failedCount, 6);
assert.equal(failed.endpoints.metrics.kind, "error");
assert.equal(failed.endpoints.metrics.quality, "failed");

assert.deepEqual(normalizeNamedRows({ "2024": 0.1 }), [{ label: "2024", value: 0.1 }]);
assert.deepEqual(normalizeNamedRows(null), []);

console.log("M14 view-model checks passed");
