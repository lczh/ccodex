// The Continue button's reply renders as a GESTURE in the user family (the user 2026-08-13): blue —
// the judges file it as your reply — but folded to a one-line gist ("Continue — keep going; open calls
// are yours") with the exact canned words one click deeper, so unwritten prose never poses as typed.
// Keyed on the kernel's romp-canned marker (event-based), never on text-matching the copy. Source pins
// (no jsdom for the chat renderer), plus the kernel side of the contract (node-tests-pin-kernel-source).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the canned Continue folds to a gist in the BLUE bubble — gesture, not prose", () => {
  assert.match(RENDER, /canned\?: string/, "the user event carries the kernel's lift");
  assert.match(RENDER, /\} else if \(!romp && !injected && ev\.md && ev\.canned === "continue"\) \{/,
    "keyed on the lifted marker, never on text-matching the canned copy");
  assert.match(RENDER, /Continue — keep going; open calls are yours/);
  // the same fold machinery nudges use: keyed expand, the stable body delegate, never a per-render listener
  assert.match(RENDER, /const ckey = ev\.uuid \? "cont:" \+ ev\.uuid : undefined;/);
  assert.match(RENDER, /bubble\.classList\.add\("nudge-collapsible"\);\s*\n\s*bubble\.dataset\.act = "nudgetoggle";\s*\n?\s*\/\/ the stable body delegate/);
});

test("the user-family fold is white-on-blue, not romp's dim gray", () => {
  assert.match(CSS, /\.user-bubble\.nudge-collapsible \{ cursor: pointer; \}/);
  assert.match(CSS, /\.user-bubble \.nudge-gist, \.user-bubble \.nudge-caret \{ color: rgba\(255, 255, 255, 0\.92\); \}/);
  assert.match(CSS, /\.user-bubble \.nudge-full \{ display: none; \}/);
  assert.match(CSS, /\.user-bubble\.expanded \.nudge-full \{ display: block; \}/);
  assert.match(CSS, /\.user-bubble\.expanded \.nudge-gist \{ display: none; \}/);
});

test("kernel: the marker is stamped at the ONE cont send and lifted for human turns only", () => {
  assert.match(KERNEL, /\(CONTINUE_TEXT \+ "\\n\\n<!-- romp-canned: continue -->"\) if msg\.get\("cont"\) else str\(msg\["text"\]\)/);
  assert.match(KERNEL, /if author == "human" and "<!-- romp-canned: continue -->" in text:/);
  assert.match(KERNEL, /ev\["canned"\] = "continue"/);
});
