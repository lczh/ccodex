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

test("the gate never hijacks a draft: EMPTY box only, no modifiers, slash menu first", () => {
  // the user 2026-08-17, tightening the first cut: any text in the box means a draft in progress —
  // ↑ starts a walk only from an empty prompt. Inside an active walk the recalled text is the
  // walk's own, so the boundary-line rules still navigate it.
  const at = RENDER.indexOf("const composerHistory");
  assert.ok(RENDER.indexOf("if (slashKey(e)) return;") > at, "the slash menu keeps first claim on the arrows");
  assert.match(RENDER, /&& !e\.metaKey && !e\.ctrlKey && !e\.altKey && !e\.shiftKey\s*\n\s*&& ta\.selectionStart === ta\.selectionEnd/);
  assert.match(RENDER, /e\.key === "ArrowUp" \? \(w \? onFirst : ta\.value === ""\) : \(onLast && w\)/,
    "↑ starts only from empty; ↓ only walks forward inside an active walk");
  // a manual edit ends the walk — the recalled text becomes an ordinary draft recall won't touch;
  // the recall's own synthetic input dispatch is fenced so it never ends its own walk
  assert.match(RENDER, /if \(!recalling && activeId\) histWalk\.delete\(activeId\);/);
  assert.match(RENDER, /let recalling = false;/);
});

test("the draft stashes on the first ↑ and restores past the newest; a send drops the walk", () => {
  assert.match(RENDER, /histWalk\.set\(activeId, \{ idx, stash: w \? w\.stash : ta\.value \}\);/);
  assert.match(RENDER, /if \(idx >= hist\.length\) \{ ta\.value = w!\.stash; histWalk\.delete\(activeId\); \}/);
  const drops = RENDER.match(/histWalk\.delete\(sid\);\s+\/\/ …and the history walk starts fresh/g) || [];
  assert.equal(drops.length, 2, "both delivery paths reset the walk");
  assert.match(RENDER, /ta\.dispatchEvent\(new Event\("input"\)\);/,
    "a recall runs the input bookkeeping (draft persist, slash menu, ask-mode) like typing would");
});

test("the resting placeholder advertises the recall, in every copy of the hint row", () => {
  // the user 2026-08-17: now that ↑ recalls history, the empty-composer placeholder must say so.
  // The placeholder shows exactly when the box is empty — exactly when the gate lets ↑ fire. The
  // hint row lives in THREE places (render.ts's canonical function, the extension's static
  // skeleton, the kernel-served skeleton); all three must carry the identical string or the hint
  // a user sees depends on which surface painted first.
  const ROW = "Message this session…  (⏎ send · ⇧⏎ newline · ⌘⏎ stage · ↑ history · / for commands)";
  const SKEL = fs.readFileSync(path.resolve(process.cwd(), "..", "vscode-extension", "src", "page-skeleton.ts"), "utf8");
  const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  assert.ok(RENDER.includes(JSON.stringify(ROW)), "canonical composerRestingPlaceholder carries the ↑ hint");
  assert.ok(SKEL.includes(ROW), "extension skeleton matches");
  assert.ok(KERNEL.includes(ROW), "kernel skeleton matches");
});
