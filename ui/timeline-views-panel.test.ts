// The timeline's corner control panel (the user 2026-08-18; filter-chip form + TAG model
// 2026-08-23): "Filter ▾" in the bottom-left corner — the strip under the lane gutter, left of the
// time labels. The dropdown picks the active VIEW (All — every session minus hidden, the default
// since 2026-08-24 — / the (untagged) built-in / the named tags),
// holds New tag… / Sessions & tags…, and carries the two timeline display toggles (collapse idle
// gaps, active only) so they finally work in every host. The dialog is TAG-CENTRIC: one row per
// session wearing its tag chips (✕ leaves a tag; [+] joins or mints one) — a tagged session leaves
// the default view and shows under its tags. House pattern: execute the pure helpers + reconcile
// on a bare prototype, regex-pin the SVG/menu wiring.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const requireCjs = createRequire(__filename);
const VIEW_PATH = path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js");
const SRC = fs.readFileSync(VIEW_PATH, "utf8");
const { TimelinePanel, viewVisible, viewLabel, viewMoreCount, viewToggleHidden, viewToggleMember, viewTagUnion } = requireCjs(VIEW_PATH);

const G = { id: "g1", name: "pool", color: "#DD42FF", members: ["s2", "s3"] };
const V = (active: string, hidden: string[] = [], tags: any[] = [G]) => ({ active, hidden, tags });

test("executed: All shows every session minus hidden; untagged the tagless; a tag view its members", () => {
  assert.equal(viewVisible(null, "s1"), true, "no blob yet → everything shows");
  assert.equal(viewVisible(V("all", ["s9"]), "s9"), false, "hidden — All respects the deliberate hide");
  assert.equal(viewVisible(V("all"), "s2"), true, "TAGGED → All still shows it (the user 2026-08-24)");
  assert.equal(viewVisible(V("all"), "s1"), true, "untagged → shown");
  assert.equal(viewVisible(V("untagged", ["s9"]), "s9"), false, "hidden hides in the untagged view too");
  assert.equal(viewVisible(V("untagged"), "s2"), false, "TAGGED → out of the untagged view (the user 2026-08-23)");
  assert.equal(viewVisible(V("untagged"), "s1"), true, "tagless → the untagged view shows it");
  assert.equal(viewVisible(V("g1", ["s2"]), "s2"), true, "a tag view shows its members, hidden or not");
  assert.equal(viewVisible(V("g1"), "s1"), false, "a tag view shows exactly its members");
  assert.equal(viewVisible(V("ghost", [], []), "s1"), true, "an orphaned active falls back open");
  assert.equal(viewVisible({ active: "untagged", groups: [G] }, "s2"), false,
    "the legacy `groups` key an un-updated kernel pushes reads identically");
});

test("executed: the trigger label and the N-more cue (live sessions outside the view)", () => {
  // the views are named for what they show: "All" (every session minus hidden — the default since
  // 2026-08-24) and "(untagged)", parenthesized as the built-in it is
  assert.equal(viewLabel(null), "All");
  assert.equal(viewLabel(V("untagged")), "(untagged)");
  assert.equal(viewLabel(V("g1")), "pool");
  const sessions = [{ id: "s1", live: true }, { id: "s2", live: true }, { id: "s4", live: false }];
  assert.equal(viewMoreCount(V("g1"), sessions), 1, "s1 is live and outside; dead s4 never counts");
  assert.equal(viewMoreCount(V("all", ["s1"]), sessions), 1, "hidden live s1 counts under All; tagged s2 shows now");
  assert.equal(viewMoreCount(V("untagged", ["s1"]), sessions), 2, "hidden s1 AND tagged s2 sit outside untagged");
});

