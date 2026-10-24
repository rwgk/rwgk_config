/* Shared, side-effect-free URL matching and fail-closed page parsers. */
(function (root) {
  "use strict";

  const KINDS = ["reviews", "approved", "mentions", "notifications"];
  const URLS = {
    reviews: "https://github.com/notifications?query=reason%3Areview-requested",
    approved: "https://github.com/search?q=is%3Apr+is%3Aopen+author%3Arwgk+review%3Aapproved&type=pullrequests",
    mentions: "https://github.com/notifications?query=reason%3Amention",
    notifications: "https://github.com/notifications",
  };
  const REFRESH_MINUTES = 2;
  const STALE_MS = 6 * 60 * 1000;
  const ACTIVE_IDLE_MS = 6 * 60 * 1000;

  function sameTokens(query, expected) {
    const tokens = String(query || "").trim().toLowerCase().split(/\s+/).sort();
    return JSON.stringify(tokens) === JSON.stringify(expected.slice().sort());
  }

  function classifyUrl(value) {
    let url;
    try { url = new URL(value); } catch { return null; }
    if (url.origin !== "https://github.com") return null;
    const path = url.pathname.replace(/\/$/, "");
    const p = url.searchParams;
    // A later notification page can be empty even when the full queue is not.
    if (p.getAll("page").length > 1 || p.getAll("p").length > 1 ||
        (p.has("page") && p.get("page") !== "1") || (p.has("p") && p.get("p") !== "1") ||
        p.has("before") || p.has("after")) return null;
    const allowed = path === "/search" ? ["q", "type", "page", "p", "utf8"] : ["query", "page", "utf8"];
    if (Array.from(p.keys()).some((key) => !allowed.includes(key))) return null;
    if (path === "/search" && p.getAll("q").length === 1 && p.getAll("type").length === 1 &&
        p.get("type") === "pullrequests" &&
        sameTokens(p.get("q"), ["is:pr", "is:open", "author:rwgk", "review:approved"])) return "approved";
    if (path === "/notifications") {
      if (!p.has("query")) return "notifications";
      if (p.getAll("query").length !== 1) return null;
      if (sameTokens(p.get("query"), ["reason:review-requested"])) return "reviews";
      if (sameTokens(p.get("query"), ["reason:mention"])) return "mentions";
    }
    return null;
  }

  function error(message) { return {status: "error", count: null, message}; }
  function ok(count, message = "") { return {status: "ok", count, message}; }
  function text(element) { return (element?.textContent || "").replace(/\s+/g, " ").trim(); }

  function signedIn(document) {
    const login = document.querySelector('meta[name="user-login"]');
    return Boolean(login?.getAttribute("content")?.trim()) &&
      !document.body?.classList.contains("logged-out") &&
      !document.querySelector('form[action="/session"] input[name="password"]');
  }

  function visible(element, document) {
    if (!element) return false;
    for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
      if (node.hidden || node.hasAttribute("hidden") || node.getAttribute("aria-hidden") === "true" ||
          node.classList.contains("d-none")) return false;
      const style = document.defaultView?.getComputedStyle?.(node) || node.style;
      if (style?.display === "none" || style?.visibility === "hidden" || style?.visibility === "collapse") return false;
    }
    return true;
  }

  function readApproved(document) {
    const app = document.querySelector('react-app[app-name="blackbird-search"]');
    if (!app) return error("GitHub approved-PR search layout not recognized.");
    // Embedded server data may remain unchanged after React changes the query.
    // Trust it only when it belongs to this exact personal approved-PR view.
    const initialPath = app.getAttribute("initial-path");
    let initialUrl;
    try { initialUrl = new URL(initialPath, "https://github.com").href; } catch { return error("GitHub search route could not be verified."); }
    if (!initialPath || classifyUrl(initialUrl) !== "approved") {
      return error("Search data belongs to a different view. Reload this approved-PR page.");
    }
    const script = app.querySelector('script[data-target="react-app.embeddedData"]');
    if (script) {
      let data;
      try { data = JSON.parse(script.textContent); } catch { return error("GitHub search data could not be read."); }
      const payload = data.payload?.blackbirdSearchRoute || data.payload;
      if (!payload || payload.type !== "pullrequests" || payload.logged_in !== true ||
          !Array.isArray(payload.errors) || payload.errors.length ||
          !Number.isSafeInteger(payload.result_count) || payload.result_count < 0 ||
          !Array.isArray(payload.results) || payload.warn_limited_results === true) {
        return error("GitHub search returned incomplete data, an error, or a signed-out view.");
      }
      if (payload.result_count === 0 && payload.results.length > 0) return error("GitHub search results are inconsistent.");
      if (payload.result_count > 0 && payload.results.length === 0) return error("GitHub search results have not loaded.");
      return ok(payload.result_count);
    }
    // Fail closed instead of inferring emptiness from missing rows or a spinner.
    return error("GitHub approved-PR search data has not loaded or its layout changed.");
  }

  function readNotifications(document, kind) {
    const main = document.querySelector('main, [role="main"]');
    if (!main) return error("GitHub notifications layout not recognized.");
    // Repository grouping can produce sibling lists without a shared wrapper.
    // One selector union collects every row once, including nested wrappers.
    const containers = Array.from(main.querySelectorAll(".js-notifications-container, .js-notifications-list, .notifications-list"));
    const rows = Array.from(main.querySelectorAll(
      ".js-notifications-container .notifications-list-item, .js-notifications-list .notifications-list-item, .notifications-list .notifications-list-item"
    )).filter((row) => visible(row, document));
    const hasMore = Array.from(main.querySelectorAll(
      'a[rel~="next"], a.next_page, button.next_page, .paginate-container a, .paginate-container button, .pagination a, .pagination button, [data-target="notifications-list.pagination"] a'
    )).some((control) => {
      if (!visible(control, document) || control.disabled || control.hasAttribute("disabled") ||
          control.getAttribute("aria-disabled") === "true") return false;
      const label = `${control.getAttribute("aria-label") || ""} ${text(control)}`;
      return control.getAttribute("rel")?.split(/\s+/).includes("next") ||
        control.classList.contains("next_page") || /\b(next|older|load more)\b/i.test(label);
    });
    // Small numbers must not imply an exact total if another page is known to
    // exist. Once ten rows are proven, the favicon's X is a safe lower bound.
    if (hasMore && rows.length < 10) return error("More notification pages exist; a count below ten could not be verified.");
    if (rows.length) {
      const scope = kind === "reviews" ? "review-request" : kind === "mentions" ? "mention" : "inbox";
      return ok(rows.length, `${hasMore ? "At least " : ""}${rows.length} ${scope} threads shown on the first page; read and unread, until Done.`);
    }
    // Empty is accepted only from a specific empty-state panel in the notifications
    // page, never from absent rows, generic body text, or the global inbox badge.
    const explicitEmpty = Array.from(main.querySelectorAll(".js-notifications-blankslate"));
    if (containers.some((container) => visible(container, document)) &&
        explicitEmpty.some((panel) => visible(panel, document))) return ok(0);
    const panels = main.querySelectorAll(".notifications-list .blankslate, .js-notifications-list .blankslate, .notifications-list-empty .blankslate");
    for (const panel of panels) {
      const heading = panel.querySelector("h1, h2, h3");
      if (!visible(panel, document) || !heading || !/^(You['’]re all caught up!?|No notifications|No notifications matched your query\.?|No notifications found\.?)$/i.test(text(heading))) continue;
      const pageHeading = main.querySelector("h1");
      if (containers.length || /notifications/i.test(text(pageHeading)) || main.querySelector('form[action="/notifications"]')) return ok(0);
    }
    return error("GitHub notifications have not loaded, or their layout is not recognized.");
  }

  function readDocument(document, kind) {
    if (!KINDS.includes(kind)) return error("Not a watched GitHub view.");
    if (!signedIn(document)) return error("GitHub sign-in is missing or could not be verified.");
    return kind === "approved" ? readApproved(document) : readNotifications(document, kind);
  }

  const api = {KINDS, URLS, REFRESH_MINUTES, STALE_MS, ACTIVE_IDLE_MS, classifyUrl, readDocument};
  root.GitHubAttention = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(globalThis);
