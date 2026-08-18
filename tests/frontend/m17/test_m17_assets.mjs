import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const html = fs.readFileSync(path.join(root, "frontend/m17/index.html"), "utf8");
const app = fs.readFileSync(path.join(root, "frontend/m17/app.js"), "utf8");
const readme = fs.readFileSync(path.join(root, "frontend/m17/README.md"), "utf8");

assert.ok(app.includes("/api/m17/overview"));
assert.ok(app.includes("/api/m17/paper-plan"));
assert.ok(app.includes("/api/live/events"));
assert.ok(app.includes("Asia/Shanghai"));
assert.doesNotMatch(html, /https?:\/\//);
assert.doesNotMatch(app, /https?:\/\//);
assert.doesNotMatch(app, /innerHTML/);
assert.doesNotMatch(app, /method\s*:\s*["']POST["']/i);
assert.doesNotMatch(app, /\/api\/(?:orders?|broker|trading)/i);
for (const route of ["/dashboard/index.html", "/m14/index.html", "/demo/index.html", "/shell/index.html", "/m16/index.html"]) {
  assert.match(html, new RegExp(route.replaceAll("/", "\\/")));
}
assert.match(readme, /paper-only|纸上/i);
console.log("M17 frontend asset checks passed");
