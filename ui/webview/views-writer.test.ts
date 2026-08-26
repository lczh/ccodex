// The webview's ONE shared views writer (the 2026-08-26 audit's Finding A): the chat strip
// (render.ts postViews) and the Outline's tag lens (fleet.ts) posted whole setTimelineViews blobs
// cloned from the payload WITHOUT advancing an optimistic revision and never consumed viewsAck —
// two quick gestures both carried rev N and the kernel's CAS always refused the second (a rapid
// hide-then-reveal, or two lens picks, silently lost the later edit). views-writer.ts is the
// timeline panel's _nextViewsRev/viewsAck discipline as the webview's shared module; both call
// sites route through it. Executed tests on the module + source pins on the wiring.
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
  assert.equal(posts[1].views.baseRev, 2, "the refusal's rev re-anchors too — no drift");
  assert.equal(consumeViewsAck({ type: "viewsAck", ok: true }), true);
  postViewsWrite(post, { active: "all", tags: [] } as any);
  assert.equal(posts[2].views.baseRev, 0, "a malformed rev anchors at 0, never NaN");
  // foreign frames (and junk) pass by unconsumed, so the routers' other cases still run
  assert.equal(consumeViewsAck({ type: "data" }), false);
  assert.equal(consumeViewsAck(null), false);
});

test("executed: every pushed payload re-anchors — the timeline's update() rule, drift heals within one push", () => {
  resetViewsWriterForTest();
  const posts: any[] = [];
  anchorViewsRev({ rev: 50 });
  anchorViewsRev({ rev: 3 });                       // the kernel's counter is the truth, even downward
  postViewsWrite((m) => posts.push(m), { active: "all", tags: [] } as any);
  assert.equal(posts[0].views.baseRev, 3, "the fresh payload's rev outranks the drifted anchor");
  anchorViewsRev(null);                             // a tabOrder frame without the blob
  anchorViewsRev({ rev: "junk" } as any);           // a malformed rev
  postViewsWrite((m) => posts.push(m), { active: "all", tags: [] } as any);
  assert.equal(posts[1].views.baseRev, 4, "non-numeric anchors change nothing — the counter stands");
});

test("wiring: the chat routes ALL views writes through the shared writer and consumes viewsAck", () => {
  // the one writer (postViews) stamps through postViewsWrite; no raw blob post survives
  assert.match(RENDER, /function postViews\(v: SessionViews\) \{[\s\S]{0,400}?postViewsWrite\(\(m\) => vscodeApi\.postMessage\(m\), v\);/);
  assert.doesNotMatch(RENDER, /postMessage\(\{ type: "setTimelineViews"/, "no raw setTimelineViews post in the chat");
  // the frame router consumes the ack — a refusal drops the known-refused optimistic overlay now
  assert.match(RENDER, /else if \(m\.type === "viewsAck"\) consumeViewsAck\(m, \(\) => \{ pendingSessionViews = null; pendingViewsAge = 0; renderTabs\(\); \}\);/);
  // …and every tabOrder payload re-anchors (captureViews is the views-arrival path)
  assert.match(RENDER, /function captureViews\(v: SessionViews \| null\) \{\s*\n\s*if \(v\) sessionViews = v;\s*\n\s*anchorViewsRev\(v\);/);
});

test("wiring: the Outline (fleet) routes BOTH its lens writers through the shared writer and consumes viewsAck", () => {
  const writes = FLEET.match(/postViewsWrite\(\(msg\) => vscodeApi\.postMessage\(msg\), v\);/g) || [];
  assert.equal(writes.length, 2, "the tag-menu apply AND the chip-sync unpick both stamp through the writer");
  assert.doesNotMatch(FLEET, /postMessage\(\{ type: "setTimelineViews"/, "no raw setTimelineViews post in the fleet");
  assert.match(FLEET, /if \(consumeViewsAck\(m\)\) return;/, "the feed-only router consumes the ack before its guard");
  assert.match(FLEET, /fleetViews = m\.views as SessionViews; anchorViewsRev\(fleetViews\);/,
    "every feed payload's views re-anchors");
});
