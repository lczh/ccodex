// Comment threads (the user 2026-08-13): the pure half (comments.ts) is driven behaviorally; the
// render.ts / kernel / CSS wiring is pinned at the source (no jsdom harness for the renderers — the
// repo convention). Synthetic text only.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { threadsByAnchor, threadBusy, threadStuck, findExact, findAnchorRange, sliceRanges, prunePending,
         type CommentThread } from "./comments";

const th = (over: Partial<CommentThread>): CommentThread => ({
  tid: "t1", anchorUuid: "a1", exact: "the passage", status: "open", createdT: 0,
  state: "", unread: false, promotedName: "", msgs: [], ...over,
});

// ── findExact: whitespace-tolerant re-anchoring ────────────────────────────────────────────────

test("findExact finds a verbatim passage", () => {
  const r = findExact("Use exponential backoff with jitter.", "exponential backoff");
  assert.ok(r);
  assert.equal("Use exponential backoff with jitter.".slice(r!.start, r!.end), "exponential backoff");
});

test("findExact tolerates collapsed and rewrapped whitespace", () => {
  // the selection was made on one rendering; the re-render wraps the line elsewhere
  const hay = "Cap the delay\n  at two   minutes.";
  const r = findExact(hay, "delay at two minutes");
  assert.ok(r);
  assert.equal(hay.slice(r!.start, r!.end).replace(/\s+/g, " "), "delay at two minutes");
});

test("findExact returns null when the text drifted away", () => {
  assert.equal(findExact("something else entirely", "the old passage"), null);
});

test("findExact never matches an empty selection", () => {
  assert.equal(findExact("anything", "   "), null);
});

// ── findAnchorRange: longest-prefix fallback for cross-message selections ──────────────────────

test("findAnchorRange returns the full match, not partial, when the text is present", () => {
  const r = findAnchorRange("Use exponential backoff with jitter.", "exponential backoff");
  assert.ok(r && !r.partial);
});

test("findAnchorRange falls back to the longest prefix that lives in this turn", () => {
  // the selection continued into the NEXT message; only its head is in the anchor turn
  const hay = "Cap the delay at two minutes for every retry loop.";
  const r = findAnchorRange(hay, "delay at two minutes for every retry loop. And the jitter stays at ten percent.");
  assert.ok(r);
  assert.ok(r!.partial);
  assert.equal(hay.slice(r!.start, r!.end), "delay at two minutes for every retry loop.");
});

test("findAnchorRange refuses a trivial remnant rather than mark the wrong words", () => {
  assert.equal(findAnchorRange("The cap is fine.", "The completely different selection body"), null);
});

// ── sliceRanges: one global range over many text nodes ─────────────────────────────────────────

test("sliceRanges splits a range across nodes", () => {
  // nodes: "Use " (4) | "exponential" (11) | " backoff." (9); range covers "exponential backoff"
  const slices = sliceRanges([4, 11, 9], 4, 23);
  assert.deepEqual(slices, [
    { idx: 1, s: 0, e: 11 },
    { idx: 2, s: 0, e: 8 },
  ]);
});

test("sliceRanges stays inside one node when the range does", () => {
  assert.deepEqual(sliceRanges([10, 10], 12, 15), [{ idx: 1, s: 2, e: 5 }]);
});

// ── grouping + state predicates ────────────────────────────────────────────────────────────────

test("threadsByAnchor groups threads per turn", () => {
  const by = threadsByAnchor([th({ tid: "t1" }), th({ tid: "t2" }), th({ tid: "t3", anchorUuid: "a2" })]);
  assert.deepEqual([...by.keys()], ["a1", "a2"]);
  assert.equal(by.get("a1")!.length, 2);
});

test("busy and stuck are disjoint state families", () => {
  for (const s of ["working", "retrying", "compacting"]) assert.ok(threadBusy(s) && !threadStuck(s));
  for (const s of ["permission", "picker"]) assert.ok(threadStuck(s) && !threadBusy(s));
  assert.ok(!threadBusy("waiting") && !threadStuck(""));
});

// ── optimistic sends reconcile against the frame ───────────────────────────────────────────────

test("prunePending spends a pending row when its message lands", () => {
  const pending = [{ text: "why jitter?", t: 1 }, { text: "and the cap?", t: 2 }];
  const msgs = [{ who: "you" as const, text: "why  jitter?", t: 5 }];   // whitespace drift tolerated
  assert.deepEqual(prunePending(pending, msgs), [{ text: "and the cap?", t: 2 }]);
});

