"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {execFileSync} = require("node:child_process");
const root = path.join(__dirname, "..", "extension");
const manifest = JSON.parse(fs.readFileSync(path.join(root, "manifest.json"), "utf8"));
assert.equal(manifest.manifest_version, 3);
assert.deepEqual(manifest.permissions, ["alarms", "storage"]);
assert.deepEqual(manifest.host_permissions, ["https://github.com/*"]);
assert.deepEqual(manifest.content_scripts[0].matches, ["https://github.com/search*", "https://github.com/notifications*"]);
assert.deepEqual(manifest.content_scripts[0].js, ["core.js", "content.js"]);
assert.equal(manifest.content_security_policy.extension_pages, "script-src 'self'; object-src 'none'");
for (const file of [manifest.background.service_worker, manifest.action.default_popup, ...manifest.content_scripts[0].js, "popup.js", "popup.css", "icons/nonempty.svg", "icons/error.svg", "icons/clear.svg"]) {
  assert.ok(fs.statSync(path.join(root, file)).isFile(), `Missing manifest/UI resource: ${file}`);
}
for (const file of fs.readdirSync(root).filter((name) => name.endsWith(".js"))) {
  execFileSync(process.execPath, ["--check", path.join(root, file)], {stdio: "inherit"});
  const source = fs.readFileSync(path.join(root, file), "utf8");
  assert.ok(!/\beval\s*\(|\bnew Function\s*\(|\bfetch\s*\(|XMLHttpRequest|\.cookies\b/.test(source), `Unexpected dynamic code, network, or cookie API in ${file}`);
}
const popup = fs.readFileSync(path.join(root, "popup.html"), "utf8");
assert.ok(!/<script\b(?![^>]*\bsrc=)/i.test(popup), "Popup must not use inline scripts.");
console.log("Manifest, packaged resources, syntax, and permission checks passed.");
