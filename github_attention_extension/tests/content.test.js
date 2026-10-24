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

test("content shows a red count for nonempty and green for verified zero without changing site icons, title, or body", () => {
  const h = harness();
  assert.match(h.icon().href, /count-3\.svg$/);
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

for (const count of [...Array.from({ length: 11 }, (_, i) => i), 99, Number.MAX_SAFE_INTEGER]) {
  test(`verified count ${count} uses the correct numbered favicon`, () => {
    const h = harness({ status: "ok", count, message: "" });
    const icon = count === 0 ? "clear" : `count-${count < 10 ? count : "x"}`;
    assert.equal(h.icon().href, `chrome-extension://test/icons/${icon}.svg`);
    h.controller.dispose();
  });
}

test("count changes update the favicon across one-digit, ten-plus, and zero boundaries", () => {
  const h = harness({ status: "ok", count: 8, message: "" });
  for (const count of [9, 10, 11, 1, 0]) {
    h.setState({ status: "ok", count, message: "" });
    h.controller.check();
    const icon = count === 0 ? "clear" : `count-${count < 10 ? count : "x"}`;
    assert.equal(h.icon().href, `chrome-extension://test/icons/${icon}.svg`);
    assert.equal(h.reports.at(-1).state.count, count);
  }
  h.controller.dispose();
});

test("all four watched queues independently display their own counts", () => {
  const kinds = ["reviews", "approved", "mentions", "notifications"];
  assert.deepEqual(core.KINDS, kinds);
  const queues = kinds.map((kind, i) => harness({ status: "ok", count: i + 1, message: "" }, core.URLS[kind]));
  for (const [i, h] of queues.entries()) {
    assert.equal(h.reports.at(-1).kind, kinds[i]);
    assert.equal(h.icon().href, `chrome-extension://test/icons/count-${i + 1}.svg`);
  }
  queues[0].setState({ status: "ok", count: 0, message: "" });
  queues[0].controller.check();
  assert.match(queues[0].icon().href, /clear\.svg$/);
  for (const [i, h] of queues.entries()) {
    if (i) assert.equal(h.icon().href, `chrome-extension://test/icons/count-${i + 1}.svg`);
    h.controller.dispose();
  }
});

test("empty, nonempty, and empty transitions stay visible, but a stale empty queue becomes amber", () => {
  const h = harness({ status: "ok", count: 0, message: "" });
  assert.match(h.icon().href, /clear\.svg$/);
  h.setState({ status: "ok", count: 2, message: "" });
  h.controller.check();
  assert.match(h.icon().href, /count-2\.svg$/);
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
  assert.match(h.icon().href, /count-1\.svg$/);
  assert.equal(h.reports.at(-1).state.status, "ok");
  h.controller.dispose();
});

for (const count of [0, 3]) {
  test(`leaving the strict watched URL clears the ${count ? "red" : "green"} override and prevents further reports`, () => {
    const h = harness({ status: "ok", count, message: "" });
    assert.match(h.icon().href, count ? /count-3\.svg$/ : /clear\.svg$/);
    h.window.location.href = "https://github.com/notifications?query=reason%3Aparticipating";
    h.interval();
    assert.equal(h.icon(), null);
    assert.equal(h.document.querySelector('link[rel="icon"]').href, "original.svg");
    assert.equal(h.reports.length, 1);
    h.controller.check();
    assert.equal(h.reports.length, 1);
    h.controller.dispose();
  });
}

for (const count of [0, 4, 12]) {
  test(`an error retaining count ${count} is amber during loading and after the grace period`, () => {
    const h = harness({ status: "error", count, message: "Unverified queue" });
    assert.match(h.icon().href, /error\.svg$/);
    assert.equal(h.reports.at(-1).state.status, "loading");
    h.advance(15001);
    h.runTimers();
    assert.match(h.icon().href, /error\.svg$/);
    assert.equal(h.reports.at(-1).state.status, "error");
    h.controller.dispose();
  });
}

for (const count of [null, -1, 1.5, "3", Number.MAX_SAFE_INTEGER + 1]) {
  test(`an invalid successful count ${String(count)} cannot display a clear or numbered favicon`, () => {
    const h = harness({ status: "ok", count, message: "" });
    assert.match(h.icon().href, /error\.svg$/);
    h.controller.dispose();
  });
}

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
  const h = harness({ status: "ok", count: 0, message: "" }, "https://github.com/notifications?query=reason%3Aparticipating");
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
  assert.match(describeQueue({ status: "ok", count: 2, checkedAt: new Date(now).toISOString() }, now, core.STALE_MS, "approved").label, /2 PRs/);
  for (const kind of ["reviews", "mentions", "notifications"]) {
    assert.match(describeQueue({ status: "ok", count: 2, checkedAt: now }, now, core.STALE_MS, kind).label, /2 threads shown/);
  }
  for (const queue of [undefined, { status: "error", count: 0, checkedAt: now }, { status: "loading", count: 0 }, { status: "inactive", count: 0 }, { status: "ok", count: 0, checkedAt: now - core.STALE_MS - 1 }, { status: "ok", count: null, checkedAt: now }]) {
    assert.notEqual(describeQueue(queue, now, core.STALE_MS).label, "🟢 Watching — queue empty");
  }
});
