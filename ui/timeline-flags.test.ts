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
    const fn = SRC.indexOf("_spoolOp(op, presetName) {");
    assert.ok(fn > 0, "the spool helper exists");
    const win = SRC.slice(fn, fn + 1600);
    assert.match(win, /!process\.versions \|\| !process\.versions\.electron/,
      "plain node can never queue into real user state — the spool carries its own guard");
    assert.match(win, /'pending-ui-ops'/,
      "one FILE per op in the spool dir (the v1.3.18 audit's P1: the shared append file's "
      + "rename-aside handoff raced a writer onto an unlinked inode)");
    assert.match(win, /'pending-ui-ops\.stage'/,
      "staged in the SIBLING dir — an in-dir tmp raced the kernel's sweep (the r46 re-verify)");
    assert.match(win, /renameSync\(/, "…and atomically published across");
  }
  {
    // views edits ride the kernel's TARGETED ops and spool the same grammar kernel-down
    const fn = SRC.indexOf("_setViews(v, ops, corrId)");
    assert.ok(fn > 0, "_setViews names its ops (corrId = the r54 local-leg correlation)");
    const win = SRC.slice(fn, fn + 4600);   // widened for the r52 retryable-refusal spool
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
    // fan-out tag edits journal FIRST (the v1.3.24 audit's P1.4: a renderer death between
    // the dispatches and the note left applied changes with zero journal rows), then initiate
    // every REMOTE half, then commit the local half; a refused initiation retracts the intent
    const fn = SRC.indexOf("_editTagUnion(g, edit)");
    const win = SRC.slice(fn, fn + 6400);
    assert.ok(win.indexOf("removeOk") > 0 && win.indexOf("fanOk") > 0);
    // the r53 shape: the dispatches live INSIDE a gated closure keyed on the journal's ACK
    // (effects used to run before persistence was known — with every ack forced false, both
    // remotes and the local commit had all executed)
    assert.ok(win.indexOf("const dispatch = () => {") > 0
      && win.indexOf("this._syncUnionOps({ run: dispatch, gids: [gid], name: g.name })") > 0,
      "remove: the effects are HELD behind the journal's acknowledged write (r53 P1.4)");
    assert.ok(win.indexOf("removeOk = this._editRemoteTag") < win.indexOf("this._applyLocalOp(lop)"),
      "remove: remotes initiate before the local half commits");
    const fanGate = win.indexOf("this._syncUnionOps({ run: dispatch, gids: [gid], name: g.name })",
                                win.indexOf("if (edit.rename || edit.color || edit.delete)"));
    assert.ok(fanGate > 0,
      "rename/color/delete: the effects are HELD behind the journal's acknowledged write");
    // the gate registers BEFORE the transport send inside _syncUnionOps itself — registered
    // after the call returned, a synchronously-delivered ack found no gate and the gesture's
    // effects never ran (the r53 verification round's own race)
    const sy = SRC.indexOf("_syncUnionOps(gate, opts) {");
    const sw = SRC.slice(sy, sy + 5200);
    const gateReg = "this._gatedDispatches[opId] = { gates: Array.isArray(gate) ? gate : [gate] };";
    assert.ok(sw.indexOf(gateReg) > 0 && sw.indexOf(gateReg) < sw.indexOf("__rompTimelineSetUnionOps"),
      "the ack-gate is registered before the journal send it keys on — per-gesture sub-gates "
      + "(r54 wave 2: the composite's all-or-nothing yield double-dispatched a claimed gesture)");
    assert.ok(win.indexOf("fanOk = this._editRemoteTag") < win.indexOf("this._setViews(nv, [op])"),
      "rename/color/delete: remotes initiate before the local half commits");
    assert.ok(win.split("filter((o) => o.gid !== gid)").length === 3,
      "a refused initiation RETRACTS both legs' journaled intent — nothing began");
    assert.ok(win.split(">= 2").length >= 3,
      "EVERY multi-owner gesture journals — remote-only two-owner included (r53 P1.3)");
  }
  {
    // the claimed completion's effects are ACK-GATED on the rekey write (r55 P1.1,
    // superseding r54 wave 2's ordering pin: sync-before-dispatch still left the effects
    // running before the write was DURABLE — a failed ack meant three executed effects
    // over a journal that only held the old gid), and the write is the r55 P1.4 CAS
    const fn = SRC.indexOf("_completeUnionGesture(gid) {");
    const win = SRC.slice(fn, fn + 3800);   // widened for the r57 whole-identity undo capture
    assert.ok(win.indexOf("const dispatch = () => {") > 0
      && win.indexOf("self2._editRemoteTag(") > win.indexOf("const dispatch = () => {"),
      "every effect lives inside the gated closure — nothing runs before the ack");
    assert.ok(win.indexOf("const rekey = { ogid: gid, gid: ngid, epoch: epoch };") > 0
      && win.indexOf("{ rekey: rekey }") > 0,
      "…and the write carries the claim epoch the kernel CAS-validates (r55 P1.4)");
    assert.ok(win.indexOf("rekey: rekey }") > 0 && win.indexOf("x.olin = (x.olin || []).concat([x.gid]).slice(-32);") > 0,
      "the gate remembers its CAS for the reconnect replay (r56 P1.2) and the rows carry "
      + "the bounded ancestor lineage (r56 P1.3; bound 32 since r57 — eleven takeovers "
      + "outran 8)");
    assert.ok(win.indexOf("{ run: dispatch, gids: [ngid], name:") > 0,
      "the gate keys on the NEW gid — the ok flips exactly the re-keyed rows");
  }
  {
    const fn = SRC.indexOf("tagEditFailed(m) {");
    const win = SRC.slice(fn, fn + 5600);   // widened for the r52 handled-gate note
    assert.ok(win.indexOf("this._applyLocalOp(o.inverse);") > 0,
      "the refusing host's entries roll the local half back — the union never stays split "
      + "(postimage-guarded since r49: a newer local gesture's value stands)");
  }
  {
    // the r45 verification's P1: the CAS refused the rollback itself. Every whole-blob write
    // commits against the optimistic counter (payload rev, +1 per local write), re-anchored on
    // every fresh payload — same-client sequences never self-409.
    const fn = SRC.indexOf("_nextViewsRev() {");
    assert.ok(fn > 0, "the optimistic rev counter exists");
    const sv = SRC.indexOf("_setViews(v, ops, corrId) {");
    const win = SRC.slice(sv, sv + 1700);   // widened for the v1.3.20 ops branch
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
    const win = SRC.slice(fn, fn + 1800);   // widened for the v1.3.20 P1.3 group retention
    assert.ok(win.indexOf("this._views && this._views.remoteTags") > 0,
      "the RAW payload decides — the optimistic overlay would echo our own edit back");
    assert.ok(win.indexOf("_unionOpApplied") > 0);
    assert.ok(!/\bage\b/.test(win), "no push-counter age-out survives");
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
  const fn = SRC.indexOf("_noteUnionOp(rt, name, inverse, edit, gid, opts)");
  assert.ok(fn > 0, "the note-op signature threads the gesture id");
  const win = SRC.slice(fn, fn + 2600);   // widened for the r49 postimage + durable-sync lines
  // the rt object + gid must ride the entry: a SIBLING host's refusal dispatches the inverse
  // REMOTE edit here, and only note-time state can say what the inverse targets are
  assert.match(win, /rt: rt, gid: gid \|\| 0,\s*\n\s*oldName: rt\.name \|\| '', oldColor: rt\.color \|\| '', post: post/);
  assert.match(win, /oldName: rt\.name \|\| '', oldColor: rt\.color \|\| ''/);
  // one id per GESTURE, minted where the fan-out starts, threaded to both note sites
  assert.match(SRC, /const gid = \+\+unionGestureSeq;/);
  assert.match(SRC, /\{ remove: edit\.remove\.slice\(\) \}, gid,\s*\n\s*\{ defer: true, lop: lop \}\);/);
  assert.match(SRC, /\{ rename: edit\.rename, color: edit\.color, delete: !!edit\.delete \},\s*\n\s*gid, \{ defer: true, lop: op \}\);/);
});

