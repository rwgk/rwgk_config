"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {parseHTML} = require("linkedom");
const core = require("../extension/core.js");

const page = (body, login = "example-user") => parseHTML(`<!doctype html><html><head><meta name="user-login" content="${login}"></head><body><main>${body}</main></body></html>`).document;
const search = (overrides = {}, legacy = false) => {
  const payload = {type: "pullrequests", logged_in: true, result_count: 1, results: [{}], errors: [], ...overrides};
  return page(`<react-app app-name="blackbird-search" initial-path="${core.URLS.reviews.replaceAll("&", "&amp;")}"><script data-target="react-app.embeddedData">${JSON.stringify({payload: legacy ? payload : {blackbirdSearchRoute: payload}})}</script></react-app>`);
};

test("matches only the two exact semantic queues, including reordered query tokens", () => {
  assert.equal(core.classifyUrl(core.URLS.reviews), "reviews");
  assert.equal(core.classifyUrl(core.URLS.mentions), "mentions");
  assert.equal(core.classifyUrl("https://github.com/search?type=pullrequests&q=user-review-requested%3A%40me+is%3Aopen+is%3Apr"), "reviews");
  assert.equal(core.classifyUrl("https://github.com/notifications?query=reason%3Amention+is%3Aunread"), "mentions");
  for (const url of ["https://evil.example/notifications?query=is:unread+reason:mention", "http://github.com/notifications?query=is:unread+reason:mention", "https://github.com/notifications", "https://github.com/search?q=is:pr+is:open+review-requested:@me&type=pullrequests", `${core.URLS.reviews}&q=is:issue`, `${core.URLS.mentions}&page=2`, `${core.URLS.mentions}&after=cursor`, `${core.URLS.reviews.replace("is%3Apr", "is%3Aissue")}`, "not a URL"]) assert.equal(core.classifyUrl(url), null, url);
});

test("search uses validated result_count in the current and legacy payload", () => {
  assert.equal(core.readDocument(search({result_count: 37}), "reviews").count, 37);
  assert.equal(core.readDocument(search({result_count: 2}, true), "reviews").count, 2);
  assert.deepEqual(core.readDocument(search({result_count: 0, results: []}), "reviews"), {status: "ok", count: 0, message: ""});
});

test("search fails closed for errors, invalid counts, inconsistent rows, and logged out state", () => {
  for (const overrides of [{errors: ["rate limit"]}, {errors: undefined}, {result_count: "0"}, {result_count: -1}, {result_count: 0, results: [{}]}, {result_count: 2, results: []}, {logged_in: false}, {type: "issues"}, {warn_limited_results: true}]) {
    assert.equal(core.readDocument(search(overrides), "reviews").status, "error");
  }
  assert.equal(core.readDocument(page("", ""), "reviews").status, "error");
  assert.equal(core.readDocument(page('<react-app app-name="blackbird-search"><script data-target="react-app.embeddedData">{</script></react-app>'), "reviews").status, "error");
});

test("stale search payload from a different SPA query cannot report clear", () => {
  const doc = search({result_count: 0, results: []});
  doc.querySelector("react-app").setAttribute("initial-path", "/search?q=is:pr+is:open+repo:example/repo&type=pullrequests");
  assert.equal(core.readDocument(doc, "reviews").status, "error");
  doc.querySelector("react-app").removeAttribute("initial-path");
  assert.equal(core.readDocument(doc, "reviews").status, "error");
});

test("later pages, duplicate pagination, and unknown filters are never watched", () => {
  for (const suffix of ["&page=1&page=2", "&page=1&page=1", "&p=2", "&p=1&p=2", "&repo=example/project", "&all=1"]) {
    assert.equal(core.classifyUrl(core.URLS.reviews + suffix), null);
    assert.equal(core.classifyUrl(core.URLS.mentions + suffix), null);
  }
  assert.equal(core.classifyUrl(core.URLS.reviews + "&page=1"), "reviews");
  assert.equal(core.classifyUrl(core.URLS.reviews + "&p=1"), "reviews");
});

test("notifications count rows once, not the global unread count", () => {
  const doc = page('<div class="js-notifications-list"><li class="notifications-list-item js-notification"></li><li class="notifications-list-item"></li></div><span class="notification-indicator">999</span>');
  assert.equal(core.readDocument(doc, "mentions").count, 2);
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
  assert.equal(core.readDocument(page('<div class="js-notifications-list"><li class="js-notification"></li></div>', ""), "mentions").status, "error");
  const doc = search(); doc.body.classList.add("logged-out");
  assert.equal(core.readDocument(doc, "reviews").status, "error");
  assert.equal(core.readDocument(search(), "other").status, "error");
});
