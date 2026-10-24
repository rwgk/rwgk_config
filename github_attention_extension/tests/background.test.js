"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const core = require("../extension/core.js");

const source = fs.readFileSync(path.join(__dirname, "../extension/background.js"), "utf8");
const REVIEW_URL = "https://github.com/notifications?query=reason%3Areview-requested";
const APPROVED_URL = "https://github.com/search?q=is%3Apr+is%3Aopen+author%3Arwgk+review%3Aapproved&type=pullrequests";
const MENTION_URL = "https://github.com/notifications?query=reason%3Amention";
const NOTIFICATIONS_URL = "https://github.com/notifications";
const QUEUE_URLS = { reviews: REVIEW_URL, approved: APPROVED_URL, mentions: MENTION_URL, notifications: NOTIFICATIONS_URL };

function event() {
  const listeners = [];
  return { addListener: (listener) => listeners.push(listener), emit: (...args) => listeners.forEach((listener) => listener(...args)) };
}

function setup(options = {}) {
  const tabs = options.tabs || [];
  const storage = options.storage || {};
  const reloads = [];
  const reloadTimes = [];
  let now = options.now ?? Date.now();
  let nextTimerId = 1;
  const timers = new Map();
  class FakeDate extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  }
  const setTimeout = (callback, delay = 0) => {
    const id = nextTimerId++;
    timers.set(id, { callback, when: now + Math.max(0, delay) });
    return id;
  };
  const clearTimeout = (id) => timers.delete(id);
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
        await options.beforeQuery?.(tabs);
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
        reloadTimes.push(now);
        if (options.reloadFails) throw new Error("closed");
      },
      onUpdated: event(),
      onRemoved: event(),
    },
    storage: { local: {
      get: async () => structuredClone(storage),
      set: async (value) => {
        await options.beforeSave?.(value, storage);
        Object.assign(storage, structuredClone(value));
      },
    } },
    runtime: { onMessage: event() },
  };
  const context = vm.createContext({ chrome, importScripts: () => {}, Date: FakeDate, setTimeout, clearTimeout, Map, Set, Promise, Number, Object });
  context.GitHubAttention = core;
  vm.runInContext(source, context);
  const idle = async () => {
    await vm.runInContext("ready", context);
    let pending;
    do {
      pending = vm.runInContext("operations", context);
      await pending;
    } while (vm.runInContext("operations", context) !== pending);
  };
  const message = async (payload, sender = {}) => {
    await idle();
    return new Promise((resolve) => chrome.runtime.onMessage.emit(payload, sender, resolve));
  };
  const nextTimer = () => [...timers].sort((left, right) => left[1].when - right[1].when || left[0] - right[0])[0];
  const fireNext = async () => {
    const next = nextTimer();
    if (!next || next[1].when > now) return false;
    timers.delete(next[0]);
    next[1].callback();
    await idle();
    return true;
  };
  const clock = {
    now: () => now,
    pending: () => [...timers.values()].map(({ when }) => when).sort((left, right) => left - right),
    jump: (milliseconds) => { now += milliseconds; },
    fireNext,
    advance: async (milliseconds) => {
      const target = now + milliseconds;
      let firings = 0;
      while (nextTimer()?.[1].when <= target) {
        assert.ok(firings++ < 1000, "Timer callbacks must not loop indefinitely");
        now = Math.max(now, nextTimer()[1].when);
        await fireNext();
      }
      now = target;
      await idle();
    },
  };
  return { tabs, storage, reloads, reloadTimes, alarms, alarmCreates, chrome, clock, idle, message };
}

function report(kind, count, status = "ok", message = "") {
  return { type: "attention:report", kind, state: { status, count, message } };
}

async function startAutomatic(app) {
  // A newly started worker waits five seconds before its first automatic
  // reload, even if a periodic alarm happens to arrive immediately.
  await app.clock.advance(5000);
  app.chrome.alarms.onAlarm.emit({ name: "attention:refresh" });
  await app.idle();
}

