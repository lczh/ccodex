#!/usr/bin/env python3
"""Peer-to-peer PULL — the other direction of `romp update` (the user 2026-07-27): sync rides the
attaching side's own ssh in BOTH directions, because the remote has no route back (its push died with
"No route to host"). `POST /tunnels/pull` / _pull_remote fetches the remote clone's committed HEAD
and fast-forwards the local checkout onto it — ff-only, clean local tree required, kernel never
auto-restarted. The automatic variant fires from the supervisor only for a TRUSTED remote strictly
ahead while the local checkout sits on main.

SYNTHETIC fixtures only — invented hosts and placeholder shas; subprocess/ssh fully stubbed."""
import os
import unittest
from importlib.machinery import SourceFileLoader
from unittest import mock
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_pull", os.path.join(BIN, "romp-kernel")).load_module()

LOCAL = "a" * 40                     # local HEAD (full sha)
REMOTE = "b" * 40                    # the remote's newer commit


class _R:
    def __init__(self, out="", err="", rc=0):
        self.stdout, self.stderr, self.returncode = out, err, rc


class FastPullGate(unittest.TestCase):
    """_is_fast_pull mirrors _is_fast_forward: it must say no to anything it cannot prove is a strict
    fast-forward of the LOCAL checkout."""

    def _fp(self, behind, ahead, ood=True):
        saved = (km._remote_out_of_date, km._behind_info)
        km._remote_out_of_date = lambda r: ood
        km._behind_info = lambda sha: {"behind": behind, "ahead": ahead, "date": ""}
        try:
            return km._is_fast_pull({"host": "TESTHOST", "kernel_sha": REMOTE, "trust": "trusted"})
        finally:
            km._remote_out_of_date, km._behind_info = saved

    def test_strictly_ahead_is_a_fast_pull(self):
        self.assertTrue(self._fp(behind=0, ahead=3), "the remote purely extends us — pulling only adds")

    def test_everything_else_is_not(self):
        self.assertFalse(self._fp(behind=0, ahead=0, ood=False), "up to date")
        self.assertFalse(self._fp(behind=2, ahead=0), "behind is the PUSH direction")
        self.assertFalse(self._fp(behind=1, ahead=1), "diverged: never automatic")
        self.assertFalse(self._fp(behind=None, ahead=None), "an unknown build is unprovable — no")
        self.assertFalse(self._fp(behind=0, ahead=None), "half-known is still unproven")

    def test_nontrusted_host_is_never_a_code_source(self):
        saved = (km._remote_out_of_date, km._behind_info)
        km._remote_out_of_date = lambda r: True
        km._behind_info = lambda sha: {"behind": 0, "ahead": 3, "date": ""}
        try:
            self.assertFalse(km._is_fast_pull({"host": "TESTHOST", "kernel_sha": REMOTE,
                                               "trust": "directed"}))
        finally:
            km._remote_out_of_date, km._behind_info = saved


