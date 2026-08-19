// The + picker (openPicker → renderPicker) surfaces RUNNING sessions whose tab you've closed at the top with
// a live badge, HIDES sessions you already have open as a tab (a tab-click away — just noise), and lists the
// closed/aged ones under a "Recent" header to revive. In PICK mode (choosing a target session) nothing is
// hidden. The user 2026-07-15. Source-level pins (no jsdom for the renderer).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("isOpenTab checks this dashboard's tabs (loaded session, order, or placeholder meta)", () => {
  assert.match(RENDER, /function isOpenTab\(id: string\): boolean \{[\s\S]*?sessions\.has\(id\) \|\| order\.includes\(id\) \|\| tabMeta\.has\(id\)/);
});

test("the + (open) flow hides already-open tabs and splits Running vs Recent", () => {
  // only non-pick mode filters out open tabs
  assert.match(RENDER, /const avail = items\.filter\(\(it\) => !isOpenTab\(it\.id\)\);/);
  assert.match(RENDER, /const running = avail\.filter\(\(it\) => it\.running\);/);
  assert.match(RENDER, /const rest = avail\.filter\(\(it\) => !it\.running\);/);
  // running group first, with a header; then Recent (only when there was a running group above)
  assert.match(RENDER, /if \(running\.length\) \{ list\.appendChild\(label\("Running — reopen"\)\);/);
  assert.match(RENDER, /if \(rest\.length\) \{ if \(running\.length \|\| hidden\.length\) list\.appendChild\(label\("Recent"\)\);/);
});

test("PICK mode still shows every session, open or not", () => {
  assert.match(RENDER, /if \(pickMode\) \{\s*for \(const it of items\) list\.appendChild\(mkRow\(it\)\);/);
});

test("a running row wears a green 'running' badge with a live dot", () => {
  assert.match(RENDER, /if \(it\.running\) \{[\s\S]*?time\.classList\.add\("picker-running-badge"\);/);
  assert.match(RENDER, /el\("span", "picker-run-dot"\), document\.createTextNode\("running"\)/);
  assert.match(CSS, /\.picker-running-badge \{[^}]*color: var\(--st-ready-bg\)/);
  assert.match(CSS, /\.picker-run-dot \{[^}]*background: var\(--st-ready-bg\)/);
});

test("group headers exist and collapse away during a search", () => {
  assert.match(CSS, /\.picker-group-label \{/);
  assert.match(CSS, /\.picker-list\.searching \.picker-group-label \{ display: none; \}/);
  assert.match(RENDER, /getElementById\("picker-list"\)\?\.classList\.toggle\("searching", !!query\)/);
});