test("startup recreates the alarm and never reloads an active tab", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: true, status: "complete" }] });
  await app.idle();
  assert.equal(app.alarms.get("attention:refresh").periodInMinutes, 2);
  assert.deepEqual(app.reloads, []);
  assert.equal(app.storage.attentionQueuesV2.reviews.status, "loading");
  assert.equal(app.storage.attentionQueuesV2.approved.status, "inactive");
  assert.equal(app.storage.attentionQueuesV2.mentions.status, "inactive");
  assert.equal(app.storage.attentionQueuesV2.notifications.status, "inactive");
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
    { id: 2, url: APPROVED_URL, active: false, status: "complete" },
    { id: 3, url: MENTION_URL, active: false, status: "complete" },
    { id: 4, url: NOTIFICATIONS_URL, active: false, status: "complete" },
    { id: 5, url: "https://github.com/search?q=other", active: false, status: "complete" },
    { id: 6, url: "https://github.com/notifications?query=reason%3Aparticipating", active: false, status: "complete" },
    { id: 7, url: "https://github.com/notifications?query=is%3Aunread%20reason%3Amention", active: false, status: "complete" },
    { id: 8, url: "https://github.com/search?q=is%3Apr+is%3Aopen+user-review-requested%3A%40me&type=pullrequests", active: false, status: "complete" },
  ] });
  await startAutomatic(app);
  assert.deepEqual(app.reloads, [2]);
  await app.clock.advance(5000);
  assert.deepEqual(app.reloads, [2, 3]);
  await app.clock.advance(5000);
  assert.deepEqual(app.reloads, [2, 3, 4]);
});

test("all four inactive queues refresh without changing their independently verified counts", async () => {
  const tabs = Object.entries(QUEUE_URLS).map(([kind, url], index) => ({ id: index + 1, url, kind, active: false, status: "complete" }));
  const app = setup({ tabs });
  for (const tab of tabs) await app.message(report(tab.kind, tab.id), { tab });
  await startAutomatic(app);
  assert.deepEqual(app.reloads, [1]);
  for (const tab of tabs.slice(1)) assert.equal(app.storage.attentionQueuesV2[tab.kind].status, "ok");
  await app.clock.advance(15000);
  assert.deepEqual(app.reloads, [1, 2, 3, 4]);
  for (const tab of tabs) {
    assert.equal(app.storage.attentionQueuesV2[tab.kind].status, "loading");
    assert.equal(app.storage.attentionQueuesV2[tab.kind].count, tab.id);
  }
});

test("automatic reloads follow queue priority with at least five seconds between actual starts", async () => {
  const tabs = Object.values(QUEUE_URLS).map((url, index) => ({ id: index + 1, url, active: false, status: "complete" }));
  const app = setup({ tabs: [...tabs].reverse() });
  await startAutomatic(app);
  const startedAt = app.clock.now();
  assert.deepEqual(app.reloads, [1]);
  await app.clock.advance(4999);
  assert.deepEqual(app.reloads, [1]);
  await app.clock.advance(1);
  assert.deepEqual(app.reloads, [1, 2]);
  await app.clock.advance(10000);
  assert.deepEqual(app.reloads, [1, 2, 3, 4]);
  assert.deepEqual(app.reloadTimes, [0, 5000, 10000, 15000].map((offset) => startedAt + offset));
});

test("queue reports and popup status remain responsive while another tab waits for its reload slot", async () => {
  const tabs = Object.entries(QUEUE_URLS).map(([kind, url], index) => ({ id: index + 1, url, kind, active: false, status: "complete" }));
  const app = setup({ tabs });
  for (const tab of tabs) await app.message(report(tab.kind, tab.id), { tab });
  const waitingCheckedAt = app.storage.attentionQueuesV2.approved.checkedAt;
  await startAutomatic(app);
  const accepted = await app.message(report("reviews", 7), { tab: tabs[0] });
  const status = await app.message({ type: "attention:status" });
  assert.equal(accepted.accepted, true);
  assert.equal(status.queues.reviews.count, 7);
  assert.equal(status.queues.approved.status, "ok");
  assert.equal(status.queues.approved.count, 2);
  assert.equal(status.queues.approved.checkedAt, waitingCheckedAt);
  assert.deepEqual(app.reloads, [1]);
  await app.clock.advance(5000);
  assert.equal(app.storage.attentionQueuesV2.approved.status, "loading");
  assert.equal(app.storage.attentionQueuesV2.approved.count, 2);
  assert.equal(app.storage.attentionQueuesV2.approved.checkedAt, waitingCheckedAt);
});