test("source: tagEditFailed compensates SIBLING hosts — inverse remote edits; delete is loud, never silent", () => {
  const fn = SRC.indexOf("tagEditFailed(m) {");
  const win = SRC.slice(fn, fn + 9600);   // widened for the r52 handled-gate note
  assert.ok(win.indexOf("_applyLocalOp(o.inverse)") > 0, "the local rollback survives untouched");
  // a refusal carrying the opId the edit was stamped with compensates EXACTLY that gesture (the
  // 2026-08-26 audit's Finding C — the kernel echoes it); an opId-less frame (an old kernel)
  // falls back to the newest-gid heuristic (the r46 verification: host+name alone also swept
  // OTHER gestures' entries into the rollback)
  assert.match(win, /const newestGid = matched\.reduce\(\(g, o\) => Math\.max\(g, o\.gid \|\| 0\), 0\);/);
  assert.ok(win.indexOf("const ops = matched.filter((o) => m.opId") > 0
    && win.indexOf("(_lin(o, m.opId) || (m.rid && String(o.rid || 0) === String(m.rid)))") > 0
    //  ^ the kernel-resolved ROOT id joined the matcher in r59 P2.1
    && win.indexOf("|| (Array.isArray(o.olin) && o.olin.some((x) => String(x) === String(id)))") > 0
    && win.indexOf(": ((o.gid || 0) === newestGid && !o.confirmed));") > 0,
    "the opId-less fallback never sweeps a poll-CONFIRMED gesture (r48); a refusal naming "
    + "ANY ancestor of a repeatedly re-keyed gesture still matches (r54 P1.2 + r56 P1.3: "
    + "the single ogid lost the immediate predecessor after a second completion)");
  // gid-matched entries on OTHER hosts — INCLUDING poll-confirmed ones, which the group
  // retention keeps precisely for this (the v1.3.20 audit's P1.3) — get the inverse REMOTE
  // edit and are dropped as compensated
  assert.match(win, /o\.gid && gids\.has\(o\.gid\)/);
  assert.match(win, /o\.host !== \(m\.host \|\| ''\)/);
  assert.match(win, /if \(e\.remove\) inv\.add = e\.remove\.slice\(\);/);
  assert.match(win, /if \(e\.rename\) inv\.rename = o\.oldName;/);
  assert.match(win, /if \(e\.color\) inv\.color = o\.oldColor;/);
  // a rename that landed re-keyed the tag on that host (edits are name-addressed): the inverse
  // must address the NEW name to rename it back — and the dispatch mints its OWN gesture id (the
  // r47 verification: an opId-less inverse wire's refusal fell into the newest-gid fallback and
  // swept an unrelated gesture; with a fresh id that matches no entry, a failed rollback is loud
  // via _tagEditErr and never a second rollback cascade)
  assert.match(win, /this\._editRemoteTag\(e\.rename \? Object\.assign\(\{\}, o\.rt, \{ name: e\.rename \}\) : o\.rt, inv,\s*\n\s*\+\+unionGestureSeq\);/);
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

// r53: gestures gate their effects on the journal's unionOpsAck — tests that assert the
// EFFECTS (not the gating itself) ack each sync synchronously, restoring the old timing
function autoAckUnion(panel: any): void {
  const real = panel._syncUnionOps.bind(panel);
  panel._syncUnionOps = (...a: any[]) => {
    const id = real(...a);   // forward the gate — dropping it would strand the dispatch
    if (id) panel.unionOpsAck({ ok: true, opId: id });
    return id;
  };
}

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

test("executed: a poll-CONFIRMED sibling is still compensated when the other owner refuses (v1.3.20 P1.3)", () => {
  // the v1.3.20 audit's P1.3, the audited silent split: once HOST-A's edit appeared in
  // polling, _reconcileUnionOps forgot it — HOST-B's later refusal rolled back local+B while
  // A kept the applied edit, with no inverse attempt and no warning
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  const panel = drawnPanel();
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  assert.equal(panel._unionOps.length, 2);
  const polled = JSON.parse(JSON.stringify(VIEWS));
  polled.remoteTags[0].members = [];            // A applied the remove; a poll echoes it
  panel.update({ now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [], views: polled });
  assert.equal(panel._unionOps.length, 2,
    "the gesture group is retained until EVERY participant is terminal — A's confirmation alone drops nothing");
  assert.ok(panel._unionOps.find((o: any) => o.host === "TESTHOST-A").confirmed,
    "…though A's entry is marked confirmed");
  const inverse: any[] = [];
  panel._editRemoteTag = (rt: any, edit: any) => { inverse.push({ rt, edit }); return true; };
  panel.tagEditFailed({ host: "TESTHOST-B", name: "pool", error: "kernel refused" });
  assert.equal(inverse.length, 1, "the CONFIRMED applier still gets the inverse");
  assert.equal(inverse[0].rt.host, "TESTHOST-A");
  assert.deepEqual(inverse[0].edit, { add: ["s1"] });
  assert.match(panel._tagEditErr.error, /rolled the applied edit back on TESTHOST-A/,
    "the loud error NAMES the host whose applied edit was rolled back");
  assert.equal(panel._unionOps.length, 0);
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews;
});

test("executed: an opId-LESS refusal never sweeps a poll-CONFIRMED gesture (r48)", () => {
  // the r48 verification: with the group retention keeping confirmed entries around (P1.3),
  // the opId-less newest-gid heuristic could roll back an APPLIED, CONFIRMED gesture on the
  // strength of a refusal that names nothing — the cross-gesture rollback one level deeper.
  // A confirmed entry is swept only by an opId MATCH (the kernel named the gesture).
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  const panel = drawnPanel();
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  const polled = JSON.parse(JSON.stringify(VIEWS));
  polled.remoteTags[0].members = [];            // A applied and a poll CONFIRMED it
  panel.update({ now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [], views: polled });
  const inverse: any[] = [];
  panel._editRemoteTag = (rt: any, edit: any) => { inverse.push({ rt, edit }); return true; };
  panel.tagEditFailed({ host: "TESTHOST-A", name: "pool", error: "late opId-less refusal" });
  assert.equal(inverse.length, 0, "no inverse dispatched off a heuristic match to confirmed state");
  assert.deepEqual(panel._curViews().tags.find((t: any) => t.id === "g1").members, ["s2"],
    "the local half of the CONFIRMED gesture stays applied — no heuristic rollback");
  assert.equal(panel._unionOps.length, 2, "the group is retained for its real deciding event");
  assert.ok(panel._tagEditErr, "…and the refusal is still loud");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews;
});

test("executed: a delayed refusal never rolls back a NEWER edit — the postimage guard (r49)", () => {
  // the v1.3.21 audit's P1.6, the green→red→blue schedule: red's late refusal used to blindly
  // invert, painting local and the sibling back to green over blue's newer value
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  const panel = drawnPanel();
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { color: "#ff0000" });       // red rides out (gesture 1)
  const gid1 = panel._unionOps[0].gid;
  panel._editTagUnion(un, { color: "#0000ff" });       // blue supersedes it (gesture 2)
  const polled = JSON.parse(JSON.stringify(VIEWS));
  polled.remoteTags[0].color = "#0000ff";              // A already shows BLUE
  polled.remoteTags[1].color = "#0000ff";
  panel.update({ now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [], views: polled });
  const inverse: any[] = [];
  panel._editRemoteTag = (rt: any, edit: any) => { inverse.push({ rt, edit }); return true; };
  panel.tagEditFailed({ host: "TESTHOST-B", name: "pool", opId: String(gid1),
                        error: "red refused, late" });
  assert.equal(inverse.length, 0, "no inverse rides to a sibling already showing a NEWER value");
  assert.equal(panel._curViews().tags.find((t: any) => t.id === "g1").color, "#0000ff",
    "the local blue stands — red's refusal rolls back nothing it no longer owns");
  assert.match(panel._tagEditErr.error, /NOT rolled back on TESTHOST-A/);
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews;
});

test("executed: the union journal is durable — mirrored on note, re-seeded after a reload (r49)", () => {
  // the v1.3.21 audit's P1.5: _unionOps lived only in panel memory — a reload while one host
  // was pending lost the journal, and its later refusal left applied siblings silently split
  const synced: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any) => synced.push(entries);
  const panel = drawnPanel();
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  assert.ok(synced.length >= 1, "the gesture mirrors durably — ONE batched sync (r53)");
  assert.equal(synced[synced.length - 1].length, 2, "…carrying both dispatched halves");
  const journal = synced[synced.length - 1];
  // THE RELOAD: a fresh panel seeds from the kernel's payload echo and still compensates
  const panel2 = drawnPanel();
  panel2.update({ now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                  turns: {}, messages: [], judging: [], views: JSON.parse(JSON.stringify(VIEWS)),
                  unionOps: journal });
  assert.equal(panel2._unionOps.length, 2, "the reload re-seeded the in-flight journal");
  const inverse: any[] = [];
  panel2._editRemoteTag = (rt: any, edit: any) => { inverse.push({ rt, edit }); return true; };
  panel2.tagEditFailed({ host: "TESTHOST-B", name: "pool", opId: String(journal[0].gid),
                         error: "refused after the reload" });
  assert.equal(inverse.length, 1, "the refusal STILL compensates the sibling — nothing was lost");
  assert.equal(inverse[0].rt.host, "TESTHOST-A");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: an all-confirmed gesture group leaves the list — retention has an exit", () => {
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = () => {};
  const panel = drawnPanel();
  autoAckUnion(panel);                          // effects gate on the journal ack now (r53)
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  assert.equal(panel._unionOps.length, 2);
  const polled = JSON.parse(JSON.stringify(VIEWS));
  polled.remoteTags[0].members = [];
  polled.remoteTags[1].members = [];            // BOTH owners applied it
  polled.tags[0].members = ["s2"];              // …and the LOCAL leg's postimage holds (r53
  //                                               P1.5: settlement judges every participant)
  panel.update({ now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [], views: polled });
  assert.equal(panel._unionOps.length, 0,
    "every participant terminal (all confirmed) — the group retires, nothing is immortal");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
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
  assert.match(SRC, /if \(typeof m\.rev === 'number'\) this\._optViewsRev = m\.rev;/);
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
  assert.equal(panel._optViewsRev, 7,
    "a rev-less ack leaves the counter STANDING (the r52 verification: the proved-read "
    + "refusal acks rev:null, and resetting to 0 rewound the CAS base for nothing)");
});

test("viewsAck is ROUTED on every WS host (the r46 verification): the boot router case + the panel's own frame listener", () => {
  // The kernel sent the frame and the panel defined viewsAck(), but no inbound router dispatched
  // it — dead code on every shipped host, the audited guessed-rev hole intact. VS Code: the boot's
  // dispatchFrame gains the case (executed in timeline-boot.test.ts). The kernel-served browser
  // page boots from the kernel's inline script, so the panel listens for the frame itself — the
  // shim/federation manager re-dispatches every kernel frame as a window "message" event.
  const BOOT = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "timeline-boot.ts"), "utf8");
  assert.match(BOOT, /if \(m\.type === "viewsAck" && panel\.viewsAck\) \{ panel\.viewsAck\(m\); return true; \}/);
  assert.match(SRC, /this\._onWinMsg = \(e\) => \{ const m = e && e\.data; if \(m && m\.type === 'viewsAck'\) this\.viewsAck\(m\); \};/);
  assert.match(SRC, /window\.addEventListener\('message', this\._onWinMsg\);/);
  assert.match(SRC, /window\.removeEventListener\('message', this\._onWinMsg\);/, "destroy unhooks it");
  // executed: a window message frame re-anchors the counter; foreign frames pass by untouched
  const panel = drawnPanel();
  panel._optViewsRev = 99;
  panel._onWinMsg({ data: { type: "viewsAck", ok: true, rev: 12 } });
  assert.equal(panel._optViewsRev, 12, "the frame reaches viewsAck through the listener");
  panel._onWinMsg({ data: { type: "data" } });
  panel._onWinMsg(null);
  assert.equal(panel._optViewsRev, 12, "non-ack frames (and junk) change nothing");
});

test("executed: the Obsidian POST /views path re-anchors the counter from the response rev (the r46 verification)", async () => {
  // The WS ack never reaches this host — it writes via POST /views, whose response body carries
  // {ok, rev} (409 bodies too). Without parsing it the counter kept guessing there.
  delete (g as any).__rompTimelineSetViews;              // no WS hook → the Electron POST branch
  (process.versions as any).electron = "30.0.0";
  try {
    const panel = drawnPanel();
    panel._spoolOp = () => { throw new Error("a kernel-up answer must never spool"); };
    const posts: any[] = [];
    // accepted: 200 {ok:true, rev} — the counter re-anchors; the overlay stays until the echoing push
    panel._kernelPost = (route: string, body: any) => { posts.push([route, body]); return Promise.resolve({ ok: true, json: { ok: true, rev: 41 } }); };
    panel._optViewsRev = 99;
    const nv = JSON.parse(JSON.stringify(panel._curViews()));
    panel._setViews(nv, [{ active: "all" }]);
    await new Promise((r) => setImmediate(r));
    assert.deepEqual(posts[0], ["/views", { ops: [{ active: "all" }] }], "the write went through the kernel");
    assert.equal(panel._optViewsRev, 41, "the response rev re-anchors the counter — no more guessing");
    assert.ok(panel._pendingViews, "an accepted write keeps the overlay until the echoing push");
    // refused (409 {ok:false, rev}): re-anchor AND drop the known-refused overlay; nothing spools
    panel._kernelPost = () => Promise.resolve({ ok: "refused", json: { ok: false, rev: 7 } });
    panel._setViews(nv, [{ active: "all" }]);
    await new Promise((r) => setImmediate(r));
    assert.equal(panel._optViewsRev, 7, "the refusal's rev re-anchors too");
    assert.equal(panel._pendingViews, null, "a KNOWN-refused overlay drops now, not after three pushes");
    // kernel unreachable: no rev to anchor to — the counter holds and the op spools for replay
    const spooled: any[] = [];
    panel._spoolOp = (op: any) => spooled.push(op);
    panel._kernelPost = () => Promise.resolve({ ok: false, json: null });
    panel._optViewsRev = 55;
    panel._setViews(nv, [{ active: "all" }]);
    await new Promise((r) => setImmediate(r));
    assert.equal(panel._optViewsRev, 56, "no server rev → the optimistic counter stands (baseRev consumed one)");
    assert.deepEqual(spooled, [{ op: "views", ops: [{ active: "all" }] }], "kernel-down still spools");
  } finally {
    delete (process.versions as any).electron;
  }
});