class PullRemote(unittest.TestCase):
    """_pull_remote drives git fetch + merge --ff-only via a dispatching subprocess mock."""

    def setUp(self):
        self._run, self._hc = km.subprocess.run, dict(km._HEAD_CACHE)
        km._HEAD_CACHE.update(ts=9e18, full=LOCAL, short=LOCAL[:8])
        km._remotes.clear()
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "trust": "trusted",
                                    "kernel_sha": REMOTE}
        # the harness machine is a DEV-channel machine: the pull's absolute stable gate refuses
        # unsigned peer commits on stable installs (which is _update_channel's default here)
        self._chan_patch = mock.patch.object(km, "_update_channel", return_value="dev")
        self._chan_patch.start()
        self.addCleanup(lambda: self._chan_patch.stop())   # late-bound: the absolute-refusal
        #                                                    test swaps the patcher object

    def tearDown(self):
        km.subprocess.run = self._run
        km._HEAD_CACHE.clear(); km._HEAD_CACHE.update(self._hc)
        km._remotes.clear()
        km._set_install_failed("")

    def _wire(self, dirty="", rhead=REMOTE, fetched=REMOTE, resolved=REMOTE,
              ancestor_rc=0, merge_rc=0, count="3", status_rc=0, install_rc=0):
        calls = []
        latch_at_merge = self.latch_at_merge = []
        merged = self.merged = {"v": False}
        def fake_popen(argv, **kw):
            calls.append(argv)                       # install.sh spawns via Popen under
            if km._remotes_lock.acquire(blocking=False):
                km._remotes_lock.release()           # a spawn OUTSIDE the hold is the exact P2
                raise AssertionError(                # regression this seam exists to prevent —
                    "install.sh spawned OUTSIDE the _remotes_lock hold")   # a mutant reverting to
            #                                          check-then-launch passed every test (the
            #                                          adversarial review, 2026-08-21)
            proc = mock.MagicMock()                  # a REAL Popen here executed the repo's
            proc.communicate.return_value = (        # actual installer from the tests
                "boom" if install_rc else "", None)
            proc.returncode = install_rc
            return proc
        # EARLY-bound restore: a late-binding self._real_popen read the value at CLEANUP time,
        # and a second _wire() in one test overwrote it — the stdlib Popen was never restored
        # and every later test in the process got the MagicMock (km.subprocess IS the shared
        # stdlib module)
        self.addCleanup(lambda real=km.subprocess.Popen: setattr(km.subprocess, "Popen", real))
        # the snapshot staging is stubbed (r44): its archive/tar run against the REAL repo,
        # which these fixtures do not have — the entry path just needs to contain install.sh
        # so the spawn-shape pins keep reading
        self._saved_stage = km._stage_pull_snapshot
        km._stage_pull_snapshot = lambda commit: "/tmp/romp-test-snap/install.sh"
        self.addCleanup(lambda: setattr(km, "_stage_pull_snapshot", self._saved_stage))
        km.subprocess.Popen = fake_popen

        def fake(argv, **kw):
            calls.append(argv)
            if any("install.sh" in str(a) for a in argv):
                return _R(rc=install_rc, err="boom" if install_rc else "")
            if argv[0] == "git" and "status" in argv:
                return _R(out=dirty, rc=status_rc)
            if argv[0] == "git" and "fetch" in argv:
                return _R()
            if argv[0] == "git" and "merge-base" in argv:
                return _R(rc=ancestor_rc)
            if argv[0] == "git" and "rev-list" in argv:
                return _R(out=count)
            if argv[0] == "git" and "merge" in argv:
                latch_at_merge.append(km._install_failed_sha())
                merged["v"] = True
                return _R(rc=merge_rc, err="not a fast-forward" if merge_rc else "")
            if argv[0] == "git" and "rev-parse" in argv:
                if "FETCH_HEAD" in argv:
                    return _R(out=fetched)
                if str(argv[-1]).endswith("^{commit}"):
                    return _R(out=resolved)
                # STATEFUL: a plain `rev-parse HEAD` answers differently before and after the
                # merge — a stateless answer let a mutant that records pre_head AFTER the merge
                # pass the rollback tests while rolling back to the fetched commit itself (the
                # adversarial review, 2026-08-19). Post-merge it answers the FETCHED sha, as a
                # real ff-merge would — the landed-verify (r32) refuses anything else.
                return _R(out=fetched if merged["v"] else "premerge")
            cmd = argv[-1]                          # ssh: the clone discovery
            if "for d in" in cmd:
                return _R(out="DIR:/home/u/romp\nHEAD:%s\nDIRTY:" % rhead)
            return _R()
        km.subprocess.run = fake
        return calls

    def test_a_noop_ff_that_left_head_elsewhere_refuses_and_disarms(self):
        # the r31 verification: merge --ff-only exits 0 WITHOUT moving when a seam commit
        # already contains the target — install then ran at C while the latch named the target
        calls = self._wire()
        real = km.subprocess.run

        def head_stays(argv, **kw):
            r = real(argv, **kw)
            if argv[0] == "git" and "rev-parse" in argv and "merge" not in argv \
                    and not str(argv[-1]).endswith("^{commit}") and "FETCH_HEAD" not in argv \
                    and self.merged["v"]:
                return type(r)(out="c" * 40)       # HEAD sits on the seam commit, not the target
            return r
        km.subprocess.run = head_stays
        ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("did not land", detail)
        self.assertEqual(km._install_failed_sha(), "",
                         "disarmed: the latch must not name a sha HEAD is not on")
        self.assertFalse(any("install.sh" in str(a) for c in calls for a in c),
                         "nothing installed at the seam commit")

    def test_an_unreadable_head_after_the_pull_move_keeps_the_latch(self):
        # the r32 verification: unknown is not "not landed" — disarming on an unreadable HEAD
        # erased the protection when the move HAD landed
        self._wire()
        real = km.subprocess.run

        def head_unreadable(argv, **kw):
            if argv[0] == "git" and "rev-parse" in argv and "FETCH_HEAD" not in argv \
                    and not str(argv[-1]).endswith("^{commit}") and self.merged["v"]:
                return _R(rc=1, out="")
            return real(argv, **kw)
        km.subprocess.run = head_unreadable
        ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("HEAD cannot be read", detail)
        self.assertEqual(km._install_failed_sha(), "bbbbbbbb",
                         "the latch STAYS armed — the boot heal settles it")

    def test_no_host_is_a_no_op(self):
        self.assertEqual(km._pull_remote(""), (False, "no host"))

    def test_a_checked_in_host_is_refused_with_the_honest_reason(self):
        # This is the observed bug: a sync attempted at a host with no ssh route died with a bare
        # "No route to host". The row knows there is no path — say so, and say where to sync instead.
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "checkin_peer": True}
        ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("no ssh path", detail)
        self.assertIn("dashboard", detail)

    def test_push_refuses_a_checked_in_host_the_same_way(self):
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "checkin_peer": True}
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("no ssh path", detail)

    def test_equal_heads_with_a_clear_latch_are_a_true_no_op(self):
        self._wire(rhead=LOCAL)                       # the remote already sits at OUR head
        with mock.patch.object(km, "_install_latch_lines", return_value=[]):
            ok, detail = km._pull_remote("TESTHOST", expected_sha=LOCAL)
        self.assertTrue(ok, detail)
        self.assertIn("already up to date", detail)

    def test_equal_heads_with_an_armed_latch_still_run_the_settle_transaction(self):
        # the v1.3.16 audit's P2.10: the equal-HEAD return reported success while an armed
        # install latch sat unsettled and the update lock was never taken
        self._wire(rhead=LOCAL)
        with mock.patch.object(km, "_install_latch_lines", return_value=["aaaaaaaa"]):
            ok, detail = km._pull_remote("TESTHOST", expected_sha=LOCAL)
        self.assertNotIn("already up to date", detail,
                         "HEAD equality alone cannot bypass this checkout's recovery record")

    def test_equal_heads_with_an_unreadable_latch_still_run_the_settle_transaction(self):
        self._wire(rhead=LOCAL)
        with mock.patch.object(km, "_install_latch_lines", return_value=None):
            ok, detail = km._pull_remote("TESTHOST", expected_sha=LOCAL)
        self.assertNotIn("already up to date", detail,
                         "an unreadable latch is never presumed clear")

    def test_the_equal_head_no_op_is_decided_inside_the_lock(self):
        # the v1.3.17 audit's P2.8: the clear-latch check preceded lock acquisition, so a latch
        # armed (or a HEAD moved) between the check and the report was bypassed with no settle.
        # With the update lock HELD elsewhere, the no-op must refuse — never report success on
        # facts it could not read under the lock.
        self._wire(rhead=LOCAL)
        with mock.patch.object(km, "_install_latch_lines", return_value=[]), \
             mock.patch.object(km, "_update_flock", return_value=None):
            ok, detail = km._pull_remote("TESTHOST", expected_sha=LOCAL)
        self.assertFalse(ok)
        self.assertIn("another update", detail)

    def test_the_no_op_facts_are_read_only_after_acquisition(self):
        # the audit's schedule (latch armed between an unlocked check and the report) is
        # structurally gone: there is no unlocked check left. Pin the order — the lock is
        # acquired BEFORE the latch/HEAD reads that justify "already up to date".
        self._wire(rhead=LOCAL)
        seq = []
        real_flock = km._update_flock

        def lock_first():
            seq.append("lock")
            return os.open(os.devnull, os.O_RDONLY)

        def latch_read():
            seq.append("latch")
            return []

        with mock.patch.object(km, "_update_flock", side_effect=lock_first), \
             mock.patch.object(km, "_install_latch_lines", side_effect=latch_read):
            ok, detail = km._pull_remote("TESTHOST", expected_sha=LOCAL)
        self.assertTrue(ok, detail)
        self.assertIn("already up to date", detail)
        self.assertEqual(seq[0], "lock", "acquisition precedes every no-op fact read")
        self.assertIn("latch", seq)

    def test_a_dirty_local_tree_is_refused(self):
        # the fast-forward rewrites the working tree, and peers' uncommitted edits are not ours to move
        self._wire(dirty=" M kernel/kernel.py")
        ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("uncommitted", detail)

    def test_a_clean_fast_forward_pulls_and_says_what_to_do_next(self):
        calls = self._wire()
        ok, detail = km._pull_remote("TESTHOST")
        self.assertTrue(ok, detail)
        self.assertIn("pulled 3 commits from TESTHOST", detail)
        self.assertIn("restart romp", detail, "the kernel is NOT auto-restarted; the detail says the next step")
        fetch = next(a for a in calls if a[0] == "git" and "fetch" in a)
        self.assertIn("TESTHOST:/home/u/romp", fetch, "fetches from the discovered clone over our own ssh")
        merge = next(a for a in calls if a[0] == "git" and "merge" in a)
        self.assertIn("--ff-only", merge, "never a merge or rebase on the user's behalf")

    def test_a_failed_status_is_unknown_never_clean(self):
        # a status that ERRORS with empty stdout used to read as a clean tree and the pull
        # fast-forwarded over state the command never saw (the user's audit, 2026-08-17)
        self._wire(status_rc=128)
        ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("state is unknown", detail)

    def test_the_pull_is_a_full_update_transaction(self):
        # this path used to change HEAD with no lock, no intent, and no install.sh (the user's
        # audit, 2026-08-17) — now: the latch is armed BEFORE the merge, install.sh runs as part
        # of the pull, and a passing install spends the latch
        calls = self._wire()
        ok, detail = km._pull_remote("TESTHOST")
        self.assertTrue(ok, detail)
        self.assertEqual(self.latch_at_merge, [km._sha8(REMOTE)],
                         "the intent is durable before HEAD can move")
        self.assertTrue(any("install.sh" in str(a) for c in calls for a in c),
                        "install.sh is part of the transaction, not an afterthought")
        self.assertEqual(km._install_failed_sha(), "", "a passing install spends the latch")
        merge = next(a for a in calls if a[0] == "git" and "merge" in a and "merge-base" not in a)
        self.assertIn(REMOTE, merge, "the merge binds to the pinned validated sha")
        self.assertNotIn("FETCH_HEAD", merge,
                         "never mutable FETCH_HEAD — a concurrent fetch rewrites it "
                         "(the user's audit, 2026-08-18)")
        anc = next(a for a in calls if a[0] == "git" and "merge-base" in a)
        self.assertNotIn("FETCH_HEAD", anc)

    def test_a_failed_install_after_the_pull_keeps_the_latch_and_says_so(self):
        self._wire(install_rc=1)
        ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("install.sh failed", detail)
        self.assertEqual(km._install_failed_sha(), km._sha8(REMOTE),
                         "armed — nothing restarts onto it until install passes")

    def test_a_held_update_lock_refuses_the_pull(self):
        self._wire()
        fd = km._update_flock()
        self.assertIsNotNone(fd)
        try:
            ok, detail = km._pull_remote("TESTHOST")
        finally:
            km.os.close(fd)
        self.assertFalse(ok)
        self.assertIn("another update is already running", detail)

    def test_a_prior_latch_heals_inside_the_pull_and_a_divergence_under_the_lock_refuses(self):
        # settle and the ancestry re-check both run UNDER the lock (the adversarial review,
        # 2026-08-19: neither had executing coverage). Stateful fake: the first is-ancestor
        # (pre-lock) passes, the second (under-lock) fails → the pull refuses, nothing armed.
        calls = self._wire()
        anc = {"n": 0}
        orig = km.subprocess.run

        def flaky(argv, **kw):
            if argv[0] == "git" and "merge-base" in argv:
                anc["n"] += 1
                if anc["n"] == 2:
                    return _R(rc=1)
            return orig(argv, **kw)
        km.subprocess.run = flaky
        ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("diverged while waiting", detail)
        self.assertEqual(km._install_latch_lines(), [], "nothing armed on an under-lock refusal")
        # settle-heal: a prior latch matching HEAD is healed inside the locked pull, then the
        # transaction proceeds
        self._wire()
        km._set_install_failed(km._sha8(LOCAL))
        with mock.patch.object(km, "_checkout_sha", return_value=km._sha8(LOCAL)):
            ok, detail = km._pull_remote("TESTHOST")
        self.assertTrue(ok, detail)
        self.assertEqual(km._install_latch_lines(), [], "healed and the new install spent it all")

    def test_a_trust_downgrade_landing_during_settle_wins_before_the_merge(self):
        # settle can run install.sh for minutes; a downgrade in that window must win before the
        # code-execution boundary (the user's audit, 2026-08-19)
        self._wire()
        km._set_install_failed(km._sha8(LOCAL))
        def degrading_install(sha8, lock_fd=None):
            km._remotes["TESTHOST"]["trust"] = "directed"   # the downgrade lands mid-settle
            return True
        with mock.patch.object(km, "_checkout_sha", return_value=km._sha8(LOCAL)), \
             mock.patch.object(km, "_converge_install", side_effect=degrading_install):
            ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("trust changed", detail)

    def test_a_trust_downgrade_landing_at_the_merge_QUARANTINES_without_any_reset(self):
        # the v1.3.8 audit closed the rollback for good: the cleanliness re-check and the reset
        # were separate processes, and an edit STAGED between them was silently discarded — no
        # lock of ours serializes another session's git add, so no check-then-reset can be made
        # safe. A downgrade landing at the move now quarantines: HEAD stays, gated by the stuck
        # NON-HEX form, and the human who made the trust call finishes it by hand.
        calls = self._wire()
        orig = km.subprocess.run

        def degrading_merge(argv, **kw):
            if argv[0] == "git" and "merge" in argv and "merge-base" not in argv:
                km._remotes["TESTHOST"]["trust"] = "directed"   # lands exactly at the move
            return orig(argv, **kw)
        km.subprocess.run = degrading_merge
        ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("quarantined", detail)
        self.assertIn("by hand", detail)
        self.assertFalse(any(a[0] == "git" and "reset" in a for a in calls),
                         "NO reset, ever: an auto-rewind destroyed staged peer work in every "
                         "form it was tried (--hard, --keep, checked-then-reset)")
        self.assertFalse(any("install.sh" in str(a) for c in calls for a in c),
                         "nothing installs code the trust decision rejected")
        self.assertEqual(km._install_latch_lines(), ["quaranti", "quaranti"],
                         "two NON-HEX lines no checkout sha can match — hex sentinels were "
                         "minable by the very peer the quarantine exists to stop")
        # the healers really DO refuse it — including HEADs mined onto the OLD hex sentinels
        km.subprocess.run = orig
        for head in (km._sha8(REMOTE), "00000000", "ffffffff"):
            with mock.patch.object(km, "_checkout_sha", return_value=head):
                with mock.patch.object(km, "_converge_install",
                                       side_effect=AssertionError("the quarantine must not auto-heal")):
                    with mock.patch.object(km, "_converge_note"):
                        km._boot_heal()
            self.assertEqual(km._install_latch_lines(), ["quaranti", "quaranti"],
                             "the quarantine survives the heal pass — human hands only")

    def test_a_trust_downgrade_during_install_quarantines_before_anything_serves(self):
        # the v1.3.8 audit: a downgrade landing while install.sh RUNS still installed and would
        # then serve the peer build. _pull_remote never restarts, so the post-install quarantine
        # still precedes any execution of the new build as romp.
        self._wire()

        def popen_flip(argv, **kw):
            proc = mock.MagicMock()

            def communicate(timeout=None):
                km._remotes["TESTHOST"]["trust"] = "directed"   # lands while install.sh runs
                return ("", None)
            proc.communicate.side_effect = communicate
            proc.returncode = 0
            return proc
        km.subprocess.Popen = popen_flip
        ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("while install.sh ran", detail)
        self.assertEqual(km._install_latch_lines(), ["quaranti", "quaranti"],
                         "quarantined before anything serves")
        # and when the install FAILED with the downgrade landing mid-run, the armed latch (the
        # auto-heal trigger) must not survive either
        km._remotes["TESTHOST"]["trust"] = "trusted"
        self._wire()

        def popen_flip_fail(argv, **kw):
            proc = mock.MagicMock()

            def communicate(timeout=None):
                km._remotes["TESTHOST"]["trust"] = "directed"
                return ("boom", None)
            proc.communicate.side_effect = communicate
            proc.returncode = 1
            return proc
        km.subprocess.Popen = popen_flip_fail
        with mock.patch.object(km, "_converge_note"):
            ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertEqual(km._install_latch_lines(), ["quaranti", "quaranti"],
                         "a failed install plus a downgrade is still a quarantine, never the "
                         "auto-heal form")

    def test_the_pull_refuses_while_a_stable_switch_is_pending(self):
        # the adversarial review, 2026-08-21: the converge refused to cross a pending
        # switch-to-stable while this pull checked out unsigned peer commits right across it
        calls = self._wire()
        with mock.patch.object(km, "_settle_prior_latch", return_value=("", "0ddba11d stable")):
            ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("STABLE channel is still pending", detail)
        self.assertFalse(any(a[0] == "git" and "merge" in a and "merge-base" not in a
                             for a in calls), "nothing merged across the pending choice")
        self.assertFalse(any("install.sh" in str(a) for c in calls for a in c))

    def test_the_pull_refuses_on_a_stable_channel_absolutely(self):
        # the v1.3.12 audit's P1, hardened by its own review: the common crashed-switch form has
        # the marker ALREADY stable (bootstrap publishes before install.sh), so a transition
        # predicate certified a closure that never landed. The rule is absolute — a stable
        # machine never pulls unsigned peer commits — matching the wrapper and the converge.
        calls = self._wire()
        state = {"settled": False}

        def channel():
            return "stable" if state["settled"] else "dev"

        def settle(fd):
            state["settled"] = True                    # the heal just published the switch
            return "", ""
        self._chan_patch.stop()
        try:
            # form 1: the settle completes the switch mid-pull
            with mock.patch.object(km, "_update_channel", side_effect=channel):
                with mock.patch.object(km, "_settle_prior_latch", side_effect=settle):
                    ok, detail = km._pull_remote("TESTHOST")
            self.assertFalse(ok)
            self.assertIn("STABLE channel", detail)
            # form 2: the marker was ALREADY stable at entry (the common crash form)
            calls2 = self._wire()
            with mock.patch.object(km, "_update_channel", return_value="stable"):
                with mock.patch.object(km, "_settle_prior_latch", return_value=("", "")):
                    ok, detail = km._pull_remote("TESTHOST")
            self.assertFalse(ok)
            self.assertIn("STABLE channel", detail)
        finally:
            self._chan_patch = mock.patch.object(km, "_update_channel", return_value="dev")
            self._chan_patch.start()
        for got in (calls, calls2):
            self.assertFalse(any(a[0] == "git" and "merge" in a and "merge-base" not in a
                                 for a in got), "nothing merged onto the stable machine")
            self.assertFalse(any("install.sh" in str(a) for c in got for a in c))

    def test_the_pull_refuses_a_tree_dirtied_during_the_settle(self):
        # the v1.3.12 audit's P2: the entry clean-check is minutes stale after a long prior
        # heal — edits saved meanwhile get the same refusal the entry gives them
        self._wire()
        state = {"settled": False}
        orig = km.subprocess.run

        def dirty_after_settle(argv, **kw):
            if argv[0] == "git" and "status" in argv and state["settled"]:
                return _R(out=" M kernel/edited-during-heal.py")
            return orig(argv, **kw)

        def settle(fd):
            state["settled"] = True
            return "", ""
        km.subprocess.run = dirty_after_settle
        with mock.patch.object(km, "_settle_prior_latch", side_effect=settle):
            ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("changed while settling", detail)

    def test_a_downgrade_at_the_install_launch_never_spawns(self):
        # the v1.3.9 audit: the final trust check and the spawn were separate steps, and a
        # downgrade in the scheduling gap still ran the peer's install.sh. The spawn now happens
        # INSIDE the same _remotes_lock hold as the check — downgrades are written under that
        # lock, so check+launch are linearized and the process is never created.
        calls = self._wire()

        merged = self.merged

        class FlippingRemotes(dict):
            """the downgrade lands exactly between the post-merge trust check (the FIRST read
            after the merge ran) and the launch hold (the second) — keyed on the merge event,
            not a brittle absolute count."""

            def __init__(self, src):
                super().__init__(src)
                self.post = 0

            def get(self, k, d=None):
                v = super().get(k, d)
                if k == "TESTHOST" and v and merged["v"]:
                    self.post += 1
                    if self.post >= 2:
                        v = dict(v, trust="directed")
                return v
        with mock.patch.object(km, "_remotes", FlippingRemotes(km._remotes)):
            ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("at the install launch", detail)
        self.assertFalse(any("install.sh" in str(a) for c in calls for a in c),
                         "the process was never created — that is the whole point")
        self.assertEqual(km._install_latch_lines(), ["quaranti", "quaranti"])

    def test_a_failed_quarantine_write_falls_back_to_in_place_and_then_says_so(self):
        # the atomic quarantine write can fail on the same degraded fs (correlated); rewriting
        # the EXISTING latch in place via UNBUFFERED pwrite survives ENOSPC and cannot truncate.
        self._wire()
        orig = km.subprocess.run

        def degrading(argv, **kw):
            if argv[0] == "git" and "merge" in argv and "merge-base" not in argv:
                km._remotes["TESTHOST"]["trust"] = "directed"
            return orig(argv, **kw)
        km.subprocess.run = degrading
        with mock.patch.object(km, "_set_install_failed", return_value=False):
            ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("quarantined", detail)
        self.assertEqual(km._install_latch_lines(), ["quaranti", "quaranti"],
                         "the in-place rewrite lands the quarantine when the atomic write cannot")
        # and when even THAT fails, the detail must stop claiming quarantine — and the ARMED
        # record must SURVIVE (a truncating fallback left an EMPTY latch that every reader
        # moot-removed, serving the rejected build with install.sh never run)
        km._set_install_failed(km._sha8(REMOTE))       # re-arm the pre-quarantine state
        km._remotes["TESTHOST"]["trust"] = "trusted"   # leg 1's downgrade must not gate leg 2
        self._wire()
        km.subprocess.run = degrading
        with mock.patch.object(km, "_set_install_failed", return_value=False):
            with mock.patch.object(km.os, "pwrite",
                                   side_effect=OSError(28, "No space left on device")):
                ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertNotIn("is quarantined", detail)
        self.assertIn("could not be written", detail)
        self.assertIn("REVIVE", detail,
                      "the one thing the user must know: this state can revive the merged build")
        self.assertEqual(km._install_latch_lines(), [km._sha8(REMOTE)],
                         "the ARMED record survives a failed quarantine write")

    def test_an_unpersistable_intent_blocks_the_merge(self):
        calls = self._wire()
        from unittest import mock
        with mock.patch.object(km, "_arm_latch", return_value=False):
            ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("install intent", detail)
        self.assertFalse(any(a[0] == "git" and "merge" in a and "merge-base" not in a
                             for a in calls), "HEAD never moved")

    def test_divergence_is_refused_loudly(self):
        self._wire(ancestor_rc=1)
        ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("diverged", detail)

    def test_already_up_to_date_short_circuits(self):
        km._remotes["TESTHOST"]["kernel_sha"] = LOCAL
        self._wire(rhead=LOCAL)
        ok, detail = km._pull_remote("TESTHOST")
        self.assertTrue(ok)
        self.assertIn("already up to date", detail)

    def test_expected_polled_sha_must_still_match_discovery(self):
        calls = self._wire(rhead="c" * 40)
        ok, detail = km._pull_remote("TESTHOST", expected_sha=REMOTE)
        self.assertFalse(ok)
        self.assertIn("changed build after it was polled", detail)
        self.assertFalse(any(a[0] == "git" and "fetch" in a for a in calls),
                         "a changed source is refused before fetching")

    def test_fetch_head_must_equal_the_discovered_and_expected_sha(self):
        calls = self._wire(rhead=REMOTE, fetched="c" * 40)
        ok, detail = km._pull_remote("TESTHOST", expected_sha=REMOTE)
        self.assertFalse(ok)
        self.assertIn("changed HEAD during the pull", detail)
        self.assertFalse(any(a[0] == "git" and "merge" in a for a in calls),
                         "a moving remote HEAD is never merged")

    def test_trust_is_unconditionally_required_before_touching_git_or_ssh(self):
        km._remotes["TESTHOST"]["trust"] = "directed"
        calls = self._wire()
        ok, detail = km._pull_remote("TESTHOST", expected_sha=REMOTE)
        self.assertFalse(ok)
        self.assertIn("not trusted", detail)
        self.assertEqual(calls, [])

    def test_short_pin_resolves_to_exact_commit_before_discovery(self):
        calls = self._wire()
        ok, detail = km._pull_remote("TESTHOST", expected_sha=REMOTE[:7])
        self.assertTrue(ok, detail)
        resolve = next(a for a in calls if a[0] == "git" and str(a[-1]).endswith("^{commit}"))
        self.assertEqual(resolve[-1], REMOTE[:7] + "^{commit}")

    def test_same_prefix_but_different_full_commit_is_refused(self):
        impostor = REMOTE[:7] + ("c" * 33)
        calls = self._wire(rhead=impostor, resolved=REMOTE)
        ok, detail = km._pull_remote("TESTHOST", expected_sha=REMOTE[:7])
        self.assertFalse(ok)
        self.assertIn("changed build after it was polled", detail)
        self.assertFalse(any(a[0] == "git" and "fetch" in a for a in calls),
                         "prefix agreement is not an executable-code pin")

    def test_unresolvable_short_pin_is_refused_before_ssh(self):
        calls = self._wire(resolved="")
        ok, detail = km._pull_remote("TESTHOST", expected_sha=REMOTE[:7])
        self.assertFalse(ok)
        self.assertIn("exact, unambiguous", detail)
        self.assertFalse(any(a[0] != "git" for a in calls), "no remote discovery before pin resolution")

    def test_trust_downgrade_during_fetch_wins_before_merge(self):
        calls = []

        def fake(argv, **kw):
            calls.append(argv)
            if argv[0] == "git" and "status" in argv:
                return _R()
            if argv[0] == "git" and "fetch" in argv:
                km._remotes["TESTHOST"]["trust"] = "directed"
                return _R()
            if argv[0] == "git" and "merge-base" in argv:
                return _R()
            if argv[0] == "git" and "rev-list" in argv:
                return _R(out="1")
            if argv[0] == "git" and "rev-parse" in argv:
                return _R(out=REMOTE if "FETCH_HEAD" in argv else "bbbbbbb")
            if "for d in" in argv[-1]:
                return _R(out="DIR:/home/u/romp\nHEAD:%s\nDIRTY:" % REMOTE)
            return _R()

        km.subprocess.run = fake
        ok, detail = km._pull_remote("TESTHOST", expected_sha=REMOTE)
        self.assertFalse(ok)
        self.assertIn("trust changed", detail)
        self.assertFalse(any(a[0] == "git" and "merge" in a for a in calls),
                         "a concurrent downgrade must win at the executable-code boundary")


