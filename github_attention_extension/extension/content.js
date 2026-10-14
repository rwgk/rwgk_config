/* Runs in Chrome's isolated world. No credentials, network requests, or body edits. */
(function (root) {
  "use strict";

  function createController(document, browserWindow, chrome, core) {
    const GRACE_MS = 15000;
    const DEBOUNCE_MS = 150;
    const ICON_ID = "github-attention-favicon";
    const now = () => (browserWindow.Date || Date).now();
    const loadedUrl = browserWindow.location.href.split("#", 1)[0];
    let changedDocumentRoute = false;
    let currentKind = null;
    let lastUrl = browserWindow.location.href;
    let beganAt = 0;
    let lastReport = null;
    let lastRead = null;
    let lastVerifiedAt = null;
    let staleShown = false;
    let timer = null;
    let graceTimer = null;
    let disposed = false;
    let badge = null;

    function send(state) {
      const signature = JSON.stringify([currentKind, state.status, state.count, state.message]);
      if (signature === lastReport) return;
      lastReport = signature;
      try {
        const result = chrome.runtime.sendMessage({
          type: "attention:report", kind: currentKind, state,
        });
        if (result && result.catch) result.catch(() => {});
      } catch (_) {
        // The extension may have been reloaded while this tab remains open.
      }
    }

    function removeBadge() {
      if (badge && badge.isConnected) {
        badge.remove();
        // Reinsert unchanged site icons once, so Chrome re-evaluates the
        // favicon instead of retaining its cached extension override.
        for (const icon of document.head.querySelectorAll('link[rel~="icon"]')) {
          document.head.appendChild(icon);
        }
      }
      badge = null;
    }

    function showBadge(state) {
      if (!document.head) return;
      const icon = state.status === "ok"
        ? (state.count > 0 ? "nonempty" : "clear") : "error";
      const href = chrome.runtime.getURL(`icons/${icon}.svg`);
      if (!badge || !badge.isConnected) {
        badge = document.createElement("link");
        badge.id = ICON_ID;
        badge.rel = "icon";
        badge.type = "image/svg+xml";
        badge.setAttribute("sizes", "any");
      }
      if (badge.getAttribute("href") !== href) badge.setAttribute("href", href);
      // Keep the override after GitHub's own favicon links; leave those untouched.
      const icons = document.head.querySelectorAll('link[rel~="icon"]');
      if (!badge.isConnected || icons[icons.length - 1] !== badge) {
        document.head.appendChild(badge);
      }
    }

    function check() {
      if (disposed) return;
      // SPA navigation can leave the old filter's DOM/data behind. Once this
      // document changes route, require a full reload before trusting a queue.
      if (browserWindow.location.href.split("#", 1)[0] !== loadedUrl) changedDocumentRoute = true;
      const kind = core.classifyUrl(browserWindow.location.href);
      if (kind !== currentKind) {
        removeBadge();
        currentKind = kind;
        lastReport = null;
        lastRead = null;
        lastVerifiedAt = null;
        staleShown = false;
        beganAt = now();
      }
      if (!currentKind) return;
      let state;
      if (changedDocumentRoute) {
        state = { status: "error", count: null, message: "Reload this view after in-page navigation to verify the queue." };
      } else {
        try {
          state = core.readDocument(document, currentKind);
        } catch (_) {
          state = { status: "error", count: null, message: "Could not read this GitHub page." };
        }
      }
      const readSignature = JSON.stringify([state.status, state.count, state.message]);
      if (state.status === "ok" && readSignature !== lastRead) lastVerifiedAt = now();
      lastRead = readSignature;
      if (!changedDocumentRoute && state.status !== "ok" && now() - beganAt < GRACE_MS) {
        state = { status: "loading", count: null, message: "Waiting for GitHub to finish loading." };
      } else if (state.status === "ok" && lastVerifiedAt !== null && now() - lastVerifiedAt >= core.STALE_MS) {
        state = { status: "error", count: null, message: "Loaded page is stale. Reload this view to verify the queue." };
        staleShown = true;
      } else {
        staleShown = false;
      }
      showBadge(state);
      send(state);
      if (state.status === "loading" && graceTimer === null) {
        graceTimer = browserWindow.setTimeout(() => {
          graceTimer = null;
          check();
        }, Math.max(1, GRACE_MS - (now() - beganAt) + 1));
      } else if (state.status !== "loading" && graceTimer !== null) {
        browserWindow.clearTimeout(graceTimer);
        graceTimer = null;
      }
    }

    function schedule(delay = DEBOUNCE_MS) {
      if (disposed) return;
      if (timer !== null) browserWindow.clearTimeout(timer);
      timer = browserWindow.setTimeout(() => {
        timer = null;
        check();
      }, Math.max(1, delay));
    }

    function navigated() {
      lastUrl = browserWindow.location.href;
      beganAt = now();
      // Do not freshen unchanged DOM merely because a soft-navigation event
      // fired. A new watched kind or a changed parser result can reverify it.
      if (graceTimer !== null) browserWindow.clearTimeout(graceTimer);
      graceTimer = null;
      check();
    }

    const observer = new browserWindow.MutationObserver((mutations) => {
      // Ignore changes made to our own icon, avoiding a self-triggering loop.
      const external = mutations.some((mutation) => {
        if (mutation.target === badge) return false;
        if (mutation.type === "childList") {
          const nodes = [...mutation.addedNodes, ...mutation.removedNodes];
          if (nodes.length && nodes.every((node) => node === badge)) return false;
        }
        return true;
      });
      if (external) schedule();
    });
    observer.observe(document.documentElement, {
      childList: true, subtree: true, characterData: true, attributes: true,
      attributeFilter: ["class", "hidden", "href", "data-notification-id"],
    });

    const events = ["popstate", "hashchange", "turbo:load", "pjax:end"];
    for (const event of events) browserWindow.addEventListener(event, navigated);
    // GitHub can replace history without dispatching popstate.
    const urlTimer = browserWindow.setInterval(() => {
      if (browserWindow.location.href !== lastUrl) {
        lastUrl = browserWindow.location.href;
        navigated();
      } else if (currentKind && !staleShown && lastVerifiedAt !== null && now() - lastVerifiedAt >= core.STALE_MS) {
        check();
      }
    }, 1000);
    const onMessage = (message) => {
      if (message && message.type === "attention:check") check();
    };
    chrome.runtime.onMessage.addListener(onMessage);
    check();
    return {
      check,
      dispose() {
        disposed = true;
        observer.disconnect();
        if (timer !== null) browserWindow.clearTimeout(timer);
        if (graceTimer !== null) browserWindow.clearTimeout(graceTimer);
        browserWindow.clearInterval(urlTimer);
        for (const event of events) browserWindow.removeEventListener(event, navigated);
        chrome.runtime.onMessage.removeListener(onMessage);
        removeBadge();
      },
    };
  }

  if (typeof module === "object" && module.exports) {
    module.exports = { createController };
  } else if (root.document && root.chrome && root.GitHubAttention) {
    createController(root.document, root, root.chrome, root.GitHubAttention);
  }
})(globalThis);
