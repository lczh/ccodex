// Notice cards (the user 2026-07-06): informational transcript notices — a backgrounded agent's report, a
// romp SYSTEM notice, folded system-reminders — each get their OWN boxed card via the shared noticeCard():
// a type chip + a one-line gist head + a KEYED collapse (survives re-renders, unlike postal/teammate). One
// family with a per-variant color (agent = accent blue, romp = swirl + faded-accent, reminder = muted),
// distinct from postal (per-peer color) and teammate (dashed neutral). Source-level pins (no jsdom, repo convention).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
function fn(name: string): string {
  const m = RENDER.match(new RegExp("function " + name + "\\([\\s\\S]*?\\n}\\n"));
  return m ? m[0] : "";
}
const NOTICE = fn("noticeCard");
const AGENT = fn("renderAgentNotif");

test("noticeCard is a boxed card: rail dot (standalone) + .notice-card + a head with a type chip", () => {
  assert.ok(NOTICE, "noticeCard is defined");
  assert.match(NOTICE, /el\("div", "turn turn-notice notice-" \+ o\.variant\)/);   // standalone-only wrapper
  assert.match(NOTICE, /el\("div", "notice-card notice-card-" \+ o\.variant/);
  assert.match(NOTICE, /el\("span", "notice-chip notice-chip-" \+ o\.variant\)/);
  // the romp swirl only when asked (the romp variant)
  assert.match(NOTICE, /if \(o\.logo\)/);
  assert.match(NOTICE, /romp-swirl-glyph\.svg/);
});

test("agent + reminder notices are NESTED (bare card, no inner turn/dot) so they connect to the parent turn's rail", () => {
  // the fix (the user 2026-07-06): these are appended INSIDE the carrying user turn, so a turn-in-a-turn drew
  // a 2nd rail + dot, indented another 24px → the card floated off the timeline. Nested = bare card.
  assert.match(NOTICE, /if \(o\.nested\) return card;/);
  assert.match(RENDER, /variant: "agent", chip, head, body[\s\S]*?nested: true/);
  assert.match(RENDER, /variant: "reminder", chip: "system"[\s\S]*?nested: true/);
  assert.match(CSS, /\.notice-nested \{ margin-top: 8px; \}/);
  // the standalone (romp system) path still keeps its own rail dot
  assert.match(NOTICE, /el\("div", "turn turn-notice notice-" \+ o\.variant\)/);
  assert.match(NOTICE, /d\.classList\.add\("notice-dot", "notice-dot-" \+ o\.variant\)/);
});

test("the collapse is KEYED (survives re-render) and driven by the head, not a separate summary line", () => {
  assert.match(NOTICE, /card\.classList\.add\("notice-collapsible"\)/);
  assert.match(NOTICE, /applyFold\(card, "notice-open", o\.key\)/);
  assert.match(NOTICE, /rememberFold\(card, "notice-open", o\.key\)/);
  assert.match(NOTICE, /headEl\.addEventListener\("click"/);
});

test("agent notices use the accent-blue variant; the chip reads 'task' for a command, 'agent' for an agent", () => {
  assert.match(AGENT, /noticeCard\(\{ variant: "agent", chip, head, body/);
  assert.match(AGENT, /const chip = a\.kind === "agent" \? "agent" : "task";/);
  assert.match(CSS, /\.notice-card-agent \{ border-left-color: var\(--accent\); \}/);
  assert.match(CSS, /\.notice-chip-agent \{ color: var\(--accent\)/);
});

test("system-reminders use the muted 'reminder' variant (no more the plain italic fold)", () => {
  assert.match(RENDER, /noticeCard\(\{ variant: "reminder", chip: "system"/);
  assert.doesNotMatch(RENDER, /reminder-fold/, "the old plain fold is gone");
  assert.match(CSS, /\.notice-chip-reminder \{ font-style: italic; \}/);
});

test("a romp SYSTEM notice becomes a romp notice card (swirl) — NOT the gray nudge bubble", () => {
  // gated on the server flag, strips the markers + the [romp] prefix, draws the romp variant with the swirl
  assert.match(RENDER, /if \(\(ev as any\)\.rompSystem && ev\.md\)/);
  assert.match(RENDER, /replace\(\/<!--\[\\s\\S\]\*\?-->\/g, ""\)\.replace\(\/\^\\s\*\\\[romp\\\]\\s\*\/i, ""\)/);
  assert.match(RENDER, /noticeCard\(\{ variant: "romp", chip: "romp", logo: true/);
  // faded-accent, so it reads as "the romp blue" without competing with the bright agent card
  assert.match(CSS, /\.notice-card-romp \{ border-left-color: rgba\(156, 210, 255, 0\.45\); \}/);
});

test("a standalone ROMP notice sits on the RIGHT, like every message from romp; other variants stay left", () => {
  // the user 2026-08-18: in the transcript, direction says who is speaking — the session from the
  // left, romp and the user from the right. The kernel-restart notice wore the session's side.
  assert.match(CSS, /\.turn-notice\.notice-romp \{ display: flex; flex-direction: column; align-items: flex-end; \}/);
  assert.match(CSS, /\.turn-notice\.notice-romp > \.notice-card \{ max-width: 72%; \}/,
    "the bubbles' width cap — it reads beside the nudges it travels with");
  assert.doesNotMatch(CSS, /\.turn-notice \{[^}]*flex-end/,
    "unscoped: agent/reminder notices are session-side output and keep the left edge");
});

test("the notice family is DIFFERENTIABLE from postal + teammate", () => {
  assert.doesNotMatch(NOTICE, /postal-service|--peer-bg|teammate/, "no postal/teammate chrome inside noticeCard");
  // its own keyed collapse rule, not the postal/teammate .expanded toggle
  assert.match(CSS, /\.notice-collapsible:not\(\.notice-open\) \.notice-body \{ display: none; \}/);
});
