// The webview's ONE shared views writer (the 2026-08-26 audit's Finding A): the chat strip
// (render.ts postViews) and the Outline's tag lens (fleet.ts) posted whole setTimelineViews blobs
// cloned from the payload WITHOUT advancing an optimistic revision and never consumed viewsAck —
// two quick gestures both carried rev N and the kernel's CAS always refused the second (a rapid
// hide-then-reveal, or two lens picks, silently lost the later edit). views-writer.ts is the
// timeline panel's _nextViewsRev/viewsAck discipline as the webview's shared module; both call
// sites route through it. The counter is confirmed-rev + in-flight since the r47 verification:
// a single monotone counter forged past its own refused writes (a stale blob could be ACCEPTED
// at a base that coincided with a foreign commit, silently erasing it), and unconditional
// re-anchoring rewound it below writes still in flight (resurrecting the second-gesture 409 on
// federation's stale re-emits). Executed tests on the module + source pins on the wiring.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { anchorViewsRev, consumeViewsAck, postViewsWrite, resetViewsWriterForTest } from "../../ui/webview/views-writer";

const p = (f: string) => path.resolve(process.cwd(), "..", "ui", "webview", f);
const RENDER = fs.readFileSync(p("render.ts"), "utf8");
const FLEET = fs.readFileSync(p("fleet.ts"), "utf8");

test("executed: two rapid writes carry ASCENDING baseRevs — the second no longer echoes the first's base", () => {
  resetViewsWriterForTest();
  const posts: any[] = [];
  const post = (m: any) => posts.push(m);
  // both gestures clone the SAME pushed payload (rev 4) — the exact double-refusal shape: the raw
  // posts both declared base 4 and the kernel 409'd the second every time
  anchorViewsRev({ rev: 4 });
  postViewsWrite(post, { active: "all", tags: [], rev: 4 } as any);
  postViewsWrite(post, { active: "all", tags: [], rev: 4 } as any);
  assert.equal(posts[0].type, "setTimelineViews");
  assert.equal(posts[0].views.baseRev, 4, "the first write declares the payload rev");
  assert.equal(posts[1].views.baseRev, 5, "the second advances past the in-flight first");
  assert.ok(!("rev" in posts[0].views) && !("rev" in posts[1].views),
    "the stale payload rev never rides a write — baseRev is the declared base");
  assert.equal((posts[0].views as any).active, "all", "the blob itself is untouched otherwise");
});

test("executed: a viewsAck re-anchors the counter — accepted and refused alike; a refusal fires the surface's rollback", () => {
  resetViewsWriterForTest();
  const posts: any[] = [];
  const post = (m: any) => posts.push(m);
  assert.equal(consumeViewsAck({ type: "viewsAck", ok: true, rev: 9 }), true, "consumed");
  postViewsWrite(post, { active: "all", tags: [] } as any);
  assert.equal(posts[0].views.baseRev, 9, "the acked rev is the next write's base");
  let refused = 0;
  assert.equal(consumeViewsAck({ type: "viewsAck", ok: false, rev: 2 }, () => refused++), true);
  assert.equal(refused, 1, "ok:false hands the refusal to the surface (drop the known-refused overlay NOW)");
  postViewsWrite(post, { active: "all", tags: [] } as any);
  assert.equal(posts[1].views.baseRev, 2, "the refusal's SERVED rev re-anchors, downward included — the CAS truth");
  assert.equal(consumeViewsAck({ type: "viewsAck", ok: true }), true);
  postViewsWrite(post, { active: "all", tags: [] } as any);
  assert.equal(posts[2].views.baseRev, 2,
    "a malformed rev leaves the anchor standing (the ack still retires its write's slot) — "
    + "never NaN, and never the old rewind to 0");
  // foreign frames (and junk) pass by unconsumed, so the routers' other cases still run
  assert.equal(consumeViewsAck({ type: "data" }), false);
  assert.equal(consumeViewsAck(null), false);
});

