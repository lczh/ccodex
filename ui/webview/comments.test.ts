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
  // the count badge is GONE (the user 2026-08-17): the highlight + the rail tick do the speaking
  assert.doesNotMatch(UI, /cmt-badge/);
  for (const act of ["cmtclose", "cmtsend", "cmtbreak", "cmtdelete", "cmtopensession"]) {
    assert.ok(UI.includes(`${act}:`), `${act} handler missing from the body delegate`);
    assert.ok(UI.includes(`dataset.act = "${act}"`), `${act} button missing its data-act`);
  }
  // Resolve is GONE (the user 2026-08-17): Delete is the only closer — the handler survives for
  // legacy resolved rows, but no button mints new ones
  assert.ok(!UI.includes('rs.dataset.act = "cmtresolve"'), "no Resolve button remains");
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

test("the ants start on the gesture, and delete is optimistic and cuts the work", () => {
  // create: a synthetic working thread marks the passage before any round-trip; the frame's
  // wholesale list replacement retires it, and a refusal drops it with the warn
  assert.match(UI, /tid: "pending:" \+ create\.uuid, anchorUuid: create\.uuid/);
  assert.match(UI, /state: "working",\s*\n\s*unread: false/);
  assert.match(UI, /t\.tid !== "pending:" \+ pa\.uuid/);
  // reply: the local state flips on send; the kernel's next frame confirms
  assert.match(UI, /cur\.th\.state = "working";\s+\/\/ optimistic/);
  // delete: the highlight goes NOW, and the kernel interrupts the in-flight reply before the kill
  assert.match(UI, /filter\(\(t\) => t\.tid !== cur\.th\.tid\)\);\s*\n\s*applyCommentMarks\(cur\.sid\);\s*\n\s*closeCommentPop\(\);/);
  assert.match(KERNEL, /be\.interrupt\(th\["sid"\]\)[\s\S]{0,200}be\.kill\(th\["sid"\]\)/);
});

