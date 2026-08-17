#!/usr/bin/env python3
"""The mesh/origin-aware update notice (the user 2026-08-14): the release banner only watched TAGS, so
ordinary merges sat undeployed with every machine reading "in sync" — all equally stale. The kernel now
also compares origin/main vs the checkout vs the RUNNING build, fires the same banner (kind:"main"),
and converges on click or unattended per the update mode. Pure verdict unit-tested; the wiring pinned
by source (the rail-spend pattern). Synthetic only; hermetic state dir."""
import inspect
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
os.environ["ROMP_MANAGER_PORT"] = "1"   # dead port: an unstubbed converge dials nothing real
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel_drift", os.path.join(BIN, "romp-kernel")).load_module()


OA = "aaaa1111" + "e" * 32          # origin/main FULL sha (the verdict binds the pull to all 40)
OB = "bbbb2222" + "e" * 32


class DriftVerdict(unittest.TestCase):
    def test_origin_ahead_of_the_checkout_wants_a_pull_bound_to_the_full_sha(self):
        self.assertEqual(km._main_drift_verdict(OA, "bbbb2222", "bbbb2222"), ("pull", OA))

    def test_checkout_ahead_of_the_running_kernel_wants_a_restart(self):
        # the hand-advanced case: updated code sits on disk, nothing booted it
        self.assertEqual(km._main_drift_verdict(OA, "aaaa1111", "cccc3333"), ("restart", "aaaa1111"))

    def test_pull_outranks_restart_when_both_hold(self):
        # origin ahead AND the running build stale: one pull converges both in a single bounce
        self.assertEqual(km._main_drift_verdict(OA, "bbbb2222", "cccc3333")[0], "pull")

    def test_in_sync_is_quiet(self):
        # origin is full, the checkout short: same commit must read as agreement, not drift
        self.assertEqual(km._main_drift_verdict(OA, "aaaa1111", "aaaa1111"), ("", ""))

    def test_unknown_shas_never_invent_a_notice(self):
        # offline ls-remote, unreadable checkout, missing running sha: unknown, not a disagreement
        self.assertEqual(km._main_drift_verdict("", "bbbb2222", "bbbb2222"), ("", ""))
        self.assertEqual(km._main_drift_verdict(OA, "", "bbbb2222"), ("", ""))
        self.assertEqual(km._main_drift_verdict("", "", ""), ("", ""))