test("a delayed timer after sleep does not cause a catch-up burst", async () => {
  const app = setup({ tabs: Object.values(QUEUE_URLS).map((url, index) => ({ id: index + 1, url, active: false, status: "complete" })) });
  await startAutomatic(app);
  const startedAt = app.clock.now();
  app.clock.jump(30000);
  assert.equal(await app.clock.fireNext(), true);
  assert.deepEqual(app.reloads, [1, 2]);
  assert.deepEqual(app.reloadTimes, [startedAt, startedAt + 30000]);
  assert.equal(await app.clock.fireNext(), false);
  await app.clock.advance(4999);
  assert.deepEqual(app.reloads, [1, 2]);
  await app.clock.advance(1);
  assert.deepEqual(app.reloads, [1, 2, 3]);
  assert.equal(app.reloadTimes[2], startedAt + 35000);
});

test("repeated periodic alarms coalesce until the current reload wave finishes", async () => {
  const app = setup({ tabs: Object.values(QUEUE_URLS).map((url, index) => ({ id: index + 1, url, active: false, status: "complete" })) });
  await startAutomatic(app);
  await app.clock.advance(2000);
  app.chrome.alarms.onAlarm.emit({ name: "attention:refresh" });
  app.chrome.alarms.onAlarm.emit({ name: "attention:refresh" });
  await app.idle();
  assert.deepEqual(app.reloads, [1]);
  await app.clock.advance(13000);
  assert.deepEqual(app.reloads, [1, 2, 3, 4]);
  assert.deepEqual(app.clock.pending(), []);
  await app.clock.advance(105000);
  app.chrome.alarms.onAlarm.emit({ name: "attention:refresh" });
  await app.idle();
  assert.deepEqual(app.reloads, [1, 2, 3, 4, 1]);
  await app.clock.advance(15000);
  assert.deepEqual(app.reloads, [1, 2, 3, 4, 1, 2, 3, 4]);
});

test("a worker restart waits five seconds before an automatic reload and abandons its previous wave", async () => {
  const tabs = Object.values(QUEUE_URLS).map((url, index) => ({ id: index + 1, url, active: false, status: "complete" }));
  const first = setup({ tabs });
  await startAutomatic(first);
  const startedAt = first.clock.now();
  assert.deepEqual(first.reloads, [1]);
  const restarted = setup({ tabs, storage: first.storage, now: startedAt + 2000 });
  await restarted.idle();
  assert.deepEqual(restarted.reloads, []);
  assert.deepEqual(restarted.clock.pending(), []);
  restarted.chrome.alarms.onAlarm.emit({ name: "attention:refresh" });
  await restarted.idle();
  assert.deepEqual(restarted.reloads, []);
  await restarted.clock.advance(4999);
  assert.deepEqual(restarted.reloads, []);
  await restarted.clock.advance(1);
  assert.deepEqual(restarted.reloads, [1]);
  assert.equal(restarted.reloadTimes[0], startedAt + 7000);
  await restarted.clock.advance(5000);
  assert.deepEqual(restarted.reloads, [1, 2]);
});

test("Check now cancels pending automatic reloads and refreshes all watched tabs immediately", async () => {
  const tabs = Object.values(QUEUE_URLS).map((url, index) => ({ id: index + 1, url, active: false, status: "complete" }));
  const app = setup({ tabs });
  await startAutomatic(app);
  await app.clock.advance(1000);
  tabs[2].active = true;
  const manualAt = app.clock.now();
  const response = await app.message({ type: "attention:refresh" });
  assert.ok(response.queues);
  assert.deepEqual(app.reloads, [1, 1, 2, 3, 4]);
  assert.deepEqual(app.reloadTimes.slice(1), [manualAt, manualAt, manualAt, manualAt]);
  assert.deepEqual(app.clock.pending(), []);
  app.chrome.alarms.onAlarm.emit({ name: "attention:refresh" });
  await app.idle();
  await app.clock.advance(4999);
  assert.deepEqual(app.reloads, [1, 1, 2, 3, 4]);
  await app.clock.advance(1);
  assert.deepEqual(app.reloads, [1, 1, 2, 3, 4, 1]);
});

