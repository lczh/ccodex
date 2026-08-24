// Session views on the chat side (the user 2026-08-18; TAG model 2026-08-23; ALL default
// 2026-08-24): the kernel's views blob — echoed on every tabOrder push — gates which sessions get
// TABS. Two built-in sentinels: "all" — the default — shows every session minus the hidden set;
// "untagged" keeps the old default's meaning (a tag marks a specialized session, excluded from the
// untagged view and shown under its tag views). A hidden session is a BACKGROUND session: still running, judged,
// carded; the + picker lists it under "Hidden — reveal" and the timeline's corner panel counts it,
// so nothing runs in secret (the 2026-08-11 rule this feature deliberately carves an exception
// into, keeping its spirit). Executed tests on the pure module + source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { viewVisible, viewsKey, hideIn, revealIn } from "../../ui/webview/session-views";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

const G = { id: "g1", name: "pool", color: "#DD42FF", members: ["s2"] };

test("executed: All shows every session minus hidden; untagged the tagless; a tag view its members", () => {
  assert.equal(viewVisible(null, "s1"), true);
  assert.equal(viewVisible({ active: "all", hidden: ["s1"] }, "s1"), false, "All respects the deliberate hide");
  assert.equal(viewVisible({ active: "all", tags: [G] }, "s2"), true, "TAGGED → All still shows it (2026-08-24)");
  assert.equal(viewVisible({ active: "all", tags: [G] }, "s1"), true, "untagged → shown");
  assert.equal(viewVisible({ active: "untagged", tags: [G] }, "s2"), false, "TAGGED → out of the untagged view");
  assert.equal(viewVisible({ active: "untagged", tags: [G] }, "s1"), true, "tagless → the untagged view shows it");
  assert.equal(viewVisible({ active: "untagged", hidden: ["s1"], tags: [G] }, "s1"), false, "hidden hides there too");
  assert.equal(viewVisible({ active: "g1", hidden: ["s2"], tags: [G] }, "s2"), true, "a tag view shows its members, hidden or not");
  assert.equal(viewVisible({ active: "g1", tags: [G] }, "s1"), false);
  assert.equal(viewVisible({ active: "gone", tags: [] }, "s1"), true, "an orphaned active fails open");
  // the pre-rename key an un-updated kernel still pushes reads identically
  assert.equal(viewVisible({ active: "untagged", groups: [G] }, "s2"), false, "legacy `groups` key honored");
  assert.equal(viewVisible({ active: "g1", groups: [G] }, "s2"), true);
});

