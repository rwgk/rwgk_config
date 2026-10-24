/* global chrome, importScripts */
"use strict";

importScripts("core.js");

const { KINDS, classifyUrl, REFRESH_MINUTES } = globalThis.GitHubAttention;
const REFRESH_ALARM = "attention:refresh";
const TIMEOUT_ALARM = "attention:timeout";
// v0.1 watched current review requests and unread mentions. Those counts must
// never be reused as verified results for the new Done-based inbox views.
const STORAGE_KEY = "attentionQueuesV2";
const CHECK_TIMEOUT_MS = 60_000;
const RELOAD_GAP_MS = 5_000;
const trackedTabs = new Map();
let queues = Object.fromEntries(KINDS.map((kind) => [kind, emptyQueue()]));
let operations = Promise.resolve();
let automaticWave = null;
let automaticTimer = null;
// A previous worker could have dispatched a reload just before shutting down.
// This startup guard preserves spacing without persisting browser tab IDs.
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
  // Recheck immediately before dispatch, not when a delayed wave is planned.
  // No await may appear between this check and the reload call.
  if ((!reloadActive && tab.active) || tab.status === "loading") return false;
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
    .filter(({ tab }) => !tab.active && tab.status !== "loading")
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
  if (!trackedTabs.has(tabId)) return;
  trackedTabs.delete(tabId);
  void enqueue(() => reconcileTabs(false, false));
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  const previous = trackedTabs.get(tabId);
  const kind = classifyUrl(changeInfo.url || tab?.url);
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
