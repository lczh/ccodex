// The webview's ONE shared views writer (the 2026-08-26 audit's Finding A): the chat strip
// (render.ts postViews) and the Outline's tag lens (fleet.ts) posted whole setTimelineViews blobs
// cloned from the payload WITHOUT advancing an optimistic revision and never consumed viewsAck —
// two quick gestures both carried rev N and the kernel's CAS always refused the second (a rapid
// hide-then-reveal, or two lens picks, silently lost the later edit).
//
// SERIALIZED since the v1.3.20 audit: the r47 two-number counter still PIPELINED blob writes on
// guessed revisions — W1 in flight, a foreign commit lands, W1 is refused but stale W2's guessed
// base could COINCIDE with the foreign commit's rev and the kernel accepted a blob that never saw
// the foreign edit, silently erasing it. Now at most ONE write is outstanding, a blob's CAS base
// is stamped at POST time from the last KERNEL-REPORTED rev, a refusal drops every queued blob,
// and the gestures expressible as TARGETED ops (the same audit's grammar extension) carry no base
// at all. Executed tests on the module + source pins on the wiring.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { anchorViewsRev, consumeViewsAck, notifyViewsTransportReset, postViewsOps, postViewsWrite,
         resetViewsWriterForTest } from "../../ui/webview/views-writer";

const p = (f: string) => path.resolve(process.cwd(), "..", "ui", "webview", f);
const RENDER = fs.readFileSync(p("render.ts"), "utf8");
const FLEET = fs.readFileSync(p("fleet.ts"), "utf8");

test("executed: whole-blob writes are SERIALIZED — the second queues until the first's ack, then posts on the served rev", () => {
  // the v1.3.20 audit: pipelined writes carried guessed revisions; serialization ends the guess
  resetViewsWriterForTest();
  const posts: any[] = [];
  const post = (m: any) => posts.push(m);
  anchorViewsRev({ rev: 4 });
  postViewsWrite(post, { active: "all", tags: [], rev: 4 } as any);
  postViewsWrite(post, { active: "all", tags: [], rev: 4 } as any);
  assert.equal(posts.length, 1, "one write outstanding — the second QUEUES, never pipelines");
  assert.equal(posts[0].type, "setTimelineViews");
  assert.equal(posts[0].views.baseRev, 4, "the first write declares the served rev");
  assert.ok(!("rev" in posts[0].views), "the stale payload rev never rides a write");
  assert.equal(consumeViewsAck({ type: "viewsAck", ok: true, rev: 5 }), true);
  assert.equal(posts.length, 2, "the ack releases the queued write");
  assert.equal(posts[1].views.baseRev, 5,
    "…stamped at POST time with the ACKED rev — the served truth, never a gesture-time guess");
});

test("executed: a foreign commit mid-flight can never be erased — the queued stale blob DROPS on the refusal", () => {
  // the audited erase, end-to-end: store at 4, W1 flies (base 4); a FOREIGN client commits
  // (push rev 5 — W1 is doomed); W2, still rendered from rev-4 state, used to post base 6 and,
  // after W1's refusal re-anchored to 5, a retry could coincide with the foreign rev and be
  // ACCEPTED — a blob that never saw the foreign edit erased it. Serialized: W2 never posts.
  resetViewsWriterForTest();
  const posts: any[] = [];
  const post = (m: any) => posts.push(m);
  anchorViewsRev({ rev: 4 });
  postViewsWrite(post, { active: "all", tags: [], rev: 4 } as any);   // W1 in flight, base 4
  anchorViewsRev({ rev: 5 });                                         // the foreign commit's push
  postViewsWrite(post, { active: "all", tags: [], rev: 4 } as any);   // W2: rendered on rev-4 state
  assert.equal(posts.length, 1, "W2 queues behind W1 — nothing is pipelined on a guess");
  let refused = 0;
  assert.equal(consumeViewsAck({ type: "viewsAck", ok: false, rev: 5 }, () => refused++), true);
  assert.equal(refused, 1, "ok:false hands the refusal to the surface (drop the overlay NOW)");
  assert.equal(posts.length, 1,
    "the queued W2 was rendered on state the kernel just refuted — DROPPED, never posted "
    + "(the v1.3.20 audit's coincide-erase)");
  postViewsWrite(post, { active: "all", tags: [], rev: 4 } as any);   // a FRESH gesture after the refusal
  assert.equal(posts[1].views.baseRev, 5, "…declares the refusal's served rev exactly");
});