test("executed: an optimistic edit holds until the kernel echoes it — then yields to authority", () => {
  const p: any = Object.create(TimelinePanel.prototype);
  p._views = null; p._pendingViews = V("g1"); p._pendingViewsAge = 0;
  p._reconcileViews();
  assert.ok(p._pendingViews, "no echo yet → still pending");
  // the kernel echoes the same shape with re-sorted lists → canonical comparison clears it
  p._views = { active: "g1", hidden: [], tags: [{ id: "g1", name: "pool", color: "#DD42FF", members: ["s3", "s2"] }] };
  p._reconcileViews();
  assert.equal(p._pendingViews, null, "echo match (order-insensitive) clears the pending edit");
  // a pending edit the kernel never echoes yields after three pushes — the kernel is authoritative
  p._pendingViews = V("g1"); p._pendingViewsAge = 0;
  p._views = { active: "all", hidden: [], tags: [] };
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
  // named for what it does (the user 2026-08-23): it FILTERS the lanes — "Show:" read as a passive label
  assert.match(SRC, /t\.textContent = 'Filter ▾';/);
  assert.match(SRC, /t\.addEventListener\('pointerdown', \(e\) => \{ e\.preventDefault\(\); e\.stopPropagation\(\); this\._openViewsMenu\(t\); \}\);/);
  assert.match(SRC, /const tailStr = more \? more \+ ' more' : '';/, "a filtered-out live session is always one glance away");
});

test("an active tag is a REMOVABLE CHIP: outline only in its colour, a dim separate ✕, air below (the user 2026-08-24)", () => {
  // the chip's own pointerdown clears the filter without a menu trip; stopPropagation keeps the
  // text element's menu handler out of it (both are pointerdown — the redraw-eats-click rule)
  assert.match(SRC, /grp\.addEventListener\('pointerdown', \(e\) => \{\n\s*e\.preventDefault\(\); e\.stopPropagation\(\);\n\s*const nv = JSON\.parse\(JSON\.stringify\(v\)\); nv\.active = 'all'; this\._setViews\(nv\);/);
  // OUTLINE only on the page's own ground (the tinted fill was too much — the user 2026-08-24),
  // and the ✕ is dim and SEPARATE, the composer context chip's read — never baked into the name
  assert.match(SRC, /fill: 'transparent',\n\s*stroke: gcol, 'stroke-width': 1, opacity: gdim/);
  // a SENTINEL view's chip dims to the corner line's own gray at the N-more opacity (the user
  // 2026-08-24: at #cccccc it read bright as a tag) — real tag chips keep their tag colors, full strength
  assert.match(SRC, /const gcol = \(g && g\.color\) \|\| MODEL_FG;/);
  assert.match(SRC, /const gdim = g \? 1 : 0\.7;/);
  assert.match(SRC, /y: y - 13, width: cw, height: 18, rx: 9,/, "taller chip");
  assert.match(SRC, /cx\.textContent = '✕';/);
  assert.match(SRC, /fill: MODEL_FG, opacity: 0\.75/);
  assert.match(SRC, /fill: gcol, 'font-weight': 650, opacity: gdim/);
  assert.match(SRC, /click to remove the filter \(back to the default view\)/);
  // no chip on All — the unfiltered default; the untagged view IS a filter now, so it wears one
  assert.match(SRC, /const active = !!v\.active && v\.active !== 'all' && \(!!g \|\| v\.active === 'untagged'\);/);
  // …and the bottom strip grew so the taller chip has air
  assert.match(SRC, /bottom: 27 \}/);
});

test("the corner line wears the LANE LABELS' typography — inherited family, measured as rendered", () => {
  // the user 2026-08-24, who read the chip as the wrong font: the corner texts carried an explicit
  // 'font-family: FONT' while the lane names inherit the host's UI font, so at the same nominal
  // 12px the chip rendered visibly bigger. The whole corner line now inherits like the lanes do —
  // no family override anywhere in the trigger drawing…
  const TRIG = SRC.slice(SRC.indexOf("_drawViewsTrigger(svg, axisY) {"), SRC.indexOf("_closeViewsMenu() {"));
  assert.doesNotMatch(TRIG, /font-family/, "corner texts inherit the host font exactly like lane labels");
  // …at the lane-label scale: trigger and N-more at the lane 12px, the chip name at the lane-name 650
  assert.match(TRIG, /const t = el\('text', \{ x: PADL, y, 'font-size': 12, fill: MODEL_FG \}\);/);
  assert.match(TRIG, /'font-size': 12, fill: gcol, 'font-weight': 650/);
  assert.match(SRC, /'font-weight': 650, 'font-size': 12, fill: F\(s\.color\)/, "the lane-name reference the line matches");
  // …and the width/ellipsis math measures in the SAME family the text renders in: _font resolves
  // the wrap's computed family (FONT is only the unstyled/bare-node fallback), so box and ellipsis
  // can never drift from the rendered glyphs
  assert.match(SRC, /_font\(b\) \{ this\._mc\.font = \(b \? '700 ' \+ BADGE_FS \+ 'px ' : '650 12px '\) \+ this\._fontFace\(\); \}/);
  assert.match(SRC, /getComputedStyle\(this\.wrap\)\.fontFamily\) \|\| FONT;/);
  // …and EVERY measure goes through it: the only ctx.font writers left are _font/_fontFace-based
  // (ctxWidth and the two inline 9px/10px axis measures included), so no measure site can drift
  const MEASURES = SRC.match(/this\._mc\.font = [^;]+;/g) || [];
  assert.ok(MEASURES.length >= 4, "the known measure sites are present");
  for (const m of MEASURES) assert.match(m, /this\._fontFace\(\)/, "a measure bypasses _fontFace: " + m);
});

