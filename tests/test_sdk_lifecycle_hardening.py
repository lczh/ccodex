#!/usr/bin/env python3
"""SDK-session lifecycle hardening (2026-07-05: a kernel death stranded every SDK session in
"purgatory" — cut turns never resumed, in-memory queues silently dropped, one orphaned CLI).

Covers the backend half:
  * queue persistence — SdkSession._pending mirrors to the registry on every mutation and is
    re-seeded from it, so a kernel death can DELAY queued turns but never lose them;
  * last_state_value — the cut-turn discriminator reads the last STATE record through the
    interleaved awaiting overlays (the boot heal itself appends one);
  * find_orphan_clis — matches only ORPHANED (ppid 1) SDK-driven CLIs (--resume <ours> +
    stream-json), never a tmux session's interactive `claude --resume` and never a LIVE CLI still
    parented to a kernel (2026-07-06: a duplicate backend's reconcile reaped live sessions);
  * _boot_reconcile — resumes exactly the cut-turn / queued sessions (a user-interrupted or
    cleanly-finished session stays lazy), prepends the visible continuation nudge, reaps orphans;
  * drain — the SIGTERM path stops every running session, counts in-flight turns, and writes NO
    idle/waiting state (the trailing 'working' IS the next boot's resume marker).

All deterministic: no SDK import, no real claude processes (ps/os.kill are patched).
"""
import json
import os
import tempfile
import signal
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = SourceFileLoader("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py")).load_module()


def _backend(d=None):
    return sb.SdkBackend(d or tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)


def _reg(d, sid, **extra):
    r = {"sid": sid, "name": "s-" + sid[:4], "cwd": "/tmp", "alive": True, "lastSid": sid}
    r.update(extra)
    sb.write_reg(Path(d), sid, r)
    return r


class LastStateValue(unittest.TestCase):
    def test_reads_through_awaiting_overlays(self):
        d = tempfile.mkdtemp()
        sid = "11111111-2222-3333-4444-555555555555"
        sb.append_state(Path(d), sid, "working")
        sb.append_awaiting(Path(d), sid, False)      # the boot heal appends exactly this overlay
        self.assertEqual(sb.last_state_value(Path(d), sid), "working",
                         "an overlay after the state record must not hide the cut-turn marker")
        # last_state (the literal last line) would have returned the overlay — that's the trap
        self.assertNotIn("state", sb.last_state(Path(d), sid))

    def test_empty_and_missing(self):
        d = tempfile.mkdtemp()
        self.assertEqual(sb.last_state_value(Path(d), "nope"), "")


