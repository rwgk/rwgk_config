"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { parseHTML } = require("linkedom");
const { createController } = require("../extension/content.js");
const { describeQueue } = require("../extension/popup.js");
const core = require("../extension/core.js");

function assertIcon(h, state, kind = "reviews") {
  assert.equal(h.icon().href, `chrome-extension://test/icons/${kind}-${state}.svg`);
}

function harness(initialState = { status: "ok", count: 3, message: "" }, initialUrl = core.URLS.reviews) {
  const { document } = parseHTML('<!doctype html><html><head><title>GitHub original title</title><link rel="icon" href="original.svg"></head><body><main></main></body></html>');
  let timestamp = 1000;
  let state = initialState;
  let observerCallback;
  let intervalCallback;
  const timers = new Map();
  const reports = [];
  const listeners = new Map();
  const documentListeners = new Map();
  let messageListener = null;
  const addDocumentListener = document.addEventListener.bind(document);
  const removeDocumentListener = document.removeEventListener.bind(document);
  document.addEventListener = (event, callback, capture) => {
    documentListeners.set(event, { callback, capture });
    addDocumentListener(event, callback, capture);
  };
  document.removeEventListener = (event, callback, capture) => {
    if (documentListeners.get(event)?.callback === callback) documentListeners.delete(event);
    removeDocumentListener(event, callback, capture);
  };
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
    onMessage: {
      addListener(callback) { messageListener = callback; },
      removeListener(callback) { if (messageListener === callback) messageListener = null; },
    },
  } };
  const controller = createController(document, window, chrome, {
    ...core, readDocument: () => ({ ...state }),
  });
  return {
    document, window, reports, timers, controller, documentListeners,
    setState(next) { state = next; },
    advance(ms) { timestamp += ms; },
    interval() { intervalCallback(); },
    event(name) { listeners.get(name)(); },
    // DOM-created events cannot set isTrusted. Invoke only the captured
    // document listener with a browser-like event to exercise both paths.
    documentEvent(name, event = { isTrusted: true, button: 0 }) {
      documentListeners.get(name)?.callback(event);
    },
    message(message) {
      let response;
      const returned = messageListener?.(message, {}, (value) => { response = value; });
      return { response, returned };
    },
    mutation(mutations) { observerCallback(mutations); },
    runTimers() {
      for (const [id, timer] of [...timers]) {
        if (timer.at <= timestamp) { timers.delete(id); timer.callback(); }
      }
    },
    icon() { return document.getElementById("github-attention-favicon"); },
  };
}

test("content shows a queue-specific red badge for nonempty and green for verified zero without changing site icons, title, or body", () => {
  const h = harness();
  assertIcon(h, "count-3");
  assert.equal(h.reports[0].state.count, 3);
  assert.equal(h.document.title, "GitHub original title");
  assert.equal(h.document.body.innerHTML, "<main></main>");
  h.setState({ status: "ok", count: 0, message: "" });
  h.controller.check();
  assertIcon(h, "clear");
  assert.equal(h.document.querySelector('link[rel="icon"]').href, "original.svg");
  h.controller.dispose();
  assert.equal(h.icon(), null);
  assert.equal(h.document.querySelector('link[rel="icon"]').href, "original.svg");
});

for (const kind of core.KINDS) {
  for (const count of [...Array.from({ length: 11 }, (_, i) => i), 99, Number.MAX_SAFE_INTEGER]) {
    test(`${kind}: verified count ${count} uses the correct queue-specific badge`, () => {
      const h = harness({ status: "ok", count, message: "" }, core.URLS[kind]);
      const icon = count === 0 ? "clear" : `count-${count < 10 ? count : "x"}`;
      assertIcon(h, icon, kind);
      h.controller.dispose();
    });
  }

  test(`${kind}: count transitions preserve the queue identity across one-digit, ten-plus, and zero boundaries`, () => {
    const h = harness({ status: "ok", count: 8, message: "" }, core.URLS[kind]);
    assertIcon(h, "count-8", kind);
    for (const count of [9, 10, 11, 1, 0]) {
      h.setState({ status: "ok", count, message: "" });
      h.controller.check();
      const icon = count === 0 ? "clear" : `count-${count < 10 ? count : "x"}`;
      assertIcon(h, icon, kind);
      assert.equal(h.reports.at(-1).state.count, count);
      assert.equal(h.reports.at(-1).kind, kind);
    }
    h.controller.dispose();
  });
}

