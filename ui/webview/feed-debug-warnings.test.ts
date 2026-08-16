// Debug-mode judge warnings (the user 2026-07-09): with `romp debug on`, the kernel joins every
// judge-failure row onto the card whose goal (or placed segment) it touched, and the card modal grows a
// "Warnings (debug)" section — one line per failure, expandable to the failing call's full input + reply.
// Off is the default and costs nothing: the kernel emits no rows, the modal builds no section. Source pins
// (feed.ts + feed.css + bin/romp-kernel), same harness as the other feed pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "bin", "romp-kernel"), "utf8");

test("the card payload carries warnRows and the modal renders them below the tree", () => {
  assert.match(FEED, /warnRows\?: \{ t: number; judge: string; err: string; note\?: string; debug\?: \{ input\?: string; reply\?: string \} \}\[\] \| null;/);
  assert.match(FEED, /function applyModalWarnings\(host: HTMLElement, it: AskItem\): void/);
  assert.match(FEED, /applyModalWarnings\(body, it\);/, "wired in the single-ask modal branch");
});

test("rows render newest first, with the capture behind a native details fold", () => {
  assert.match(FEED, /rows\.slice\(\)\.reverse\(\)/, "the live story on top");
  assert.match(FEED, /\$\{clockHM\(r\.t\)\} · \$\{r\.judge\} · \$\{r\.err\}\$\{r\.note \? " — " \+ r\.note : ""\}/);
  assert.match(FEED, /r\.debug && \(r\.debug\.input \|\| r\.debug\.reply\)/,
    "only rows captured in debug mode grow the input+reply fold");
  assert.match(FEED, /\(sec as any\)\._sig === sig/, "an unchanged section keeps its DOM, so an open fold survives the push");
});

test("the kernel emits warnRows only in debug mode, joined per card", () => {
  assert.match(KERNEL, /dbg_rows = _judge_error_rows\(now\) if jd\._debug_mode\(\) else None/);
  assert.match(KERNEL, /"warnRows": \(_card_warn_rows\(dbg_rows, fsid, set\(_subtree\(nid\)\),/);
  assert.match(KERNEL, /def _card_warn_rows\(rows, fsid, subtree, placements, cap=20\):/);
  assert.match(KERNEL, /for suf in \("", "#p", "#live", "#d"\):/,
    "a filing-judge failure resolves to its card through placements, any phase");
});

test("the section has its own styles at the shared modal sizes", () => {
  assert.match(CSS, /\.fmodal-warns \{ margin-top: 12px/);
  assert.match(CSS, /\.fmodal-warns-head \{ font-size: 0\.72em/);
  assert.match(CSS, /\.fmodal-warn-pre \{ font-size: 0\.82em/);
});
