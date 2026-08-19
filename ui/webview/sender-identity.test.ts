// The ONE sender classifier (the user 2026-08-18): bubble, rail dot, and scrollbar notch all read
// senderKind, so the surfaces can never desynchronize. Behavioral on the module, plus source pins
// holding both consumers to it.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { senderKind } from "./sender-identity";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the four kinds, and their precedence", () => {
  assert.equal(senderKind({ human: true, md: "hello" }), "user");
  assert.equal(senderKind({ human: false, romp: true, md: "checking in" }), "romp");
  assert.equal(senderKind({ human: true, romp: true, md: "x" }), "romp", "romp wins — an injection never poses as typed words");
  assert.equal(senderKind({ human: true, tag: "nightly-optimizer", md: "briefing" }), "tagged");
  assert.equal(senderKind({ human: true, tag: "t" }), "user", "a tag without a body renders no tag chip — same rule as the dress");
  assert.equal(senderKind({ human: false }), "injected");
});

test("both consumers read the classifier — never a second hand-maintained predicate", () => {
  // the bubble/dot site derives its flags from the ONE verdict…
  assert.match(RENDER, /const kind = senderKind\(ev\);\s*\n\s*const romp = kind === "romp";\s*\n\s*const injected = kind === "injected";\s*\n\s*const tagged = kind === "tagged";/);
  // …and the notch painter reads the SAME function, coloring by it
  assert.match(RENDER, /const kind = senderKind\(ev\);\s*\n\s*if \(kind === "injected"\) continue;/);
  assert.match(RENDER, /offs\.push\(\{ top: off, m: kind === "user" \? "" : "machine" \}\);/);
  // machine notches wear the light gray, never the blue that means "yours"
  assert.match(CSS, /\.scroll-marks \.scroll-mark\.machine \{ background: #8a8f98; opacity: 0\.55; \}/);
});
