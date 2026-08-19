// The feed's Clear-all / Undo-clear controls sit in a small bordered sub-pane in the BOTTOM-RIGHT of
// the feed, not a full-width footer bar, and the button label is two words "Undo clear" (the user
// 2026-06-16). The chat renderer has no jsdom harness, so — like the ledger tests — pin at the source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("the Undo-clear button label is two words", () => {
  assert.match(FEED, /b\.textContent = "Undo clear"/);
  assert.doesNotMatch(FEED, /"UndoClear"/);
});

test("#feed-foot DOCKS as a dedicated in-flow bottom bar (the user 2026-06-29), not a floating overlay", () => {
  // the user 2026-06-29: the control panel is its own dedicated rectangle at the bottom in normal flow, NOT a
  // floating thing on top. The body is a column flex so #feed-list flexes above the bar without overlap.
  assert.match(CSS, /#feed-foot \{[^}]*flex: 0 0 auto/);          // an in-flow flex item, not floated
  assert.match(CSS, /#feed-foot \{[^}]*border-top: 1px solid var\(--card-border\)/);  // a docked bar with a top edge
  assert.doesNotMatch(CSS, /#feed-foot \{[^}]*position: absolute/);   // no longer floats
  assert.doesNotMatch(CSS, /#feed-foot \{[^}]*width: fit-content/);   // spans the bar, doesn't hug the buttons
  // (the footer Sub-goals checkbox was removed 2026-07-08 — sub-goals is a per-card button now)
  assert.doesNotMatch(CSS, /#feed-subgoals/);
});

test("the docked footer leaves the list a proper scroll container with no float clearance", () => {
  assert.match(CSS, /body \{ display: flex; flex-direction: column; position: relative; \}/);   // column flex
  assert.match(CSS, /#feed-list \{[^}]*min-height: 0;/);          // proper scroll container
  assert.match(CSS, /#feed-list \{[^}]*padding: 12px;/);          // no reserved band for a float anymore
});

test("when the feed stacks (narrow), the columns read Completed → Blocked → Working (the user 2026-07-30)", () => {
  // the side-by-side DOM order stays Working/Blocked/Completed; a container query stacks them and `order`
  // re-sequences ONLY the stacked case: done first (moved up from the middle, superseding the 2026-07-08
  // Blocked-first order), then needs-you, then still-running.
  assert.match(CSS, /@container \(max-width: 540px\) or style\(--romp-stack: on\) \{[\s\S]*?\.feed-cols \{ flex-direction: column; \}/);
  assert.match(CSS, /\.feed-col\.col-completed\s+\{ order: var\(--stack-order, 1\); \}/);   // the DEFAULT — a
  // dragged section overrides via --stack-order (feed-col-fold.test.ts owns that rule)
  assert.match(CSS, /\.feed-col\.col-needsInput \{ order: var\(--stack-order, 2\); \}/);
  assert.match(CSS, /\.feed-col\.col-asks\s+\{ order: var\(--stack-order, 3\); \}/);
});
