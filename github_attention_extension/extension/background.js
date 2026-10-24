/* global chrome, importScripts */
"use strict";

importScripts("core.js");

const { KINDS, classifyUrl, REFRESH_MINUTES, ACTIVE_IDLE_MS } = globalThis.GitHubAttention;
const REFRESH_ALARM = "attention:refresh";
const TIMEOUT_ALARM = "attention:timeout";
// v0.1 watched current review requests and unread mentions. Those counts must
// never be reused as verified results for the new Done-based inbox views.
const STORAGE_KEY = "attentionQueuesV2";
const CHECK_TIMEOUT_MS = 60_000;
const RELOAD_GAP_MS = 5_000;
const ACTIVITY_KEY = "attentionActivity";
const ACTIVITY_TIMEOUT_MS = 1_000;
const trackedTabs = new Map();
const activityByTab = new Map();
let activityRevision = 0;
let savedActivityRevision = -1;
let activityStorageHealthy = false;
let queues = Object.fromEntries(KINDS.map((kind) => [kind, emptyQueue()]));
let operations = Promise.resolve();
let automaticWave = null;
let automaticTimer = null;
// A previous worker could have dispatched a reload just before shutting down.
// This startup guard preserves spacing without persisting the reload plan.
let nextAutomaticReloadAt = Date.now() + RELOAD_GAP_MS;

function emptyQueue() {
  return { status: "inactive", count: null, checkedAt: null, message: "Open this GitHub view to monitor it." };
}

function cloneQueues() {
  return Object.fromEntries(KINDS.map((kind) => [kind, { ...queues[kind] }]));
}

function validStoredQueue(value) {
  return value && ["ok", "error", "loading", "inactive"].includes(value.status)
    && (value.count === null || (Number.isSafeInteger(value.count) && value.count >= 0))
    && (value.checkedAt === null || (typeof value.checkedAt === "string" && Number.isFinite(Date.parse(value.checkedAt))))
    && typeof value.message === "string";
}

async function save() {
  await chrome.storage.local.set({ [STORAGE_KEY]: cloneQueues() });
}

function setUnknown(kind, status, message) {
  // A failed check must never overwrite a previously verified nonempty count.
  queues[kind] = { ...queues[kind], status, message };
}

function enqueue(operation) {
  const result = operations.then(() => ready).then(operation);
  operations = result.catch(() => {
    // A later event must still be able to recover after a transient API error.
  });
  return result;
}

async function watchedTabs() {
  const tabs = await chrome.tabs.query({ url: "https://github.com/*" });
  return tabs.map((tab) => ({ tab, kind: classifyUrl(tab.url) })).filter(({ kind }) => kind !== null);
}

function validActivityTime(value, ceiling = Date.now()) {
  return Number.isSafeInteger(value) && value >= 0 && value <= ceiling;
}

function noteActivity(tabId, lastClickAt = null) {
  // Unknown history starts a full grace period. Loading/reloading a known tab
  // must not change its timestamp: only a genuine mouse press can do that.
  const previous = activityByTab.get(tabId);
  const baseline = validActivityTime(previous) ? previous : Date.now();
  const next = lastClickAt === null ? baseline : Math.max(baseline, lastClickAt);
  if (next !== previous) {
    activityByTab.set(tabId, next);
    activityRevision++;
  }
}

function forgetActivity(tabId) {
  if (activityByTab.delete(tabId)) activityRevision++;
}

async function saveActivity() {
  if (activityStorageHealthy && activityRevision === savedActivityRevision) return true;
  const revision = activityRevision;
  try {
    // Session storage survives worker restarts, not browser/extension restarts.
    // No URL, click target, page text, or pointer coordinates are retained.
    await chrome.storage.session.set({ [ACTIVITY_KEY]: Object.fromEntries(activityByTab) });
    savedActivityRevision = revision;
    activityStorageHealthy = true;
    return true;
  } catch {
    // Never discard a document's only click history by auto-reloading while
    // its session checkpoint is unavailable. A later cycle can retry.
    activityStorageHealthy = false;
    return false;
  }
}

function locallyIdle(tabId) {
  const lastActivityAt = activityByTab.get(tabId);
  return validActivityTime(lastActivityAt) && Date.now() - lastActivityAt >= ACTIVE_IDLE_MS;
}

