// The card's distiller line (decision brief / takeaway) goes through distillInputs(distillState, column),
// which withholds the line entirely while the card sits in the Working column (the user 2026-07-22) and
// otherwise resolves it from the kernel's genuine state. The RULE is executed in distiller-line.test.ts;
// this pins that feed.ts and the kernel actually WIRE that rule in — no jsdom for the feed renderer, so
// pin at the source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

test("the card routes the distiller line through distillInputs(distillState, column), not column directly", () => {
  assert.match(FEED, /import \{ distillText, distillInputs, applyDistillLine, distillPending, distillStaleNote \}/);
  // the card computes (completed, blocked) from the genuine state via the shared helper
  assert.match(FEED, /const \{ completed: dCompleted, blocked: dBlocked \} = distillInputs\(it\.distillState, it\.column\);/);
  // both the shown line AND the pending swirl read those, so the two can never disagree about the state
  assert.match(FEED, /applyDistillLine\(a\._distill as HTMLElement, dCompleted, dBlocked,/);
  assert.match(FEED, /distillPending\(dCompleted, dBlocked,/);
  // it no longer keys the line on the transient column (the old `it.column === "needs_input"` at the call)
  assert.doesNotMatch(FEED, /applyDistillLine\([^)]*it\.column === "needs_input"/);
});

test("the recheck/rejudging swirl is never gated on the brief", () => {
  // Gating the swirl on `!briefText` (the user 2026-07-21) left a re-judged card showing its brief and
  // nothing else, reading as a working card that inexplicably has a summary. Since 2026-07-22 the brief is
  // withheld for that whole window, which makes this the only cue the card has left: gating it on anything
  // to do with the brief would now leave the card entirely silent.
  assert.doesNotMatch(FEED, /it\.recheck && !briefText/);
  assert.doesNotMatch(FEED, /it\.rejudging && !briefText/);
  // the swirl's rule is EXECUTED in spin-caption.test.ts; the line's in distiller-line.test.ts
  assert.match(FEED, /const spin = spinFor\(it, distillPending\(/);
});

test("AskItem carries distillState from the kernel", () => {
  assert.match(FEED, /distillState\?: "completed" \| "blocked" \| null;/);
});