test("executed: a refusal compensates ONLY the newest gesture's entries for that host+name (the r46 verification)", () => {
  // Two gestures fanned to the same host+name: the refusal carries no gesture id, and matching by
  // host+name alone rolled back and inverse-dispatched the OLDER gesture's entries too — edits the
  // refusal said nothing about. Only the max-gid group compensates; older groups settle on their
  // own acks/reconciles.
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  const panel = new TimelinePanel(makeNode("div"));
  panel.update({
    now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
    turns: {}, messages: [], judging: [],
    views: {
      active: "all",
      tags: [{ id: "g1", name: "pool", color: "#DD42FF", members: ["s1", "s2"] }],
      remoteTags: [
        { id: "TESTHOST-A:r1", host: "TESTHOST-A", name: "pool", color: "#7aa2f7", members: ["s1", "s2"] },
        { id: "TESTHOST-B:r2", host: "TESTHOST-B", name: "pool", color: "#4EC9B0", members: ["s1", "s2"] },
      ],
    },
  });
  // gesture 1 removes s1, gesture 2 removes s2 — each dispatches to A and B and notes an entry per host
  panel._editTagUnion(viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool"), { remove: ["s1"] });
  panel._editTagUnion(viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool"), { remove: ["s2"] });
  assert.equal(panel._unionOps.length, 4, "two gestures × two dispatched hosts");
  const gid1 = panel._unionOps[0].gid, gid2 = panel._unionOps[2].gid;
  assert.ok(gid2 > gid1, "gestures mint increasing ids");
  assert.deepEqual(panel._curViews().tags.find((t: any) => t.id === "g1").members, [],
    "both local halves committed on dispatch success");
  const inverse: any[] = [];
  panel._editRemoteTag = (rt: any, edit: any) => { inverse.push({ rt, edit }); return true; };
  panel.tagEditFailed({ host: "TESTHOST-B", name: "pool", error: "kernel refused" });
  assert.deepEqual(panel._curViews().tags.find((t: any) => t.id === "g1").members, ["s2"],
    "only the newest gesture's local half rolled back — s1's removal (the older gesture) stands");
  assert.equal(inverse.length, 1, "one sibling inverse — the older gesture's A entry stays quiet");
  assert.equal(inverse[0].rt.host, "TESTHOST-A");
  assert.deepEqual(inverse[0].edit, { add: ["s2"] }, "the inverse restores the newest gesture's members only");
  assert.deepEqual(panel._unionOps.map((o: any) => [o.host, o.gid]),
    [["TESTHOST-A", gid1], ["TESTHOST-B", gid1]],
    "the older gesture's entries survive for their own ack/reconcile");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews;
});

// ── the 2026-08-26 audit's Findings B + C ─────────────────────────────────────────────────────
// B: the Obsidian/Electron no-ops branch reduced every blob write to {ops:[{active}]} — the
// multi-select lens map (v.actives), the union drag order (v.tagOrder), and dialog-minted tags
// all silently vanished on the POST path. A no-ops write posts the REV-GATED WHOLE BLOB now.
// C: the kernel echoes the edit's opId on tagEditFailed, so a delayed refusal rolls back exactly
// the gesture it refused — the newest-gid heuristic stays only for opId-less frames (old kernels).

test("executed: a no-ops _setViews POSTs the whole rev-gated blob — actives/tagOrder/tags survive (Finding B)", async () => {
  delete (g as any).__rompTimelineSetViews;              // no WS hook → the Electron POST branch
  (process.versions as any).electron = "30.0.0";
  try {
    const panel = drawnPanel();
    panel._spoolOp = () => { throw new Error("a kernel-up answer must never spool"); };
    const posts: any[] = [];
    panel._kernelPost = (route: string, body: any) => { posts.push([route, body]); return Promise.resolve({ ok: true, json: { ok: true, rev: 8 } }); };
    panel._optViewsRev = 3;
    const nv = JSON.parse(JSON.stringify(panel._curViews()));
    nv.actives = { timeline: { tags: ["pool"] } };       // the multi-select lens map
    nv.tagOrder = ["pool"];                              // the union drag order
    panel._setViews(nv);                                 // NO ops — the exact shape that was reduced
    await new Promise((r) => setImmediate(r));
    assert.equal(posts[0][0], "/views");
    const body = posts[0][1];
    assert.ok(body.views && !body.ops, "no ops → the whole blob, never the {active} reduction");
    assert.deepEqual(body.views.actives, { timeline: { tags: ["pool"] } }, "the lens map rides");
    assert.deepEqual(body.views.tagOrder, ["pool"], "the drag order rides");
    assert.ok(Array.isArray(body.views.tags) && body.views.tags.length, "tags ride (dialog mints)");
    assert.equal(body.views.baseRev, 3, "the blob is REV-GATED — the optimistic counter's base");
    assert.ok(!("rev" in body.views), "the stale payload rev never rides a write");
    assert.equal(panel._optViewsRev, 8, "the response rev re-anchors via viewsAck (200 and 409 alike)");
    // callers that NAME their ops still compose server-side — the ops path is untouched
    panel._setViews(nv, [{ active: "all" }]);
    await new Promise((r) => setImmediate(r));
    assert.deepEqual(posts[1][1], { ops: [{ active: "all" }] });
    // kernel UNREACHABLE: the replay spool speaks only the op grammar — the blob write degrades
    // to its active pick there only (the pre-existing reduction, confined to the offline spool)
    const spooled: any[] = [];
    panel._spoolOp = (op: any) => spooled.push(op);
    panel._kernelPost = () => Promise.resolve({ ok: false, json: null });
    panel._setViews(nv);
    await new Promise((r) => setImmediate(r));
    assert.deepEqual(spooled, [{ op: "views", ops: [{ active: "all" }] }]);
  } finally {
    delete (process.versions as any).electron;
  }
});

test("executed: _viewsKey sees actives — a dropped lens write is VISIBLE to the reconcile, never masked (Finding B)", () => {
  const panel = drawnPanel();
  const a = { active: "all", tags: [] };
  const b = { active: "all", tags: [], actives: { timeline: { tags: ["pool"] } } };
  assert.notEqual(panel._viewsKey(a), panel._viewsKey(b),
    "blobs differing ONLY in actives hash apart — an echo missing the lens can't fake a confirm");
  assert.equal(panel._viewsKey(JSON.parse(JSON.stringify(b))), panel._viewsKey(b), "same shape, same key");
});

test("executed: a refusal carrying an opId rolls back EXACTLY that gesture — even with a newer one live (Finding C)", () => {
  const wires: any[] = [];
  g.__rompTimelineEditTag = (e: any) => wires.push(e);
  g.__rompTimelineSetViews = () => {};
  const panel = new TimelinePanel(makeNode("div"));
  panel.update({
    now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
    turns: {}, messages: [], judging: [],
    views: {
      active: "all",
      tags: [{ id: "g1", name: "pool", color: "#DD42FF", members: ["s1", "s2"] }],
      remoteTags: [
        { id: "TESTHOST-A:r1", host: "TESTHOST-A", name: "pool", color: "#7aa2f7", members: ["s1", "s2"] },
        { id: "TESTHOST-B:r2", host: "TESTHOST-B", name: "pool", color: "#4EC9B0", members: ["s1", "s2"] },
      ],
    },
  });
  // gesture 1 removes s1, gesture 2 removes s2 — each fans to owners A and B
  panel._editTagUnion(viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool"), { remove: ["s1"] });
  panel._editTagUnion(viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool"), { remove: ["s2"] });
  const gid1 = panel._unionOps[0].gid, gid2 = panel._unionOps[2].gid;
  assert.ok(gid2 > gid1, "gestures mint increasing ids");
  // every dispatched remote edit is STAMPED with its gesture's id — the kernel echoes it back
  assert.equal(wires.length, 4, "two gestures × two owners");
  assert.deepEqual(wires.map((w) => w.opId), [String(gid1), String(gid1), String(gid2), String(gid2)],
    "each wire edit carries ITS OWN gesture's opId");
  const inverse: any[] = [];
  panel._editRemoteTag = (rt: any, edit: any) => { inverse.push({ rt, edit }); return true; };
  // the refusal names gesture 1 — the OLDER one. The newest-gid heuristic would have hit
  // gesture 2 (the delayed-refusal cross-gesture rollback this kills for real).
  panel.tagEditFailed({ host: "TESTHOST-B", name: "pool", opId: String(gid1), error: "kernel refused" });
  assert.deepEqual(panel._curViews().tags.find((t: any) => t.id === "g1").members, ["s1"],
    "gesture 1's local half rolled back (s1 restored); gesture 2's removal of s2 STANDS");
  assert.equal(inverse.length, 1, "one sibling inverse — gesture 2's entries stay quiet");
  assert.equal(inverse[0].rt.host, "TESTHOST-A");
  assert.deepEqual(inverse[0].edit, { add: ["s1"] }, "the inverse restores gesture 1's members only");
  assert.deepEqual(panel._unionOps.map((o: any) => [o.host, o.gid]),
    [["TESTHOST-A", gid2], ["TESTHOST-B", gid2]],
    "gesture 2's entries survive untouched for their own verdicts");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews;
});

test("executed: the compensation's INVERSE dispatch carries its OWN opId — its refusal is loud, never a second rollback (r47)", () => {
  // Finding C's sibling leg: the rollback's inverse remote edit was the one _editRemoteTag call
  // without a gid, so it rode the wire opId-less — and when the sibling refused the INVERSE (the
  // owner went down mid-sequence), the echoed frame carried no opId either, fell into the
  // newest-gid fallback, and swept whichever unrelated gesture was newest by then: its local half
  // reverted and an uncalled-for counter-inverse dispatched — a rollback cascading into a gesture
  // nothing refused. The inverse now mints a fresh gesture id; no _unionOps entry rides it, so its
  // refusal matches nothing and lands as the loud _tagEditErr alone.
  const wires: any[] = [];
  g.__rompTimelineEditTag = (e: any) => wires.push(e);
  g.__rompTimelineSetViews = () => {};
  const panel = new TimelinePanel(makeNode("div"));
  panel.update({
    now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
    turns: {}, messages: [], judging: [],
    views: {
      active: "all",
      tags: [{ id: "g1", name: "pool", color: "#DD42FF", members: ["s1", "s2"] }],
      remoteTags: [
        { id: "TESTHOST-A:r1", host: "TESTHOST-A", name: "pool", color: "#7aa2f7", members: ["s1", "s2"] },
        { id: "TESTHOST-B:r2", host: "TESTHOST-B", name: "pool", color: "#4EC9B0", members: ["s1", "s2"] },
      ],
    },
  });
  // gesture 1 removes s1, gesture 2 removes s2 — both fan to owners A and B, both unconfirmed
  panel._editTagUnion(viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool"), { remove: ["s1"] });
  panel._editTagUnion(viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool"), { remove: ["s2"] });
  const gid1 = panel._unionOps[0].gid, gid2 = panel._unionOps[2].gid;
  assert.equal(wires.length, 4, "two gestures × two owners");
  // B refuses gesture 1 → the rollback dispatches the inverse {add:[s1]} to sibling A
  panel.tagEditFailed({ host: "TESTHOST-B", name: "pool", opId: String(gid1), error: "kernel refused" });
  assert.equal(wires.length, 5, "exactly one inverse dispatch to the still-unconfirmed sibling");
  const inv = wires[4];
  assert.equal(inv.host, "TESTHOST-A");
  assert.deepEqual(inv.add, ["s1"]);
  assert.ok(inv.opId && inv.opId !== String(gid1) && inv.opId !== String(gid2),
    "the inverse rides the wire with a FRESH opId of its own — never opId-less, never a live gesture's");
  // A refuses the INVERSE. The kernel echoes exactly what the wire carried — pre-fix nothing, and
  // the opId-less frame's newest-gid fallback swept gesture 2 (reverting s2's removal and
  // counter-dispatching an uncalled-for {add:[s2]} to B).
  const before = panel._unionOps.map((o: any) => [o.host, o.gid]);
  panel.tagEditFailed({ host: "TESTHOST-A", name: "pool", opId: inv.opId, error: "owner down" });
  assert.equal(wires.length, 5, "a failed rollback dispatches NOTHING — no second rollback cascade");
  assert.deepEqual(panel._curViews().tags.find((t: any) => t.id === "g1").members, ["s1"],
    "gesture 2's local removal of s2 STANDS — the inverse's refusal compensates no one");
  assert.deepEqual(panel._unionOps.map((o: any) => [o.host, o.gid]), before,
    "gesture 2's entries survive untouched for their own verdicts");
  assert.match(panel._tagEditErr.error, /owner down/);   // the failed rollback is LOUD, not silent
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews;
});

test("executed: a retirement leaves the ledger only on the kernel's ack (r50)", () => {
  // the v1.3.22 audit's P1.3: the twin assumed its journal writes landed — a failed save was
  // never retried, and the kernel journal kept the stale group forever. The ledger
  // (_journaledGids) shrinks ONLY by an acked retirement; a refused write re-sends the same
  // retirement on the next payload (an event, not a timer).
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  const gid = panel._unionOps[0].gid;
  const last = wire[wire.length - 1];
  assert.ok(last.opId, "every sync is correlated");
  assert.ok(panel._journaledGids.has(gid), "a SENT gesture enters the ledger — the kernel may hold it");
  panel.unionOpsAck({ ok: true, opId: last.opId });   // release the gated dispatch (r53)
  // the group retires (every participant confirms, LOCAL leg included — r53 P1.5) → the
  // sync carries the retirement…
  const polled = JSON.parse(JSON.stringify(VIEWS));
  polled.remoteTags[0].members = [];
  polled.remoteTags[1].members = [];
  polled.tags[0].members = ["s2"];
  const base = { now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [] };
  panel.update(Object.assign({}, base, { views: polled }));
  const retirePost = wire[wire.length - 1];
  assert.deepEqual(retirePost.retired, [gid]);
  // …but the SAVE FAILS: the ledger stands, and the next payload re-sends the retirement
  panel.unionOpsAck({ ok: false, opId: retirePost.opId });
  assert.ok(panel._journaledGids.has(gid), "a refused write retires nothing");
  panel.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(polled)) }));
  const retry = wire[wire.length - 1];
  assert.notEqual(retry.opId, retirePost.opId, "a fresh correlated post");
  assert.deepEqual(retry.retired, [gid], "the SAME retirement re-sends — nothing assumed landed");
  panel.unionOpsAck({ ok: true, opId: retry.opId });
  assert.ok(!panel._journaledGids.has(gid), "the confirmed retirement finally leaves the ledger");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: a gesture minted and resolved while a sync is UNACKED still gets retired (r50 round)", () => {
  // the verification round's P2 on my own fix: the acked-snapshot watermark held only what
  // some ack had confirmed — a gesture whose send was never acked (a busy kernel) but whose
  // refusal resolved it meanwhile was in NO snapshot, so no sync ever named it retired and
  // its rows stranded in the kernel journal. The ledger records at SEND time.
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });          // sync sent — ack NEVER arrives
  const gid = panel._unionOps[0].gid;
  panel.tagEditFailed({ host: "TESTHOST-B", name: "pool", opId: String(gid),
                        error: "refused before any ack" });   // resolves the group
  const post = wire[wire.length - 1];
  assert.ok(post.retired.indexOf(gid) >= 0,
    "the resolution names the gid retired even though no ack ever confirmed the send");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: the direct refusal frame does NOT erase the journal row other panels still need (r50 wave 3)", () => {
  // the r50 verification round, wave 3: the wave-2 proactive tombstone retired the refusal
  // row within milliseconds of the direct frame — but Obsidian panels receive no frames and
  // a reloaded dashboard's adopted copies were waiting for exactly that row; erasing it
  // stranded their entries forever. The consuming panel now retires the row only when it
  // rides a payload (the scan marks it handled, so nothing re-fires).
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  const gid = panel._unionOps[0].gid;
  panel.tagEditFailed({ host: "TESTHOST-B", name: "pool", opId: String(gid), error: "refused" });
  const post = wire[wire.length - 1];
  assert.ok(post.retired.indexOf(-Math.abs(gid)) < 0,
    "no sight-unseen tombstone — the row lives for the panels that have only the journal");
  // …but when the row RIDES a payload, this panel retires it without re-firing
  const inverse: any[] = [];
  panel._editRemoteTag = (rt: any, edit: any) => { inverse.push(edit); return true; };
  panel.update({ now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [], views: JSON.parse(JSON.stringify(VIEWS)),
                 unionOps: [{ refusal: true, gid: -Math.abs(gid), opId: String(gid),
                              host: "TESTHOST-B", name: "pool", error: "refused" }] });
  assert.equal(inverse.length, 0, "handled by the direct frame — the row's arrival re-fires nothing");
  const tomb = wire[wire.length - 1];
  assert.ok(tomb.retired.indexOf(-Math.abs(gid)) >= 0, "…and NOW the row is tombstoned");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: an adopted group the OWNER retired releases the bystander's copies (r50 wave 3)", () => {
  // the r50 verification round, wave 3: a reloaded panel adopted gesture G's rows; the owner
  // compensated G's refusal and retired the rows — the bystander's copies could never confirm
  // (the refused edit never applies), were retained forever, and re-upserted the dead group
  // into the kernel journal on any later sync
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  const journal = JSON.parse(JSON.stringify(wire[wire.length - 1].entries));
  const gid = journal[0].gid;
  const base = { now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [] };
  // the bystander adopts…
  const panel2 = drawnPanel();
  const inverse: any[] = [];
  panel2._editRemoteTag = (rt: any, edit: any) => { inverse.push(edit); return true; };
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                          unionOps: journal }));
  assert.equal(panel2._unionOps.length, 2, "adopted");
  // …and the owner retires the group: the next journal echo no longer carries it
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                          unionOps: [] }));
  assert.equal(panel2._unionOps.length, 0,
    "the owner's retirement releases the copies — no immortal re-upsert loop");
  assert.equal(inverse.length, 0, "…with no compensation: the owner already settled it");
  // a payload carrying the REFUSAL row instead is the refusal path's to handle, not a drop
  const panel3 = drawnPanel();
  const inv3: any[] = [];
  panel3._editRemoteTag = (rt: any, edit: any) => { inv3.push(edit); return true; };
  panel3.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                          unionOps: journal }));
  panel3.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                          unionOps: [{ refusal: true, gid: -Math.abs(gid),
                                                       opId: String(gid), host: "TESTHOST-B",
                                                       name: "pool", error: "late refusal" }] }));
  assert.equal(inv3.length, 1, "the refusal row still fires the adopted holder's compensation");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: a reload seeds the watermark WITH the entries — retirement still reaches the kernel (r50)", () => {
  // the v1.3.22 audit's P2.4 (first half): the re-seed restored _unionOps but not _syncedGids,
  // so a seeded group's later retirement diffed against nothing — entries:[] retired:[] — and
  // the kernel journal held the stale group forever
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  autoAckUnion(panel);   // r53: the ack landed pre-reload, so the journal rides dispatched:true
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  const journal = JSON.parse(JSON.stringify(wire[wire.length - 1].entries));
  const gid = journal[0].gid;
  // THE RELOAD, then EVERY participant confirms — local postimage included (r53 P1.5: a
  // preimage local store now HOLDS the group and re-posts the local op instead of retiring)
  const panel2 = drawnPanel();
  const polled = JSON.parse(JSON.stringify(VIEWS));
  polled.remoteTags[0].members = [];
  polled.remoteTags[1].members = [];
  polled.tags[0].members = ["s2"];
  panel2.update({ now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                  turns: {}, messages: [], judging: [], views: polled, unionOps: journal });
  assert.ok(panel2._journaledGids.has(gid), "the ledger seeded WITH the entries");
  const retirePost = wire[wire.length - 1];
  assert.equal(retirePost.entries.length, 0);
  assert.deepEqual(retirePost.retired, [gid],
    "the seeded group's retirement reaches the kernel — the journal is not immortal");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: a journaled refusal is consumed after a reload and tombstoned exactly once (r50)", () => {
  // the v1.3.22 audit's P2.4 (second half): the tagEditFailed frame is transient — a panel
  // reloading in the send window lost the one event its re-seeded journal was waiting for.
  // The kernel persists the refusal beside the gestures now; the owning panel consumes it,
  // fires the same compensation the lost frame carried, and retires it with a tombstone.
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  const journal = JSON.parse(JSON.stringify(wire[wire.length - 1].entries));
  const gid = journal[0].gid;
  const refusal = { refusal: true, gid: -777, opId: String(gid), host: "TESTHOST-B",
                    name: "pool", error: "refused while the panel was away" };
  // THE RELOAD: the payload echo carries the journal AND the refusal row
  const panel2 = drawnPanel();
  const inverse: any[] = [];
  panel2._editRemoteTag = (rt: any, edit: any) => { inverse.push({ rt, edit }); return true; };
  const base = { now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [] };
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                          unionOps: journal.concat([refusal]) }));
  assert.equal(inverse.length, 1, "the lost frame's compensation fires from the journal");
  assert.equal(inverse[0].rt.host, "TESTHOST-A", "…on the still-applied sibling");
  assert.match(panel2._tagEditErr.error, /refused while the panel was away/);
  const post = wire[wire.length - 1];
  assert.ok(post.retired.indexOf(-777) >= 0, "the consumed refusal row is tombstoned");
  assert.ok(post.retired.indexOf(gid) >= 0, "…alongside the compensated group");
  // the row rides the NEXT payload too (the tombstone hasn't landed yet) — no second rollback
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                          unionOps: [refusal] }));
  assert.equal(inverse.length, 1, "consumed once, never re-fired");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: another panel's journaled refusal is NOT ours to consume (r50)", () => {
  // ownership: only the panel that minted (or adopted) the gesture may fire its compensation
  // and retire the row — a bystander panel consuming it would strip the owner of the one
  // event it reloads to find
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  const inverse: any[] = [];
  panel._editRemoteTag = (rt: any, edit: any) => { inverse.push({ rt, edit }); return true; };
  panel.update({ now, sessions: [sess("s1", "web", "#f7768e")], turns: {}, messages: [],
                 judging: [], views: JSON.parse(JSON.stringify(VIEWS)),
                 unionOps: [{ refusal: true, gid: -888, opId: "424242", host: "TESTHOST-B",
                              name: "pool", error: "someone else's gesture" }] });
  assert.equal(inverse.length, 0, "no compensation for a gesture this panel never made");
  const tomb = wire.filter((w) => (w.retired || []).indexOf(-888) >= 0);
  assert.equal(tomb.length, 0, "…and no tombstone — the row waits for its owner");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: a viewsAck carrying conflicts surfaces them loudly and drops the overlay (r50)", () => {
  // the v1.3.22 audit's P2.8: a duplicate-name create/rename was acked plain-ok — the dialog
  // kept showing a tag the store never held
  const panel = drawnPanel();
  panel._pendingViews = { active: "all", tags: [] };
  panel.viewsAck({ ok: true, rev: 9, conflicts: ["create 'pool': the name is already taken"] });
  assert.match(panel._tagEditErr.error, /already taken/);
  assert.equal(panel._pendingViews, null, "the optimistic overlay drops — the store's truth shows");
  assert.equal(panel._optViewsRev, 9, "the rev still re-anchors");
});

