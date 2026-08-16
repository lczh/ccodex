// The staged stack (the user 2026-08-15): every rule executed.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { StagedStack } from "./staged-messages";

test("stage order is release order, and a flush is one-shot", () => {
  const s = new StagedStack();
  s.push("a", { text: "first", cites: [] });
  s.push("a", { text: "second", cites: [{ quote: "ctx" }] });
  s.push("a", { text: "third", cites: [] });
  assert.equal(s.count("a"), 3);
  assert.deepEqual(s.takeAll("a").map((m) => m.text), ["first", "second", "third"]);
  assert.equal(s.count("a"), 0, "released, not re-sendable");
  assert.deepEqual(s.takeAll("a"), []);
});

test("tabs hold separate stacks", () => {
  const s = new StagedStack();
  s.push("a", { text: "for a", cites: [] });
  s.push("b", { text: "for b", cites: [] });
  assert.deepEqual(s.takeAll("a").map((m) => m.text), ["for a"]);
  assert.equal(s.count("b"), 1, "flushing one tab never touches another");
});

test("discard removes exactly the one chip", () => {
  const s = new StagedStack();
  s.push("a", { text: "keep", cites: [] });
  s.push("a", { text: "drop", cites: [] });
  s.push("a", { text: "keep too", cites: [] });
  s.removeAt("a", 1);
  assert.deepEqual(s.list("a").map((m) => m.text), ["keep", "keep too"]);
});

test("the persistence round-trip keeps text, context and order — and drops junk", () => {
  const s = new StagedStack();
  s.push("a", { text: "one", cites: [{ quote: "q", title: "t" }] });
  s.push("a", { text: "two", cites: [] });
  const r = new StagedStack();
  r.restore(JSON.parse(JSON.stringify(s.entries())));
  assert.deepEqual(r.list("a").map((m) => m.text), ["one", "two"]);
  assert.equal((r.list("a")[0].cites[0] as any).quote, "q", "the context survives the reload");
  r.restore({ b: [{ text: "" }, { nope: 1 }, "junk"], c: "junk" });   // a hand-edited/old store
  assert.equal(r.count("b"), 0, "junk hydrates to nothing, never a crash");
  assert.equal(r.count("c"), 0);
});