class DriftWiring(unittest.TestCase):
    def test_off_mode_silences_the_watcher_and_auto_converges_unattended(self):
        src = inspect.getsource(km._main_drift_check)
        self.assertIn('if _update_mode() == "off":', src)
        self.assertIn('if _update_mode() == "auto":', src)
        self.assertIn("_run_main_update(kind, target=target)", src,
                      "even unattended, the converge is bound to the sha this pass advertised")
        self.assertIn('"kind": "main"', src, "ask mode fires the shared banner with the drift variant")

    def test_the_default_channel_is_stable_and_only_dev_reads_as_dev(self):
        # the CHANNEL is persisted separately from signature policy: keying convergence off the
        # trust root left default no-trust-root installs silently tracking unsigned main (the
        # user's audit, 2026-08-17). Absent, unreadable, or garbage all read STABLE.
        ch = km.jd.STATE / "update-channel"
        ch.unlink(missing_ok=True)
        self.assertEqual(km._update_channel(), "stable", "absent file → stable, never dev")
        ch.write_text("banana\n")
        self.assertEqual(km._update_channel(), "stable", "garbage → stable")
        ch.write_text("dev\n")
        self.assertEqual(km._update_channel(), "dev")
        ch.unlink(missing_ok=True)

    def test_a_stable_install_never_probes_or_pulls_main(self):
        # stable means code moves ONLY via signed release tags: the check must not even ls-remote
        # origin (no probe → no pull verdict → no banner, no auto converge), and the pull step
        # itself refuses as defence in depth (the user's audits, 2026-08-17)
        src = inspect.getsource(km._main_drift_check)
        self.assertIn('_origin_main_sha() if _update_channel() == "dev" else ""', src)
        self.assertIn("stable channel", inspect.getsource(km._run_main_update_locked))
        probes = []
        saved = (km._update_mode, km._origin_main_sha, km._checkout_sha, km._kernel_sha,
                 km._MAIN_DRIFT[0], km._MAIN_DRIFT[1])
        km._update_mode = lambda: "ask"
        km._origin_main_sha = lambda: probes.append(1) or OA
        km._checkout_sha = lambda: "bbbb2222"
        km._kernel_sha = lambda: "bbbb2222"
        try:
            km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""
            (km.jd.STATE / "update-channel").unlink(missing_ok=True)   # default = stable
            km._main_drift_check()
            self.assertEqual(probes, [], "a stable install never dials origin")
            self.assertEqual(km._MAIN_DRIFT[0], "", "and no pull offer is ever latched")
        finally:
            (km._update_mode, km._origin_main_sha, km._checkout_sha, km._kernel_sha) = saved[:4]
            km._MAIN_DRIFT[0], km._MAIN_DRIFT[1] = saved[4], saved[5]

    def test_the_restart_half_still_fires_on_stable(self):
        # restart-drift moves no code — it runs what is already on disk (a landed tag update whose
        # bounce died). Functional: stable (default) channel, checkout ahead of running → restart.
        ran = []
        saved = (km._update_mode, km._checkout_sha, km._kernel_sha,
                 km._run_main_update, km._LAST_AUTO_CONVERGE[0], km._MAIN_DRIFT[0], km._MAIN_DRIFT[1])
        km._update_mode = lambda: "auto"
        km._checkout_sha = lambda: "bbbb2222"
        km._kernel_sha = lambda: "cccc3333"
        km._run_main_update = lambda kind, immediate=False, target="": ran.append((kind, target))
        try:
            km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""
            km._LAST_AUTO_CONVERGE[0] = 0.0
            (km.jd.STATE / "update-channel").unlink(missing_ok=True)   # default = stable
            km._main_drift_check()
            self.assertEqual(ran, [("restart", "bbbb2222")])
        finally:
            (km._update_mode, km._checkout_sha, km._kernel_sha,
             km._run_main_update) = saved[:4]
            km._LAST_AUTO_CONVERGE[0] = saved[4]
            km._MAIN_DRIFT[0], km._MAIN_DRIFT[1] = saved[5], saved[6]

    def test_the_pull_step_verifies_every_precondition_and_says_why_not(self):
        # the first cut checked none of these (the user's audit, 2026-08-17): a quiet fetch failure
        # re-checked-out the stale ref, any differing sha was "an update" (older/diverged included),
        # the checkout was never bound to the advertised sha, install.sh never ran, and the restart
        # POST carried no manager token (every converge ended 401 with the processes still stale).
        # ConvergeFunctional below DRIVES these; the pins here just keep the spellings honest.
        src = (inspect.getsource(km._run_main_update) + inspect.getsource(km._run_main_update_locked)
               + inspect.getsource(km._converge_install))
        self.assertIn('"status", "--porcelain"', src)
        self.assertIn("uncommitted work", src, "the refusal names the real problem")
        self.assertIn("if f.returncode != 0:", src, "a failed fetch aborts loudly, never re-checks-out stale refs")
        self.assertIn("tip != target", src, "the move is bound to the sha the verdict advertised")
        self.assertIn('"merge-base", "--is-ancestor", "HEAD", tip', src,
                      "a diverged or rewound origin/main is refused, named")
        self.assertIn('"checkout", "--detach", tip', src, "the checkout lands on the BOUND sha, not a ref")
        self.assertIn('install.sh', src, "new code with stale deps/build is not an update")
        self.assertIn('"X-Romp-Manager-Token"', src, "the restart request authenticates to the manager")
        self.assertIn('_MAIN_DRIFT[0] = ""', src, "every refusal re-arms the notice")

    def test_the_click_converges_immediately_and_auto_rides_the_quiet_window(self):
        src = inspect.getsource(km._run_main_update_locked)
        self.assertIn('"" if immediate else "?when=quiet"', src)
        route = inspect.getsource(km)
        self.assertIn('threading.Thread(target=_run_main_update, args=(kind, True, converge_target)',
                      route, "the banner click is the user's own deliberate cut, bound to the offer")

    def test_auto_converges_batch_behind_the_cool_down(self):
        # the user 2026-08-15, after flipping auto: main took a merge every few minutes and auto mode
        # converged once per commit — 4+ restarts/hour, each cutting every in-flight turn. Behind the
        # cool-down, N merges inside the window become ONE restart to the LATEST sha.
        ran = []
        saved = (km._update_mode, km._origin_main_sha, km._checkout_sha, km._kernel_sha,
                 km._run_main_update, km._LAST_AUTO_CONVERGE[0], km._MAIN_DRIFT[0], km._MAIN_DRIFT[1])
        km._update_mode = lambda: "auto"
        (km.jd.STATE / "update-channel").write_text("dev\n")   # only the dev channel pulls main
        self.addCleanup(lambda: (km.jd.STATE / "update-channel").unlink(missing_ok=True))
        km._checkout_sha = lambda: "aaa"
        km._kernel_sha = lambda: "aaa"
        km._run_main_update = lambda kind, immediate=False, target="": ran.append(kind)
        try:
            km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""
            km._LAST_AUTO_CONVERGE[0] = 0.0
            km._origin_main_sha = lambda: "bbb"
            km._main_drift_check()
            self.assertEqual(ran, ["pull"], "the first drift converges at once")
            km._origin_main_sha = lambda: "ccc"          # a new merge lands inside the window
            km._main_drift_check()
            self.assertEqual(ran, ["pull"], "inside the cool-down, no second restart")
            self.assertEqual(km._MAIN_DRIFT[0], "", "the deferred sha is NOT marked offered")
            km._LAST_AUTO_CONVERGE[0] = 0.0              # the window passes
            km._main_drift_check()
            self.assertEqual(ran, ["pull", "pull"], "past the window, one converge takes the LATEST sha")
        finally:
            (km._update_mode, km._origin_main_sha, km._checkout_sha, km._kernel_sha,
             km._run_main_update) = saved[:5]
            km._LAST_AUTO_CONVERGE[0] = saved[5]
            km._MAIN_DRIFT[0], km._MAIN_DRIFT[1] = saved[6], saved[7]

    def test_the_shell_banner_carries_the_drift_variants(self):
        src = inspect.getsource(km)
        self.assertIn("m.drift||''", src, "the shell relay forwards the drift kind")
        self.assertIn("new romp commits are on main", src)
        self.assertIn("is ready on disk", src)

    def test_the_route_acts_only_on_what_the_kernel_itself_found(self):
        src = inspect.getsource(km)
        self.assertIn('d0, d1 = _MAIN_DRIFT[0], _MAIN_DRIFT[1]', src,
                      "one snapshot: a re-read could pair a pull with an emptied target")
        self.assertIn('kind = "pull" if d0 else ("restart" if d1 else "")', src,
                      "no version or kind is ever taken from the client")


