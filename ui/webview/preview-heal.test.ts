// The managed image fetch heals from every failure mode the wire can produce (the user 2026-08-18,
// whose inline figures "never render until I send another message"): a refused status voids the
// resume state (the stale-Range 416 loop), a settled give-up chip still rides the push-heal (one
// attempt per NEW error, never one per push), and every tap acknowledges even when an attempt is
// already in flight. Source pins on ui/webview/preview.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const PREVIEW = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "preview.ts"), "utf8");

test("a refused status voids the resume state — the stale-Range 416 loop (2026-08-18)", () => {
  // an agent re-plotting the same filename SHRINKS the file; the kernel's 416 expects a clean
  // restart, but the client kept `got`, so every tap and heal replayed the same stale Range and
  // failed deterministically fast — while a send's freshly-minted box (got=0) rendered instantly
  const at = PREVIEW.indexOf("a refused status VOIDS the resume state");
  assert.ok(at > 0);
  const tail = PREVIEW.slice(at, at + 700);
  assert.ok(tail.indexOf("parts = []; got = 0;") > 0, "reset BEFORE the throw");
  assert.ok(tail.indexOf("throw new Error(why") > tail.indexOf("parts = []; got = 0;"));
});

test("a settled chip still rides the push-heal, one attempt per NEW error (2026-08-18)", () => {
  // only the retrying branch registered for the heal, so a spent budget dropped the box from the
  // map forever — "figures never render on their own, only when I send a message" (the send's tail
  // re-render minted a fresh box; the old one healed never)
  assert.match(PREVIEW, /let chipHealedErr: string \| null = null;/);
  assert.match(PREVIEW, /if \(lastErr !== chipHealedErr\) \{\s*\n\s*failedPreviews\.set\(box, \(\) => \{ chipHealedErr = lastErr; autoRetries = 1; build\(true\); \}\);/,
    "re-registers ONLY on new information — never one fetch per push for a dead figure");
});

test("every tap acknowledges, even mid-attempt when build() no-ops on its fetching guard", () => {
  assert.match(PREVIEW, /const ackTap = \(ev: Event\) => \{/);
  assert.match(PREVIEW, /autoRetries = 3; ackTap\(ev\); build\(true\); \};   \/\/ a tap re-arms persistence/);
  assert.equal((PREVIEW.match(/ackTap\(ev\); build\(true\)/g) || []).length, 2,
    "both the retrying note and the settled chip acknowledge");
});
