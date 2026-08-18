// A thin blue notch on the chat's right scroll edge for every USER message (the user 2026-08-17) —
// the conversation's shape at a glance, overview-ruler style. Proportional positions (scroll-
// invariant), painted by the rail-sticky scheduler with a signature skip so pure scrolls do no DOM
// work; passive fixed chrome that never blocks the native scrollbar; gestures (command rows, the
// Continue row) draw no notch — those are doings, not words. Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("one notch per real user message across the WHOLE loaded conversation", () => {
  // the user 2026-08-17: the chat virtualizes (a rendered window between two estimated-height
  // spacers), and window-only notches "forgot" the newer messages when scrolled back. Notches now
  // come from the full resident events array: true pixels for rendered turns, a proportional slot
  // inside the MEASURED spacer for the rest — the same estimate the scrollbar itself stands on.
  assert.match(RENDER, /for \(let i = 0; i < s\.events\.length; i\+\+\) \{/);
  assert.match(RENDER, /if \(ev\.kind !== "user" \|\| !ev\.human \|\| ev\.romp \|\| ev\.rompAuto\) continue;/);
  assert.match(RENDER, /ev\.canned === "continue" \|\| SLASH_CMD_RE\.test\(md\)/,
    "a /command or Continue gesture is a doing, not words — no notch");
  assert.match(RENDER, /'\.turn\[data-unit="' \+ i \+ '"\]'/, "rendered events use their true pixel offset");
  // spacer slots feed on CACHED measured heights (the user 2026-08-17, video: uniform-average
  // slots made notches wiggle as messages crossed the render-window boundary and corrected to
  // truth) — cumulative cached heights, normalized to the spacer's actual height, one prefix-sum
  // pass per spacer, O(1) per notch
  assert.match(RENDER, /const unitHeights = new Map<string, Map<number, number>>\(\);/);
  assert.match(RENDER, /if \(Number\.isFinite\(u\) && h > 0\) uh\.set\(u, h\);/, "every rendered unit's height is remembered");
  assert.match(RENDER, /off = slotIn\(topPre, i, topH\);/, "top-spacer events slot by cumulative cached heights");
  assert.match(RENDER, /off = \(sh - botH\) \+ slotIn\(botPre, i - winEnd, botH\);/,
    "bottom-spacer events too — normalized to the spacer the scrollbar stands on");
});

test("a history load rescales the map smoothly — moved notches are carried, never teleported", () => {
  // the user 2026-08-17: scrolling back streams older history in; the scroller's world grows and
  // every proportional position compresses (the native thumb does the same). Rebuilt nodes can't
  // transition, so same-count updates move the EXISTING nodes and CSS carries them.
  assert.match(RENDER, /if \(kids\.length === ys\.length\) \{\s*\n\s*ys\.forEach\(\(y, i\) => \{ kids\[i\]\.style\.top = y \+ "px"; \}\);/);
  assert.match(CSS, /transition: top 180ms ease;/);
  assert.match(CSS, /prefers-reduced-motion: reduce\) \{ \.scroll-marks \.scroll-mark \{ transition: none; \} \}/);
});

test("positions are proportional and pure scrolls do no DOM work", () => {
  assert.match(RENDER, /node\.getBoundingClientRect\(\)\.top - cRect\.top \+ scrollTop/);
  assert.match(RENDER, /if \(sig !== scrollMarksSig\) \{/, "signature skip: rebuild only on real change");
  assert.match(RENDER, /paintRailSticky\(\); paintScrollMarks\(\);/, "rides the existing rAF scheduler");
});

test("passive chrome in the user's own blue", () => {
  assert.match(CSS, /\.scroll-marks \{ position: fixed; z-index: 3; pointer-events: none; width: 12px; \}/);
  assert.match(CSS, /background: #2b6cef; opacity: 0\.65;/, "the outgoing-bubble blue, never the romp accent");
});
