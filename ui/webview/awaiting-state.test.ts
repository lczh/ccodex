// The distinct AWAITING state (the user 2026-07-13, who wanted to differentiate working from awaiting): a session
// whose main thread is idle but waiting on background work it dispatched no longer folds into "working".
// The kernel's shared _session_chip emits `awaitingBg`; the chat chip says "Awaiting" in the romp brand
// GREEN (--st-awaitbg-bg #54B204, the swirl's green arm — distinct from Working's gold), and the little
// dots match the chip's color everywhere: the chat tab dot and the feed's fwork-dot (cards, group cards,
// modal headers, grouped-mode session headers). ("awaiting" the chip state = a live permission/picker
// prompt, on YOU — a different concept; the Bg suffix dodges that name.) Source pins (no jsdom).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const W = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const RENDER = W("render.ts");
const STYLES = W("styles.css");
const FEED = W("feed.ts");
const FEEDCSS = W("feed.css");
const FED = W("federation.ts");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "bin", "romp-kernel"), "utf8");

test("the chat chip knows awaitingBg: its own await-green chip, label 'Awaiting', with the elapsed timer", () => {
  assert.match(RENDER, /"awaiting" \| "awaitingBg" \|/);           // the ChipState union carries both meanings
  assert.match(RENDER, /awaitingBg: "Awaiting",/);                 // CHIP_LABEL
  // its own statusline branch: await-green chip + the wait's clock — but NO pulse (nothing computing here)
  assert.match(RENDER, /\} else if \(s\.status\.state === "awaitingBg"\) \{[\s\S]*?chip chip-awaitingBg[\s\S]*?timer\.id = "work-timer";/);
  assert.doesNotMatch(RENDER.split('state === "awaitingBg") {')[1].split("} else if")[0], /chip-pulse/);
  // the ticking clock covers it, same as working
  assert.match(RENDER, /if \(s\.status\.state === "working" \|\| s\.status\.state === "awaitingBg"\) \{\s*\n\s*const timer = document\.getElementById\("work-timer"\);/);
  // no stop button — the main thread is idle, there's nothing to interrupt
  assert.doesNotMatch(RENDER, /awaitingBg[^\n]*stopButton|stopButton[^\n]*awaitingBg/);
});

test("the chat tab dot matches the chip: await-green for awaitingBg, yellow for working", () => {
  assert.match(RENDER, /if \(st === "working"\) tab\.appendChild\(el\("span", "tab-dot"\)\);\s*\n\s*else if \(st === "awaitingBg"\) tab\.appendChild\(el\("span", "tab-dot await"\)\);/);
  assert.match(STYLES, /--st-awaitbg-bg: #54B204; --st-awaitbg-fg: #0c1a00;/);
  assert.match(STYLES, /\.chip-awaitingBg \{ background: var\(--st-awaitbg-bg\); color: var\(--st-awaitbg-fg\); \}/);
  assert.match(STYLES, /\.tab-dot\.await \{ background: var\(--st-awaitbg-bg\); \}/);
});

test("the feed dot matches too: dotFor picks work/await per name, the dot retints in place", () => {
  // the kernel's feed payload carries the awaiting name list beside working; federation merges + prefixes it
  assert.match(KERNEL, /"working": working, "awaiting": awaiting,/);
  assert.match(KERNEL, /if sess_awaiting_why and not who_working:\s*\n\s*awaiting\.append\(name\)/);
  assert.match(KERNEL, /\{"type": "working", "names": feed\["working"\],\s*\n\s*"awaiting": feed\.get\("awaiting"\) or \[\]\}/);
  assert.match(FEED, /awaitingSet = new Set\(Array\.isArray\(m\.awaiting\) \? m\.awaiting : \[\]\);/);
  // dotFor still ranks work over await; the unreadable-state quarter follows (feed-status-pips.test.ts)
  assert.match(FEED, /workingSet\.has\(name\) \? "work" : awaitingSet\.has\(name\) \? "await"/);
  // an existing dot RETINTS when the state flips, instead of only add/remove (now via paint(), which
  // carries the tooltip too — see feed-status-pips.test.ts)
  assert.match(FEED, /else if \(st && has\) paint\(prev!\);/);
  assert.match(FEED, /d\.classList\.toggle\(k, st === k\);/);
  // every name-dot site routes through dotFor: cards, group cards, both modal headers, grouped
  // headers, and the session-filter button (2026-08-08; its menu rows route via setWorkDot(label,…))
  assert.equal((FEED.match(/setWorkDot\((?:a\._name|agent|nm), dotFor\(/g) || []).length, 6);
  assert.match(FEEDCSS, /\.fwork-dot\.await \{ background: #54B204; \}/);
  assert.match(FED, /const ARRAY_ID = \["order", "names", "working", "awaiting", "stateUnknown"\];/);
  assert.match(FED, /if \(Array\.isArray\(f\.awaiting\)\) merged\.awaiting\.push\(\.\.\.f\.awaiting\);/);
});

test("the kernel split happens in the ONE shared derivation (_session_chip), not per surface", () => {
  assert.match(KERNEL, /"working" if open_now else\n/);
  assert.match(KERNEL, /"awaitingBg" if awaiting_why else "ready"\)/);
});

test("the awaiting WHY lives in the background box, not the statusline (the user 2026-08-13, twice)", () => {
  // the kernel ships the why + the live awaited task descriptions in the chat status payload…
  assert.match(KERNEL, /"awaitingWhy": awaiting_why or None,/);
  assert.match(KERNEL, /"awaitingTasks": \(_awaiting_task_descs\(sid, sess\["path"\]\) if awaiting_why else \[\]\),/);
  // …plus WHAT the wait is on, as data (jd.AWAIT_KINDS; the user 2026-08-15) — on the chat status,
  // the timeline lane, and the feed card's awaiting object alike, so every surface words one fact
  assert.match(KERNEL, /"awaitingKind": awaiting_kind,/);
  assert.match(KERNEL, /"kind": await_kind,/);
  // …the statusline branch stays chip + clock ONLY — the reason line PR #350 put beside the chip
  // crowded the composer area, and the user moved it the same day
  const branch = RENDER.split('state === "awaitingBg") {')[1].split("} else if")[0];
  assert.doesNotMatch(branch, /sl-await-why/);
  assert.doesNotMatch(STYLES, /sl-await-why/);
  // …and the #bg-tasks box renders it when no tracked tasks claim the box: the same fold treatment
  // (await-green dot, verb-stripped header), expanding to the full why, the awaited items when there are
  // several, and a plain-words note on what the state means. No Stop — nothing untracked is killable.
  assert.match(RENDER, /\{ renderAwaitWhy\(host, s \|\| null\); return; \}/);
  assert.match(RENDER, /"bg-fold-head bg-await"/);
  assert.match(RENDER, /"Awaiting" \+ \(kw \? " " \+ kw : ""\) \+ " · " \+ why\.replace\(\/\^\(waiting on\|awaiting\)\\s\+\/i, ""\)/);
  // …the kind word rides the visible label (the user 2026-08-15) — tooltips are dead on the touch PWA
  assert.match(RENDER, /KIND_WORD\[\(s!\.status\.awaitingKind \|\| ""\)\]/);
  assert.match(RENDER, /chip-awaiting-" \+ \(s\.status\.awaitingKind \|\| "untyped"\)/);
  assert.match(RENDER, /if \(items\.length > 1\)/);
  assert.match(RENDER, /bg-await-note/);
  assert.doesNotMatch(RENDER.split("function renderAwaitWhy")[1].split("\n}")[0], /bg-stop/);
  assert.match(STYLES, /\.bg-fold-head\.bg-await \{ --bgt: var\(--st-awaitbg-bg\); \}/);
});

test("the timeline lane's awaitingBg why reads the SAME working signal as its badge (same input, 2026-07-03 rule)", () => {
  // the skeleton build's raw-snapshot open_now fed _session_awaiting while the chip read the event
  // model — a lane badge could say Awaiting with a null why beside it (audited live 2026-08-13)
  assert.match(KERNEL, /aw_open = _session_working\(comp_sess\["turns"\]\) if comp_sess is not None else open_now/);
  assert.match(KERNEL, /_session_awaiting\(sid, s\["path"\], not aw_open, stamp=True\) if live else None/);
  // the awaiting stretch's hover labels the wait with the state's one word
  const TL = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");
  assert.match(TL, /– awaiting…/);
  assert.doesNotMatch(TL, /– waiting…/);
});
