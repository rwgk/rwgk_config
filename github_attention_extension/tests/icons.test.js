"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {parseHTML} = require("linkedom");
const {renderIcon} = require("../scripts/render-icons.js");

const icon = (kind, state) => parseHTML(renderIcon(kind, state)).document.querySelector("svg");
const number = (element, name) => Number(element.getAttribute(name));

function assertFits(shape) {
  const stroke = number(shape, "stroke-width") / 2;
  const circle = shape.tagName.toLowerCase() === "circle";
  const x = circle ? number(shape, "cx") - number(shape, "r") : number(shape, "x");
  const y = circle ? number(shape, "cy") - number(shape, "r") : number(shape, "y");
  const width = circle ? 2 * number(shape, "r") : number(shape, "width");
  const height = circle ? 2 * number(shape, "r") : number(shape, "height");
  assert.ok(x - stroke >= 0 && y - stroke >= 0, "Badge border must fit inside the top-left corner");
  assert.ok(x + width + stroke <= 32 && y + height + stroke <= 32, "Badge border must fit inside the bottom-right corner");
}

test("single-digit badges retain their established size and position", () => {
  const svg = icon("notifications", "count-1");
  const circle = svg.querySelector("circle");
  assert.equal(number(circle, "cx"), 22);
  assert.equal(number(circle, "cy"), 10);
  assert.equal(number(circle, "r"), 9.1);
  assert.equal(circle.getAttribute("fill"), "#cf222e");
  assert.equal(number(svg.querySelector("#badge-glyph"), "font-size"), 18.2);
  assertFits(circle);
});

test("two-digit badges widen without shrinking the digits or clipping", () => {
  for (const count of [10, 25]) {
    const svg = icon("reviews", `count-${count}`);
    const pill = svg.querySelector('rect[fill="#cf222e"]');
    const text = svg.querySelector("#badge-glyph");
    assert.ok(pill);
    assert.equal(number(pill, "width"), 25);
    assert.equal(text.textContent, String(count));
    assert.equal(number(text, "font-size"), 18.2);
    assert.equal(number(text, "x"), number(pill, "x") + number(pill, "width") / 2);
    assertFits(pill);
  }
});

test("overflow uses a double asterisk inside the wider red badge", () => {
  const svg = icon("approved", "count-overflow");
  assert.equal(svg.querySelector("#badge-glyph").textContent, "**");
  assertFits(svg.querySelector('rect[fill="#cf222e"]'));
});

test("green and amber indicators keep their original small-circle geometry", () => {
  for (const state of ["clear", "error"]) {
    const svg = icon("mentions", state);
    const circle = svg.querySelector("circle");
    assert.equal(number(circle, "cx"), 24);
    assert.equal(number(circle, "cy"), 8);
    assert.equal(number(circle, "r"), 7);
    assert.equal(circle.getAttribute("fill"), state === "clear" ? "#b7e4c7" : "#d4a72c");
    assert.equal(svg.querySelector("#badge-glyph")?.textContent ?? null, state === "clear" ? null : "!");
    assertFits(circle);
  }
});

test("queue characters retain their horizontal placement and lowered asterisk", () => {
  for (const [kind, glyph] of Object.entries({reviews: "R", approved: "A", mentions: "M", notifications: "*"})) {
    const text = icon(kind, "count-25").querySelector("#queue-glyph");
    assert.equal(text.textContent, glyph);
    assert.equal(number(text, "x"), 11);
    assert.equal(number(text, "y"), glyph === "*" ? 38 : 28);
  }
});