test("executed: targeted ops ride the queue for order but SURVIVE a blob refusal — they compose, nothing to refute", () => {
  resetViewsWriterForTest();
  const posts: any[] = [];
  const post = (m: any) => posts.push(m);
  anchorViewsRev({ rev: 4 });
  postViewsWrite(post, { active: "all", tags: [], rev: 4 } as any);   // the blob flies
  postViewsOps(post, [{ actives: { chat: { all: true } } }]);         // a lens pick queues behind it
  assert.equal(posts.length, 1);
  assert.equal(consumeViewsAck({ type: "viewsAck", ok: false, rev: 7 }, () => {}), true);
  assert.equal(posts.length, 2, "the refusal drops queued BLOBS only — the op still posts");
  assert.equal(posts[1].type, "setTimelineViewsOps");
  assert.deepEqual(posts[1].ops, [{ actives: { chat: { all: true } } }]);
  assert.ok(!("baseRev" in posts[1]), "ops carry no CAS base — they compose server-side");
  assert.equal(consumeViewsAck({ type: "viewsAck", ok: true, rev: 8 }), true, "the op's ack retires it");
  postViewsWrite(post, { active: "all", tags: [] } as any);
  assert.equal(posts[2].views.baseRev, 8, "…and re-anchored the counter for the next blob");
});

test("executed: a RAISED payload anchor releases a wedged OPS queue — but never a queued blob (r48)", () => {
  // the r48 verification: a dropped ack frame left the one outstanding slot occupied forever —
  // every later gesture queued behind it and none ever posted. The kernel demonstrably moving
  // PAST our write (a payload with a HIGHER rev) releases the slot — but ONLY when nothing
  // queued is a blob: a queued blob was rendered under the optimistic overlay, without the
  // foreign edit that raised the rev, and releasing it would be the coincide-erase itself.
  resetViewsWriterForTest();
  const posts: any[] = [];
  const post = (m: any) => posts.push(m);
  anchorViewsRev({ rev: 4 });
  postViewsOps(post, [{ actives: { outline: { none: true } } }]);     // O1 flies… and its ack is lost
  postViewsOps(post, [{ actives: { chat: { all: true } } }]);         // a second lens pick queues
  assert.equal(posts.length, 1);
  anchorViewsRev({ rev: 4 });                                         // same rev: NOT evidence — still wedged
  assert.equal(posts.length, 1, "an equal-rev payload releases nothing");
  anchorViewsRev({ rev: 5 });                                         // the kernel moved past O1
  assert.equal(posts.length, 2, "the raised payload releases the wedge");
  assert.equal(posts[1].type, "setTimelineViewsOps");
  assert.deepEqual(posts[1].ops, [{ actives: { outline: { none: true } } }],
    "the UNACKED head itself re-posts (r49 keep-until-ack: its effect may never have applied; "
    + "ops are idempotent, so the replay is safe) — the queue drains in order behind it");
  // …but a queued BLOB holds the wedge instead of risking the erase
  resetViewsWriterForTest();
  const posts2: any[] = [];
  anchorViewsRev({ rev: 4 });
  postViewsWrite((m) => posts2.push(m), { active: "all", tags: [], rev: 4 } as any);
  postViewsWrite((m) => posts2.push(m), { active: "all", tags: [], rev: 4 } as any);
  anchorViewsRev({ rev: 5 });
  assert.equal(posts2.length, 1, "a stale-rendered queued blob is never released by the raise");
});

