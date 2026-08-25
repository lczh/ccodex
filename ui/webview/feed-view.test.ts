// The FEED follows the active session view (the user 2026-08-24): the board gates on the SAME
// union-aware decider the tabs/lanes read (session-views.ts viewVisible through feed-view.ts —
// never a fourth fork), a needs-you card ALWAYS breaks through wearing its cue, and what's filtered
// stays one glance away ("N cards outside this view", the 2026-08-11 rule). The deciders EXECUTE
// here with the tag-home fixtures the untagged-union fix established; the feed/kernel wiring is
// source-pinned (the repo convention).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { cardInView, outsideView, outsideViewCount, viewLabel } from "./feed-view";
import type { SessionViews } from "./session-views";

const ROOT = path.resolve(process.cwd(), "..");
const FEED = fs.readFileSync(path.join(ROOT, "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.join(ROOT, "ui", "webview", "feed.css"), "utf8");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");
const FED = fs.readFileSync(path.join(ROOT, "ui", "webview", "federation.ts"), "utf8");

const U = "11111111-2222-3333-4444-555555555555";
const V = "99999999-8888-7777-6666-555555555555";

test("no views blob (an older kernel) → today's behavior, byte-identical: nothing filters", () => {
  assert.equal(cardInView(null, U, false), true);
  assert.equal(outsideView(undefined, U), false);
  assert.equal(outsideViewCount(null, [{ sid: U, needsYou: false }]), 0);
});

test("All shows literally everything (hidden retired 2026-08-24); a tag-filtered card still breaks through", () => {
  // a legacy hidden entry no longer hides anywhere — the tag system covers backgrounding
  assert.equal(cardInView({ active: "all", hidden: [V] }, V, false), true, "legacy hidden ignored under All");
  const tagged: SessionViews = { active: "untagged", tags: [{ id: "t9", name: "pool", members: [V] }] };
  assert.equal(cardInView(tagged, U, false), true);
  assert.equal(cardInView(tagged, V, false), false, "tagged — out of the untagged board");
  assert.equal(cardInView(tagged, V, true), true, "…until it needs the human (the interrupt rule)");
  assert.equal(outsideView(tagged, V), true, "and then it wears the outside-view cue");
});

test("untagged view: BOTH tag homes exclude — a local tag and a remote-homed tag alike (the union fix)", () => {
  const local: SessionViews = { active: "untagged", tags: [{ id: "t1", name: "infra", members: [U] }] };
  assert.equal(cardInView(local, U, false), false, "locally tagged → out of untagged");
  const remote: SessionViews = { active: "untagged", tags: [],
    remoteTags: [{ id: "TESTHOST:t9", name: "infra", members: [U] }] };
  assert.equal(cardInView(remote, U, false), false, "a remote-homed tag counts everywhere");
  assert.equal(cardInView(remote, U, true), true, "the breakthrough guard outranks both homes");
  assert.equal(cardInView(remote, V, false), true, "the genuinely untagged session stays");
});

test("a tag view shows the NAME-KEYED union's members; outsiders only break through", () => {
  const views: SessionViews = { active: "t1",
    tags: [{ id: "t1", name: "infra", members: [U] }],
    remoteTags: [{ id: "TESTHOST:t9", name: "infra", members: [V] }] };
  assert.equal(cardInView(views, U, false), true, "the active tag's own member");
  assert.equal(cardInView(views, V, false), true, "the same-NAME remote tag's member joins the union");
  const w = "77777777-6666-5555-4444-333333333333";
  assert.equal(cardInView(views, w, false), false, "a session in neither home is outside");
  assert.equal(cardInView(views, w, true), true, "…unless it needs you");
});

test("the outside count is exactly what the board is NOT showing — breakthroughs excluded", () => {
  const views: SessionViews = { active: "untagged", tags: [{ id: "t1", name: "infra", members: [U, V] }] };
  const n = outsideViewCount(views, [
    { sid: U, needsYou: false },   // hidden, counted
    { sid: U, needsYou: false },   // hidden, counted (per card, not per session)
    { sid: V, needsYou: true },    // breaks through — already visible, NOT counted
  ]);
  assert.equal(n, 2);
});

test("the feed gates through the shared decider and adopts the payload's views blob", () => {
  assert.match(FEED, /import \{ cardInView, outsideView, outsideViewCount, viewLabel \} from "\.\/feed-view";/);
  assert.match(FEED, /let shown = feedViews \? list\.filter\(\(a\) => cardInView\(feedViews, a\.sid, askColumn\(a\) === "needsInput"\)\) : list;/,
    "the view layer runs FIRST in viewFiltered — the freeze badges and the render share it");
  // adoption rides every payload; the optimistic pending copy follows the tab strip's own
  // convention (render.ts pendingSessionViews): shape-matched echo or three pushes — never a timer
  assert.match(FEED, /if \(m\.views && typeof m\.views === "object"\) \{/);
  assert.match(FEED, /viewsKey\(incoming\) === viewsKey\(feedViewsPending\) \|\| \+\+feedViewsPendingAge >= 3/);
  // the kernel attaches the SAME blob every tabOrder push carries
  assert.ok(KERNEL.includes('"views": _views_client(),'), "build_feed carries the views blob");
  // federation: mergeHostFeeds spreads the LOCAL payload first and never reassigns views — the
  // viewer's blob passes through untouched (remote kernels' actives are their dashboards' business)
  const mf = FED.slice(FED.indexOf("export function mergeHostFeeds"), FED.indexOf("return merged;"));
  assert.ok(mf.includes("const merged: any = { ...local,"), "local spread first");
  assert.ok(!mf.includes("merged.views"), "and no later reassignment clobbers it");
});

test("the breakthrough cue rides BOTH card shapes as a row2-visible mark, the ↪ family", () => {
  assert.match(FEED, /viewbreak\.textContent = "\\u21aa outside this view";/);
  assert.match(FEED, /\(a\._viewbreak as HTMLElement\)\.style\.display = outsideView\(feedViews, it\.sid\) \? "" : "none";/,
    "ask cards toggle it by the session's view state");
  assert.match(FEED, /const gbroke = outsideView\(feedViews, g\.sid\);/,
    "group cards too — a group is one session's turn");
  assert.match(FEED, /if \(gvb\) gvb\.style\.display = gbroke \? "" : "none";/);
  // grouped mode hides the group's row2 (name-only) — EXCEPT when the breakthrough cue is live
  assert.match(FEED, /\(a\._row2 as HTMLElement\)\.style\.display = gmode && !gbroke \? "none" : "";/);
  assert.match(FEED, /row2\.append\(idwrap, gviewbreak\);/, "the cue is a row2 SIBLING — idwrap hides wholesale grouped");
  // a DIRECT row2 child on the ask card: grouped mode hides idwrap wholesale, and a breakthrough
  // under a session header still needs its why
  assert.match(FEED, /origin, viewbreak, fupBadge/);
  assert.match(CSS, /\.fask-viewbreak \{ color: var\(--dim\); font-size: 0\.82em; font-style: italic;/,
    "dim, sub-line scale, never a status colour");
});

test("the near-empty board says WHICH view it shows — the whisper promotes to card chrome", () => {
  // the user 2026-08-25 read a view-narrowed board as "the feed is broken": one card showed, twenty
  // hid, the dim line went unseen. Exact promotion rule: the view hides more than the board shows.
  assert.match(FEED, /const shownN = viewFiltered\(asks\)\.length;/);
  assert.match(FEED, /vmore\.classList\.toggle\("prominent", outN > shownN\);/);
  assert.match(FEED, /"Showing the \\u201c" \+ viewLabel\(feedViews\) \+ "\\u201d view — " \+ outN/,
    "the promoted line NAMES the active view");
  assert.match(CSS, /#feed-viewmore\.prominent \{ margin: 6px 8px 2px; padding: 10px 14px; background: #252526;/,
    "the judge-limit banner's own card chrome — neutral, never a status colour");
  // label truth: a tag view is its name; untagged says plain words; All only excludes the hidden set
  assert.equal(viewLabel({ active: "untagged" }), "no tags");
  assert.equal(viewLabel({ active: "t1", tags: [{ id: "t1", name: "infra" }] }), "infra");
  assert.equal(viewLabel({ active: "TESTHOST:t9", tags: [], remoteTags: [{ id: "TESTHOST:t9", name: "infra" }] }), "infra");
  assert.equal(viewLabel({ active: "all" }), "All");
  assert.equal(viewLabel(null), "All");
});

test("what's filtered stays one glance away: the N-outside line, click-switches to All", () => {
  assert.match(FEED, /const outN = feedViews \? outsideViewCount\(feedViews,/);
  assert.match(FEED, /vmore\.id = "feed-viewmore";/);
  assert.match(FEED, /vmore\.style\.display = outN \? "" : "none";/, "zero outside → no line, not a zero");
  assert.match(FEED, /outN \+ \(outN === 1 \? " card" : " cards"\) \+ " outside this view — show all";/,
    "the whisper stays for a lightly narrowed board (outN <= shown)");
  // the click switches the ACTIVE view to All — optimistically (the board reflows now, the
  // click-safety acknowledgement), through the same whole-blob op the dashboard uses
  assert.match(FEED, /feedViewsPending = \{ \.\.\.feedViews, active: "all" \};/);
  assert.match(FEED, /vscodeApi\?\.postMessage\(\{ type: "setTimelineViews", views: feedViewsPending \}\);/);
  assert.match(CSS, /#feed-viewmore \{ padding: 4px 14px 10px; color: var\(--dim\); font-size: 0\.82em; cursor: pointer; \}/);
  assert.match(CSS, /#feed-viewmore:hover \{ color: var\(--accent\); text-decoration: underline; \}/);
});
