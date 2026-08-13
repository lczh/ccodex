// The chat side of the closed-pane jump (the user 2026-08-13): when a feed click's focus is what un-hid
// the pane, the shell's reveal lands one task AFTER revealSelfPane's postMessage — so the synchronous
// live-tail scroll ran at display:none (scrollHeight 0) and the jump read as a no-op. The live branch
// defers one frame; anchored jumps need nothing (landOn's ResizeObserver realign re-lands them when the
// pane sizes in). Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("focus reveals the pane first, and the live-tail scroll waits one frame for real layout", () => {
  assert.match(RENDER, /revealSelfPane\(\);\s+\/\/ every focus is someone jumping HERE/);
  assert.match(RENDER, /window\.requestAnimationFrame\(\(\) => \{\s*\n\s*const c = document\.getElementById\("content"\); if \(c\) c\.scrollTop = c\.scrollHeight;\s*\n\s*\}\);/,
    "one frame later, not now — the un-hide lands a task after the postMessage");
});