class ConvergeFunctional(unittest.TestCase):
    """The pull step, DRIVEN, not source-matched (the adversarial review, 2026-08-17: the wiring
    pins above stay green if a guard loses its `return` or the steps reorder). A scripted
    subprocess fake records every call; each refusal must stop the sequence where it claims to,
    and the happy path must run the steps in order and check out the BOUND sha."""

    FULL = "f" * 40
    OTHER = "0" * 40

    def setUp(self):
        self._saved = (km._MAIN_DRIFT[0], km._MAIN_DRIFT[1], km._CONVERGE_STATE[0])
        km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""
        km._CONVERGE_STATE[0] = ""
        km._set_install_failed("")
        (km.jd.STATE / "update-channel").write_text("dev\n")   # only dev converges; per-test default
        self.notices = []
        self.requests = []

    def tearDown(self):
        (km._MAIN_DRIFT[0], km._MAIN_DRIFT[1], km._CONVERGE_STATE[0]) = self._saved
        km._set_install_failed("")
        (km.jd.STATE / "update-channel").unlink(missing_ok=True)

    def _run(self, kind="pull", target=None, fail=(), tip=None, channel=None):
        """Drive _run_main_update with every subprocess scripted. `fail` names steps that exit 1."""
        import subprocess as sp
        target = self.FULL if target is None else target
        tip = self.FULL if tip is None else tip
        calls = []
        requests = self.requests

        def fake_run(argv, **kw):
            step = ("install" if any("install.sh" in str(a) for a in argv) else
                    "status" if "status" in argv else "fetch" if "fetch" in argv else
                    "rev-parse" if "rev-parse" in argv else
                    "merge-base" if "merge-base" in argv else
                    "checkout" if "checkout" in argv else "other")
            calls.append((step, list(argv)))
            rc = 1 if step in fail else 0
            out = tip + "\n" if step == "rev-parse" else ""
            if step == "status" and step in fail:
                rc, out = 0, "M peer-session-edit.py\n"   # "failing" status = a DIRTY tree answer
            return sp.CompletedProcess(argv, rc, stdout=out, stderr="boom" if rc else "")

        class FakeConn:
            # the restart POST is DIRECT http.client (urllib honors HTTP_PROXY and leaked the
            # manager token to a configured proxy — the user's audit, 2026-08-17)
            def __init__(self, host, port, timeout=0):
                self.host = host

            def request(self, method, path, headers=None):
                requests.append((method, path, dict(headers or {})))

            def getresponse(self):
                r = mock.MagicMock()
                r.status = 200
                return r

            def close(self):
                pass

        if channel is not None:
            (km.jd.STATE / "update-channel").write_text(channel + "\n")
        env = dict(km.os.environ, ROMP_MANAGER_TOKEN="tok-abc12", ROMP_MANAGER_PORT="1",
                   HTTP_PROXY="http://127.0.0.1:9")   # a proxy that must NEVER be consulted
        with mock.patch.object(km.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(km, "_sync_notice",
                               side_effect=lambda m, ok=True: self.notices.append(m)), \
             mock.patch.dict(km.os.environ, env, clear=True), \
             mock.patch.object(km.http.client, "HTTPConnection", FakeConn):
            km._run_main_update(kind, target=target)
        return [s for s, _ in calls], calls

    def test_happy_path_runs_in_order_and_checks_out_the_bound_sha(self):
        steps, calls = self._run()
        self.assertEqual(steps, ["status", "fetch", "rev-parse", "merge-base", "checkout", "install"])
        checkout_argv = next(a for s, a in calls if s == "checkout")
        self.assertIn(self.FULL, checkout_argv, "the checkout lands on the resolved+bound sha")
        self.assertNotIn("origin/main", checkout_argv, "never the ref — it can move under us")
        self.assertEqual(len(self.requests), 1)
        method, path, headers = self.requests[0]
        self.assertEqual((method, path), ("POST", "/restart-all?when=quiet"),
                         "an unattended converge rides the quiet window")
        self.assertEqual(headers.get("X-Romp-Manager-Token"), "tok-abc12",
                         "the restart authenticates to the manager, proxy env notwithstanding")

    def test_each_refusal_stops_the_sequence_and_restarts_nothing(self):
        for fail_step, last_expected in (("status", "status"), ("fetch", "fetch"),
                                         ("merge-base", "merge-base"), ("checkout", "checkout")):
            self.setUp()
            steps, _ = self._run(fail=(fail_step,))
            self.assertEqual(steps[-1], last_expected, fail_step)
            self.assertNotIn("install", steps, fail_step)
            self.assertEqual(self.requests, [], "%s: a refused pull must restart nothing" % fail_step)

    def test_a_moved_or_unbound_tip_never_reaches_checkout(self):
        steps, _ = self._run(tip=self.OTHER)          # main moved between verdict and fetch
        self.assertNotIn("checkout", steps)
        self.setUp()
        steps, _ = self._run(target="")               # binding is mandatory, not optional-by-default
        self.assertNotIn("checkout", steps)
        self.assertEqual(self.requests, [])

    def test_the_stable_channel_stops_the_pull_before_any_subprocess(self):
        steps, _ = self._run(channel="stable")
        self.assertEqual(steps, [])
        self.assertTrue(any("stable channel" in n for n in self.notices))
        self.assertIn(km._CONVERGE_ERROR[0], self.notices[-1],
                      "the refusal parks where /update-check's poll reads it — the banner unsticks")

    def test_a_failed_install_latches_DURABLY_and_the_restart_half_heals_it(self):
        steps, _ = self._run(fail=("install",))
        self.assertEqual(steps[-1], "install")
        self.assertEqual(self.requests, [], "no restart onto a build whose install failed")
        self.assertEqual(km._install_failed_sha(), "f" * 8)
        self.assertEqual((km.jd.STATE / "converge-install-failed").read_text().strip(), "f" * 8,
                         "the latch survives a process death — it lives in STATE, not memory")
        with mock.patch.object(km, "_checkout_sha", return_value="f" * 8):
            steps, _ = self._run(kind="restart")
        self.assertEqual(steps, ["install"], "the restart half retries the install first")
        self.assertEqual(km._install_failed_sha(), "", "a passing install spends the latch")
        self.assertEqual(len(self.requests), 1, "and only then does the restart go out")

    def test_a_nonzero_install_with_no_output_is_still_a_failure(self):
        # rc!=0 with empty stdout+stderr counted as SUCCESS in the first cut (the why-string was
        # the success test) — the user's audit, 2026-08-17
        import subprocess as sp
        with mock.patch.object(km.subprocess, "run",
                               return_value=sp.CompletedProcess([], 2, stdout="", stderr="")), \
             mock.patch.object(km, "_sync_notice",
                               side_effect=lambda m, ok=True: self.notices.append(m)):
            self.assertFalse(km._converge_install("f" * 8))
        self.assertEqual(km._install_failed_sha(), "f" * 8)
        self.assertTrue(any("exit 2 with no output" in n for n in self.notices))
        self.assertFalse(any("romp refresh" in n for n in self.notices),
                         "the advice must never be the restart the latch exists to stop")

    def test_one_converge_at_a_time(self):
        km._CONVERGE_STATE[0] = "running"
        steps, _ = self._run()
        self.assertEqual(steps, [], "a second converge while one runs is a no-op")

    def test_the_interprocess_flock_refuses_a_second_update(self):
        # several kernels can share one checkout, and the tag path races the converge: only the
        # flock serializes them (the user's audit, 2026-08-17). Hold it; the converge must refuse.
        fd = km._update_flock()
        self.assertIsNotNone(fd, "the lock is free at rest")
        try:
            steps, _ = self._run()
            self.assertEqual(steps, [], "a held lock stops the pull before any subprocess")
            self.assertTrue(any("another update is already running" in n for n in self.notices))
        finally:
            km.os.close(fd)


if __name__ == "__main__":
    unittest.main()
