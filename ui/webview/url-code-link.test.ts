// Every URL shape a session emits renders TAPPABLE in the chat (the user 2026-08-16, on mobile,
// wanting to tap a dashboard link a session sent). Bare URLs and [text](url) already link — marked
// runs gfm autolink and the global anchor click delegate opens any absolute-scheme href (web:
// window.open noopener; VS Code: host openExternal). The dead shape was the WHOLE-BACKTICK URL,
// which stayed a plain <code> span: linkifyFileUris now wraps it in an anchor that keeps the code
// styling. Inline spans only, whole-URL-only, never double-wrapped. Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("bare-URL autolinking stays on (gfm) and the click delegate opens absolute schemes", () => {
  assert.match(RENDER, /marked\.setOptions\(\{ gfm: true, breaks: false \}\);/);
  assert.match(RENDER, /window\.open\(href, "_blank", "noopener,noreferrer"\)/);
  assert.match(RENDER, /vscodeApi\.postMessage\(\{ type: "openLink", href \}\);/);
});

test("a whole-backtick http(s) URL becomes a tappable link that still looks like code", () => {
  assert.match(RENDER, /if \(!\/\^https\?:\\\/\\\/\\S\+\$\/\.test\(t\)\) continue;/,
    "only when the span's ENTIRE text is one URL — quoted URLs inside prose code stay code");
  assert.match(RENDER, /if \(code\.closest\("pre"\) \|\| code\.closest\("a"\)\) continue;/,
    "inline spans only, never inside a block or an existing anchor");
  assert.match(RENDER, /a\.className = "url-code-link";/);
  assert.match(RENDER, /code\.replaceWith\(a\);\s*\n\s*a\.appendChild\(code\);/, "the code keeps its dress inside the link");
  assert.match(CSS, /\.url-code-link code \{ text-decoration: underline dotted;/);
  assert.match(CSS, /\.url-code-link:hover code \{ color: var\(--accent\); \}/);
});
