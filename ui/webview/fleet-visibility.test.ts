// The outline pane rebuilds only when it can be seen (2026-09-04). The dashboard shell keeps it in a
// display:none iframe by default, yet every feed push rebuilt its whole list on the main thread the chat
// pane's clicks share. Pinned at the source, like the other pane-wiring tests.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "fleet.ts"), "utf8");

test("render() defers while the list is off screen and paints once when it comes into view", () => {
  assert.match(SRC, /function render\(\) \{[\s\S]*?if \(!fleetWatching\) \{ fleetWatching = true; watchFleetVisibility\(list\); \}\n\s*if \(!fleetVisible\) \{ fleetDirty = true; return; \}/);
  assert.match(SRC, /new IntersectionObserver\(\(entries\) => \{\n\s*fleetVisible = entries\.some\(\(e\) => e\.isIntersecting\);\n\s*if \(fleetVisible && fleetDirty\) \{ fleetDirty = false; render\(\); \}/);
  assert.match(SRC, /if \(typeof IntersectionObserver === "undefined"\) return;/, "no observer → always render, as before");
  assert.match(SRC, /let fleetVisible = true;/, "visible until told otherwise: the first paint is never withheld");
});
