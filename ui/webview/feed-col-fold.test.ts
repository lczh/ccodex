// Stacked-layout column controls (the user 2026-08-16): a caret LEFT of each category chip folds the
// whole section to its header, and a hover-revealed grip drags the section to a new slot in the
// stack. Both live on the build-once headers (click-safe across the feed's constant re-renders),
// both persist with the view state, and both exist ONLY in the stacked layout — the side-by-side
// layout hides them and ignores the dragged order entirely. Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("the caret folds a category to its header and persists like every other disclosure", () => {
  assert.match(FEED, /const fold = el\("button", "fcol-fold"\);/);
  assert.match(FEED, /if \(collapsedCols\.has\(key\)\) collapsedCols\.delete\(key\); else collapsedCols\.add\(key\);/);
  assert.match(FEED, /cols: \[\.\.\.collapsedCols\],\s*\n\s*order: stackOrder\.slice\(\)/, "rides the persisted view state");
  // The fold BITES only in the stacked layout (the user 2026-08-18): collapsed while stacked, then
  // widened to three columns, the section stayed hidden with no caret to reopen it. The rule must
  // live INSIDE the stacked container query — side by side, every card always shows.
  const stacked = CSS.slice(CSS.indexOf("@container (max-width: 540px) or style(--romp-stack: on)"));
  assert.match(stacked, /\.feed-col\.col-collapsed \.feed-col-list \{ display: none; \}/,
    "the collapse rule is scoped to the stacked layout");
  assert.doesNotMatch(CSS.slice(0, CSS.indexOf("@container (max-width: 540px) or style(--romp-stack: on)")),
    /\.col-collapsed \.feed-col-list/,
    "and no unscoped copy survives to hide cards side-by-side");
  assert.match(FEED, /fold\.textContent = folded \? "▸" : "▾";/);
  // consistency with the session headers' fold (the user 2026-08-16): same side — caret RIGHT of the
  // label — and the same rendered size (the header's 0.72em compensated back to the feed's base)
  assert.match(FEED, /head\.append\(name, fold, count\);/);
  assert.match(CSS, /\.fcol-fold \{ display: inline-block; flex: none; padding: 0 5px; margin-left: -2px;[^}]*font-size: 1\.389em;/);
});

test("the chip itself drags — grab cursor as the affordance, live provisional movement, stacked only", () => {
  // the user 2026-08-16, dropping the earlier grip handle: grabbing the Working/Blocked/Completed
  // chip moves the section, the grabbed section follows the pointer, and the displaced sections
  // FLIP-animate into their provisional slots — what you see mid-drag is what you get on drop.
  assert.match(CSS, /\.feed-col-head \.fcol-chip \{ cursor: grab; touch-action: none; user-select: none; \}/);
  assert.match(CSS, /\.feed-col\.col-dragging \{ position: relative; z-index: 5;/, "lifted over siblings while following");
  assert.match(CSS, /\.feed-col\.col-completed\s+\{ order: var\(--stack-order, 1\); \}/,
    "the dragged order overrides the stacked default and only there");
  assert.match(FEED, /wireColDrag\(name, col, key\);/, "no separate handle — the chip is the drag surface");
  assert.doesNotMatch(FEED, /fcol-grip/, "the grip is gone");
  assert.match(FEED, /if \(!colsEl \|\| getComputedStyle\(colsEl\)\.flexDirection !== "column"\) return;/,
    "side by side, the chip never captures the pointer");
  assert.match(FEED, /chip\.setPointerCapture\(down\.pointerId\);/, "the drag survives leaving the chip");
  assert.match(FEED, /col\.style\.transform = "translateY\(" \+ \(ev\.clientY - startY - slotShift\) \+ "px\)";/,
    "the grabbed section follows the pointer");
  assert.match(FEED, /e\.animate\(\[\{ transform: "translateY\(" \+ d \+ "px\)" \}, \{ transform: "translateY\(0\)" \}\]/,
    "displaced sections FLIP into their provisional slots");
  assert.match(FEED, /\{ slotShift -= d; continue; \}/, "a re-slot never yanks the section out from under the pointer");
  assert.match(FEED, /chip\.addEventListener\("pointercancel", up\);/, "a cancelled drag still settles + persists");
});

test("both controls live on the build-once header — click-safe across re-renders", () => {
  const build = FEED.slice(FEED.indexOf("function ensureCols"), FEED.indexOf("return {", FEED.indexOf("function ensureCols")));
  assert.ok(build.includes('el("button", "fcol-fold")') && build.includes("wireColDrag(name, col, key)"),
    "wired inside ensureCols' one-time scaffold, never in a render loop");
});

test("the Stack toggle says so when narrow width already forces stacking (2026-08-19)", () => {
  // at or under the container query's 540px the layout stacks regardless of the pref, so the
  // toggle is a no-op there: faded (aria-disabled), click no-ops, tooltip names the way out.
  // The width constant must match the CSS query — the two stacking owners cannot drift.
  assert.match(FEED, /const STACK_FORCED_W = 540;/);
  assert.match(CSS, /@container \(max-width: 540px\) or style\(--romp-stack: on\)/);
  assert.match(FEED, /b\.classList\.toggle\("forced", forced\);/);
  assert.match(FEED, /widen the feed to unstack into three columns/);
  assert.match(FEED, /if \(b\.classList\.contains\("forced"\)\) return;/,
    "keyboard activation bypasses pointer-events, so the handler itself no-ops");
  assert.match(FEED, /new ResizeObserver\(\(\) => refreshStackForced\(b\)\)/,
    "width changes are the event — never a poll");
  assert.match(CSS, /#feed-stacked\.forced \{ opacity: 0\.4; cursor: default; \}/);
});
