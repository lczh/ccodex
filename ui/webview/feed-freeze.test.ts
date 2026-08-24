// HOVER-FREEZE (the user 2026-08-24): a feed card under the pointer must never move on screen.
// While a card is hovered, incoming feed payloads QUEUE (newest wins) instead of rendering; the
// deferred churn hints as +N/-N beside the column pills and (grouped) the session headers; the
// hovered card's mouseleave flushes everything at once, window blur is the backstop — no timers.
// The counting rule EXECUTES here (feed-freeze.ts is pure); the feed wiring is source-pinned
// (feed.ts has no jsdom harness — the repo convention).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { freezeDiff } from "./feed-freeze";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

const it = (id: string, col: string, sid = "s1") => ({ id, col, sid });

test("freezeDiff counts arrivals and departures per column and per session", () => {
  const d = freezeDiff(
    [it("a", "asks"), it("b", "completed"), it("c", "needsInput", "s2")],
    [it("a", "asks"), it("d", "completed"), it("e", "completed", "s2")],
  );
  assert.deepEqual(d.cols.completed, { add: 2, del: 1 });
  assert.deepEqual(d.cols.needsInput, { add: 0, del: 1 });
  assert.equal(d.cols.asks, undefined, "an unmoved card counts nothing");
  assert.deepEqual(d.sess.s1, { add: 1, del: 1 });
  assert.deepEqual(d.sess.s2, { add: 1, del: 1 });
  assert.ok(d.any);
});

test("a column move is one departure + one arrival — it leaves one header and joins another", () => {
  const d = freezeDiff([it("a", "asks")], [it("a", "completed")]);
  assert.deepEqual(d.cols.asks, { add: 0, del: 1 });
  assert.deepEqual(d.cols.completed, { add: 1, del: 0 });
  assert.deepEqual(d.sess.s1, { add: 1, del: 1 }, "grouped mode groups per column, so the run moves too");
});

test("identical views count nothing — in-place content edits are not movement", () => {
  const d = freezeDiff([it("a", "asks"), it("b", "completed")], [it("a", "asks"), it("b", "completed")]);
  assert.equal(d.any, false);
  assert.deepEqual(d.cols, {});
  assert.deepEqual(d.sess, {});
});

