// romp-manager's restart-storm guard (the user 2026-06-24): the kernel was SIGTERM'd + respawned 300+ times
// because every /restart restarted 1:1. restartGate coalesces near-simultaneous requests into one trailing
// restart and, once the rate looks like a storm, holds to one restart per cooldown. Pure decision function,
// time injected — unit-tested here. Run: node --test tests/manager-restart.test.js
const { test } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { restartGate } = require(path.join(__dirname, '..', 'bin', 'romp-manager'));

const OPTS = { coalesceMs: 3000, stormWindowMs: 60000, stormMax: 8, stormCooldownMs: 30000 };
const burst = (n, start = 1000, gap = 100) => Array.from({ length: n }, (_, i) => start + i * gap);

test('first restart goes through immediately', () => {
  assert.equal(restartGate({ last: null, history: [] }, 1000, OPTS).action, 'restart');
});

test('a rapid second request coalesces into a single trailing restart', () => {
  const g = restartGate({ last: 1000, history: [1000] }, 1500, OPTS);   // 500ms later
  assert.equal(g.action, 'coalesce');
  assert.ok(g.scheduleIn > 0 && g.scheduleIn <= OPTS.coalesceMs);
});

test('a request after the coalesce window restarts', () => {
  assert.equal(restartGate({ last: 1000, history: [1000] }, 4001, OPTS).action, 'restart');
});

test('a storm (>= stormMax in window) widens the gap to the cooldown — caps a tight loop', () => {
  const history = burst(8);                                            // 8 restarts in 700ms
  const g = restartGate({ last: history[7], history }, history[7] + 4000, OPTS); // 4s > coalesce, < cooldown
  assert.equal(g.inStorm, true);
  assert.equal(g.action, 'coalesce');
});

test('after the cooldown elapses a restart goes through even mid-storm (latest code still loads)', () => {
  const history = burst(8);
  assert.equal(restartGate({ last: history[7], history }, history[7] + 31000, OPTS).action, 'restart');
});

test('old restarts age out of the window — no false storm', () => {
  const history = burst(8);
  const g = restartGate({ last: history[7], history }, history[7] + 70000, OPTS);
  assert.equal(g.inStorm, false);
  assert.equal(g.action, 'restart');
});

// Quiet-window deploy deferral (the user 2026-07-20): peers deploying with `romp --refresh` bounced
// the kernel 11x in one day, each bounce cutting whatever SDK turns were in flight. quietGate is the
// pure apply/wait decision: apply when the kernel reports zero in-flight turns (or is unreachable —
// nothing to cut), wait otherwise, with a backstop cap so a never-quiet fleet still gets its deploy.
const { quietGate } = require(path.join(__dirname, '..', 'bin', 'romp-manager'));
const QOPTS = { maxDeferMs: 900000 };

test('quiet fleet applies immediately', () => {
  const g = quietGate({ since: 1000 }, 0, 2000, QOPTS);
  assert.equal(g.action, 'apply');
  assert.equal(g.reason, 'quiet');
});

test('in-flight turns defer the bounce', () => {
  const g = quietGate({ since: 1000 }, 2, 2000, QOPTS);
  assert.equal(g.action, 'wait');
  assert.match(g.reason, /2 turn/);
});

test('an unreachable kernel applies — nothing a restart could cut', () => {
  assert.equal(quietGate({ since: 1000 }, null, 2000, QOPTS).action, 'apply');
});

test('the backstop cap applies even while busy — a deploy can never starve', () => {
  const g = quietGate({ since: 1000 }, 5, 1000 + QOPTS.maxDeferMs, QOPTS);
  assert.equal(g.action, 'apply');
  assert.equal(g.reason, 'backstop cap');
});

test('just under the cap still waits', () => {
  assert.equal(quietGate({ since: 1000 }, 5, 999 + QOPTS.maxDeferMs, QOPTS).action, 'wait');
});

// quietTick — the CHAIN around quietGate (the 2026-08-14 incident: a queued refresh logged its backstop
// and then silently never applied; the old loop re-armed the next tick only inside the busy-probe
// callback, so a probe whose callback never fired killed the pending refresh with zero further log
// lines). Dependency-injected: these tests drive the exact seams that failed.
const { quietTick } = require(path.join(__dirname, '..', 'bin', 'romp-manager'));

test('quietTick: a probe whose callback never fires cannot kill the chain (schedule-first)', () => {
  let scheduled = 0;
  quietTick({ pending: () => ({ since: 0 }), fetchBusy: () => {}, now: () => 1000,
              opts: QOPTS, log: () => {}, schedule: () => { scheduled++; }, apply: () => {} });
  assert.equal(scheduled, 1);
});

test('quietTick: a double-fired probe callback evaluates once (http timeout + error both fire)', () => {
  let applied = 0; let cb;
  quietTick({ pending: () => ({ since: 0 }), fetchBusy: (c) => { cb = c; }, now: () => 1000,
              opts: QOPTS, log: () => {}, schedule: () => {}, apply: () => { applied++; } });
  cb(0); cb(0);
  assert.equal(applied, 1);
});

test('quietTick: a throwing evaluation is LOUD and leaves the refresh queued', () => {
  let logged = ''; let applied = 0;
  quietTick({ pending: () => ({ since: 0 }), fetchBusy: (c) => c(0), now: () => { throw new Error('boom'); },
              opts: QOPTS, log: (m) => { logged = m; }, schedule: () => {}, apply: () => { applied++; } });
  assert.match(logged, /stays queued/);
  assert.equal(applied, 0);
});

test('quietTick: a satisfied pending (an immediate restart won) neither probes nor re-arms', () => {
  let scheduled = 0;
  quietTick({ pending: () => null, fetchBusy: () => { throw new Error('must not probe'); },
              now: () => 0, opts: QOPTS, log: () => {}, schedule: () => { scheduled++; }, apply: () => {} });
  assert.equal(scheduled, 0);
});

test('quietTick: a quiet fleet applies through the injected apply', () => {
  let applied = null;
  quietTick({ pending: () => ({ since: 0 }), fetchBusy: (c) => c(0), now: () => 1000,
              opts: QOPTS, log: () => {}, schedule: () => {}, apply: (g) => { applied = g; } });
  assert.equal(applied && applied.reason, 'quiet');
});
