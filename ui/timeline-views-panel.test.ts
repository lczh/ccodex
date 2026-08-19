// The timeline's corner control panel (the user 2026-08-18): "Show: <view> ▾ · N more" in the
// bottom-left corner — the strip under the lane gutter, left of the time labels. The dropdown picks
// the active VIEW (All sessions / named groups), holds New group… / Edit sessions…, and carries the
// two timeline display toggles (collapse idle gaps, active only) so they finally work in every host.
// The dialog is one checkbox per session: unchecked in the all-view = hidden from the timeline AND
// the chat strip (a background session); checked in a group = member. House pattern: execute the
// pure helpers + reconcile on a bare prototype, regex-pin the SVG/menu wiring.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const requireCjs = createRequire(__filename);
const VIEW_PATH = path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js");
const SRC = fs.readFileSync(VIEW_PATH, "utf8");
const { TimelinePanel, viewVisible, viewLabel, viewMoreCount, viewToggleHidden, viewToggleMember } = requireCjs(VIEW_PATH);

const G = { id: "g1", name: "pool", color: "#DD42FF", members: ["s2", "s3"] };
const V = (active: string, hidden: string[] = [], groups: any[] = [G]) => ({ active, hidden, groups });

test("executed: the all-view hides the hidden set; a group shows exactly its members", () => {
  assert.equal(viewVisible(null, "s1"), true, "no blob yet → everything shows");
  assert.equal(viewVisible(V("all", ["s2"]), "s2"), false);
  assert.equal(viewVisible(V("all", ["s2"]), "s1"), true);
  assert.equal(viewVisible(V("g1", ["s2"]), "s2"), true, "membership beats the hidden bit");
  assert.equal(viewVisible(V("g1"), "s1"), false, "a group shows exactly its members");
  assert.equal(viewVisible(V("ghost", [], []), "s1"), true, "an orphaned active falls back open");
});

test("executed: the trigger label and the N-more cue (live sessions outside the view)", () => {
  assert.equal(viewLabel(null), "All");
  assert.equal(viewLabel(V("g1")), "pool");
  const sessions = [{ id: "s1", live: true }, { id: "s2", live: true }, { id: "s4", live: false }];
  assert.equal(viewMoreCount(V("g1"), sessions), 1, "s1 is live and outside; dead s4 never counts");
  assert.equal(viewMoreCount(V("all", ["s1", "s4"]), sessions), 1, "hidden live s1 counts; hidden dead s4 does not");
});

test("executed: an optimistic edit holds until the kernel echoes it — then yields to authority", () => {
  const p: any = Object.create(TimelinePanel.prototype);
  p._views = null; p._pendingViews = V("g1"); p._pendingViewsAge = 0;
  p._reconcileViews();
  assert.ok(p._pendingViews, "no echo yet → still pending");
  // the kernel echoes the same shape with re-sorted lists → canonical comparison clears it
  p._views = { active: "g1", hidden: [], groups: [{ id: "g1", name: "pool", color: "#DD42FF", members: ["s3", "s2"] }] };
  p._reconcileViews();
  assert.equal(p._pendingViews, null, "echo match (order-insensitive) clears the pending edit");
  // a pending edit the kernel never echoes yields after three pushes — the kernel is authoritative
  p._pendingViews = V("g1"); p._pendingViewsAge = 0;
  p._views = { active: "all", hidden: [], groups: [] };
  p._reconcileViews(); p._reconcileViews();
  assert.ok(p._pendingViews, "two silent pushes → still holding");
  p._reconcileViews();
  assert.equal(p._pendingViews, null, "the third silent push adopts the kernel's blob");
});

test("the lane gate composes the view filter first, and the all-quiet fallback respects it", () => {
  assert.match(SRC, /const inView = \(s\) => viewVisible\(this\._curViews\(\), s\.id\);/);
  assert.match(SRC, /let vis = data\.sessions\.filter\(inView\)\.filter\(active\);/);
  assert.match(SRC, /if \(this\._activeOnly && !vis\.length\) vis = data\.sessions\.filter\(inView\)\.filter\(\(s\) => s\.live \|\| hasWork\(s\)\);/,
    "the fallback can never resurrect a view-hidden lane");
});