class FindOrphanClis(unittest.TestCase):
    SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def test_matches_only_sdk_clis_resuming_ours(self):
        lines = [
            # ours, SDK-driven (stream-json), re-parented to launchd → a true orphan, matched
            " 4242 1 /x/claude --output-format stream-json --resume %s --input-format stream-json" % self.SID,
            # a TMUX session's interactive resume (no stream-json mark) → never touched
            " 4243 1 claude --resume %s --name termsess" % self.SID,
            # SDK-driven but a sid we don't own → not ours to reap
            " 4244 1 /x/claude --resume ffffffff-0000-1111-2222-333333333333 --input-format stream-json",
            # junk / short lines are skipped, not crashed on
            "garbage", " 99", " 99 100", "",
        ]
        self.assertEqual(sb.find_orphan_clis(lines, [self.SID]), [4242])

    def test_live_children_are_never_orphans(self):
        # Same command line as a true orphan, but still parented to a running kernel (ppid != 1):
        # a LIVE session's CLI. Reaping these was the 2026-07-06 kill storm — a duplicate backend's
        # reconcile SIGTERM'd freshly-resumed sessions mid-turn (exit 143).
        lines = [
            " 4242 38438 /x/claude --output-format stream-json --resume %s --input-format stream-json" % self.SID,
            " 4245 1 /x/claude --output-format stream-json --resume %s --input-format stream-json" % self.SID,
        ]
        self.assertEqual(sb.find_orphan_clis(lines, [self.SID]), [4245])

    def test_empty_sids_match_nothing(self):
        lines = [" 1 1 claude --resume  --input-format stream-json"]
        self.assertEqual(sb.find_orphan_clis(lines, [""]), [])

    def test_equals_flag_spelling_matches(self):
        # The Agent SDK moved to `--resume=<sid>` (equals form); the space-only match was blind to
        # it, so every boot reconcile "reaped 0" while a real orphan kept working the repo for over
        # an hour (2026-07-25, the twin incident). Both spellings, and --session-id for a CLI that
        # was spawned fresh and never resumed, must match.
        lines = [
            " 5001 1 /x/claude --output-format stream-json --resume=%s --input-format stream-json" % self.SID,
            " 5002 1 /x/claude --output-format stream-json --session-id=%s --input-format stream-json" % self.SID,
            " 5003 1 /x/claude --output-format stream-json --session-id %s --input-format stream-json" % self.SID,
            # equals form but a foreign sid → still not ours
            " 5004 1 /x/claude --resume=ffffffff-0000-1111-2222-333333333333 --input-format stream-json",
        ]
        self.assertEqual(sb.find_orphan_clis(lines, [self.SID]), [5001, 5002, 5003])


