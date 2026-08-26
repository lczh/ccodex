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