test("the popover send acknowledges before any round-trip", () => {
  assert.match(UI, /send\.disabled = true; send\.classList\.add\("busy"\); \}\s+\/\/ ack before the round-trip/);
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
  assert.match(UI, /m\.type === "warn" && pendingCommentAnchor\) \{[\s\S]{0,400}document\.getElementById\("cmt-pop"\)\?\.remove\(\);/);
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

test("Delete is offered on open and resolved threads, never mid-promote", () => {
  // Resolve is gone (the user 2026-08-17) — Delete is the closer for BOTH, still gated off
  // 'promoting'/'promoted' (the kernel refuses those anyway; the button never dangles one)
  assert.match(UI, /if \(th\.status === "open" \|\| th\.status === "resolved"\) \{/);
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

test("the WHOLE popover drags — grip anywhere that isn't a control — and closes on tab switch", () => {
  assert.match(UI, /pop\.addEventListener\("pointerdown"/);
  assert.match(UI, /pop\.setPointerCapture\(ev\.pointerId\)/);
  assert.match(UI, /ev\.clientX > pr\.right - 18 && ev\.clientY > pr\.bottom - 18\) return;/);
  assert.match(CSS, /\.cmt-pop \{[^}]*resize: both/s);
  assert.match(CSS, /\.cmt-pop\.sized \.cmt-quote \{/, "a user resize hands the room to the quoted context");
  assert.match(UI, /commentPopPos = \{ x, y \};/);
  assert.match(UI, /if \(openCommentKey && openCommentKey\.sid !== id\) closeCommentPop\(\);/);
  assert.match(CSS, /\.cmt-pop \{[^}]*cursor: grab/s, "the grab hand covers the whole box now");
});

test("picking a model/effort never reads as an outside press — the box stays put", () => {
  // the dropdowns and the break-out dialog are appended to document.body (fixed position, like the
  // statusline's menus), so the outside-press closer must exempt them: it used to close the popover
  // on mousedown and null pendingCommentAnchor before the item's click could land the pick, and a
  // click on the break-out dialog's Cancel stranded the user the same way (the user 2026-08-18)
  assert.match(UI, /if \(!pop \|\| pop\.contains\(ev\.target as Node\)\) return;\s*\n\s*if \(\(ev\.target as HTMLElement\)\.closest\?\.\("\.meta-menu, #fork-prompt"\)\) return;\s*\n\s*closeCommentPop\(\);/);
  // and the surviving popover shows the pick: the click acks the label, the frame keeps it honest
  assert.match(UI, /function liveMetaLabel\(label: HTMLElement, kind: "model" \| "effort", th: CommentThread\)/);
  assert.match(UI, /\.meta-btn\[data-kind\]/, "the in-place refresh reaches the live chips");
  assert.match(UI, /label\.textContent = c\.label;\s+\/\/ acknowledge the pick now/);
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
  // the name lives IN the header ("New comment: <name>"), the button says Comment, and the picks ride along
  assert.match(UI, /"New comment:"/);
  assert.match(UI, /if \(nameBox\) head\.append\(title, nameBox, closeBtn\);/);
  assert.match(UI, /send\.setAttribute\("aria-label", create \? "Comment" : "Send"\);/);   // the ➤ carries the word
  assert.match(UI, /text, name: nm, model: create\.model \|\| "", effort: create\.effort \|\| "",\s*\n\s*color: create\.color \|\| ""/);
  // the comment's own model/effort selectors reuse the statusline's /models-fed choices + menu skin
  assert.match(UI, /const metaRow = el\("div", "cmt-meta-row"\);/);
  assert.match(UI, /META_CHOICES\[kind\]/);
  assert.match(KERNEL, /model=str\(msg\.get\("model"\) or ""\), effort=str\(msg\.get\("effort"\) or ""\)/);
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

test("ticks and message notches share ONE scrollbar frame, so they can never disagree about order", () => {
  // the user 2026-08-17: scrolling through a history load moved the comment highlights relative to
  // the blue message notches — the ticks were placed by uniform index fraction, a second frame that
  // drifts from the notches' measured-height pixel offsets. Both painters now consume
  // contentOffsetFrame, the one event-index → content-pixel mapping.
  assert.match(UI, /function contentOffsetFrame\(/);
  assert.match(UI, /const off = frame\.offsetOf\(evUnit\[idx\]\);/,
    "ticks place by the shared frame, in UNIT space (anchors are events; the frame speaks units)");
  assert.doesNotMatch(UI, /\(idx \/ n\) \* 100/, "the uniform index-fraction percent frame is gone");
  // the rail repaints with the notches (same rAF), so both always draw from one world
  assert.match(UI, /paintRailSticky\(\); paintScrollMarks\(\); updateCommentRail\(\);/);
  // an unchanged tick set moves IN PLACE — ticks are buttons, and a mid-press rebuild eats the click
  assert.match(UI, /kids\.every\(\(k, i\) => k\.dataset\.tid === ticks\[i\]\.th\.tid\)/);
  assert.match(UI, /kids\[i\]\.style\.top = t\.y \+ "px";/);
});

test("while the thread is WRITING the region wears marching ants; the fill lands with the reply", () => {
  assert.match(UI, /m\.classList\.toggle\("busy", threadBusy\(th\.state\) && th\.status === "open"\)/);
  // a dashed outline whose dashes crawl around the box — four gradient strips, animated offsets,
  // no fill, never a border (it would shift the inline text); solid returns when busy drops
  assert.match(CSS, /mark\.cmt-hl\.busy \{\s*\n\s*background-color: transparent;\s*\n\s*background-image:\s*\n\s*repeating-linear-gradient/);
  // each no-repeat strip is oversized by one 12px dash period along its travel axis and starts a
  // period back, and the keyframe travels exactly that period — a strip sized to its edge slid open
  // a gap that snapped shut every cycle, a visible lurch on text-height vertical edges (2026-08-19)
  assert.match(CSS, /background-size: calc\(100% \+ 12px\) 1\.5px, calc\(100% \+ 12px\) 1\.5px, 1\.5px calc\(100% \+ 12px\), 1\.5px calc\(100% \+ 12px\);/);
  assert.match(CSS, /background-position: -12px 0, 0 100%, 0 0, 100% -12px;/);
  assert.match(CSS, /@keyframes cmt-ants \{\s*\n\s*to \{ background-position: 0 0, -12px 100%, 0 -12px, 100% 0; \}/);
  assert.match(CSS, /code\.cmt-hl-host:has\(mark\.cmt-hl\.busy\)/, "hosts march too, or their tint defeats the cue");
  // both ants blocks (mark + host) carry the oversize — a lone fixed block leaves the other lurching
  assert.strictEqual((CSS.match(/background-size: calc\(100% \+ 12px\) 1\.5px/g) || []).length, 2);
  assert.match(KERNEL, /state = be\.session_state\(tsid\)/);
});

test("the popover renders the thread with the CHAT's own renderer from the branch point", () => {
  assert.match(UI, /renderingSid = th\.tid;/);
  assert.match(UI, /list\.appendChild\(renderEvent\(ev, prev, null\)\);/);
  assert.match(KERNEL, /def _thread_events\(tsid, cut_uuid, now, tmux\):/);
  assert.match(KERNEL, /evs = evs\[at \+ 1:\]/, "sliced to AFTER the branch point — the head system card never rides");
  // the thread's own live model/effort chips post the chat's own ops, keyed to the thread sid
  assert.match(UI, /type: kind === "model" \? "setModel" : "setEffort", id: th\.tid, value: c\.value/);
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
