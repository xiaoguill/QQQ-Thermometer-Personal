import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../../../frontend/m16");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const css = fs.readFileSync(path.join(root, "styles.css"), "utf8");
const js = fs.readFileSync(path.join(root, "app.js"), "utf8");
const readme = fs.readFileSync(path.join(root, "README.md"), "utf8");

assert.match(html, /Northstar Live/);
assert.match(html, /PRIVATE \/ LIVE/);
assert.match(html, /styles\.css/);
assert.match(html, /app\.js/);
assert.match(js, /EventSource/);
assert.match(js, /\/api\/live\/events/);
assert.match(js, /Asia\/Shanghai/);
assert.match(html, /PROVISIONAL/);
assert.match(readme, /不访问 Massive/);
assert.doesNotMatch(js, /MASSIVE_API_KEY|apiKey|Authorization\s*:/i);
assert.doesNotMatch(html + css + js, /https?:\/\/(?!127\.0\.0\.1)/i);

console.log("M16 frontend asset checks passed");
