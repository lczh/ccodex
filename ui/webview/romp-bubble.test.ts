// The gray "romp-injected" bubble (the user 2026-06-19): a message romp pasted into the pane (a feed
// nudge / follow-up) renders as a GRAY right-aligned bubble with a "↯ romp" tag — same spot as the blue
// user bubble, but clearly romp, not you. The renderer has no jsdom harness, so pin the wiring at source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("a user ChatEvent can carry a romp flag", () => {
  assert.match(RENDER, /kind: "user";[^}]*romp\?: boolean/);
});

test("a romp event renders the gray romp-bubble + a romp tag, NOT the blue or the note box", () => {
  assert.match(RENDER, /const romp = kind === "romp";/, "derived from the ONE senderKind verdict (2026-08-18)");
  // 'injected' (the neutral left note) excludes romp, so romp gets its own branch
  assert.match(RENDER, /const injected = kind === "injected";/, "same verdict — the predicate itself lives in sender-identity.ts");
  // the tag shows the romp swirl-glyph LOGO (not the old ↯ symbol) + "romp" (the user 2026-06-19)
  assert.match(RENDER, /el\("img", "romp-tag-logo"\)/);
  assert.match(RENDER, /logo\.src = mediaSrc\("romp-swirl-glyph\.svg"\)/);
  assert.match(RENDER, /createTextNode\("romp"\)/);
  assert.doesNotMatch(RENDER, /tag\.textContent = "↯ romp"/, "the ↯ placeholder is gone");
  assert.match(RENDER, /\(romp \? "romp-bubble" : tagged \? "romp-bubble tag-bubble" : injected \? "user-note" : "user-bubble"\)/);
  // its own gray rail dot
  assert.match(RENDER, /dot\(romp \? "romp" : tagged \? "tag" : injected \? "ring" : "user"\)/);
  assert.match(RENDER, /"green" \| "ring" \| "user" \| "red" \| "romp"/, "the dot helper knows the romp variant");
});

test("the swirl LOGO is on EVERY romp bubble, next to the 'romp' tag (the user 2026-07-05; supersedes the 2026-06-23 auto-nudge-only gating)", () => {
  assert.match(RENDER, /kind: "user";[^}]*rompAuto\?: boolean/);
  // the <img> logo appends UNCONDITIONALLY inside the romp branch (no `if (ev.rompAuto)` gate), immediately
  // before the "romp" textnode — so any romp-tagged message (system notice, auto-nudge, or Nudge click) shows it
  assert.doesNotMatch(RENDER, /if \(ev\.rompAuto\) \{[\s\S]*?el\("img", "romp-tag-logo"\)/);
  assert.match(RENDER, /const tag = el\("div", "romp-tag"\);\s*const logo = el\("img", "romp-tag-logo"\)[\s\S]*?tag\.appendChild\(logo\);\s*tag\.appendChild\(document\.createTextNode\("romp"\)\)/);
});

test("a postal card carries the romp swirl (postal is 'from romp' too — the user 2026-06-23)", () => {
  assert.match(RENDER, /el\("img", "postal-service-romp-logo"\)/);
  assert.match(RENDER, /rlogo\.src = mediaSrc\("romp-swirl-glyph\.svg"\)/);
  assert.match(CSS, /\.postal-service-romp-logo \{/);
});

test("the romp bubble is a gray, right-aligned bubble (inherits the non-injected right-align)", () => {
  // the turn carries 'romp' (no 'injected'), so .turn-user:not(.injected) right-aligns it
  assert.match(RENDER, /"turn turn-user" \+ \(romp \? " romp" : injected \? " injected" : ""\)/);
  assert.match(CSS, /\.romp-bubble \{[\s\S]*?background: rgba\(255, 255, 255, 0\.08\)/);
  assert.match(CSS, /\.romp-tag \{/);
  // its rail dot is the swirl in a dark disc since 2026-07-23, matching the timeline's romp glyph —
  // the bubble stays gray, but the dot is no longer anonymous. Pinned in rail-line-hover.test.ts.
  assert.match(CSS, /\.dot\.romp \{ background: #000; border: 1px solid #e8eef5; \}/);
});
