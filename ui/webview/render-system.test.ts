// The pinned "system context" card (the user 2026-06-19): a bordered BOX at the top of the transcript
// showing the CLAUDE.md instructions in effect + the session's model/cwd/branch/permission-mode/version.
// It looks complete even collapsed (⚙ header + one-line summary + caret), and its open/closed state — like
// every other collapsible — survives the re-render a send/turn triggers (persisted in `openFolds`, not the
// DOM). The chat renderer has no jsdom harness, so — like the other webview tests — this pins the wiring at
// the source level.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("there is a 'system' ChatEvent kind carrying the config meta + the CLAUDE.md docs", () => {
  assert.match(RENDER, /kind: "system";[\s\S]*?claudemd\?: \{ path: string; scope: string; text: string \}\[\]/);
});

test("renderEventInner dispatches the system kind to renderSystem", () => {
  assert.match(RENDER, /if \(ev\.kind === "system"\) return renderSystem\(ev\);/);
  assert.match(RENDER, /function renderSystem\(ev: Extract<ChatEvent, \{ kind: "system" \}>\)/);
});

test("the card is a bordered box with a ⚙ header + one-line summary + caret, complete even collapsed", () => {
  assert.match(RENDER, /el\("div", "sys-card"\)/);
  assert.match(RENDER, /el\("div", "sys-card-head"\)/);
  assert.match(RENDER, /el\("div", "sys-card-body"\)/);
  // the collapsed summary is informative: prettified model + the CLAUDE.md count
  assert.match(RENDER, /bits\.push\(prettyModel\(ev\.model\)\)/);
  assert.match(RENDER, /bits\.push\(`\$\{n\} CLAUDE\.md`\)/);
  // the box has a real border/background, and the body shows only when .open
  assert.match(CSS, /\.sys-card \{[^}]*border: 1px solid/);
  assert.match(CSS, /\.sys-card-body \{ display: none;/);
  assert.match(CSS, /\.sys-card\.open \.sys-card-body \{ display: block; \}/);
});

test("each CLAUDE.md doc renders as a raw, scrollable SUB-box with a scope badge + path", () => {
  assert.match(RENDER, /\(ev\.claudemd \|\| \[\]\)\.forEach\(\(doc, i\) =>/);   // indexed — each doc's scroll key needs its position
  assert.match(RENDER, /el\("span", "sys-doc-scope " \+ \(doc\.scope === "global" \? "global" : "project"\)\)/);
  assert.match(RENDER, /sec\.appendChild\(preEl\(doc\.text, key \? key \+ ":doc" \+ i : undefined\)\)/,
    "raw text in a .fold-pre box, not markdown-rendered — scroll keyed per doc (fold-scroll.test.ts)");
});

test("the card never claims to be the verbatim harness prompt — it says the base prompt isn't recorded", () => {
  assert.match(RENDER, /el\("div", "sys-note"\)/);
  assert.match(RENDER, /base harness prompt isn.t recorded in the transcript/);
});

test("the system card sits OFF the conversational rail (no dot/timeline wiring)", () => {
  assert.match(CSS, /\.turn-system::before \{ display: none; \}/);
  assert.match(CSS, /\.turn-system \{[^}]*padding-left/);
});
