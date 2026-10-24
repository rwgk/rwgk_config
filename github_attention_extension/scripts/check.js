"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {execFileSync} = require("node:child_process");
const {KIND_GLYPHS, ICON_STATES, renderIcon} = require("./render-icons.js");
const root = path.join(__dirname, "..", "extension");
const manifest = JSON.parse(fs.readFileSync(path.join(root, "manifest.json"), "utf8"));
const packageInfo = JSON.parse(fs.readFileSync(path.join(root, "..", "package.json"), "utf8"));
const packageLock = JSON.parse(fs.readFileSync(path.join(root, "..", "package-lock.json"), "utf8"));
const core = require(path.join(root, "core.js"));
assert.equal(manifest.version, packageInfo.version, "Manifest and package versions must match.");
assert.equal(packageLock.version, packageInfo.version, "Lockfile and package versions must match.");
assert.equal(packageLock.packages[""].version, packageInfo.version, "Lockfile root-package version must match.");
assert.deepEqual(core.KINDS, ["reviews", "approved", "mentions", "notifications"]);
assert.equal(manifest.manifest_version, 3);
assert.deepEqual(manifest.permissions, ["alarms", "storage"]);
assert.deepEqual(manifest.host_permissions, ["https://github.com/*"]);
assert.deepEqual(manifest.content_scripts[0].matches, ["https://github.com/search*", "https://github.com/notifications*"]);
assert.deepEqual(manifest.content_scripts[0].js, ["core.js", "content.js"]);
assert.equal(manifest.content_security_policy.extension_pages, "script-src 'self'; object-src 'none'");
assert.deepEqual(Object.keys(KIND_GLYPHS), core.KINDS, "Every queue must have an icon glyph.");
assert.deepEqual(Object.values(KIND_GLYPHS), ["R", "A", "M", "*"], "Queue glyphs must follow priority order.");
const iconFiles = core.KINDS.flatMap((kind) => ICON_STATES.map((state) => `${kind}-${state}.svg`));
assert.equal(iconFiles.length, 48, "Every queue must have clear, error, and ten count icons.");
assert.deepEqual(fs.readdirSync(path.join(root, "icons")).sort(), [...iconFiles].sort(), "Packaged icons must match the queue/state matrix exactly.");
for (const file of [manifest.background.service_worker, manifest.action.default_popup, ...manifest.content_scripts[0].js, "popup.js", "popup.css", ...iconFiles.map((icon) => `icons/${icon}`)]) {
  assert.ok(fs.statSync(path.join(root, file)).isFile(), `Missing manifest/UI resource: ${file}`);
}
for (const kind of core.KINDS) {
  for (const state of ICON_STATES) {
    const icon = `${kind}-${state}.svg`;
    const svg = fs.readFileSync(path.join(root, "icons", icon), "utf8");
    assert.equal(svg, renderIcon(kind, state), `Packaged icon differs from the shared layout: ${icon}`);
    const queueGlyphs = [...svg.matchAll(/<text\b[^>]*\bid="queue-glyph"[^>]*>([^<]*)<\/text>/g)].map((match) => match[1]);
    assert.deepEqual(queueGlyphs, [KIND_GLYPHS[kind]], `Wrong queue glyph in ${icon}`);
    const badgeGlyphs = [...svg.matchAll(/<text\b[^>]*\bid="badge-glyph"[^>]*>([^<]*)<\/text>/g)].map((match) => match[1]);
    const badgeGlyph = state === "error" ? "!" : state === "count-x" ? "X" : state.slice("count-".length);
    assert.deepEqual(badgeGlyphs, state === "clear" ? [] : [badgeGlyph], `Wrong badge glyph in ${icon}`);
    assert.ok(!/<script\b|<foreignObject\b|(?:href|src)\s*=|\bon\w+\s*=|\burl\s*\(/i.test(svg), `Icon must be self-contained: ${icon}`);
  }
}
for (const file of fs.readdirSync(root).filter((name) => name.endsWith(".js"))) {
  execFileSync(process.execPath, ["--check", path.join(root, file)], {stdio: "inherit"});
  const source = fs.readFileSync(path.join(root, file), "utf8");
  assert.ok(!/\beval\s*\(|\bnew Function\s*\(|\bfetch\s*\(|XMLHttpRequest|\.cookies\b/.test(source), `Unexpected dynamic code, network, or cookie API in ${file}`);
}
for (const file of fs.readdirSync(__dirname).filter((name) => name.endsWith(".js"))) {
  execFileSync(process.execPath, ["--check", path.join(__dirname, file)], {stdio: "inherit"});
}
const popup = fs.readFileSync(path.join(root, "popup.html"), "utf8");
assert.ok(!/<script\b(?![^>]*\bsrc=)/i.test(popup), "Popup must not use inline scripts.");
const popupIds = [...popup.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
assert.deepEqual(popupIds.filter((id) => id.endsWith("-link")), core.KINDS.map((kind) => `${kind}-link`), "Popup queue links must follow priority order.");
for (const kind of core.KINDS) {
  for (const suffix of ["label", "link", "status", "detail"]) {
    const id = `${kind}-${suffix}`;
    assert.equal(popupIds.filter((value) => value === id).length, 1, `Popup must contain exactly one ${id}.`);
  }
}
const readme = fs.readFileSync(path.join(root, "..", "README.md"), "utf8");
assert.equal([...readme.matchAll(/^## (.+)$/gm)][0]?.[1], "Quick start", "Quick start must be the first README section.");
const quickStart = readme.split("## Quick start\n")[1]?.split("\n## ")[0] || "";
const quickStartUrls = [...quickStart.matchAll(/^- \*\*[^*]+:\*\* <([^>]+)>$/gm)].map((match) => match[1]);
assert.deepEqual(quickStartUrls, core.KINDS.map((kind) => core.URLS[kind]), "Quick start must list the four canonical queue URLs in priority order.");
console.log("Versions, popup, Quick start URLs, packaged resources, syntax, and permission checks passed.");
