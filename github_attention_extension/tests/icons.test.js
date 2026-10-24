"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {parseHTML} = require("linkedom");
const {ICON_STATES, renderIcon} = require("../scripts/render-icons.js");

const QUEUES = {reviews: "R", approved: "A", mentions: "M", notifications: "*"};
const COUNTS = [...Array.from({length: 9}, (_, index) => String(index + 1)), "X"];

function attributes(element) {
  assert.ok(element, "The expected SVG element must exist");
  return Object.fromEntries([...element.attributes].map(({name, value}) => [name, value]));
}

function assertBaseLayout(svg, glyph) {
  assert.equal(svg.getAttribute("width"), "32");
  assert.equal(svg.getAttribute("height"), "32");
  assert.equal(svg.getAttribute("viewBox"), "0 0 32 32");
  assert.deepEqual(attributes(svg.querySelector("rect")), {
    x: "1", y: "2", width: "29", height: "29", rx: "6", fill: "#24292f", stroke: "white", "stroke-width": "1",
  });
  const queue = svg.querySelector("#queue-glyph");
  assert.equal(queue.textContent, glyph);
  assert.deepEqual(attributes(queue), {
    id: "queue-glyph", x: "11", y: glyph === "*" ? "38" : "28",
    "font-family": "Arial, Helvetica, sans-serif", "font-size": glyph === "M" ? "23" : glyph === "*" ? "32" : "25",
    "font-weight": "700", "text-anchor": "middle", fill: "white",
  });
}

function assertClose(actual, expected) {
  assert.ok(Math.abs(actual - expected) < 1e-12, `${actual} should equal ${expected} within floating-point precision`);
}

for (const [kind, glyph] of Object.entries(QUEUES)) {
  for (const count of COUNTS) {
    test(`${kind} count ${count}: the red badge and number are 1.3x larger without clipping or changing the queue glyph`, () => {
      const state = count === "X" ? "count-x" : `count-${count}`;
      const svg = parseHTML(renderIcon(kind, state)).document.querySelector("svg");
      assertBaseLayout(svg, glyph);
      const circle = svg.querySelector("circle");
      assert.deepEqual(attributes(circle), {
        cx: "22", cy: "10", r: "9.1", fill: "#cf222e", stroke: "white", "stroke-width": "1.5",
      });
      const badge = svg.querySelector("#badge-glyph");
      assert.equal(badge.textContent, count);
      assert.deepEqual(attributes(badge), {
        id: "badge-glyph", x: "22", y: "16.5", "font-family": "Arial, Helvetica, sans-serif",
        "font-size": "18.2", "font-weight": "700", "text-anchor": "middle", fill: "white",
      });
      assertClose(Number(circle.getAttribute("r")) / 7, 1.3);
      assertClose(Number(badge.getAttribute("font-size")) / 14, 1.3);
      assert.equal(badge.getAttribute("x"), circle.getAttribute("cx"), "Count text stays horizontally centered on its badge");
      const extent = Number(circle.getAttribute("r")) + Number(circle.getAttribute("stroke-width")) / 2;
      for (const axis of ["cx", "cy"]) {
        const center = Number(circle.getAttribute(axis));
        assert.ok(center - extent >= 0, `${axis}: the badge border must not clip at the minimum edge`);
        assert.ok(center + extent <= 32, `${axis}: the badge border must not clip at the maximum edge`);
      }
      const baseline = Number(badge.getAttribute("y"));
      assert.ok(baseline > Number(circle.getAttribute("cy")));
      assert.ok(baseline < Number(circle.getAttribute("cy")) + Number(circle.getAttribute("r")));
    });
  }

  for (const state of ["clear", "error"]) {
    test(`${kind} ${state}: the green or amber badge retains its original geometry`, () => {
      const svg = parseHTML(renderIcon(kind, state)).document.querySelector("svg");
      assertBaseLayout(svg, glyph);
      assert.deepEqual(attributes(svg.querySelector("circle")), {
        cx: "24", cy: "8", r: "7", fill: state === "clear" ? "#b7e4c7" : "#d4a72c", stroke: "white", "stroke-width": "1.5",
      });
      const badge = svg.querySelector("#badge-glyph");
      if (state === "clear") {
        assert.equal(badge, null);
      } else {
        assert.equal(badge.textContent, "!");
        assert.deepEqual(attributes(badge), {
          id: "badge-glyph", x: "24", y: "13", "font-family": "Arial, Helvetica, sans-serif",
          "font-size": "14", "font-weight": "700", "text-anchor": "middle", fill: "#24292f",
        });
      }
    });
  }
}

test("all twelve notification variants lower only the asterisk, without shifting letters or badges", () => {
  assert.equal(ICON_STATES.length, 12);
  for (const state of ICON_STATES) {
    const notification = parseHTML(renderIcon("notifications", state)).document.querySelector("svg");
    const star = notification.querySelector("#queue-glyph");
    assert.equal(star.textContent, "*");
    assert.equal(star.getAttribute("y"), "38");
    assert.equal(star.getAttribute("x"), "11");
    assert.equal(star.getAttribute("font-size"), "32");
    for (const kind of ["reviews", "approved", "mentions"]) {
      const letterIcon = parseHTML(renderIcon(kind, state)).document.querySelector("svg");
      assert.equal(letterIcon.querySelector("#queue-glyph").getAttribute("y"), "28");
      assert.deepEqual(attributes(notification.querySelector("rect")), attributes(letterIcon.querySelector("rect")));
      assert.deepEqual(attributes(notification.querySelector("circle")), attributes(letterIcon.querySelector("circle")));
      const notificationBadge = notification.querySelector("#badge-glyph");
      const letterBadge = letterIcon.querySelector("#badge-glyph");
      if (state === "clear") {
        assert.equal(notificationBadge, null);
        assert.equal(letterBadge, null);
      } else {
        assert.deepEqual(attributes(notificationBadge), attributes(letterBadge));
        assert.equal(notificationBadge.textContent, letterBadge.textContent);
      }
    }
  }
});
