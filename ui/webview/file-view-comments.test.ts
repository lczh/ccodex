// Review comments in the file viewer (the user 2026-08-14): comment on passages while you read, then
// ONE Submit hands the whole set to the session as a single message drafted into the composer.
//
// This layer rides ON the viewer main already had (file-view.ts) rather than shipping a second reader.
// No jsdom for these renderers (the repo convention), so the contract is pinned at source — with two
// tests that exist because source pins alone once let a completely unusable feature through: the menu
// has to be REACHABLE (bound on the viewer body) and VISIBLE (above the viewer's own backdrop).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const here = (...p: string[]) => path.resolve(process.cwd(), "..", ...p);
const VIEW = fs.readFileSync(here("ui", "webview", "file-view.ts"), "utf8");
const RENDER = fs.readFileSync(here("ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(here("ui", "webview", "styles.css"), "utf8");

test("the layer rides on main's viewer — no second reader, no second route", () => {
  assert.ok(!fs.existsSync(here("ui", "webview", "docreview-reader.ts")), "no rival reader module");
  assert.doesNotMatch(RENDER, /openDocReview/, "render.ts must not carry its own doc reader");
  const KERNEL = fs.readFileSync(here("kernel", "kernel.py"), "utf8");
  assert.doesNotMatch(KERNEL, /_doc_source|_DOC_EXT/, "/file already serves text — no rival /doc route");
});

test("comments are keyed per session AND per file, and persist across a reload", () => {
  assert.match(VIEW, /const key = docKey\(sid \|\| "", path\);/);
  assert.match(VIEW, /const CMT_KEY = "romp:fileviewComments";/);
  assert.match(VIEW, /localStorage\.setItem\(CMT_KEY, JSON\.stringify\(Object\.fromEntries\(comments\)\)\)/);
  assert.match(VIEW, /loadComments\(\);/);
});

test("right-click on a selection offers Comment — bound on the viewer body so it is REACHABLE", () => {
  // The regression: bound on the wrong element, the browser's native menu wins and the only gesture
  // that makes a comment does nothing at all (2026-08-18).
  assert.match(VIEW, /body\.addEventListener\("contextmenu"/);
  assert.match(VIEW, /!body\.contains\(sel\.anchorNode\)\) return;/);
  assert.match(VIEW, /item\("Comment", \(\) => askComment\(picked\)\);/);
});

test("that menu clears the viewer's own backdrop, or it is invisible", () => {
  // The other half of the same regression: .ctx-menu is z-100, the viewer's backdrop far above it,
  // so an unlifted menu renders BEHIND the panel it belongs to. Compare the two, never hardcode.
  const menuZ = Number(CSS.match(/\.ctx-menu\.fv-menu \{ z-index: (\d+); \}/)![1]);
  const backZ = Number(CSS.match(/#romp-fileview \{ position: fixed; inset: 0; z-index: (\d+);/)![1]);
  assert.ok(menuZ > backZ, `menu z-index ${menuZ} must exceed the viewer backdrop's ${backZ}`);
});

test("Submit DRAFTS one assembled message — it never sends, and never clobbers a draft", () => {
  const fn = VIEW.slice(VIEW.indexOf('submitBtn.addEventListener("click"'));
  const body = fn.slice(0, fn.indexOf("\n  });"));
  assert.match(body, /buildReviewMessage\(path, list\)/);
  assert.match(body, /commentSink!\(/);
  assert.doesNotMatch(body, /sendMessage/, "Submit drafts; the person sends");
  // the composer side appends and remembers the draft
  const sink = RENDER.slice(RENDER.indexOf("setCommentSink((text) => {"));
  const sinkBody = sink.slice(0, sink.indexOf("\n});"));
  assert.match(sinkBody, /ta\.value = ta\.value \+ sep \+ text;/);
  assert.match(sinkBody, /drafts\.set\(sid, ta\.value\);/);
  assert.match(sinkBody, /persistDrafts\(\);/);
});

test("the click is acknowledged before the round trip", () => {
  assert.match(VIEW, /submitBtn\.disabled = true;\s*\n\s*submitBtn\.textContent = "Submitting/);
});

test("a file that changed under the reader is called out, not silently mis-numbered", () => {
  assert.match(VIEW, /\.then\(\(fresh\) => fresh !== text\)/);
  assert.match(VIEW, /the file changed while I was reading it/);
  // a failed re-read must not fabricate staleness nobody observed
  assert.match(VIEW, /\.catch\(\(\) => false\)/);
});

test("marks reuse the chat threads' re-anchoring, so a span found in Rendered survives Raw", () => {
  assert.match(VIEW, /findAnchorRange\(nodes\.map\(\(t\) => t\.data\)\.join\(""\), c\.quote\)/);
  assert.match(VIEW, /sliceRanges\(nodes\.map\(\(t\) => t\.data\.length\), r\.start, r\.end\)/);
  assert.match(VIEW, /markComments\(\);/);
  assert.match(CSS, /mark\.fv-hl \{/);
});

test("the comment's text is one click under its number (progressive disclosure)", () => {
  assert.match(VIEW, /function showNote\(c: DocComment, at: HTMLElement\)/);
  assert.match(VIEW, /const same = open\?\.dataset\.dcid === c\.id;/);   // same marker toggles it shut
  assert.match(CSS, /\.fv-note \{/);
});

test("the review controls stay hidden until this file actually has a comment", () => {
  assert.match(VIEW, /cmtCount\.hidden = !n;\s*\n\s*submitBtn\.hidden = !n;/);
});

test("the viewer does not import render.ts — the sink is registered inward", () => {
  assert.doesNotMatch(VIEW, /from "\.\/render"/, "that would be an import cycle");
  assert.match(VIEW, /export function setCommentSink/);
  assert.match(RENDER, /import \{ openFileView, setCommentSink \} from "\.\/file-view";/);
});