test("executed: hide sets the one-off bit (and leaves the active tag); reveal SWITCHES views", () => {
  const hid = hideIn({ active: "all", hidden: [] }, "s1");
  assert.deepEqual(hid.hidden, ["s1"], "hiding = the manual one-off hide");
  assert.deepEqual(hideIn(hid, "s1").hidden, ["s1"], "idempotent");
  // hiding while a tag that CONTAINS the session is active must also leave that tag — the tag view
  // shows its members however hidden they are, so without this the gesture is a silent no-op
  const g = hideIn({ active: "g1", hidden: [], tags: [{ id: "g1", members: ["s2", "s3"] }, { id: "g2", members: ["s2"] }] }, "s2");
  assert.deepEqual(g.hidden, ["s2"]);
  assert.deepEqual(g.tags![0].members, ["s3"], "dropped from the ACTIVE tag");
  assert.deepEqual(g.tags![1].members, ["s2"], "other tags keep it — multi-tag membership");
  // reveal never mutates membership (re-grounded 2026-08-23: peeking at a tagged worker must not
  // strip its tag) — it lands on a visible tab with the MINIMAL move (ALL default 2026-08-24)
  const rev = revealIn({ active: "g1", hidden: [], tags: [G] }, "s1");
  assert.equal(rev.active, "all", "tagless session from a tag view → land on All, the default");
  assert.deepEqual(rev.hidden, [], "…and nothing edited");
  const rev2 = revealIn({ active: "untagged", hidden: [], tags: [G] }, "s2");
  assert.equal(rev2.active, "g1", "tagged → its holder tag is its home view");
  assert.deepEqual(rev2.tags![0].members, ["s2"], "membership untouched — the peek never strips a tag");
  const revAll = revealIn({ active: "all", hidden: [], tags: [G] }, "s2");
  assert.equal(revAll.active, "all", "tagged is VISIBLE under All → nothing changes at all");
  const revHid = revealIn({ active: "all", hidden: ["s2"], tags: [G] }, "s2");
  assert.equal(revHid.active, "all", "hidden under All → unhide and STAY — a focus never kicks off All");
  assert.deepEqual(revHid.hidden, [], "…even for a tagged session: the unhide wins, not a holder switch");
  const rev3 = revealIn({ active: "untagged", hidden: ["sX"], tags: [G] }, "sX");
  assert.equal(rev3.active, "untagged", "unhidden tagless session already shows here — no gratuitous switch");
  assert.deepEqual(rev3.hidden, [], "hidden and tagless → un-hidden (the one edit)");
  const rev4 = revealIn({ active: "g1", hidden: ["s2"], tags: [G] }, "s2");
  assert.equal(rev4.active, "g1", "already visible in the active tag → nothing changes");
  const rev5 = revealIn({ active: "g1", hidden: ["sX"], tags: [G] }, "sX");
  assert.deepEqual(rev5.hidden, [], "hidden AND tagless from a tag view → BOTH edits: un-hidden…");
  assert.equal(rev5.active, "all", "…and still invisible after the unhide, so the view switches to All");
  const rev6 = revealIn({ active: "untagged", hidden: ["s2"], tags: [G] }, "s2");
  assert.equal(rev6.active, "g1", "hidden AND tagged under untagged → the holder tag wins");
  assert.deepEqual(rev6.hidden, ["s2"], "…hidden bit untouched — membership beats hidden in a tag view");
});

test("executed: the canonical key ignores list order AND which key the kernel used", () => {
  const a = { active: "g1", hidden: ["b", "a"], tags: [{ id: "g1", members: ["y", "x"] }] };
  const b = { active: "g1", hidden: ["a", "b"], groups: [{ id: "g1", members: ["x", "y"] }] };
  assert.equal(viewsKey(a), viewsKey(b), "a legacy-keyed echo still reconciles an optimistic edit");
  assert.notEqual(viewsKey(a), viewsKey({ active: "all", hidden: ["a", "b"], tags: [] }));
});

