/* Shared, side-effect-free URL matching and fail-closed page parsers. */
(function (root) {
  "use strict";

  const KINDS = ["reviews", "mentions"];
  const URLS = {
    reviews: "https://github.com/search?q=is%3Apr+is%3Aopen+user-review-requested%3A%40me&type=pullrequests",
    mentions: "https://github.com/notifications?query=is%3Aunread%20reason%3Amention",
  };
  const REFRESH_MINUTES = 2;
  const STALE_MS = 6 * 60 * 1000;

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
        sameTokens(p.get("q"), ["is:pr", "is:open", "user-review-requested:@me"])) return "reviews";
    if (path === "/notifications" && p.getAll("query").length === 1 &&
        sameTokens(p.get("query"), ["is:unread", "reason:mention"])) return "mentions";
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

  function readReviews(document) {
    const app = document.querySelector('react-app[app-name="blackbird-search"]');
    if (!app) return error("GitHub review-search layout not recognized.");
    // Embedded server data may remain unchanged after React changes the query.
    // Trust it only when it belongs to this exact personal review-request view.
    const initialPath = app.getAttribute("initial-path");
    let initialUrl;
    try { initialUrl = new URL(initialPath, "https://github.com").href; } catch { return error("GitHub search route could not be verified."); }
    if (!initialPath || classifyUrl(initialUrl) !== "reviews") {
      return error("Search data belongs to a different view. Reload this review-request page.");
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
    return error("GitHub review-search data has not loaded or its layout changed.");
  }

  function readMentions(document) {
    const main = document.querySelector('main, [role="main"]');
    if (!main) return error("GitHub notifications layout not recognized.");
    const container = main.querySelector(".js-notifications-container, .js-notifications-list, .notifications-list");
    const rows = Array.from(container?.querySelectorAll(".notifications-list-item") || []).filter((row) => visible(row, document));
    if (rows.length) return ok(rows.length, "At least this many unread mention threads are listed (first page).");
    // Empty is accepted only from a specific empty-state panel in the notifications
    // page, never from absent rows, generic body text, or the global inbox badge.
    const explicitEmpty = main.querySelector(".js-notifications-blankslate");
    if (container && visible(explicitEmpty, document)) return ok(0);
    const panel = main.querySelector(".notifications-list .blankslate, .js-notifications-list .blankslate, .notifications-list-empty .blankslate");
    const heading = panel?.querySelector("h1, h2, h3");
    if (visible(panel, document) && heading && /^(You['’]re all caught up!?|No notifications|No notifications matched your query\.?|No notifications found\.?)$/i.test(text(heading))) {
      const pageHeading = main.querySelector("h1");
      if (container || /notifications/i.test(text(pageHeading)) || main.querySelector('form[action="/notifications"]')) return ok(0);
    }
    return error("GitHub notifications have not loaded, or their layout is not recognized.");
  }

  function readDocument(document, kind) {
    if (!KINDS.includes(kind)) return error("Not a watched GitHub view.");
    if (!signedIn(document)) return error("GitHub sign-in is missing or could not be verified.");
    return kind === "reviews" ? readReviews(document) : readMentions(document);
  }

  const api = {KINDS, URLS, REFRESH_MINUTES, STALE_MS, classifyUrl, readDocument};
  root.GitHubAttention = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(globalThis);
