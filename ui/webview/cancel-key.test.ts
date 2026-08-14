import { test } from "node:test";
import assert from "node:assert/strict";
import { queuedCancelKey } from "./cancel-key";

test("queuedCancelKey is stable across the click and cancel-result halves", () => {
  const pending = new Map<string, string>();
  pending.set(queuedCancelKey("session-1", "send this later"), "draft-before");
  assert.equal(pending.get(queuedCancelKey("session-1", "send this later")), "draft-before");
});

test("queuedCancelKey cannot collide at spaces or embedded separators", () => {
  assert.notEqual(queuedCancelKey("a b", "c"), queuedCancelKey("a", "b c"));
  assert.notEqual(queuedCancelKey("a\0b", "c"), queuedCancelKey("a", "b\0c"));
});