test("the tabOrder frame carries the blob and the strip filters on it, composing with #only", () => {
  assert.match(RENDER, /else if \(m\.type === "tabOrder"\) \{ captureViews\(m\.views \|\| null\); applyTabOrder\(m\.order, m\.tabs\); \}/,
    "echo-less frames still reach captureViews — an older kernel must age out a pending edit");
  assert.match(RENDER, /const inViewIds = ids\.filter\(tabInView\);/);
  assert.match(RENDER, /const visibleIds = only \? inViewIds\.filter\(\(id\) => matchesOnly\(nameOf\(id\), only\)\) : inViewIds;/);
  // the active-tab re-point covers BOTH filters — but only for a session that still EXISTS: a
  // torn-down session's reselect belongs to the teardown path's MRU logic, not to us
  assert.match(RENDER, /if \(activeId && ids\.includes\(activeId\) && !visibleIds\.includes\(activeId\) && visibleIds\.length\) \{/);
});

test("every cycling path walks the VISIBLE order — keyboard can never land on a hidden session", () => {
  const hits = RENDER.match(/const ord = visibleOrder\(\);\s*(?:\/\/[^\n]*)?\n/g) || [];
  assert.equal(hits.length, 3, "focused-tab arrows, window arrows, and the shell's cycleTab");
  assert.doesNotMatch(RENDER, /setActive\(order\[\(i \+ dir \+ order\.length\) % order\.length\]\)/,
    "no raw-order cycle survives");
});

test("optimistic edits hold sticky and yield to the kernel after three silent pushes", () => {
  assert.match(RENDER, /function captureViews\(v: SessionViews \| null\) \{[\s\S]{0,600}\+\+pendingViewsAge >= 3/);
  assert.match(RENDER, /function postViews\(v: SessionViews\) \{[\s\S]{0,300}setTimelineViews/);
});

test("a hidden session keeps one visible home: the picker's Hidden section, and picking reveals", () => {
  assert.match(RENDER, /isOpenTab\(it\.id\) && !tabInView\(it\.id\)/);
  assert.match(RENDER, /label\("Hidden — reveal"\)/);
  assert.match(RENDER, /time\.textContent = "hidden";/);
  assert.match(RENDER, /revealSession\(it\.id\);\s*\/\/ its tab already exists[\s\S]{0,80}setActive\(it\.id\);/);
});

test("the tab menu's hide gesture posts the same blob the timeline dialog edits", () => {
  assert.match(RENDER, /l\.textContent = "Hide from chat & timeline";/);
  assert.match(RENDER, /postViews\(hideIn\(effViews\(\), id\)\);/);
});

test("federation carries the LOCAL kernel's views blob through merged tabOrder re-emits", () => {
  const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");
  assert.match(FED, /if \(host === LOCAL && m\.views && typeof m\.views === "object"\) this\.localViews = m\.views;/);
  assert.match(FED, /\{ type: "tabOrder", order, tabs, views: this\.localViews \?\? undefined \}/,
    "without this the browser dashboard's chat never receives the blob at all");
});

test("view edits are INTENT ops — they survive a kernel-restart reconnect instead of dropping", () => {
  const PIPE = fs.readFileSync(path.resolve(process.cwd(), "src", "pipe-intent.ts"), "utf8");
  assert.match(PIPE, /"setTimelineViews"/);
});

test("hiding the last visible session blanks its transcript and the placeholder says why", () => {
  assert.match(RENDER, /const blank = !visibleIds\.length && ids\.length > 0 && !tabInView\(activeId\);/);
  assert.match(RENDER, /Every session is hidden from this view\./);
  assert.match(RENDER, /allHiddenBlanked = false;/, "…and the transcript restores when anything is visible again");
});

test("executed: a REMOTE tag is a view too — resolved, labeled by its host, gone-falls-open (federation v0)", () => {
  // the kernel joins attached kernels' tags into remoteTags (read-only, id = "host:tagid", members
  // already respelled viewer-relative); picking one filters exactly like a local tag
  const rt = { active: "alpha:g9", tags: [], remoteTags: [{ id: "alpha:g9", host: "alpha", members: ["alpha:rs1", "x"] }] };
  assert.equal(viewVisible(rt, "alpha:rs1"), true);
  assert.equal(viewVisible(rt, "y"), false);
  // its kernel detached → the view falls OPEN, never trapping the viewer in an empty view
  assert.equal(viewVisible({ active: "alpha:g9", tags: [] }, "y"), true);
  // the echo key EXCLUDES remoteTags: derived kernel state, not an edit — two blobs differing only
  // there must reconcile the same optimistic edit
  const a = { active: "all", hidden: [], tags: [] };
  const b = { active: "all", hidden: [], tags: [], remoteTags: [{ id: "alpha:g9" }] };
  assert.equal(viewsKey(a), viewsKey(b));
});

test("executed: a tag view is the NAME-KEYED union (user ruling 2026-08-24) — kernels are plumbing", () => {
  const both = { active: "gL",
                 tags: [{ id: "gL", name: "team", members: ["local1"] }],
                 remoteTags: [{ id: "TESTHOST-A:g1", host: "TESTHOST-A", name: "team", members: ["m1"] }] };
  assert.equal(viewVisible(both, "local1"), true);
  assert.equal(viewVisible(both, "m1"), true, "the remote store's member joins the local id's view");
  assert.equal(viewVisible(both, "other"), false);
  assert.equal(viewVisible({ ...both, active: "TESTHOST-A:g1" }, "local1"), true, "either id, same union");
});