test("executed: payload anchors are MONOTONIC — a stale re-emitted payload never rewinds the base (r47)", () => {
  // the old rule ("the kernel's counter is the truth, even downward") let federation's cached
  // re-emits — the localViews blob with its OLD rev rides every remote tabOrder arrival and every
  // cross-pane drag storage event — rewind the counter under an in-flight write, so the next
  // gesture reused that write's base and self-409'd: the exact bug class the module exists to end
  resetViewsWriterForTest();
  const posts: any[] = [];
  anchorViewsRev({ rev: 50 });
  anchorViewsRev({ rev: 3 });                       // a stale re-emit, not fresher truth
  postViewsWrite((m) => posts.push(m), { active: "all", tags: [] } as any);
  assert.equal(posts[0].views.baseRev, 50, "the highest kernel-reported rev stands");
  anchorViewsRev(null);                             // a tabOrder frame without the blob
  anchorViewsRev({ rev: "junk" } as any);           // a malformed rev
  postViewsWrite((m) => posts.push(m), { active: "all", tags: [] } as any);
  assert.equal(posts[1].views.baseRev, 51,
    "non-numeric anchors change nothing — confirmed 50 + the one write in flight");
});

test("executed: an ack or stale payload never rewinds the base below a write still in flight (r47)", () => {
  // the finding's exact shape: G1 (base 4) accepted, its ack anchors 5 — then a payload built
  // BEFORE G1 committed (rev 4) arrives and the old unconditional re-anchor rewound the counter
  // to 4, so G2 declared base 4 against a store at 5: refused, the second same-client gesture
  // lost — the very bug the module advertises as fixed
  resetViewsWriterForTest();
  const posts: any[] = [];
  const post = (m: any) => posts.push(m);
  anchorViewsRev({ rev: 4 });
  postViewsWrite(post, { active: "all", tags: [], rev: 4 } as any);   // G1
  assert.equal(posts[0].views.baseRev, 4);
  assert.equal(consumeViewsAck({ type: "viewsAck", ok: true, rev: 5 }), true, "G1 accepted");
  anchorViewsRev({ rev: 4 });                                         // the pre-commit payload lands late
  postViewsWrite(post, { active: "all", tags: [], rev: 4 } as any);   // G2, cloned from that payload
  assert.equal(posts[1].views.baseRev, 5, "G2 declares the ACKED rev — never the stale payload's");
  // and mid-flight: a stale re-emit between two quick gestures must not hand G4 G3's own base
  postViewsWrite(post, { active: "all", tags: [], rev: 4 } as any);   // G3: base 5 + 1 in flight = 6
  anchorViewsRev({ rev: 4 });                                         // stale re-emit while G3 flies
  postViewsWrite(post, { active: "all", tags: [], rev: 4 } as any);   // G4
  assert.equal(posts[2].views.baseRev, 6);
  assert.equal(posts[3].views.baseRev, 7, "confirmed 5 + two writes in flight — no rewind, no self-409");
});

