// Per-session toggles on the timeline lane. History: an EYE (2026-06-22) became a direct-toggle
// checkbox, gained a postal mailbox (2026-06-23) and a notification bell (2026-07-28) — and at THREE
// toggles the icons crowded the lane, so they folded into ONE settings GEAR whose drop-down lists each
// toggle with its icon, state, and a plain-language line (the user 2026-07-28, round 3 — superseding
// the 2026-06-22 "no menu" rule, which held for a single flag). The timeline has no headless render
// harness for the lane header, so — like timeline-view.test.ts — pin the wiring at the source level
// against the shared ui/romp-timeline-view.js (the same file the web dashboard serves verbatim).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");

test("ONE gear column sits between the name and the model (live lanes only)", () => {
  assert.match(SRC, /const eyeColX = PADL \+ Math\.ceil\(maxName\) \+ COLGAP;/);
  assert.match(SRC, /const modelColX = eyeColX \+ \(anyLive \? EYE_W \+ EYE_GAP : 0\);/);
  // the three separate icon columns are gone
  assert.doesNotMatch(SRC, /const mailColX/, "the mailbox column folded into the gear");
  assert.doesNotMatch(SRC, /const bellColX/, "the bell column folded into the gear");
  assert.match(SRC, /if \(s\.live\) \{[\s\S]*?gearIcon\(gcx, gcy, MODEL_FG\)/);
});

test("the gear is DRAWN (hollow toothed ring) and opens the menu on POINTERDOWN (redraw-proof)", () => {
  assert.match(SRC, /function gearIcon\(cx, cy, color\)/);
  // hollow: no hub dot (the user 2026-07-28), matching the ⛭ the rail's settings button wears
  const gear = SRC.slice(SRC.indexOf("function gearIcon"), SRC.indexOf("const LANE_TOGGLES"));
  assert.doesNotMatch(gear, /fill: color/, "the centre stays empty");
  assert.match(gear, /r: 3\.9, fill: 'none'/);
  assert.match(SRC, /const ghit = el\('rect', \{[^}]*fill: 'transparent', 'pointer-events': 'all'/);
  // pointerdown, not click: a lane redraw between mousedown and mouseup replaced the hit-rect so a
  // plain 'click' never fired (the original direct-toggle lesson, 2026-06-23) — the menu opens on press
  assert.match(SRC, /ghit\.addEventListener\('pointerdown', \(e\) => \{[\s\S]*?this\._openLaneMenu\(s, ghit\);/);
  assert.match(SRC, /Session settings<div style='opacity:\.65;margin-top:2px'>feed cards, postal service, notifications<\/div>/);
});

test("the menu lists all three toggles with icons, state words, and plain-language explanations", () => {
  assert.match(SRC, /const LANE_TOGGLES = \[/);
  assert.match(SRC, /\{ flag: 'hideFromFeed', label: 'Feed cards', icon: feedCheckIcon,/);
  assert.match(SRC, /\{ flag: 'postalServiceOff', label: 'Postal service', icon: mailboxIcon,/);
  assert.match(SRC, /\{ flag: 'notify', label: 'Notifications', icon: bellIcon,/);
  // each row explains itself (the user asked for an explanation per toggle, not bare labels)
  assert.match(SRC, /its prompts make cards on the feed; off, the lane stays here but new prompts mint none/);
  assert.match(SRC, /visible to peer sessions, can send and receive their messages; off = fully isolated/);
  assert.match(SRC, /system notification when its work blocks on you or completes/);
  // polarity is encoded per toggle: two off-flags, one on-flag
  assert.match(SRC, /enabled: \(s\) => !s\.hideFromFeed, value: \(enable\) => !enable,/);
  assert.match(SRC, /enabled: \(s\) => !!s\.notify, value: \(enable\) => enable,/);
});

test("menu rows toggle with the SAME optimistic + sticky + reconcile-before-draw treatment as the old icons", () => {
  assert.match(SRC, /const next = t\.value\(!on\);/);
  assert.match(SRC, /\(this\._pendingFlags\[s\.id\] = this\._pendingFlags\[s\.id\] \|\| \{\}\)\[t\.flag\] = next;/);
  assert.match(SRC, /this\._setSessionFlag\(s, t\.flag, next\);\s*\n\s*this\._reconcilePendingFlags\(\);\s*\n\s*this\.draw\(\);/);
  // the panel STAYS OPEN and repaints in place — it's a settings panel, not a command
  assert.match(SRC, /this\.draw\(\);\s*\n\s*build\(\);/);
  // inside clicks must not reach the document-level closer that dismisses the menu
  assert.match(SRC, /menu\.addEventListener\('click', \(e\) => e\.stopPropagation\(\)\);/);
});

test("the gear menu closes on outside click / Escape / teardown, alongside the meta menu", () => {
  assert.match(SRC, /_closeLaneMenu\(\) \{ if \(this\._laneMenu\) \{ this\._laneMenu\.remove\(\); this\._laneMenu = null; \} \}/);
  assert.match(SRC, /this\._onDocClick = \(\) => \{ this\._closeMetaMenu\(\); this\._closeLaneMenu\(\); this\._closeViewsMenu\(\); \};/);
  assert.match(SRC, /if \(e\.key === 'Escape'\) \{ this\._closeMetaMenu\(\); this\._closeLaneMenu\(\); this\._closeViewsMenu\(\); \}/);
});

test("the icon drawers survive (they render inside the menu now): ON = romp blue, OFF = slashed gray", () => {
  assert.match(SRC, /function feedCheckIcon\(off, cx, cy, color\)/);
  assert.match(SRC, /function mailboxIcon\(off, cx, cy, color\)/);
  assert.match(SRC, /function bellIcon\(off, cx, cy, color\)/);
  assert.match(SRC, /const ROMP_BLUE = '#9cd2ff';/);
  assert.match(SRC, /t\.icon\(!on, 8\.5, 8\.5, on \? ROMP_BLUE : MODEL_FG\)/);
});

test("setSessionFlag posts via the web host hook; kernel-down gestures SPOOL for locked replay", () => {
  assert.match(SRC, /_setSessionFlag\(s, flag, value\)/);
  assert.match(SRC, /window\.__rompTimelineSetFlag === 'function'/);
  assert.match(SRC, /window\.__rompTimelineSetFlag\(s\.id, flag, value\)/);
  {
    // KERNEL-FIRST (the v1.3.16 audit's P1.6): the flag write rides the kernel's locked,
    // canonicalizing POST /flag. Kernel down: the gesture is QUEUED (the v1.3.17 audit's P1.5)
    // — the old direct whole-file replace raced OTHER Electron processes even with the kernel
    // down, losing a concurrent writer's hideFromFeed/postalServiceOff and able to recreate
    // migrated TIDs after settlement.
    const fn = SRC.indexOf("_setSessionFlag(s, flag, value)");
    const win = SRC.slice(fn, fn + 2200);
    const kp = win.indexOf("_kernelPost('/flag'");
    const sp = win.indexOf("_spoolOp({ op: 'flag'");
    assert.ok(kp > 0, "the flag writer posts through the kernel");
    assert.ok(sp > kp, "…and QUEUES for replay only after the kernel POST failed");
    assert.match(win.slice(kp, sp), /if \(ok !== false\) return;/,
      "a kernel-accepted OR kernel-REFUSED write never falls back — only a " +
      "network-level failure means kernel-down (the r44 verification)");
    assert.ok(win.indexOf("writeFileSync") < 0 && win.indexOf("session-flags.json") < 0,
      "no direct state-file write survives in the flag path (P1.5)");
  }
  {
    // the spool itself: Electron-gated append to the kernel's replay queue
    const fn = SRC.indexOf("_spoolOp(op) {");
    assert.ok(fn > 0, "the spool helper exists");
    const win = SRC.slice(fn, fn + 1600);
    assert.match(win, /!process\.versions \|\| !process\.versions\.electron/,
      "plain node can never queue into real user state — the spool carries its own guard");
    assert.match(win, /'pending-ui-ops'/,
      "one FILE per op in the spool dir (the v1.3.18 audit's P1: the shared append file's "
      + "rename-aside handoff raced a writer onto an unlinked inode)");
    assert.match(win, /writeFileSync\([\s\S]*\.tmp/, "staged…");
    assert.match(win, /renameSync\(/, "…and atomically published");
  }
  {
    // views edits ride the kernel's TARGETED ops and spool the same grammar kernel-down
    const fn = SRC.indexOf("_setViews(v, ops)");
    assert.ok(fn > 0, "_setViews names its ops");
    const win = SRC.slice(fn, fn + 1800);
    const kp = win.indexOf("_kernelPost('/views'");
    const sp = win.indexOf("_spoolOp({ op: 'views'");
    assert.ok(kp > 0 && sp > kp, "views: kernel first, spool on network failure only");
    assert.ok(win.indexOf("writeFileSync") < 0 && win.indexOf("timeline-views.json") < 0,
      "no direct views-file write survives (P1.5)");
  }
  {
    // the Obsidian ORDER is viewer-local now (the v1.3.17 audit's P2.16): one drag there must
    // never rewrite the shared arrival-order seed every other surface reads
    const fn = SRC.indexOf("_persistOrder(order)");
    const win = SRC.slice(fn, fn + 1400);
    assert.match(win, /localStorage\.setItem\('romp:tl-vieworder'/,
      "the arrangement lives in the viewer's own storage");
    assert.ok(win.indexOf("session-order.json") < 0 && win.indexOf("_kernelPost('/order'") < 0,
      "neither the seed file nor POST /order is touched by an Obsidian drag");
    assert.match(win, /process\.versions\.electron/,
      "plain node can never write real state — the guard survives (the 2026-07-02 lesson)");
  }
  // tag names are USER text: the {}-indexed union builder crashed on __proto__/constructor
  // (the v1.3.16 audit's P2.15)
  assert.match(SRC, /const out = \[\], byName = Object\.create\(null\)/);
  {
    // fan-out tag edits initiate every REMOTE half first; a refused initiation skips the local
    // commit (P2.16: the repro deleted the local tag while the remote edit returned false)
    const fn = SRC.indexOf("_editTagUnion(g, edit)");
    const win = SRC.slice(fn, fn + 4200);
    assert.ok(win.indexOf("removeOk") > 0 && win.indexOf("fanOk") > 0);
    assert.ok(win.indexOf("removeOk = this._editRemoteTag") < win.indexOf("if (removeOk && g.localId)"),
      "remove: remotes initiate before the local half commits");
    assert.ok(win.indexOf("fanOk = this._editRemoteTag") < win.indexOf("if (fanOk && g.localId)"),
      "rename/color/delete: remotes initiate before the local half commits");
    // …and an ASYNC refusal compensates the committed local half (the v1.3.17 audit's P2.11)
    assert.ok(win.indexOf("_noteUnionOp") > 0, "each dispatched remote half records its inverse");
  }
  {
    const fn = SRC.indexOf("tagEditFailed(m) {");
    const win = SRC.slice(fn, fn + 1600);
    assert.ok(win.indexOf("_applyLocalOp(o.inverse)") > 0,
      "the refusing host's entries roll the local half back — the union never stays split");
  }
  {
    // the r45 verification's P1: the CAS refused the rollback itself. Every whole-blob write
    // commits against the optimistic counter (payload rev, +1 per local write), re-anchored on
    // every fresh payload — same-client sequences never self-409.
    const fn = SRC.indexOf("_nextViewsRev() {");
    assert.ok(fn > 0, "the optimistic rev counter exists");
    const sv = SRC.indexOf("_setViews(v, ops) {");
    const win = SRC.slice(sv, sv + 700);
    assert.match(win, /const baseRev = this\._nextViewsRev\(\);/);
    assert.match(win, /v\.baseRev = baseRev; delete v\.rev;/,
      "the stale payload rev never rides a write — the counter is the declared base");
    assert.match(SRC, /this\._optViewsRev = typeof data\.views\.rev === 'number' \? data\.views\.rev : 0;/,
      "every fresh payload re-anchors the counter, so a refused write self-heals");
  }
  {
    // the r45 verification: compensation entries drop on the DECIDING EVENT (the owner's RAW
    // polled store confirms the edit), never a push counter racing a slow refusal
    const fn = SRC.indexOf("_reconcileUnionOps() {");
    const win = SRC.slice(fn, fn + 800);
    assert.ok(win.indexOf("this._views && this._views.remoteTags") > 0,
      "the RAW payload decides — the optimistic overlay would echo our own edit back");
    assert.ok(win.indexOf("_unionOpApplied") > 0);
    assert.ok(win.indexOf("age") < 0, "no push-counter age-out survives");
  }
});

test("the sticky-flag machinery survives: pendingFlags reconcile on every update (no flicker-back)", () => {
  assert.match(SRC, /this\._pendingFlags = \{\};/);
  assert.match(SRC, /this\.data = data;\s*\n(?:[^\n]*\n){0,8}\s*this\._reconcilePendingFlags\(\);/);
  assert.match(SRC, /_reconcilePendingFlags\(\) \{[\s\S]*?if \(s\[flag\] === p\[flag\]\) delete p\[flag\];[\s\S]*?else s\[flag\] = p\[flag\];/);
});

test("every timeline dot's white border is thin (0.75px) — romp + user dots alike (the user 2026-06-23)", () => {
  // the shared dot() helper strokes #e8eef5 at 0.75 (was 1.5) for EVERY dot — prompt dots, the romp swirl
  // dot, etc. (r is lit-conditional since 2026-07-17: a cross-lit dot draws grown in its own color.)
  assert.match(SRC, /el\('circle', \{ cx, cy, r: lit \? DOT_R \+ 2 : DOT_R, fill: color, stroke: '#e8eef5', 'stroke-width': 0\.75 \}\)/);
  assert.doesNotMatch(SRC, /stroke: '#e8eef5', 'stroke-width': 1\.5/, "the old 1.5px dot border is gone");
});

// ── multi-host compensation + the views write acknowledgement (the v1.3.18 audit's P2s) ──────
// P2 "partial multi-host tag edits still split state": a gesture fanned to owners A and B where
// only B refuses used to roll back ONLY the local half — A kept the applied edit, so the union
// read A-changed / B-and-local-reverted. P2 "the views CAS has no acknowledgement": a refused
// setTimelineViews write left the optimistic overlay pinned and the rev counter drifting until
// a full push happened by. Source pins below; the executed tests drive a real TimelinePanel
// over the same house fake-DOM shim timeline-tagorder-drag.test.ts established.

test("source: union-op entries carry rt + gesture id + pre-edit name/color at note time", () => {
  const fn = SRC.indexOf("_noteUnionOp(rt, name, inverse, edit, gid)");
  assert.ok(fn > 0, "the note-op signature threads the gesture id");
  const win = SRC.slice(fn, fn + 900);
  // the rt object + gid must ride the entry: a SIBLING host's refusal dispatches the inverse
  // REMOTE edit here, and only note-time state can say what the inverse targets are
  assert.match(win, /rt: rt, gid: gid \|\| 0,/);
  assert.match(win, /oldName: rt\.name \|\| '', oldColor: rt\.color \|\| ''/);
  // one id per GESTURE, minted where the fan-out starts, threaded to both note sites
  assert.match(SRC, /const gid = \+\+unionGestureSeq;/);
  assert.match(SRC, /\{ remove: edit\.remove\.slice\(\) \}, gid\);/);
  assert.match(SRC, /\{ rename: edit\.rename, color: edit\.color, delete: !!edit\.delete \},\s*\n\s*gid\);/);
});

test("source: tagEditFailed compensates SIBLING hosts — inverse remote edits; delete is loud, never silent", () => {
  const fn = SRC.indexOf("tagEditFailed(m) {");
  const win = SRC.slice(fn, fn + 3800);
  assert.ok(win.indexOf("_applyLocalOp(o.inverse)") > 0, "the local rollback survives untouched");
  // gid-matched entries on OTHER hosts (still unconfirmed — _reconcileUnionOps already dropped
  // confirmed ones) get the inverse REMOTE edit and are dropped as compensated
  assert.match(win, /o\.gid && gids\.has\(o\.gid\)/);
  assert.match(win, /o\.host !== \(m\.host \|\| ''\)/);
  assert.match(win, /if \(e\.remove\) inv\.add = e\.remove\.slice\(\);/);
  assert.match(win, /if \(e\.rename\) inv\.rename = o\.oldName;/);
  assert.match(win, /if \(e\.color\) inv\.color = o\.oldColor;/);
  // a rename that landed re-keyed the tag on that host (edits are name-addressed): the inverse
  // must address the NEW name to rename it back
  assert.match(win, /this\._editRemoteTag\(e\.rename \? Object\.assign\(\{\}, o\.rt, \{ name: e\.rename \}\) : o\.rt, inv\);/);
  // an applied delete has no remote inverse — the by-hand note rides the loud error slot
  assert.ok(win.indexOf("if (e.delete) { undead.push(o.host || 'unknown'); continue; }") > 0,
    "delete siblings are collected for the loud note, never dispatched blind");
  assert.match(win, /recreate the tag there by hand/);
});

// The house fake-DOM shim (same shape as timeline-tagorder-drag.test.ts) so the shared view
// file — plain JS, no DOM library — can instantiate and draw under node:test.
function makeNode(tag: string): any {
  const n: any = {
    tag, _attrs: {}, children: [] as any[], style: {}, dataset: {}, textContent: "", parentNode: null,
    classList: { _s: new Set<string>(), add(...a: string[]) { a.forEach((c) => this._s.add(c)); },
      remove(...a: string[]) { a.forEach((c) => this._s.delete(c)); },
      toggle(c: string, f?: boolean) { f ? this._s.add(c) : this._s.delete(c); }, contains(c: string) { return this._s.has(c); } },
    setAttribute(k: string, v: any) { this._attrs[k] = v; }, getAttribute(k: string) { return this._attrs[k]; },
    setAttributeNS(_n: any, k: string, v: any) { this._attrs[k] = v; }, removeAttribute(k: string) { delete this._attrs[k]; },
    appendChild(c: any) {
      if (c.parentNode) { const i = c.parentNode.children.indexOf(c); if (i >= 0) c.parentNode.children.splice(i, 1); }
      c.parentNode = n; this.children.push(c); return c;
    },
    insertBefore(c: any, ref: any) { c.parentNode = n; const i = this.children.indexOf(ref); i < 0 ? this.children.push(c) : this.children.splice(i, 0, c); return c; },
    removeChild(c: any) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; },
    get firstChild() { return this.children[0] || null; },
    remove() { if (n.parentNode) n.parentNode.removeChild(n); },
    _listeners: {} as any,
    addEventListener(t: string, fn: any) { n._listeners[t] = fn; }, removeEventListener(t: string) { delete n._listeners[t]; },
    setPointerCapture() {}, releasePointerCapture() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return n._rect || { width: 200, height: 20, left: 0, top: 0, right: 200, bottom: 20 }; },
    closest() { return null; }, focus() {}, select() {},
    createEl(t: string, o: any) { const e = makeNode(t); if (o && o.cls) e.classList.add(o.cls); if (o && o.text) e.textContent = o.text; this.appendChild(e); return e; },
    createDiv(o: any) { return this.createEl("div", o); }, createSpan(o: any) { return this.createEl("span", o); },
  };
  return n;
}
const g: any = global;
g.document = {
  createElement(t: string) { return t === "canvas" ? { getContext() { return { font: "", measureText(s: string) { return { width: (s ? s.length : 0) * 6 }; } }; } } : makeNode(t); },
  createElementNS(_n: any, t: string) { return makeNode(t); },
  createTextNode(text: string) { const n = makeNode("#text"); n.textContent = text; return n; },
  body: makeNode("body"), documentElement: makeNode("html"), head: makeNode("head"),
  getElementById() { return null; },
  addEventListener() {}, removeEventListener() {},
};
g.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
g.getComputedStyle = () => ({ backgroundColor: "rgb(30,30,30)", fontFamily: "sans-serif" });
g.requestAnimationFrame = () => 0;
g.addEventListener = () => {}; g.removeEventListener = () => {};
g.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
g.window = g;
g.innerWidth = 1400; g.innerHeight = 800;

const viewPath = path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js");
const { TimelinePanel, viewTagUnion } = createRequire(__filename)(viewPath);

const now = 1_781_000_000;
const sess = (id: string, name: string, color: string) => ({
  id, name, color, state: "working", live: true, model: "Opus", effort: "high",
  context: 40, since: now - 60, awaiting: [], compacting: [], pendingMail: 0, compactions: [], faded: false, stale: false,
});
// ONE name defined on the local store AND two remote owners — the multi-host fan-out shape
const VIEWS = {
  active: "all",
  tags: [{ id: "g1", name: "pool", color: "#DD42FF", members: ["s1", "s2"] }],
  remoteTags: [
    { id: "TESTHOST-A:r1", host: "TESTHOST-A", name: "pool", color: "#7aa2f7", members: ["s1"] },
    { id: "TESTHOST-B:r2", host: "TESTHOST-B", name: "pool", color: "#4EC9B0", members: ["s1"] },
  ],
};

function drawnPanel(): any {
  const panel = new TimelinePanel(makeNode("div"));
  panel.update({
    now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
    turns: { s1: [{ id: "t1", start: now - 400, end: now - 100, prompt: "do the thing", tid: "f1", mids: [] }] },
    messages: [], judging: [], views: JSON.parse(JSON.stringify(VIEWS)),
  });
  return panel;
}

test("executed: one owner's refusal compensates the OTHER owner and the local half — the union never splits", () => {
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  const panel = drawnPanel();
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  assert.equal(panel._unionOps.length, 2, "one compensation entry per dispatched remote half");
  assert.ok(panel._unionOps[0].gid > 0 && panel._unionOps[0].gid === panel._unionOps[1].gid,
    "both halves share the gesture id");
  assert.deepEqual(panel._curViews().tags.find((t: any) => t.id === "g1").members, ["s2"],
    "the local half committed on dispatch success");
  const inverse: any[] = [];
  panel._editRemoteTag = (rt: any, edit: any) => { inverse.push({ rt, edit }); return true; };
  panel.tagEditFailed({ host: "TESTHOST-B", name: "pool", error: "kernel refused" });
  assert.equal(inverse.length, 1, "exactly the one still-unconfirmed sibling gets an inverse edit");
  assert.equal(inverse[0].rt.host, "TESTHOST-A");
  assert.deepEqual(inverse[0].edit, { add: ["s1"] }, "remove inverts to add-back of the removed members");
  assert.deepEqual(panel._curViews().tags.find((t: any) => t.id === "g1").members.slice().sort(),
    ["s1", "s2"], "the local tag regained the member");
  assert.equal(panel._unionOps.length, 0, "refused + compensated entries are all dropped");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews;
});

test("executed: a refused rename compensates the sibling ADDRESSED BY THE NEW NAME, back to the old", () => {
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  const panel = drawnPanel();
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { rename: "crew" });
  const inverse: any[] = [];
  panel._editRemoteTag = (rt: any, edit: any) => { inverse.push({ rt, edit }); return true; };
  panel.tagEditFailed({ host: "TESTHOST-B", name: "pool", error: "kernel refused" });
  assert.equal(inverse.length, 1);
  assert.equal(inverse[0].rt.host, "TESTHOST-A");
  // A applied the rename (the premise of the split), so its tag is keyed "crew" now — the
  // inverse must address that name to land, renaming it back to the note-time old name
  assert.equal(inverse[0].rt.name, "crew");
  assert.deepEqual(inverse[0].edit, { rename: "pool" });
  assert.equal(panel._curViews().tags.find((t: any) => t.id === "g1").name, "pool",
    "the local rename rolled back too");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews;
});

test("executed: an applied delete on a sibling cannot be undone remotely — loud by-hand note, no blind dispatch", () => {
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  const panel = drawnPanel();
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { delete: true });
  const inverse: any[] = [];
  panel._editRemoteTag = (rt: any, edit: any) => { inverse.push({ rt, edit }); return true; };
  panel.tagEditFailed({ host: "TESTHOST-B", name: "pool", error: "kernel refused" });
  assert.equal(inverse.length, 0, "no inverse edit exists for a delete — nothing is dispatched blind");
  assert.ok(panel._curViews().tags.some((t: any) => t.id === "g1"), "the local delete rolled back (re-created)");
  assert.match(panel._tagEditErr.error, /TESTHOST-A/);
  assert.match(panel._tagEditErr.error, /recreate the tag there by hand/);
  assert.equal(panel._unionOps.length, 0, "the sibling entry is still consumed — surfaced, not retried forever");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews;
});

test("viewsAck: the kernel's write acknowledgement re-anchors the rev counter; a refusal drops the overlay", () => {
  // the exact client contract for the kernel's {type:'viewsAck', ok, rev} message
  assert.match(SRC, /viewsAck\(m\) \{/);
  assert.match(SRC, /this\._optViewsRev = \(typeof m\.rev === 'number'\) \? m\.rev : 0;/);
  const panel = drawnPanel();
  panel._optViewsRev = 99;
  panel._pendingViews = { active: "all", tags: [] };
  panel.viewsAck({ ok: false, rev: 3 });
  assert.equal(panel._optViewsRev, 3, "the refusal's rev re-anchors the counter");
  assert.equal(panel._pendingViews, null, "a KNOWN-refused overlay drops now, not after three pushes");
  panel._pendingViews = { active: "all", tags: [] };
  panel.viewsAck({ ok: true, rev: 7 });
  assert.equal(panel._optViewsRev, 7);
  assert.ok(panel._pendingViews, "an accepted write keeps the overlay until the echoing push");
  panel.viewsAck({ ok: true });
  assert.equal(panel._optViewsRev, 0, "a malformed rev anchors at 0, never NaN");
});