test("Check now invalidates an automatic step already queued behind another operation", async () => {
  let holdNextQuery = false;
  let queryHeld;
  let releaseQuery;
  const held = new Promise((resolve) => { queryHeld = resolve; });
  const released = new Promise((resolve) => { releaseQuery = resolve; });
  const app = setup({
    tabs: Object.values(QUEUE_URLS).map((url, index) => ({ id: index + 1, url, active: false, status: "complete" })),
    beforeQuery: async () => {
      if (!holdNextQuery) return;
      holdNextQuery = false;
      queryHeld();
      await released;
    },
  });
  await startAutomatic(app);
  assert.deepEqual(app.reloads, [1]);
  holdNextQuery = true;
  const statusResponse = new Promise((resolve) => app.chrome.runtime.onMessage.emit({ type: "attention:status" }, {}, resolve));
  await held;
  // The status query occupies operations. Queue Check now first, then fire
  // the old timer so its dispatch is already queued when Check now cancels it.
  const manualResponse = new Promise((resolve) => app.chrome.runtime.onMessage.emit({ type: "attention:refresh" }, {}, resolve));
  app.clock.jump(5000);
  const timerSettled = app.clock.fireNext();
  releaseQuery();
  const [status, manual, fired] = await Promise.all([statusResponse, manualResponse, timerSettled]);
  assert.ok(status.queues);
  assert.ok(manual.queues);
  assert.equal(fired, true);
  await app.idle();
  assert.deepEqual(app.reloads, [1, 1, 2, 3, 4]);
  assert.deepEqual(app.clock.pending(), []);
  await app.clock.advance(15000);
  assert.deepEqual(app.reloads, [1, 1, 2, 3, 4]);
});

test("a transient save failure cancels its automatic wave and the next periodic alarm recovers", async () => {
  let failNextSave = false;
  const tabs = Object.entries(QUEUE_URLS).map(([kind, url], index) => ({ id: index + 1, url, kind, active: false, status: "complete" }));
  const app = setup({
    tabs,
    beforeSave: async () => {
      if (!failNextSave) return;
      failNextSave = false;
      throw new Error("Temporary storage failure");
    },
  });
  for (const tab of tabs) await app.message(report(tab.kind, tab.id), { tab });
  await startAutomatic(app);
  assert.deepEqual(app.reloads, [1]);
  failNextSave = true;
  await app.clock.advance(5000);
  assert.deepEqual(app.reloads, [1, 2]);
  assert.deepEqual(app.clock.pending(), []);
  for (const tab of tabs) assert.equal(app.storage.attentionQueuesV2[tab.kind].count, tab.id);
  await app.clock.advance(115000);
  assert.deepEqual(app.reloads, [1, 2]);
  app.chrome.alarms.onAlarm.emit({ name: "attention:refresh" });
  await app.idle();
  assert.deepEqual(app.reloads, [1, 2, 1]);
  await app.clock.advance(15000);
  assert.deepEqual(app.reloads, [1, 2, 1, 2, 3, 4]);
  for (let index = 1; index < app.reloadTimes.length; index++) {
    assert.ok(app.reloadTimes[index] - app.reloadTimes[index - 1] >= 5000);
  }
  for (const tab of tabs) assert.equal(app.storage.attentionQueuesV2[tab.kind].count, tab.id);
});