class QueuePersistence(unittest.TestCase):
    def test_enqueue_and_unqueue_mirror_to_registry(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        sid = "11111111-2222-3333-4444-666666666666"
        reg = _reg(d, sid)
        s = sb.SdkSession(be, reg)                   # never started: pure kernel-thread surface
        s.enqueue("first")
        s.enqueue("second")
        self.assertEqual(sb.read_reg(Path(d), sid).get("queue"), ["first", "second"])
        self.assertEqual(s.unqueue(0), "first")
        self.assertEqual(sb.read_reg(Path(d), sid).get("queue"), ["second"],
                         "a canceled turn leaves the persisted queue too")

    def test_init_seeds_pending_from_persisted_queue(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        sid = "11111111-2222-3333-4444-777777777777"
        reg = _reg(d, sid, queue=["held over", "", 42, "and this"])
        s = sb.SdkSession(be, reg)
        self.assertEqual(s.pending(), ["held over", "and this"],
                         "restores strings only — junk entries never wedge delivery")


class RegistrySerialization(unittest.TestCase):
    SID = "11111111-2222-3333-4444-888888888888"

    @staticmethod
    def _gate_two_reads(real_read, sid):
        """Make two named mutator threads snapshot the same old file when no shared lock exists.
        With serialization, the first read times out and commits before the second can enter."""
        first = threading.Event()
        second = threading.Event()
        count_lock = threading.Lock()
        count = [0]

        def gated(state, target):
            value = real_read(state, target)
            if target != sid or not threading.current_thread().name.startswith("reg-mutator"):
                return value
            with count_lock:
                count[0] += 1
                n = count[0]
            if n == 1:
                first.set()
                second.wait(0.2)
            elif n == 2:
                second.set()
            return value
        return gated, first

    def test_public_setter_and_queue_mirror_cannot_lose_each_others_fields(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        _reg(d, self.SID, queue=["old"], mode="acceptEdits")
        real_read = sb.read_reg
        gated, first = self._gate_two_reads(real_read, self.SID)
        with mock.patch.object(sb, "read_reg", side_effect=gated):
            a = threading.Thread(name="reg-mutator-mode",
                                 target=lambda: be.set_mode(self.SID, "plan"))
            b = threading.Thread(name="reg-mutator-queue",
                                 target=lambda: be._update_reg(self.SID, queue=["owed"]))
            a.start(); b.start()
            self.assertTrue(first.wait(1))
            a.join(2); b.join(2)
        self.assertFalse(a.is_alive() or b.is_alive())
        reg = real_read(Path(d), self.SID)
        self.assertEqual(reg["mode"], "plan")
        self.assertEqual(reg["queue"], ["owed"])

    def test_remembered_defaults_merge_is_one_serialized_rmw(self):
        d = tempfile.mkdtemp()
        real_read = sb.read_sdk_defaults
        first = threading.Event()
        second = threading.Event()
        count_lock = threading.Lock()
        count = [0]

        def gated(state):
            value = real_read(state)
            if not threading.current_thread().name.startswith("defaults-mutator"):
                return value
            with count_lock:
                count[0] += 1
                n = count[0]
            if n == 1:
                first.set()
                second.wait(0.2)
            elif n == 2:
                second.set()
            return value

        with mock.patch.object(sb, "read_sdk_defaults", side_effect=gated):
            a = threading.Thread(name="defaults-mutator-model",
                                 target=lambda: sb.write_sdk_default(Path(d), model="opus"))
            b = threading.Thread(name="defaults-mutator-effort",
                                 target=lambda: sb.write_sdk_default(Path(d), effort="high"))
            a.start(); b.start()
            self.assertTrue(first.wait(1))
            a.join(2); b.join(2)
        self.assertFalse(a.is_alive() or b.is_alive())
        self.assertEqual(real_read(Path(d)), {"model": "opus", "effort": "high"})

    def test_kill_waits_out_ensure_then_shuts_down_the_created_session(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        _reg(d, self.SID)
        constructing = threading.Event()
        release = threading.Event()
        instances = []

        class FakeThread:
            def __init__(self):
                self.alive = False
            def is_alive(self):
                return self.alive

        class FakeSession:
            def __init__(self, backend, reg):
                self.sid = reg["sid"]
                self.thread = FakeThread()
                self.on_boot_settled = None
                self.shutdown_called = False
                instances.append(self)
                constructing.set()
                release.wait(2)
            def start(self):
                self.thread.alive = True
            def shutdown(self):
                self.shutdown_called = True
                self.thread.alive = False

        ensured = []
        killed = threading.Event()
        with mock.patch.object(sb, "SdkSession", FakeSession):
            starter = threading.Thread(target=lambda: ensured.append(be._ensure(self.SID)))
            starter.start()
            self.assertTrue(constructing.wait(1))
            killer = threading.Thread(target=lambda: (be.kill(self.SID), killed.set()))
            killer.start()
            time.sleep(0.05)
            self.assertFalse(killed.is_set(), "kill must serialize with an in-progress _ensure")
            release.set()
            starter.join(2); killer.join(2)
        self.assertFalse(starter.is_alive() or killer.is_alive())
        self.assertNotIn(self.SID, be.sessions)
        self.assertTrue(instances[0].shutdown_called)
        self.assertFalse(sb.read_reg(Path(d), self.SID)["alive"])


class BootReconcile(unittest.TestCase):
    def _setup(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        be._ensured = []
        be._ensure = lambda sid, on_boot_settled=None: (be._ensured.append(sid), on_boot_settled and on_boot_settled())
        return d, be

    def test_resumes_exactly_cut_and_queued_sessions(self):
        d, be = self._setup()
        cut = "11111111-aaaa-0000-0000-000000000001"       # tail 'working' → cut by the kernel death
        queued = "11111111-aaaa-0000-0000-000000000002"    # finished, but has a persisted queue
        interrupted = "11111111-aaaa-0000-0000-000000000003"  # user interrupt wrote 'idle'
        finished = "11111111-aaaa-0000-0000-000000000004"  # clean turn end wrote 'waiting'
        dead = "11111111-aaaa-0000-0000-000000000005"
        regs = [_reg(d, cut), _reg(d, queued, queue=["waiting msg"]),
                _reg(d, interrupted), _reg(d, finished), _reg(d, dead, alive=False)]
        sb.append_state(Path(d), cut, "working")
        sb.append_state(Path(d), queued, "waiting")
        sb.append_state(Path(d), interrupted, "working")
        sb.append_state(Path(d), interrupted, "idle")      # the user-interrupt marker
        sb.append_state(Path(d), finished, "waiting")
        sb.append_state(Path(d), dead, "working")          # dead: even a 'working' tail stays dead
        with mock.patch.object(sb.subprocess, "run",
                               return_value=mock.Mock(stdout="")):
            be._boot_reconcile(regs)
        self.assertEqual(sorted(be._ensured), sorted([cut, queued]),
                         "user-interrupted / finished / dead sessions stay lazy")

    def test_cut_turn_gets_the_nudge_prepended_before_its_queue(self):
        d, be = self._setup()
        cut = "11111111-aaaa-0000-0000-00000000000a"
        _reg(d, cut, queue=["sent during the outage"])
        sb.append_state(Path(d), cut, "working")
        sb.append_awaiting(Path(d), cut, False)            # the boot heal's overlay must not mask the cut
        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")):
            be._boot_reconcile([sb.read_reg(Path(d), cut)])
        q = sb.read_reg(Path(d), cut).get("queue")
        self.assertEqual(q, [sb.BOOT_RESUME_NUDGE, "sent during the outage"],
                         "the visible continuation nudge is fed FIRST, then the restored backlog")
        self.assertEqual(be._ensured, [cut])

    def test_dead_bg_tasks_wake_the_session_with_a_named_notice(self):
        # bg tasks die with their CLI (the user 2026-07-11: nimbus's campaign watcher died with a
        # kernel restart and the session waited forever on a notification that could never arrive).
        # The reg's bgTasks mirror survives the death; the reconcile must tell the session what it
        # lost — by DESCRIPTION — and clear the mirror so the same deaths never re-notify.
        d, be = self._setup()
        sid = "11111111-aaaa-0000-0000-00000000000c"
        _reg(d, sid, bgTasks=[{"desc": "20-minute timer for campaign-start check", "type": "local_bash",
                               "since": 1, "toolUseId": "tu1", "lastTool": ""}])
        sb.append_state(Path(d), sid, "waiting")           # idle — NOT cut; the notice alone wakes it
        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")):
            be._boot_reconcile([sb.read_reg(Path(d), sid)])
        self.assertEqual(be._ensured, [sid], "an idle session with dead tasks is woken to hear it")
        reg = sb.read_reg(Path(d), sid)
        self.assertEqual(len(reg["queue"]), 1)
        self.assertIn("20-minute timer for campaign-start check", reg["queue"][0])
        self.assertIn("romp-system", reg["queue"][0], "a visible romp system notice, not silent")
        self.assertEqual(reg["bgTasks"], [], "reported — the same deaths never re-notify")

    def test_cut_turn_with_dead_tasks_orders_resume_nudge_then_notice(self):
        d, be = self._setup()
        sid = "11111111-aaaa-0000-0000-00000000000d"
        _reg(d, sid, queue=["backlog msg"], bgTasks=[{"desc": "power watcher", "since": 1}])
        sb.append_state(Path(d), sid, "working")           # cut by the kernel death
        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")):
            be._boot_reconcile([sb.read_reg(Path(d), sid)])
        q = sb.read_reg(Path(d), sid)["queue"]
        self.assertEqual(q[0], sb.BOOT_RESUME_NUDGE, "continuation context first")
        self.assertIn("power watcher", q[1])
        self.assertEqual(q[2], "backlog msg", "the restored backlog follows the notices")

    def test_stranded_pending_switch_flags_heal_at_boot_without_waking_the_session(self):
        # a /model or /effort switch mid-flight at the kernel's death strands its pending flags; the
        # dormant serving path shows them as switching-dots FOREVER (the user 2026-07-11, who reported the three
        # dots sitting there forever). The boot sweep heals the flags; an otherwise-idle session
        # stays lazy (no wake just for the heal).
        d, be = self._setup()
        sid = "11111111-aaaa-0000-0000-00000000000e"
        _reg(d, sid, effortPending=True, modelPending=True)
        sb.append_state(Path(d), sid, "waiting")
        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")):
            be._boot_reconcile([sb.read_reg(Path(d), sid)])
        reg = sb.read_reg(Path(d), sid)
        self.assertFalse(reg.get("effortPending"))
        self.assertFalse(reg.get("modelPending"))
        self.assertEqual(be._ensured, [], "the heal alone never wakes a session")

    def test_reaps_orphans_but_never_tmux(self):
        d, be = self._setup()
        sid = "11111111-aaaa-0000-0000-00000000000b"
        _reg(d, sid)
        sb.append_state(Path(d), sid, "working")
        ps = ("  555 1 /x/claude --output-format stream-json --resume %s --input-format stream-json\n"
              "  556 1 claude --resume %s --name termsess\n"
              "  557 90210 /x/claude --output-format stream-json --resume %s --input-format stream-json\n"
              ) % (sid, sid, sid)
        killed = []
        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout=ps)), \
             mock.patch.object(sb.os, "kill", side_effect=lambda p, s: killed.append((p, s))):
            be._boot_reconcile([sb.read_reg(Path(d), sid)])
        self.assertEqual(killed, [(555, sb.signal.SIGTERM)],
                         "the SDK orphan is reaped; the tmux CLI and the live (parented) CLI "
                         "on the same sid are untouched")

    def test_reconcile_is_opt_in(self):
        # Constructing the backend plain (tests, ad-hoc) must NOT spawn a reconcile thread; the
        # kernel opts in with reconcile=True. Pinned by patching the method and constructing both ways.
        d = tempfile.mkdtemp()
        sid = "11111111-aaaa-0000-0000-00000000000c"
        _reg(d, sid)
        sb.append_state(Path(d), sid, "working")
        with mock.patch.object(sb.SdkBackend, "_boot_reconcile") as br:
            sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
            self.assertEqual(br.call_count, 0)
            sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, reconcile=True)
            deadline = time.time() + 5
            while br.call_count == 0 and time.time() < deadline:
                time.sleep(0.01)                     # the reconcile runs on its own thread
            self.assertEqual(br.call_count, 1)


class WriteRegConcurrency(unittest.TestCase):
    def test_temp_names_are_writer_unique(self):
        """During a kernel restart the OUTGOING kernel and the incoming boot reconcile write the
        SAME sid's registry concurrently; a shared '<sid>.tmp' let one os.replace steal the other's
        temp mid-write (FileNotFoundError, live 2026-07-06). Pin: concurrent writers never collide
        and the final registry is one of the written values, with no stray temps left behind."""
        d = tempfile.mkdtemp()
        sid = "11111111-2222-3333-4444-888888888888"
        errs = []

        def hammer(tag):
            try:
                for i in range(50):
                    sb.write_reg(Path(d), sid, {"sid": sid, "writer": tag, "i": i})
            except Exception as e:
                errs.append(e)

        ts = [threading.Thread(target=hammer, args=(t,)) for t in ("a", "b", "c")]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errs, [], "no writer may crash on another's temp file")
        self.assertIn(sb.read_reg(Path(d), sid).get("writer"), ("a", "b", "c"))
        strays = [f for f in os.listdir(os.path.join(d, "sdk")) if f.endswith(".tmp")]
        self.assertEqual(strays, [], "failed/completed writes leave no temp litter")


class BootReconcileResilience(unittest.TestCase):
    def test_one_bad_session_does_not_strand_the_rest(self):
        """Live 2026-07-06: a write_reg race on the FIRST session aborted two whole reconcile
        passes, stranding every later session. One session's failure must log and continue."""
        d = tempfile.mkdtemp()
        be = _backend(d)
        be._ensured = []
        be._ensure = lambda sid, on_boot_settled=None: (be._ensured.append(sid), on_boot_settled and on_boot_settled())
        logs = []
        be._log_cb = logs.append
        bad = "11111111-aaaa-0000-0000-0000000000e1"
        good = "11111111-aaaa-0000-0000-0000000000e2"
        regs = [_reg(d, bad), _reg(d, good)]
        for s in (bad, good):
            sb.append_state(Path(d), s, "working")
        real_write = sb.write_reg

        def exploding_write(state_dir, sid, reg):
            if sid == bad:
                raise FileNotFoundError("simulated temp-steal race")
            real_write(state_dir, sid, reg)

        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")), \
             mock.patch.object(sb, "write_reg", side_effect=exploding_write):
            be._boot_reconcile(regs)
        self.assertEqual(be._ensured, [good], "the sweep continued past the failing session")
        self.assertTrue(any("sweep continues" in m for m in logs), "the failure is loud, not silent")


class CrashHeal(unittest.TestCase):
    """_on_session_gone on an ABNORMAL mid-turn death (CLI killed/crashed; not user-interrupted,
    not our shutdown) must NOT settle 'waiting' — that masked the cut and stranded the session
    until the next kernel restart (2026-07-06: reaped sessions wrote triple 'waiting' and stalled).
    Instead it keeps the trailing 'working' and resumes ONCE via _heal_cut_session; the budget
    re-arms only when a turn completes."""

    SID = "11111111-2222-3333-4444-777777777777"

    def _dead_session(self, be, d, inflight=1, interrupted=False):
        reg = sb.read_reg(Path(d), self.SID) or _reg(d, self.SID)   # keep a prior heal's queue intact
        s = sb.SdkSession(be, reg)          # never started: pure object surface
        sb.append_state(Path(d), self.SID, "working")
        s.inflight = inflight
        s._interrupted = interrupted
        return s

    def test_midturn_death_keeps_cut_marker_and_resumes(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        s = self._dead_session(be, d)
        with mock.patch.object(be, "_ensure") as ens:
            be._on_session_gone(s)
        self.assertEqual(sb.last_state_value(Path(d), self.SID), "working",
                         "no 'waiting' settle — the trailing 'working' IS the cut marker")
        q = sb.read_reg(Path(d), self.SID).get("queue")
        self.assertEqual(q, [sb.CRASH_RESUME_NUDGE],
                         "the visible crash nudge is queued so the resume is never silent")
        ens.assert_called_once_with(self.SID)

    def test_second_death_without_completed_turn_is_a_crash_loop(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        logs = []
        be._log_cb = logs.append
        with mock.patch.object(be, "_ensure") as ens:
            be._on_session_gone(self._dead_session(be, d))
            be._on_session_gone(self._dead_session(be, d))   # died again before any ResultMessage
        self.assertEqual(ens.call_count, 1, "one resume per cut — no respawn loop")
        self.assertEqual(sb.read_reg(Path(d), self.SID).get("queue"), [sb.CRASH_RESUME_NUDGE],
                         "the nudge is not stacked by the refused second heal")
        self.assertTrue(any("crash loop" in m for m in logs), "the give-up is loud")
        self.assertEqual(sb.last_state_value(Path(d), self.SID), "working",
                         "still cut — the next kernel restart's reconcile picks it up")

    def test_completed_turn_rearms_the_heal_budget(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        with mock.patch.object(be, "_ensure") as ens:
            be._on_session_gone(self._dead_session(be, d))
            be._turn_completed(self.SID)                     # a ResultMessage landed in between
            be._on_session_gone(self._dead_session(be, d))
        self.assertEqual(ens.call_count, 2, "a completed turn re-arms one resume for the next cut")

    def test_user_interrupted_death_still_settles_waiting(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        s = self._dead_session(be, d, inflight=1, interrupted=True)
        with mock.patch.object(be, "_ensure") as ens:
            be._on_session_gone(s)
        self.assertEqual(sb.last_state_value(Path(d), self.SID), "waiting",
                         "a user-interrupted turn's death is not a cut — settle as before")
        ens.assert_not_called()

    def test_idle_death_still_settles_waiting(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        s = self._dead_session(be, d, inflight=0)
        with mock.patch.object(be, "_ensure") as ens:
            be._on_session_gone(s)
        self.assertEqual(sb.last_state_value(Path(d), self.SID), "waiting")
        ens.assert_not_called()


class Drain(unittest.TestCase):
    def test_drain_stops_sessions_and_writes_no_state(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        sid = "11111111-aaaa-0000-0000-00000000000d"
        reg = _reg(d, sid)
        sb.append_state(Path(d), sid, "working")     # an in-flight turn's stamp
        s = sb.SdkSession(be, reg)
        s.inflight = 1
        s.thread = threading.Thread(target=lambda: time.sleep(0.01), daemon=True)
        s.thread.start()
        be.sessions[sid] = s
        r = be.drain(1.0)
        self.assertEqual((r["stopped"], r["inflight"]), (1, 1))
        self.assertTrue(s.ended, "shutdown was requested on the session")
        self.assertEqual(sb.last_state_value(Path(d), sid), "working",
                         "drain writes no idle/waiting — the trailing 'working' IS the boot "
                         "reconcile's resume marker")

    def test_drain_with_nothing_running_is_a_quiet_noop(self):
        be = _backend()
        self.assertEqual(be.drain(0.1), {"stopped": 0, "inflight": 0, "unjoined": 0, "reaped": 0})

    def test_drain_reaps_the_cli_of_a_session_that_wont_close(self):
        # The 2026-07-25 twin incident: the drain's bound expired on a busy session ("still
        # closing: ..."), the kernel exited, and the orphaned CLI kept executing its turn for over
        # an hour while the next boot resumed the same conversation into a second process. The
        # drain must never exit leaving a live child: SIGTERM the unjoined session's CLI (then
        # SIGKILL if it lingers).
        d = tempfile.mkdtemp()
        be = _backend(d)
        sid = "11111111-aaaa-0000-0000-00000000000e"
        s = sb.SdkSession(be, _reg(d, sid))
        s.inflight = 1
        s.thread = threading.Thread(target=lambda: time.sleep(5), daemon=True)
        s.thread.start()                                  # outlives the drain bound → unjoined
        be.sessions[sid] = s
        be._session_cli_pid = lambda sess: 4242
        calls = []
        def fake_kill(pid, sig):
            calls.append((pid, sig))
            if sig == 0:
                raise ProcessLookupError                  # the TERM landed; existence poll sees it gone
        r = be.drain(0.1, kill=fake_kill)
        self.assertEqual((r["unjoined"], r["reaped"]), (1, 1))
        self.assertIn((4242, signal.SIGTERM), calls)
        self.assertNotIn((4242, signal.SIGKILL), calls, "a TERM that lands never escalates")

    def test_drain_sigkills_a_cli_that_ignores_term(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        sid = "11111111-aaaa-0000-0000-00000000000f"
        s = sb.SdkSession(be, _reg(d, sid))
        s.thread = threading.Thread(target=lambda: time.sleep(5), daemon=True)
        s.thread.start()
        be.sessions[sid] = s
        be._session_cli_pid = lambda sess: 4243
        calls = []
        def stubborn_kill(pid, sig):
            calls.append((pid, sig))                      # sig 0 never raises → still alive
        r = be.drain(0.1, kill=stubborn_kill)
        self.assertEqual(r["reaped"], 1)
        self.assertIn((4243, signal.SIGKILL), calls, "a wedged CLI still never outlives the kernel")


if __name__ == "__main__":
    unittest.main()