async function activeTabIsIdle(tabId, kind) {
  const requestedAt = Date.now();
  let timeout;
  let reply;
  try {
    reply = await Promise.race([
      chrome.tabs.sendMessage(tabId, { type: "attention:activity-state", kind }, { frameId: 0 }),
      new Promise((resolve) => { timeout = setTimeout(() => resolve(null), ACTIVITY_TIMEOUT_MS); }),
    ]);
  } catch {
    return false;
  } finally {
    if (timeout !== undefined) clearTimeout(timeout);
  }
  if (!reply || reply.kind !== kind || !validActivityTime(reply.observedAt) ||
      reply.observedAt < requestedAt ||
      (reply.lastClickAt !== null && !validActivityTime(reply.lastClickAt, reply.observedAt))) return false;
  noteActivity(tabId, reply.lastClickAt);
  if (!await saveActivity()) return false;
  return locallyIdle(tabId);
}

function cancelAutomaticReloads() {
  if (automaticTimer !== null) clearTimeout(automaticTimer);
  automaticTimer = null;
  automaticWave = null;
}

async function reloadWatchedTab(tabId, kind, reloadActive) {
  let tab;
  try {
    tab = await chrome.tabs.get(tabId);
  } catch {
    setUnknown(kind, "error", "The watched GitHub tab could not be refreshed.");
    await save();
    return false;
  }
  if (classifyUrl(tab.url) !== kind) {
    trackedTabs.delete(tabId);
    setUnknown(kind, "error", "The watched tab left its queue; it may need GitHub sign-in.");
    await save();
    return false;
  }
  if (tab.status === "loading") return false;
  if (!reloadActive) {
    if (!await saveActivity()) return false;
    let idleApproved = false;
    if (tab.active) {
      idleApproved = await activeTabIsIdle(tabId, kind);
      if (!idleApproved) return false;
    }
    // Storage and the live activity check are asynchronous. Re-read browser
    // state before dispatch; newly active tabs without idle approval stay put.
    try { tab = await chrome.tabs.get(tabId); } catch { return false; }
    if (classifyUrl(tab.url) !== kind || tab.status === "loading" ||
        (tab.active && (!idleApproved || !locallyIdle(tabId))) ||
        activityRevision !== savedActivityRevision) return false;
  }
  // Recheck immediately before dispatch, not when a delayed wave is planned.
  // No await may appear between this check and the reload call.
  if (tab.status === "loading") return false;
  setUnknown(kind, "loading", "Checking this GitHub view…");
  try {
    await chrome.tabs.reload(tab.id);
  } catch {
    setUnknown(kind, "error", "The watched GitHub tab could not be refreshed.");
  }
  // Count failed dispatch attempts too. Waiting from API completion is
  // conservative: actual reload starts are always at least this far apart.
  nextAutomaticReloadAt = Date.now() + RELOAD_GAP_MS;
  await save();
  if (queues[kind].status === "loading") {
    await chrome.alarms.create(TIMEOUT_ALARM, { when: Date.now() + CHECK_TIMEOUT_MS });
  }
  return true;
}

function scheduleAutomaticStep(wave) {
  if (automaticWave !== wave) return;
  const delay = Math.max(1, nextAutomaticReloadAt - Date.now());
  automaticTimer = setTimeout(() => {
    if (automaticWave !== wave) return;
    automaticTimer = null;
    // Only dispatch work enters the shared queue. Never hold it during the
    // five-second gap: reports, popup requests, and timeouts must stay live.
    void enqueue(() => dispatchAutomaticStep(wave)).catch(() => {
      if (automaticWave === wave) cancelAutomaticReloads();
    });
  }, delay);
}

async function dispatchAutomaticStep(wave) {
  if (automaticWave !== wave) return;
  if (Date.now() < nextAutomaticReloadAt) {
    scheduleAutomaticStep(wave);
    return;
  }
  try {
    while (wave.pending.length) {
      const { tabId, kind } = wave.pending.shift();
      if (!await reloadWatchedTab(tabId, kind, false)) continue;
      if (wave.pending.length) scheduleAutomaticStep(wave);
      else automaticWave = null;
      return;
    }
    automaticWave = null;
  } catch (error) {
    cancelAutomaticReloads();
    throw error;
  }
}