test("executed: the journal is durable before ANY effect dispatches (r52 P1.4)", () => {
  // the v1.3.24 audit, reproduced there with a synthetic stop: remote dispatches, then the
  // local op, THEN the note — {"events":["remote","local"],"journalRows":0} — a renderer
  // death in that window left applied changes with no durable rollback information
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = () => {};
  const panel = drawnPanel();
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  const rowsAt: number[] = [];
  panel._editRemoteTag = (rt: any, edit: any, gid: any) => {
    rowsAt.push(panel._unionOps.length); return true;
  };
  const realSet = panel._setViews.bind(panel);
  panel._setViews = (v: any, ops: any) => { rowsAt.push(panel._unionOps.length); realSet(v, ops); };
  panel._editTagUnion(un, { remove: ["s1"] });
  assert.equal(rowsAt.length, 0,
    "NOTHING dispatched yet — the effects wait for the journal's ACK (the r53 audit's P1.4: "
    + "with every ack forced false, both remotes and the local commit had already run)");
  const gated = Object.keys(panel._gatedDispatches || {});
  assert.equal(gated.length, 1, "one gated transaction");
  panel.unionOpsAck({ ok: true, opId: gated[0] });
  assert.ok(rowsAt.length >= 3, "two remote dispatches and one local commit ran on the ack");
  for (const n of rowsAt) assert.equal(n, 2,
    "every effect saw the FULL journal already durable — never rows:0 after a dispatch");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: a LIVE panel adopts a foreign journal row and compensates its refusal (r52 round)", () => {
  // the r52 verification round on this round's own P2.5: adoption lived only in the one-shot
  // seed — a panel already open never held the chat's entries, its tagEditFailed matched
  // nothing yet marked the opId handled, and its next payload TOMBSTONED the refusal row the
  // adopting panel needed. Continuous adoption + the matched-only handled gate close both.
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();                       // LIVE — seeded on its first payload
  const inverse: any[] = [];
  panel._editRemoteTag = (rt: any, edit: any) => { inverse.push(edit); return true; };
  const base = { now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [] };
  // the DIRECT refusal frame arrives FIRST (the kernel routes it to this dashboard's
  // timeline) — the panel holds nothing yet, so it must compensate nothing AND leave the
  // refusal unconsumed for its adopting self one payload later
  const chatGid = 777001;
  panel.tagEditFailed({ host: "TESTHOST-A", name: "pool", opId: String(chatGid),
                        error: "owner down" });
  assert.equal(inverse.length, 0, "nothing held, nothing compensated");
  assert.ok(!panel._handledRefusalOps.has(String(chatGid)),
    "…and the frame is NOT marked handled — it compensated nothing (the r52 round: the "
    + "premature mark tombstoned the journal row and made the split permanent)");
  // the next payload carries the chat-minted entries AND the journaled refusal row
  const entry = { host: "TESTHOST-A", name: "pool",
                  inverse: { tag: "g1", add: ["s1"] }, edit: { remove: ["s1"] },
                  rt: { id: "TESTHOST-A:r1", host: "TESTHOST-A", name: "pool", color: "#7aa2f7",
                        members: ["s1"] },
                  gid: chatGid, oldName: "pool", oldColor: "#7aa2f7", post: {}, confirmed: false };
  panel.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                         unionOps: [entry,
                                                    { refusal: true, gid: -chatGid,
                                                      opId: String(chatGid), host: "TESTHOST-A",
                                                      name: "pool", error: "owner down" }] }));
  assert.equal(inverse.length, 0, "one host, no siblings to roll — but the LOCAL half…");
  assert.ok(wire.length > 0, "…was compensated: the journal synced the retirement");
  const last = wire[wire.length - 1];
  assert.ok(last.retired.indexOf(-chatGid) >= 0, "the refusal row is consumed and tombstoned");
  assert.ok(last.retired.indexOf(chatGid) >= 0, "…with its gesture group");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: a live panel RETIRES a foreign gesture its polls confirm (r52 round)", () => {
  // the r52 round's unbounded-growth finding: confirmed chat gestures had no retirer — every
  // open panel ignored foreign gids, and union-gestures.json grew until a panel reloaded
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  const chatGid = 777002;
  const entry = { host: "TESTHOST-A", name: "pool",
                  inverse: { tag: "g1", add: ["s1"] }, edit: { remove: ["s1"] },
                  rt: { id: "TESTHOST-A:r1", host: "TESTHOST-A", name: "pool", color: "#7aa2f7",
                        members: ["s1"] },
                  gid: chatGid, oldName: "pool", oldColor: "#7aa2f7", post: {}, confirmed: false };
  const polled = JSON.parse(JSON.stringify(VIEWS));
  polled.remoteTags[0].members = [];                 // the owner APPLIED the remove
  const base = { now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [] };
  panel.update(Object.assign({}, base, { views: polled, unionOps: [entry] }));
  const last = wire[wire.length - 1];
  assert.ok(last.retired.indexOf(chatGid) >= 0,
    "adopted, confirmed by the SAME payload's polled views, and retired — the journal is "
    + "not immortal for foreign writers");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: a dead writer's journaled-but-undispatched gesture is COMPLETED after two sightings (r53)", () => {
  // the r53 verification round: ack-gating traded the fail-open dispatch for a new stranding —
  // a webview dying between the journal's ack and the gated dispatch left rows whose effects
  // never ran anywhere, immortal (nothing confirmed, nothing retired). An adopting panel now
  // completes them — but only after the row shows dispatched:false on TWO payload sightings,
  // so a live writer's own sub-second flip always wins the race.
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();                        // the writer: gated, NEVER acked (it "dies")
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  const journal = JSON.parse(JSON.stringify(wire[wire.length - 1].entries));
  assert.ok(journal.length === 2 && journal.every((o: any) => o.dispatched === false),
    "the stranded shape: journaled rows, effects never ran");
  assert.ok(journal.some((o: any) => o.lop), "the LOCAL op rides the journal (r53 P1.5)");
  const panel2 = drawnPanel();                       // the adopter
  autoAckUnion(panel2);                              // its rekey write acks (r55: effects gate on it)
  g.__rompTimelineClaimUnion = (gid2: any) => panel2.unionClaimAck({ gid: gid2, ok: true });
  const dispatched: any[] = [];
  panel2._editRemoteTag = (rt: any, edit: any) => { dispatched.push({ rt, edit }); return true; };
  const base = { now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [] };
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                          unionOps: journal }));
  assert.equal(dispatched.length, 0, "FIRST sighting: patience — a live writer may still flip");
  assert.deepEqual(panel2._curViews().tags[0].members, ["s1", "s2"], "…and no local effect yet");
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                          unionOps: journal }));
  assert.equal(dispatched.length, 2, "SECOND sighting: both remote halves dispatch");
  assert.deepEqual(dispatched.map((d) => d.rt.host).sort(), ["TESTHOST-A", "TESTHOST-B"]);
  assert.deepEqual(dispatched[0].edit, { remove: ["s1"] });
  assert.deepEqual(panel2._curViews().tags[0].members, ["s2"],
    "…and the LOCAL leg runs too — the gesture completes whole, never half");
  assert.ok((panel2._unionOps || []).every((o: any) => o.dispatched === true),
    "the completed rows flip dispatched — never re-run on a third sighting");
  assert.ok((panel2._unionOps || []).every((o: any) => o.ogid === journal[0].gid
      && o.gid !== journal[0].gid),
    "the group RE-KEYED with the original carried as ogid (r54 P1.2): a refusal of the "
    + "completion correlates to exactly these rows");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
  delete g.__rompTimelineClaimUnion;
});