test("the feed payload path defers while a card is hovered — and ONLY the payload path", () => {
  // the message handler's feed branch gates on the freeze; the newest payload supersedes by overwrite
  assert.match(FEED, /if \(freezeKey \|\| tabScopeKey\) \{ pendingFeedPayload = m; paintFreezeBadges\(\); return; \}\s*\n\s*applyFeedPayload\(m\);/,
    "the keyboard scope holds the same gate (the user 2026-08-24) — a card being keyed cannot move either");
  const queues = FEED.match(/pendingFeedPayload = m;/g) || [];
  assert.equal(queues.length, 1, "one queue write, in the payload path — local gestures and render() are never gated");
  assert.doesNotMatch(FEED, /function render\(\) \{\s*\n[^\n]*freezeKey/, "render is not gated — the hovered card's controls stay live");
  // one body, two callers: live application and the flush
  assert.match(FEED, /function applyFeedPayload\(m: any\): void \{/);
  assert.match(FEED, /if \(m\) applyFeedPayload\(m\);/, "flush applies the newest queued payload");
});

test("flush is event-based: card mouseleave, window blur backstop — no timers anywhere in the freeze", () => {
  assert.match(FEED, /function freezeLeave\(key: string\): void \{\s*\n\s*if \(tabScopeKey === key\) releaseTabScope\(\);[^\n]*\n\s*if \(freezeKey !== key\) return;\s*\n\s*freezeKey = null;\s*\n\s*flushFreeze\(\);/);
  assert.match(FEED, /window\.addEventListener\("blur", \(\) => \{ releaseTabScope\(\); freezeKey = null; flushFreeze\(\); \}\);/,
    "blur releases BOTH gate holders — the keyboard scope has no pointer to leave with");
  const block = FEED.slice(FEED.indexOf("// ── HOVER-FREEZE"), FEED.indexOf("function applyFeedPayload"));
  assert.ok(!/setTimeout|setInterval/.test(block), "no timers — mouseleave and blur are the flush events");
  // the flush is a MICROTASK — same gesture, after its handlers finish (found in review 2026-08-24:
  // the clear path's synthetic mouseleave used to re-render the board under the rest of its own
  // click handler, before pendingCleared.add ran). Ordering, not a time window.
  assert.match(FEED, /queueMicrotask\(\(\) => \{/);
  assert.match(FEED, /if \(freezeKey\) return; \/\/ re-armed before the tick ended → keep the queue for the next leave/);
});

test("a local render that detaches or re-keys the hovered element heals the freeze (:hover truth)", () => {
  // found in review 2026-08-24: a removed element never fires mouseleave — typing in search can
  // filter the hovered card out, and toggling Group swaps it for a group card in place with no
  // enter/leave events. The render tail checks live pointer truth per render — event-based.
  assert.match(FEED, /const hov = document\.querySelector<HTMLElement>\("\.feed-cols \.fitem:hover"\);/);
  assert.match(FEED, /if \(!hov\) \{ freezeKey = null; flushFreeze\(\); \}/);
  assert.match(FEED, /else \{ const k = kbHoverId\(hov\); if \(k && k !== freezeKey\) freezeKey = k; \}/,
    "a re-keyed card under a stationary pointer re-arms to the element actually hovered");
});

test("the badge hint mirrors the optimistic-restore overlay — a card the flush restores is no departure", () => {
  // found in review 2026-08-24: the displayed board holds a pendingRestored card the queued payload
  // lacks; without the mirror the badge promised a -1 that the flush immediately took back
  const pv = FEED.slice(FEED.indexOf("function payloadView"), FEED.indexOf("function paintFreezeParts"));
  assert.ok(pv.includes(
    "for (const it of pendingRestored.values()) if (!present.has(it.itemId) && !inIncoming.has(it.itemId)) out.push(it);"),
    "exact mirror: an entry the payload itself carries is the flush's to drop, not the hint's to re-add");
  assert.ok(!pv.includes("pendingRestored.delete"), "read-only — the flush re-runs the real bookkeeping");
});

test("both card shapes arm the freeze on the same events the hover highlight rides", () => {
  assert.match(FEED, /freezeEnter\(it\.itemId\);\s*[^\n]*\n\s*if \(it\.provisional\) return;/, "ask cards enter before the provisional bail");
  assert.match(FEED, /freezeLeave\(it\.itemId\);/, "ask cards flush on leave");
  assert.match(FEED, /freezeEnter\(fkey\);/, "group cards enter");
  assert.match(FEED, /freezeLeave\(fkey\);/, "group cards flush on leave");
  // a card CLEARED under the pointer flushes via its synthetic mouseleave — the existing dispatch
  // (feed.ts's clear paths) runs the exact leave logic, freezeLeave included
  assert.match(FEED, /card\.dispatchEvent\(new MouseEvent\("mouseleave"\)\);/);
});

test("the badge hint counts the USER'S view and never mutates state computing it", () => {
  // render and the painter share one view filter, so the hint counts exactly what would move
  assert.match(FEED, /function viewFiltered\(list: AskItem\[\]\): AskItem\[\]/);
  assert.match(FEED, /let shown = viewFiltered\(asks\);/);
  assert.match(FEED, /const d = freezeDiff\(toItems\(asks\), toItems\(payloadView\(pendingFeedPayload\)\)\);/);
  // payloadView reads pendingCleared but must not write it (the flush re-runs the real bookkeeping)
  const pv = FEED.slice(FEED.indexOf("function payloadView"), FEED.indexOf("function paintFreezeParts"));
  assert.ok(!pv.includes("pendingCleared.delete") && !pv.includes("pruneViewStateTo"),
    "the hint path is side-effect free");
});

test("badges wear the header conventions: accent adds, block-red removes, the count's own scale", () => {
  assert.match(CSS, /\.freeze-badge \{ font-weight: 600; opacity: 0\.7; margin-left: 6px; white-space: nowrap; \}/);
  assert.match(CSS, /\.freeze-badge \.fz-add \{ color: var\(--accent\); \}/, "never a re-hardcoded accent hex");
  assert.match(CSS, /\.freeze-badge \.fz-del \{ color: var\(--err\); \}/, "the board's existing block red");
  assert.doesNotMatch(CSS, /\.freeze-badge[^}]*font-size/, "no new font sizes — the badge inherits the head's scale");
  // painted on the build-once column heads and the data-fsid-stamped session headers; cleared when quiet
  assert.match(FEED, /put\(document\.querySelector\("\.feed-col\.col-" \+ key \+ " \.feed-col-head"\), d\.cols\[key\]\);/);
  assert.match(FEED, /h\.setAttribute\("data-fsid", e\.sid\);/);
  assert.match(FEED, /put\(h, groupedNow \? d\.sess\[h\.getAttribute\("data-fsid"\) \|\| ""\] : undefined\);/);
  assert.match(FEED, /document\.querySelectorAll\("\.freeze-badge"\)\.forEach\(\(n\) => n\.remove\(\)\);/,
    "nothing pending → every badge comes off");
  // local renders while frozen re-sync the hints, so a rebuilt board never strands a stale count
  assert.match(FEED, /paintFreezeBadges\(\);   \/\/ hover-freeze: local renders while frozen re-sync/);
});