async function startAutomaticReloads(matches) {
  // A delayed alarm must not duplicate or restart a partially completed wave.
  if (automaticWave !== null) return;
  const pending = matches
    .filter(({ tab }) => tab.status !== "loading")
    .sort((a, b) => KINDS.indexOf(a.kind) - KINDS.indexOf(b.kind) || a.tab.id - b.tab.id)
    .map(({ tab, kind }) => ({ tabId: tab.id, kind }));
  if (!pending.length) return;
  automaticWave = { pending };
  await dispatchAutomaticStep(automaticWave);
  // A lost timer after worker shutdown is harmless: the next periodic alarm
  // builds a fresh wave. Delayed callbacks always space from the last actual
  // dispatch, rather than trying to catch up to nominal offsets in a burst.
}

async function reconcileTabs(reloadActive = false, reload = false) {
  if (reload && reloadActive) cancelAutomaticReloads();
  let matches;
  try {
    matches = await watchedTabs();
  } catch {
    for (const kind of KINDS) setUnknown(kind, "error", "Cannot inspect the watched GitHub tabs.");
    await save();
    return;
  }

  const present = new Set(matches.map(({ kind }) => kind));
  const presentIds = new Set(matches.map(({ tab }) => tab.id));
  for (const tabId of activityByTab.keys()) {
    if (!presentIds.has(tabId)) forgetActivity(tabId);
  }
  for (const { tab } of matches) noteActivity(tab.id);
  await saveActivity();
  for (const [tabId] of trackedTabs) {
    if (!presentIds.has(tabId)) trackedTabs.delete(tabId);
  }
  for (const kind of KINDS) {
    if (!present.has(kind)) setUnknown(kind, "inactive", "No tab is open on this watched GitHub view.");
  }

  let awaitingReport = false;
  for (const { tab, kind } of matches) {
    trackedTabs.set(tab.id, { kind });
    if (queues[kind].status === "inactive") {
      setUnknown(kind, "loading", "Waiting for this tab to report its queue.");
      awaitingReport = true;
    }
  }
  await save();
  if (awaitingReport) {
    await chrome.alarms.create(TIMEOUT_ALARM, { when: Date.now() + CHECK_TIMEOUT_MS });
  }

  if (reload) {
    if (!reloadActive) {
      await startAutomaticReloads(matches);
      return;
    }
    // Explicit Check now stays immediate, and supersedes pending automatic work.
    for (const { tab, kind } of matches) {
      await reloadWatchedTab(tab.id, kind, true);
    }
  }
}

async function initialize() {
  try {
    const stored = await chrome.storage.session.get(ACTIVITY_KEY);
    const records = stored[ACTIVITY_KEY];
    if (records && typeof records === "object" && !Array.isArray(records)) {
      for (const [tabId, timestamp] of Object.entries(records)) {
        const id = Number(tabId);
        if (Number.isSafeInteger(id) && id >= 0 && validActivityTime(timestamp)) {
          // A click can arrive while storage is being read on worker startup.
          activityByTab.set(id, Math.max(activityByTab.get(id) ?? 0, timestamp));
        }
      }
    }
    activityStorageHealthy = true;
  } catch {
    // Reconciliation supplies conservative new grace periods when history
    // cannot be recovered. Queue reporting/manual refresh still work; automatic
    // reloads resume only once the session checkpoint can be saved safely.
  }
  try {
    const stored = await chrome.storage.local.get(STORAGE_KEY);
    for (const kind of KINDS) {
      if (validStoredQueue(stored[STORAGE_KEY]?.[kind])) {
        queues[kind] = { ...stored[STORAGE_KEY][kind], message: stored[STORAGE_KEY][kind].message.slice(0, 200) };
        if (queues[kind].status === "loading") {
          setUnknown(kind, "error", "The previous queue check did not complete.");
        }
      }
    }
  } catch {
    for (const kind of KINDS) setUnknown(kind, "error", "Saved queue state could not be read.");
  }
  // Alarms may be cleared between browser sessions. Do not reset an existing
  // alarm on every worker wake: frequent content reports could postpone it forever.
  const refreshAlarm = await chrome.alarms.get(REFRESH_ALARM);
  if (!refreshAlarm || refreshAlarm.periodInMinutes !== REFRESH_MINUTES) {
    await chrome.alarms.create(REFRESH_ALARM, { periodInMinutes: REFRESH_MINUTES });
  }
  await reconcileTabs(false, false);
}

