// The romp strip — the VS Code stand-in for the web shell's bottom rail
// (usage bar-pairs + the settings gear below the chat composer / feed foot).
// Pure helpers tested directly; the host opt-in + feed-over-chat wiring is
// source-pinned (chat-view/src/host-chrome.test.ts covers the builders).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { usageColor, fmtAgo, fmtReset, fmtUsd, usageWindows, apiCell, STRIP_PANES } from "./strip";

test("usageColor mirrors the rail's green/amber/red ramp", () => {
  assert.equal(usageColor(0), "#54B204");
  assert.equal(usageColor(69), "#54B204");
  assert.equal(usageColor(70), "#e0b020");
  assert.equal(usageColor(89), "#e0b020");
  assert.equal(usageColor(90), "#c0392b");
});

test("fmtReset renders d/h/m compactly and 'soon' at rollover", () => {
  assert.equal(fmtReset(10_000, 10_100), "soon");
  assert.equal(fmtReset(10_000 + 5 * 60, 10_000), "5m");
  assert.equal(fmtReset(10_000 + 2 * 3600 + 5 * 60, 10_000), "2h 5m");
  assert.equal(fmtReset(10_000 + 86400 + 3600, 10_000), "1d 1h 0m");
});

test("usageWindows keeps only reported windows, clamps, and computes pace", () => {
  const nowS = 100_000;
  const ws = usageWindows({
    fiveHour: { pct: 91, resetsAt: nowS + 3600 },        // 1h left of 5h → 80% elapsed
    fable: { pct: 120, resetsAt: nowS + 7 * 86400 },     // over-reported → clamped, 0% elapsed
  }, nowS);
  assert.deepEqual(ws.map((w) => w.key), ["fiveHour", "fable"]);
  assert.deepEqual(ws.map((w) => w.short), ["5h", "F5"], "each window carries its compressed tag");
  assert.equal(ws[0].pct, 91);
  assert.equal(ws[0].elapsedPct, 80);
  assert.match(ws[0].title, /5 hours — used 91% · 80% through the window · resets in 1h 0m/);
  assert.equal(ws[1].pct, 100);
  assert.equal(ws[1].elapsedPct, 0);
});

test("a window that reset since the last report is UNKNOWN — never a confident 0 (the user 2026-07-31)", () => {
  // a remote whose kernel had no live session to ask sat on a days-old snapshot, and the rail drew
  // 0% beside a live account's real bars — indistinguishable from a genuinely idle account. The
  // last-known reading survives (drawn faded), the readout is "?", and the title dates the gap.
  const ws = usageWindows({ fiveHour: { pct: 91, resetsAt: 50 } }, 100);
  assert.equal(ws[0].unknown, true);
  assert.equal(ws[0].pct, 91, "the last-known reading survives for the faded fill");
  assert.equal(ws[0].elapsedPct, null, "pace means nothing against a window that already ended");
  assert.match(ws[0].title, /window reset .* — current usage unknown \(last known 91%\)/);
});

test("a live window is not unknown, and fmtAgo dates a gap", () => {
  const ws = usageWindows({ fiveHour: { pct: 20, resetsAt: 200 } }, 100);
  assert.equal(ws[0].unknown, false);
  assert.equal(fmtAgo(100, 100 + 90060), "1d 1h 1m ago");
});

test("no usage → no windows (the strip stays quiet, never fakes bars)", () => {
  assert.deepEqual(usageWindows(null, 100), []);
  assert.deepEqual(usageWindows({}, 100), []);
});

test("the strip carries the rail's controls: refresh, network popover, pane quick-opens", () => {
  const src = fs.readFileSync(path.join(path.resolve(process.cwd(), ".."), "ui", "webview", "strip.ts"), "utf8");
  assert.ok(src.includes('"/restart"') || src.includes("/restart`"), "the refresh button restarts the kernel");
  for (const ep of ["/ssh-hosts", "/tunnels", "/tunnels/detach", "/tunnels/update", "/tunnels/start"])
    assert.ok(src.includes(ep), `the network popover must drive ${ep} (the rail twin)`);
  assert.ok(src.includes('{ type: "openPane", pane: p.key }'), "quick-opens post openPane to the host");
});

test("the strip quick-opens cover chat/outline/feed only (timeline is a native panel)", () => {
  assert.deepEqual(STRIP_PANES.map((p) => p.key), ["chat", "fleet", "feed"]);
  assert.deepEqual(STRIP_PANES.map((p) => p.label), ["Chat", "Outline", "Feed"]);
});

