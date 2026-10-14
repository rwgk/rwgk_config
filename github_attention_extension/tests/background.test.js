"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../extension/background.js"), "utf8");
const REVIEW_URL = "https://github.com/search?q=is%3Apr+is%3Aopen+user-review-requested%3A%40me&type=pullrequests";
const MENTION_URL = "https://github.com/notifications?query=is%3Aunread%20reason%3Amention";

function event() {
  const listeners = [];
  return { addListener: (listener) => listeners.push(listener), emit: (...args) => listeners.forEach((listener) => listener(...args)) };
}

function setup(options = {}) {
  const tabs = options.tabs || [];
  const storage = options.storage || {};
  const reloads = [];
  const alarms = new Map(options.alarms || []);
  const alarmCreates = [];
  const chrome = {
    alarms: {
      get: async (name) => alarms.get(name),
      create: async (name, value) => { alarmCreates.push(name); alarms.set(name, value); },
      onAlarm: event(),
    },
    tabs: {
      query: async () => {
        if (options.queryFails) throw new Error("offline");
        return structuredClone(tabs);
      },
      get: async (tabId) => {
        options.beforeGet?.(tabId, tabs);
        const tab = tabs.find((candidate) => candidate.id === tabId);
        if (!tab) throw new Error("closed");
        return structuredClone(tab);
      },
      reload: async (tabId) => {
        reloads.push(tabId);
        if (options.reloadFails) throw new Error("closed");
      },
      onUpdated: event(),
      onRemoved: event(),
    },
    storage: { local: {
      get: async () => structuredClone(storage),
      set: async (value) => Object.assign(storage, structuredClone(value)),
    } },
    runtime: { onMessage: event() },
  };
  const context = vm.createContext({ chrome, importScripts: () => {}, Date, Map, Set, Promise, Number, Object });
  context.GitHubAttention = {
    KINDS: ["reviews", "mentions"], REFRESH_MINUTES: 2,
    classifyUrl: (url) => url === REVIEW_URL ? "reviews" : url === MENTION_URL ? "mentions" : null,
  };
  vm.runInContext(source, context);
  const idle = async () => {
    await vm.runInContext("ready", context);
    await vm.runInContext("operations", context);
  };
  const message = async (payload, sender = {}) => {
    await idle();
    return new Promise((resolve) => chrome.runtime.onMessage.emit(payload, sender, resolve));
  };
  return { tabs, storage, reloads, alarms, alarmCreates, chrome, idle, message };
}

function report(kind, count, status = "ok", message = "") {
  return { type: "attention:report", kind, state: { status, count, message } };
}

test("startup recreates the alarm and never reloads an active tab", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: true, status: "complete" }] });
  await app.idle();
  assert.equal(app.alarms.get("attention:refresh").periodInMinutes, 2);
  assert.deepEqual(app.reloads, []);
  assert.equal(app.storage.attentionQueues.reviews.status, "loading");
  assert.equal(app.storage.attentionQueues.mentions.status, "inactive");
});

test("worker wake preserves an existing periodic alarm rather than postponing it", async () => {
  const existing = { periodInMinutes: 2, scheduledTime: Date.now() + 10_000 };
  const app = setup({ alarms: [["attention:refresh", existing]] });
  await app.idle();
  assert.equal(app.alarms.get("attention:refresh"), existing);
  assert.equal(app.alarmCreates.includes("attention:refresh"), false);
});

test("periodic checks refresh only inactive exact watched views", async () => {
  const app = setup({ tabs: [
    { id: 1, url: REVIEW_URL, active: true, status: "complete" },
    { id: 2, url: MENTION_URL, active: false, status: "complete" },
    { id: 3, url: "https://github.com/search?q=other", active: false, status: "complete" },
  ] });
  await app.idle();
  app.chrome.alarms.onAlarm.emit({ name: "attention:refresh" });
  await app.idle();
  assert.deepEqual(app.reloads, [2]);
});

test("explicit Check now may refresh an active watched tab", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: true, status: "complete" }] });
  await app.message({ type: "attention:refresh" });
  assert.deepEqual(app.reloads, [1]);
});