const ready = initialize();

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === REFRESH_ALARM) {
    void enqueue(() => reconcileTabs(false, true));
  } else if (alarm.name === TIMEOUT_ALARM) {
    void enqueue(async () => {
      for (const kind of KINDS) {
        if (queues[kind].status === "loading") {
          setUnknown(kind, "error", "No reliable queue result arrived. Check this tab for sign-in or a page error.");
        }
      }
      await save();
    });
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  const known = trackedTabs.has(tabId) || activityByTab.has(tabId);
  forgetActivity(tabId);
  if (!known) return;
  trackedTabs.delete(tabId);
  void enqueue(() => reconcileTabs(false, false));
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  const previous = trackedTabs.get(tabId);
  const kind = classifyUrl(changeInfo.url || tab?.url);
  if (changeInfo.url && kind === null && activityByTab.has(tabId)) {
    forgetActivity(tabId);
    void enqueue(() => saveActivity());
  }
  if (previous && changeInfo.url && kind !== previous.kind) {
    trackedTabs.delete(tabId);
    void enqueue(async () => {
      setUnknown(previous.kind, "error", "The watched tab left its queue; it may need GitHub sign-in.");
      await save();
    });
    return;
  }
  if (kind === null) return;
  trackedTabs.set(tabId, { kind });
  if (changeInfo.status === "loading") {
    void enqueue(async () => {
      setUnknown(kind, "loading", "Waiting for the GitHub page to finish loading…");
      await save();
      await chrome.alarms.create(TIMEOUT_ALARM, { when: Date.now() + CHECK_TIMEOUT_MS });
    });
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "attention:activity") {
    const tabId = sender.tab?.id;
    const kind = classifyUrl(sender.tab?.url);
    if (!Number.isSafeInteger(tabId) || tabId < 0 || kind === null || kind !== message.kind ||
        (sender.frameId !== undefined && sender.frameId !== 0) || !validActivityTime(message.lastClickAt)) {
      sendResponse({ error: "Ignored an invalid activity report." });
      return false;
    }
    // Record immediately, even while an idle-check operation is awaiting an
    // API. Persistence remains serialized, and no verification time is changed.
    noteActivity(tabId, message.lastClickAt);
    void enqueue(async () => ({ accepted: await saveActivity() })).then(sendResponse,
      () => sendResponse({ error: "Activity history could not be saved." }));
    return true;
  }
  if (message?.type === "attention:status") {
    void enqueue(async () => {
      await reconcileTabs(false, false);
      return { queues: cloneQueues() };
    }).then(sendResponse, () => sendResponse({ error: "Queue status could not be read." }));
    return true;
  }
  if (message?.type === "attention:refresh") {
    void enqueue(async () => {
      // An explicit popup click authorizes refreshing an active watched tab too.
      await reconcileTabs(true, true);
      return { queues: cloneQueues() };
    }).then(sendResponse, () => sendResponse({ error: "Queue refresh failed." }));
    return true;
  }
  if (message?.type !== "attention:report") return false;

  const kind = classifyUrl(sender.tab?.url);
  const state = message.state;
  const validCount = state?.count === null || (Number.isSafeInteger(state?.count) && state.count >= 0);
  if (kind === null || kind !== message.kind || !state || !["ok", "loading", "error"].includes(state.status)
      || !validCount || (state.status === "ok" && state.count === null)
      || (state.message !== undefined && typeof state.message !== "string")) {
    sendResponse({ error: "Ignored an invalid queue report." });
    return false;
  }

  void enqueue(async () => {
    trackedTabs.set(sender.tab.id, { kind });
    if (state.status === "ok") {
      queues[kind] = {
        status: "ok",
        count: state.count,
        checkedAt: new Date().toISOString(),
        message: (state.message || "").slice(0, 200),
      };
    } else if (state.status === "loading") {
      setUnknown(kind, "loading", (state.message || "Waiting for a reliable GitHub queue result…").slice(0, 200));
      await chrome.alarms.create(TIMEOUT_ALARM, { when: Date.now() + CHECK_TIMEOUT_MS });
    } else {
      setUnknown(kind, "error", (state.message || "This GitHub queue could not be read reliably.").slice(0, 200));
    }
    await save();
    return { accepted: true };
  }).then(sendResponse, () => sendResponse({ error: "The queue report could not be saved." }));
  return true;
});
