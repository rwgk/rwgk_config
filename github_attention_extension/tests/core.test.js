"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {parseHTML} = require("linkedom");
const core = require("../extension/core.js");

const page = (body, login = "example-user") => parseHTML(`<!doctype html><html><head><meta name="user-login" content="${login}"></head><body><main>${body}</main></body></html>`).document;
const search = (overrides = {}, legacy = false) => {
  const payload = {type: "pullrequests", logged_in: true, result_count: 1, results: [{}], errors: [], ...overrides};
  return page(`<react-app app-name="blackbird-search" initial-path="${core.URLS.approved.replaceAll("&", "&amp;")}"><script data-target="react-app.embeddedData">${JSON.stringify({payload: legacy ? payload : {blackbirdSearchRoute: payload}})}</script></react-app>`);
};
const notificationKinds = ["reviews", "mentions", "notifications"];
const rows = (count) => '<li class="notifications-list-item"></li>'.repeat(count);

test("one-minute active-tab idleness is independent of two-minute polling and six-minute verification staleness", () => {
  assert.equal(core.ACTIVE_IDLE_MS, 60_000);
  assert.equal(core.REFRESH_MINUTES, 2);
  assert.equal(core.STALE_MS, 6 * 60_000);
});

test("matches the four exact semantic queues in priority order", () => {
  assert.deepEqual(core.KINDS, ["reviews", "approved", "mentions", "notifications"]);
  for (const kind of core.KINDS) assert.equal(core.classifyUrl(core.URLS[kind]), kind);
  assert.equal(core.classifyUrl("https://github.com/search?type=pullrequests&q=review%3Aapproved+author%3Arwgk+is%3Aopen+is%3Apr"), "approved");
  assert.equal(core.classifyUrl("https://github.com/notifications/?query=%20REASON%3AMENTION%20"), "mentions");
  assert.equal(core.classifyUrl("https://github.com/notifications/#inbox"), "notifications");
});

test("old queue URLs and other searches are deliberately not watched", () => {
  for (const url of [
    "https://evil.example/notifications?query=reason:mention",
    "http://github.com/notifications?query=reason:mention",
    "https://github.com/search?q=is:pr+is:open+user-review-requested:@me&type=pullrequests",
    "https://github.com/notifications?query=is:unread+reason:mention",
    "https://github.com/notifications?query=is:unread+reason:review-requested",
    "https://github.com/notifications?query=reason:participating",
    "https://github.com/notifications?query=",
    "https://github.com/notifications?query=reason:mention&query=reason:review-requested",
    `${core.URLS.approved}&q=is:issue`,
    core.URLS.approved.replace("author%3Arwgk", "author%3Aother"),
    core.URLS.approved.replace("is%3Apr", "is%3Aissue"),
    core.URLS.approved.replace("review%3Aapproved", "review%3Aapproved+draft%3Afalse"),
    "not a URL",
  ]) assert.equal(core.classifyUrl(url), null, url);
});

test("search uses validated result_count in the current and legacy payload", () => {
  assert.equal(core.readDocument(search({result_count: 37}), "approved").count, 37);
  assert.equal(core.readDocument(search({result_count: 2}, true), "approved").count, 2);
  assert.deepEqual(core.readDocument(search({result_count: 0, results: []}), "approved"), {status: "ok", count: 0, message: ""});
});

test("search fails closed for errors, invalid counts, inconsistent rows, and logged out state", () => {
  for (const overrides of [{errors: ["rate limit"]}, {errors: undefined}, {result_count: "0"}, {result_count: -1}, {result_count: 0, results: [{}]}, {result_count: 2, results: []}, {logged_in: false}, {type: "issues"}, {warn_limited_results: true}]) {
    assert.equal(core.readDocument(search(overrides), "approved").status, "error");
  }
  assert.equal(core.readDocument(page("", ""), "approved").status, "error");
  const malformed = search();
  malformed.querySelector("script").textContent = "{";
  assert.equal(core.readDocument(malformed, "approved").status, "error");
  assert.equal(core.readDocument(page('<react-app app-name="blackbird-search"></react-app>'), "approved").status, "error");
});

test("stale search payload from a different SPA query cannot report clear", () => {
  const doc = search({result_count: 0, results: []});
  doc.querySelector("react-app").setAttribute("initial-path", "/search?q=is:pr+is:open+repo:example/repo&type=pullrequests");
  assert.equal(core.readDocument(doc, "approved").status, "error");
  doc.querySelector("react-app").setAttribute("initial-path", "/search?q=is:pr+is:open+user-review-requested:@me&type=pullrequests");
  assert.equal(core.readDocument(doc, "approved").status, "error");
  doc.querySelector("react-app").removeAttribute("initial-path");
  assert.equal(core.readDocument(doc, "approved").status, "error");
});

test("later pages, duplicate pagination, and unknown filters are never watched", () => {
  for (const kind of core.KINDS) {
    const separator = core.URLS[kind].includes("?") ? "&" : "?";
    for (const suffix of ["page=2", "page=1&page=2", "page=1&page=1", "p=2", "p=1&p=2", "repo=example/project", "all=1", "before=cursor", "after=cursor", "source=search_bar"]) {
      assert.equal(core.classifyUrl(core.URLS[kind] + separator + suffix), null, `${kind}: ${suffix}`);
    }
    assert.equal(core.classifyUrl(core.URLS[kind] + separator + "page=1"), kind);
    assert.equal(core.classifyUrl(core.URLS[kind] + separator + "utf8=%E2%9C%93"), kind);
  }
  assert.equal(core.classifyUrl(core.URLS.approved + "&p=1"), "approved");
});