test("a tab activated after enumeration is not automatically refreshed or marked loading", async () => {
  const app = setup({
    tabs: [{ id: 1, url: REVIEW_URL, active: false, status: "complete" }],
    beforeGet: (_tabId, tabs) => { tabs[0].active = true; },
  });
  await app.message(report("reviews", 2), { tab: app.tabs[0] });
  const verifiedAt = app.storage.attentionQueues.reviews.checkedAt;
  app.chrome.alarms.onAlarm.emit({ name: "attention:refresh" });
  await app.idle();
  assert.deepEqual(app.reloads, []);
  assert.equal(app.storage.attentionQueues.reviews.status, "ok");
  assert.equal(app.storage.attentionQueues.reviews.checkedAt, verifiedAt);
});

test("a tab navigated away after enumeration is never refreshed", async () => {
  const app = setup({
    tabs: [{ id: 1, url: REVIEW_URL, active: false, status: "complete" }],
    beforeGet: (_tabId, tabs) => { tabs[0].url = "https://github.com/login"; },
  });
  await app.message(report("reviews", 2), { tab: app.tabs[0] });
  app.chrome.alarms.onAlarm.emit({ name: "attention:refresh" });
  await app.idle();
  assert.deepEqual(app.reloads, []);
  assert.equal(app.storage.attentionQueues.reviews.status, "error");
  assert.equal(app.storage.attentionQueues.reviews.count, 2);
});

test("a tab closed after enumeration is handled without any reload or false empty", async () => {
  const app = setup({
    tabs: [{ id: 1, url: REVIEW_URL, active: false, status: "complete" }],
    beforeGet: (_tabId, tabs) => { tabs.splice(0, 1); },
  });
  await app.message(report("reviews", 2), { tab: app.tabs[0] });
  app.chrome.alarms.onAlarm.emit({ name: "attention:refresh" });
  await app.idle();
  assert.deepEqual(app.reloads, []);
  assert.equal(app.storage.attentionQueues.reviews.status, "error");
  assert.equal(app.storage.attentionQueues.reviews.count, 2);
});

test("a tab that starts loading after enumeration is not restarted", async () => {
  const app = setup({
    tabs: [{ id: 1, url: REVIEW_URL, active: false, status: "complete" }],
    beforeGet: (_tabId, tabs) => { tabs[0].status = "loading"; },
  });
  await app.message(report("reviews", 2), { tab: app.tabs[0] });
  app.chrome.alarms.onAlarm.emit({ name: "attention:refresh" });
  await app.idle();
  assert.deepEqual(app.reloads, []);
  assert.equal(app.storage.attentionQueues.reviews.count, 2);
});

test("valid queue reports store only minimal aggregate fields", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: true }] });
  const response = await app.message(report("reviews", 3), { tab: app.tabs[0] });
  assert.equal(response.accepted, true);
  const queue = app.storage.attentionQueues.reviews;
  assert.equal(queue.status, "ok");
  assert.equal(queue.count, 3);
  assert.ok(Number.isFinite(Date.parse(queue.checkedAt)));
  assert.deepEqual(Object.keys(queue).sort(), ["checkedAt", "count", "message", "status"]);
  const result = await app.message({ type: "attention:status" });
  assert.equal(result.queues.reviews.count, 3);
});

test("failed reports retain the last known nonempty count and successful timestamp", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: true }] });
  await app.message(report("reviews", 3), { tab: app.tabs[0] });
  const verifiedAt = app.storage.attentionQueues.reviews.checkedAt;
  await app.message(report("reviews", null, "error", "Layout changed"), { tab: app.tabs[0] });
  assert.equal(app.storage.attentionQueues.reviews.status, "error");
  assert.equal(app.storage.attentionQueues.reviews.count, 3);
  assert.equal(app.storage.attentionQueues.reviews.checkedAt, verifiedAt);
});

