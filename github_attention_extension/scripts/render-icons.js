"use strict";

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
  ...Array.from({length: 9}, (_, i) => `count-${i + 1}`),
  "count-x",
]);

function renderIcon(kind, state) {
  const glyph = KIND_GLYPHS[kind];
  if (!glyph || !ICON_STATES.includes(state)) throw new Error(`Unknown icon: ${kind}-${state}`);
  const isCount = state.startsWith("count-");
  const badge = isCount ? (state === "count-x" ? "X" : state.slice("count-".length)) : "!";
  const color = state === "clear" ? "#b7e4c7" : state === "error" ? "#d4a72c" : "#cf222e";
  const fontSize = glyph === "M" ? 23 : glyph === "*" ? 32 : 25;
  const baseline = glyph === "*" ? 33 : 28;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
  <rect x="1" y="2" width="29" height="29" rx="6" fill="#24292f" stroke="white" stroke-width="1"/>
  <text id="queue-glyph" x="11" y="${baseline}" font-family="Arial, Helvetica, sans-serif" font-size="${fontSize}" font-weight="700" text-anchor="middle" fill="white">${glyph}</text>
  <circle cx="24" cy="8" r="7" fill="${color}" stroke="white" stroke-width="1.5"/>
${state === "clear" ? "" : `  <text id="badge-glyph" x="24" y="13" font-family="Arial, Helvetica, sans-serif" font-size="14" font-weight="700" text-anchor="middle" fill="${isCount ? "white" : "#24292f"}">${badge}</text>\n`}</svg>
`;
}

module.exports = {KIND_GLYPHS, ICON_STATES, renderIcon};
