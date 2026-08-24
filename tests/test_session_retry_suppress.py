#!/usr/bin/env python3
"""Per-session auto-retry suppression (the user 2026-07-06): during a usage-limit storm the GLOBAL
"stop all auto-retries" is account-wide + flap-prone, and it only stops romp's own 10s "send retry" loop —
not the CLI's INTERNAL api_retry backoff. So the user wanted to just INTERRUPT the one stuck thread: the
interrupt aborts the in-flight CLI retry, and this suppression keeps romp from re-firing "retry" into that
thread until a SUCCESSFUL turn lands, then it re-arms. Mirrors how an interrupt already suppresses
auto-NUDGE (_interrupt_suppresses_nudge). Functional tests on the state machine + source-pins on the wiring.
"""
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
SRC = open(os.path.join(BIN, "romp-kernel")).read()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_srs", os.path.join(BIN, "romp-kernel")).load_module()


def _human_turn(t):
    """A turn whose user atom is a GENUINE human message at time t (not an interrupt record)."""
    return {"atoms": [{"type": "user", "author": "human", "t": t, "text": "carry on"}]}


class SessionRetrySuppress(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)
        self._orig = {k: getattr(km, k) for k in
                      ("_alive_sessions", "_parse_cached", "_session_chip", "_mark_views_dirty")}
        km.jd.STATE = self.dir                          # retry-suppressed.json lives under jd.STATE
        km._retry_suppress_cache.clear()                # the file cache is process-global — reset per test
        km._mark_views_dirty = lambda *a, **k: None     # no clients in the test

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(km, k, v)
        self.td.cleanup()

    # --- arming ---
    def test_interrupt_arms_suppression_and_membership_reads_true(self):
        self.assertFalse(km._session_retry_suppressed("s1"))
        km._suppress_session_retry("s1")
        km._retry_suppress_cache.clear()                # force a fresh read of the file we just wrote
        self.assertTrue(km._session_retry_suppressed("s1"), "an interrupt arms this thread's suppression")
        self.assertFalse(km._session_retry_suppressed("s2"), "a DIFFERENT thread is untouched — per-session, not global")

    def test_arming_records_a_floor_timestamp(self):
        km._suppress_session_retry("s1")
        d = json.loads((self.dir / "retry-suppressed.json").read_text())
        self.assertIn("s1", d)
        self.assertGreater(d["s1"], 0, "the stamp is the re-arm floor (only a success AFTER it lifts it)")

    def test_writer_canonicalizes_at_commit_and_cannot_restore_a_tid_row(self):
        tid = "01911111-2222-7333-8444-555555555555"
        sid = "11111111-2222-3333-4444-555555555555"
        (self.dir / "retry-suppressed.json").write_text(json.dumps({tid: 10}))
        old_map = dict(km.jd._CODEX_IDENTITY_MAP)
        km.jd._CODEX_IDENTITY_MAP.clear()
        km.jd._CODEX_IDENTITY_MAP[tid] = sid
        km._retry_suppress_cache.clear()
        try:
            km._suppress_session_retry(tid)
            state = json.loads((self.dir / "retry-suppressed.json").read_text())
            self.assertNotIn(tid, state)
            self.assertGreater(state[sid], 10)
        finally:
            km.jd._CODEX_IDENTITY_MAP.clear()
            km.jd._CODEX_IDENTITY_MAP.update(old_map)
            km._retry_suppress_cache.clear()

    def test_two_writers_merge_under_the_identity_lock(self):
        gate = threading.Barrier(2)
        original = km.jd.canonicalize_retry_suppressed_identity

        def synchronized(blob):
            # Both callers start together, but only the one inside the shared lock reaches this
            # function; the other must then re-read its predecessor's committed snapshot.
            if not (self.dir / "retry-suppressed.json").exists():
                try:
                    gate.wait(0.05)
                except threading.BrokenBarrierError:
                    pass
            return original(blob)

        with mock.patch.object(
                km.jd, "canonicalize_retry_suppressed_identity", side_effect=synchronized):
            a = threading.Thread(target=km._suppress_session_retry, args=("s1",))
            b = threading.Thread(target=km._suppress_session_retry, args=("s2",))
            a.start(); b.start(); a.join(5); b.join(5)
        self.assertFalse(a.is_alive() or b.is_alive())
        self.assertEqual(set(json.loads((self.dir / "retry-suppressed.json").read_text())),
                         {"s1", "s2"})

    # --- re-arm: a successful re-engagement clears it ---
    def test_reengaged_and_settled_clean_rearms(self):
        km._suppress_session_retry("s1")
        floor = json.loads((self.dir / "retry-suppressed.json").read_text())["s1"]
        km._alive_sessions = lambda now, tmux: [{"sid": "s1", "path": "x"}]
        km._parse_cached = lambda p: {"turns": [_human_turn(floor + 5)]}   # user spoke AFTER the stop
        km._session_chip = lambda *a, **k: "ready"                          # and it settled clean → success
        km._auto_resume_session_retry(int(time.time()), {})
        km._retry_suppress_cache.clear()
        self.assertFalse(km._session_retry_suppressed("s1"),
                         "a successful user turn after the interrupt re-arms auto-retry")

    # --- a FAILED re-engagement (still blocked) stays suppressed ---
    def test_reengaged_but_still_blocked_stays_suppressed(self):
        km._suppress_session_retry("s1")
        floor = json.loads((self.dir / "retry-suppressed.json").read_text())["s1"]
        km._alive_sessions = lambda now, tmux: [{"sid": "s1", "path": "x"}]
        km._parse_cached = lambda p: {"turns": [_human_turn(floor + 5)]}   # user spoke...
        km._session_chip = lambda *a, **k: "blocked"                        # ...but the turn errored again
        km._auto_resume_session_retry(int(time.time()), {})
        km._retry_suppress_cache.clear()
        self.assertTrue(km._session_retry_suppressed("s1"),
                        "a re-engagement that failed with an API error must NOT re-arm — stay hands-off")

    # --- the user hasn't spoken since the interrupt → stays suppressed even if the chip looks clean ---
    def test_no_reengagement_stays_suppressed(self):
        km._suppress_session_retry("s1")
        floor = json.loads((self.dir / "retry-suppressed.json").read_text())["s1"]
        km._alive_sessions = lambda now, tmux: [{"sid": "s1", "path": "x"}]
        km._parse_cached = lambda p: {"turns": [_human_turn(floor - 60)]}  # last human message was BEFORE the stop
        km._session_chip = lambda *a, **k: "ready"
        km._auto_resume_session_retry(int(time.time()), {})
        km._retry_suppress_cache.clear()
        self.assertTrue(km._session_retry_suppressed("s1"),
                        "no message since the interrupt → the user is still at the controls, keep it off")

    def test_noop_when_nothing_suppressed(self):
        called = []
        km._alive_sessions = lambda now, tmux: called.append(1) or []
        km._auto_resume_session_retry(int(time.time()), {})
        self.assertEqual(called, [], "no suppressed sessions → the sweep does no work")


