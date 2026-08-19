// Drag-to-resize the chat tab strip (the user 2026-08-18): #tabbar wraps its session tabs into rows
// and scrolls past a max-height cap, which clipped the fifth row of a many-session strip with no way
// to see more. A #tabbar-resize grip straddles the strip's bottom border — drag DOWN for more rows,
// UP for fewer, double-click to reset — and the strip stays a scroll pane at every size. The dragged
// cap is per-viewer arrangement (localStorage, like romp:vieworder). Executed tests on the pure
// module + source pins for the wiring (no jsdom for the chat renderer).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { TABBAR_H_KEY, TABBAR_H_DEFAULT, TABBAR_H_MIN, clampTabbarH, parseTabbarH } from "../../ui/webview/tabbar-resize";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const SKELETON = fs.readFileSync(path.resolve(process.cwd(), "src", "page-skeleton.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("executed: the cap clamps to [one row, 60% of the window], and never below the CSS default's reach", () => {
  assert.equal(clampTabbarH(10, 900), TABBAR_H_MIN, "at least one tab row stays visible");
  assert.equal(clampTabbarH(-50, 900), TABBAR_H_MIN);
  assert.equal(clampTabbarH(300, 900), 300, "inside the bounds a drag lands where it dragged");
  assert.equal(clampTabbarH(163.4, 900), 163, "whole pixels");
  assert.equal(clampTabbarH(5000, 900), 540, "at most 60% of the window");
  assert.equal(clampTabbarH(150, 100), TABBAR_H_DEFAULT,
    "a tiny window never clamps a stored height below the CSS default's own reach");
});

test("executed: a stored height parses to a positive number or null — never NaN into layout", () => {
  assert.equal(parseTabbarH(null), null, "never dragged");
  assert.equal(parseTabbarH(""), null, "reset");
  assert.equal(parseTabbarH("garbage"), null);
  assert.equal(parseTabbarH("-12"), null);
  assert.equal(parseTabbarH("0"), null);
  assert.equal(parseTabbarH("210"), 210);
});

test("the grip is a sibling BELOW the bar in both page skeletons — a child would scroll away with the rows", () => {
  assert.match(SKELETON, /<div id="tabbar"><span id="tabs"><\/span><\/div>\s*\n\s*<div id="tabbar-resize"[^>]*><\/div>/);
  assert.match(KERNEL, /<div id="tabbar"><span id="tabs"><\/span><\/div>'\s*\n\s*'<div id="tabbar-resize"/);
  // the mobile page replaces the strip with its own header — nothing there to resize
  assert.match(KERNEL, /"#tabbar-resize\{display:none\}"/);
});

test("the CSS keeps the strip a scroll pane and the grip layout-free, wearing the composer grip's affordance", () => {
  // the cap rides a VAR, never an inline max-height: the mobile page's #tabbar{max-height:none}
  // must keep winning, or a stored desktop cap clips the mobile header after a rotation
  assert.match(CSS, /#tabbar \{[^}]*max-height: var\(--tabbar-cap, 150px\); overflow-y: auto;/s,
    "the cap the grip drags; scroll stays; the mobile rule can still zero it out");
  assert.match(CSS, /#tabbar-resize \{[^}]*height: 7px; margin: -3px 0 -4px;[^}]*\n[^}]*cursor: ns-resize;/s,
    "negative margins cancel the 7px grab zone — zero layout height, and only 2px over the last tab row");
  assert.match(CSS, /#tabbar-resize:hover::after, #tabbar-resize\.dragging::after \{ background: var\(--accent\); width: 52px; \}/,
    "same hover/drag brightening as #composer-resize");
});

test("the cap is applied at boot and re-clamped on every window resize, via the var", () => {
  const at = RENDER.indexOf("drag-to-resize the tab strip");
  const end = RENDER.indexOf("// Keep the rendered window over the viewport");
  assert.ok(at > 0 && end > at, "the wiring block exists and the end anchor follows it");
  const fn = RENDER.slice(at, end);
  // one ORDERED block: read → applyCap defined on the var → applied → re-applied on resize. The
  // apply itself is pinned (not just the read): deleting the apply left the suite green once.
  assert.match(fn, new RegExp(
    String.raw`let cap = parseTabbarH\(localStorage\.getItem\(TABBAR_H_KEY\)\);[\s\S]*?` +
    String.raw`bar\.style\.setProperty\("--tabbar-cap", clampTabbarH\(cap, window\.innerHeight\) \+ "px"\);[\s\S]*?` +
    String.raw`applyCap\(\);\n\s*window\.addEventListener\("resize", applyCap\);`),
    "boot applies the stored cap and a window resize re-clamps it");
  assert.match(fn, /bar\.style\.removeProperty\("--tabbar-cap"\);/, "a reset removes the var — the CSS default returns");
});

test("dragging moves the EFFECTIVE cap, guarded and captured, persists on release, dblclick resets", () => {
  const at = RENDER.indexOf("drag-to-resize the tab strip");
  const fn = RENDER.slice(at, RENDER.indexOf("// Keep the rendered window over the viewport"));
  assert.match(fn, /if \(e\.button !== 0\) return;/, "a right-click opens menus, never a drag");
  assert.match(fn, /if \(!\(e\.buttons & 1\)\) \{ onUp\(\); return; \}/,
    "a swallowed release ends the drag — no phantom resize, no stuck cursor");
  assert.match(fn, /try \{ grip\.setPointerCapture\(pid\); \} catch/,
    "the drag survives leaving the pane — and a failed capture never aborts the setup");
  assert.match(fn, /grip\.releasePointerCapture\(pid\);/, "and the capture never outlives it");
  assert.match(fn, /startH = cap != null \? clampTabbarH\(cap, window\.innerHeight\) : TABBAR_H_DEFAULT;/,
    "anchored to the EFFECTIVE cap — anchoring to the rendered height silently collapsed a larger stored cap");
  assert.match(fn, /cap = clampTabbarH\(startH \+ \(e\.clientY - startY\), window\.innerHeight\);/,
    "drag DOWN grows — the cap follows the pointer");
  assert.match(fn, /stick = !!content && nearBottom\(content\);/, "a tail-following transcript is noted at drag start…");
  assert.match(fn, /if \(stick && content\) content\.scrollTop = content\.scrollHeight;/,
    "…and held on the tail while the bar grows over it");
  assert.match(fn, /if \(cap != null\) localStorage\.setItem\(TABBAR_H_KEY, String\(cap\)\);/,
    "persisted on release, not per-move");
  assert.match(fn, /grip\.addEventListener\("dblclick", \(\) => \{ cap = null; applyCap\(\); localStorage\.removeItem\(TABBAR_H_KEY\); \}\)/,
    "double-click resets to the CSS default");
});