test("the trigger sits in the corner strip and opens on pointerdown, like every timeline control", () => {
  assert.match(SRC, /_drawViewsTrigger\(svg, axisY\);/);
  assert.match(SRC, /lead\.textContent = 'Show: ';/);
  assert.match(SRC, /t\.addEventListener\('pointerdown', \(e\) => \{ e\.preventDefault\(\); e\.stopPropagation\(\); this\._openViewsMenu\(t\); \}\);/);
  assert.match(SRC, /' · ' \+ more \+ ' more';/, "a filtered-out live session is always one glance away");
});

test("the dropdown and dialog wear the shared menu vocabulary and adopt into the menu host", () => {
  assert.match(SRC, /'position:fixed;z-index:1001;min-width:200px;' \+ MENU_STYLE/);
  assert.match(SRC, /c\.setAttribute\('style', MENU_CHECK_STYLE\);/, "the ✓-in-circle current mark");
  assert.match(SRC, /'position:fixed;inset:0;z-index:1002;background:rgba\(0,0,0,0\.55\);'/,
    "the one modal dim, over the topmost same-origin document");
  assert.match(SRC, /const h = this\._menuHost\(anchorEl\.getBoundingClientRect\(\)\);[\s\S]{0,400}this\._viewsMenu = menu;/);
});

test("the two display toggles write the host's own romp:settings — reachable in every host now", () => {
  assert.match(SRC, /item\('Collapse idle gaps', \{ current: !!this\._collapseGaps, dim: true \}\)/);
  assert.match(SRC, /item\('Active sessions only', \{ current: !!this\._activeOnly, dim: true \}\)/);
  assert.match(SRC, /localStorage\.setItem\('romp:settings', JSON\.stringify\(s\)\);/);
});

test("_setViews posts through the host hook with a GUARDED, atomic Obsidian fallback", () => {
  assert.match(SRC, /window\.__rompTimelineSetViews === 'function'/);
  // Electron-gated (a bare-node test run must never touch the real file — the 2026-07-02 lesson),
  // env-aware state root, tmp+rename so a reader never sees a torn blob
  assert.match(SRC, /process\.versions && process\.versions\.electron/);
  assert.match(SRC, /process\.env\.ROMP_STATE_DIR\n?\s*\|\| path\.join\(process\.env\.XDG_STATE_HOME \|\| path\.join\(os\.homedir\(\), '\.local', 'state'\), 'romp'\)/);
  assert.match(SRC, /fs\.renameSync\(fp \+ '\.tmp', fp\);/);
  assert.match(SRC, /this\._pendingViews = v; this\._pendingViewsAge = 0;/);
  assert.match(SRC, /this\._reconcileViews\(\);\s*\/\/ \.\.\.and an optimistic view edit/);
});

test("executed: the dialog's two checkbox mutations, pure", () => {
  const v = { active: "all", hidden: ["a"], groups: [{ id: "g1", members: ["m"] }] };
  assert.deepEqual(viewToggleHidden(v, "a").hidden, [], "unhide");
  assert.deepEqual(viewToggleHidden(v, "b").hidden, ["a", "b"], "hide");
  assert.deepEqual(viewToggleMember(v, "g1", "m").groups[0].members, [], "leave");
  assert.deepEqual(viewToggleMember(v, "g1", "n").groups[0].members, ["m", "n"], "join");
  assert.deepEqual(viewToggleMember(v, "ghost", "n"), v, "an unknown group mutates nothing");
});

test("the trigger measures its WHOLE string against the gutter, and the dialog's Escape hook dies on every close", () => {
  assert.match(SRC, /const fits = \(n\) => this\.labelWidth\('Show: ' \+ n \+ tail\) <= this\.M\.left - PADL - 6;/);
  assert.match(SRC, /this\._viewsDialogKey = \{ doc: h\.doc, fn: onKey \};/);
  assert.match(SRC, /this\._viewsDialogKey\.doc\.removeEventListener\('keydown', this\._viewsDialogKey\.fn\);/);
});

test("the views menu closes with its siblings on outside click / Escape / pagehide", () => {
  assert.match(SRC, /this\._onDocClick = \(\) => \{ this\._closeMetaMenu\(\); this\._closeLaneMenu\(\); this\._closeViewsMenu\(\); \};/);
  assert.match(SRC, /if \(e\.key === 'Escape'\) \{ this\._closeMetaMenu\(\); this\._closeLaneMenu\(\); this\._closeViewsMenu\(\); \}/);
  assert.match(SRC, /this\._closeViewsMenu\(\); this\._closeViewsDialog\(\);/, "pagehide drops both overlays");
});