test("executed: completion NEVER runs this panel's own still-gated gesture (r53 wave 3)", () => {
  // the wave-3 verification: nothing excluded the panel's own pre-ack rows — two payload
  // echoes before a slow ack completed the gesture, and the arriving ack's gated.run()
  // dispatched it a second time
  const wire: any[] = [];
  const edits: any[] = [];
  g.__rompTimelineEditTag = (e: any) => edits.push(e);
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();                        // gated, ack never arrives
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  const journal = JSON.parse(JSON.stringify(wire[wire.length - 1].entries));
  assert.equal(edits.length, 0, "gated: nothing dispatched yet");
  const base = { now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [] };
  panel.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                         unionOps: journal }));
  panel.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                         unionOps: journal }));
  assert.equal(edits.length, 0, "TWO sightings of our own gated rows complete NOTHING");
  assert.ok((panel._unionOps || []).every((o: any) => o.dispatched === false),
    "…and the rows stay undispatched until OUR ack releases the gate");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: completion is postimage-aware and dispatches under a FRESH id (r53 wave 3)", () => {
  // the wave-3 verification's P1 pair: two bystander panels complete the same dead writer's
  // gesture near-simultaneously — the duplicate rename targets the old, now-gone name, and
  // its gid-correlated refusal rolled the SETTLED gesture back on every other host. Applied
  // halves are marked confirmed (never re-dispatched); what does dispatch wears a fresh id,
  // so a refused duplicate is loud, never a compensation.
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();                        // the dead writer
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  const journal = JSON.parse(JSON.stringify(wire[wire.length - 1].entries));
  const gid = journal[0].gid;
  const panel2 = drawnPanel();                       // the adopter
  autoAckUnion(panel2);                              // its rekey write acks (r55: effects gate on it)
  g.__rompTimelineClaimUnion = (gid2: any) => panel2.unionClaimAck({ gid: gid2, ok: true });
  const calls: any[] = [];
  panel2._editRemoteTag = (rt: any, edit: any, g2: any) => { calls.push({ rt, edit, g2 }); return true; };
  const polled = JSON.parse(JSON.stringify(VIEWS));
  polled.remoteTags[0].members = [];                 // TESTHOST-A already APPLIED the remove
  const base = { now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [] };
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(polled)),
                                          unionOps: journal }));
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(polled)),
                                          unionOps: journal }));
  assert.equal(calls.length, 1, "the applied half is CONFIRMED, never re-dispatched");
  assert.equal(calls[0].rt.host, "TESTHOST-B", "…only the genuinely-missing half dispatches");
  assert.ok(calls[0].g2 && calls[0].g2 !== gid,
    "…under the group's NEW id — correlated (r54 P1.2), and never the settled original's");
  const aRow = (panel2._unionOps || []).find((o: any) => o.host === "TESTHOST-A" && o.ogid === gid);
  assert.equal(aRow.confirmed, true, "the applied half carries its confirmation");
  assert.equal(aRow.gid, calls[0].g2,
    "ONE new id keys the whole group — its refusal compensates siblings and the local half");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
  delete g.__rompTimelineClaimUnion;
});

test("executed: an echo saying dispatched:true refreshes held rows — no completion, no regression (r53 wave 3)", () => {
  // the wave-3 verification's P1.3: adopters kept dispatched:false copies forever, their
  // full-replace syncs regressed the journal, and the completion pass re-ran a live writer's
  // executed gesture
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  const journal = JSON.parse(JSON.stringify(wire[wire.length - 1].entries));
  const panel2 = drawnPanel();
  const calls: any[] = [];
  panel2._editRemoteTag = (rt: any, edit: any, g2: any) => { calls.push(g2); return true; };
  const base = { now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [] };
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                          unionOps: journal }));           // adopts, sighting 1
  const flipped = journal.map((o: any) => Object.assign({}, o, { dispatched: true }));
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                          unionOps: flipped }));           // the writer's flip
  assert.equal(calls.length, 0, "a flipped echo means a LIVE writer — nothing completes");
  assert.ok((panel2._unionOps || []).every((o: any) => o.dispatched === true),
    "…and the held copies converge to true, so this panel's own sync never regresses the journal");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: the lapplied latch is reachable DURING the confirmation window — a re-add survives (r53 wave 3)", () => {
  // the wave-3 verification's P2: latching only at done-time was unreachable (the local leg
  // applies instantly, remotes confirm payloads later) — a user re-adding the member in that
  // window read as 'pre' at done-time and the retry silently re-removed it
  const wire: any[] = [];
  const viewWrites: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = (v: any) => viewWrites.push(v);
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  autoAckUnion(panel);
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });       // local leg applies now
  const base = { now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [] };
  const during = JSON.parse(JSON.stringify(VIEWS));
  during.tags[0].members = ["s2"];                   // local postimage; remotes NOT yet confirmed
  panel.update(Object.assign({}, base, { views: during }));
  assert.ok((panel._unionOps || []).some((o: any) => o.lapplied === true),
    "the latch fires while remotes are still confirming — not only at done-time");
  const writesBefore = viewWrites.length;
  const after = JSON.parse(JSON.stringify(VIEWS));
  after.tags[0].members = ["s1", "s2"];              // the user RE-ADDED s1…
  after.remoteTags[0].members = [];
  after.remoteTags[1].members = [];                  // …and the remotes confirm
  panel.update(Object.assign({}, base, { views: after }));
  assert.equal(viewWrites.length, writesBefore,
    "no retry re-removes the re-added member — the latch says this leg already settled");
  assert.equal((panel._unionOps || []).length, 0, "…and the group retires");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: deleting the panel's LAST local tag still retires — absence is a delete's postimage (r53 wave 3)", () => {
  // the wave-3 verification's immortal-rows pair: the unconditional empty-tags hold could not
  // tell a store fault from the one gesture class whose postimage IS the empty list
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  autoAckUnion(panel);
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { delete: true });
  const gid = (panel._unionOps || [])[0].gid;
  const polled = JSON.parse(JSON.stringify(VIEWS));
  polled.tags = [];                                  // the delete's own local postimage
  polled.remoteTags = [];                            // both owners applied the delete
  panel.update({ now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [], views: polled });
  assert.equal((panel._unionOps || []).length, 0, "the all-confirmed delete retires");
  assert.ok(wire[wire.length - 1].retired.indexOf(gid) >= 0,
    "…and the retirement reaches the kernel — never an immortal journal group");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: the claim decides — a refused claimant completes NOTHING (r54 P1.3)", () => {
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();                        // the dead writer
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  const journal = JSON.parse(JSON.stringify(wire[wire.length - 1].entries));
  const base = { now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [] };
  const mk = () => {
    const p = drawnPanel();
    autoAckUnion(p);                                 // rekey writes ack (r55: effects gate on it)
    const calls: any[] = [];
    p._editRemoteTag = (rt: any, edit: any, g2: any) => { calls.push({ rt, edit, g2 }); return true; };
    return { p, calls };
  };
  const A = mk(), B = mk();
  let granted = 0;
  g.__rompTimelineClaimUnion = (gid2: any) => {
    // the kernel's grant is exclusive: the FIRST claimant wins, every later one is refused
    const ok = granted === 0; granted += 1;
    // both panels share the hook — answer whichever panel asked (pendingClaims says who)
    for (const q of [A.p, B.p]) if (q._pendingClaims && q._pendingClaims[gid2])
      q.unionClaimAck({ gid: gid2, ok: ok });
  };
  for (const round of [1, 2]) {
    A.p.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                         unionOps: journal }));
    B.p.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                         unionOps: journal }));
  }
  assert.equal(A.calls.length + B.calls.length, 2,
    "EXACTLY one panel dispatched the two remote halves — never both (the r54 audit's "
    + "double-completion, executed there as a rolled-back settled rename)");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
  delete g.__rompTimelineClaimUnion;
});

test("executed: a MIXED group completes only its unsent halves (r54 P1.1)", () => {
  // the audited stranding: the writer died BETWEEN sequential sends — A's edit arrived
  // (dispatched:true via the kernel's per-host evidence), B's never did. The all-or-nothing
  // guard skipped the whole group; B's half was unadoptable forever.
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  const journal = JSON.parse(JSON.stringify(wire[wire.length - 1].entries));
  for (const o of journal) if (o.host === "TESTHOST-A") o.dispatched = true;   // A arrived
  const panel2 = drawnPanel();
  autoAckUnion(panel2);                              // rekey writes ack (r55: effects gate on it)
  g.__rompTimelineClaimUnion = (gid2: any) => panel2.unionClaimAck({ gid: gid2, ok: true });
  const calls: any[] = [];
  panel2._editRemoteTag = (rt: any, edit: any, g2: any) => { calls.push({ rt, edit, g2 }); return true; };
  const base = { now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [] };
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                          unionOps: journal }));
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                          unionOps: journal }));
  assert.equal(calls.length, 1, "only the UNSENT half dispatches");
  assert.equal(calls[0].rt.host, "TESTHOST-B");
  assert.ok((panel2._unionOps || []).every((o: any) => o.dispatched === true),
    "…and the whole group reads dispatched after completion");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
  delete g.__rompTimelineClaimUnion;
});

