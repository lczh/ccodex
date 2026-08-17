// ↑ in the chat box recalls the session's previously SENT prompts, shell-style (the user 2026-08-16).
// The gate is deliberately narrow: only from the boundary lines (↑ needs the caret on the FIRST line,
// ↓ the last), only with a collapsed caret and no modifiers, and only after the slash menu has passed
// on the key — so multi-line editing, selection, and menu navigation are never hijacked. History is
// the session payload's own human-sent messages (romp's injected turns excluded, adjacent repeats
// collapsed), the live draft stashes on the first ↑ and restores when walking past the newest, and
// any send drops the walk. Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("history is the session's own sent prompts: human-only, romp-free, deduped", () => {
  assert.match(RENDER, /if \(ev\.kind !== "user" \|\| !ev\.human \|\| ev\.romp \|\| ev\.rompAuto\) continue;/);
  assert.match(RENDER, /if \(t && out\[out\.length - 1\] !== t\) out\.push\(t\);/, "adjacent repeats collapse");
});

test("the gate never hijacks editing: boundary line, collapsed caret, no modifiers, slash menu first", () => {
  const at = RENDER.indexOf("const composerHistory");
  assert.ok(RENDER.indexOf("if (slashKey(e)) return;") > at, "the slash menu keeps first claim on the arrows");
  assert.match(RENDER, /&& !e\.metaKey && !e\.ctrlKey && !e\.altKey && !e\.shiftKey\s*\n\s*&& ta\.selectionStart === ta\.selectionEnd/);
  assert.match(RENDER, /const onFirst = !ta\.value\.slice\(0, ta\.selectionStart\)\.includes\("\\n"\);/);
  assert.match(RENDER, /e\.key === "ArrowUp" \? onFirst : \(onLast && w\)/,
    "↓ only walks forward inside an active walk — plain ↓ in a draft stays native");
});

test("the draft stashes on the first ↑ and restores past the newest; a send drops the walk", () => {
  assert.match(RENDER, /histWalk\.set\(activeId, \{ idx, stash: w \? w\.stash : ta\.value \}\);/);
  assert.match(RENDER, /if \(idx >= hist\.length\) \{ ta\.value = w!\.stash; histWalk\.delete\(activeId\); \}/);
  const drops = RENDER.match(/histWalk\.delete\(sid\);\s+\/\/ …and the history walk starts fresh/g) || [];
  assert.equal(drops.length, 2, "both delivery paths reset the walk");
  assert.match(RENDER, /ta\.dispatchEvent\(new Event\("input"\)\);/,
    "a recall runs the input bookkeeping (draft persist, slash menu, ask-mode) like typing would");
});