test("loading reports retain a previously verified count without changing its timestamp", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: true }] });
  await app.message(report("reviews", 3), { tab: app.tabs[0] });
  const verifiedAt = app.storage.attentionQueues.reviews.checkedAt;
  await app.message(report("reviews", null, "loading"), { tab: app.tabs[0] });
  assert.equal(app.storage.attentionQueues.reviews.status, "loading");
  assert.equal(app.storage.attentionQueues.reviews.count, 3);
  assert.equal(app.storage.attentionQueues.reviews.checkedAt, verifiedAt);
});

test("reports from unrelated pages, mismatched views, or malformed counts are rejected", async () => {
  const app = setup();
  for (const [payload, sender] of [
    [report("reviews", 0), { tab: { id: 1, url: "https://example.com/" } }],
    [report("mentions", 0), { tab: { id: 1, url: REVIEW_URL } }],
    [report("reviews", -1), { tab: { id: 1, url: REVIEW_URL } }],
    [report("reviews", null), { tab: { id: 1, url: REVIEW_URL } }],
    [report("reviews", "0"), { tab: { id: 1, url: REVIEW_URL } }],
  ]) {
    const result = await app.message(payload, sender);
    assert.match(result.error, /invalid/);
  }
  assert.equal(app.storage.attentionQueues.reviews.count, null);
});

test("a page-check timeout is error, never an empty result", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: true }] });
  await app.message(report("reviews", 2), { tab: app.tabs[0] });
  app.chrome.tabs.onUpdated.emit(1, { status: "loading" }, app.tabs[0]);
  await app.idle();
  app.chrome.alarms.onAlarm.emit({ name: "attention:timeout" });
  await app.idle();
  assert.equal(app.storage.attentionQueues.reviews.status, "error");
  assert.equal(app.storage.attentionQueues.reviews.count, 2);
});

test("a successful report is not replaced by a later timeout alarm", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: true }] });
  await app.message(report("reviews", 0), { tab: app.tabs[0] });
  app.chrome.alarms.onAlarm.emit({ name: "attention:timeout" });
  await app.idle();
  assert.equal(app.storage.attentionQueues.reviews.status, "ok");
  assert.equal(app.storage.attentionQueues.reviews.count, 0);
});

test("closing the last watched tab makes its count inactive rather than verified empty", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: true }] });
  await app.message(report("reviews", 2), { tab: app.tabs[0] });
  app.tabs.splice(0, 1);
  app.chrome.tabs.onRemoved.emit(1);
  await app.idle();
  assert.equal(app.storage.attentionQueues.reviews.status, "inactive");
  assert.equal(app.storage.attentionQueues.reviews.count, 2);
});

test("a GitHub sign-in redirect is marked as an error", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: true }] });
  await app.message(report("reviews", 2), { tab: app.tabs[0] });
  app.tabs[0].url = "https://github.com/login";
  app.chrome.tabs.onUpdated.emit(1, { url: app.tabs[0].url }, app.tabs[0]);
  await app.idle();
  assert.equal(app.storage.attentionQueues.reviews.status, "error");
  assert.equal(app.storage.attentionQueues.reviews.count, 2);
});

test("worker restart preserves verified count while recognizing an interrupted check", async () => {
  const app = setup({
    tabs: [{ id: 1, url: REVIEW_URL, active: true }],
    storage: { attentionQueues: { reviews: {
      status: "loading", count: 4, checkedAt: "2026-10-14T18:00:00.000Z", message: "Checking…",
    } } },
  });
  await app.idle();
  assert.equal(app.storage.attentionQueues.reviews.status, "error");
  assert.equal(app.storage.attentionQueues.reviews.count, 4);
});

test("reload errors preserve the last known count", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: false }], reloadFails: true });
  await app.message(report("reviews", 2), { tab: app.tabs[0] });
  app.chrome.alarms.onAlarm.emit({ name: "attention:refresh" });
  await app.idle();
  assert.equal(app.storage.attentionQueues.reviews.status, "error");
  assert.equal(app.storage.attentionQueues.reviews.count, 2);
});

test("already loading tabs are not repeatedly restarted by an alarm", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: false, status: "loading" }] });
  await app.idle();
  app.chrome.alarms.onAlarm.emit({ name: "attention:refresh" });
  await app.idle();
  assert.deepEqual(app.reloads, []);
});
