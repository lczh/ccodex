// The composer 📎 attach glyph and the statusline 📁 directory glyph were replaced with monochrome line-icons
// (currentColor inline SVGs) in the romp style — matching the gear/network chrome (the user 2026-07-15).
// Source-level pins (no jsdom for the chat renderer).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const SKELETON = fs.readFileSync(path.resolve(process.cwd(), "src", "page-skeleton.ts"), "utf8");

test("the attach button renders a monochrome paperclip SVG, not the 📎 emoji", () => {
  const attach = SKELETON.slice(SKELETON.indexOf('id="composer-attach"'), SKELETON.indexOf('id="composer-send"'));
  assert.match(attach, /<svg[^>]*stroke="currentColor"/);   // monochrome — inherits the button tint
  assert.doesNotMatch(attach, /📎/);                          // the emoji is gone
});

test("the statusline directory shows a monochrome folder icon (folderIcon), not the 📁 emoji", () => {
  // folderIcon() builds a currentColor line-SVG in the ctxIcon style; the statusline prepends it before the
  // dir basename text node (so it inherits the dim tint and brightens on the .folder-link hover)
  assert.match(RENDER, /function folderIcon\(\): HTMLElement \{/);
  assert.match(RENDER, /"status-dir-icon"/);
  assert.match(RENDER, /<svg viewBox="0 0 16 16"[^>]*stroke="currentColor"/);
  assert.match(RENDER, /dir\.appendChild\(folderIcon\(\)\);/);
  assert.match(RENDER, /dir\.appendChild\(document\.createTextNode\(" " \+ \(s\.cwd/);
  // the emoji is no longer set as the statusline dir text
  assert.doesNotMatch(RENDER, /dir\.textContent = "📁/);
  // and the icon has an alignment rule
  assert.match(CSS, /\.status-dir-icon \{/);
});

test("the paperclip and send glyphs wear the romp accent blue (the user 2026-07-15)", () => {
  // action affordances (not status), so var(--accent) is the intended use — currentColor on the paperclip SVG
  // then picks up the accent tint from the button color
  assert.match(CSS, /#composer-attach, \.cmt-attach \{ color: var\(--accent\)/);   // the offset moved to the sizing block (2026-07-29); the comment popover's clip shares the rule (2026-08-17)
  assert.match(CSS, /#composer-send:not\(:disabled\), \.cmt-send:not\(:disabled\) \{ color: var\(--accent\)/);
});
