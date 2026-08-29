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
test("the tag-transaction halves are ALL intent — a reconnect must not land one leg (r52)", () => {
  // the v1.3.24 audit's P1.3, reproduced there: queued [editTag, editTag,
  // setTimelineViewsOps, setUnionOps] replayed as [setTimelineViewsOps] — the local edit
  // landed while the remote edits and the compensation journal disappeared
  for (const op of ["editTag", "setUnionOps", "setTimelineViewsOps"]) {
    assert.ok(INTENT_OPS.has(op), op + " must survive a reconnect");
  }
});