test("a pointerdown-opened menu survives its OWN opening click (the user 2026-08-24, click-and-hold bug)", () => {
  // the browser fires a click after pointerup; unstopped it bubbles to the document's menu-closer
  // and shuts the menu the instant it opened — only a mid-press redraw (element swapped, no click
  // at all) let it survive, which read as "hold to open". Every pointerdown anchor swallows it.
  assert.match(SRC, /this\._openViewsMenu\(t\); \}\);[\s\S]{0,400}t\.addEventListener\('click', \(e\) => e\.stopPropagation\(\)\);/);
  assert.match(SRC, /this\._openLaneMenu\(s, ghit\);\n\s*\}\);[\s\S]{0,300}ghit\.addEventListener\('click', \(e\) => e\.stopPropagation\(\)\);/);
  assert.match(SRC, /grp\.addEventListener\('click', \(e\) => e\.stopPropagation\(\)\);/);
});

test("the dropdown and dialog wear the shared menu vocabulary and adopt into the menu host", () => {
  assert.match(SRC, /'position:fixed;z-index:1001;min-width:200px;' \+ MENU_STYLE/);
  assert.match(SRC, /c\.setAttribute\('style', MENU_CHECK_STYLE\);/, "the ✓-in-circle current mark");
  assert.match(SRC, /'position:fixed;inset:0;z-index:1002;background:rgba\(0,0,0,0\.55\);'/,
    "the one modal dim, over the topmost same-origin document");
  assert.match(SRC, /const h = this\._menuHost\(anchorEl\.getBoundingClientRect\(\)\);[\s\S]{0,400}this\._viewsMenu = menu;/);
});

