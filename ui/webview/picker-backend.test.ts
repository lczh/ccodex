// A per-session backend picker on the + dialog (the user 2026-06-23): a tmux | SDK segmented toggle,
// defaulting to the gear's Default backend but overridable for THIS new session. Source-pin over render.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the + dialog builds a SDK | tmux | Codex backend toggle, hidden in pick-mode", () => {
  assert.match(RENDER, /const beWrap = el\("div", "picker-backend"\)/);
  assert.match(RENDER, /mkBe\("tmux", "tmux"/);
  assert.match(RENDER, /mkBe\("sdk", "SDK"/);
  assert.match(RENDER, /mkBe\("codex", "Codex"/);
  assert.match(RENDER, /box\.appendChild\(beWrap\)/);
  // hidden + reset to the gear default each open (overridable for this session)
  assert.match(RENDER, /beWrapEl\.style\.display = pick \? "none" : ""/);
  assert.match(RENDER, /\.classList\.toggle\("sel", \(x as HTMLElement\)\.dataset\.be === def\)/);
});

test("createSession uses the picked backend, falling back to the gear default", () => {
  assert.match(RENDER, /const beSel = beWrap\.querySelector\("\.picker-be-opt\.sel"\)/);
  assert.match(RENDER, /backend: beSel\?\.dataset\.be \|\| loadSettings\(\)\.backend/);
});

test("the toggle's selected option is the romp ACCENT (the user 2026-06-24), not the working-yellow", () => {
  assert.match(CSS, /\.picker-be-opt\.sel \{[^}]*background: var\(--accent\)/);
  assert.doesNotMatch(CSS, /\.picker-be-opt\.sel \{[^}]*background: var\(--st-working-bg\)/);
});