test("executed: a transport reconnect REPLAYS the unacked head — a send lost on the dead socket survives (r49)", () => {
  // the v1.3.21 audit's P2.8: a send accepted by the browser on a socket that then died was
  // simply gone — the slot waited forever and no reconnect replayed it. The head stays queued
  // until its ack; the reconnect resets the slot and re-posts it.
  resetViewsWriterForTest();
  const posts: any[] = [];
  const post = (m: any) => posts.push(m);
  anchorViewsRev({ rev: 4 });
  postViewsOps(post, [{ actives: { chat: { all: true } } }]);   // sent… and lost with the socket
  assert.equal(posts.length, 1);
  notifyViewsTransportReset();                                  // the fresh socket's open event
  assert.equal(posts.length, 2, "the reconnect re-posts the unacked head");
  assert.deepEqual(posts[1].ops, posts[0].ops, "…the SAME write (idempotent ops, CAS'd blobs)");
  assert.equal(consumeViewsAck({ type: "viewsAck", ok: true, rev: 5 }), true);
  postViewsWrite(post, { active: "all", tags: [] } as any);
  assert.equal(posts[2].views.baseRev, 5, "the queue drains normally after the replayed ack");
});

test("executed: a viewsAck re-anchors the counter — accepted and refused alike; malformed revs change nothing", () => {
  resetViewsWriterForTest();
  const posts: any[] = [];
  const post = (m: any) => posts.push(m);
  assert.equal(consumeViewsAck({ type: "viewsAck", ok: true, rev: 9 }), true, "consumed");
  postViewsWrite(post, { active: "all", tags: [] } as any);
  assert.equal(posts[0].views.baseRev, 9, "the acked rev is the next write's base");
  assert.equal(consumeViewsAck({ type: "viewsAck", ok: false, rev: 2 }, () => {}), true);
  postViewsWrite(post, { active: "all", tags: [] } as any);
  assert.equal(posts[1].views.baseRev, 2, "the refusal's SERVED rev re-anchors, downward included — the CAS truth");
  assert.equal(consumeViewsAck({ type: "viewsAck", ok: true }), true);
  postViewsWrite(post, { active: "all", tags: [] } as any);
  assert.equal(posts[2].views.baseRev, 2,
    "a malformed rev leaves the anchor standing (the ack still retires its write) — "
    + "never NaN, and never the old rewind to 0");
  // foreign frames (and junk) pass by unconsumed, so the routers' other cases still run
  assert.equal(consumeViewsAck({ type: "data" }), false);
  assert.equal(consumeViewsAck(null), false);
});

test("executed: payload anchors are MONOTONIC — a stale re-emitted payload never rewinds the base (r47)", () => {
  // the old rule ("the kernel's counter is the truth, even downward") let federation's cached
  // re-emits rewind the counter, so the next gesture reused a spent base and self-409'd
  resetViewsWriterForTest();
  const posts: any[] = [];
  const post = (m: any) => posts.push(m);
  anchorViewsRev({ rev: 50 });
  anchorViewsRev({ rev: 3 });                       // a stale re-emit, not fresher truth
  postViewsWrite(post, { active: "all", tags: [] } as any);
  assert.equal(posts[0].views.baseRev, 50, "the highest kernel-reported rev stands");
  anchorViewsRev(null);                             // a tabOrder frame without the blob
  anchorViewsRev({ rev: "junk" } as any);           // a malformed rev
  assert.equal(consumeViewsAck({ type: "viewsAck", ok: true, rev: 51 }), true);
  postViewsWrite(post, { active: "all", tags: [] } as any);
  assert.equal(posts[1].views.baseRev, 51, "non-numeric anchors change nothing");
  // and mid-flight: a stale re-emit while a write is outstanding must not rewind what the
  // NEXT (queued) write will declare
  postViewsWrite(post, { active: "all", tags: [], rev: 4 } as any);   // queues behind posts[1]
  anchorViewsRev({ rev: 4 });                                         // stale re-emit mid-flight
  assert.equal(consumeViewsAck({ type: "viewsAck", ok: true, rev: 52 }), true);
  assert.equal(posts[2].views.baseRev, 52, "the queued write declares the acked rev — no rewind, no self-409");
});

