#!/usr/bin/env python3
"""Parked ops deliver on the SETTLE event, independent of the judge pass (2026-09-03).

The kernel parks a user's input while its session is busy, compacting, or limit-held, and delivers the
queue FIFO once the session is quiet (_apply_pending_ops). Until this change the ONLY caller of that
function was the tail of the judge producer's pass, after an untimed join on the judge tiers — so every
parked op in every session waited for the judges to finish. A judge pass can run for hours: one session's
closer sweep, alarm-killed turn after turn, held a pass for 6h22m, and a typed /clear parked mid-turn sat
as a queued chip the whole time until the user cancelled it. The drain now rides the pusher cycle, woken
by the backends' turn-end poke (_wake_kernel), by /tick, by every queue mutation, and by its own 0.5 s
backstop; the producer never touches it.

SYNTHETIC fixtures only: a placeholder uuid, invented texts.
"""
import inspect
import io
import os
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from unittest import mock
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_parkedlive", os.path.join(BIN, "romp-kernel")).load_module()

# The ACCOUNT gate is a separate axis (tests/test_kernel_limit_queue.py); pinned off so the real
# machine's usage.json can never make these tests park for a reason none of them is about.
km._limit_hold = lambda sid: None

SID = "11111111-2222-3333-4444-555555555555"

# Every pusher-cycle job except the drain, quieted so a cycle run here does exactly one thing.
_OTHER_JOBS = ("_push_all", "_lift_spent_awaiting", "_death_sweep_tick", "_end_on_idle_sweep",
               "_deferral_sweep_tick", "_auto_nudge_tick", "_interrupt_block_tick", "_auto_pause_on_limit",
               "_usage_poll_tick", "_auto_pause_on_spend_limit", "_auto_resume_retry",
               "_auto_resume_session_retry", "_auto_retry_tick", "_idle_queue_drive_tick",
               "_clear_done_working_notes")


class _FakeBackend:
    """A tmux-shaped backend: it cannot forward its own sends, so a send parks while the turn is open."""

    def __init__(self):
        self.calls = []
        self.open = True
        self.refuse = False                  # a backend that cannot take the op right now (send → False)

    def busy(self, sid):
        return self.open

    def forwards_sends(self):
        return False

    def send(self, sid, text):
        self.calls.append(("send", text))
        return not self.refuse

    def set_model(self, sid, value):
        self.calls.append(("model", value))
        return True

    def turn_seq(self, sid):
        return 0


class _Drain(unittest.TestCase):
    def setUp(self):
        self.be = _FakeBackend()
        self._patches = [
            mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: self.be)),
            mock.patch.object(km, "_compacting_now", lambda sid, **k: False),
            mock.patch.object(km, "_optimistic_echo", lambda *a, **k: None),
            mock.patch.object(km, "_mark_compacting", lambda sid: None),
            mock.patch.object(km, "_tmux_sessions", lambda: {SID: {"state": "waiting", "backend": "tmux"}}),
        ] + [mock.patch.object(km, name, lambda *a, **k: None) for name in _OTHER_JOBS]
        for p in self._patches:
            p.start()
        km._pending_ops.clear()
        km._drain_hold.clear()
        km._refused_heads.clear()
        km._pusher_wake.clear()
        km._producer_wake.clear()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        km._pending_ops.clear()
        km._drain_hold.clear()
        km._refused_heads.clear()


