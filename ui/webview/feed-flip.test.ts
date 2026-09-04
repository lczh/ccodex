import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { flipNeeded } from "./feed-flip";

const M = (o: Record<string, string>) => new Map(Object.entries(o));

test("no FLIP when every card kept its column (the everyday in-place update)", () => {
  assert.equal(flipNeeded(M({ "a:1": "asks", "a:2": "completed" }), M({ "a:1": "asks", "a:2": "completed" })), false);
});
test("a card that changed column needs the pass", () => {
  assert.equal(flipNeeded(M({ "a:1": "asks", "a:2": "asks" }), M({ "a:1": "asks", "a:2": "needsInput" })), true);
});
test("a card that appeared or left needs the pass (its neighbours shift)", () => {
  assert.equal(flipNeeded(M({ "a:1": "asks" }), M({ "a:1": "asks", "a:2": "asks" })), true);
  assert.equal(flipNeeded(M({ "a:1": "asks", "a:2": "asks" }), M({ "a:1": "asks" })), true);
  assert.equal(flipNeeded(M({ "a:1": "asks" }), M({ "a:9": "asks" })), true, "same count, different card");
});
test("the first paint never flies", () => {
  assert.equal(flipNeeded(new Map(), M({ "a:1": "asks" })), false);
});

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
test("render() gates both forced layouts on the flip decision, and remembers the columns it painted", () => {
  assert.match(SRC, /const nextCols = columnsOf\(buckets\);\n\s*const needFlip = flipNeeded\(prevCols, nextCols\);\n\s*prevCols = nextCols;\n\s*const flipFirst = needFlip \? captureCardRects\(cols\) : new Map<string, FlipState>\(\);/);
  assert.match(SRC, /if \(needFlip\) flyColumnChanges\(flipFirst, cols\);/);
  // columnsOf mints the SAME keys reconcileCol does, so a header keyed per column counts as moved when it changes column
  assert.match(SRC, /const key = e\.kind === "ask" \? "a:" \+ e\.ask\.itemId : e\.kind === "group" \? "g:" \+ e\.group\.turnId : "s:" \+ col \+ ":" \+ e\.sid;/);
  assert.match(SRC, /key = "s:" \+ listEl\.id \+ ":" \+ e\.sid;/);
});
test("the fly reads every rect before it writes any transform", () => {
  const body = /function flyColumnChanges\([\s\S]*?\n\}/.exec(SRC)![0];
  const firstWrite = body.indexOf("c.style.transform = ");
  const lastRead = body.lastIndexOf("getBoundingClientRect()");
  assert.ok(firstWrite > 0 && lastRead > 0 && lastRead < firstWrite, "all reads precede the first write");
  assert.match(body, /const moves: \{ c: HTMLElement; dx: number; dy: number; crossed: boolean \}\[\] = \[\];/);
});

test("a card whose data and display state did not change is not repainted", () => {
  assert.match(SRC, /function cardPaintKey\(it: AskItem\): string \{\n\s*return JSON\.stringify\(it\) \+ "\|"/);
  assert.match(SRC, /const pk = cardPaintKey\(it\);\n\s*if \(a\._paintKey === pk\) return;\s*\/\/ nothing this card shows has changed/);
  // the display-side inputs the paint reads are part of the key (a hover, a pin, a pending bell, a done tick)
  assert.match(SRC, /hoverAskId \?\? pinnedAskId/);
  assert.match(SRC, /pendingNotify\.has\(it\.itemId\)/);
  assert.match(SRC, /\[\.\.\.pendingDone\]\.join\(","\)/, "an optimistic done tick anywhere repaints every card: the set is usually empty");
});
