// intentOp gates what survives a KernelPipe reconnect: user intent (typed text,
// explicit picks) delivers after the socket returns; view chatter drops, because
// the reconnect reloads the webview and resyncs it fresh (the user 2026-07-21).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { INTENT_OPS, intentOp } from "./pipe-intent";

test("typed-text ops are intent — losing them loses the user's words", () => {
  for (const t of ["sendMessage", "askFollowUp", "askText", "addCustomAsk", "sendCommand", "rewindSend"]) {
    assert.ok(intentOp(t), `${t} must survive a reconnect`);
  }
});

test("explicit state-changing picks are intent", () => {
  for (const t of ["setModel", "setEffort", "setMode", "setFast", "interrupt", "endSession",
    "nodeOverride", "askClear", "answerAsk", "submitAsk", "renameSession"]) {
    assert.ok(intentOp(t), `${t} must survive a reconnect`);
  }
});

test("view chatter is not intent — the reconnect reload resyncs it", () => {
  for (const t of ["ready", "openSession", "showAskPath", "showOnTimeline", "dotHover",
    "hoverHighlight", "loadOlder", "requestSessions", "openByName", "dotOpen", "imgRequest"]) {
    assert.ok(!intentOp(t), `${t} is view state, not user intent`);
  }
});

test("non-strings never classify as intent", () => {
  assert.ok(!intentOp(undefined));
  assert.ok(!intentOp(null));
  assert.ok(!intentOp(42));
  assert.ok(!INTENT_OPS.has(""));
});
test("tag-edit legs survive a reconnect; the union JOURNAL body never replays (r59 P1.1)", () => {
  // the v1.3.24 audit's P1.3: queued [editTag, setTimelineViewsOps] must both land. But
  // setUnionOps is a WORLD-SCOPED journal mirror, not user intent — the r59 audit
  // reproduced a retained body replaying after reconnect with the PRE-reset rows and
  // forking one gesture into two claimable identities (both completion CASes succeeded).
  // The panel's unionTransportReset re-sends CURRENT state; the retained replay is the bug.
  for (const op of ["editTag", "setTimelineViewsOps"]) {
    assert.ok(INTENT_OPS.has(op), op + " must survive a reconnect");
  }
  assert.ok(!INTENT_OPS.has("setUnionOps"),
    "the journal mirror is rebuilt from the live panel, never replayed from a dead world");
  assert.ok(!INTENT_OPS.has("claimUnionGesture"),
    "claims are socket-scoped by design (r54) — a replayed claim is always stale");
});