for (const change of ["active", "loading", "closed", "navigated"]) {
  test(`a waiting tab that becomes ${change} is rechecked and not reloaded`, async () => {
    const tabs = [
      { id: 1, url: REVIEW_URL, active: false, status: "complete" },
      { id: 2, url: APPROVED_URL, active: false, status: "complete" },
      { id: 3, url: MENTION_URL, active: false, status: "complete" },
    ];
    const app = setup({ tabs });
    await app.message(report("approved", 8), { tab: tabs[1] });
    await startAutomatic(app);
    if (change === "active") tabs[1].active = true;
    if (change === "loading") tabs[1].status = "loading";
    if (change === "closed") tabs.splice(1, 1);
    if (change === "navigated") tabs[1].url = "https://github.com/login";
    await app.clock.advance(5000);
    assert.deepEqual(app.reloads, [1, 3]);
    assert.equal(app.storage.attentionQueuesV2.approved.count, 8);
    assert.equal(app.storage.attentionQueuesV2.approved.status, ["active", "loading"].includes(change) ? "ok" : "error");
    assert.equal(app.reloadTimes[1] - app.reloadTimes[0], 5000);
  });
}

test("an alarm immediately after worker startup waits out the initial safety gap", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: false, status: "complete" }] });
  await app.idle();
  app.chrome.alarms.onAlarm.emit({ name: "attention:refresh" });
  await app.idle();
  assert.deepEqual(app.reloads, []);
  await app.clock.advance(4999);
  assert.deepEqual(app.reloads, []);
  await app.clock.advance(1);
  assert.deepEqual(app.reloads, [1]);
});

test("failed reload attempts still consume a stagger slot without losing verified counts", async () => {
  const tabs = Object.entries(QUEUE_URLS).map(([kind, url], index) => ({ id: index + 1, url, kind, active: false, status: "complete" }));
  const app = setup({ tabs, reloadFails: true });
  for (const tab of tabs) await app.message(report(tab.kind, tab.id), { tab });
  await startAutomatic(app);
  assert.deepEqual(app.reloads, [1]);
  await app.clock.advance(4999);
  assert.deepEqual(app.reloads, [1]);
  await app.clock.advance(10001);
  assert.deepEqual(app.reloads, [1, 2, 3, 4]);
  for (let index = 1; index < app.reloadTimes.length; index++) {
    assert.equal(app.reloadTimes[index] - app.reloadTimes[index - 1], 5000);
  }
  for (const tab of tabs) {
    assert.equal(app.storage.attentionQueuesV2[tab.kind].status, "error");
    assert.equal(app.storage.attentionQueuesV2[tab.kind].count, tab.id);
  }
});

test("periodic checks do not reload any active queue or change its verified state", async () => {
  const tabs = Object.entries(QUEUE_URLS).map(([kind, url], index) => ({ id: index + 1, url, kind, active: true, status: "complete" }));
  const app = setup({ tabs });
  for (const tab of tabs) await app.message(report(tab.kind, tab.id), { tab });
  const before = structuredClone(app.storage.attentionQueuesV2);
  app.chrome.alarms.onAlarm.emit({ name: "attention:refresh" });
  await app.idle();
  assert.deepEqual(app.reloads, []);
  assert.deepEqual(app.storage.attentionQueuesV2, before);
});

test("reports and errors affect only their matching queue, not another view of the same inbox", async () => {
  const tabs = Object.entries(QUEUE_URLS).map(([kind, url], index) => ({ id: index + 1, url, kind, active: true, status: "complete" }));
  const app = setup({ tabs });
  for (const tab of tabs) {
    const result = await app.message(report(tab.kind, tab.id), { tab });
    assert.equal(result.accepted, true);
  }
  await app.message(report("reviews", 0), { tab: tabs[0] });
  await app.message(report("mentions", null, "error", "The page could not be checked."), { tab: tabs[2] });
  const result = await app.message({ type: "attention:status" });
  assert.deepEqual(Object.keys(result.queues), ["reviews", "approved", "mentions", "notifications"]);
  assert.equal(result.queues.reviews.count, 0);
  assert.equal(result.queues.approved.count, 2);
  assert.equal(result.queues.mentions.status, "error");
  assert.equal(result.queues.mentions.count, 3);
  assert.equal(result.queues.notifications.count, 4);
});