class DeliveryRidesTheSettle(_Drain):
    def test_parked_op_delivers_on_settle_while_a_judge_pass_is_stuck(self):
        gate = threading.Event()             # the judge tiers block here: a closer sweep that never returns
        ran_on = []                          # the threads that ran the drain
        real_apply = km._apply_pending_ops

        def counting_apply():
            ran_on.append(threading.current_thread().name)
            return real_apply()

        # The stuck pass stays stuck: a real _producer has no exit, and ending its thread by exception
        # trips pytest's thread-exception hook — so the gate is never set and the three daemon threads
        # (producer + two tiers) stay parked, touching nothing, until the process ends.
        with mock.patch.object(km, "_run_tier", lambda fn: gate.wait()), \
             mock.patch.object(km, "_retry_paused_on", lambda: False), \
             mock.patch.object(km, "_episode_boundary_tick", lambda now: None), \
             mock.patch.object(km, "_begin_goals_pass", lambda: None), \
             mock.patch.object(km, "_end_goals_pass", lambda: None), \
             mock.patch.object(km, "_compact_goal_stores", lambda: 0), \
             mock.patch.object(km, "_sdk", lambda: None), \
             mock.patch.object(km.jd, "begin_pass_frame", lambda: False), \
             mock.patch.object(km.jd, "end_pass_frame", lambda f: None), \
             mock.patch.object(km.jd, "consume_judge_recovery", lambda: False), \
             mock.patch.object(km, "_apply_pending_ops", counting_apply):
            producer = threading.Thread(target=km._producer, name="producer-under-test", daemon=True)
            producer.start()
            deadline = time.time() + 5
            while time.time() < deadline and not any(t.name == "triage" for t in threading.enumerate()):
                time.sleep(0.01)
            self.assertTrue(any(t.name == "triage" for t in threading.enumerate()),
                            "a judge pass is in flight, stuck on its tiers")
            # a typed slash command lands while the turn is open → parked (the standing rule)
            km._send_or_park(self.be, SID, "/frobnicate now")
            self.assertEqual(km._pending_ops[SID], [("command", "/frobnicate now", None)])
            self.assertEqual(self.be.calls, [])
            # the turn SETTLES: the backend pokes the kernel exactly as its ResultMessage path does. (On a
            # tree without _wake_kernel the poke is what the backends did before — both wakes by hand — so
            # this test reaches the delivery assertion there and fails on the bug, not on a missing name.)
            self.be.open = False
            km._pusher_wake.clear()
            poke = getattr(km, "_wake_kernel", None) or (lambda: (km._producer_wake.set(), km._pusher_wake.set()))
            poke()
            self.assertTrue(km._pusher_wake.is_set(), "the settle wakes the thread that delivers")
            self.assertTrue(km._producer_wake.is_set(), "…and the judges, as before")
            km._pusher_cycle()                           # ONE pusher cycle, while the judge pass is still stuck
            self.assertFalse(gate.is_set())
            self.assertTrue(producer.is_alive())
            self.assertEqual(self.be.calls, [("send", "/frobnicate now")], "delivered alone, as a fresh prompt")
            self.assertNotIn(SID, km._pending_ops)
            self.assertEqual(ran_on, [threading.current_thread().name],
                             "the drain ran on this cycle and never on the producer")

    def test_two_parks_drain_one_per_settle_in_park_order(self):
        self.assertTrue(km._send_or_park(self.be, SID, "one", echo="human"))   # tmux-shaped: a send parks mid-turn
        self.assertTrue(km._ops_gate(SID), "the compact parks behind it (queued)")   # the drive handler's gate…
        km._park_op(SID, ("compact",))                                          # …and the op it parks
        self.assertEqual([op[0] for op in km._pending_ops[SID]], ["send", "compact"])
        self.be.open = False                                                    # settle #1
        km._pusher_cycle()
        self.assertEqual(self.be.calls, [("send", "one")], "the send fired; the compact waits for ITS turn to end")
        self.assertEqual([op[0] for op in km._pending_ops[SID]], ["compact"])
        self.be.open = True                                                     # the delivered send's turn
        km._pusher_cycle()
        self.assertEqual(self.be.calls, [("send", "one")], "nothing fires into an open turn")
        self.be.open = False                                                    # settle #2
        km._pusher_cycle()
        self.assertEqual(self.be.calls, [("send", "one"), ("send", "/compact")])
        self.assertNotIn(SID, km._pending_ops)
        self.assertNotIn(SID, km._drain_hold, "an authoritative busy() needs no hold between deliveries")

    def test_a_tmux_shaped_delivery_holds_the_sid_until_the_prompt_can_have_landed(self):
        # TmuxBackend has no busy(): _working_now falls to the cached transcript parse, which reads idle for a
        # moment after the keystrokes land — and the drain's own delivery wakes the pusher, so without the hold
        # the op behind would fire into the opening turn (the SEND-ends-the-drain contract, broken). The hold
        # is the producer's old cadence made explicit; it retires when tmux gains an authoritative busy().
        class _TmuxShaped(_FakeBackend):
            def busy(self, sid):
                return None
        self.be = _TmuxShaped()
        with mock.patch.object(km, "_working_now", lambda sid: True):             # the turn is open: both park
            km._send_or_park(self.be, SID, "one", echo="human")
            km._park_op(SID, ("compact",))                                        # the compact handler's park
        self.assertEqual([op[0] for op in km._pending_ops[SID]], ["send", "compact"])
        km._pusher_cycle()                                                        # the cached parse reads idle
        self.assertEqual(self.be.calls, [("send", "one")], "the send fired")
        self.assertIn(SID, km._drain_hold, "…and the sid is held while its prompt lands")
        km._pusher_cycle()                                                        # back-to-back, as a wake would
        self.assertEqual(self.be.calls, [("send", "one")], "the compact does NOT fire into the opening turn")
        km._drain_hold.clear()                                                    # the window passed
        km._pusher_cycle()
        self.assertEqual(self.be.calls, [("send", "one"), ("send", "/compact")])

    def test_wake_kernel_sets_both_events_and_is_the_backends_poke(self):
        km._producer_wake.clear()
        km._pusher_wake.clear()
        km._wake_kernel()
        self.assertTrue(km._producer_wake.is_set() and km._pusher_wake.is_set())
        src = open(os.path.join(BIN, "romp-kernel"), encoding="utf-8").read()
        self.assertEqual(src.count("poke=_wake_kernel"), 2, "both backend constructors (SDK + Codex) poke the kernel")
        self.assertNotIn("poke=_producer_wake.set", src, "no backend pokes only the judges anymore")

    def test_delivery_moved_from_the_producer_to_the_pusher_cycle(self):
        self.assertNotIn("_apply_pending_ops()", inspect.getsource(km._producer), "the judge pass no longer gates delivery")
        src = inspect.getsource(km._pusher_cycle_jobs)
        self.assertIn("_apply_pending_ops()", src, "the pusher cycle delivers the parked queue")
        self.assertLess(src.index("_apply_pending_ops()"), src.index("_push_all("),
                        "delivery precedes the push, so the delivered op's echo and retired chip ride it")

    def test_drain_does_not_wake_the_pusher_when_nothing_changed(self):
        # this tree RETAINS a head op the backend refused (a later retry, never a dropped queue) — the one
        # way a quiet sid's cycle delivers nothing. Retained is not changed: no mirror save, no self-wake
        # (the 0.5 s backstop would otherwise become a hot loop); the sid is marked refused, and only a
        # kernel poke re-asks it (RefusedHeadRetriesOnPoke below).
        self.be.open = False
        self.be.refuse = True
        km._pending_ops[SID] = [("send", "please retry me", None)]
        km._pusher_wake.clear()
        km._apply_pending_ops()
        self.assertEqual(km._pending_ops[SID], [("send", "please retry me", None)], "still parked, at the head")
        self.assertEqual(self.be.calls, [("send", "please retry me")], "asked once")
        self.assertFalse(km._pusher_wake.is_set(),
                         "a cycle that delivered nothing must not re-wake the thread it runs on (a hot loop)")
        self.assertIn(SID, km._refused_heads, "…and the head is marked refused: an ordinary cycle will not re-ask")
        km._apply_pending_ops()                                                   # back-to-back, as a wake would
        self.assertEqual(len(self.be.calls), 1, "no re-ask on an ordinary cycle")
        self.be.refuse = False
        km._wake_kernel()                                                         # the settle / resume poke
        km._pusher_cycle()
        self.assertEqual(self.be.calls[-1], ("send", "please retry me"), "…then the retry fires")
        self.assertNotIn(SID, km._pending_ops)
        km._pusher_wake.clear()
        km._pending_ops[SID] = [("model", "opus")]
        km._apply_pending_ops()
        self.assertEqual(self.be.calls[-1], ("model", "opus"))
        self.assertTrue(km._pusher_wake.is_set(), "a real delivery wakes the push that retires the chip")

    def test_a_cycle_resolves_a_held_sids_path_once(self):
        # review find on #904: the drain's gates each resolved a held sid's transcript path (a discover
        # fingerprint) every cycle; inside a cycle the resolution is memoized on the cycle's scope, and a
        # caller outside a cycle (a WS handler) still resolves fresh
        calls = []
        with mock.patch.object(km, "_sessions", lambda now: (calls.append(1), [])[1]):
            km._path_of(SID)
            km._path_of(SID)
            self.assertEqual(len(calls), 2, "outside a cycle every ask resolves fresh")
            calls.clear()
            km._live_scope.paths = {}
            try:
                km._path_of(SID)
                km._path_of(SID)
            finally:
                km._live_scope.paths = None
            self.assertEqual(len(calls), 1, "inside a cycle the second ask is the memo")
        km._pusher_cycle()
        self.assertIsNone(getattr(km._live_scope, "paths", None), "the memo ends with the cycle")

    def test_cancel_parked_logs_sid_and_kind_only(self):
        km._pending_ops[SID] = [("send", "a private sentence", "human")]
        buf = io.StringIO()
        with redirect_stderr(buf):
            self.assertIsNone(km._cancel_parked(SID, 0, ""))
        line = buf.getvalue()
        self.assertIn(SID, line)
        self.assertIn("send", line)
        self.assertNotIn("a private sentence", line, "the body is user text — never logged")
        self.assertNotIn(SID, km._pending_ops)