test("executed: a refused COMPLETION compensates the whole re-keyed group (r54 P1.2)", () => {
  // the audited hole: completion dispatched under per-row fresh ids while the journal kept
  // the original gid — B's refusal matched no row, so neither A's applied half nor the
  // local operation was ever compensated
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  const journal = JSON.parse(JSON.stringify(wire[wire.length - 1].entries));
  const panel2 = drawnPanel();
  autoAckUnion(panel2);                              // rekey writes ack (r55: effects gate on it)
  g.__rompTimelineClaimUnion = (gid2: any) => panel2.unionClaimAck({ gid: gid2, ok: true });
  const calls: any[] = [];
  panel2._editRemoteTag = (rt: any, edit: any, g2: any) => { calls.push({ rt, edit, g2 }); return true; };
  const base = { now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [] };
  const polled = JSON.parse(JSON.stringify(VIEWS));
  polled.remoteTags[0].members = [];                 // A applied (the dead writer got one out)
  polled.tags[0].members = ["s2"];                   // the local leg landed too
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(polled)),
                                          unionOps: journal }));
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(polled)),
                                          unionOps: journal }));
  assert.equal(calls.length, 1, "B's half dispatches under the re-keyed id");
  const ngid = calls[0].g2;
  calls.length = 0;
  panel2.tagEditFailed({ host: "TESTHOST-B", name: "pool", opId: String(ngid),
                         error: "kernel refused" });
  assert.equal(calls.length, 1, "the refusal finds the RE-KEYED group and compensates A");
  assert.equal(calls[0].rt.host, "TESTHOST-A");
  assert.deepEqual(calls[0].edit, { add: ["s1"] }, "the inverse restores the removed member");
  assert.deepEqual(panel2._curViews().tags[0].members.slice().sort(), ["s1", "s2"],
    "…and the LOCAL half rolls back too — never a split");
  assert.equal((panel2._unionOps || []).length, 0, "the compensated group retires");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
  delete g.__rompTimelineClaimUnion;
});

test("executed: an UNPROVED payload judges nothing — the audited delete-retirement repro (r54 P1.4)", () => {
  // the executed audit finding: an EIO store rendered tags=[] and the pending local
  // delete's journal row read the emptiness as its postimage — retired on zero information
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  autoAckUnion(panel);
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { delete: true });
  const gid = (panel._unionOps || [])[0].gid;
  const faulted = { active: "all", tags: [], remoteTags: [], unproved: true };
  panel.update({ now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [], views: faulted });
  assert.ok((panel._unionOps || []).some((o: any) => o.gid === gid),
    "the marked payload holds ALL judgment — the recovery rows survive the fault");
  const settled = JSON.parse(JSON.stringify(VIEWS));
  settled.tags = []; settled.remoteTags = [];        // the PROVED postimage of the delete
  panel.update({ now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [], views: settled });
  assert.equal((panel._unionOps || []).length, 0, "…and a PROVED payload still settles it");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: the writer's gate YIELDS gestures the ack names unclaimed (r54 P1.3)", () => {
  const wire: any[] = [];
  const edits: any[] = [];
  g.__rompTimelineEditTag = (e: any) => edits.push(e);
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  const real = panel._syncUnionOps.bind(panel);
  panel._syncUnionOps = (...a: any[]) => {           // ack ok, but a completer owns the gid
    const id = real(...a);
    if (id) panel.unionOpsAck({ ok: true, opId: id,
      unclaimed: (panel._unionOps || []).map((o: any) => o.gid) });
    return id;
  };
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  assert.equal(edits.length, 0, "the gate yielded — the completer's dispatch is the only one");
  assert.equal((panel._unionOps || []).length, 0,
    "…and the yielded rows DROP from this panel's mirror (r54 wave 2: keeping them re-posted "
    + "stale dispatched:false copies on every later sync, resurrecting rows the completer "
    + "had retired)");
  assert.ok(!(panel._journaledGids && panel._journaledGids.size),
    "…forgotten, never retired — the completer owns the journal rows");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: a MIXED unclaimed answer yields per gesture — the composite replay never double-dispatches (r54 wave 2)", () => {
  // the wave-2 verification's P1 (confirmed five ways): unionTransportReset merges every held
  // gate into ONE replay sync; the all-or-nothing yield saw a mixed answer, fell through, and
  // dispatched the completer-owned gesture too — the duplicate's refusal rolled the settled
  // edit back on every host
  const wire: any[] = [];
  const edits: any[] = [];
  g.__rompTimelineEditTag = (e: any) => edits.push(e);
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });       // G1: journaled, gated, ack lost
  const g1 = (panel._unionOps || [])[0].gid;
  panel._editTagUnion(un, { rename: "crew" });       // G2: journaled (fans to both), gated too
  const g2 = (panel._unionOps || []).map((o: any) => o.gid).find((x: any) => x !== g1);
  assert.ok(g2 && g2 !== g1, "two distinct gated gestures");
  panel.unionTransportReset();                        // the reconnect replay: ONE sync, two gestures
  const replayOp = wire[wire.length - 1].opId;
  panel.unionOpsAck({ ok: true, opId: replayOp, unclaimed: [g1] });   // MIXED: a completer owns G1
  assert.ok(edits.length >= 1 && edits.every((e: any) => String(e.remove || "") !== "s1"),
    "G1's dispatches never run — the claim holder owns them");
  assert.ok(edits.some((e: any) => e.rename === "crew"),
    "…while G2's dispatches DO run — the yield is per gesture, never all-or-nothing");
  assert.ok((panel._unionOps || []).every((o: any) => o.gid !== g1),
    "G1's rows dropped (forgotten, not retired)");
  assert.ok((panel._unionOps || []).filter((o: any) => o.gid === g2)
    .every((o: any) => o.dispatched === true), "G2's rows flipped");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: a permanent LOCAL conflict compensates the confirmed remotes (r54 P2.7)", () => {
  // the audited immortal split: remotes confirmed a rename, the local write hit a
  // duplicate-name conflict, and the un-correlated ack meant the retry hammered the same
  // conflict every payload while the journal rows lived forever
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  autoAckUnion(panel);
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { rename: "platform" });
  const gid = (panel._unionOps || [])[0].gid;
  const calls: any[] = [];
  panel._editRemoteTag = (rt: any, edit: any, g2: any) => { calls.push({ rt, edit, g2 }); return true; };
  // remotes CONFIRM the rename; the local store still shows the preimage (its write conflicted)
  const polled = JSON.parse(JSON.stringify(VIEWS));
  polled.remoteTags[0].name = "platform";
  polled.remoteTags[1].name = "platform";
  panel.update({ now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [], views: polled });
  // the payload-paced retry answered with a CONFLICT, correlated to the gesture's local leg
  panel.viewsAck({ ok: false, opId: "lg" + gid,
                   conflicts: ["rename 'pool' → 'platform': the name is already taken"] });
  assert.equal(calls.length, 2, "both confirmed remote halves get the inverse rename");
  assert.deepEqual(calls.map((c) => c.edit.rename).sort(), ["pool", "pool"],
    "…back to the recorded old name");
  assert.equal((panel._unionOps || []).length, 0, "the group retires — never immortal");
  assert.match(panel._tagEditErr.error, /refused.*already taken/s,
    "…and the reason is loud");
  assert.equal(panel._pendingViews, null,
    "…and the refused optimistic paint drops NOW (r54 wave 2: the pinned overlay kept "
    + "rendering the refused rename for three pushes under a banner saying it was refused)");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: a REFUSED rekey write runs ZERO effects — the journal never lied (r55 P1.1)", () => {
  // the audit's executed repro: a failed first ack still ran both remote effects and the
  // local removal while durable storage held only the old gid — a later refusal could
  // compensate nothing. The effects gate on the rekey ack now.
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();                        // the dead writer
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  const journal = JSON.parse(JSON.stringify(wire[wire.length - 1].entries));
  const gid = journal[0].gid;
  const panel2 = drawnPanel();
  const real = panel2._syncUnionOps.bind(panel2);
  panel2._syncUnionOps = (...a: any[]) => {          // the rekey write FAILS
    const id = real(...a);
    if (id) panel2.unionOpsAck({ ok: false, opId: id });
    return id;
  };
  g.__rompTimelineClaimUnion = (gid2: any) => panel2.unionClaimAck({ gid: gid2, ok: true, epoch: 7 });
  const calls: any[] = [];
  panel2._editRemoteTag = (rt: any, edit: any, g2: any) => { calls.push(g2); return true; };
  const base = { now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [] };
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                          unionOps: journal }));
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                          unionOps: journal }));
  assert.equal(calls.length, 0, "NOTHING dispatched over an unacked rekey");
  assert.deepEqual(panel2._curViews().tags[0].members, ["s1", "s2"],
    "…and the local leg never ran either");
  assert.ok((panel2._unionOps || []).every((o: any) => o.gid !== gid && o.ogid !== gid),
    "the failed transaction's rows left this panel's mirror (forgotten, never retired)");
  // recovery: the refused transaction suppressed the original, and the very next GRANTED
  // claim (nobody owns it — the CAS refused ours) legitimately lifts the suppression; once
  // the store heals, the gesture completes whole under the fresh claim
  panel2._syncUnionOps = real;
  autoAckUnion(panel2);
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                          unionOps: journal }));
  assert.equal(calls.length, 2,
    "…and the gesture RECOVERS under a fresh claim once the store heals — both halves run");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
  delete g.__rompTimelineClaimUnion;
});

test("executed: adoption copies EVERY participant of a multi-host gesture (r55 P1.2)", () => {
  // the audit's executed repro: the gid-wide guards updated after the FIRST row, so host
  // B's half was skipped — completion then retired both old rows carrying only A, and B's
  // operation was lost outright
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  const journal = JSON.parse(JSON.stringify(wire[wire.length - 1].entries));
  assert.equal(journal.length, 2, "two hosts, two rows");
  const panel2 = drawnPanel();
  panel2.update({ now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                  turns: {}, messages: [], judging: [],
                  views: JSON.parse(JSON.stringify(VIEWS)), unionOps: journal });
  assert.deepEqual((panel2._unionOps || []).map((o: any) => o.host).sort(),
    ["TESTHOST-A", "TESTHOST-B"],
    "BOTH (gid, host) rows adopted — eligibility decides once per gid, then every row copies");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: a yielded gid is SUPPRESSED until proof — no stale-echo resurrection (r55 P1.5)", () => {
  const wire: any[] = [];
  const edits: any[] = [];
  g.__rompTimelineEditTag = (e: any) => edits.push(e);
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  const real = panel._syncUnionOps.bind(panel);
  panel._syncUnionOps = (...a: any[]) => {           // every gesture yields to a completer
    const id = real(...a);
    if (id) panel.unionOpsAck({ ok: true, opId: id,
      unclaimed: (panel._unionOps || []).map((o: any) => o.gid) });
    return id;
  };
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  const gid = Array.from(panel._yieldedGids || [])[0];
  assert.ok(gid, "the yielded gid is suppressed");
  panel._syncUnionOps = real;
  const base = { now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [] };
  const staleRow = { host: "TESTHOST-A", name: "pool", inverse: {}, edit: { remove: ["s1"] },
                     rt: { id: "TESTHOST-A:r1", host: "TESTHOST-A", name: "pool",
                           color: "#7aa2f7", members: ["s1"] },
                     gid: gid, oldName: "pool", oldColor: "#7aa2f7", post: {},
                     confirmed: false, dispatched: false };
  panel.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                         unionOps: [staleRow] }));
  assert.ok(!(panel._unionOps || []).some((o: any) => o.gid === gid),
    "a STALE echo still carrying the gid is NOT re-adopted (the audit's executed repro: "
    + "re-adoption republished rows the completer had retired)");
  panel.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                         unionOps: [] }));
  panel.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                         unionOps: [] }));
  assert.ok(!(panel._yieldedGids && panel._yieldedGids.has(gid)),
    "…and PROVEN absence (two consecutive echoes — one stale re-emit is a mark, not proof, "
    + "r55 wave 2) ends the suppression — the completer settled it");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: a granted claim for a yielded gid recovers the gesture — the completer died (r55 P1.5)", () => {
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  autoAckUnion(panel);
  panel._yieldUnionGids([424242]);                   // yielded earlier; the completer then died
  let asked = 0;
  g.__rompTimelineClaimUnion = (gid2: any) => { asked += 1; panel.unionClaimAck({ gid: gid2, ok: true, epoch: 3 }); };
  const calls: any[] = [];
  panel._editRemoteTag = (rt: any, edit: any, g2: any) => { calls.push(g2); return true; };
  const row = { host: "TESTHOST-A", name: "pool", inverse: {}, edit: { remove: ["s1"] },
                rt: { id: "TESTHOST-A:r1", host: "TESTHOST-A", name: "pool",
                      color: "#7aa2f7", members: ["s1"] },
                gid: 424242, oldName: "pool", oldColor: "#7aa2f7", post: {},
                confirmed: false, dispatched: false };
  const base = { now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [] };
  panel.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                         unionOps: [row] }));       // sighting 1 (suppressed)
  panel.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                         unionOps: [row] }));       // sighting 2 → claim asked
  assert.ok(asked >= 1, "a suppressed gid still competes for the claim");
  panel.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                         unionOps: [row] }));       // adopt + complete
  assert.equal(calls.length, 1,
    "the grant PROVES the completer died — the gesture recovers and completes here");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
  delete g.__rompTimelineClaimUnion;
});