class SessionRetrySuppressWiring(unittest.TestCase):
    """Source-pins: the suppression is actually WIRED into the interrupt handler, the retry gate, the chat
    status, and the pusher — so a refactor that drops any leg fails here."""

    def test_interrupt_handler_arms_suppression(self):
        blk = SRC.split('elif t == "interrupt":', 1)[1].split("elif t ==", 1)[0]
        self.assertIn("_suppress_session_retry(sid)", blk,
                      "interrupting a thread arms its per-session retry-suppression")

    def test_apiretry_gate_checks_per_session_suppression(self):
        fn = SRC.split("def _fire_api_retry(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_session_retry_suppressed(sid)", fn,
                      "the retry decision skips a thread the user interrupted, not just the global pause")

    def test_status_carries_the_flag(self):
        self.assertIn('"retrySuppressed": _session_retry_suppressed(sid)', SRC,
                      "the chat status exposes retrySuppressed so the client retry loop + card can read it")

    def test_pusher_runs_the_per_session_resume_sweep(self):
        # (now, tmux) — the cycle's ONE liveness snapshot, not a per-job fresh read (2026-08-10 CPU fix)
        self.assertIn("_auto_resume_session_retry(now, tmux)", SRC,
                      "the pusher tick re-arms suppressed threads that land a clean turn")


if __name__ == "__main__":
    unittest.main()
