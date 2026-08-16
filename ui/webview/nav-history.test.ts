// The chat's back/forward trail (the user 2026-08-14): every rule executed, not regex-pinned.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { NavHistory, type NavSpot } from "./nav-history";

function rig(alive: (sid: string) => boolean = () => true) {
  let here: NavSpot | null = { sid: "a", top: 0 };
  const applied: NavSpot[] = [];
  const h = new NavHistory({
    now: () => (here ? { ...here } : null),
    alive,
    apply: (s) => { applied.push({ ...s }); here = { ...s }; },
  });
  return {
    h, applied,
    moveTo(sid: string, top: number) { h.record(); here = { sid, top }; },   // what setActive does
    at: () => here!,
  };
}

test("back returns to the spot you left, forward re-walks it — across tabs", () => {
  const r = rig();
  r.moveTo("b", 500);                       // a → b (records a@0)
  r.moveTo("c", 900);                       // b → c (records b@500)
  assert.ok(r.h.go(-1));
  assert.deepEqual(r.at(), { sid: "b", top: 500 });
  assert.ok(r.h.go(-1));
  assert.deepEqual(r.at(), { sid: "a", top: 0 });
  assert.ok(r.h.go(1));
  assert.deepEqual(r.at(), { sid: "b", top: 500 });
  assert.ok(r.h.go(1));
  assert.deepEqual(r.at(), { sid: "c", top: 900 });
  assert.equal(r.h.go(1), false, "the trail's end says so instead of pretending");
});

test("a new move truncates the forward trail — the browser rule", () => {
  const r = rig();
  r.moveTo("b", 100);
  r.h.go(-1);                               // back to a
  r.moveTo("z", 50);                        // branch off somewhere new
  assert.equal(r.h.go(1), false, "forward history died at the branch point");
});

test("re-recording the same reading spot is one entry, not many", () => {
  const r = rig();
  r.moveTo("a", 10);                        // within SAME_TOP of a@0 — deduped
  r.moveTo("b", 100);                       // records a once
  r.h.go(-1);
  assert.deepEqual(r.at().sid, "a");
  assert.equal(r.h.go(-1), false, "one entry for one spot");
});

test("spots on closed tabs are skipped, not shown", () => {
  const dead = new Set<string>();
  const r = rig((sid) => !dead.has(sid));
  r.moveTo("b", 100);
  r.moveTo("c", 200);
  dead.add("b");
  assert.ok(r.h.go(-1), "one press: b is gone, so it lands on a — silently past the dead tab");
  assert.deepEqual(r.at(), { sid: "a", top: 0 });
  assert.equal(r.h.go(-1), false, "the dead spot was consumed, not left to trip over");
});

test("a history jump never records itself (the applying latch)", () => {
  let here: NavSpot = { sid: "a", top: 0 };
  const h = new NavHistory({
    now: () => ({ ...here }),
    alive: () => true,
    // the real wiring: apply() lands via setActive, whose first act is record() — re-entrant
    apply: (s) => { here = { ...s }; h.record(); },
  });
  h.record();                       // a@0 onto the trail
  here = { sid: "b", top: 300 };    // the move the record above preceded
  assert.ok(h.go(-1));
  assert.equal(here.sid, "a");
  assert.equal(h.go(-1), false, "the re-entrant record() inside apply() minted no phantom entry");
});

test("the trail is capped — old spots age out, the walk stays sound", () => {
  const r = rig();
  for (let i = 1; i <= 150; i++) r.moveTo("s" + i, i);
  let steps = 0;
  while (r.h.go(-1)) steps++;
  assert.ok(steps <= 101, `bounded: walked ${steps}`);
  assert.ok(steps >= 99, `the cap keeps the recent hundred: walked ${steps}`);
});
