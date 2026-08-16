// Timeline AWAITING badge (the user 2026-07-01, working-state audit; recolored 2026-07-13): the lane
// shows a distinct AWAITING badge for "waiting on dispatched/background work". Originally the badge wore
// working-yellow; since the kernel's shared _session_chip split awaitingBg out of "working" (the user
// 2026-07-13: "differentiate working from awaiting") it wears its OWN await-green — the working gold's paler
// sibling — matching the chat chip and the tab/feed dots. The s.awaitingBg why-field stays the fallback
// key for a remote host on an older kernel (state still 'working' there).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const TL = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");

test("an awaitingBg lane renders an Awaiting badge in the romp brand green (the user 2026-07-22)", () => {
  // keyed on the chip state (the shared _session_chip split) OR the legacy why-field (older remote kernels)
  assert.match(TL, /else if \(s\.state === 'awaitingBg' \|\| s\.awaitingBg\) m = \{ label: 'Awaiting' \+ \(s\.awaitingKind \? ' ' \+ s\.awaitingKind : ''\), kind: 'awaitbg' \};/);
  // brand green, matching --st-awaitbg-bg in styles.css (this file loads standalone, so the hex is mirrored)
  assert.match(TL, /awaitbg: \{ bg: '#54B204', fg: '#0c1a00' \}/);
  // an awaitingBg lane still reads ACTIVE (full opacity / ongoing treatment), like working/compacting/clearing
  assert.match(TL, /s\.state === 'awaitingBg' \|\| s\.state === 'compacting' \|\| s\.state === 'clearing';/);
});

test("precedence: blocked-on-you beats awaiting, awaiting beats Ready", () => {
  const blocked = TL.indexOf("m = { label: 'Blocked', kind: 'attention' }");
  const awaiting = TL.indexOf("label: 'Awaiting'");
  const ready = TL.indexOf("m = { label: 'Ready', kind: 'ready' }");
  assert.ok(blocked > 0 && awaiting > 0 && ready > 0, "all three badge branches exist");
  assert.ok(blocked < awaiting, "a hard block (on you) outranks the awaiting cue");
  assert.ok(awaiting < ready, "awaiting is checked before the plain Ready fallback");
});

test("needsInput maps to Blocked, and the legacy 'awaiting' name (an older remote kernel) still does too", () => {
  assert.match(TL, /s\.state === 'permission' \|\| s\.state === 'needsInput' \|\| s\.state === 'awaiting'\) m = \{ label: 'Blocked', kind: 'attention' \}/);
});

test("an idle awaitingBg lane draws a full-thickness FADED stretch (0.4 alpha), not a thin dash (the user 2026-07-13)", () => {
  // from the last work period's end to the live edge, lane-colored, at the work-bar thickness (BAR_H) but
  // faded to 0.4 — a faded continuation of the bar, not a thin dash, and never the solid ~0.9 work bar
  assert.match(TL, /if \(s\.live && s\.awaitingBg\) \{/);
  assert.match(TL, /el\('line', \{ x1: lx1, y1: y, x2: lx2, y2: y, stroke: s\.color, 'stroke-width': BAR_H,\s*\n\s*'stroke-linecap': 'round', opacity: 0\.4,/);
  assert.doesNotMatch(TL, /'stroke-dasharray': '5 4'/);   // the dash is gone
  // the hover lists the live task descriptions (kernel awaitingTasks), falling back to the why line
  assert.match(TL, /s\.awaitingTasks && s\.awaitingTasks\.length\) \? s\.awaitingTasks : \[s\.awaitingBg\]/);
  // hover bumps opacity (0.4 -> 0.6) + a slight grow, keeping the "faded/pending" read rather than going solid
  assert.match(TL, /ln\.setAttribute\('stroke-width', String\(BAR_H \+ 2\)\); ln\.setAttribute\('opacity', '0\.6'\); this\.showTip\(tip, e\);/);
  assert.match(TL, /ln\.setAttribute\('stroke-width', String\(BAR_H\)\); ln\.setAttribute\('opacity', '0\.4'\); this\.hideTip\(\);/);
  // the stretch keeps empty-row behaviors: drag to pan/reorder, click to select/open
  assert.match(TL, /wh\.addEventListener\('mousedown', \(e\) => this\._beginDrag\(s\.id, e\)\);/);
});