for (const kind of notificationKinds) {
  test(`${kind} counts read and unread threads, not the global unread count`, () => {
    const doc = page('<div class="js-notifications-list"><li class="notifications-list-item notification-unread"></li><li class="notifications-list-item notification-read"></li></div><span class="notification-indicator">999</span>');
    const result = core.readDocument(doc, kind);
    assert.equal(result.count, 2);
    assert.match(result.message, /read and unread, until Done/);
  });

  test(`${kind} counts all sibling repository groups without double counting nested wrappers`, () => {
    const doc = page(`<div class="js-notifications-list">${rows(3)}</div><div class="js-notifications-container"><ul class="notifications-list">${rows(8)}</ul></div>`);
    assert.equal(core.readDocument(doc, kind).count, 11);
  });

  test(`${kind} ignores hidden groups, hidden rows, and unrelated rows`, () => {
    const doc = page(`<div class="js-notifications-list">${rows(2)}<li hidden class="notifications-list-item"></li><li class="notifications-list-item d-none"></li></div><div hidden class="notifications-list">${rows(7)}</div><aside>${rows(4)}</aside>`);
    assert.equal(core.readDocument(doc, kind).count, 2);
  });

  test(`${kind} handles a Done removal and verifies the resulting explicit empty view`, () => {
    const doc = page(`<div class="js-notifications-container">${rows(1)}<div class="js-notifications-blankslate" hidden>All done</div></div>`);
    assert.equal(core.readDocument(doc, kind).count, 1);
    doc.querySelector(".notifications-list-item").remove();
    assert.equal(core.readDocument(doc, kind).status, "error");
    doc.querySelector(".js-notifications-blankslate").removeAttribute("hidden");
    assert.equal(core.readDocument(doc, kind).count, 0);
  });
}

test("a hidden or stale blankslate cannot hide nonempty sibling groups", () => {
  const doc = page(`<div class="js-notifications-list"><div class="blankslate"><h3>No notifications</h3></div></div><ul class="notifications-list">${rows(5)}</ul>`);
  assert.equal(core.readDocument(doc, "notifications").count, 5);
});

test("pagination below ten is unknown, while ten proven rows safely show X", () => {
  for (const pagination of [
    '<a rel="next" href="/notifications?page=2">Next</a>',
    '<div class="paginate-container"><a href="/notifications?after=cursor">Older</a></div>',
    '<div class="pagination"><button aria-label="Next page">→</button></div>',
    '<button class="next_page">Load more</button>',
  ]) {
    for (const kind of notificationKinds) {
      for (const count of [0, 1, 9]) {
        const doc = page(`<div class="js-notifications-list">${rows(count)}<div class="blankslate"><h3>No notifications</h3></div></div>${pagination}`);
        const result = core.readDocument(doc, kind);
        assert.equal(result.status, "error");
        assert.equal(result.count, null);
      }
      for (const count of [10, 25]) {
        const result = core.readDocument(page(`<div class="js-notifications-list">${rows(count)}</div>${pagination}`), kind);
        assert.equal(result.count, count);
        assert.match(result.message, /At least/);
      }
    }
  }
});

test("disabled and hidden pagination controls do not imply more results", () => {
  for (const pagination of [
    '<a rel="next" aria-disabled="true">Next</a>',
    '<button class="next_page" disabled>Next</button>',
    '<div hidden><a rel="next">Next</a></div>',
    '<div class="pagination"><a rel="prev">Previous</a></div>',
  ]) {
    const result = core.readDocument(page(`<div class="notifications-list">${rows(3)}</div>${pagination}`), "notifications");
    assert.equal(result.count, 3);
  }
});

test("notifications require explicit scoped empty evidence", () => {
  for (const heading of ["You're all caught up!", "You’re all caught up!", "No notifications matched your query."]) {
    const doc = page(`<div class="js-notifications-list"><div class="blankslate"><h3>${heading}</h3></div></div>`);
    assert.equal(core.readDocument(doc, "mentions").count, 0);
  }
  for (const body of ['<div class="js-notifications-list"></div>', '<p>No notifications</p>', '<div class="blankslate"><h3>No notifications</h3></div>', '<div class="blankslate"><h3>Something went wrong</h3></div>']) {
    assert.equal(core.readDocument(page(body), "mentions").status, "error");
  }
});

test("current notifications-container blankslate is accepted only when visible", () => {
  assert.equal(core.readDocument(page('<div class="js-notifications-container"><div class="js-notifications-blankslate"><h2>All done</h2></div></div>'), "mentions").count, 0);
  for (const attr of ['hidden', 'aria-hidden="true"', 'style="display: none"', 'style="visibility: hidden"', 'class="d-none"']) {
    const doc = page(`<div class="js-notifications-container"><div ${attr}><div class="js-notifications-blankslate">All done</div></div></div>`);
    assert.equal(core.readDocument(doc, "mentions").status, "error");
  }
  assert.equal(core.readDocument(page('<div class="js-notifications-container"><div class="js-notifications-blankslate"></div><li class="notifications-list-item"></li></div>'), "mentions").count, 1);
});

test("all views require sign-in evidence and a supported kind", () => {
  for (const kind of core.KINDS) {
    assert.equal(core.readDocument(page(`<div class="js-notifications-list">${rows(1)}</div>`, ""), kind).status, "error");
    const doc = kind === "approved" ? search() : page(`<div class="js-notifications-list">${rows(1)}</div>`);
    doc.body.classList.add("logged-out");
    assert.equal(core.readDocument(doc, kind).status, "error");
  }
  assert.equal(core.readDocument(search(), "other").status, "error");
});
