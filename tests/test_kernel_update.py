#!/usr/bin/env python3
"""Automatic updates of THIS machine (the user 2026-08-09): at kernel boot, one async check reads
origin's newest release tag (git ls-remote — the remote's own refs, never the local tag list) and
compares it against the VERSION-file release. Modes (update-mode.json, default "ask" — ON out of
the box): ask = the shell's update banner offers it; auto = the kernel updates itself once per
discovered version; off = never checks. The update runs DETACHED (fetch + ff-only merge ONTO THE TAG + install.sh,
report to update-report.json, restart through the manager door only on success), and the outcome is
always filed as a sync notice — by the next boot, or by /update-check's poll on the still-running
kernel (fail loudly, never silent). Synthetic tags/paths only."""
import io
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Raw-run belt over conftest's suspenders — via the ENVIRONMENT, not a module attribute: loading the
# kernel re-executes romp_judge into the same module object, which RESETS a hand-assigned jd.STATE
# back to the env-derived default (verified 2026-08-09 — the webpush test's attribute rebind only
# holds under pytest because conftest moves XDG_STATE_HOME first). ROMP_STATE_DIR is the designed
# override and is read on every (re)execution, so it protects a bare `python3 tests/...` run too.
# Scoped to the loads and RESTORED right after (the modules capture STATE at exec): left set, it
# outranks conftest's XDG_STATE_HOME for every test module pytest imports after this one — which is
# exactly how this file's first cut broke two postal-bus tests three modules downstream.
_STATE_TD = tempfile.TemporaryDirectory()
_PREV_STATE_DIR = os.environ.get("ROMP_STATE_DIR")
os.environ["ROMP_STATE_DIR"] = _STATE_TD.name
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
# A dead manager port: any update/converge path a test exercises unstubbed dials nothing real.
# (2026-08-14: the converge route, hit by this suite while genuine main-drift existed, posted an
# IMMEDIATE restart-all to the LIVE manager — every suite run bounced every kernel on the box.)
os.environ["ROMP_MANAGER_PORT"] = "1"
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
jd = SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel_update", os.path.join(BIN, "romp-kernel")).load_module()
if _PREV_STATE_DIR is None:
    os.environ.pop("ROMP_STATE_DIR", None)
else:
    os.environ["ROMP_STATE_DIR"] = _PREV_STATE_DIR


def _serve_get(path, headers=None):
    """The real do_GET over a fake socket (the webpush-test harness): (status, body_bytes)."""
    h = km.Handler.__new__(km.Handler)
    h.client_address = ("127.0.0.1", 0)
    h.headers = dict(headers or {})
    h.path = path
    h.command = "GET"
    h.request_version = "HTTP/1.1"
    h.wfile = io.BytesIO()
    h.rfile = io.BytesIO()
    h.close_connection = True
    captured = {}
    h.send_response = lambda code, *a: captured.__setitem__("status", code)
    h.send_header = lambda k, v: None
    h.end_headers = lambda: None
    h.log_message = lambda *a: None
    h.do_GET()
    return captured.get("status"), h.wfile.getvalue()


