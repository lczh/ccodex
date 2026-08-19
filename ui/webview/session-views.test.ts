// Session views on the chat side (the user 2026-08-18): the kernel's views blob — echoed on every
// tabOrder push — gates which sessions get TABS. A hidden session is a BACKGROUND session: still
// running, judged, carded; the + picker lists it under "Hidden — reveal" and the timeline's corner
// panel counts it, so nothing runs in secret (the 2026-08-11 rule this feature deliberately carves
// an exception into, keeping its spirit). Executed tests on the pure module + source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { viewVisible, viewsKey, hideIn, revealIn } from "../../ui/webview/session-views";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

const G = { id: "g1", name: "pool", color: "#DD42FF", members: ["s2"] };

test("executed: the all-view minus hidden; a group exactly; membership beats hidden", () => {
  assert.equal(viewVisible(null, "s1"), true);
  assert.equal(viewVisible({ active: "all", hidden: ["s1"] }, "s1"), false);
  assert.equal(viewVisible({ active: "g1", hidden: ["s2"], groups: [G] }, "s2"), true);
  assert.equal(viewVisible({ active: "g1", groups: [G] }, "s1"), false);
  assert.equal(viewVisible({ active: "gone", groups: [] }, "s1"), true, "an orphaned active fails open");
});

test("executed: hide adds the bit; reveal drops it and falls back to All when the group excludes it", () => {
  const hid = hideIn({ active: "all", hidden: [] }, "s1");
  assert.deepEqual(hid.hidden, ["s1"]);
  assert.deepEqual(hideIn(hid, "s1").hidden, ["s1"], "idempotent");
  // hiding while a group that CONTAINS the session is active must also leave that group — membership
  // beats hidden, so without this the gesture is a silent no-op
  const g = hideIn({ active: "g1", hidden: [], groups: [{ id: "g1", members: ["s2", "s3"] }, { id: "g2", members: ["s2"] }] }, "s2");
  assert.deepEqual(g.hidden, ["s2"]);
  assert.deepEqual(g.groups![0].members, ["s3"], "dropped from the ACTIVE group");
  assert.deepEqual(g.groups![1].members, ["s2"], "other groups keep it");
  const rev = revealIn({ active: "g1", hidden: ["s1"], groups: [G] }, "s1");
  assert.deepEqual(rev.hidden, []);
  assert.equal(rev.active, "all", "the active group excludes it → fall back to All");
  const rev2 = revealIn({ active: "g1", hidden: ["s2"], groups: [G] }, "s2");
  assert.equal(rev2.active, "g1", "a member reveals in place — no view switch");
});

test("executed: the canonical key ignores list order — the kernel normalizer re-sorts", () => {
  const a = { active: "g1", hidden: ["b", "a"], groups: [{ id: "g1", members: ["y", "x"] }] };
  const b = { active: "g1", hidden: ["a", "b"], groups: [{ id: "g1", members: ["x", "y"] }] };
  assert.equal(viewsKey(a), viewsKey(b));
  assert.notEqual(viewsKey(a), viewsKey({ active: "all", hidden: ["a", "b"], groups: [] }));
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