test("wiring: the chat routes views writes through the shared writer — ops for expressible gestures, blob as the belt", () => {
  assert.match(RENDER, /function postViews\(v: SessionViews, ops\?: Record<string, unknown>\[\]\) \{/);
  assert.match(RENDER, /if \(ops && ops\.length\) postViewsOps\(\(m\) => vscodeApi\.postMessage\(m\), ops\);/);
  assert.match(RENDER, /else postViewsWrite\(\(m\) => vscodeApi\.postMessage\(m\), v\);/);
  assert.doesNotMatch(RENDER, /postMessage\(\{ type: "setTimelineViews"/, "no raw setTimelineViews post in the chat");
  // the lens picks, the reveal, the membership edits and the create all post TARGETED ops
  // (the v1.3.20 audit) — count the call sites that pass them
  const opsCalls = RENDER.match(/postViews\([^)]*, \[\{ (actives|create): /g) || [];
  assert.ok(opsCalls.length >= 3, "the lens/create gestures pass ops: " + opsCalls.length);
  assert.match(RENDER, /postViews\(nv, localOps\.length \? localOps : undefined\);/,
    "the membership editor passes its collected ops");
  assert.match(RENDER, /postViews\(v, v\.actives && v\.actives\["chat"\] \? \[\{ actives: \{ chat: v\.actives\["chat"\] \} \}\] : undefined\);/,
    "the reveal posts ONLY the chat surface it moved (r48)");
  // the frame router consumes the ack — a refusal drops the known-refused optimistic overlay now
  assert.match(RENDER, /else if \(m\.type === "viewsAck"\) consumeViewsAck\(m, \(\) => \{\s*\n\s*pendingSessionViews = null; pendingViewsAge = 0;/);
  // …and every tabOrder payload re-anchors (captureViews is the views-arrival path)
  assert.match(RENDER, /function captureViews\(v: SessionViews \| null\) \{\s*\n\s*if \(v\) sessionViews = v;\s*\n\s*anchorViewsRev\(v\);/);
});

test("wiring: the chat's viewsAck refusal re-derives the ACTIVE session's peek after dropping the overlay (r47)", () => {
  assert.match(RENDER, new RegExp(
    'else if \\(m\\.type === "viewsAck"\\) consumeViewsAck\\(m, \\(\\) => \\{\\s*\\n'
    + '\\s*pendingSessionViews = null; pendingViewsAge = 0;\\s*\\n'
    + '\\s*if \\(activeId\\) assertPeekFor\\(activeId\\);\\s*\\n'
    + '\\s*renderTabs\\(\\);\\s*\\n\\s*\\}\\);'));
});

test("wiring: the Outline (fleet) posts its lens picks as TARGETED ops and consumes viewsAck", () => {
  const writes = FLEET.match(/postViewsOps\(\(msg\) => vscodeApi\.postMessage\(msg\), \[\{ actives: \{ outline: l \} \}\]\);/g) || [];
  assert.equal(writes.length, 2, "the tag-menu apply AND the chip-sync unpick both post ONLY their "
    + "surface's lens (r48: a whole-dict post overwrote other panes' concurrent picks)");
  assert.doesNotMatch(FLEET, /postMessage\(\{ type: "setTimelineViews"/, "no raw setTimelineViews post in the fleet");
  assert.doesNotMatch(FLEET, /postViewsWrite/, "no whole-blob CAS write remains in the fleet");
  assert.match(FLEET, /if \(consumeViewsAck\(m\)\) return;/, "the feed-only router consumes the ack before its guard");
  assert.match(FLEET, /fleetViews = m\.views as SessionViews; anchorViewsRev\(fleetViews\);/,
    "every feed payload's views re-anchors");
});
