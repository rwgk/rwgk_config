"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { parseHTML } = require("linkedom");
const { createController } = require("../extension/content.js");
const { describeQueue } = require("../extension/popup.js");
const core = require("../extension/core.js");

function harness(initialState = { status: "ok", count: 3, message: "" }, initialUrl = core.URLS.reviews) {
  const { document } = parseHTML('<!doctype html><html><head><title>GitHub original title</title><link rel="icon" href="original.svg"></head><body><main></main></body></html>');
  let timestamp = 1000;
  let state = initialState;
  let observerCallback;
  let intervalCallback;
  const timers = new Map();
  const reports = [];
  const listeners = new Map();
  let nextTimer = 0;
  const window = {
    location: { href: initialUrl },
    Date: { now: () => timestamp },
    MutationObserver: class {
      constructor(callback) { observerCallback = callback; }
      observe() {}
      disconnect() {}
    },
    setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, { callback, at: timestamp + delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    setInterval(callback) { intervalCallback = callback; return 1; },
    clearInterval() { intervalCallback = null; },
    addEventListener(event, callback) { listeners.set(event, callback); },
    removeEventListener(event) { listeners.delete(event); },
  };
  const chrome = { runtime: {
    getURL: (path) => `chrome-extension://test/${path}`,
    sendMessage(message) { reports.push(message); return Promise.resolve(); },
    onMessage: { addListener() {}, removeListener() {} },
  } };
  const controller = createController(document, window, chrome, {
    ...core, readDocument: () => ({ ...state }),
  });
  return {
    document, window, reports, timers, controller,
    setState(next) { state = next; },
    advance(ms) { timestamp += ms; },
    interval() { intervalCallback(); },
    event(name) { listeners.get(name)(); },
    mutation(mutations) { observerCallback(mutations); },
    runTimers() {
      for (const [id, timer] of [...timers]) {
        if (timer.at <= timestamp) { timers.delete(id); timer.callback(); }
      }
    },
    icon() { return document.getElementById("github-attention-favicon"); },
  };
}

test("content shows red for nonempty and green for verified zero without changing site icons, title, or body", () => {
  const h = harness();
  assert.match(h.icon().href, /nonempty\.svg$/);
  assert.equal(h.reports[0].state.count, 3);
  assert.equal(h.document.title, "GitHub original title");
  assert.equal(h.document.body.innerHTML, "<main></main>");
  h.setState({ status: "ok", count: 0, message: "" });
  h.controller.check();
  assert.match(h.icon().href, /clear\.svg$/);
  assert.equal(h.document.querySelector('link[rel="icon"]').href, "original.svg");
  h.controller.dispose();
  assert.equal(h.icon(), null);
  assert.equal(h.document.querySelector('link[rel="icon"]').href, "original.svg");
});

test("empty, nonempty, and empty transitions stay visible, but a stale empty queue becomes amber", () => {
  const h = harness({ status: "ok", count: 0, message: "" });
  assert.match(h.icon().href, /clear\.svg$/);
  h.setState({ status: "ok", count: 2, message: "" });
  h.controller.check();
  assert.match(h.icon().href, /nonempty\.svg$/);
  h.setState({ status: "ok", count: 0, message: "" });
  h.controller.check();
  assert.match(h.icon().href, /clear\.svg$/);
  h.advance(core.STALE_MS);
  h.interval();
  assert.match(h.icon().href, /error\.svg$/);
  assert.equal(h.reports.at(-1).state.status, "error");
  assert.match(h.reports.at(-1).state.message, /stale/i);
  h.controller.dispose();
});

test("unknown page is amber during loading grace and remains amber on final error", () => {
  const h = harness({ status: "error", count: null, message: "Unknown layout" });
  assert.match(h.icon().href, /error\.svg$/);
  assert.equal(h.reports[0].state.status, "loading");
  h.advance(15001);
  h.runTimers();
  assert.equal(h.reports.at(-1).state.status, "error");
  assert.equal(h.reports.at(-1).state.message, "Unknown layout");
  h.controller.dispose();
});

test("identical DOM rereads neither report nor postpone stale indication", () => {
  const h = harness({ status: "ok", count: 0, message: "" });
  h.advance(120000);
  h.controller.check();
  assert.equal(h.reports.length, 1);
  h.advance(core.STALE_MS - 120000);
  h.interval();
  assert.match(h.icon().href, /error\.svg$/);
  assert.match(h.reports.at(-1).state.message, /stale/i);
  h.setState({ status: "ok", count: 1, message: "" });
  h.controller.check();
  assert.match(h.icon().href, /nonempty\.svg$/);
  assert.equal(h.reports.at(-1).state.status, "ok");
  h.controller.dispose();
});

for (const count of [0, 3]) {
  test(`leaving the strict watched URL clears the ${count ? "red" : "green"} override and prevents further reports`, () => {
    const h = harness({ status: "ok", count, message: "" });
    assert.match(h.icon().href, count ? /nonempty\.svg$/ : /clear\.svg$/);
    h.window.location.href = "https://github.com/notifications";
    h.interval();
    assert.equal(h.icon(), null);
    assert.equal(h.document.querySelector('link[rel="icon"]').href, "original.svg");
    assert.equal(h.reports.length, 1);
    h.controller.check();
    assert.equal(h.reports.length, 1);
    h.controller.dispose();
  });
}

test("an error with a misleading zero count is amber during loading and after the grace period", () => {
  const h = harness({ status: "error", count: 0, message: "Unverified empty view" });
  assert.match(h.icon().href, /error\.svg$/);
  assert.equal(h.reports.at(-1).state.status, "loading");
  h.advance(15001);
  h.runTimers();
  assert.match(h.icon().href, /error\.svg$/);
  assert.equal(h.reports.at(-1).state.status, "error");
  h.controller.dispose();
});

test("our own favicon mutations do not schedule a reread loop", () => {
  const h = harness();
  const badge = h.icon();
  h.mutation([{ type: "childList", target: h.document.head, addedNodes: [badge], removedNodes: [] }]);
  h.mutation([{ type: "attributes", target: badge }]);
  assert.equal(h.timers.size, 0);
  h.controller.dispose();
});

test("in-page watched-query navigation rejects old empty DOM until a full reload", () => {
  const h = harness({ status: "ok", count: 0, message: "" });
  h.advance(120000);
  h.window.location.href = `${core.URLS.reviews}&page=1`;
  h.event("turbo:load");
  h.interval();
  assert.equal(h.reports.length, 2);
  assert.equal(h.reports.at(-1).state.status, "error");
  assert.match(h.reports.at(-1).state.message, /in-page navigation/);
  assert.match(h.icon().href, /error\.svg$/);
  h.window.location.href = core.URLS.reviews;
  h.interval();
  assert.equal(h.reports.at(-1).state.status, "error");
  h.controller.dispose();
});

test("unchanged-URL navigation events do not renew successful-check timestamps", () => {
  const h = harness({ status: "ok", count: 0, message: "" });
  h.advance(120000);
  h.event("turbo:load");
  h.interval();
  assert.equal(h.reports.length, 1);
  h.advance(core.STALE_MS - 120000);
  h.interval();
  assert.match(h.icon().href, /error\.svg$/);
  h.controller.dispose();
});

test("entering a watched filter from an unrelated view cannot trust that view's old empty DOM", () => {
  const h = harness({ status: "ok", count: 0, message: "" }, "https://github.com/notifications");
  assert.equal(h.reports.length, 0);
  h.window.location.href = core.URLS.mentions;
  h.interval();
  assert.equal(h.reports.at(-1).state.status, "error");
  assert.equal(h.reports.at(-1).state.count, null);
  assert.match(h.icon().href, /error\.svg$/);
  h.controller.dispose();
});

test("hash-only navigation does not invalidate a loaded queue", () => {
  const h = harness({ status: "ok", count: 0, message: "" });
  h.window.location.href = `${core.URLS.reviews}#search-results`;
  h.event("hashchange");
  assert.equal(h.reports.length, 1);
  assert.match(h.icon().href, /clear\.svg$/);
  h.controller.dispose();
});

test("popup treats zero as clear only for current successful checks", () => {
  const now = 1000000;
  assert.equal(describeQueue({ status: "ok", count: 0, checkedAt: now }, now, core.STALE_MS).label, "🟢 Watching — queue empty");
  assert.match(describeQueue({ status: "ok", count: 2, checkedAt: new Date(now).toISOString() }, now, core.STALE_MS).label, /2 items/);
  assert.match(describeQueue({ status: "ok", count: 2, checkedAt: now }, now, core.STALE_MS, "mentions").label, /2 threads shown/);
  for (const queue of [undefined, { status: "error", count: 0, checkedAt: now }, { status: "loading", count: 0 }, { status: "inactive", count: 0 }, { status: "ok", count: 0, checkedAt: now - core.STALE_MS - 1 }, { status: "ok", count: null, checkedAt: now }]) {
    assert.notEqual(describeQueue(queue, now, core.STALE_MS).label, "🟢 Watching — queue empty");
  }
});