// A narrow pane must NEVER grow a horizontal scrollbar under the strip (the
// user 2026-07-13), and the ladder must be MEASURED, not width-thresholded
// (the user 2026-07-14: hardcoded @container widths compressed the labels
// while free space remained, and the bars never narrowed): the bars are
// fluid down to a floor, fit() steps the label tiers only when the bars are
// actually pinched or the strip wrapped, and flex-wrap folds whatever still
// doesn't fit onto another row.
test("the strip compresses by measurement: fluid bars, fit()-stepped tiers, wrap backstop", () => {
  const ROOT = path.resolve(process.cwd(), "..");
  const css = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.css"), "utf8");
  const stripRule = css.match(/#romp-strip \{[^}]*\}/)![0];
  assert.ok(stripRule.includes("flex-wrap: wrap"), "the wrap backstop: leftover buttons take another row");
  assert.ok(!css.includes("@container"), "no width-threshold ladder — tiers are measured (fit()), never guessed from pane width");
  const barsRule = css.match(/\.ru-bars \{[^}]*\}/)![0];
  assert.ok(barsRule.includes("width: 54px") && barsRule.includes("min-width: 18px") && barsRule.includes("flex: 0 1 auto"),
    "bars are fluid: a definite 54px width (intrinsic-size-proof, unlike a bare flex-basis) shrinking to an 18px floor");
  // the tier ladder rides #romp-strip[data-tier]: tag, then no %, then no labels
  assert.ok(css.includes('#romp-strip[data-tier="1"] .ru-name-full'), "tier 1 swaps the expanded label for the tag");
  assert.ok(css.includes('#romp-strip[data-tier="2"] .ru-pct'), "tier 2 drops the % readout");
  assert.ok(css.includes('#romp-strip[data-tier="3"] .ru-name'), "tier 3 drops labels — bars only");
  const src = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.ts"), "utf8");
  assert.ok(src.includes('"ru-name-full"') && src.includes('"ru-name-short"'), "both label variants render; CSS picks one");
  assert.ok(src.includes("BAR_COMFORT") && src.includes("ResizeObserver"),
    "fit() steps tiers off real measurements, re-run on width changes (event-based)");
  assert.ok(src.includes("offsetWidth"), "measurements use layout px — zoom-independent under uiZoom");
  assert.ok(src.includes('"strip-acts"'), "the actions travel as one right-pinned cluster");
  assert.ok(!src.includes("strip-spacer"), "no spacer item — margin-left:auto keeps the pin across wrapped rows");
});

test("a buildless connected host reads 'unversioned copy', matching the web popover", () => {
  // a plain file copy (no git checkout) reports no sha/version, so drift detection is blind to it —
  // the row must say so where the build word sits, never a bare "connected" that reads as in-sync
  // (the user 2026-08-11, devbox). test_kernel_remote_update.py pins the web popover's twin wording.
  const ROOT = path.resolve(process.cwd(), "..");
  const src = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.ts"), "utf8");
  assert.match(src, /const unversioned = t\.status === "up" && !t\.outOfDate && !t\.kernelSha && !t\.kernelVer;/);
  assert.match(src, /if \(unversioned\) ver = " · unversioned copy";/);
  assert.match(src, /Reinstall it as a git clone to restore the build name and updates\./);
});

// The network button must acknowledge and fail LOUDLY (the user 2026-07-14
// reported it "doing nothing" in VS Code while every repro elsewhere works):
// instant .open chrome on the button, instant popover content before any
// round-trip, a visible error line when the kernel is unreachable, and a
// clientDiag breadcrumb through the host so the kernel records what the
// click actually observed.
test("the net button acknowledges, fails loudly, and leaves a diagnostic trail", () => {
  const ROOT = path.resolve(process.cwd(), "..");
  const src = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.ts"), "utf8");
  assert.ok(src.includes('button.classList.toggle("open", open)'), "the button itself acknowledges the toggle");
  assert.ok(src.includes("Checking remotes…"), "the popover never opens blank");
  assert.ok(src.includes("Couldn't reach the kernel"), "an unreachable kernel reads as an error, not an empty box");
  assert.ok(src.includes("(kernel unreachable)"), "the host select fails loudly too");
  const diags = src.match(/type: "clientDiag"/g) || [];
  assert.ok(diags.length >= 2, "toggle + fetch outcome each post a clientDiag breadcrumb");
  const cssSrc = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.css"), "utf8");
  assert.ok(cssSrc.includes("#strip-net.open"), "the .open acknowledgment has visible chrome");
  const kernel = fs.readFileSync(path.join(ROOT, "bin", "romp-kernel"), "utf8");
  assert.ok(kernel.includes('"clientDiag"') && kernel.includes("client-diag.jsonl"),
    "the kernel persists clientDiag breadcrumbs (the locateDiag pattern, generalized)");
});