class Fresh(unittest.TestCase):
    """Every test starts with no update state: fresh STATE dir, empty avail/latch, empty notices."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        km._UPDATE_AVAIL[0] = ""
        km._UPDATE_STATE[0] = ""
        km._UPDATE_ERROR[0] = ""
        with km._SYNC_LOCK:
            del km._SYNC_NOTICES[:]

    def tearDown(self):
        jd.STATE = self.saved
        self.td.cleanup()

    def notices(self):
        with km._SYNC_LOCK:
            return list(km._SYNC_NOTICES)


class Semver(unittest.TestCase):
    def test_parses_plain_releases_only(self):
        self.assertEqual(km._semver("v0.6.0"), (0, 6, 0))
        self.assertEqual(km._semver("1.12.3"), (1, 12, 3))
        for junk in ("", None, "v1.2", "v1.2.3-rc1", "release", "v1.2.3.4", "v1.2.x"):
            self.assertIsNone(km._semver(junk), junk)

    def test_orders_numerically_not_lexically(self):
        self.assertGreater(km._semver("v0.10.0"), km._semver("v0.9.9"))


class ModeStore(Fresh):
    def test_default_is_ask_the_check_is_on_out_of_the_box(self):
        self.assertEqual(km._update_mode(), "ask")

    def test_set_and_persist(self):
        km._set_update_mode("auto")
        self.assertEqual(km._update_mode(), "auto")
        km._set_update_mode("off")
        self.assertEqual(km._update_mode(), "off")

    def test_garbage_is_refused_at_both_ends(self):
        km._set_update_mode("yes please")               # setter drops it
        self.assertEqual(km._update_mode(), "ask")
        (jd.STATE / "update-mode.json").write_text(json.dumps({"mode": "banana"}))
        self.assertEqual(km._update_mode(), "ask", "an unknown stored mode reads as the default")


class VerifyEnforcedFailsClosed(unittest.TestCase):
    """The trust-root probe downgrades to best-effort ONLY on git's clean "key absent" answer
    (rc 1, silent). A query error, timeout, or noise proves nothing about the trust root, and
    "couldn't tell" must never un-enforce a configured install; an EMPTY configured value is a
    misconfiguration for verification to fail loudly against, not an absence (the user's audit,
    2026-08-17)."""

    def _probe(self, result=None, exc=None):
        clean_env = {k: v for k, v in km.os.environ.items()
                     if k not in ("ROMP_RELEASE_ALLOWED_SIGNERS", "ROMP_VERIFY_RELEASES")}
        kw = {"side_effect": exc} if exc else {"return_value": result}
        with mock.patch.dict(km.os.environ, clean_env, clear=True), \
             mock.patch.object(km.subprocess, "run", **kw):
            return km._release_verify_enforced()

    def _cp(self, rc, out="", err=""):
        return subprocess.CompletedProcess([], rc, stdout=out, stderr=err)

    def test_configured_value_enforces(self):
        self.assertTrue(self._probe(self._cp(0, out="/etc/signers\n")))

    def test_configured_EMPTY_value_still_enforces(self):
        self.assertTrue(self._probe(self._cp(0, out="\n")))

    def test_cleanly_absent_key_is_the_only_downgrade(self):
        self.assertFalse(self._probe(self._cp(1)))

    def test_config_errors_timeouts_and_noise_enforce(self):
        self.assertTrue(self._probe(self._cp(128, err="fatal: bad config line 3\n")))
        self.assertTrue(self._probe(self._cp(1, err="warning: something odd\n")))
        self.assertTrue(self._probe(exc=km.subprocess.TimeoutExpired("git", 10)))
        self.assertTrue(self._probe(exc=OSError("fork failed")))


class LatestReleaseTag(unittest.TestCase):
    def _ls(self, stdout, rc=0, stderr=""):
        return mock.patch.object(km.subprocess, "run", return_value=subprocess.CompletedProcess(
            args=[], returncode=rc, stdout=stdout, stderr=stderr))

    def test_picks_the_numerically_newest_real_release(self):
        out = ("aaa\trefs/tags/v0.9.9\n"
               "bbb\trefs/tags/v0.10.0\n"
               "ccc\trefs/tags/v0.10.0^{}\n"          # peeled duplicate of the annotated tag
               "ddd\trefs/tags/nightly\n"             # not a release number
               "eee\trefs/tags/v0.2.0\n")
        with self._ls(out):
            self.assertEqual(km._latest_release_tag(), "v0.10.0")

    def test_no_release_tags_is_empty_not_an_error(self):
        with self._ls("aaa\trefs/tags/nightly\n"):
            self.assertEqual(km._latest_release_tag(), "")

    def test_git_failure_raises_so_the_caller_can_say_so(self):
        with self._ls("", rc=128, stderr="fatal: could not read from remote"):
            with self.assertRaises(RuntimeError):
                km._latest_release_tag()


class UpdateCheck(Fresh):
    def test_mode_off_never_even_asks_the_network(self):
        km._set_update_mode("off")
        with mock.patch.object(km, "_latest_release_tag", side_effect=AssertionError("must not run")):
            km._update_check()
        self.assertEqual(km._UPDATE_AVAIL[0], "")

    def test_newer_release_in_ask_mode_raises_the_banner_on_every_shell(self):
        sent = []
        with mock.patch.object(km, "_kernel_ver", return_value="v0.6.0+"), \
             mock.patch.object(km, "_latest_release_tag", return_value="v0.7.0"), \
             mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: sent.append((app, m))):
            km._update_check()
        self.assertEqual(km._UPDATE_AVAIL[0], "v0.7.0")
        self.assertEqual(sent, [("shell", {"type": "updateAvail", "cur": "v0.6.0+", "tag": "v0.7.0",
                                           "boot": km._BOOT_ID})],
                         "the offer names the kernel life it came from, so a page can retire it")

    def test_same_or_older_release_is_silence(self):
        sent = []
        for latest in ("v0.6.0", "v0.5.9", ""):
            with mock.patch.object(km, "_kernel_ver", return_value="v0.6.0"), \
                 mock.patch.object(km, "_latest_release_tag", return_value=latest), \
                 mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: sent.append(m)):
                km._update_check()
        self.assertEqual((km._UPDATE_AVAIL[0], sent), ("", []))

    def test_no_local_release_number_means_nothing_to_compare(self):
        with mock.patch.object(km, "_kernel_ver", return_value=None), \
             mock.patch.object(km, "_latest_release_tag", side_effect=AssertionError("must not run")):
            km._update_check()                        # no raise — the check just stands down

    def test_network_failure_is_a_stderr_note_never_a_crash(self):
        with mock.patch.object(km, "_kernel_ver", return_value="v0.6.0"), \
             mock.patch.object(km, "_latest_release_tag", side_effect=RuntimeError("no route to host")):
            km._update_check()
        self.assertEqual(km._UPDATE_AVAIL[0], "")

    def test_auto_mode_updates_once_per_discovered_version(self):
        ran = []
        with mock.patch.object(km, "_kernel_ver", return_value="v0.6.0"), \
             mock.patch.object(km, "_latest_release_tag", return_value="v0.7.0"), \
             mock.patch.object(km, "_run_update", side_effect=lambda tag: ran.append(tag) or True), \
             mock.patch.object(km, "_send_to_app"):
            km._set_update_mode("auto")
            km._update_check()
            self.assertEqual(ran, ["v0.7.0"])
            self.assertEqual(json.loads((jd.STATE / "update-attempted.json").read_text())["tag"], "v0.7.0")
            # a SECOND boot finding the same version does not loop the failed attempt — it says so
            # in the Log and falls back to offering the banner. (The in-memory discovery is
            # per-run, so a fresh boot starts empty — modeled by clearing it.)
            km._UPDATE_AVAIL[0] = ""
            km._update_check()
        self.assertEqual(ran, ["v0.7.0"], "one automatic attempt per version")
        self.assertTrue(any(not n["ok"] and "v0.7.0" in n["text"] for n in self.notices()))

    def test_a_refused_auto_launch_is_not_an_attempt(self):
        # writing the once-only marker BEFORE the launch consumed the automatic retry forever when
        # the launch was refused — e.g. another update holding the interprocess lock (the user's
        # audit, 2026-08-17). A refusal writes no marker and says why; a later pass retries.
        ran = []
        with mock.patch.object(km, "_kernel_ver", return_value="v0.6.0"), \
             mock.patch.object(km, "_latest_release_tag", return_value="v0.7.0"), \
             mock.patch.object(km, "_run_update", side_effect=lambda tag: ran.append(tag) and False), \
             mock.patch.object(km, "_send_to_app"):
            km._set_update_mode("auto")
            km._UPDATE_ERROR[0] = "another update is already running on this checkout"
            km._update_check()
            self.assertEqual(ran, ["v0.7.0"], "the launch was tried")
            self.assertFalse((jd.STATE / "update-attempted.json").exists(),
                             "a refusal is not an attempt — the marker must not burn the retry")
            self.assertEqual(km._UPDATE_AVAIL[0], "", "the discovery re-arms for the next pass")
        self.assertTrue(any("did not start" in n["text"] for n in self.notices()))
        km._UPDATE_ERROR[0] = ""

    def test_rediscovering_the_same_release_mid_run_stays_quiet(self):
        # the 6h re-check re-finds a version for weeks — only a CHANGED discovery is new information
        sent = []
        with mock.patch.object(km, "_kernel_ver", return_value="v0.6.0"), \
             mock.patch.object(km, "_latest_release_tag", return_value="v0.7.0"), \
             mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: sent.append(m)):
            km._update_check()
            km._update_check()
            km._update_check()
        self.assertEqual(len(sent), 1, "one banner push per discovered version, not one per pass")

    def test_a_newer_release_than_the_announced_one_reoffers(self):
        sent = []
        with mock.patch.object(km, "_kernel_ver", return_value="v0.6.0"), \
             mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: sent.append(m)):
            with mock.patch.object(km, "_latest_release_tag", return_value="v0.7.0"):
                km._update_check()
            with mock.patch.object(km, "_latest_release_tag", return_value="v0.8.0"):
                km._update_check()
        self.assertEqual([m["tag"] for m in sent], ["v0.7.0", "v0.8.0"])
        self.assertEqual(km._UPDATE_AVAIL[0], "v0.8.0")

    def test_a_mode_flip_applies_on_the_next_pass_without_a_restart(self):
        # every pass re-reads the mode: turning the gear setting on mid-run must not need a boot
        sent = []
        km._set_update_mode("off")
        with mock.patch.object(km, "_kernel_ver", return_value="v0.6.0"), \
             mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: sent.append(m)):
            with mock.patch.object(km, "_latest_release_tag", side_effect=AssertionError("off must not ask")):
                km._update_check()
            km._set_update_mode("ask")
            with mock.patch.object(km, "_latest_release_tag", return_value="v0.7.0"):
                km._update_check()
        self.assertEqual([m["tag"] for m in sent], ["v0.7.0"])


class CheckLoop(Fresh):
    def test_two_cadences_one_loop_and_a_crash_never_kills_the_thread(self):
        # The loop carries TWO watchers since the mesh-aware notice (the user 2026-08-14): the cheap
        # origin/main drift probe every round (minutes — a merge should be noticed promptly), the
        # release-tag check on its old six-hour stride. Either watcher dying must not kill the loop,
        # nor one watcher's crash starve the other.
        releases = []
        drifts = []
        naps = []

        def nap(s):
            naps.append(s)
            if len(naps) == 2:
                raise SystemExit                       # unhook the forever-loop after two rounds

        def release_pass():
            releases.append(1)
            raise RuntimeError("boom")                 # a dying release check must not kill the loop…

        def drift_pass():
            drifts.append(1)
            if len(drifts) == 1:
                raise RuntimeError("boom")             # …nor a dying drift probe the NEXT drift probe
        with mock.patch.object(km, "_update_check", side_effect=release_pass), \
             mock.patch.object(km, "_main_drift_check", side_effect=drift_pass), \
             mock.patch.object(km.time, "sleep", side_effect=nap), \
             self.assertRaises(SystemExit):
            km._update_check_loop()
        self.assertEqual(len(releases), 1, "the six-hour stride: one release check across two fast rounds")
        self.assertEqual(len(drifts), 2, "the drift probe runs every round, surviving its own crash")
        self.assertEqual(naps, [km._MAIN_CHECK_EVERY_S] * 2)
        self.assertEqual(km._UPDATE_CHECK_EVERY_S, 6 * 3600)
        self.assertEqual(km._MAIN_CHECK_EVERY_S, 300)


class RunUpdate(Fresh):
    def test_detached_child_lands_on_the_tag_installs_reports_and_restarts_only_on_success(self):
        calls = []
        real_popen = subprocess.Popen
        def _popen(*a, **kw):
            argv = a[0] if a else kw.get("args")
            if argv and argv[0] == "git":   # the enforcement config query runs for real (see harness)
                return real_popen(*a, **kw)
            calls.append((a, kw))
            return mock.MagicMock()
        with mock.patch.object(km.subprocess, "Popen", side_effect=_popen), \
             mock.patch.dict(km.os.environ, {"ROMP_MANAGER_PORT": "7777",
                                             "GIT_CONFIG_GLOBAL": "/dev/null",
                                             "GIT_CONFIG_SYSTEM": "/dev/null"}):
            self.assertTrue(km._run_update("v0.7.0"))
        (a, kw), = calls
        self.assertEqual(a[0][:2], ["bash", "-c"])
        self.assertTrue(kw.get("start_new_session"), "install.sh + the restart take the kernel down — the child must outlive it")
        script = a[0][2]
        # EXACTLY the release commit, never the branch tip (the user 2026-08-09): the tag is fetched
        # by explicit refspec and fast-forwarded onto — an update to v0.7.0 means running v0.7.0
        self.assertIn("git fetch origin refs/tags/v0.7.0:refs/tags/v0.7.0", script)
        self.assertIn("gpg.minTrustLevel=fully", script)
        self.assertIn("verify-tag v0.7.0", script)
        self.assertLess(script.index("verify-tag v0.7.0"), script.index("git merge --ff-only v0.7.0"),
                        "signature verification is the gate immediately before code moves")
        self.assertIn("git merge --ff-only v0.7.0", script)
        self.assertLess(script.index('rm -f "$MARKER.pub"'),
                        script.index('> "$MARKER.pub"'),
                        "the marker staging name is cleared of plants before the shell opens it "
                        "(the r34 verification: > on a planted FIFO blocks forever)")
        import re as _re
        m = _re.search(r"rm -f (\S*romp-install-failed\.tmp)\n", script)
        self.assertIsNotNone(m, "the latch staging name is cleared of plants before the shell "
                                "opens it — the old prefix pin was satisfied by the MARKER.pub "
                                "rm alone (the r35 verification's mutant)")
        self.assertLess(script.index(m.group(0)), script.index("> " + m.group(1)),
                        "and the rm sits BEFORE the redirect that would block on a plant")
        rep_redirects = [mm.start() for mm in _re.finditer(r"> (\S*update-report\.json\S*\.tmp)", script)]
        self.assertTrue(rep_redirects, "the report is staged at a fixed .tmp name")
        for pos in rep_redirects:
            head = script[max(0, pos - 250):pos]
            self.assertIn("rm -f", head,
                          "every report redirect clears a plant first — the fixed staging "
                          "name blocked the detached updater otherwise (the r35 verification)")
        self.assertIn('rev-parse --short=8 HEAD | head -c 8)\" = \"$NEW8\"'.replace('\\"', '"'),
                      script,
                      "the move VERIFIES it landed before install: merge --ff-only exits 0 as a "
                      "no-op when a seam commit already contains the tag (the r31 verification)")
        self.assertLess(script.index("merge --ff-only v0.7.0"),
                        script.index('= "$NEW8" ] && snap_install "$NEW8"'),
                        "the landed-verify sits between the move and the SNAPSHOT install "
                        "(the commit's own tree executes — the v1.3.16 audit's P1.1, r44)")
        self.assertIn('git archive "$1" | tar -x -C', script,
                      "snap_install materializes the complete target tree")
        self.assertIn('ROMP_INSTALL_TARGET="$(pwd)"', script,
                      "…and aims the snapshot's outputs at the real checkout")
        self.assertNotIn("git pull", script, "a pull takes whatever the branch has gained past the tag")
        self.assertIn('snap_install "$NEW8"', script,
                      "the install runs the snapshot, never ./install.sh from the live tree")
        # the v1.3.19 audit's P1: the tag updater spent the latch and restarted with NO runtime
        # generation for the verified commit — serve/manager then resolved LIVE checkout bytes
        # (executed MUTATED_LIVE_BYTES on the normal signed-release path)
        self.assertIn('gen_build "$NEW8"', script,
                      "the runtime generation for the verified commit is REQUIRED")
        self.assertLess(script.index('snap_install "$NEW8"'), script.index('&& gen_build "$NEW8"'),
                        "…built inside the transaction, after the install")
        self.assertLess(script.index('&& gen_build "$NEW8"'), script.index("if pub_line"),
                        "…and BEFORE the latch is spent: a missing generation fails the update")
        self.assertIn('romp-run-$1', script, "the per-commit generation name the resolvers read")
        self.assertIn("update-report.json", script)
        # the restart rides the SUCCESS branch only: everything after `if` up to `else` has it,
        # the failure branch does not
        _tail = script.split('if [ "$OK" = 1 ]', 1)[1]   # the settle block has its own else now
        ok_branch, fail_branch = _tail.split("else\n", 1)
        self.assertIn("/restart-all", ok_branch)
        self.assertIn("X-Romp-Manager-Token", ok_branch)
        self.assertNotIn("$ROMP_MANAGER_TOKEN", ok_branch,
                         "the manager bearer must not be expanded into curl/process argv")
        self.assertIn("os.environ.get", ok_branch, "the detached helper reads the token in memory")
        self.assertIn("http.client.HTTPConnection", ok_branch,
                      "the localhost control hop must not consult ambient proxy settings")
        self.assertNotIn("urllib.request", ok_branch)
        self.assertNotIn("/restart-all", fail_branch)
        self.assertEqual(km._UPDATE_STATE[0], "running")

    def test_no_manager_means_no_restart_leg(self):
        calls = []
        env = {k: v for k, v in km.os.environ.items() if k != "ROMP_MANAGER_PORT"}
        with mock.patch.object(km.subprocess, "Popen", side_effect=lambda *a, **kw: calls.append(a)), \
             mock.patch.dict(km.os.environ, env, clear=True):
            self.assertTrue(km._run_update("v0.7.0"))
        self.assertNotIn("/restart-all", calls[0][0][2])

    def test_refuses_junk_tags_and_reentry(self):
        with mock.patch.object(km.subprocess, "Popen", side_effect=AssertionError("must not spawn")):
            self.assertFalse(km._run_update("v1; rm -rf /"), "a tag is shell payload — semver or nothing")
        with mock.patch.object(km.subprocess, "Popen"):
            self.assertTrue(km._run_update("v0.7.0"))
            self.assertFalse(km._run_update("v0.7.0"), "one update at a time")

    def test_allowed_signers_path_is_resolved_and_shell_quoted(self):
        with tempfile.TemporaryDirectory() as td:
            signers = Path(td) / "release signers"
            signers.write_text("release@example ssh-ed25519 AAAATEST\n")
            with mock.patch.dict(km.os.environ, {"ROMP_RELEASE_ALLOWED_SIGNERS": str(signers)}):
                argv = km._release_verify_argv("v0.7.0")
            self.assertEqual(argv[:3], ["git", "-c", "gpg.minTrustLevel=fully"])
            self.assertIn("gpg.ssh.allowedSignersFile=%s" % signers.resolve(), argv)
            self.assertEqual(argv[-2:], ["verify-tag", "v0.7.0"])

    def test_missing_directory_or_unreadable_allowed_signers_refuses_before_spawn(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(km.subprocess, "Popen", side_effect=AssertionError("must not spawn")):
            for configured in (str(Path(td) / "missing"), td):
                km._UPDATE_ERROR[0] = ""
                with mock.patch.dict(km.os.environ, {"ROMP_RELEASE_ALLOWED_SIGNERS": configured}):
                    self.assertFalse(km._run_update("v0.7.0"))
                self.assertIn("readable regular file", km._UPDATE_ERROR[0])
            signers = Path(td) / "signers"; signers.write_text("synthetic")
            with mock.patch.dict(km.os.environ, {"ROMP_RELEASE_ALLOWED_SIGNERS": str(signers)}), \
                 mock.patch.object(km.os, "access", return_value=False):
                self.assertFalse(km._run_update("v0.7.0"))
            self.assertIn("readable regular file", km._UPDATE_ERROR[0])

    def _execute_captured_updater(self, verify_rc, enforce=False, install_rc=0, manager_port=None,
                                  pre_latch=None, latch_mode=None, pre_files=None):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"; root.mkdir()
            (root / ".git").mkdir()                   # the interprocess update flock lives here
            state = Path(td) / "state"; state.mkdir()
            fakebin = Path(td) / "bin"; fakebin.mkdir()
            calls = Path(td) / "git-calls"
            git = fakebin / "git"
            # `config` queries DELEGATE to the real git: _release_verify_enforced asks git for the
            # EFFECTIVE gpg.ssh.allowedSignersFile (worktrees, [include]/[includeIf], global and
            # system config resolve exactly as verification will see them), so the stub must let
            # real resolution happen under the harness's controlled GIT_CONFIG_* env.
            # The calls log and verify rc are BAKED into the stubs, not read from the environment:
            # the enforcement query is spawned by the kernel under test (its env is not the
            # harness's to pass), and an inherited $GIT_CALLS proved flaky under the full suite —
            # a fork occasionally saw it empty, crashing the stub before its exec and flipping
            # enforcement off (2026-08-16, three hits in one instrumented run).
            git.write_text("#!/bin/sh\nprintf '%%s\\n' \"$*\" >> '%s'\n"
                           "case \" $* \" in\n"
                           "  *' config '*) exec /usr/bin/git \"$@\";;\n"
                           "  *' verify-tag '*) exit %d;;\n"
                           "  *' rev-parse '*) echo deadbee1;;\n"
                           "  *' archive '*) tar -c -C '%s' install.sh;;\n"
                           "esac\nexit 0\n" % (calls, int(verify_rc), root))
            git.chmod(0o755)
            install = root / "install.sh"
            install.write_text("#!/bin/sh\nprintf 'install\\n' >> '%s'\nexit %d\n"
                               % (calls, int(install_rc)))
            install.chmod(0o755)
            spawned = []
            env = {k: v for k, v in km.os.environ.items()
                   if k not in ("ROMP_MANAGER_PORT", "ROMP_RELEASE_ALLOWED_SIGNERS",
                                "ROMP_VERIFY_RELEASES", "GIT_CONFIG_GLOBAL")}
            # hermetic global-config dimension: _release_verify_enforced also reads the USER'S
            # global git config (bootstrap enforces on it, so the updater must too) — a dev box
            # with global signers would otherwise flip the no-trust-root cases
            env.update(PATH=str(fakebin) + os.pathsep + env.get("PATH", ""),
                       GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
            if manager_port is not None:
                env["ROMP_MANAGER_PORT"] = str(manager_port)
            if enforce == "global":
                # the trust root lives ONLY in the global git config, and only behind an [include]
                # — the exact resolution a raw file scan missed (the user's audit, 2026-08-16);
                # git itself must surface it
                inc = Path(td) / "included.cfg"
                inc.write_text('[gpg "ssh"]\n\tallowedSignersFile = /tmp/signers\n')
                gcfg = Path(td) / "gitconfig"
                gcfg.write_text('[include]\n\tpath = %s\n' % inc)
                env["GIT_CONFIG_GLOBAL"] = str(gcfg)
            elif enforce:
                # the opt-in trust-root flag: with it, verification is the fail-closed gate
                env["ROMP_VERIFY_RELEASES"] = "1"
            real_popen = subprocess.Popen
            def _popen(*a, **kw):
                argv = a[0] if a else kw.get("args")
                if argv and argv[0] == "git":
                    # the enforcement config query — let it RUN (it resolves via the stub → real
                    # git under the controlled env); only the detached bash updater is captured
                    return real_popen(*a, **kw)
                spawned.append(a)
                return mock.MagicMock()
            with mock.patch.object(km, "ROOT", root), mock.patch.object(km.jd, "STATE", state), \
                 mock.patch.object(km.subprocess, "Popen", side_effect=_popen), \
                 mock.patch.dict(km.os.environ, env, clear=True):
                self.assertTrue(km._run_update("v0.7.0"))
            script = spawned[0][0][2]
            latch_p = root / ".git" / "romp-install-failed"
            if pre_latch is not None:
                latch_p.write_text(pre_latch)
                if latch_mode is not None:
                    os.chmod(latch_p, latch_mode)
            for rel, content in (pre_files or {}).items():
                if content is None:
                    (root / rel).mkdir()
                else:
                    (root / rel).write_text(content)
            ran = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
            if pre_latch is not None and latch_mode is not None:
                os.chmod(latch_p, 0o644)
            rows = calls.read_text().splitlines()
            report = json.loads((state / "update-report.json").read_text())
            latch = root / ".git" / "romp-install-failed"   # checkout-scoped, beside the update lock
            self._latch = latch.read_text().strip() if latch.exists() else None
            marker = root / ".git" / "romp-update-channel"
            self._marker = marker.read_text().strip() if marker.exists() else None
            return ran.returncode, rows, report

    def test_the_report_states_what_the_restart_actually_did(self):
        # restarted:true was written BEFORE the manager request; when that request failed,
        # /update-check waited forever on a restart that never happened (the user's audit,
        # 2026-08-18). Executed: a dead manager port → the helper fails → ok + restarted:false.
        rc, rows, report = self._execute_captured_updater(0, manager_port=1)
        self.assertEqual(rc, 0)
        self.assertTrue(report["ok"])
        self.assertFalse(report["restarted"],
                         "the report says what HAPPENED, not what was hoped")

    def test_the_tag_script_refuses_an_unreadable_existing_latch_executed(self):
        # bash's settle could rm -f a latch it could not READ (unlink needs only dir perms) —
        # erasing the record every other reader refuses to touch (the adversarial check,
        # 2026-08-19). Executed: unreadable latch → the update reports failure, the latch survives.
        import pathlib
        rc, rows, report = None, None, None
        # arm an unreadable latch inside the harness root before the script runs
        orig = self._execute_captured_updater
        def with_latch(verify_rc, **kw):
            return orig(verify_rc, pre_latch="deadbee9", latch_mode=0, **kw)
        rc, rows, report = with_latch(0)
        self.assertEqual(rc, 0, "the reporter records the refusal")
        self.assertFalse(report["ok"])
        self.assertEqual(self._latch, "deadbee9", "the unreadable record SURVIVES, unread and unerased")
        self.assertFalse(any(row.startswith("merge ") for row in rows), "nothing moved")

    def test_good_signature_verifier_allows_merge_and_install(self):
        rc, rows, report = self._execute_captured_updater(0)
        self.assertEqual(rc, 0)
        self.assertTrue(any("gpg.minTrustLevel=fully" in row and "verify-tag v0.7.0" in row for row in rows))
        self.assertIn("merge --ff-only v0.7.0", rows)
        self.assertIn("install", rows)
        self.assertTrue(report["ok"])
        # the install-intent latch is armed BEFORE the merge moves HEAD (a crash between the two
        # otherwise boots half-installed code with no record — the user's audit, 2026-08-17)...
        rev = next(i for i, row in enumerate(rows) if "rev-parse" in row)
        mrg = next(i for i, row in enumerate(rows) if row.startswith("merge "))
        self.assertLess(rev, mrg, "the latch's sha resolves before the move")
        self.assertIsNone(self._latch, "...and a completed install spends it")

    def test_a_failed_install_after_the_merge_leaves_the_intent_latch_armed(self):
        rc, rows, report = self._execute_captured_updater(0, install_rc=1)
        self.assertEqual(rc, 0)
        self.assertIn("merge --ff-only v0.7.0", rows, "HEAD moved")
        self.assertIn("install", rows, "install ran and failed")
        self.assertFalse(report["ok"])
        self.assertEqual(self._latch, "deadbee1",
                         "the durable latch names the moved-to commit for the boot heal")

    def test_the_settle_heal_publishes_a_staged_channel_intent(self):
        # the v1.3.8 audit's hard-death repro, tag-updater edition: a crashed bootstrap left the
        # latch matching HEAD, its intent staged, and the marker stale — the updater's settle
        # heals it and must publish the intent before spending the latch
        rc, rows, report = self._execute_captured_updater(
            0, pre_latch="deadbee1 stable",
            pre_files={".git/romp-update-channel": "dev\n"})
        self.assertEqual(rc, 0)
        self.assertTrue(report["ok"])
        self.assertEqual(self._marker, "stable",
                         "the healed build wears the channel its update intended")
        self.assertIsNone(self._latch)
        self.assertIsNone(self._latch, "the record and its channel are spent together")

    def test_an_unpublishable_marker_keeps_the_tag_updates_latch(self):
        # the tag script's two pub_line sites were the only healer legs with no unpublishable-
        # marker coverage (the adversarial review, 2026-08-20): a reorder spending the latch
        # before the publish would serve the build under the OLD marker unnoticed
        rc, rows, report = self._execute_captured_updater(
            0, pre_latch="deadbee1 stable",
            pre_files={".git/romp-update-channel": "dev\n",
                       ".git/romp-update-channel.pub": None})   # a DIRECTORY at the staging
        #                                                         name: the printf fails
        self.assertEqual(rc, 0)
        self.assertFalse(report["ok"], "an unpublishable channel is an incomplete update")
        self.assertIsNotNone(self._latch, "the latch survives for the retry")
        self.assertIn("deadbee1", self._latch)

    def test_the_tag_settle_refuses_empty_and_malformed_latches(self):
        # the strict grammar, tag-script edition (the v1.3.9 audit): an EXISTING empty latch fell
        # through every [ -s ] branch and the update proceeded over it; a malformed channel token
        # ("sha8 sta") was healed as a legacy plain latch, spending the record with the channel
        # unpublished
        for bad in ("", "deadbee1 sta", "deadbee1\nffff9999\neeee1111",
                    "deadbee1\nquarantined", "quarantined\ndeadbee1"):
            # the mixed sha+quarantined forms: per-line validation healed them and ERASED the
            # quarantine record while every python reader refused (the adversarial review,
            # 2026-08-21 — 'quarantined' is valid only as the exact pair)
            km._UPDATE_STATE[0] = ""       # each iteration is its own update episode
            rc, rows, report = self._execute_captured_updater(0, pre_latch=bad)
            self.assertEqual(rc, 0, "the reporter records the refusal")
            self.assertFalse(report["ok"], "%r must refuse, never settle" % bad)
            self.assertEqual(self._latch, bad.strip(),
                             "the unknown record survives, unerased")
            self.assertFalse(any(row.startswith("merge ") for row in rows),
                             "nothing moved over %r" % bad)

    def test_the_tag_success_leg_publishes_the_carried_token(self):
        # the v1.3.10 audit's P1, tag edition: the latch holds "deadbee1\n0ddba11d stable" (the
        # successor's arm carried a crashed stable switch); HEAD==deadbee1 heals, completes, and
        # must publish the carried stable instead of deleting it with the spent latch
        rc, rows, report = self._execute_captured_updater(
            0, pre_latch="deadbee1\n0ddba11d stable",
            pre_files={".git/romp-update-channel": "dev\n"})
        self.assertEqual(rc, 0)
        self.assertTrue(report["ok"])
        self.assertEqual(self._marker, "stable",
                         "the carried explicit choice survives the supersession")
        self.assertIsNone(self._latch)

    def test_healing_the_carried_line_never_inherits_in_the_tag_settle(self):
        # the v1.3.11 audit's P1, tag edition: HEAD matches the CARRIED line while line 1's move
        # never landed — the unlanded intent's token must not publish
        rc, rows, report = self._execute_captured_updater(
            0, pre_latch="aaaa1111 dev\ndeadbee1",
            pre_files={".git/romp-update-channel": "stable\n"})
        self.assertEqual(rc, 0)
        self.assertEqual(self._marker, "stable",
                         "an unlanded intent's channel is never published")

    def test_a_failing_tag_update_carries_the_pending_token_forward(self):
        # the adversarial review, 2026-08-21: the tag settle's lossy carry destroyed the pending
        # stable on line 2 when line 1's heal failed — the re-armed latch must keep the choice
        rc, rows, report = self._execute_captured_updater(
            0, install_rc=1, pre_latch="deadbee1\n0ddba11d stable",
            pre_files={".git/romp-update-channel": "dev\n"})
        self.assertEqual(rc, 0)
        self.assertFalse(report["ok"])
        self.assertEqual(self._latch, "deadbee1 stable",
                         "the pending choice rides the surviving line through the failed update")
        self.assertEqual(self._marker, "dev", "nothing published while the heal keeps failing")

    def test_a_line2_heal_fail_never_launders_the_unlanded_token_in_the_tag_settle(self):
        # reversed direction of the merge (the adversarial review, 2026-08-21): HEAD matches
        # line 2; line 1 is a crashed bootstrap's unlanded "sha dev" — the failing heal must
        # carry the PLAIN survivor, not steal the token
        rc, rows, report = self._execute_captured_updater(
            0, install_rc=1, pre_latch="aaaa1111 dev\ndeadbee1",
            pre_files={".git/romp-update-channel": "stable\n"})
        self.assertEqual(rc, 0)
        self.assertFalse(report["ok"])
        self.assertNotIn("dev", self._latch or "",
                         "the unlanded token is never laundered onto the carry: %r" % self._latch)
        self.assertEqual(self._marker, "stable")

    def test_a_plain_sha_latch_line_publishes_no_channel(self):
        # in-channel updaters (this one included) arm plain sha lines: healing one changes no
        # marker, and there is no separate file for a stranger's record to poison (the
        # adversarial review, 2026-08-20)
        rc, rows, report = self._execute_captured_updater(
            0, pre_latch="deadbee1",
            pre_files={".git/romp-update-channel": "dev\n"})
        self.assertEqual(rc, 0)
        self.assertTrue(report["ok"])
        self.assertEqual(self._marker, "dev", "a plain line stages no channel")

    def test_the_settle_never_moot_clears_a_nonhex_latch_line(self):
        # a torn quarantine prefix is one non-hex line; the old settle rm -f'd it as moot and
        # proceeded (the v1.3.8 audit reproduced the launcher doing the same)
        rc, rows, report = self._execute_captured_updater(0, pre_latch="quarantin")
        self.assertEqual(rc, 0, "the reporter records the refusal")
        self.assertFalse(report["ok"])
        self.assertEqual(self._latch, "quarantin", "the unknown record survives, unerased")
        self.assertFalse(any(row.startswith("merge ") for row in rows), "nothing moved")

    def test_unsigned_or_bad_tag_stops_before_merge_install_and_restart(self):
        # enforcement requires a CONFIGURED trust root (the env opt-in here; bootstrap's persisted
        # allowed-signers config is the friend-install equivalent) — with one, the gate is exactly
        # as strict as before
        rc, rows, report = self._execute_captured_updater(1, enforce=True)
        self.assertEqual(rc, 0, "the detached reporter records a refusal rather than crashing")
        self.assertTrue(any("gpg.minTrustLevel=fully" in row and "verify-tag v0.7.0" in row for row in rows))
        self.assertFalse(any(row.startswith("merge ") for row in rows))
        self.assertNotIn("install", rows)
        self.assertFalse(report["ok"])
        self.assertIn("signature verification", report["why"])

    def test_global_git_config_trust_root_enforces_in_the_updater(self):
        # bootstrap enforces when the GLOBAL git config carries the signers file; the updater
        # checking only env + clone-local config made a documented global-only setup silently
        # weaker in the long-lived half (the user's audit, 2026-08-16)
        rc, rows, report = self._execute_captured_updater(1, enforce="global")
        self.assertEqual(rc, 0)
        self.assertFalse(any(row.startswith("merge ") for row in rows))
        self.assertNotIn("install", rows)
        self.assertFalse(report["ok"])

    def test_bare_semver_tags_are_not_releases(self):
        # bootstrap and the manual recipe select v-prefixed tags only; _semver alone also parses
        # the VERSION file's bare X.Y.Z, so the tag sites must require the prefix explicitly
        fake = subprocess.CompletedProcess([], 0, stdout=(
            "aaaa\trefs/tags/1.9.9\n"
            "bbbb\trefs/tags/v0.7.0\n"
            "cccc\trefs/tags/v0.7.0^{}\n"), stderr="")
        with mock.patch.object(km.subprocess, "run", return_value=fake):
            self.assertEqual(km._latest_release_tag(), "v0.7.0",
                             "a bare X.Y.Z tag must never outrank or be selected")
        with self.assertRaises(ValueError):
            km._release_verify_argv("1.2.3")

    def test_unsigned_without_trust_root_proceeds_with_a_note(self):
        # no trust root anywhere → verification is best-effort: still attempted (and its refusal in
        # the log), but the update proceeds. Mandatory-with-no-published-key bricked every install's
        # updater from the first release, since no key was ever distributed (2026-08-14 review).
        rc, rows, report = self._execute_captured_updater(1)
        self.assertEqual(rc, 0)
        self.assertTrue(any("verify-tag v0.7.0" in row for row in rows), "verification is still attempted")
        self.assertTrue(any("merge --ff-only v0.7.0" in row for row in rows))
        self.assertIn("install", rows)
        self.assertTrue(report["ok"])


class ReportConsumption(Fresh):
    def test_success_with_restart_files_once_and_archives(self):
        (jd.STATE / "update-report.json").write_text(json.dumps({"ok": True, "tag": "v0.7.0", "restarted": True}))
        rep = km._consume_update_report()
        self.assertTrue(rep["ok"])
        ns = self.notices()
        self.assertEqual(len(ns), 1)
        self.assertTrue(ns[0]["ok"])
        self.assertIn("v0.7.0", ns[0]["text"])
        self.assertFalse((jd.STATE / "update-report.json").exists(), "consumed — never re-filed")
        self.assertTrue((jd.STATE / "update-report-last.json").exists())
        self.assertIsNone(km._consume_update_report(), "second boot: nothing left to file")

    def test_failure_is_a_loud_not_ok_notice(self):
        (jd.STATE / "update-report.json").write_text(json.dumps({"ok": False, "tag": "v0.7.0",
                                                                 "why": "the pull or install failed"}))
        km._consume_update_report()
        ns = self.notices()
        self.assertEqual(len(ns), 1)
        self.assertFalse(ns[0]["ok"])
        self.assertIn("update.log", ns[0]["text"])

    def test_running_only_clears_the_inflight_latch(self):
        km._UPDATE_STATE[0] = "running"
        (jd.STATE / "update-report.json").write_text(json.dumps({"ok": False, "tag": "v0.7.0"}))
        km._consume_update_report(running_only=True)
        self.assertEqual(km._UPDATE_STATE[0], "")


class Routes(Fresh):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _post(self, path, token=True):
        import urllib.request, urllib.error
        headers = {"X-Romp-Token": km.TOKEN} if token else {}
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method="POST",
                                     data=b"{}", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_update_check_is_gated_and_reports_the_state(self):
        status, _ = _serve_get("/update-check")
        self.assertEqual(status, 403)
        km._UPDATE_AVAIL[0] = "v0.7.0"
        status, body = _serve_get("/update-check", headers={"X-Romp-Token": km.TOKEN})
        d = json.loads(body)
        self.assertEqual((status, d["tag"], d["mode"], d["state"]), (200, "v0.7.0", "ask", ""))
        self.assertEqual(d["boot"], km._BOOT_ID, "the banner detects the NEW kernel by this flipping")

    def test_update_check_unsticks_a_dead_reportless_updater(self):
        # the detached updater holds the checkout flock for its whole life: running + no report +
        # a FREE lock = it died silently, which wedged the banner and refused every further update
        # until a kernel restart (the user's audit, 2026-08-18)
        km._UPDATE_STATE[0] = "running"
        (jd.STATE / "update-report.json").unlink(missing_ok=True)
        try:
            _, body = _serve_get("/update-check", headers={"X-Romp-Token": km.TOKEN})
            d = json.loads(body)
            self.assertEqual(d["state"], "", "a dead updater no longer reads as running")
            self.assertIn("without reporting", d["failed"])
            # negative: while the lock IS held (the updater alive), running stands
            km._UPDATE_STATE[0] = "running"
            km._UPDATE_ERROR[0] = ""
            fd = km._update_flock()
            self.assertIsNotNone(fd)
            try:
                _, body = _serve_get("/update-check", headers={"X-Romp-Token": km.TOKEN})
                self.assertEqual(json.loads(body)["state"], "running")
            finally:
                km.os.close(fd)
        finally:
            km._UPDATE_STATE[0] = ""
            km._UPDATE_ERROR[0] = ""

    def test_update_check_reports_a_running_converge_as_in_flight(self):
        # a page loading mid-converge must get the wait treatment, not a fresh offer — converge
        # errors already ride `failed`; the running state rides `state` (the user's audit, 2026-08-17)
        km._CONVERGE_STATE[0] = "running"
        try:
            _, body = _serve_get("/update-check", headers={"X-Romp-Token": km.TOKEN})
            self.assertEqual(json.loads(body)["state"], "running")
        finally:
            km._CONVERGE_STATE[0] = ""

    def test_update_check_surfaces_converge_refusals_so_the_banner_unsticks(self):
        # a refused converge left the banner's spinner polling /update-check forever: the route
        # reported nothing, because converge errors lived only in the notice ring (the user's
        # audit, 2026-08-17). They ride the same `failed` slot the banner already acts on.
        km._CONVERGE_ERROR[0] = "the checkout advanced to aaaa1111 but install.sh failed: boom"
        try:
            _, body = _serve_get("/update-check", headers={"X-Romp-Token": km.TOKEN})
            self.assertIn("install.sh failed", json.loads(body)["failed"])
        finally:
            km._CONVERGE_ERROR[0] = ""

    def test_a_held_update_lock_refuses_the_tag_update_too(self):
        # the tag path and the converge share the interprocess flock: several kernels can share
        # one checkout, and two install.sh runs interleaving corrupts the build (the user's
        # audit, 2026-08-17). Hold the lock; the tag update must refuse before spawning.
        km._UPDATE_STATE[0] = ""
        fd = km._update_flock()
        self.assertIsNotNone(fd, "the lock is free at rest")
        try:
            with mock.patch.object(km.subprocess, "Popen",
                                   side_effect=AssertionError("must not spawn")):
                self.assertFalse(km._run_update("v0.7.0"))
            self.assertIn("another update is already running", km._UPDATE_ERROR[0])
            self.assertEqual(km._UPDATE_STATE[0], "", "the in-flight latch is released on refusal")
        finally:
            km.os.close(fd)
            km._UPDATE_ERROR[0] = ""

    def test_update_check_poll_consumes_a_failed_report_mid_run(self):
        km._UPDATE_STATE[0] = "running"
        (jd.STATE / "update-report.json").write_text(json.dumps({"ok": False, "tag": "v0.7.0",
                                                                 "why": "the pull or install failed"}))
        _, body = _serve_get("/update-check", headers={"X-Romp-Token": km.TOKEN})
        d = json.loads(body)
        self.assertEqual((d["failed"], d["state"]), ("the pull or install failed", ""))
        self.assertTrue(any(not n["ok"] for n in self.notices()), "the failure reached the Log too")

    def test_a_success_headed_for_restart_waits_only_while_the_updater_lives(self):
        # ok+restarted belongs to the NEXT boot — but only while the child still holds the
        # checkout flock. A restart the manager accepted and never delivered wedged this kernel
        # forever (the adversarial review, 2026-08-19): the child freeing the lock is the event
        # that hands the report to the running kernel, consumed loudly.
        km._UPDATE_STATE[0] = "running"
        (jd.STATE / "update-report.json").write_text(json.dumps({"ok": True, "tag": "v0.7.0",
                                                                 "restarted": True}))
        fd = km._update_flock()                       # the child alive: report stays for the boot
        self.assertIsNotNone(fd)
        try:
            _, body = _serve_get("/update-check", headers={"X-Romp-Token": km.TOKEN})
            d = json.loads(body)
            self.assertEqual((d["failed"], d["updated"], d["state"]), ("", "", "running"))
            self.assertTrue((jd.STATE / "update-report.json").exists(), "not consumed while it lives")
        finally:
            km.os.close(fd)
        _, body = _serve_get("/update-check", headers={"X-Romp-Token": km.TOKEN})
        d = json.loads(body)
        self.assertEqual(d["updated"], "v0.7.0", "child gone → the running kernel consumes it")
        self.assertFalse((jd.STATE / "update-report.json").exists())
        self.assertTrue(any("romp refresh" in n["text"] for n in self.notices()),
                        "and says what to do if the promised restart never shows")
        km._UPDATE_STATE[0] = ""

    def test_non_object_reports_are_as_corrupt_as_garbage(self):
        # null parses (wedging state:running forever) and []/numbers 500'd at _peek.get (the
        # user's audit, 2026-08-19): anything but a dict is set aside and the probe unsticks
        for payload in ("null", "[]", "42", '"a string"'):
            km._UPDATE_STATE[0] = "running"
            (jd.STATE / "update-report.json").write_text(payload)
            try:
                status, body = _serve_get("/update-check", headers={"X-Romp-Token": km.TOKEN})
                self.assertEqual(status, 200, payload)
                self.assertEqual(json.loads(body)["state"], "", payload)
            finally:
                (jd.STATE / "update-report.json.bad").unlink(missing_ok=True)
                km._UPDATE_STATE[0] = ""
                km._UPDATE_ERROR[0] = ""

    def test_a_stale_latch_cannot_satisfy_the_tag_scripts_publish_gate(self):
        # a failed tmp/mv left the OLD latch satisfying [ -s ] and the merge proceeded with no
        # record of the new build (the user's audit, 2026-08-19): the gate now demands the
        # published first line EQUAL the new tag's sha
        km._UPDATE_STATE[0] = ""
        calls = []
        real_popen = subprocess.Popen
        def _popen(*a, **kw):
            argv = a[0] if a else kw.get("args")
            if argv and argv[0] == "git":
                return real_popen(*a, **kw)
            calls.append((a, kw))
            return mock.MagicMock()
        with mock.patch.object(km.subprocess, "Popen", side_effect=_popen), \
             mock.patch.dict(km.os.environ, {"GIT_CONFIG_GLOBAL": "/dev/null",
                                             "GIT_CONFIG_SYSTEM": "/dev/null"}):
            self.assertTrue(km._run_update("v0.7.0"))
        script = calls[0][0][0][2]
        self.assertIn('sed -n 1p', script)
        self.assertIn('= "$NEW8" ]', script.replace("\\", ""),
                      "the gate compares the PUBLISHED latch line to the new sha — a stale "
                      "survivor of a failed publish can never green-light the merge")
        km._UPDATE_STATE[0] = ""

    def test_a_corrupt_report_never_defeats_the_liveness_probe(self):
        # exists() gating let a zero-byte/garbage report wedge 'running' forever (the adversarial
        # review, 2026-08-19): unparseable is set aside as .bad and treated as missing
        km._UPDATE_STATE[0] = "running"
        (jd.STATE / "update-report.json").write_text("{not json")
        try:
            _, body = _serve_get("/update-check", headers={"X-Romp-Token": km.TOKEN})
            d = json.loads(body)
            self.assertEqual(d["state"], "", "corrupt report + free lock → unstuck")
            self.assertFalse((jd.STATE / "update-report.json").exists())
            self.assertTrue((jd.STATE / "update-report.json.bad").exists(), "set aside, not lost")
        finally:
            (jd.STATE / "update-report.json.bad").unlink(missing_ok=True)
            km._UPDATE_STATE[0] = ""
            km._UPDATE_ERROR[0] = ""

    def test_post_update_requires_something_known_and_the_token(self):
        code, _ = self._post("/update", token=False)
        self.assertEqual(code, 403)
        km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""     # module state: a prior drift pass must not leak in
        code, body = self._post("/update")
        self.assertEqual(code, 409, "nothing known → nothing to act on: " + body)
        km._UPDATE_AVAIL[0] = "v0.7.0"
        ran = []
        with mock.patch.object(km, "_run_update", side_effect=lambda tag: ran.append(tag) or True):
            code, body = self._post("/update")
        self.assertEqual((code, ran), (200, ["v0.7.0"]))

    def test_post_update_converges_main_drift_when_no_release_is_pending(self):
        # the drift click is a REAL restart, so the converge is stubbed: a live manager must never hear
        # a test (2026-08-14: this exact route, exercised unstubbed while real drift existed, restart-
        # stormed the machine running the suite — each run bounced every kernel on the box)
        km._UPDATE_AVAIL[0] = ""
        km._MAIN_DRIFT[0], km._MAIN_DRIFT[1] = "a" * 40, ""
        ran = []
        with mock.patch.object(km, "_update_channel", return_value="dev"), \
             mock.patch.object(km, "_run_main_update",
                               side_effect=lambda kind, immediate=False, target="":
                               ran.append((kind, immediate, target))):
            code, body = self._post("/update")
            self.assertEqual(code, 200)
            self.assertIn("converging", body)
            for _ in range(200):                       # the route hands off to a daemon thread
                if ran:
                    break
                time.sleep(0.01)
        self.assertEqual(ran, [("pull", True, "a" * 40)],
                         "the click is the user's own cut, BOUND to the sha the kernel offered")
        km._MAIN_DRIFT[0] = ""

    def test_post_update_refuses_main_convergence_on_the_stable_channel(self):
        # stable installs (the DEFAULT — trust root or none) move only via signed release tags;
        # the banner never fires there, but the route itself must not trust the banner (the
        # user's audits, 2026-08-17). Channel absent = stable: nobody converges by default.
        km._UPDATE_AVAIL[0] = ""
        km._MAIN_DRIFT[0], km._MAIN_DRIFT[1] = "a" * 40, ""
        try:
            with mock.patch.object(km, "_update_channel", return_value="stable"), \
                 mock.patch.object(km, "_run_main_update",
                                   side_effect=AssertionError("must not converge")):
                code, body = self._post("/update")
            self.assertEqual(code, 409)
            self.assertIn("signed release tags", body)
        finally:
            km._MAIN_DRIFT[0] = ""


class Wiring(unittest.TestCase):
    """Source pins: the check runs at boot, the banner ships on the landing page, the gear posts."""

    @classmethod
    def setUpClass(cls):
        cls.src = Path(os.path.join(BIN, "romp-kernel")).resolve().read_text()
        cls.gear = (Path(BIN).parent / "ui" / "webview" / "gear.js").read_text()

    def test_boot_starts_the_check_loop_and_files_the_last_report(self):
        # the LOOP, not a one-shot: kernels outlive browser tabs by weeks (the user 2026-08-09),
        # so a boot-only check would almost never fire
        self.assertIn("threading.Thread(target=_update_check_loop, daemon=True).start()", self.src)
        self.assertIn("_consume_update_report()                                   # last self-update's outcome", self.src)

    def test_the_banner_dismissal_is_per_release(self):
        # Not-now silences THE dismissed tag; a strictly newer release found by a later pass is
        # new information and re-offers
        self.assertIn("if(waiting||!tag||tag===dismissedTag)return;", self.src)
        self.assertIn("dm.onclick=function(){dismissedTag=curTag;", self.src)

    def test_the_landing_ships_the_banner_and_the_shell_relay(self):
        self.assertIn("_stale_block(v) + _update_block() + _rdrift_block()", self.src)
        self.assertIn("window.__rompUpdateOffer=offer", self.src)
        self.assertIn("m.type==='updateAvail'&&window.__rompUpdateOffer", self.src)

    def test_offers_retire_on_the_truth_not_in_an_error_banner(self):
        # the user 2026-08-15: a stale offer survived the restart it asked for; its Update click hit a
        # converged kernel and painted "Could not start the update" over a working dashboard. The offer
        # now (a) carries + checks the pushing kernel's boot, (b) retires when the 30s /version poll
        # sees a new boot, (c) treats the 409 as "already done" — retire + Log, never a dead-end error,
        # and (d) can be re-derived on page load from /update-check's new drift fields.
        self.assertIn("if(boot&&bootNow&&boot!==bootNow)return;", self.src)
        self.assertIn("window.__rompUpdBoot=function(b)", self.src)
        self.assertIn("if(v&&v.boot&&window.__rompUpdBoot)window.__rompUpdBoot(v.boot);", self.src)
        self.assertIn("/no newer release or main commit/.test(em)", self.src)
        self.assertIn("__rompNotify('sync','the update this prompt offered already ran", self.src)
        self.assertIn("else if(d.drift&&d.driftSha)offer(d.cur||'',d.driftSha,d.drift);", self.src)
        # …and an update starting ANYWHERE flips every window to the in-flight wait
        self.assertIn("if(state==='running'){waiting=true;go.hidden=true;dm.hidden=true;", self.src)
        self.assertIn('{"type": "updateAvail", "state": "running", "boot": _BOOT_ID}', self.src)

    def test_the_gear_offers_the_three_modes_and_posts_the_pick(self):
        self.assertIn("id=rs-updates", self.gear)
        for opt in ("value=ask", "value=auto", "value=off"):
            self.assertIn(opt, self.gear)
        self.assertIn("post({ type: 'setUpdateMode', mode: upm.value })", self.gear)
        self.assertIn("upm.value = v.updateMode", self.gear)
        self.assertIn('msg.get("type") == "setUpdateMode"', self.src)


if __name__ == "__main__":
    unittest.main()