test("all four watched queues independently display their own counts", () => {
  const kinds = ["reviews", "approved", "mentions", "notifications"];
  assert.deepEqual(core.KINDS, kinds);
  const queues = kinds.map((kind, i) => harness({ status: "ok", count: i + 1, message: "" }, core.URLS[kind]));
  for (const [i, h] of queues.entries()) {
    assert.equal(h.reports.at(-1).kind, kinds[i]);
    assertIcon(h, `count-${i + 1}`, kinds[i]);
  }
  queues[0].setState({ status: "ok", count: 0, message: "" });
  queues[0].controller.check();
  assertIcon(queues[0], "clear");
  for (const [i, h] of queues.entries()) {
    if (i) assertIcon(h, `count-${i + 1}`, kinds[i]);
    h.controller.dispose();
  }
});

for (const kind of core.KINDS) {
  for (const count of [0, 1, 9, 10]) {
    test(`${kind}: stale count ${count} becomes amber without losing queue identity, then recovers`, () => {
      const h = harness({ status: "ok", count, message: "" }, core.URLS[kind]);
      assertIcon(h, count === 0 ? "clear" : `count-${count < 10 ? count : "x"}`, kind);
      h.advance(core.STALE_MS);
      h.interval();
      assertIcon(h, "error", kind);
      assert.equal(h.reports.at(-1).state.status, "error");
      assert.match(h.reports.at(-1).state.message, /stale/i);
      h.setState({ status: "ok", count: count + 1, message: "" });
      h.controller.check();
      assertIcon(h, `count-${count + 1 < 10 ? count + 1 : "x"}`, kind);
      assert.equal(h.reports.at(-1).kind, kind);
      h.controller.dispose();
    });
  }

  test(`${kind}: unknown page is amber during loading grace and final error, then recovers with its own badge`, () => {
    const h = harness({ status: "error", count: null, message: "Unknown layout" }, core.URLS[kind]);
    assertIcon(h, "error", kind);
    assert.equal(h.reports[0].state.status, "loading");
    h.advance(15001);
    h.runTimers();
    assertIcon(h, "error", kind);
    assert.equal(h.reports.at(-1).state.status, "error");
    assert.equal(h.reports.at(-1).state.message, "Unknown layout");
    h.setState({ status: "ok", count: 0, message: "" });
    h.controller.check();
    assertIcon(h, "clear", kind);
    assert.equal(h.reports.at(-1).kind, kind);
    h.controller.dispose();
  });
}

test("identical DOM rereads neither report nor postpone stale indication", () => {
  const h = harness({ status: "ok", count: 0, message: "" });
  h.advance(120000);
  h.controller.check();
  assert.equal(h.reports.length, 1);
  h.advance(core.STALE_MS - 120000);
  h.interval();
  assertIcon(h, "error");
  assert.match(h.reports.at(-1).state.message, /stale/i);
  h.setState({ status: "ok", count: 1, message: "" });
  h.controller.check();
  assertIcon(h, "count-1");
  assert.equal(h.reports.at(-1).state.status, "ok");
  h.controller.dispose();
});

for (const count of [0, 3]) {
  test(`leaving the strict watched URL clears the ${count ? "red" : "green"} override and prevents further reports`, () => {
    const h = harness({ status: "ok", count, message: "" });
    assertIcon(h, count ? "count-3" : "clear");
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
    assertIcon(h, "error");
    assert.equal(h.reports.at(-1).state.status, "loading");
    h.advance(15001);
    h.runTimers();
    assertIcon(h, "error");
    assert.equal(h.reports.at(-1).state.status, "error");
    h.controller.dispose();
  });
}