test("legacy review-search and unread-mention state cannot report a new Done-based queue as empty", async () => {
  const oldQueue = { status: "ok", count: 0, checkedAt: "2026-10-14T18:00:00.000Z", message: "" };
  const legacyState = { reviews: { ...oldQueue }, mentions: { ...oldQueue } };
  const app = setup({
    tabs: Object.values(QUEUE_URLS).map((url, index) => ({ id: index + 1, url, active: true, status: "complete" })),
    storage: { attentionQueues: legacyState },
  });
  await app.idle();
  for (const kind of Object.keys(QUEUE_URLS)) {
    assert.equal(app.storage.attentionQueuesV2[kind].status, "loading");
    assert.equal(app.storage.attentionQueuesV2[kind].count, null);
    assert.equal(app.storage.attentionQueuesV2[kind].checkedAt, null);
  }
  assert.deepEqual(app.storage.attentionQueues, legacyState);
});

test("worker restart restores all four V2 queues independently while ignoring legacy values", async () => {
  const queues = Object.fromEntries(Object.keys(QUEUE_URLS).map((kind, index) => [kind, {
    status: "ok", count: index + 1, checkedAt: "2026-10-24T18:00:00.000Z", message: "",
  }]));
  const app = setup({
    tabs: Object.values(QUEUE_URLS).map((url, index) => ({ id: index + 1, url, active: true, status: "complete" })),
    storage: {
      attentionQueues: { reviews: { ...queues.reviews, count: 0 } },
      attentionQueuesV2: queues,
    },
  });
  await app.idle();
  assert.deepEqual(app.storage.attentionQueuesV2, queues);
  assert.deepEqual(app.reloads, []);
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
  const verifiedAt = app.storage.attentionQueuesV2.reviews.checkedAt;
  await startAutomatic(app);
  assert.deepEqual(app.reloads, []);
  assert.equal(app.storage.attentionQueuesV2.reviews.status, "ok");
  assert.equal(app.storage.attentionQueuesV2.reviews.checkedAt, verifiedAt);
});

test("a tab navigated away after enumeration is never refreshed", async () => {
  const app = setup({
    tabs: [{ id: 1, url: REVIEW_URL, active: false, status: "complete" }],
    beforeGet: (_tabId, tabs) => { tabs[0].url = "https://github.com/login"; },
  });
  await app.message(report("reviews", 2), { tab: app.tabs[0] });
  await startAutomatic(app);
  assert.deepEqual(app.reloads, []);
  assert.equal(app.storage.attentionQueuesV2.reviews.status, "error");
  assert.equal(app.storage.attentionQueuesV2.reviews.count, 2);
});

test("a tab closed after enumeration is handled without any reload or false empty", async () => {
  const app = setup({
    tabs: [{ id: 1, url: REVIEW_URL, active: false, status: "complete" }],
    beforeGet: (_tabId, tabs) => { tabs.splice(0, 1); },
  });
  await app.message(report("reviews", 2), { tab: app.tabs[0] });
  await startAutomatic(app);
  assert.deepEqual(app.reloads, []);
  assert.equal(app.storage.attentionQueuesV2.reviews.status, "error");
  assert.equal(app.storage.attentionQueuesV2.reviews.count, 2);
});

test("a tab that starts loading after enumeration is not restarted", async () => {
  const app = setup({
    tabs: [{ id: 1, url: REVIEW_URL, active: false, status: "complete" }],
    beforeGet: (_tabId, tabs) => { tabs[0].status = "loading"; },
  });
  await app.message(report("reviews", 2), { tab: app.tabs[0] });
  await startAutomatic(app);
  assert.deepEqual(app.reloads, []);
  assert.equal(app.storage.attentionQueuesV2.reviews.count, 2);
});