test("executed: a refused in-flight write is never forged past — and a refusal clears the guessed headroom (r47)", () => {
  // the P2 erasure: with the store at 4, W1 flies (base 4); a FOREIGN client commits (the push
  // lands, rev 5 — W1 is now doomed to refusal); the old single counter stamped the next write
  // max(counter, blob)+1 = base 5, which COINCIDES with the rev the foreign write produced — so
  // the kernel ACCEPTED a blob that never saw the foreign edit and silently erased it. The base
  // must be confirmed + writes actually in flight: past the coincidence, into an honest refusal.
  resetViewsWriterForTest();
  const posts: any[] = [];
  const post = (m: any) => posts.push(m);
  anchorViewsRev({ rev: 4 });
  postViewsWrite(post, { active: "all", tags: [], rev: 4 } as any);   // W1 in flight, base 4
  anchorViewsRev({ rev: 5 });                                         // a foreign commit's push
  postViewsWrite(post, { active: "all", tags: [], rev: 4 } as any);   // W2, still built from rev-4 state
  assert.equal(posts[1].views.baseRev, 6,
    "W2 counts W1 as in flight ABOVE the foreign rev — never base 5, the stale-acceptance coincidence");
  // and once a refusal arrives, the slots minted after the refused write were guesses that assumed
  // it would land: the NEXT write re-anchors to the SERVED rev exactly, reusing none of them
  assert.equal(consumeViewsAck({ type: "viewsAck", ok: false, rev: 5 }, () => {}), true, "W1 refused");
  postViewsWrite(post, { active: "all", tags: [], rev: 4 } as any);   // W3
  assert.equal(posts[2].views.baseRev, 5, "the served rev, no forged headroom — W2's fate is the kernel's to answer");
});

test("wiring: the chat routes ALL views writes through the shared writer and consumes viewsAck", () => {
  // the one writer (postViews) stamps through postViewsWrite; no raw blob post survives
  assert.match(RENDER, /function postViews\(v: SessionViews\) \{[\s\S]{0,400}?postViewsWrite\(\(m\) => vscodeApi\.postMessage\(m\), v\);/);
  assert.doesNotMatch(RENDER, /postMessage\(\{ type: "setTimelineViews"/, "no raw setTimelineViews post in the chat");
  // the frame router consumes the ack — a refusal drops the known-refused optimistic overlay now
  assert.match(RENDER, /else if \(m\.type === "viewsAck"\) consumeViewsAck\(m, \(\) => \{\s*\n\s*pendingSessionViews = null; pendingViewsAge = 0;/);
  // …and every tabOrder payload re-anchors (captureViews is the views-arrival path)
  assert.match(RENDER, /function captureViews\(v: SessionViews \| null\) \{\s*\n\s*if \(v\) sessionViews = v;\s*\n\s*anchorViewsRev\(v\);/);
});

test("wiring: the chat's viewsAck refusal re-derives the ACTIVE session's peek after dropping the overlay (r47)", () => {
  // The refusal rollback is the THIRD path that changes effViews() — captureViews and postViews
  // both re-run assertPeekFor after touching the blob, and without it here the lens edit that had
  // revealed the active session (peekId cleared) snapped back to hiding it with peekId still null:
  // the ACTIVE tab vanished from the strip. And because the refused write changed nothing
  // kernel-side, the dedup withheld the healing tabOrder frame — the hole persisted until some
  // unrelated push, unbounded on a quiet dashboard.
  assert.match(RENDER, new RegExp(
    'else if \\(m\\.type === "viewsAck"\\) consumeViewsAck\\(m, \\(\\) => \\{\\s*\\n'
    + '\\s*pendingSessionViews = null; pendingViewsAge = 0;\\s*\\n'
    + '\\s*if \\(activeId\\) assertPeekFor\\(activeId\\);\\s*\\n'
    + '\\s*renderTabs\\(\\);\\s*\\n\\s*\\}\\);'));
});

test("wiring: the Outline (fleet) routes BOTH its lens writers through the shared writer and consumes viewsAck", () => {
  const writes = FLEET.match(/postViewsWrite\(\(msg\) => vscodeApi\.postMessage\(msg\), v\);/g) || [];
  assert.equal(writes.length, 2, "the tag-menu apply AND the chip-sync unpick both stamp through the writer");
  assert.doesNotMatch(FLEET, /postMessage\(\{ type: "setTimelineViews"/, "no raw setTimelineViews post in the fleet");
  assert.match(FLEET, /if \(consumeViewsAck\(m\)\) return;/, "the feed-only router consumes the ack before its guard");
  assert.match(FLEET, /fleetViews = m\.views as SessionViews; anchorViewsRev\(fleetViews\);/,
    "every feed payload's views re-anchors");
});
