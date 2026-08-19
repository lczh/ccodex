// Dead-lane "Clear" pill (the user 2026-07-02): a DEAD session lingers in the timeline as a faded/struck lane
// while it's still in the activity window, with NONE of the live controls (no feed/postal toggle, no model
// picker, no chip, no ctx battery). A small Clear pill — the feed cards' clear-button chrome — sits just right
// of the struck name to dismiss the leftover lane; the kernel forgets the dismissal on restart so it can come
// back. No DOM harness for the SVG draw path, so pin the wiring at the source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");

test("only DEAD lanes draw the Clear pill (gated on !s.live), placed at the empty controls column", () => {
  assert.match(SRC, /if \(!s\.live\) \{[\s\S]*?ctx\.textContent = 'Clear';/);
  // positioned at eyeColX — the controls column, empty for a dead lane, just right of the name
  assert.match(SRC, /const cw = Math\.ceil\(this\.ctxWidth\('Clear'\)\), bw = cw \+ CL_PAD \* 2, bx = eyeColX/);
});

test("the pill mirrors the feed clear button: outlined+dim, red fill on hover", () => {
  assert.match(SRC, /const CL_H = 15, CL_PAD = 7, CL_RED = '#c74e39';/);
  // resting = MODEL_FG outline; hover = red fill + white text
  assert.match(SRC, /box\.setAttribute\('fill', CL_RED\); box\.setAttribute\('stroke', CL_RED\)[\s\S]*?ctx\.setAttribute\('fill', '#ffffff'\)/);
});

test("clicking the pill dismisses on pointerdown (poll-redraw-safe) + optimistically drops the lane", () => {
  // pointerdown, not click: a poll rebuild between mousedown/up would eat a native click (as the toggles do)
  assert.match(SRC, /chit\.addEventListener\('pointerdown', \(e\) => \{[\s\S]*?this\._dismissLane\(s\.id\); this\.draw\(\);/);
  // optimistic: drop it from the current frame so it vanishes at once (the kernel push is authoritative)
  assert.match(SRC, /this\.data\.sessions = this\.data\.sessions\.filter\(\(x\) => x\.id !== s\.id\)/);
});

test("_dismissLane posts via the web-shell hook only (no persistence path)", () => {
  assert.match(SRC, /_dismissLane\(id\) \{[\s\S]*?window\.__rompTimelineDismiss === 'function'[\s\S]*?window\.__rompTimelineDismiss\(id\)/);
});

// A cleared lane POPPED BACK and had to be clicked several times (the user 2026-07-22). The optimistic
// removal only edited the current frame, and update()'s wholesale `this.data = data` restored it: any
// payload still carrying the lane wins — the kernel's own in-flight push, or (far more often, with a
// flapping remote) the federation manager re-emitting a MERGED timeline from its cached per-host
// snapshots, which still hold the pre-dismissal local one. Hence "it came back, then went away on its
// own a second later" when the kernel's real push finally landed. The dismissal is now held STICKY, the
// same contract _pendingFlags uses for the eye toggle. These EXECUTE the reconcile.
const { TimelinePanel } = createRequire(__filename)(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"));
const panel = (sessions: any[], dismissed: string[] = []) => {
  const p: any = Object.create(TimelinePanel.prototype);
  p._dismissed = new Set(dismissed);
  p.data = { sessions };
  return p;
};

test("a cleared lane stays gone when a stale push still carries it", () => {
  const p = panel([{ id: "a", live: false }, { id: "b", live: true }], ["a"]);
  p._reconcileDismissed();
  assert.deepEqual(p.data.sessions.map((s: any) => s.id), ["b"], "the cleared dead lane must not pop back");
  assert.ok(p._dismissed.has("a"), "still held — the kernel has not confirmed yet");
});

test("the hold is RELEASED once the kernel's payload drops the lane", () => {
  const p = panel([{ id: "b", live: true }], ["a"]);
  p._reconcileDismissed();
  assert.equal(p._dismissed.size, 0, "kernel caught up → stop holding, so a later revive can show it again");
});

test("a REVIVED sid is released and shown (the kernel un-dismisses on revive)", () => {
  const p = panel([{ id: "a", live: true }], ["a"]);
  p._reconcileDismissed();
  assert.deepEqual(p.data.sessions.map((s: any) => s.id), ["a"], "a revived session must reappear");
  assert.equal(p._dismissed.size, 0);
});

test("reconcile is a no-op with nothing cleared", () => {
  const p = panel([{ id: "a", live: false }]);
  p._reconcileDismissed();
  assert.deepEqual(p.data.sessions.map((s: any) => s.id), ["a"]);
});

test("the click holds the sid, and update() re-applies it on every push", () => {
  assert.match(SRC, /this\._dismissed = new Set\(\);/);              // constructor
  assert.match(SRC, /this\._dismissed\.add\(s\.id\);/);              // click site
  assert.match(SRC, /this\._reconcileDismissed\(\);/);               // called from update()
  // ...right after the wholesale replace that used to undo the optimistic removal
  assert.match(SRC, /this\.data = data;[\s\S]{0,700}?this\._reconcileDismissed\(\);/);
});