test("prunePending spends one pending per landed message — a repeated reply keeps its bubble", () => {
  const pending = [{ text: "why?", t: 1 }, { text: "why?", t: 2 }];
  const msgs = [{ who: "you" as const, text: "why?", t: 5 }];
  assert.deepEqual(prunePending(pending, msgs), [{ text: "why?", t: 2 }]);
});

// ── source pins: the wiring (render.ts, kernel.py, sdk_backend.py, styles.css) ─────────────────

const UI = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const BACKEND = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "sdk_backend.py"), "utf8");

test("the selection menu offers Comment, gated on a real transcript turn", () => {
  assert.match(UI, /mk\("Comment", \(\) => openCommentComposer\(/);
  assert.match(UI, /q\?\.uuid && activeId && !isProvisionalId\(activeId\)/);
});

test("marks, badges AND every popover button ride the stable document.body delegate", () => {
  assert.match(UI, /cmtopen: \(elx\) =>/, "delegated — marks are re-created on every rebuild");
  assert.match(UI, /m\.dataset\.act = "cmtopen"/);
  assert.match(UI, /b\.dataset\.act = "cmtopen"/);
  for (const act of ["cmtclose", "cmtsend", "cmtbreak", "cmtresolve", "cmtdelete", "cmtopensession"]) {
    assert.ok(UI.includes(`${act}:`), `${act} handler missing from the body delegate`);
    assert.ok(UI.includes(`dataset.act = "${act}"`), `${act} button missing its data-act`);
  }
});

test("highlights re-apply after every render path", () => {
  assert.match(UI, /m\.type === "session" \|\| m\.type === "chatTail" \|\| m\.type === "chatHead" \|\| m\.type === "chatEpisode"\)\)\s*\n\s*applyCommentMarks\(String\(m\.id\)\)/);
  assert.match(UI, /applyCommentMarks\(activeId\);\s+\/\/ the re-window rebuilt turns/,
               "the scroll re-window path re-anchors too");
  // the syncView wrapper covers renders that run OFF the message handlers (tab switch, prebuild)
  assert.match(UI, /function syncView\(id: string, atBottom\?: boolean\): View \{\s*\n\s*const v = syncViewInner\(id, atBottom\);\s*\n\s*applyCommentMarks\(id\);/);
});

test("a comments frame refreshes the open popover IN PLACE — composer and caret survive", () => {
  assert.match(UI, /prev\.dataset\.mode === mode && prev\.dataset\.tid === \(th \? th\.tid : create!\.uuid\)\s*\n\s*&& prev\.dataset\.status === status/);
  assert.match(UI, /function fillCommentMsgs\(list: HTMLElement, th: CommentThread\)/);
});

test("the popover send acknowledges before any round-trip", () => {
  assert.match(UI, /send\.disabled = true; send\.textContent = "Starting…"; \}\s+\/\/ ack before the round-trip/);
  assert.match(UI, /the pending bubble IS the acknowledgement/);
});