for (const count of [null, -1, 1.5, "3", Number.MAX_SAFE_INTEGER + 1]) {
  test(`an invalid successful count ${String(count)} cannot display a clear or numbered favicon`, () => {
    const h = harness({ status: "ok", count, message: "" });
    assertIcon(h, "error");
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
  assertIcon(h, "error");
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
  assertIcon(h, "error");
  h.controller.dispose();
});

test("entering a watched filter from an unrelated view cannot trust that view's old empty DOM", () => {
  const h = harness({ status: "ok", count: 0, message: "" }, "https://github.com/notifications?query=reason%3Aparticipating");
  assert.equal(h.reports.length, 0);
  h.window.location.href = core.URLS.mentions;
  h.interval();
  assert.equal(h.reports.at(-1).state.status, "error");
  assert.equal(h.reports.at(-1).state.count, null);
  assertIcon(h, "error", "mentions");
  h.controller.dispose();
});

test("hash-only navigation does not invalidate a loaded queue", () => {
  const h = harness({ status: "ok", count: 0, message: "" });
  h.window.location.href = `${core.URLS.reviews}#search-results`;
  h.event("hashchange");
  assert.equal(h.reports.length, 1);
  assertIcon(h, "clear");
  h.controller.dispose();
});

for (const kind of core.KINDS) {
  test(`${kind}: trusted presses of every mouse button report activity without reporting click details`, () => {
    const h = harness({ status: "ok", count: 0, message: "" }, core.URLS[kind]);
    assert.equal(h.documentListeners.get("mousedown").capture, true);
    assert.deepEqual(h.message({ type: "attention:activity-state", kind }), {
      response: { kind, lastClickAt: null, observedAt: 1000 }, returned: undefined,
    });
    for (const button of [0, 1, 2]) {
      h.advance(100);
      h.documentEvent("mousedown", { isTrusted: true, button, target: { privateDetails: "not recorded" } });
      const timestamp = 1100 + button * 100;
      assert.deepEqual(h.reports.at(-1), { type: "attention:activity", kind, lastClickAt: timestamp });
      assert.deepEqual(h.message({ type: "attention:activity-state", kind }), {
        response: { kind, lastClickAt: timestamp, observedAt: timestamp }, returned: undefined,
      });
    }
    assert.equal(h.reports.filter((report) => report.type === "attention:report").length, 1);
    assertIcon(h, "clear", kind);
    h.controller.dispose();
  });
}

test("untrusted presses, scrolling, keyboard activity, and mouse movement do not count as clicks", () => {
  const h = harness();
  h.advance(10000);
  h.documentEvent("mousedown", { isTrusted: false, button: 0 });
  for (const event of ["click", "wheel", "scroll", "keydown", "mousemove", "pointermove"]) {
    h.documentEvent(event, { isTrusted: true });
  }
  assert.equal(h.reports.length, 1);
  assert.deepEqual(h.message({ type: "attention:activity-state", kind: "reviews" }).response,
    { kind: "reviews", lastClickAt: null, observedAt: 11000 });
  h.controller.dispose();
});

test("click activity never freshens a verified queue or clears its stale indication", () => {
  const h = harness({ status: "ok", count: 0, message: "" });
  h.advance(core.STALE_MS - 1);
  h.documentEvent("mousedown");
  h.advance(1);
  h.interval();
  assertIcon(h, "error");
  assert.match(h.reports.at(-1).state.message, /stale/i);
  h.advance(1000);
  h.documentEvent("mousedown");
  h.controller.check();
  assertIcon(h, "error");
  assert.equal(h.reports.filter((report) => report.type === "attention:report").length, 2);
  h.controller.dispose();
});

test("checks and fresh documents do not invent click activity", () => {
  const h = harness();
  h.documentEvent("mousedown");
  h.advance(1000);
  h.message({ type: "attention:check" });
  assert.equal(h.message({ type: "attention:activity-state", kind: "reviews" }).response.lastClickAt, 1000);
  h.controller.dispose();
  const reloaded = harness();
  assert.equal(reloaded.message({ type: "attention:activity-state", kind: "reviews" }).response.lastClickAt, null);
  reloaded.controller.dispose();
});

test("activity queries use the live URL and ignore unwatched or mismatched queue requests", () => {
  const h = harness();
  assert.equal(h.message({ type: "attention:activity-state", kind: "mentions" }).response, null);
  h.window.location.href = core.URLS.mentions;
  // Neither a route timer nor a queue check must run before this reply.
  assert.equal(h.message({ type: "attention:activity-state", kind: "reviews" }).response, null);
  assert.deepEqual(h.message({ type: "attention:activity-state", kind: "mentions" }).response,
    { kind: "mentions", lastClickAt: null, observedAt: 1000 });
  h.advance(1000);
  h.documentEvent("mousedown");
  assert.deepEqual(h.reports.at(-1), { type: "attention:activity", kind: "mentions", lastClickAt: 2000 });
  h.window.location.href = "https://github.com/notifications?query=reason%3Aparticipating";
  const count = h.reports.length;
  h.advance(1000);
  h.documentEvent("mousedown");
  assert.equal(h.reports.length, count);
  assert.equal(h.message({ type: "attention:activity-state", kind: "mentions" }).response, null);
  h.controller.dispose();
});

test("disposing the controller removes the captured click and message listeners", () => {
  const h = harness();
  const capturedClick = h.documentListeners.get("mousedown").callback;
  h.controller.dispose();
  assert.equal(h.documentListeners.has("mousedown"), false);
  h.documentEvent("mousedown");
  capturedClick({ isTrusted: true, button: 0 });
  assert.equal(h.reports.length, 1);
  assert.deepEqual(h.message({ type: "attention:activity-state", kind: "reviews" }),
    { response: undefined, returned: undefined });
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