test("the sessions dialog is a TABLE speaking romp's own conventions (the user 2026-08-24, JLD-designed)", () => {
  // one grid, columns [name | chips | + | feed | eye] — the [+] column's ALIGNMENT carries the
  // table structure (JLD: sequence in space suggests structure)
  assert.match(SRC, /grid-template-columns:max-content 1fr max-content max-content max-content;/);
  // the session NAME wears its identity colour directly (JLD: label directly, never a legend-like
  // proxy dot), the host: prefix is quiet lowercase italic, a dead session is struck — the same
  // read as the lanes and the feed. No model column, no instruction caption, no ellipsized names.
  assert.match(SRC, /font-weight:650;color:' \+ \(s\.color \|\| '#cccccc'\)/);
  assert.match(SRC, /font-style:italic;font-size:0\.88em;/);
  assert.match(SRC, /text-decoration:line-through;/);
  const DLG = SRC.slice(SRC.indexOf('_openViewsDialog'), SRC.indexOf('_openLaneMenu('));
  assert.doesNotMatch(DLG, /s\.model/, "no model column");
  assert.doesNotMatch(SRC, /Tags mark specialized sessions/, "the display explains itself");
  // SEARCH (name or host — one string, the host prefix rides the name) + the bulk controls that
  // act on the FILTERED set: search is how a batch is selected (the user 2026-08-24)
  assert.match(SRC, /q\.placeholder = 'search name or host…';/);
  assert.match(SRC, /const tagAll = bar\.createSpan\(\{ text: '\+ tag all' \}\);/);
  assert.match(SRC, /anyOn \? 'mute feed for all' : 'restore feed for all'/);
  assert.match(SRC, /const flagVal = ft\.value\(!anyOn\);/, "any still minting → mute all; all muted → restore");
  // chips: outline in the tag's colour, dim separate ✕, hover changes colour (menu chrome)
  // the chips live in the SHARED name-keyed builder now (user ruling 2026-08-24): one chip per
  // tag NAME, ✕ = remove-everywhere via the union dispatcher — the dialog and the gear both call it
  assert.match(SRC, /ch\.createSpan\(\{ text: g\.name \}\);/);
  assert.match(SRC, /const chx = ch\.createSpan\(\{ text: '✕' \}\);/, "the ✕ is its own dim span — the composer chip's read");
  assert.doesNotMatch(SRC, /background:color-mix/, "no tinted chip grounds anywhere in the dialog");
  assert.match(SRC, /this\._editTagUnion\(g, \{ remove: \[s\.id\] \}\); rebuild\(\);/,
    "chip ✕ = remove-everywhere, through the one dispatcher");
  assert.match(SRC, /ni\.placeholder = 'new tag…';/, "minting a tag right from a row or the bulk bar");
  assert.match(SRC, /delete nv\.groups;/, "a write normalizes onto the tags key, never re-emitting the legacy one");
  // the eye-off appears ONLY on a hidden session, to un-hide it (hiding lives on the chat tab)
  assert.match(SRC, /if \(\(\(vv\.hidden \|\| \[\]\)\)\.indexOf\(s\.id\) >= 0\) \{/);
  // the feed toggle still rides every live row (the user 2026-08-19 pool-builder rule), aligned in
  // its own column; NOT auto-coupled to membership.
  assert.match(SRC, /const ft = LANE_TOGGLES\.find\(\(t\) => t\.flag === 'hideFromFeed'\);/);
  assert.match(SRC, /\(this\._pendingFlags\[s\.id\] = this\._pendingFlags\[s\.id\] \|\| \{\}\)\.hideFromFeed = next;/,
    "the same optimistic sticky flags the lane gear uses");
  // the menu items say so: All first (the default), (untagged) second, then New tag… / Sessions & tags…
  assert.match(SRC, /item\('New tag…', \{ dim: true \}\)/);
  assert.match(SRC, /item\('Sessions & tags…', \{ dim: true \}\)/);
  assert.match(SRC, /item\('All', \{ current: !v\.active \|\| v\.active === 'all' \}\)/);
  assert.match(SRC, /item\('\(untagged\)', \{ current: v\.active === 'untagged' \}\)/);
  assert.match(SRC, /item\('All',[\s\S]{0,300}item\('\(untagged\)',/, "All sits ABOVE (untagged) in the menu");
  assert.match(SRC, /item\('\(untagged\)',[\s\S]{0,600}for \(const g of viewTagUnion\(v\)\)/,
    "…and the tag rows come AFTER both built-ins — the DoD's menu order, pinned end to end (the rows are the NAME-KEYED union since the 2026-08-24 ruling)");
});

test("federation, NAME-KEYED (user ruling 2026-08-24): one name = one row/label/union — kernels are plumbing", () => {
  // the ruling superseded the v0 host-marked two-rows render: "if the UX requires understanding
  // that tags exist across different kernels, it is not good". Executed on the mirror:
  const both = { active: "TESTHOST-A:g1",
                 tags: [{ id: "gL", name: "team", color: "#123456", members: ["local1"] }],
                 remoteTags: [{ id: "TESTHOST-A:g1", host: "TESTHOST-A", name: "team", color: "#DD42FF", members: ["m1"] }] };
  assert.equal(viewLabel(both), "team", "the NAME, never a host prefix");
  assert.equal(viewVisible(both, "m1"), true, "the union: the remote store's member shows");
  assert.equal(viewVisible(both, "local1"), true, "…and the local store's, under ONE view");
  assert.equal(viewVisible(both, "other"), false);
  const u = viewTagUnion(both);
  assert.equal(u.length, 1, "one name = one identity — the twin-chip render is gone");
  assert.equal(u[0].color, "#123456", "the LOCAL store's colour wins the render, deterministically");
  assert.deepEqual(u[0].members.slice().sort(), ["local1", "m1"]);
  assert.equal(viewVisible({ active: "TESTHOST-A:g1", tags: [] }, "m2"), true, "gone → falls open");
  // the menu: one row per union tag, picked via the handiest id (local first)
  assert.match(SRC, /for \(const g of viewTagUnion\(v\)\)/);
  assert.match(SRC, /pick\(g\.localId \|\| g\.ids\[0\]\)/);
  assert.doesNotMatch(SRC, /border:1px dashed/, "no dashed twin chips anywhere — one solid chip per name");
  // the gray-glyph fix (unchanged): on an exact miss the colour join retries by the bare sid tail
  assert.match(SRC, /s = data\.sessions\.find\(\(x\) => x\.id === bare \|\| String\(x\.id\)\.endsWith\(':' \+ bare\)\);/);
});

test("the N-more count opens the views menu — what am I not seeing answers itself (the user 2026-08-24)", () => {
  assert.match(SRC, /m\.addEventListener\('pointerdown', \(e\) => \{ e\.preventDefault\(\); e\.stopPropagation\(\); this\._openViewsMenu\(m\); \}\);/);
  assert.match(SRC, /m\.addEventListener\('click', \(e\) => e\.stopPropagation\(\)\);/, "and survives its own opening click");
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
  const v = { active: "all", hidden: ["a"], tags: [{ id: "g1", members: ["m"] }] };
  assert.deepEqual(viewToggleHidden(v, "a").hidden, [], "unhide");
  assert.deepEqual(viewToggleHidden(v, "b").hidden, ["a", "b"], "hide");
  assert.deepEqual(viewToggleMember(v, "g1", "m").tags[0].members, [], "leave");
  assert.deepEqual(viewToggleMember(v, "g1", "n").tags[0].members, ["m", "n"], "join");
  assert.deepEqual(viewToggleMember(v, "ghost", "n"), v, "an unknown tag mutates nothing");
});

test("the trigger measures its WHOLE string against the gutter, and the dialog's Escape hook dies on every close", () => {
  // the fit measures the whole line as LAID OUT: trigger + gap + padded chip + gap + tail
  assert.match(SRC, /const width = \(n\) => this\.labelWidth\('Filter ▾'\)\n\s*\+ \(active \? GAP \+ PADH \* 2 \+ this\.labelWidth\(n\) \+ XGAP \+ this\.labelWidth\('✕'\) : 0\)\n\s*\+ \(tailStr \? GAP \+ this\.labelWidth\(tailStr\) : 0\);/);
  assert.match(SRC, /const fits = \(n\) => width\(n\) <= this\.M\.left - PADL - 6;/);
  assert.match(SRC, /this\._viewsDialogKey = \{ doc: h\.doc, fn: onKey \};/);
  assert.match(SRC, /this\._viewsDialogKey\.doc\.removeEventListener\('keydown', this\._viewsDialogKey\.fn\);/);
});

test("the views menu closes with its siblings on outside click / Escape / pagehide", () => {
  assert.match(SRC, /this\._onDocClick = \(\) => \{ this\._closeMetaMenu\(\); this\._closeLaneMenu\(\); this\._closeViewsMenu\(\); \};/);
  assert.match(SRC, /if \(e\.key === 'Escape'\) \{ this\._closeMetaMenu\(\); this\._closeLaneMenu\(\); this\._closeViewsMenu\(\); \}/);
  assert.match(SRC, /this\._closeViewsMenu\(\); this\._closeViewsDialog\(\);/, "pagehide drops both overlays");
});

test("executed: a remote-tag edit renders optimistically, echoes on the poll, yields after three silences", () => {
  // the sessionViews reconcile precedent, per remote tag (federation v1)
  const p: any = Object.create(TimelinePanel.prototype);
  p._pendingViews = null; p._pendingTagEdits = {}; p._views = { active: "all", hidden: [], tags: [],
    remoteTags: [{ id: "TESTHOST-A:g1", host: "TESTHOST-A", name: "team", color: "#DD42FF", members: ["m1"] }] };
  // the optimistic overlay: a member add renders immediately
  p._pendingTagEdits["TESTHOST-A:g1"] = { tag: { id: "TESTHOST-A:g1", host: "TESTHOST-A", name: "team",
    color: "#DD42FF", members: ["m1", "m2"] }, age: 0 };
  assert.deepEqual(p._curViews().remoteTags[0].members, ["m1", "m2"], "pending copy renders");
  // the owner's poll echoes (order-insensitive) → the pending clears
  p._views = { active: "all", hidden: [], tags: [], remoteTags: [{ id: "TESTHOST-A:g1", host: "TESTHOST-A",
    name: "team", color: "#DD42FF", members: ["m2", "m1"] }] };
  p._reconcileTagEdits();
  assert.deepEqual(p._pendingTagEdits, {}, "echo match clears the pending edit");
  // never echoed → three silent pushes yield to the polled truth
  p._pendingTagEdits["TESTHOST-A:g1"] = { tag: { id: "TESTHOST-A:g1", host: "TESTHOST-A", name: "renamed",
    color: "#DD42FF", members: [] }, age: 0 };
  p._reconcileTagEdits(); p._reconcileTagEdits();
  assert.ok(p._pendingTagEdits["TESTHOST-A:g1"], "two silences → still holding");
  p._reconcileTagEdits();
  assert.deepEqual(p._pendingTagEdits, {}, "the third yields — the owner refused or another dashboard won");
  // a pending DELETE hides the tag meanwhile
  p._pendingTagEdits["TESTHOST-A:g1"] = { tag: null, age: 0 };
  assert.equal((p._curViews().remoteTags || []).length, 0, "a pending delete renders as gone");
});

test("executed: tagEditFailed reverts the optimistic copy and keeps the reason for the dialog", () => {
  const p: any = Object.create(TimelinePanel.prototype);
  p._pendingViews = null; p._views = { active: "all", hidden: [], tags: [] };
  p._pendingTagEdits = { "TESTHOST-A:g1": { tag: null, age: 0 }, "TESTHOST-B:g2": { tag: null, age: 0 } };
  p._viewsDialog = null; p._viewsDialogBuild = null; p.draw = () => {};
  p.tagEditFailed({ host: "TESTHOST-A", name: "team", error: "not reachable" });
  assert.deepEqual(Object.keys(p._pendingTagEdits), ["TESTHOST-B:g2"],
    "only the failing owner's pendings revert — B's edit is still in flight");
  assert.equal(p._tagEditErr.error, "not reachable");
});

test("federation v1+ruling source pins: header/chips route through the UNION dispatcher, loudly on failure", () => {
  // rename/recolor/delete fan out to EVERY home; chip ✕ removes everywhere; add prefers local
  assert.match(SRC, /this\._editTagUnion\(tg, \{ rename: nv \}\);/);
  assert.match(SRC, /this\._editTagUnion\(tg, \{ color: c \}\); build\(\);/);
  assert.match(SRC, /this\._editTagUnion\(tg, \{ delete: true \}\);/);
  assert.match(SRC, /this\._editTagUnion\(g, \{ remove: \[s\.id\] \}\); rebuild\(\);/);
  assert.match(SRC, /this\._editTagUnion\(g, \{ add: rowIds\.filter\(\(id\) => g\.members\.indexOf\(id\) < 0\) \}\); rebuild\(\);/);
  // the remote transport underneath is unchanged: no hook (the Obsidian panel) → read-only + an
  // immediate visible refusal; the error line is dismissible and names the owner
  assert.match(SRC, /typeof window\.__rompTimelineEditTag !== 'function'/);
  assert.match(SRC, /er\.createSpan\(\{ text: '⚠ ' \+ \(this\._tagEditErr\.host \? this\._tagEditErr\.host \+ ': ' : ''\) \+ this\._tagEditErr\.error \}\);/);
  // a NEW tag still mints locally, posting the whole blob (zero local-path change)
  assert.match(SRC, /nv\.tags = viewTags\(nv\)\.concat/);
});

test("executed: the union dispatcher — add prefers local, remove reaches every store, delete fans out", () => {
  const p: any = Object.create(TimelinePanel.prototype);
  const setViews: any[] = []; const remote: any[] = [];
  p._setViews = (v: any) => setViews.push(v);
  p._editRemoteTag = (rt: any, e: any) => remote.push([rt.id, e]);
  const rtA = { id: "TESTHOST-A:g1", host: "TESTHOST-A", name: "team", members: ["m1", "x"] };
  const rtB = { id: "TESTHOST-B:g7", host: "TESTHOST-B", name: "team", members: ["m1"] };
  const local = { id: "gL", name: "team", color: "#123456", members: ["m1"] };
  p._views = { active: "all", hidden: [], tags: [local], remoteTags: [rtA, rtB] };
  p._pendingViews = null; p._pendingTagEdits = {};
  const g = { name: "team", color: "#123456", members: ["m1", "x"], ids: ["gL", rtA.id, rtB.id],
              localId: "gL", homes: ["TESTHOST-A", "TESTHOST-B"], remotes: [rtA, rtB] };
  // ADD prefers the local store when the name exists locally — no remote call at all
  p._editTagUnion(g, { add: ["new1"] });
  assert.equal(setViews.length, 1);
  assert.deepEqual(setViews[0].tags[0].members.slice().sort(), ["m1", "new1"]);
  assert.equal(remote.length, 0, "add lands locally, never forks to remotes");
  // …and on the single home when the name is remote-only
  const gRemote = { ...g, localId: null, ids: [rtA.id], homes: ["TESTHOST-A"], remotes: [rtA] };
  p._editTagUnion(gRemote, { add: ["new2"] });
  assert.deepEqual(remote.pop(), ["TESTHOST-A:g1", { add: ["new2"] }]);
  // REMOVE removes the (name, member) pair from EVERY store holding it — never half-works
  setViews.length = 0; remote.length = 0;
  p._editTagUnion(g, { remove: ["m1"] });
  assert.equal(setViews.length, 1, "the local store cleans");
  assert.deepEqual(remote.map((r: any) => r[0]).sort(), ["TESTHOST-A:g1", "TESTHOST-B:g7"],
    "…and BOTH remote stores holding the pair");
  // a remote NOT holding the member is left alone
  setViews.length = 0; remote.length = 0;
  p._editTagUnion(g, { remove: ["x"] });
  assert.deepEqual(remote.map((r: any) => r[0]), ["TESTHOST-A:g1"], "only the holder is touched");
  // DELETE fans out to every home
  setViews.length = 0; remote.length = 0;
  p._editTagUnion(g, { delete: true });
  assert.equal(setViews.length, 1);
  assert.equal(setViews[0].tags.length, 0, "the local tag goes");
  assert.deepEqual(remote.map((r: any) => r[1].delete), [true, true], "…and every remote home");
});

test("the lane gear carries the SAME tag editor — the shared builders, never a fork (the user 2026-08-24)", () => {
  // both surfaces call the one chip builder and the one join menu
  assert.ok((SRC.match(/this\._tagChips\(/g) || []).length >= 2, "dialog rows AND the gear");
  assert.ok((SRC.match(/this\._tagJoinMenu\(/g) || []).length >= 2, "dialog [+] menus AND the gear");
  // the gear's section: compact label row + [+] behind it, menu vocabulary throughout
  assert.match(SRC, /const tlab = trow\.createSpan\(\{ text: 'Tags' \}\);/);
  assert.match(SRC, /this\._tagJoinMenu\(am, \[s\.id\], build\);/);
});