class AutoPullFiring(unittest.TestCase):
    """The supervisor hook fires the pull only for a TRUSTED remote strictly ahead, local checkout on
    main — and picks the pull worker, not the push one."""

    def setUp(self):
        self._prev = km._auto_update_remotes_on()
        km._set_auto_update_remotes(True)
        km._auto_push.clear()
        km._auto_push_tried.clear()
        self._saved = (km._remote_out_of_date, km._behind_info, km._local_head, km._local_branch)
        km._remote_out_of_date = lambda r: True
        km._behind_info = lambda sha: {"behind": 0, "ahead": 2, "date": ""}   # strictly ahead → pull side
        km._local_head = lambda short=False: (LOCAL[:8] if short else LOCAL)
        km._local_branch = lambda: "main"
        self.calls = []

    def tearDown(self):
        (km._remote_out_of_date, km._behind_info, km._local_head, km._local_branch) = self._saved
        km._set_auto_update_remotes(self._prev)
        km._auto_push.clear()
        km._auto_push_tried.clear()

    def _run(self, row):
        saved = km.threading.Thread
        calls = self.calls

        class _T:
            def __init__(self, target=None, args=(), daemon=None):
                self._t, self._a = target, args

            def start(self):
                calls.append((self._t.__name__, self._a[0]))
        km.threading.Thread = _T
        try:
            km._maybe_auto_push(row)
        finally:
            km.threading.Thread = saved

    def test_a_trusted_ahead_remote_fires_one_pull(self):
        self._run({"host": "TESTHOST", "kernel_sha": REMOTE, "trust": "trusted"})
        self.assertEqual(self.calls, [("_auto_pull_remote", "TESTHOST")])

    def test_a_directed_remote_is_never_auto_pulled(self):
        # pulling runs THEIR code here; that is exactly what the trusted tier means and directed does not
        self._run({"host": "TESTHOST", "kernel_sha": REMOTE, "trust": "directed"})
        self._run({"host": "TESTHOST", "kernel_sha": REMOTE})
        self.assertEqual(self.calls, [])

    def test_off_main_is_never_auto_pulled(self):
        # feature branches are mid-thought; they move when their owner says so
        km._local_branch = lambda: "somefeature"
        self._run({"host": "TESTHOST", "kernel_sha": REMOTE, "trust": "trusted"})
        self.assertEqual(self.calls, [])
        km._local_branch = lambda: ""              # detached HEAD (a release checkout)
        self._run({"host": "TESTHOST", "kernel_sha": REMOTE, "trust": "trusted"})
        self.assertEqual(self.calls, [])

    def test_a_checked_in_row_never_fires_this_machine_s_ssh(self):
        # no ssh route from here — a push or pull of OURS could only manufacture "No route to host"
        self._run({"host": "TESTHOST", "kernel_sha": REMOTE, "trust": "trusted", "checkin_peer": True})
        self.assertEqual(self.calls, [], "strictly ahead + no route: nothing this machine can run")

    def test_a_checked_in_peer_that_is_BEHIND_is_asked_to_update_itself(self):
        # the third direction (the user 2026-07-28): the peer owns the only ssh between the machines, so
        # the fast-forward is driven through the tunnel IT holds instead of offered as an impossible push
        km._behind_info = lambda sha: {"behind": 2, "ahead": 0, "date": ""}
        self._run({"host": "TESTHOST", "kernel_sha": REMOTE, "trust": "trusted", "checkin_peer": True})
        self.assertEqual(self.calls, [("_auto_ask_peer", "TESTHOST")])

    def test_a_checked_in_peer_is_never_driven_on_an_unproven_relationship(self):
        # same bar as the push gate: diverged, or a build this repo has never seen, is not driven at all
        for drift in ({"behind": 2, "ahead": 1, "date": ""}, {"behind": None, "ahead": None, "date": ""}):
            km._auto_push_tried.clear()
            km._behind_info = lambda sha, d=drift: d
            self._run({"host": "TESTHOST", "kernel_sha": REMOTE, "trust": "trusted", "checkin_peer": True})
        self.assertEqual(self.calls, [])

    def test_the_same_advance_is_not_pulled_twice(self):
        row = {"host": "TESTHOST", "kernel_sha": REMOTE, "trust": "trusted"}
        self._run(row)
        self._run(row)
        self.assertEqual(len(self.calls), 1, "one attempt per (remote sha, local HEAD)")

    def test_a_pulled_phase_survives_the_drift_clearing(self):
        # after a pull the drift clears at once (HEAD moved) but the RUNNING kernel is the old build —
        # the 'pulled … restart romp' trace must outlive the clearing event, until the restart itself
        km._set_auto_push("TESTHOST", "pulled", "pulled 2 commits from TESTHOST — restart romp to run it")
        km._remote_out_of_date = lambda r: False
        self._run({"host": "TESTHOST", "kernel_sha": REMOTE, "trust": "trusted"})
        st = km._auto_push_state("TESTHOST")
        self.assertEqual((st or {}).get("phase"), "pulled")

    def test_the_row_publishes_the_fast_pull_verdict(self):
        pub = km._remote_public({"host": "TESTHOST", "kernel_port": 29855, "local_port": 8801,
                                 "token": "t", "status": "up", "sids": [], "kernel_sha": REMOTE,
                                 "trust": "trusted"})
        self.assertTrue(pub["fastPull"])
        self.assertFalse(pub["fastForward"])


class PullRoute(unittest.TestCase):
    def test_the_popover_wires_a_pull_button_to_the_route(self):
        src = km._LANDING_REMOTES_JS
        self.assertIn("data-p=", src, "a Pull control keyed by host")
        self.assertIn("/tunnels/pull", src)
        self.assertIn("t.fastPull", src, "offered exactly when the pull is a provable fast-forward")
        self.assertIn("checkinPeer", src, "no Push/Pull dead-ends on a host with no ssh path")


if __name__ == "__main__":
    unittest.main()