test("valid queue reports store only minimal aggregate fields", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: true }] });
  const response = await app.message(report("reviews", 3), { tab: app.tabs[0] });
  assert.equal(response.accepted, true);
  const queue = app.storage.attentionQueuesV2.reviews;
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
  const verifiedAt = app.storage.attentionQueuesV2.reviews.checkedAt;
  await app.message(report("reviews", null, "error", "Layout changed"), { tab: app.tabs[0] });
  assert.equal(app.storage.attentionQueuesV2.reviews.status, "error");
  assert.equal(app.storage.attentionQueuesV2.reviews.count, 3);
  assert.equal(app.storage.attentionQueuesV2.reviews.checkedAt, verifiedAt);
});

test("loading reports retain a previously verified count without changing its timestamp", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: true }] });
  await app.message(report("reviews", 3), { tab: app.tabs[0] });
  const verifiedAt = app.storage.attentionQueuesV2.reviews.checkedAt;
  await app.message(report("reviews", null, "loading"), { tab: app.tabs[0] });
  assert.equal(app.storage.attentionQueuesV2.reviews.status, "loading");
  assert.equal(app.storage.attentionQueuesV2.reviews.count, 3);
  assert.equal(app.storage.attentionQueuesV2.reviews.checkedAt, verifiedAt);
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
  assert.equal(app.storage.attentionQueuesV2.reviews.count, null);
});

test("a page-check timeout is error, never an empty result", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: true }] });
  await app.message(report("reviews", 2), { tab: app.tabs[0] });
  app.chrome.tabs.onUpdated.emit(1, { status: "loading" }, app.tabs[0]);
  await app.idle();
  app.chrome.alarms.onAlarm.emit({ name: "attention:timeout" });
  await app.idle();
  assert.equal(app.storage.attentionQueuesV2.reviews.status, "error");
  assert.equal(app.storage.attentionQueuesV2.reviews.count, 2);
});

test("a successful report is not replaced by a later timeout alarm", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: true }] });
  await app.message(report("reviews", 0), { tab: app.tabs[0] });
  app.chrome.alarms.onAlarm.emit({ name: "attention:timeout" });
  await app.idle();
  assert.equal(app.storage.attentionQueuesV2.reviews.status, "ok");
  assert.equal(app.storage.attentionQueuesV2.reviews.count, 0);
});

test("closing the last watched tab makes its count inactive rather than verified empty", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: true }] });
  await app.message(report("reviews", 2), { tab: app.tabs[0] });
  app.tabs.splice(0, 1);
  app.chrome.tabs.onRemoved.emit(1);
  await app.idle();
  assert.equal(app.storage.attentionQueuesV2.reviews.status, "inactive");
  assert.equal(app.storage.attentionQueuesV2.reviews.count, 2);
});

test("a GitHub sign-in redirect is marked as an error", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: true }] });
  await app.message(report("reviews", 2), { tab: app.tabs[0] });
  app.tabs[0].url = "https://github.com/login";
  app.chrome.tabs.onUpdated.emit(1, { url: app.tabs[0].url }, app.tabs[0]);
  await app.idle();
  assert.equal(app.storage.attentionQueuesV2.reviews.status, "error");
  assert.equal(app.storage.attentionQueuesV2.reviews.count, 2);
});

test("worker restart preserves verified count while recognizing an interrupted check", async () => {
  const app = setup({
    tabs: [{ id: 1, url: REVIEW_URL, active: true }],
    storage: { attentionQueuesV2: { reviews: {
      status: "loading", count: 4, checkedAt: "2026-10-14T18:00:00.000Z", message: "Checking…",
    } } },
  });
  await app.idle();
  assert.equal(app.storage.attentionQueuesV2.reviews.status, "error");
  assert.equal(app.storage.attentionQueuesV2.reviews.count, 4);
});

test("reload errors preserve the last known count", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: false }], reloadFails: true });
  await app.message(report("reviews", 2), { tab: app.tabs[0] });
  await startAutomatic(app);
  assert.equal(app.storage.attentionQueuesV2.reviews.status, "error");
  assert.equal(app.storage.attentionQueuesV2.reviews.count, 2);
});

test("already loading tabs are not repeatedly restarted by an alarm", async () => {
  const app = setup({ tabs: [{ id: 1, url: REVIEW_URL, active: false, status: "loading" }] });
  await startAutomatic(app);
  assert.deepEqual(app.reloads, []);
});
