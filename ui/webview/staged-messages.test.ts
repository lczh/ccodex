// The staged stack (the user 2026-08-15): every rule executed.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
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

test("a staged line stays inside the pane: shrinkable strips, ellipsis, click-to-expand tail", () => {
  // the user 2026-08-16 (screenshot): one long staged message refused to shrink — the composer's
  // strips are wrapped flex ITEMS, and without flex-basis 100% + min-width 0 their min-content
  // width is the nowrap label's full intrinsic width, so the line blew past the pane and dragged a
  // horizontal scroll with it. The pane must NEVER scroll sideways.
  const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
  const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
  assert.match(CSS, /#composer-files, #composer-staged, #composer-chips \{ flex: 1 1 100%; min-width: 0; max-width: 100%; \}/);
  assert.match(CSS, /body \{ display: flex; flex-direction: column; overflow-x: hidden; \}/,
    "the hard guarantee: a wide child is a layout bug, never a sideways scroll");
  assert.match(CSS, /\.composer-chip-label \{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap;/,
    "the one-line ellipsis that the shrinkable strip finally lets engage");
  assert.match(RENDER, /hint\.textContent = open \? "\(collapse\)" : "\(click to expand\)";/,
    "the tail names the gesture");
});