test("create adopts exactly the thread the kernel named — never a guess", () => {
  assert.match(UI, /m\.type === "commentCreated"/);
  assert.match(UI, /function adoptCommentThread\(sid: string, tid: string\)/);
  assert.match(KERNEL, /"type": "commentCreated", "id": sid, "tid": tid/);
  // the FRAME rides ahead of the ack, so adoption always finds the thread in the map
  assert.match(KERNEL, /fr = _comments_frame\(sid\)\s*\n\s*if fr:\s*\n\s*client\["send"\]\(json\.dumps\(fr\)\)\s*\n\s*client\["send"\]\(json\.dumps\(\{"type": "commentCreated"/);
});

test("a refused reply hands the words back instead of thinking forever", () => {
  assert.match(KERNEL, /"type": "commentSendFailed", "id": sid, "tid": str\(msg\["tid"\]\)/);
  assert.match(UI, /m\.type === "commentSendFailed"/);
  assert.match(UI, /commentDrafts\.set\(String\(m\.tid\), lost\.text\)/);
});

test("ending the parent sweeps its threads' CLIs — no unreachable running sessions", () => {
  assert.match(KERNEL, /_comment_kill_all\(sid, be\)/);
  assert.match(KERNEL, /def _comment_kill_all\(parent_sid, be\):/);
});

test("a refused create un-sticks the popover; a pre-seam anchor tip-forks instead of erroring", () => {
  assert.match(UI, /m\.type === "warn" && pendingCommentAnchor\) \{\s*\n\s*document\.getElementById\("cmt-pop"\)\?\.remove\(\);/);
  assert.match(KERNEL, /def _anchor_adapter\(path, sid\)/);
  assert.match(KERNEL, /return "", cut_t, None/);
});

test("the projection never reads the parent transcript pre-fork", () => {
  assert.match(KERNEL, /if reg\.get\("forkOf"\):\s*\n\s*return \[\]/);
});

test("a promoted thread whose session ended drops off the frame entirely", () => {
  assert.match(KERNEL, /if status == "promoted" and not _thread_reg\(tsid\)\.get\("alive"\):\s*\n\s*continue/);
});

test("promote latches the row before seeding; racing ops refuse through the CAS", () => {
  assert.match(KERNEL, /def _comment_update_if\(parent_sid, tid, expect, \*\*changes\):/);
  assert.match(KERNEL, /_comment_update_if\(parent_sid, tid, \("open", "resolved"\), status="promoting"\)/);
  assert.match(KERNEL, /def _revert\(msg\):/);
});

test("the /kill route sweeps comment threads like the WS endSession op", () => {
  assert.match(KERNEL, /_comment_kill_all\(sid, be\)\s+# its comment threads must not outlive it \(the WS endSession twin\)/);
});

test("a thread that couldn't start says so instead of pulsing dots forever", () => {
  assert.match(KERNEL, /be\.launch_error\(tsid\) if hasattr\(be, "launch_error"\) else None/);
  assert.match(UI, /cmt-note cmt-err/);
  assert.match(UI, /!th\.error && \(threadBusy\(th\.state\) \|\| pend\.length\)/);
});

test("Delete is offered only on resolved threads, never mid-promote", () => {
  assert.match(UI, /\} else if \(th\.status === "resolved"\) \{\s+\/\/ never for 'promoting'/);
});

test("break out posts commentPromote and acks with a provisional tab", () => {
  assert.match(UI, /function showBreakoutPrompt\(sid: string, tid: string\)/);
  assert.match(UI, /type: "commentPromote", id: sid, tid, name \}\);\s*\n\s*close\(\);\s*\n\s*closeCommentPop\(\);\s*\n\s*openProvisional\(\{ name, backend: "sdk", dir: "", host: hostOf\(sid\) \}\);/);
});

test("kernel registers every comment drive op", () => {
  for (const op of ["commentCreate", "commentReply", "commentResolve", "commentDelete", "commentSeen", "commentPromote"]) {
    assert.ok(KERNEL.includes(`"${op}"`), `${op} missing from ID_OPS/handlers`);
  }
});

test("the comments frame rides its own dedup slot, never the chat delta baseline", () => {
  assert.match(KERNEL, /_send_client\(c, \("comments", s\["sid"\]\), fr\)/);
});

test("a thread fork withholds the names/ entry; promote seeds first, then registers", () => {
  assert.match(BACKEND, /if not thread_of:\s*\n\s*write_name/);
  assert.match(BACKEND, /def promote_thread\(/);
  assert.match(BACKEND, /reg\.get\("threadOf"\):\s*\n\s*continue/, "live_sessions skips threads — no tab");
  assert.match(KERNEL, /err = _seed_fork_stores\(parent_sid, tsid, parent_path, str\(th\.get\("cutUuid"\) or ""\)\)/);
});

test("the popover drags by its header and closes when you leave the session", () => {
  assert.match(UI, /head\.addEventListener\("pointerdown"/);
  assert.match(UI, /head\.setPointerCapture\(ev\.pointerId\)/);
  assert.match(UI, /commentPopPos = \{ x, y \};/);
  assert.match(UI, /if \(openCommentKey && openCommentKey\.sid !== id\) closeCommentPop\(\);/);
  assert.match(CSS, /\.cmt-head \{[^}]*cursor: grab/s);
});

test("marks use the prefix-tolerant anchor matcher", () => {
  assert.match(UI, /findAnchorRange\(nodes\.map\(\(t\) => t\.data\)\.join\(""\), th\.exact\)/);
});

test("the highlight is highlighter-YELLOW — never the selection blue — and one unbroken block", () => {
  assert.match(CSS, /--cmt-hl: #ffd54a;/);
  assert.match(CSS, /mark\.cmt-hl \{[^}]*var\(--cmt-hl\)/s);
  assert.doesNotMatch(CSS.match(/mark\.cmt-hl \{[^}]*\}/s)![0], /var\(--accent\)/);
  // the radius sits only on the run's outer ends — per-segment rounding drew word-boundary seams
  assert.match(UI, /classList\.toggle\("hl-first", i === 0\)/);
  assert.match(CSS, /mark\.cmt-hl\.hl-first \{ border-top-left-radius: 2px/);
  // a fully-covered inline-code span tints at the ELEMENT: a mark inside it can't paint the code's
  // padded background, which left an untinted sliver around every code word (the word-island look)
  assert.match(UI, /host\.classList\.toggle\("cmt-hl-host", th\.status !== "resolved"\)/);
  assert.match(UI, /p\.classList\.remove\("cmt-hl-host"\)/);
  assert.match(CSS, /code\.cmt-hl-host \{ background: color-mix\(in srgb, var\(--cmt-hl\) 30%, var\(--code-bg\)\)/);
  assert.match(CSS, /code\.cmt-hl-host > mark\.cmt-hl \{ background: transparent/);
});

test("the create dialog names the thread right there: prefilled <session>-comment-<N>, validated", () => {
  assert.match(UI, /nameBox\.value = commentDrafts\.get\(nk\)\s*\n\s*\|\| \(\(sess0\?\.name \|\| "session"\)\.replace\(\/\[\^A-Za-z0-9._-\]\/g, "-"\)\s*\n\s*\+ "-comment-" \+ \(\(commentThreads\.get\(sid\) \|\| \[\]\)\.length \+ 1\)\);/);
  assert.match(UI, /type: "commentCreate", id: create\.sid, uuid: create\.uuid, exact: create\.exact, text, name: nm/);
  assert.match(KERNEL, /"%s-comment-%d" % \(sess\["name"\], len\(data\.get\("threads"\) or \[\]\) \+ 1\)/);
  assert.match(UI, /const base = thName \|\|/, "break-out prefills the thread's own name");
});

test("the landing pulse fires once per navigation, not once per history-fetch round", () => {
  assert.match(UI, /let flashedAnchor: string \| null = null;/);
  assert.match(UI, /if \(flashKey == null \|\| flashKey !== flashedAnchor\)/);
  assert.match(UI, /landOn\(target, uuid\);/);
  assert.match(UI, /if \(anchor\) flashedAnchor = null;/);
});

test("math hosts tint like code hosts — a mark can't paint KaTeX's glyph spacing", () => {
  assert.match(UI, /closest\(".katex"\)/);
  assert.match(CSS, /\.md \.katex\.cmt-hl-host \{ background: color-mix\(in srgb, var\(--cmt-hl\) 30%/);
  assert.match(CSS, /\.md \.katex\.cmt-hl-host mark\.cmt-hl \{ background: transparent/);
});

test("scroll-rail ticks mark the commented spots and jump-open on click", () => {
  assert.match(UI, /function updateCommentRail\(\)/);
  assert.match(UI, /tick\.dataset\.act = "cmtjump"/);
  assert.match(UI, /cmtjump: \(elx\) =>/);
  assert.match(UI, /if \(sid === activeId\) updateCommentRail\(\);/);
  assert.match(CSS, /\.cmt-tick \{/);
  assert.match(CSS, /\.cmt-rail \{ position: fixed;/);
});

test("the tint ladder keeps every state distinct: base < unread < hover", () => {
  const base = CSS.match(/mark\.cmt-hl \{[^}]*var\(--cmt-hl\) (\d+)%/s)![1];
  const unread = CSS.match(/mark\.cmt-hl\.unread \{[^}]*var\(--cmt-hl\) (\d+)%/s)![1];
  const hover = CSS.match(/mark\.cmt-hl:hover \{[^}]*var\(--cmt-hl\) (\d+)%/s)![1];
  assert.ok(Number(base) < Number(unread), "unread must read stronger than read");
  assert.ok(Number(unread) < Number(hover), "hovering an unread mark must still answer");
});

test("comment chrome (badge, popover card) stays on the menu vocabulary", () => {
  assert.match(CSS, /\.cmt-pop \{[^}]*#252526[^}]*\}/s);
  assert.match(CSS, /\.cmt-pop \{[^}]*border-radius: 6px/s);
});
