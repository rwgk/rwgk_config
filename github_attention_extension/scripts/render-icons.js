"use strict";

const {MAX_BADGE_COUNT} = require("../extension/core.js");

// Keep every packaged queue/state combination on the same small-icon layout.
// The checker compares the checked-in SVGs with this renderer byte-for-byte.
const KIND_GLYPHS = Object.freeze({
  reviews: "R",
  approved: "A",
  mentions: "M",
  notifications: "*",
});
const ICON_STATES = Object.freeze([
  "clear",
  "error",
  ...Array.from({length: MAX_BADGE_COUNT}, (_, i) => `count-${i + 1}`),
  "count-overflow",
]);

function renderIcon(kind, state) {
  const glyph = KIND_GLYPHS[kind];
  if (!glyph || !ICON_STATES.includes(state)) throw new Error(`Unknown icon: ${kind}-${state}`);
  const isCount = state.startsWith("count-");
  const overflow = state === "count-overflow";
  const badge = isCount ? (overflow ? "**" : state.slice("count-".length)) : "!";
  const wideBadge = isCount && badge.length > 1;
  const color = state === "clear" ? "#b7e4c7" : state === "error" ? "#d4a72c" : "#cf222e";
  // Preserve single-digit sizing; let two-character badges extend farther left.
  const layout = isCount
    ? { x: wideBadge ? 18.5 : 22, y: 10, radius: 9.1, fontSize: overflow ? 26 : 18.2, baseline: overflow ? 23 : 16.5 }
    : { x: 24, y: 8, radius: 7, fontSize: 14, baseline: 13 };
  const shape = wideBadge
    ? `<rect x="6" y="0.9" width="25" height="18.2" rx="9.1" fill="${color}" stroke="white" stroke-width="1.5"/>`
    : `<circle cx="${layout.x}" cy="${layout.y}" r="${layout.radius}" fill="${color}" stroke="white" stroke-width="1.5"/>`;
  const fontSize = glyph === "M" ? 23 : glyph === "*" ? 32 : 25;
  // An asterisk sits high above its font baseline; lower its visible bottom
  // to align with the letters without changing its horizontal placement.
  const baseline = glyph === "*" ? 38 : 28;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
  <rect x="1" y="2" width="29" height="29" rx="6" fill="#24292f" stroke="white" stroke-width="1"/>
  <text id="queue-glyph" x="11" y="${baseline}" font-family="Arial, Helvetica, sans-serif" font-size="${fontSize}" font-weight="700" text-anchor="middle" fill="white">${glyph}</text>
  ${shape}
${state === "clear" ? "" : `  <text id="badge-glyph" x="${layout.x}" y="${layout.baseline}" font-family="Arial, Helvetica, sans-serif" font-size="${layout.fontSize}" font-weight="700" text-anchor="middle" fill="${isCount ? "white" : "#24292f"}">${badge}</text>\n`}</svg>
`;
}

module.exports = {KIND_GLYPHS, ICON_STATES, renderIcon};
