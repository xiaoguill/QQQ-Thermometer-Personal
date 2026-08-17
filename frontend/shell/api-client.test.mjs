import assert from "node:assert/strict";
import { ApiClientError, createApiClient } from "./api-client.mjs";

const calls = [];
const fakeFetch = async (path, init) => {
  calls.push({ path, init });
  return { ok: true, status: 200, json: async () => ({ source: "fixture" }) };
};

const client = createApiClient({ baseUrl: "/api", fetchImpl: fakeFetch });
await client.getHistory({ from: "2020-01-01", to: "2020-02-01", limit: 25 });
await client.explainSignals({ as_of: "2020-02-01" });
await client.getVersions();

assert.equal(calls[0].path, "/api/thermometer/history?from=2020-01-01&to=2020-02-01&limit=25");
assert.equal(calls[1].path, "/api/signals/explain?as_of=2020-02-01");
assert.equal(calls[2].path, "/api/versions");
assert.equal(calls.every(({ init }) => init.method === "GET"), true);
assert.equal(calls.every(({ init }) => init.credentials === "same-origin"), true);

assert.throws(() => createApiClient({ baseUrl: "https://remote.invalid", fetchImpl: fakeFetch }), /same-origin/);
await assert.rejects(() => client.getHistory({ limit: 501 }), /1 to 500/);
await assert.rejects(() => client.getHistory({ from: "not-a-date" }), /YYYY-MM-DD/);

const failingClient = createApiClient({
  fetchImpl: async () => ({ ok: false, status: 503, json: async () => ({ error: "unavailable" }) }),
});
await assert.rejects(() => failingClient.getLatest(), (error) => {
  assert.equal(error instanceof ApiClientError, true);
  assert.equal(error.status, 503);
  return true;
});

console.log("frontend api client checks passed");