test("the POST claim ack forwards the kernel's epoch — the CAS token survives the transport (r55 wave 2)", () => {
  // confirmed four ways: the dropped epoch made every Obsidian-side completion CAS-refuse
  // forever, and each re-grant refreshed the POST claim's TTL — starving the gesture across
  // every surface while the panel polled
  const fn = SRC.indexOf("_requestUnionClaim(gid) {");
  const win = SRC.slice(fn, fn + 1600);
  assert.ok(win.indexOf("epoch: r.json && r.json.epoch") > 0,
    "the /union-claim response's epoch rides into unionClaimAck");
  const ca = SRC.indexOf("unionClaimAck(m) {");
  const cw = SRC.slice(ca, ca + 1200);
  assert.ok(cw.indexOf("this._claimEpochs[m.gid] = m.epoch;") > 0,
    "…and is stored for the rekey CAS");
});

test("executed: one absent echo is NOT settlement proof — unsuppress needs two (r55 wave 2)", () => {
  // the wave-2 verification: a stale federated re-emit whose cached journal predated the
  // gesture read as 'absent', the suppression lifted, and the next REAL push re-adopted
  // rows the completer still owned
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  autoAckUnion(panel);
  panel._yieldUnionGids([515151]);
  const base = { now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [] };
  const row = { host: "TESTHOST-A", name: "pool", inverse: {}, edit: { remove: ["s1"] },
                rt: { id: "TESTHOST-A:r1", host: "TESTHOST-A", name: "pool",
                      color: "#7aa2f7", members: ["s1"] },
                gid: 515151, oldName: "pool", oldColor: "#7aa2f7", post: {},
                confirmed: false, dispatched: false };
  panel.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                         unionOps: [] }));          // the STALE re-emit
  assert.ok(panel._yieldedGids.has(515151),
    "ONE absent echo is a mark, not proof — the suppression holds");
  panel.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                         unionOps: [row] }));       // the real push: still live
  assert.ok(panel._yieldedGids.has(515151), "…a live sighting resets the mark");
  assert.ok(!(panel._unionOps || []).some((o: any) => o.gid === 515151),
    "…and the completer's rows are never re-adopted");
  panel.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                         unionOps: [] }));
  panel.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                         unionOps: [] }));
  assert.ok(!panel._yieldedGids.has(515151),
    "TWO consecutive absent echoes: the completer settled it — suppression ends");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
});

test("executed: the reconnect replay carries the completion's CAS — never a plain-merge bypass (r56 P1.2)", () => {
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any, rekey: any) =>
    wire.push({ entries, retired, opId, rekey });
  const panel = drawnPanel();
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });
  const journal = JSON.parse(JSON.stringify(wire[wire.length - 1].entries));
  const gid = journal[0].gid;
  const panel2 = drawnPanel();
  g.__rompTimelineClaimUnion = (gid2: any) => panel2.unionClaimAck({ gid: gid2, ok: true, epoch: 9 });
  panel2._editRemoteTag = () => true;
  const base = { now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [] };
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                          unionOps: journal }));
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                          unionOps: journal }));       // claim → rekey sync (unacked)
  const sent = wire[wire.length - 1];
  assert.ok(sent.rekey && sent.rekey.ogid === gid && sent.rekey.epoch === 9,
    "the rekey write carries the CAS");
  const wireBefore = wire.length;
  panel2.unionTransportReset();                       // the socket died pre-ack: UNWIND
  const after = wire.slice(wireBefore);
  assert.ok(after.every((w: any) => !w.rekey),
    "a completion gate NEVER replays (r56 wave 2: its CAS cannot pass — the claim died with "
    + "the socket — and the refused replay plus the plain sync's un-CAS'd retirement erased "
    + "the gesture entirely)");
  assert.ok(after.every((w: any) => (w.retired || []).indexOf(gid) < 0),
    "…and NO retirement of the original ever rides the reset — the journal rows survive");
  assert.ok(panel2._yieldedGids && panel2._yieldedGids.has(gid),
    "…the unwind suppresses both keys; re-adoption + a fresh claim recover the gesture");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
  delete g.__rompTimelineClaimUnion;
});

test("executed: a refusal naming the MIDDLE ancestor of a twice-completed gesture still compensates (r56 P1.3)", () => {
  const wire: any[] = [];
  g.__rompTimelineEditTag = () => {};
  g.__rompTimelineSetViews = () => {};
  g.__rompTimelineSetUnionOps = (entries: any, retired: any, opId: any) =>
    wire.push({ entries, retired, opId });
  const panel = drawnPanel();
  const un = viewTagUnion(panel._curViews()).find((u: any) => u.name === "pool");
  panel._editTagUnion(un, { remove: ["s1"] });        // g0, dead writer
  const journal = JSON.parse(JSON.stringify(wire[wire.length - 1].entries));
  const g0 = journal[0].gid;
  const panel2 = drawnPanel();
  autoAckUnion(panel2);
  let grants = 0;
  g.__rompTimelineClaimUnion = (gid2: any) => { grants += 1; panel2.unionClaimAck({ gid: gid2, ok: true, epoch: grants }); };
  const calls: any[] = [];
  panel2._editRemoteTag = (rt: any, edit: any, g2: any) => { calls.push({ rt, edit, g2 }); return true; };
  const base = { now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
                 turns: {}, messages: [], judging: [] };
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                          unionOps: journal }));
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                          unionOps: journal }));       // completion 1: g0 → g1
  const g1 = calls.length ? calls[0].g2 : 0;
  assert.ok(g1 && g1 !== g0, "first completion re-keyed");
  // force a SECOND completion of the same group (the first's flip never reached the echo):
  for (const o of panel2._unionOps) o.dispatched = false;
  panel2._undispatchedSeen = new Set([g1]);
  const echo = JSON.parse(JSON.stringify(panel2._unionOps));
  panel2.update(Object.assign({}, base, { views: JSON.parse(JSON.stringify(VIEWS)),
                                          unionOps: echo }));          // completion 2: g1 → g2
  const g2v = calls[calls.length - 1].g2;
  assert.ok(g2v && g2v !== g1, "second completion re-keyed again");
  assert.ok((panel2._unionOps || []).every((o: any) =>
      Array.isArray(o.olin) && o.olin.indexOf(g1) >= 0 && o.olin.indexOf(g0) >= 0),
    "the rows carry the WHOLE bounded lineage");
  calls.length = 0;
  panel2.tagEditFailed({ host: "TESTHOST-B", name: "pool", opId: String(g1),
                         error: "kernel refused" });
  assert.ok(calls.length >= 1,
    "a refusal naming the MIDDLE ancestor matches and compensates (the r56 audit's P1.3, "
    + "executed there: the single oldest ogid matched zero rows and zero inverses ran)");
  delete g.__rompTimelineEditTag; delete g.__rompTimelineSetViews; delete g.__rompTimelineSetUnionOps;
  delete g.__rompTimelineClaimUnion;
});

test("a transport reset clears the claim epochs — no guaranteed-stale CAS attempt (r56 P2.11)", () => {
  const panel = drawnPanel();
  panel._claimEpochs = { 777: 4 };
  g.__rompTimelineSetUnionOps = () => {};
  panel.unionTransportReset();
  assert.deepEqual(panel._claimEpochs, {},
    "the claims died with the socket; a kept epoch made the next completion attempt refuse "
    + "before recovering");
  delete g.__rompTimelineSetUnionOps;
});

test("r57: an indeterminate journal ack UNWINDS, skipped retirements re-ledger, the undo restores the whole identity", () => {
  {
    // P1.1: a dead response is not a refusal — the kernel may have COMMITTED. The old
    // definitive ok:false retired the possibly-committed successor and the durable journal
    // ended empty.
    assert.ok(SRC.indexOf("indeterminate: r.ok !== true && !r.json") > 0,
      "a json-less POST outcome is marked indeterminate, never a definitive refusal");
    assert.ok(SRC.indexOf(
      ".catch(() => this.unionOpsAck({ ok: false, opId: opId, indeterminate: true }))") > 0,
      "a network-level death synthesizes the indeterminate ack");
  }
  {
    const fn = SRC.indexOf("unionOpsAck(m) {");
    const win = SRC.slice(fn, fn + 8000);
    const ind = win.indexOf("if (m.ok === false && m.indeterminate) {");
    const def1 = win.indexOf("if (m.ok === false) {");
    assert.ok(ind > 0 && def1 > ind,
      "the ok:false path splits on indeterminate BEFORE the definitive-refusal arm");
    const arm = win.slice(ind, def1);
    assert.ok(arm.indexOf(
      "this._yieldUnionGids(g1.rekey ? [g1.rekey.gid, g1.rekey.ogid] : g1.gids);") > 0,
      "…and UNWINDS both identities of every gated gesture — retire NOTHING, refuse NOTHING");
    assert.ok(arm.indexOf("_unionSyncDirty = true") > 0 && arm.indexOf("_syncUnionOps(") < 0,
      "no immediate retry rides the unknown outcome — the paced re-send's ledger diff decides");
    assert.ok(win.indexOf(
      "const _unret = new Set(Array.isArray(m.unretired) ? m.unretired : []);") > 0
      && win.indexOf("if (_unret.has(g)) continue;") > 0,
      "a retirement the kernel SKIPPED over a live claim stays in the ledger so it re-sends "
      + "(r57 P2.12: the silent skip dropped the panel's retry evidence)");
  }
  {
    // P2: the no-transport undo restored gid alone — a second-generation row kept ogid/olin
    // claiming a rekey that never happened, and the refusal-compensation matcher misfired
    const fn = SRC.indexOf("_completeUnionGesture(gid) {");
    const win = SRC.slice(fn, fn + 3800);
    assert.ok(win.indexOf("const prior = group.map((x) => ({ x: x, gid: x.gid, ogid: x.ogid,") > 0,
      "the whole prior identity is captured before the re-key");
    assert.ok(win.indexOf("pr.x.gid = pr.gid; pr.x.ogid = pr.ogid; pr.x.olin = pr.olin;") > 0,
      "…and the undo restores gid AND ogid AND olin");
  }
});

test("r57 wave 2: a received 4xx is DEFINITIVE, and the indeterminate/unretired arms are EXECUTED, not just pinned", () => {
  assert.ok(SRC.indexOf("&& !(r.status >= 400 && r.status < 500) }))") > 0,
    "a received 4xx (403 auth, 413 size) proves the kernel rejected pre-commit — only "
    + "network death and json-less non-4xx stay indeterminate (wave 2, reproduced: a "
    + "token-less panel's 403 text unwound with 'recovers by itself' and silently lost "
    + "the edit forever, where the definitive arm surfaces an actionable error)");
  assert.ok(SRC.indexOf("status: status || 0 } : ok)") > 0,
    "_kernelPost's fold carries the HTTP status the classifier needs");
  assert.ok(SRC.indexOf(".catch(() => this.unionOpsAck({ ok: false, opId: opId }))") < 0,
    "the dead definitive-refusal synthesis is gone — chained after the indeterminate "
    + "catch, it re-armed exactly the removed behavior if unionOpsAck ever threw");
  {
    // EXECUTED indeterminate ack (wave 2: every prior assertion was a source-grep pin —
    // deleting the arm's `return` fell through into the definitive arm, fired the
    // forbidden immediate retry, overwrote the error, and the full suite stayed green)
    const panel = drawnPanel();
    panel._unionOps = [{ gid: 41, host: "TESTHOST-A", edit: {}, inverse: {}, rt: {},
                         name: "pool", dispatched: false }];
    if (!panel._journaledGids) panel._journaledGids = new Set();
    panel._journaledGids.add(40);                    // a retirement riding this sync
    panel._pendingUnionSyncs = { op9: { entries: [], retired: [40], tomb: [] } };
    panel._gatedDispatches = { op9: { gates: [{ gids: [41], name: "pool",
      run: () => { throw new Error("the gated effects must NOT run on indeterminate"); } }] } };
    let retries = 0;
    panel._syncUnionOps = () => { retries += 1; return null; };
    panel.unionOpsAck({ ok: false, opId: "op9", indeterminate: true });
    assert.ok(panel._journaledGids.has(40),
      "retire NOTHING: the ledger keeps the retirement for the paced re-send");
    assert.equal(retries, 0,
      "no immediate retry rides the unknown outcome (the deleted-return mutation fires one)");
    assert.equal(panel._unionSyncDirty, true, "the paced re-send is armed");
    assert.ok(panel._unionOps.every((o: any) => o.gid !== 41),
      "the gated gesture's rows unwound from the mirror");
    assert.ok(panel._tagEditErr && /connection dropped/.test(panel._tagEditErr.error),
      "…and the user is told the outcome is unknown, never that the edit was refused");
  }
  {
    // EXECUTED unretired re-ledger (wave 2, reproduced: the kept gid had NO re-send
    // trigger — claim-clear notifies nobody, so a quiet panel stranded the settled rows
    // in the journal forever)
    const panel = drawnPanel();
    if (!panel._journaledGids) panel._journaledGids = new Set();
    panel._journaledGids.add(51);
    panel._journaledGids.add(52);
    panel._pendingUnionSyncs = { op10: { entries: [], retired: [51, 52], tomb: [] } };
    panel._unionSyncDirty = false;
    panel.unionOpsAck({ ok: true, opId: "op10", unclaimed: [], unretired: [52] });
    assert.ok(!panel._journaledGids.has(51), "the honored retirement leaves the ledger");
    assert.ok(panel._journaledGids.has(52), "the claim-skipped one stays…");
    assert.equal(panel._unionSyncDirty, true, "…and ARMS the paced re-send");
  }
});

