// The settings gear moved from kernel-inline strings into the shared feed
// bundle (gear.js + feed.css's gear section) so both hosts render the SAME
// modal (the user 2026-07-13). These pins keep that single-source shape:
// undoing the extraction, or adding a host-blind fetch/post, breaks here.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const read = (...p: string[]) => fs.readFileSync(path.join(ROOT, ...p), "utf8");
const KERNEL = read("bin", "romp-kernel");
const GEAR = read("ui", "webview", "gear.js");
const FEED = read("ui", "webview", "feed.ts");
const GEAR_CSS = read("ui", "webview", "gear.css");
const EXT = read("vscode-extension", "src", "extension.ts");

test("the kernel no longer carries an inline gear (single source: the feed bundle)", () => {
  for (const twin of ["_GEAR_CSS", "_GEAR_JS", "_gear_html"])
    assert.ok(!KERNEL.includes(twin), `${twin} must stay deleted from the kernel`);
});

test("the feed bundle builds and wires the gear", () => {
  assert.ok(FEED.includes('require("./gear.js")'), "feed.ts must load the gear module");
  assert.ok(FEED.includes("initGear("), "feed.ts must init the gear on its kernel channel");
  assert.ok(GEAR.includes("module.exports = { initGear }"));
});

test("the gear opens on the shared {romp:'openSettings'} message on BOTH hosts", () => {
  assert.ok(GEAR.includes("e.data.romp === 'openSettings'"), "gear must listen for the open message");
  assert.ok(KERNEL.includes("openSettings"), "the web shell's rail must still post the open message");
  assert.ok(EXT.includes('{ romp: "openSettings" }'), "the VS Code menu must post the open message");
});

test("every gear fetch routes through the kernel base + token (VS Code's webview origin is synthetic)", () => {
  assert.ok(!/fetch\(['"`]\//.test(GEAR), "no bare same-origin fetches in gear.js — use ku()");
  assert.ok(!/fetch\(kb\(\) \+/.test(GEAR), "kb()-only fetches skip the serve token — use ku()");
  const kuFetches = GEAR.match(/fetch\(ku\(/g) || [];
  assert.ok(kuFetches.length >= 4, `expected the /palette, /models, /version, /analytics fetches via ku(), got ${kuFetches.length}`);
  assert.ok(EXT.includes("window.__rompKernelBase="), "the VS Code feed builder must inject the base");
  assert.ok(EXT.includes("window.__rompKernelToken="), "the VS Code feed builder must inject the serve token (the kernel gates every request, loopback included)");
  assert.ok(EXT.includes("connect-src ${kernelBase}"), "the feed webview CSP must allow the kernel origin");
});

test("the gear posts kernel ops through ONE shared channel (never re-acquires the VS Code API)", () => {
  assert.ok(!GEAR.includes("acquireVsCodeApi"), "a second acquire throws in a real webview");
  for (const op of ["setAutoNudge", "setJudgeModel", "setIndexModel", "setJudgeEffort", "setIndexEffort",
    "setColormap", "setPalette", "setDefaultDir", "browseDir"])
    assert.ok(GEAR.includes(`'${op}'`), `gear must post ${op}`);
});

test("the gear owns its browseResult (the reply lands in the FEED document, not the chat's)", () => {
  assert.ok(GEAR.includes("'browseResult'") && GEAR.includes("'gear'"));
});

test("gear.css carries the modal styling for every pane that hosts it", () => {
  for (const sel of ["#rsettings", ".rs-card", "#rs-cmap-btn", "#rs-pal-btn", ".ra-openbtn", "#ranalytics"])
    assert.ok(GEAR_CSS.includes(sel), `gear.css must style ${sel}`);
  assert.ok(KERNEL.includes("/dist/gear.css"), "the kernel feed page must link the extracted stylesheet");
});

test("the gear's dormant web-rail restart sends the default scope like every other surface", () => {
  // AGENTS.md decision 2: every restart surface sends the same default; the missing body pin is
  // how gear.js silently disagreed with the dashboard for two releases (the user's audit, 2026-08-16)
  assert.ok(GEAR.includes("body: '{}'"), "gear restart body is the default scope");
  assert.ok(!GEAR.includes('"fleet":false'), "no local-only opt-out hardcoded in gear");
});
