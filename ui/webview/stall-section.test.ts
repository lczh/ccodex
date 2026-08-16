// The STALLED card section (the user 2026-07-23). A working card romp is HOLDING gets a fifth
// mutually-exclusive section beside Background / Summary / Sub-goals / Waiting-on-task, clicked open the
// same way. Its colour is the one difference: every other toggle turns accent-blue when selected, which is
// the app's "this is the one you picked" language, but a stall is not a preference — it is something wrong,
// so it keeps working-yellow in BOTH states and only its fill changes. Source-pinned like the sibling
// distill-background test: feed.ts builds the card imperatively, so the wiring is asserted over the source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("the card carries the kernel's stalled field", () => {
  assert.match(FEED, /stalled\?: \{ why: string; since: number; note\?: string \| null; blocked\?: boolean \} \| null;/,
    "the AskItem declares the kernel's stall record");
});

test("Stalled is a real section button, in the toggle row, with its own class", () => {
  assert.match(FEED, /const stallBtn = el\("button", "fask-secbtn fask-stallbtn"\); stallBtn\.textContent = "Stalled";/);
  assert.match(FEED, /row3\.append\(bgBtn, takeBtn, stallBtn, subBtn, taskBtn, actions\)/,
    "it rides the same row as the other toggles");
  assert.match(FEED, /secs\.append\(bgBody, distill, stallBody\)/,
    "and its body rides the same body container");
});

test("it joins the ONE mutually-exclusive selection, so opening it closes the others", () => {
  assert.match(FEED, /const secChoice = new Map<string, "bg" \| "summary" \| "subgoals" \| "tasks" \| "stall" \| "none">\(\)/);
  assert.match(FEED, /if \(choice === "stall" && !stall\) choice = "none";/,
    "a card with no stall can never sit on the stall section");
  assert.match(FEED, /a\._stallBtn\.onclick = pick\("stall"\);/);
  assert.match(FEED, /a\._stallBtn\.classList\.toggle\("on", choice === "stall"\);/);
});

test("opening the stall section shows the shared bodies CONTAINER, not just the body", () => {
  // The 2026-07-23 dead-toggle bug: stallBody lives inside _secs, and _secs only showed for bg/summary —
  // so clicking Stalled pressed the button .on while its body sat inside a display:none parent. "It
  // selects but nothing happens" (the user, the very first click on the day-old section).
  assert.match(FEED,
    /a\._secs\.style\.display = \(choice === "bg" \|\| choice === "summary" \|\| choice === "stall"\) \? "" : "none";/,
    "every choice whose body rides _secs must be in the container's show condition");
});

test("the button shows on the kernel's why alone, not on a judge-written note", () => {
  // The whole point is that a stalled card is never mute. The kernel knows the mechanical reason before the
  // staller is ever called, so the section must not wait on the LLM to have produced anything.
  assert.match(FEED, /const stall = it\.stalled && it\.stalled\.why \? it\.stalled : null;/);
  assert.match(FEED, /a\._stallBtn\.style\.display = stall \? "" : "none";/);
});

test("the body prefers the staller's note and falls back to the mechanical reason", () => {
  assert.match(FEED, /function stallText\(/);
  assert.match(FEED, /return note \|\| \("Nothing is moving this: romp is waiting on " \+ st\.why \+ "\."\);/,
    "never '(generating…)' — there is always something true to say");
});

test("the toggle wears the problem-chip OUTLINE, deepening to a yellow wash when selected", () => {
  // Outline at rest — yellow text + translucent yellow border on transparent, the same vocabulary as
  // .fask-warnchip / .fask-interrupted (the user 2026-07-27, superseding the 2026-07-23 solid fill,
  // which broke ranks with every other trouble chip). Selected = wash + full border + weight: clearly
  // pressed, never the solid working-status fill.
  assert.match(CSS, /\.fask-stallbtn \{ color: #ffd166; background: transparent; border-color: rgba\(255, 209, 102, 0\.6\); \}/,
    "at rest it reads like the card's other trouble chips: yellow outline, never a fill");
  assert.match(CSS, /\.fask-stallbtn\.on, \.fask-stallbtn\.on:hover \{ color: #ffd166; border-color: #ffd166;\n  background: rgba\(255, 209, 102, 0\.18\); font-weight: 600; \}/,
    "selected deepens within the same vocabulary: wash + solid border + weight, the note below showing");
  assert.doesNotMatch(CSS, /\.fask-stallbtn[^{]*\{[^}]*var\(--st-working-bg\)/,
    "never the solid working-status fill again");
  assert.doesNotMatch(CSS, /\.fask-stallbtn[^{]*\{[^}]*var\(--accent\)/,
    "it must never take the accent the other selected toggles use — yellow is a STATUS colour here");
});

test("the stall body reads exactly like the summary and background bodies", () => {
  assert.match(CSS, /\.fask-bg-body, \.fask-stall-body \{[^}]*font-size: 0\.86em/,
    "one text style per card section — no new font size");
});
