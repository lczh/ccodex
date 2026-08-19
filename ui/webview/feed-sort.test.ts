// Feed card sort order: oldest-at-top by DEFAULT (the user 2026-06-27) — the newest work sits at the BOTTOM
// of each column, and new/moved cards stack onto the bottom. A footer "Newest first" toggle (default OFF,
// the user 2026-07-07) reverses each column. The old ⛭ "Oldest first" gear checkbox stays gone. Source-pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "bin", "romp-kernel"), "utf8");

test("each column sorts by modified time; the footer 'Modified ↑/↓' control reverses the direction", () => {
  assert.match(FEED, /const newestFirst = feedPrefs\(\)\.newestFirst;/);
  assert.match(FEED, /buckets\[k\]\.sort\(\(x, y\) => newestFirst \? y\.t - x\.t : x\.t - y\.t\)/);
  // renamed from the "Newest first" on/off toggle (the user 2026-08-18): the arrow IS the state —
  // ↓ newest at the top, ↑ oldest at the top — so the button drops the pressed accent
  assert.match(FEED, /ensureFeedToggle\("feed-newestfirst", "Modified", \(\) => feedPrefs\(\)\.newestFirst, "newestFirst"/);
  assert.match(FEED, /b\.textContent = "Modified " \+ \(feedPrefs\(\)\.newestFirst \? "\\u2193" : "\\u2191"\);/);
  assert.match(FEED, /b\.classList\.remove\("on"\);/);
  assert.doesNotMatch(FEED, /oldestFirst/, "no oldestFirst pref — the natural order is oldest-first");
});

test("the Collapsed default lives in the settings modal now; flipping it still drops per-card overrides", () => {
  // moved off the footer (the user 2026-08-18): a set-and-forget default, not a per-glance action.
  // The gear writes romp:settings.collapsed; the feed's settings watcher drops the per-card section
  // overrides whenever the pref CHANGES — whichever surface changed it — so every card re-flows.
  assert.doesNotMatch(FEED, /ensureFeedToggle\("feed-collapsed"/, "no footer Collapsed button survives");
  assert.match(FEED, /if \(p\.collapsed !== lastCollapsedPref\) \{ lastCollapsedPref = p\.collapsed; secChoice\.clear\(\); \}/);
  const GEAR = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "gear.js"), "utf8");
  assert.match(GEAR, /rs-feedcollapsed/);
  assert.match(GEAR, /s\.collapsed = fc\.checked; save\(s\);/);
  assert.match(GEAR, /if \(fc\) fc\.checked = s\.collapsed === true;/);
  assert.doesNotMatch(FEED, /anySectionOpen/, "the old Collapse/Expand-all button is gone");
});

test("the ⛭ gear no longer has an 'Oldest first' checkbox", () => {
  assert.doesNotMatch(KERNEL, /rs-oldest/);
  assert.doesNotMatch(KERNEL, /Oldest first/);
  assert.doesNotMatch(KERNEL, /oldestFirst/, "no leftover wiring in the gear JS/defaults");
});