test("r58: queued sends of a moved world are void, rid correlation is stable, unproved echoes arm reconstruction", async () => {
  {
    // P1.3 EXECUTED (reproduced upstream: a snapshot captured at queue time survived the
    // reconnect unwind, flushed after the reset, and the kernel forked one gesture into
    // two separately claimable identities). The body now builds at the send head under a
    // world-epoch guard.
    const panel = drawnPanel();
    const posts: any[] = [];
    (panel as any)._kernelPost = (route: string, body: any) => {
      posts.push(body);
      return Promise.resolve({ ok: true, json: { ok: true }, status: 200 });
    };
    const realProcess = (globalThis as any).process;
    (globalThis as any).process = { versions: { electron: "1" } };
    try {
      panel._unionOps = [{ gid: 61, host: "TESTHOST-A", edit: {}, inverse: {}, rt: {},
                           name: "pool", dispatched: false }];
      const opId = panel._syncUnionOps();
      assert.ok(opId, "the Electron arm queued a send");
      panel._unionOps = [];                        // the gesture UNWINDS…
      panel.unionTransportReset();                 // …and the world resets BEFORE the flush
      await panel._postChain;
      assert.ok(posts.every((b) => !b || !(b.entries || []).some((o: any) => o.gid === 61)),
        "no queued closure sent the pre-unwind rows — the body builds at the send head "
        + "under the epoch guard (old code captured them at queue time and the flushed "
        + "snapshot forked the gesture into two claimable identities)");
    } finally {
      (globalThis as any).process = realProcess;
    }
  }
  {
    // P2.19: the STABLE root id is minted once and survives re-keying — refusal
    // correlation no longer dies past the lineage cap
    assert.ok(SRC.indexOf("rid: o.rid || (o.rid = ((o.olin && o.olin[0]) || o.ogid || o.gid)),") > 0,
      "every journaled row carries rid, minted from the earliest known ancestor");
    assert.ok(SRC.indexOf("|| String(o.rid || 0) === String(id)") > 0,
      "…and the refusal matcher correlates by it");
  }
  {
    // P1.1 twin half: a null echo while this panel HOLDS journaled rows arms the mirror
    // re-sync — the kernel's merge reconstructs the judged store and clears its marker
    const panel = drawnPanel();
    panel._unionOps = [{ gid: 71, host: "TESTHOST-A", edit: {}, inverse: {}, rt: {},
                         name: "pool", dispatched: false }];
    if (!panel._journaledGids) panel._journaledGids = new Set();
    panel._journaledGids.add(71);
    panel._unionSyncDirty = false;
    let syncs = 0;
    panel._syncUnionOps = () => { syncs += 1; return null; };   // the pump in the SAME
    //  update() consumes the dirty bit — the spy proves the re-send actually fired
    panel.update({ now, sessions: [sess("s1", "web", "#f7768e")], turns: {}, messages: [],
                   judging: [], views: JSON.parse(JSON.stringify(VIEWS)), unionOps: null });
    assert.ok(syncs >= 1,
      "the held mirror is the reconstruction evidence — the paced re-send fired");
  }
});

test("r58 wave 2: a voided queued send treats its gates like the transport reset", () => {
  // reproduced upstream: the silent gate delete let a voided COMPLETION's re-keyed rows
  // ride a later plain mirror sync as an un-CAS'd merge (the r56 P1.2 regression), and a
  // voided PLAIN gate's effects simply never ran — no unwind, no error
  const sy = SRC.indexOf("if (_sendEpoch !== (this._unionEpoch || 0) || _rekeyGone) {");
  assert.ok(sy > 0);
  const win = SRC.slice(sy, sy + 3600);   // widened for the r60 wave-2 refusal-consumed arm
  assert.ok(win.indexOf("for (const g1 of _gates.filter((x) => x.rekey)) {") > 0
    && win.indexOf("this._yieldUnionGids([g1.rekey.gid, g1.rekey.ogid]);") > 0,
    "a voided completion gate UNWINDS both identities (the r56 rule)");
  assert.ok(win.indexOf("const _plain = _gates.filter((x) => !x.rekey);") > 0
    && win.indexOf("this._syncUnionOps(_plain);") > 0,
    "…and voided plain gates re-gate on a CURRENT-world send");
});

test("r59: union frames never queue across a reconnect in the browser shim", () => {
  // reproduced upstream: the webview shim's send() queued setUnionOps while the socket
  // was down and flushed the stale body on reconnect BEFORE unionTransportReset — the
  // kernel accepted the pre-reset rows and forked the gesture
  const kern = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  const i = kern.indexOf("function send(m){");
  assert.ok(i > 0);
  const win = kern.slice(i, i + 600);
  assert.ok(win.indexOf('m.type==="setUnionOps"||m.type==="claimUnionGesture"') > 0
    && win.indexOf("queue.push(s);}") > win.indexOf('m.type==="setUnionOps"'),
    "setUnionOps/claimUnionGesture DROP when the socket is down — everything else queues");
});

test("r60 executed: a queued completion whose gesture a refusal consumed is VOID — no stale rekey rides, no effects run", async () => {
  // reproduced upstream (the v1.3.32 audit's P1.1): _postChain blocked, tagEditFailed
  // delivered, POST released — the send-head rebuild sent entries=[] with the STALE
  // rekey, the kernel CAS accepted it, and the acked gate ran the effects the refusal
  // had just rolled back (effectsAfterAck=1, localRollbacks=1)
  const panel = drawnPanel();
  const posts: any[] = [];
  (panel as any)._kernelPost = (route: string, body: any) => {
    posts.push(body);
    return Promise.resolve({ ok: true, json: { ok: true }, status: 200 });
  };
  const realProcess = (globalThis as any).process;
  (globalThis as any).process = { versions: { electron: "1" } };
  try {
    panel._unionOps = [{ gid: 81, host: "TESTHOST-A", edit: { add: ["s1"] }, inverse: {},
                         rt: { id: "TESTHOST-A:r1", host: "TESTHOST-A", name: "pool" },
                         name: "pool", dispatched: false, post: {} }];
    panel._claimEpochs = { 81: 7 };
    const effects: any[] = [];
    panel._editRemoteTag = (rt: any, edit: any) => { effects.push(edit); return true; };
    panel._completeUnionGesture(81);               // queues the rekey POST behind the chain
    // the refusal lands WHILE the POST sits queued — it consumes the re-keyed group
    panel.tagEditFailed({ host: "TESTHOST-A", name: "pool", opId: "81", error: "refused" });
    await panel._postChain;
    assert.ok(posts.every((b) => !b.rekey),
      "the stale rekey never reached the kernel — the send-head check voided the "
      + "completion when its gesture vanished from the mirror");
    assert.equal(effects.length, 0,
      "the voided gate's effects NEVER ran (the audit measured effectsAfterAck=1)");
    assert.equal(Object.keys(panel._gatedDispatches || {}).length, 0,
      "the voided gate is unwound, not stranded");
  } finally {
    (globalThis as any).process = realProcess;
  }
});

test("r60: the dispatch carries its stable root id — refusal correlation without a journal lookup", () => {
  // the v1.3.32 audit's P2.1: with the named generation aged past the 32-entry retained
  // lineage, the kernel's _union_rid_for answered 0 and the refusal was never
  // compensated — the panel stamps the rid onto the editTag wire instead
  const wires: any[] = [];
  g.__rompTimelineEditTag = (w: any) => { wires.push(w); };
  const panel = drawnPanel();
  panel._unionOps = [{ gid: 136, ogid: 135, olin: [104, 135], rid: 100,
                       host: "TESTHOST-A", edit: {}, inverse: {},
                       rt: { id: "TESTHOST-A:r1", host: "TESTHOST-A", name: "pool" },
                       name: "pool", dispatched: false }];
  panel._editRemoteTag({ id: "TESTHOST-A:r1", host: "TESTHOST-A", name: "pool" },
                       { add: ["s1"] }, 136);
  assert.equal(wires.length, 1);
  assert.equal(wires[0].opId, "136");
  assert.equal(wires[0].rid, "100",
    "the STABLE root rides the dispatch — any-depth lineage still correlates");
  delete g.__rompTimelineEditTag;
});

test("r60 wave 2 executed: a refusal-consumed completion RETIRES the gesture — never re-claims and re-runs it", async () => {
  // the verify round's P1, reproduced end-to-end there: the wave-1 void arm's plain
  // yield deleted the original gid from the ledger, so the retirement the refusal owed
  // was never sent — the rows rode every echo, the kernel refreshed the panel's OWN
  // still-held claim (same ckey), the suppression lifted on that refresh, and the panel
  // re-adopted and RE-COMPLETED the gesture, re-running effects the refusal rolled back
  const panel = drawnPanel();
  const posts: any[] = [];
  (panel as any)._kernelPost = (route: string, body: any) => {
    posts.push(body);
    return Promise.resolve({ ok: true, json: { ok: true }, status: 200 });
  };
  const realProcess = (globalThis as any).process;
  (globalThis as any).process = { versions: { electron: "1" } };
  try {
    panel._unionOps = [{ gid: 81, host: "TESTHOST-A", edit: { add: ["s1"] }, inverse: {},
                         rt: { id: "TESTHOST-A:r1", host: "TESTHOST-A", name: "pool" },
                         name: "pool", dispatched: false, post: {} }];
    if (!panel._journaledGids) panel._journaledGids = new Set();
    panel._journaledGids.add(81);                  // the adopted gesture is ledgered
    panel._claimEpochs = { 81: 7 };
    const effects: any[] = [];
    panel._editRemoteTag = (rt: any, edit: any) => { effects.push(edit); return true; };
    panel._completeUnionGesture(81);
    panel.tagEditFailed({ host: "TESTHOST-A", name: "pool", opId: "81", error: "refused" });
    await panel._postChain;
    // the RETIREMENT rode a later sync instead of being forgotten
    const retiredEver = posts.some((b) => (b.retired || []).indexOf(81) >= 0);
    assert.ok(retiredEver,
      "the refusal-consumed gesture's ORIGINAL gid is retired (wave 1 deleted it from "
      + "the ledger and the live rows stranded in the kernel journal forever)");
    assert.ok(panel._refusalConsumed && panel._refusalConsumed.has(81),
      "…and the gesture is barred from re-completion until the retirement proves out");
    // a granted claim is NO unsuppression here: the kernel refreshes this panel's OWN
    // still-held claim, which proves nothing about a dead completer
    panel._pendingClaims = { 81: true };
    panel.unionClaimAck({ gid: 81, ok: true, epoch: 9 });
    assert.ok(panel._yieldedGids && panel._yieldedGids.has(81),
      "the suppression survives the same-ckey claim refresh");
    panel._completeUnionGesture(81);
    assert.equal(effects.length, 0, "no effect ever re-ran after the refusal's rollback");
    // the retirement PROVES OUT: two consecutive echoes without the gid lift the bar
    for (let i = 0; i < 2; i += 1) {
      panel.update({ now, sessions: [sess("s1", "web", "#f7768e")], turns: {}, messages: [],
                     judging: [], views: JSON.parse(JSON.stringify(VIEWS)), unionOps: [] });
    }
    assert.ok(!(panel._refusalConsumed && panel._refusalConsumed.has(81)),
      "proven absence retires the bar with the suppression");
  } finally {
    (globalThis as any).process = realProcess;
  }
});