class RefusedHeadRetriesOnPoke(_Drain):
    """This tree keeps a head op the backend refused (send/set_* → False) or whose delivery raised, for a
    later retry. What ends a refusal is an EVENT the kernel already receives — SdkBackend.send refuses iff
    the registry says the session is not alive, and resume() flips that and ends with the poke that is
    _wake_kernel; the hooks' POST /tick is the same cue from outside — so the poke CLEARS the refused marks
    and the drain's next visit re-asks, never on a clock, and an ordinary pusher wake leaves a marked head
    alone. The poke clears rather than arming one walk, so a poke landing while a handler holds the lock or
    while the sid sits behind a gate is never lost. SYNTHETIC fixtures only."""

    def _refuse_once(self):
        self.be.open = False
        self.be.refuse = True
        km._pending_ops[SID] = [("send", "please retry me", None)]
        km._pusher_cycle()                                                        # asked once, refused, marked
        self.assertEqual(self.be.calls, [("send", "please retry me")])
        self.assertIn(SID, km._refused_heads)

    def test_a_refused_head_is_not_re_asked_on_an_ordinary_cycle(self):
        self._refuse_once()
        for _ in range(3):                                                        # a push, a stream atom, the backstop
            km._pusher_wake.set()
            km._pusher_cycle()
        self.assertEqual(len(self.be.calls), 1, "no kernel poke → no re-ask")
        self.assertEqual(km._pending_ops[SID], [("send", "please retry me", None)], "still parked, still first")

    def test_a_kernel_poke_re_asks_the_refused_head_and_it_delivers_once_accepted(self):
        self._refuse_once()
        km._wake_kernel()                                                         # a settle / resume, still refusing
        self.assertNotIn(SID, km._refused_heads, "the poke clears the mark: the next visit re-asks")
        km._pusher_cycle()                                                        # an ORDINARY cycle
        self.assertEqual(len(self.be.calls), 2, "one poke, one re-ask")
        self.assertIn(SID, km._refused_heads, "still refused → marked again")
        km._pusher_cycle()
        self.assertEqual(len(self.be.calls), 2, "the next ordinary cycle does not ask again")
        self.be.refuse = False                                                    # the session came back…
        km._wake_kernel()                                                         # …which is exactly what pokes
        km._pusher_cycle()
        self.assertEqual(self.be.calls[-1], ("send", "please retry me"), "delivered on the visit after the poke")
        self.assertNotIn(SID, km._pending_ops)
        self.assertNotIn(SID, km._refused_heads, "the mark goes with the delivered head")

    def test_a_poke_during_a_lock_held_cycle_is_not_lost(self):
        # a poke that lands while a handler holds the queue lock wakes a cycle whose drain yields. Had the
        # poke armed that one walk, it would be spent on nothing and the refused head skipped until an
        # unrelated poke; clearing the mark leaves the sid unmarked for the first visit that reaches it
        self._refuse_once()
        held, release = threading.Event(), threading.Event()

        def handler():
            with km._pending_ops_lock:
                held.set()
                release.wait(5)
        th = threading.Thread(target=handler, name="handler-under-test", daemon=True)
        th.start()
        self.assertTrue(held.wait(5))
        try:
            km._wake_kernel()                                                     # the poke lands mid-handoff
            km._pusher_cycle()                                                    # its cycle: the drain yields
            self.assertEqual(len(self.be.calls), 1, "the drain skipped this cycle")
            self.assertNotIn(SID, km._refused_heads, "…but the poke's clearing stands")
        finally:
            release.set()
            th.join(5)
        km._pusher_cycle()                                                        # an ordinary cycle
        self.assertEqual(len(self.be.calls), 2, "the first visit after the poke re-asks")

    def test_a_refused_head_behind_a_limit_hold_is_re_asked_when_the_hold_lifts(self):
        # the account gate lifts on the API's reset stamp — a wall clock, no poke. A poke that landed while
        # the hold was on must not be spent on the gate skip: the sid stays unmarked (a gate skip never
        # marks) and the first visit past the lifted gate re-asks
        self._refuse_once()
        hold = {"reason": "limit", "resetsAt": None, "what": "waiting for your usage limit to reset"}
        with mock.patch.object(km, "_limit_hold", lambda sid: hold):
            km._wake_kernel()                                                     # the poke, during the hold
            km._pusher_cycle()
            self.assertEqual(len(self.be.calls), 1, "held: not asked")
            self.assertNotIn(SID, km._refused_heads, "…and not re-marked by a gate skip")
            km._pusher_cycle()
            self.assertEqual(len(self.be.calls), 1, "still held")
        km._pusher_cycle()                                                        # the hold lifted, no poke
        self.assertEqual(len(self.be.calls), 2, "the first visit past the gate re-asks")

    def test_replacing_the_refused_head_pick_in_place_clears_the_mark(self):
        # _park_op replaces a same-kind model/effort/fast pick IN PLACE; at index 0 that changes the HEAD of a
        # marked sid, and a new head is not known-refused (a park behind the head leaves the mark alone)
        self.be.open = False
        km._pending_ops[SID] = [("model", "opus"), ("send", "later", None)]
        km._refused_heads.add(SID)
        km._park_op(SID, ("effort", "high"))                                      # no effort op yet: appends
        self.assertIn(SID, km._refused_heads, "a park behind the head keeps the mark")
        km._park_op(SID, ("model", "sonnet"))                                     # replaces the head in place
        self.assertEqual(km._pending_ops[SID][0], ("model", "sonnet"))
        self.assertNotIn(SID, km._refused_heads, "a new head is not known-refused")
        km._pusher_cycle()                                                        # an ordinary cycle asks it
        self.assertEqual(self.be.calls[0], ("model", "sonnet"))

    def test_a_refused_compact_park_marks_the_sid_so_its_own_wake_does_not_re_ask(self):
        # the WS compact handler (_drive: "compact" / "compactSession") parks a refused /compact for retry,
        # like _send_or_park — and marks it at once, so the wake its park raises does not ask again
        self.be.open = False
        self.be.refuse = True
        client = {"send": lambda *a, **k: None, "wid": "w1"}
        with mock.patch.object(km, "_kernel_knows", lambda sid: True):
            self.assertTrue(km._drive({"type": "compactSession", "id": SID}, client))
        self.assertEqual(self.be.calls, [("send", "/compact")], "asked once, by the handler itself")
        self.assertEqual(km._pending_ops[SID], [("compact",)], "refused → parked")
        self.assertIn(SID, km._refused_heads, "marked at the park")
        km._pusher_cycle()                                                        # the park's own wake
        self.assertEqual(len(self.be.calls), 1, "…does not re-ask")

    def test_send_or_park_marks_an_immediate_refusal_so_the_park_wake_does_not_re_ask(self):
        self.be.open = False
        self.be.refuse = True
        self.assertTrue(km._send_or_park(self.be, SID, "hello"), "refused → parked (queued)")
        self.assertEqual(self.be.calls, [("send", "hello")], "asked once, by the send itself")
        self.assertIn(SID, km._refused_heads, "marked at the park, not after one more refusal")
        self.assertTrue(km._pusher_wake.is_set(), "the park woke the pusher (the chip renders)…")
        km._pusher_cycle()
        self.assertEqual(len(self.be.calls), 1, "…and that wake does not re-ask")

    def test_cancelling_the_refused_head_clears_the_mark(self):
        self._refuse_once()
        km._pending_ops[SID].append(("model", "opus"))                            # something behind it
        self.assertIsNone(km._cancel_parked(SID, 1, ""))                          # cancel the one BEHIND: head unchanged
        self.assertIn(SID, km._refused_heads, "the refused head is still the head")
        self.assertIsNone(km._cancel_parked(SID, 0, ""))                          # cancel the refused head itself
        self.assertNotIn(SID, km._refused_heads, "a new head (or none) is not known-refused")
        self.assertNotIn(SID, km._pending_ops)
        km._pending_ops[SID] = [("send", "please retry me", None), ("model", "opus")]
        km._refused_heads.add(SID)
        self.be.refuse = False
        self.assertIsNone(km._cancel_parked(SID, 0, ""))                          # head cancelled, one op left
        km._pusher_cycle()                                                        # ordinary cycle: the new head is not refused
        self.assertEqual(self.be.calls[-1], ("model", "opus"), "the op behind fires without waiting for a poke")

    def test_cancelling_the_last_op_drops_the_tmux_hold(self):
        km._pending_ops[SID] = [("send", "one", None), ("send", "two", None)]
        km._drain_hold[SID] = time.monotonic() + 60
        self.assertIsNone(km._cancel_parked(SID, 1, ""))
        self.assertIn(SID, km._drain_hold, "a hold still has something to space")
        self.assertIsNone(km._cancel_parked(SID, 0, ""))
        self.assertNotIn(SID, km._drain_hold, "an emptied queue has nothing left for a hold to space")
        self.assertNotIn(SID, km._pending_ops)

    def test_a_raising_backend_is_re_asked_only_on_pokes_and_logs_once_per_poke(self):
        km._pending_ops[SID] = [("send", "into the void", None)]

        def dead(sid):
            raise RuntimeError("no such session")
        buf = io.StringIO()
        with mock.patch.object(km.Sessions, "backend_for", staticmethod(dead)), redirect_stderr(buf):
            km._pusher_cycle()                                                    # the first ask fails and logs
            self.assertEqual(buf.getvalue().count("pending ops apply"), 1)
            self.assertIn(SID, km._refused_heads)
            for _ in range(3):
                km._pusher_wake.set()
                km._pusher_cycle()
            self.assertEqual(buf.getvalue().count("pending ops apply"), 1, "ordinary cycles neither ask nor log")
            km._wake_kernel()
            km._pusher_cycle()
            self.assertEqual(buf.getvalue().count("pending ops apply"), 2, "one poke → one more ask → one more line")
        self.assertEqual(km._pending_ops[SID], [("send", "into the void", None)], "retained throughout")
        self.assertNotIn("into the void", buf.getvalue(), "the body is user text — never logged")

    def test_the_drain_yields_to_a_handler_holding_the_queue_lock(self):
        # the fork's handlers hold _pending_ops_lock ACROSS their backend call (a Codex client connect can
        # take seconds); the drain is the first job of every cycle, so a blocking take would stall every
        # session's push behind that handler. It takes the lock non-blocking and skips; the handler's own
        # park wakes the pusher when it is done, so the skipped delivery costs nothing.
        self.be.open = False
        km._pending_ops[SID] = [("model", "opus")]
        held, release = threading.Event(), threading.Event()

        def handler():
            with km._pending_ops_lock:
                held.set()
                release.wait(5)
        th = threading.Thread(target=handler, name="handler-under-test", daemon=True)
        th.start()
        self.assertTrue(held.wait(5))
        pushes = []
        try:
            with mock.patch.object(km, "_push_all", lambda **k: pushes.append(1)), \
                 mock.patch.object(km, "_clients", [object()]):                  # a browser is connected
                km._pusher_cycle()
            self.assertEqual(self.be.calls, [], "the drain skipped: a handler owns the queue")
            self.assertEqual(km._pending_ops[SID], [("model", "opus")], "nothing lost")
            self.assertEqual(pushes, [1], "…and the push still ran, not stalled behind the handler")
        finally:
            release.set()
            th.join(5)
        km._pusher_cycle()
        self.assertEqual(self.be.calls, [("model", "opus")], "the next cycle drains")
        self.assertNotIn(SID, km._pending_ops)


if __name__ == "__main__":
    unittest.main()