test("the feed's control bar wraps on a narrow pane instead of overflowing", () => {
  const ROOT = path.resolve(process.cwd(), "..");
  const css = fs.readFileSync(path.join(ROOT, "ui", "webview", "feed.css"), "utf8");
  const foot = css.match(/#feed-foot \{[^}]*\}/)![0];
  assert.ok(foot.includes("flex-wrap: wrap"), "#feed-foot must fold its buttons onto another row");
});

test("the chat hosts its OWN gear modal (opens over the pane it was clicked in)", () => {
  const ROOT = path.resolve(process.cwd(), "..");
  const render = fs.readFileSync(path.join(ROOT, "ui", "webview", "render.ts"), "utf8");
  assert.ok(render.includes('require("./gear.js")'), "chat bundle must load the gear module");
  assert.ok(!render.includes("openRompSettings"), "no cross-pane settings hop remains");
});

test("both bundles init the strip; the web pages never opt in", () => {
  const ROOT = path.resolve(process.cwd(), "..");
  const read = (f: string) => fs.readFileSync(path.join(ROOT, "ui", "webview", f), "utf8");
  assert.ok(read("render.ts").includes("initStrip("), "chat bundle must init the strip");
  assert.ok(read("feed.ts").includes("initStrip("), "feed bundle must init the strip");
  const kernel = fs.readFileSync(path.join(ROOT, "bin", "romp-kernel"), "utf8");
  assert.ok(!kernel.includes("__rompShowStrip"), "the web shell keeps its own rail — no strip opt-in kernel-side");
});

// ── the API spend CELL (the user 2026-08-11): the rail moved key spend to one compact cell — "API",
// then designator → dollars·tokens pairs, no bars — and the strip must mirror it. Pay-per-token wears
// calendar-ish windows (the user 2026-08-13): 1 day + 1 month on the cell, 1 week in the hover.
test("apiCell arms on the spend windows' presence and carries 1 day + 1 month", () => {
  const cell = apiCell({ spend: {
    day: { usd: 12.34, tok: 3_456_000, turns: 5 },
    week: { usd: 40.2, tok: 9_000_000, turns: 21 },
    month: { usd: 87.9, tok: 20_500_000, turns: 60 },
  } });
  assert.ok(cell);
  assert.deepEqual(cell!.segs.map((s) => [s.key, s.label, s.short]),
    [["day", "1 day", "1d"], ["month", "1 month", "1mo"]],
    "the collapsed cell shows 1 day + 1 month only — 1 week lives in the hover");
  assert.deepEqual(cell!.segs.map((s) => fmtUsd(s.usd)), ["$12", "$88"], "whole dollars, no cents");
  assert.match(cell!.title, /^API-key spend\n/);
  assert.match(cell!.title, /1 week — \$40 · 9M tok · 21 turns/, "the hover keeps the full breakdown");
  assert.match(cell!.title, /1 day — \$12 · 3\.5M tok · 5 turns/);
});

test("an older kernel's fiveHour window still arms the cell (version skew)", () => {
  const cell = apiCell({ spend: { fiveHour: { usd: 3.2, tok: 900_000, turns: 2 } } });
  assert.ok(cell, "day||fiveHour — a remote host on an older kernel must not blank its spend");
  assert.deepEqual(cell!.segs.map((s) => [s.key, fmtUsd(s.usd)]), [["day", "$3"]],
    "the 5h burn stands in under the 1-day designator until that host updates");
});

test("no key spend → no cell; and no fragment of any key ever reaches the strip", () => {
  assert.equal(apiCell(null), null);
  assert.equal(apiCell({ spend: {} }), null, "presence of the day window is the whole test — the rail's hasSpend branch");
  const ROOT = path.resolve(process.cwd(), "..");
  const src = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.ts"), "utf8");
  assert.ok(!src.includes("apiTail") && !src.includes("authTail"), "constant 'API' label — no key-tail plumbing");
});

test("the cell's dollars survive every tier; tokens fold at tier 2, labels at 3 (source pins)", () => {
  const ROOT = path.resolve(process.cwd(), "..");
  const src = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.ts"), "utf8");
  assert.match(src, /box\.className = "ru-w ru-api";/);
  assert.match(src, /lbl\.textContent = "API";/);
  assert.match(src, /val\.className = "ru-apiv";/, "dollars wear their own class, never .ru-pct");
  assert.match(src, /tok\.className = "ru-apitok";/);
  assert.doesNotMatch(src, /spendWindows/, "spend-as-rows is gone, not merely unused");
  const css = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.css"), "utf8");
  assert.match(css, /\.ru-apiv \{ font: 600 10px/);
  assert.match(css, /#romp-strip\[data-tier="2"\] \.ru-apitok, #romp-strip\[data-tier="3"\] \.ru-apitok \{ display: none; \}/);
  assert.doesNotMatch(css, /ru-textonly/, "the ghost-row mechanism died with spend-as-rows");
});

test("an unknown window is not drawn on the bar at all — its last-known lives on hover (the user 2026-08-13)", () => {
  // supersedes the 2026-07-31 '?' slot: the bar shows only what we know. The strip has no rich hover
  // panel, so the hidden rows' last-known text rides the usage wrap's own title, labelled as such.
  const ROOT = path.resolve(process.cwd(), "..");
  const src = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.ts"), "utf8");
  const css = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.css"), "utf8");
  assert.match(src, /if \(w\.unknown\) \{ unknownLines\.push\(w\.title\); continue; \}/);
  assert.match(src, /usageWrap\.title = unknownLines\.length\s*\n\s*\? "Not shown \(no current reading\):\\n" \+ unknownLines\.join\("\\n"\) : "";/);
  assert.doesNotMatch(src, /ru-qmark/, "the '?' slot is gone");
  assert.doesNotMatch(css, /ru-qmark/);
  assert.doesNotMatch(css, /\.ru-w\.ru-unk \.ru-fill \{ opacity: 0\.3; \}/, "the faded fill stays gone");
});
