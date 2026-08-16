// The Continue button's reply renders as a GESTURE in the slash-command family (the user 2026-08-15;
// supersedes the 2026-08-13 blue-bubble fold, whose PARAPHRASED gist made it the one "user message"
// that expanded — to different words than its label): ✦ mark, mono chip "Continue", then the SENT
// text's own first line with the rest one click deeper — expanding only ever reveals MORE of the same
// words, never different ones. The judges still file it as the user's reply. Keyed on the kernel's
// romp-canned marker (event-based), never on text-matching the copy. Source pins (no jsdom for the
// chat renderer), plus the kernel side of the contract (node-tests-pin-kernel-source).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the canned Continue is a GESTURE row: ✦ + chip + the sent text's own first line", () => {
  assert.match(RENDER, /canned\?: string/, "the user event carries the kernel's lift");
  assert.match(RENDER, /\} else if \(!romp && !injected && !tagged && ev\.md && ev\.canned === "continue"\) \{/,
    "keyed on the lifted marker, never on text-matching the canned copy");
  // the slash-command dress — same family, same classes
  assert.match(RENDER, /turn\.classList\.add\("turn-cmd"\);\s*\n\s*bubble\.classList\.add\("cmd-row"\);\s*\n\s*const chip = el\("span", "slash-cmd-chip"\); chip\.textContent = "Continue";/);
  // the collapsed line is the SENT text itself (first non-quote line, clipped) — never a paraphrase,
  // so expanding reveals more of the same words rather than different ones
  assert.match(RENDER, /const first = lines\.find\(\(l\) => l && !l\.startsWith\(">"\)\) \|\| lines\.find\(\(l\) => l\) \|\| raw;/);
  assert.match(RENDER, /const clipped = first\.length > 90 \? first\.slice\(0, 88\)\.replace\(\/\\s\+\\S\*\$\/, ""\) \+ "…" : first;/);
  assert.doesNotMatch(RENDER, /Continue — keep going; open calls are yours/, "the paraphrased label is gone");
  // the same fold machinery nudges use: keyed expand, the stable body delegate, never a per-render listener
  assert.match(RENDER, /const ckey = ev\.uuid \? "cont:" \+ ev\.uuid : undefined;/);
  assert.match(RENDER, /bubble\.classList\.add\("nudge-collapsible"\);\s*\n\s*bubble\.dataset\.act = "nudgetoggle";\s*\n?\s*\/\/ the stable body delegate/);
});

test("the gesture fold is dim like the command family, and unfolds in place", () => {
  assert.match(CSS, /\.user-bubble\.nudge-collapsible \{ cursor: pointer; \}/);
  assert.match(CSS, /\.user-bubble\.cmd-row \.nudge-gist, \.user-bubble\.cmd-row \.nudge-caret \{ color: var\(--dim\); \}/);
  assert.match(CSS, /\.user-bubble\.cmd-row \.nudge-full \{ color: var\(--dim\); margin-top: 4px; max-width: 640px; \}/);
  assert.match(CSS, /\.user-bubble \.nudge-full \{ display: none; \}/);
  assert.match(CSS, /\.user-bubble\.expanded \.nudge-full \{ display: block; \}/);
  assert.match(CSS, /\.user-bubble\.expanded \.nudge-gist \{ display: none; \}/);
});

test("kernel: the marker is stamped at the ONE cont send and lifted for human turns only", () => {
  assert.match(KERNEL, /\(CONTINUE_TEXT \+ "\\n\\n<!-- romp-canned: continue -->"\) if msg\.get\("cont"\) else str\(msg\["text"\]\)/);
  assert.match(KERNEL, /if author == "human" and "<!-- romp-canned: continue -->" in text:/);
  assert.match(KERNEL, /ev\["canned"\] = "continue"/);
});
