// The Stack toggle (the user 2026-08-18): force the feed's one-column layout at ANY width, as a
// standing choice — not only when the container narrows past 540px. The pref drives a style()
// container condition on #feed-list, OR-combined with the existing size query, so the CSS block
// stays the single owner of what stacking means and the two triggers can never drift apart.
// Verified headless in the shipped Chromium: wide+forced stacks, wide+off doesn't, narrow always
// stacks. Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("one stacked block, two triggers: the narrow size query OR the forced style query", () => {
  assert.match(CSS, /@container \(max-width: 540px\) or style\(--romp-stack: on\) \{/);
  const count = (CSS.match(/^@container /gm) || []).length;
  assert.equal(count, 1, "still exactly one container query — no duplicated stacked rules");
});

test("the footer Stack button writes the pref and applies the style var; boot applies a persisted one", () => {
  assert.match(FEED, /stacked: s\.stacked === true/);
  assert.match(FEED, /ensureFeedToggle\("feed-stacked", "Stack", \(\) => feedPrefs\(\)\.stacked, "stacked"/);
  assert.match(FEED, /\.style\.setProperty\("--romp-stack", on \? "on" : "off"\)/);
  assert.match(FEED, /applyStacked\(feedPrefs\(\)\.stacked\);\s*\/\/ boot/);
  assert.match(FEED, /applyStacked\(p\.stacked\);/, "a settings change from any surface re-applies it");
  assert.match(FEED, /ensureStackToggle\(\)\.style\.display = showCA \? "" : "none";/, "docked in the footer row");
});
