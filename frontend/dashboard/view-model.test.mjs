import assert from "node:assert/strict";
import { DASHBOARD_FIXTURES } from "./fixtures.mjs";
import { normalizeDashboardPayload } from "./view-model.mjs";

const normal = normalizeDashboardPayload({ ...DASHBOARD_FIXTURES.normal, mode: "SIMULATED" });
assert.equal(normal.mode, "SIMULATED");
assert.equal(normal.state.key, "normal");
assert.equal(normal.temperature.value, 72);
assert.equal(normal.agreement.isProbability, false);
assert.equal(normal.confirmation.key, "not_provided");
assert.equal(normal.confirmation.label, "未提供");
assert.deepEqual(normal.weights.rows.map((row) => [row.asset, row.value]), [["SIMULATED_ASSET_A", 0.6], ["SIMULATED_ASSET_B", 0.4]]);
assert.equal(normal.weights.total, 1);

const review = normalizeDashboardPayload({ ...DASHBOARD_FIXTURES.needs_review, mode: "SIMULATED" });
assert.equal(review.quality.key, "needs_review");
assert.equal(review.temperature.display, "未提供");
assert.equal(review.agreement.display, "未提供");
assert.equal(review.explanationAvailable, true);

const partial = normalizeDashboardPayload({ ...DASHBOARD_FIXTURES.partial, mode: "SIMULATED" });
assert.equal(partial.weights.missing, true);
assert.equal(partial.missing.includes("target_weights"), true);
assert.equal(partial.missing.includes("indicators"), true);

const explicitConfirmation = structuredClone(DASHBOARD_FIXTURES.normal);
explicitConfirmation.latest.meta.confirmation_status = "confirmed";
const explicit = normalizeDashboardPayload({ ...explicitConfirmation, mode: "LIVE" });
assert.equal(explicit.confirmation.key, "confirmed");

const invalid = structuredClone(DASHBOARD_FIXTURES.normal);
invalid.latest.data.target_weights = { VALID: 0.5, INVALID: -1, TEXT: "0.5" };
const invalidView = normalizeDashboardPayload({ ...invalid, mode: "LIVE" });
assert.equal(invalidView.weights.rows.length, 1);
assert.equal(invalidView.weights.invalidCount, 2);

console.log("dashboard view-model checks passed");
