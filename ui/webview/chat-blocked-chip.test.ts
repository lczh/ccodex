// The chat status CHIP must not read "blocked" while a session is still BUSY — actively working (an open
// turn) OR awaiting dispatched/background work (background agents). The chip's only route to "blocked" is
// _api_error (an isApiErrorMessage atom in the transcript). build_session used to compute that
// UNCONDITIONALLY, so a lingering error atom from an EARLIER turn flipped a still-busy session's chip to
// "blocked" — while the FEED's blocked column, which gates the SAME _api_error on `not who_working` (and
// treats awaiting as a working flavor), correctly showed it working. Result: the chip said "blocked", the
// blocked column didn't list it, and the session was actually running background agents (the user
// 2026-06-24). Fix: gate build_session's aerr on `not (open_now or awaiting_why)`, mirroring the feed, so
// both the red "API error" card AND the chip clear while busy; and the chip reads "working" (not "ready")
// when awaiting background work. Source-pin over bin/romp-kernel (build_session is too dependency-heavy to
// invoke directly), matching the existing feed-sort.test.ts precedent.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "bin", "romp-kernel"), "utf8");

test("build_session gates the API-error (chip 'blocked') on NOT busy — mirrors the feed", () => {
  // gated form: a session that is WORKING or AWAITING background work never goes 'blocked' from a stale atom
  assert.match(KERNEL, /aerr = _api_error\(sess\["path"\]\) if not \(open_now or awaiting_why\) else None/);
  // the OLD ungated form (aerr = _api_error(sess["path"]) immediately followed by `if aerr:`) is gone —
  // re-introducing it would bring the bug back.
  assert.doesNotMatch(KERNEL, /aerr = _api_error\(sess\["path"\]\)\s*\n\s*if aerr:/);
});

test("build_session computes open_now + the awaiting signal BEFORE the gate consumes them", () => {
  const onow = KERNEL.indexOf('open_now = _session_working(session["turns"])');
  const awaiting = KERNEL.indexOf('_aw = _session_awaiting(sid, sess["path"], not open_now, stamp=True)');
  const unpack = KERNEL.indexOf('awaiting_why = _aw["why"] if _aw else None');
  assert.ok(unpack !== -1 && awaiting < unpack, "the {kind, why} unpack follows the one call");
  const gate = KERNEL.indexOf('aerr = _api_error(sess["path"]) if not (open_now or awaiting_why) else None');
  assert.ok(onow !== -1 && awaiting !== -1 && gate !== -1, "open_now, awaiting_why, and the gate must all be present");
  assert.ok(onow < gate && awaiting < gate, "open_now + awaiting_why must be defined before the gate (else NameError)");
});

test("the chip reads 'working' for an open turn, 'awaitingBg' for a held one — never 'ready'/'blocked'", () => {
  // the formula lives in the SHARED _session_chip now (the user 2026-07-03) — one derivation for the
  // chat chip AND the timeline lane, so the two surfaces can never disagree. Since 2026-07-13 the
  // awaiting-background case is its OWN state (await-green Awaiting), no longer folded into working.
  assert.match(KERNEL, /"working" if open_now else\n/);
  assert.match(KERNEL, /"awaitingBg" if awaiting_why else "ready"\)/);
  assert.match(KERNEL, /chip = _session_chip\(sid, sess\["path"\], session, tm, now\)/);
});

test("the FEED's blocked column still gates _api_error on `not who_working` (+ cache-only `ps`)", () => {
  // the feed is cache-only on a cold start (the user 2026-06-26): the API-error floor reads the parse only
  // when it's already cached (`ps`), still gated on `not who_working` — the badge fills in after the warm.
  // b4d639e added the awaiting arm (live background agents outrank the stale error floor); the pin follows.
  assert.match(KERNEL, /aerr = _api_error\(s\["path"\]\) if \(ps and not who_working and not sess_awaiting_why\) else None/);
});
