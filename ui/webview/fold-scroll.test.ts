// Inner fold scroll positions survive the per-push rebuild (the user 2026-08-14: scrolling a
// CLAUDE.md doc inside the expanded System context card snapped back to the top "randomly" — i.e.
// on every kernel push, since the first 25 turns rebuild and a fresh .fold-pre starts at scrollTop
// 0). keepScroll mirrors openFolds: saved per stable key on scroll, reapplied a frame after the
// rebuilt node lands (an unlaid-out node clamps scrollTop writes to 0). Source pins (no jsdom).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("keepScroll: keyed map, passive save on scroll, rAF restore after rebuild", () => {
  assert.match(RENDER, /const foldScroll = new Map<string, number>\(\);/);
  assert.match(RENDER, /box\.addEventListener\("scroll", \(\) => \{ foldScroll\.set\(key, box\.scrollTop\); \}, \{ passive: true \}\);/);
  // the restore waits a frame — writing scrollTop to a node that hasn't laid out clamps to 0
  assert.match(RENDER, /if \(saved\) requestAnimationFrame\(\(\) => \{ box\.scrollTop = saved; \}\);/);
  // keyless callers stay transient, like keyless folds
  assert.match(RENDER, /if \(!key\) return box;/);
});

test("every scrollable .fold-pre a rebuild can hit carries a stable scroll key", () => {
  // preEl is the ONE .fold-pre factory, and it routes through keepScroll
  assert.match(RENDER, /function preEl\(text: string, scrollKey\?: string\): HTMLElement \{/);
  assert.match(RENDER, /return keepScroll\(pre, scrollKey\);/);
  // the System context card keys each CLAUDE.md doc by session + doc index
  assert.match(RENDER, /preEl\(doc\.text, key \? key \+ ":doc" \+ i : undefined\)/);
  // Read dumps (and the legacy Skill-output fold) key on the turn's fold key
  assert.ok((RENDER.match(/preEl\(ev\.output, fkey && fkey \+ ":out"\)/g) || []).length >= 2,
    "both tail preEl(ev.output) folds pass a scroll key");
});
