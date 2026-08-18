// The day-context label rides above the rail's TOP stamp (the user 2026-08-17): whenever that
// stamp's day is not today, a small "Yesterday" / "3 days ago" sits just above it — anchored to the
// tracked turn's marker while it leads, to the first stamp below the line when nothing is tracked
// ("the next one"), and to the sticky's line once it takes over, so the handoff lands at the same
// pixel and scrolling never jumps. Source pins (dayContext's behavior is tested in time-marker.test.ts).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the label anchors to whichever stamp owns the top slot, and hands off at the same pixel", () => {
  assert.match(RENDER, /m\.dataset\.epoch = String\(epoch\);/, "markers carry their epoch for the day pass");
  assert.match(RENDER, /const anchorM = marker \|\| \(firstBelow \? firstBelow\[0\] : null\);/);
  assert.match(RENDER, /paintDay\(realLeads \? markerTop : \(firstBelow \? firstBelow\[1\] : cBottom \+ 1\)\);/,
    "a leading real stamp carries the label at its own position");
  assert.match(RENDER, /paintDay\(line\);/, "the sticky carries it at the line — same pixel at handoff");
  // BELOW the stamp, in the stamp's own box (the user 2026-08-17: floating it ABOVE bled into the
  // tab bar at the top of the view, and transform alignment didn't match the stamp's right edge)
  assert.match(RENDER, /const yTop = slotTop \+ stampH \+ 1;/);
  assert.match(RENDER, /day\.style\.width = gRect!\.width \+ "px";/);
  assert.match(RENDER, /if \(day\.textContent !== label\) day\.textContent = label;/, "text swaps only at day boundaries");
  assert.match(RENDER, /if \(!label \|\| yTop > cBottom\) \{ day\.style\.display = "none"; return; \}/,
    "today → no label; an off-screen anchor paints nothing");
});

test("the label is passive fixed chrome, right-aligned to the gutter with no width math", () => {
  assert.match(CSS, /\.rail-day \{\s*\n\s*position: fixed; z-index: 3; pointer-events: none; white-space: nowrap;/);
  assert.match(CSS, /text-align: right;/);
  assert.match(CSS, /font-size: 0\.68em; letter-spacing: 0\.03em; color: var\(--dim\); opacity: 0\.85;/,
    "context, not the time itself — smaller and dimmer than the stamp");
});
