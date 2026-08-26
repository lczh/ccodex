"""Remote version-drift detection + `romp update` (the user 2026-07-04): the local kernel polls each attached
remote's /version, flags one running an OLDER commit (outOfDate), and offers to pull+restart it behind the
scenes. `POST /tunnels/update` runs the ssh git-pull + restart; the rail popover + a top banner surface it.
SYNTHETIC hosts; subprocess/http are stubbed so nothing actually launches or connects."""
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()


class _R:
    def __init__(self, out="", err="", rc=0):
        self.stdout, self.stderr, self.returncode = out, err, rc


class VersionDrift(unittest.TestCase):
    def setUp(self):
        self._hc = dict(km._HEAD_CACHE)
        km._HEAD_CACHE.update(ts=9e18, full="abc12340000", short="abc1234")   # pin local HEAD, skip the subprocess

    def tearDown(self):
        km._HEAD_CACHE.clear(); km._HEAD_CACHE.update(self._hc)

    def test_sha_base_strips_dirty(self):
        self.assertEqual(km._sha_base("abc1234-dirty"), "abc1234")
        self.assertEqual(km._sha_base("abc1234"), "abc1234")
        self.assertIsNone(km._sha_base(""))
        self.assertIsNone(km._sha_base(None))

    def test_shas_agree_tolerates_different_short_lengths(self):
        self.assertTrue(km._shas_agree("abc1234", "abc1234567"), "one a prefix of the other → same commit")
        self.assertTrue(km._shas_agree("abc1234-dirty", "abc1234"), "'-dirty' ignored")
        self.assertFalse(km._shas_agree("abc1234", "def5678"))
        self.assertFalse(km._shas_agree("abc1234", ""))

    def test_drift_is_measured_against_live_HEAD_and_CLEARS_when_matched(self):
        # the fix (the user 2026-07-04): drift compares the remote to the LIVE HEAD — the SAME thing the push
        # sends — so once the remote is pushed to HEAD the flag goes away (it used to compare to the kernel's
        # cached startup sha while the push sent HEAD, so it never reconciled → banner stuck forever).
        self.assertTrue(km._remote_out_of_date({"kernel_sha": "def5678"}), "different commit → out of date")
        self.assertFalse(km._remote_out_of_date({"kernel_sha": "abc1234"}), "remote pushed to HEAD → CLEARS")
        self.assertFalse(km._remote_out_of_date({"kernel_sha": "abc12345"}), "same commit, longer short → clears")
        self.assertFalse(km._remote_out_of_date({}), "unknown remote sha → not flagged")
        self.assertFalse(km._remote_out_of_date({"kernel_sha": ""}), "blank remote sha → not flagged")

    def test_remote_public_exposes_version_fields(self):
        pub = km._remote_public({"host": "TESTHOST", "kernel_port": 29855, "local_port": 8801, "token": "t",
                                 "status": "up", "sids": [], "kernel_sha": "def5678"})
        self.assertTrue(pub["hasToken"])
        self.assertNotIn("token", pub, "remote credentials stay server-side behind the relay")
        self.assertEqual(pub["kernelSha"], "def5678")
        self.assertEqual(pub["localSha"], "abc1234", "localSha is the live HEAD short (what a push would send)")
        self.assertTrue(pub["outOfDate"])

    def test_remote_public_rejects_malicious_version_metadata(self):
        attack = '<img src=x onerror="globalThis.pwned=1">'
        pub = km._remote_public({"host": "TESTHOST", "kernel_port": 29855, "local_port": 8801,
                                 "status": "up", "kernel_sha": attack, "kernel_ver": attack})
        self.assertEqual((pub["kernelSha"], pub["kernelVer"]), ("", ""))
        self.assertFalse(pub["outOfDate"], "an invalid revision must not reach drift git commands")

    def test_version_poll_accepts_only_sha_and_safe_release_atoms(self):
        old = km.http.client.HTTPConnection

        class Resp:
            status = 200
            def __init__(self, payload): self.payload = payload
            def read(self): return json.dumps(self.payload).encode()

        class Conn:
            payload = {"kernel_sha": "def5678", "kernel_ver": '<svg onload="pwned=1">'}
            def __init__(self, *a, **k): pass
            def request(self, *a, **k): pass
            def getresponse(self): return Resp(self.payload)
            def close(self): pass

        try:
            km.http.client.HTTPConnection = Conn
            self.assertEqual(km._poll_remote_version({"local_port": 1, "token": "t"}),
                             {"sha": "def5678", "ver": "", "autoNudge": None, "settings": None})
            Conn.payload = {"kernel_sha": '<img src=x onerror="pwned=1">', "kernel_ver": "v1.2.3"}
            self.assertIsNone(km._poll_remote_version({"local_port": 1, "token": "t"}))
            # the settings-sync atoms ride the same poll and are type-narrowed at the same boundary:
            # a remote that answers with attacker-shaped values yields None fields, never the values
            Conn.payload = {"kernel_sha": "def5678", "kernel_ver": "v1.2.3",
                            "autoNudge": "yes please", "settings": ["not", "a", "dict"]}
            self.assertEqual(km._poll_remote_version({"local_port": 1, "token": "t"}),
                             {"sha": "def5678", "ver": "v1.2.3", "autoNudge": None, "settings": None})
        finally:
            km.http.client.HTTPConnection = old


class UpdateRemote(unittest.TestCase):
    """PEER-TO-PEER update (the user 2026-07-04): push local committed HEAD to the remote (no GitHub), refuse on
    a dirty/diverged remote, restart. Three subprocess calls — ssh-discover, git-push, ssh-apply — are dispatched
    by inspecting argv so each case can drive them independently."""
    LFULL = "1" * 40                        # local HEAD (full sha) the push sends
    RHEAD = "2" * 40                         # a remote at a DIFFERENT (older) commit

    def setUp(self):
        self._run, self._hc = km.subprocess.run, dict(km._HEAD_CACHE)
        km._HEAD_CACHE.update(ts=0.0, full=None, short=None)   # force _local_head to consult the mocked git

    def tearDown(self):
        km.subprocess.run = self._run
        km._HEAD_CACHE.clear(); km._HEAD_CACHE.update(self._hc)

    def _wire(self, rhead=None, dirty="", disc_out=None, push_rc=0, push_err="", apply_out="SYNCED:1111111"):
        """Install a dispatching subprocess mock; returns the list of argv it saw."""
        if disc_out is None:
            disc_out = "DIR:/home/u/romp\nHEAD:%s\nDIRTY:%s" % (rhead if rhead is not None else self.RHEAD, dirty)
        calls = []

        def fake(argv, **kw):
            calls.append(argv)
            if argv[0] == "git" and "push" in argv:
                return _R(err=push_err, rc=push_rc)
            if argv[0] == "git" and "rev-parse" in argv and "HEAD" in argv:   # _local_head
                return _R(out=self.LFULL)
            cmd = argv[-1]                                                     # ssh: dispatch on the remote command
            if "for d in" in cmd:
                return _R(out=disc_out)
            if "merge-base" in cmd or "merge --ff-only" in cmd:
                return _R(out=apply_out)
            return _R()
        km.subprocess.run = fake
        return calls

    def test_no_host_is_a_no_op(self):
        self.assertEqual(km._update_remote(""), (False, "no host"))

    def test_a_clean_ancestor_remote_is_pushed_reset_and_restarted(self):
        calls = self._wire(apply_out="SYNCED:1111111")
        ok, detail = km._update_remote("TESTHOST")
        self.assertTrue(ok)
        self.assertIn("synced to 1111111", detail)
        # it force-pushed local HEAD to a scratch ref at host:remote-dir
        push = next(a for a in calls if a[0] == "git" and "push" in a)
        self.assertIn("--force", push)
        self.assertIn("TESTHOST:/home/u/romp", push)
        self.assertTrue(any(str(x).startswith(self.LFULL + ":refs/heads/") for x in push),
                        "pushes the exact uncached HEAD snapshot to a scratch ref")

    def test_a_synced_tag_for_any_other_commit_is_a_refusal(self):
        self._wire(apply_out="SYNCED:abcdef0")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("not the exact commit", detail)
        self.assertIn(self.LFULL[:8], detail)

    def test_the_push_ignores_the_display_cache_and_transports_one_exact_snapshot(self):
        km._HEAD_CACHE.update(ts=9e18, full="a" * 40, short="a" * 8)
        calls = self._wire(apply_out="SYNCED:1111111")
        ok, _detail = km._update_remote("TESTHOST")
        self.assertTrue(ok)
        push = next(a for a in calls if a[0] == "git" and "push" in a)
        self.assertIn(self.LFULL + ":refs/heads/" + km._P2P_REF, push)
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        self.assertIn('"$GD/romp-update.lock" "$R" ' + self.LFULL, apply,
                      "the transaction applies the same SHA the push transported")

    def test_equal_heads_with_an_armed_latch_still_run_the_settle_transaction(self):
        calls = self._wire(disc_out="DIR:/home/u/romp\nHEAD:%s\nDIRTY:\nLATCH:1" % self.LFULL)
        ok, detail = km._update_remote("TESTHOST")
        self.assertTrue(ok)
        self.assertIn("synced", detail)
        self.assertTrue(any(a[0] == "git" and "push" in a for a in calls),
                        "HEAD equality alone cannot bypass the remote recovery record")

    def test_equal_heads_with_an_unreadable_latch_still_run_the_settle_transaction(self):
        calls = self._wire(rhead=self.LFULL)  # no LATCH line at all -> UNKNOWN
        ok, detail = km._update_remote("TESTHOST")
        self.assertTrue(ok)
        self.assertIn("synced", detail)
        self.assertTrue(any(a[0] == "git" and "push" in a for a in calls),
                        "an unreadable latch is never presumed clear")

    def test_equal_heads_with_a_clear_latch_are_a_true_no_op(self):
        # the r43 verification's P1 (no restart, no STABLENOW on an up-to-date stable host),
        # DECIDED INSIDE THE WRAPPER'S FLOCK now (the v1.3.18 audit: a latch could arm between
        # the local discovery and the local return, bypassing the settle). The wrapper reads
        # HEAD + the latch fresh under its lock and exits INSYNC before the channel gate and
        # before any kill; the local report maps it back to "already up to date".
        calls = self._wire(disc_out="DIR:/home/u/romp\nHEAD:%s\nDIRTY:\nLATCH:0" % self.LFULL,
                           apply_out="INSYNC:1111111")
        ok, detail = km._update_remote("TESTHOST")
        self.assertTrue(ok, detail)
        self.assertIn("already up to date", detail)
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        self.assertIn("sys.exit(36)", apply, "the in-sync verdict is the wrapper's, under flock")
        self.assertLess(apply.index("sys.exit(36)"), apply.index("if v is not None:"),
                        "…decided BEFORE the channel gate: a stable host at the target commit "
                        "reads in-sync, never STABLENOW-unbootable (the r43 P1)")
        self.assertLess(apply.index("sys.exit(36)"), apply.index('["pkill","-f"'),
                        "…and before any kill: an up-to-date healthy kernel is never restarted")

    def test_an_insync_report_must_name_the_pushed_commit(self):
        self._wire(disc_out="DIR:/home/u/romp\nHEAD:%s\nDIRTY:\nLATCH:0" % self.LFULL,
                   apply_out="INSYNC:9999999")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("not the pushed one", detail)

    def test_the_discover_step_reports_the_latch_state(self):
        calls = self._wire()
        km._update_remote("TESTHOST")
        disc = next(a[-1] for a in calls if isinstance(a[-1], str) and "for d in" in a[-1])
        self.assertIn('romp-install-failed', disc, "the discover step stats the install latch")
        self.assertIn('echo "LATCH:UNKNOWN"', disc,
                      "an unresolvable git dir reports UNKNOWN, never a presumed-clear 0")

    def test_a_dirty_local_is_not_refused_it_pushes_committed_head(self):
        # "just take what is committed on local" (the user 2026-07-04): a dirty working tree is NOT a blocker —
        # _update_remote pushes the committed HEAD and never asks you to commit first.
        self._wire(apply_out="SYNCED:1111111")
        ok, detail = km._update_remote("TESTHOST")
        self.assertTrue(ok)
        self.assertNotIn("commit", detail.lower())

    def test_no_local_checkout_fails_cleanly(self):
        def fake(argv, **kw):
            if argv[0] == "git" and "rev-parse" in argv:
                return _R(rc=1)                            # not a git checkout
            return _R()
        km.subprocess.run = fake
        km._HEAD_CACHE.update(ts=0.0, full=None, short=None)
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("git checkout", detail)

    def test_refuses_a_dirty_remote_without_clobbering(self):
        self._wire(dirty="M")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("uncommitted changes", detail)

    def test_refuses_a_diverged_remote(self):
        self._wire(apply_out="DIVERGED")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("diverged", detail)

    def test_a_failed_status_reads_as_unknown_never_clean(self):
        # a status command that ERRORS used to read as a clean tree — green-lighting a reset of
        # code it could not actually see (the user's audit, 2026-08-17); both the discover probe
        # and the apply-step recheck must refuse on it, named
        self._wire(dirty="STATERR")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("state is unknown", detail)
        calls = self._wire(apply_out="STATERR")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("state is unknown", detail)
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        self.assertIn("STATERR", apply.partition("merge --ff-only")[0],
                      "the rc check sits before the move, same shell")
        disc = next(a[-1] for a in calls if isinstance(a[-1], str) and "for d in" in a[-1])
        self.assertIn("STATERR", disc, "the discover probe distinguishes error from clean too")

    def test_the_landed_verify_tags_map_to_their_own_messages(self):
        # the r33 mutant hunt: swapping the NOTLANDED and HEADUNKNOWN branches stayed green —
        # inverted guidance for the two states the landed-verify exists to distinguish
        self._wire(apply_out="NOTLANDED")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("did not land", detail)
        self.assertNotIn("HEAD cannot be read", detail)
        self._wire(apply_out="HEADUNKNOWN")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("HEAD cannot be read", detail)
        self.assertIn("stays armed", detail)
        self._wire(apply_out="RESTOREFAIL")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("restoring the prior install record failed", detail)
        self.assertIn("armed latch stays", detail)
        self._wire(apply_out="DIRTYPOSTMOVE")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("tree changed while updating", detail)
        self.assertIn("latch stays armed", detail)
        self._wire(apply_out="STATERRPOSTMOVE")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("reading the remote's tree state failed", detail)
        self.assertNotIn("tree changed", detail)
        self._wire(apply_out="DIRTYPOSTINSTALL")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("tracked files changed", detail)
        self.assertIn("not reported", detail)

    def test_an_install_failure_on_the_remote_is_its_own_verdict(self):
        # the apply used to lock the reset alone and never install at all (the user's audit,
        # 2026-08-17); a failed install now leaves the remote's latch armed and says so
        self._wire(apply_out="INSTALLFAIL")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("install.sh failed", detail)
        self.assertIn("boot heal", detail)
        calls = self._wire(apply_out="SYNCED:1111111")
        km._update_remote("TESTHOST")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        self.assertIn("install.sh", apply, "the apply transaction installs")
        self.assertIn("romp-install-failed", apply, "and arms the remote's latch before the reset")
        self.assertLess(apply.index("romp-install-failed"), apply.index('"merge","--ff-only"'),
                        "intent before the move, on the remote exactly as locally")
        self.assertIn('>>"$LOGDIR/update.log" 2>&1', apply,
                      "installer diagnostics survive in the bounded update log")
        self._wire(apply_out="CHANNELPUBFAIL")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("update-channel marker", detail)
        self.assertNotIn("install.sh failed", detail)

    def test_the_generated_shell_actually_emits_STATERR_when_status_dies(self):
        # the string-level pins above never EXECUTE the shell: replanting the audited bug (a dead
        # status reading as clean) passed all of them (the adversarial review, 2026-08-17). Run
        # the real generated scripts against a fixture checkout whose git can't report status.
        import tempfile
        from pathlib import Path
        calls = self._wire(apply_out="SYNCED:1111111")
        km._update_remote("TESTHOST")
        disc = next(a[-1] for a in calls if isinstance(a[-1], str) and "for d in" in a[-1])
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            fix = home / "romp"
            (fix / ".git").mkdir(parents=True)
            fakebin = Path(td) / "bin"
            fakebin.mkdir()
            log = Path(td) / "ops"
            (fakebin / "git").write_text(
                "#!/bin/sh\necho \"$*\" >> '%s'\ncase \" $* \" in\n"
                "  *' status '*) echo 'fatal: index corrupt' >&2; exit 128;;\n"
                "  *' merge '*) touch \"$(dirname \"$0\")/.head-moved\";;\n"
                "  *' rev-parse HEAD'*) if [ -e \"$(dirname \"$0\")/.head-moved\" ]; then echo 1111111111111111111111111111111111111111; else echo 0000000000000000000000000000000000000000; fi;;\n"
                "  *' show '*) cat \"$2/install.sh\";;\n"
                "  *' archive '*) tar -c -C \"$2\" install.sh;;\n"
                "esac\nexit 0\n" % log)
            (fakebin / "git").chmod(0o755)
            env = dict(os.environ, HOME=str(home),
                       PATH="%s%s%s" % (fakebin, os.pathsep, os.environ.get("PATH", "")))
            # km.subprocess IS this module's subprocess (one singleton), so _wire's fake is still
            # installed — the REAL runner was saved by setUp exactly for this
            d = self._run(["sh", "-c", disc], env=env, capture_output=True, text=True, timeout=30)
            self.assertIn("DIRTY:STATERR", d.stdout,
                          "the DISCOVER probe, executed, reports the dead status as an error")
            # the apply script embeds its own $R; point it at the fixture and run it
            apply_r = apply.replace("R=/home/u/romp;", "R=%s;" % fix).replace(
                "R=/home/u/romp ", "R=%s " % fix)
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=30)
            self.assertIn("STATERR", a.stdout,
                          "the APPLY recheck, executed, refuses on a dead status")
            ops = log.read_text() if log.exists() else ""
            self.assertNotIn("merge --ff-only", ops, "and the move never ran")

    def test_the_INSTALLFAIL_wrapper_executed_arms_the_latch_and_a_pass_spends_it(self):
        # string pins alone let the audited replant pass (this very file documents why); run the
        # real apply script against a fixture whose install.sh fails, then one whose passes
        import tempfile
        from pathlib import Path
        calls = self._wire(apply_out="SYNCED:1111111")
        km._update_remote("TESTHOST")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        with tempfile.TemporaryDirectory() as td:
            fix = Path(td) / "romp"
            gd = fix / ".git"
            gd.mkdir(parents=True)
            (gd / "romp-update-channel").write_text("dev\n")   # a dev remote: mechanics under test
            fakebin = Path(td) / "bin"
            fakebin.mkdir()
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' rev-parse --short=8 '*) echo deadbee2;;\n"
                "  *' merge '*) touch \"$(dirname \"$0\")/.head-moved\";;\n"
                "  *' rev-parse HEAD'*) if [ -e \"$(dirname \"$0\")/.head-moved\" ]; then echo 1111111111111111111111111111111111111111; else echo 0000000000000000000000000000000000000000; fi;;\n"
                "  *' show '*) cat \"$2/install.sh\";;\n"
                "  *' archive '*) tar -c -C \"$2\" install.sh;;\n"
                "esac\nexit 0\n" % gd)
            (fakebin / "git").chmod(0o755)
            (fix / "install.sh").write_text("#!/bin/sh\nexit 1\n")
            (fix / "install.sh").chmod(0o755)
            env = dict(os.environ, PATH="%s%s%s" % (fakebin, os.pathsep, os.environ.get("PATH", "")))
            apply_r = apply.replace("R=/home/u/romp;", "R=%s;" % fix)
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("INSTALLFAIL", a.stdout, "a failed install is its own executed verdict")
            self.assertEqual((gd / "romp-install-failed").read_text().strip(), "deadbee2",
                             "the remote latch stays ARMED for its boot heal")
            (fix / "install.sh").write_text("#!/bin/sh\nexit 0\n")
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertNotIn("INSTALLFAIL", a.stdout)
            self.assertFalse((gd / "romp-install-failed").exists(),
                             "a passing install spends the latch, executed end to end")
            # the reset must target the exact 40-char sha the push validated — the scratch ref
            # is force-updated by any concurrent sender (the user's audit, 2026-08-18). The sha
            # rides into the wrapper as its argv target, and the wrapper resets to that target.
            self.assertIn('"$R" %s' % ("1" * 40), apply_r,
                          "the wrapper's target argv is the pinned sha, never the mutable ref")
            self.assertIn('"merge","--ff-only",target', apply_r.replace("'", '"'),
                          "and the MOVE is a fast-forward that git itself refuses on conflicting "
                          "local edits — a reset --hard erased edits saved during the prior heal "
                          "(the v1.3.12 audit's P1)")
            # the ancestry check lives INSIDE the locked wrapper; a non-ancestor target must
            # refuse with DIVERGED, executed — make merge-base fail and rerun
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' merge-base '*) exit 1;;\n"
                "  *' rev-parse --short=8 '*) echo deadbee2;;\n"
                "  *' show '*) cat \"$2/install.sh\";;\n"
                "  *' archive '*) tar -c -C \"$2\" install.sh;;\n"
                "esac\nexit 0\n" % gd)
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("DIVERGED", a.stdout, "divergence is decided UNDER the lock, executed")
            self.assertFalse((gd / "romp-install-failed").exists(),
                             "a diverged refusal arms nothing and moves nothing")
            # dirtiness is ALSO decided under the lock now (the pre-lock probe raced an edit
            # landing in the gap — the user's audit, 2026-08-18): a dirty answer from the locked
            # wrapper's own status check refuses before arm/reset
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' merge-base '*) rm -f \"$(dirname \"$0\")/.head-moved\"; exit 0;;\n"
                "  *' status '*) echo ' M peer-edit.py';;\n"
                "  *' rev-parse --short=8 '*) echo deadbee2;;\n"
                "  *' show '*) cat \"$2/install.sh\";;\n"
                "  *' archive '*) tar -c -C \"$2\" install.sh;;\n"
                "esac\nexit 0\n" % gd)
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("DIRTYNOW", a.stdout, "the locked wrapper's own dirty check refuses")
            self.assertFalse((gd / "romp-install-failed").exists())

    def test_edits_landing_during_the_heal_or_after_the_move_never_ride_into_the_install(self):
        # the v1.3.13 audit's P1, executed there: the only clean check ran BEFORE the settle
        # heal (minutes of install.sh) — nonconflicting edits landing meanwhile rode into the
        # install while the wrapper reported the target sha (merge --ff-only protects only
        # CONFLICTING edits)
        import tempfile
        from pathlib import Path
        calls = self._wire(apply_out="SYNCED:1111111")
        km._update_remote("TESTHOST")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        with tempfile.TemporaryDirectory() as td:
            fix = Path(td) / "romp"
            gd = fix / ".git"
            gd.mkdir(parents=True)
            (gd / "romp-update-channel").write_text("dev\n")
            fakebin = Path(td) / "bin"
            fakebin.mkdir()
            nstat = Path(td) / "status-count"
            ops = Path(td) / "ops.log"
            # the tree turns dirty at the THIRD status read — the shell prefix probes once, the
            # wrapper's entry check is read 2, and the POST-SETTLE recheck (this fix) is read 3:
            # the heal (a full install.sh) dirtied the tree after both earlier checks
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' merge-base '*) rm -f \"$(dirname \"$0\")/.head-moved\"; exit 0;;\n"
                "  *' status '*) echo x >> '%s'; [ $(wc -l < '%s') -ge 3 ] && echo ' M raced.py';;\n"
                "  *' rev-parse --short=8 '*) echo deadbee2;;\n"
                "  *' merge '*) touch \"$(dirname \"$0\")/.head-moved\";;\n"
                "  *' rev-parse HEAD'*) if [ -e \"$(dirname \"$0\")/.head-moved\" ]; then echo 1111111111111111111111111111111111111111; else echo 0000000000000000000000000000000000000000; fi;;\n"
                "  *' merge --ff-only '*) echo MOVED >> '%s';;\n"
                "  *' show '*) cat \"$2/install.sh\";;\n"
                "  *' archive '*) tar -c -C \"$2\" install.sh;;\n"
                "esac\nexit 0\n" % (gd, nstat, nstat, ops))
            (fakebin / "git").chmod(0o755)
            (fix / "install.sh").write_text("#!/bin/sh\necho INSTALLED >> '%s'\nexit 0\n" % ops)
            (fix / "install.sh").chmod(0o755)
            env = dict(os.environ, PATH="%s%s%s" % (fakebin, os.pathsep, os.environ.get("PATH", "")))
            apply_r = apply.replace("R=/home/u/romp;", "R=%s;" % fix)
            (gd / "romp-install-failed").write_text("deadbee2 dev")   # a prior to settle-heal
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("DIRTYPOSTHEAL", a.stdout,
                          "the successful heal keeps its record armed when the tree changed")
            self.assertNotIn("MOVED", ops.read_text() if ops.exists() else "")
            # and dirty at the FOURTH read (post-move st3): HEAD moved, install must NOT run —
            # the armed latch stays for the boot heal
            nstat.unlink()
            ops.unlink(missing_ok=True)
            (gd / "romp-install-failed").unlink(missing_ok=True)   # the heal SPENT it before
            #                                                        the post-settle refusal
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' merge-base '*) rm -f \"$(dirname \"$0\")/.head-moved\"; exit 0;;\n"
                "  *' status '*) echo x >> '%s'; [ $(wc -l < '%s') -ge 4 ] && echo ' M raced.py';;\n"
                "  *' rev-parse --short=8 '*) echo deadbee2;;\n"
                "  *' merge '*) touch \"$(dirname \"$0\")/.head-moved\";;\n"
                "  *' rev-parse HEAD'*) if [ -e \"$(dirname \"$0\")/.head-moved\" ]; then echo 1111111111111111111111111111111111111111; else echo 0000000000000000000000000000000000000000; fi;;\n"
                "  *' merge --ff-only '*) echo MOVED >> '%s';;\n"
                "  *' show '*) cat \"$2/install.sh\";;\n"
                "  *' archive '*) tar -c -C \"$2\" install.sh;;\n"
                "esac\nexit 0\n" % (gd, nstat, nstat, ops))
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("DIRTYPOSTMOVE", a.stdout)
            self.assertNotIn("INSTALLED", ops.read_text() if ops.exists() else "",
                             "nothing installs on a tree that changed after the move")
            self.assertEqual((gd / "romp-install-failed").read_text().strip(), "deadbee2",
                             "the armed latch stays for the boot heal")
            # a status COMMAND FAILURE post-move is not "tree changed" — it has its own verdict
            # (the r37 verification: st3 folded rc!=0 into DIRTYPOSTMOVE, telling the user to
            # clean a tree that never changed)
            nstat.unlink()
            ops.unlink(missing_ok=True)
            (gd / "romp-install-failed").unlink(missing_ok=True)
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' merge-base '*) rm -f \"$(dirname \"$0\")/.head-moved\"; exit 0;;\n"
                "  *' status '*) echo x >> '%s'; [ $(wc -l < '%s') -ge 4 ] && exit 1;;\n"
                "  *' rev-parse --short=8 '*) echo deadbee2;;\n"
                "  *' merge '*) touch \"$(dirname \"$0\")/.head-moved\";;\n"
                "  *' rev-parse HEAD'*) if [ -e \"$(dirname \"$0\")/.head-moved\" ]; then echo 1111111111111111111111111111111111111111; else echo 0000000000000000000000000000000000000000; fi;;\n"
                "  *' show '*) cat \"$2/install.sh\";;\n"
                "  *' archive '*) tar -c -C \"$2\" install.sh;;\n"
                "esac\nexit 0\n" % (gd, nstat, nstat))
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("STATERRPOSTMOVE", a.stdout)
            self.assertNotIn("DIRTYPOSTMOVE", a.stdout)
            self.assertEqual((gd / "romp-install-failed").read_text().strip(), "deadbee2")
            # the POST-SETTLE recheck's POSITION is pinned in the generated text: hoisting it
            # above the settle heal re-opened the P1 while every executed leg stayed green (the
            # r37 mutant hunt — the fake models the heal as a read counter, not a real install)
            self.assertLess(apply_r.index("merge_carry"), apply_r.index("st2="),
                            "st2 sits AFTER the settle-heal block")
            self.assertLess(apply_r.index("st2="), apply_r.index('tmp=lp+".tmp"'),
                            "and BEFORE the arm")

    def test_a_heal_spends_nothing_when_head_moves_while_install_runs(self):
        calls = self._wire(apply_out="SYNCED:1111111")
        km._update_remote("TESTHOST")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        with tempfile.TemporaryDirectory() as td:
            fix = pathlib.Path(td) / "romp"
            gd = fix / ".git"
            gd.mkdir(parents=True)
            fakebin = pathlib.Path(td) / "bin"
            fakebin.mkdir()
            moved = pathlib.Path(td) / "head-moved"
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' rev-parse --short=8 HEAD'*) echo aaaaaaaa;;\n"
                "  *' rev-parse --short=8 '*) echo 11111111;;\n"
                "  *' rev-parse HEAD'*) if [ -e '%s' ]; then printf '%%040d\\n' 2; "
                "else printf '%%040d\\n' 1; fi;;\n"
                "  *' show '*) cat \"$2/install.sh\";;\n"
                "  *' archive '*) tar -c -C \"$2\" install.sh;;\n"
                "esac\nexit 0\n" % (gd, moved))
            (fakebin / "git").chmod(0o755)
            (fix / "install.sh").write_text("#!/bin/sh\ntouch '%s'\nexit 0\n" % moved)
            (fix / "install.sh").chmod(0o755)
            (gd / "romp-update-channel").write_text("dev\n")
            (gd / "romp-install-failed").write_text("aaaaaaaa stable")
            env = dict(os.environ, PATH="%s%s%s" % (fakebin, os.pathsep,
                                                      os.environ.get("PATH", "")))
            apply_r = apply.replace("R=/home/u/romp;", "R=%s;" % fix)
            a = self._run(["bash", "-c", apply_r], env=env,
                          capture_output=True, text=True, timeout=60)
            self.assertIn("HEADMOVEDPOSTHEAL", a.stdout)
            self.assertEqual((gd / "romp-install-failed").read_text().strip(),
                             "aaaaaaaa stable")
            self.assertEqual((gd / "romp-update-channel").read_text().strip(), "dev")

    def test_a_heal_checks_head_after_its_post_install_status_probe(self):
        calls = self._wire(apply_out="SYNCED:1111111")
        km._update_remote("TESTHOST")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        with tempfile.TemporaryDirectory() as td:
            fix = pathlib.Path(td) / "romp"
            gd = fix / ".git"
            gd.mkdir(parents=True)
            fakebin = pathlib.Path(td) / "bin"
            fakebin.mkdir()
            moved = pathlib.Path(td) / "head-moved"
            installed = pathlib.Path(td) / "install-finished"
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' status '*) [ -e '%s' ] && touch '%s';;\n"
                "  *' rev-parse --short=8 HEAD'*) echo aaaaaaaa;;\n"
                "  *' rev-parse --short=8 '*) echo 11111111;;\n"
                "  *' rev-parse HEAD'*) if [ -e '%s' ]; then printf '%%040d\\n' 2; "
                "else printf '%%040d\\n' 1; fi;;\n"
                "  *' show '*) cat \"$2/install.sh\";;\n"
                "  *' archive '*) tar -c -C \"$2\" install.sh;;\n"
                "esac\nexit 0\n" % (gd, installed, moved, moved))
            (fakebin / "git").chmod(0o755)
            (fix / "install.sh").write_text("#!/bin/sh\ntouch '%s'\nexit 0\n" % installed)
            (fix / "install.sh").chmod(0o755)
            (gd / "romp-update-channel").write_text("dev\n")
            (gd / "romp-install-failed").write_text("aaaaaaaa stable")
            env = dict(os.environ, PATH="%s%s%s" % (fakebin, os.pathsep,
                                                      os.environ.get("PATH", "")))
            apply_r = apply.replace("R=/home/u/romp;", "R=%s;" % fix)
            result = self._run(["bash", "-c", apply_r], env=env,
                               capture_output=True, text=True, timeout=60)
            self.assertIn("HEADMOVEDPOSTHEAL", result.stdout)
            self.assertEqual((gd / "romp-install-failed").read_text().strip(),
                             "aaaaaaaa stable")
            self.assertEqual((gd / "romp-update-channel").read_text().strip(), "dev")

    def test_a_writer_racing_the_install_never_executes_and_never_reports_success(self):
        # the v1.3.14 audit's P1, executed there: install.sh was replaced after the final clean
        # snapshot and the wrapper EXECUTED the replacement and reported SYNCED. The install
        # entry is the TARGET COMMIT's committed bytes now (git show), and success is reported
        # only after a post-install verify — changed bytes neither execute nor produce SYNCED.
        import tempfile
        from pathlib import Path
        calls = self._wire(apply_out="SYNCED:1111111")
        km._update_remote("TESTHOST")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        with tempfile.TemporaryDirectory() as td:
            fix = Path(td) / "romp"
            gd = fix / ".git"
            gd.mkdir(parents=True)
            (gd / "romp-update-channel").write_text("dev\n")
            fakebin = Path(td) / "bin"
            fakebin.mkdir()
            ops = Path(td) / "ops.log"
            commitdir = Path(td) / "committed-tree"
            commitdir.mkdir()
            committed = commitdir / "install.sh"
            # the committed bytes prove WHERE they ran from and WHAT they were told to install:
            # the snapshot installer executes them from an immutable staging under the git dir
            # (dirname $0), with ROMP_INSTALL_TARGET naming the real checkout — the split the
            # real install.sh uses for code-vs-target (the v1.3.16 audit's P1.1; the r42
            # dirname-guard lesson rides the TARGET now)
            committed.write_text(
                "#!/bin/sh\nD=\"$(cd \"$(dirname \"$0\")\" && pwd)\"\n"
                "echo \"COMMITTED SELF=$D TARGET=${ROMP_INSTALL_TARGET:-}\" >> '%s'\nexit 0\n" % ops)
            # the COMMITTED tree comes from `git archive`; the TREE's install.sh is the racing
            # writer's replacement
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' merge-base '*) rm -f \"$(dirname \"$0\")/.head-moved\"; exit 0;;\n"
                "  *' show '*) cat '%s';;\n"
                "  *' archive '*) tar -c -C '%s' install.sh;;\n"
                "  *' rev-parse --short=8 '*) echo deadbee2;;\n"
                "  *' merge '*) touch \"$(dirname \"$0\")/.head-moved\";;\n"
                "  *' rev-parse HEAD'*) if [ -e \"$(dirname \"$0\")/.head-moved\" ]; then echo 1111111111111111111111111111111111111111; else echo 0000000000000000000000000000000000000000; fi;;\n"
                "esac\nexit 0\n" % (gd, committed, commitdir))
            (fakebin / "git").chmod(0o755)
            (fix / "install.sh").write_text("#!/bin/sh\necho RACED >> '%s'\nexit 0\n" % ops)
            (fix / "install.sh").chmod(0o755)
            env = dict(os.environ, PATH="%s%s%s" % (fakebin, os.pathsep, os.environ.get("PATH", "")))
            apply_r = apply.replace("R=/home/u/romp;", "R=%s;" % fix)
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            log = ops.read_text() if ops.exists() else ""
            self.assertIn("COMMITTED", log, "the TARGET's committed install bytes executed")
            m = re.search(r"SELF=(\S+) TARGET=(\S+)", log)
            self.assertTrue(m, log)
            self.assertIn("romp-install-snap", m.group(1),
                          "the install executed FROM the immutable snapshot under the git dir — "
                          "so a racing writer can swap no shell child (the v1.3.16 audit's P1.1)")
            self.assertEqual(os.path.realpath(m.group(2)), os.path.realpath(fix),
                             "…while ROMP_INSTALL_TARGET names the real checkout for outputs. "
                             "REALPATH both sides: macOS mounts /var under /private/var; got: %r" % log)
            self.assertNotIn("RACED", log,
                             "the racing writer's replacement NEVER executes — the entry is "
                             "immutable (the v1.3.14 audit's executed repro)")
            # and a tree that turns dirty DURING the install yields no success: the committed
            # install itself dirties the tree, standing in for any mid-install writer
            ops.unlink(missing_ok=True)
            committed.write_text(
                "#!/bin/sh\necho COMMITTED >> '%s'\necho tampered > '%s'\nexit 0\n"
                % (ops, fix / "mid-install-edit"))
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' merge-base '*) rm -f \"$(dirname \"$0\")/.head-moved\"; exit 0;;\n"
                "  *' show '*) cat '%s';;\n"
                "  *' archive '*) tar -c -C '%s' install.sh;;\n"
                "  *' status '*) [ -e '%s' ] && echo ' M mid-install-edit';;\n"
                "  *' rev-parse --short=8 '*) echo deadbee2;;\n"
                "  *' merge '*) touch \"$(dirname \"$0\")/.head-moved\";;\n"
                "  *' rev-parse HEAD'*) if [ -e \"$(dirname \"$0\")/.head-moved\" ]; then echo 1111111111111111111111111111111111111111; else echo 0000000000000000000000000000000000000000; fi;;\n"
                "esac\nexit 0\n" % (gd, committed, commitdir, fix / "mid-install-edit"))
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("DIRTYPOSTINSTALL", a.stdout)
            self.assertNotIn("SYNCED", a.stdout,
                             "no success report from a tree that changed during the install")
            self.assertEqual((gd / "romp-install-failed").read_text().strip(), "deadbee2",
                             "the latch stays armed on ANY uncertainty (the audit's requirement)")

    def test_the_launch_probes_unlocked_and_synced_requires_a_healthy_kernel(self):
        # the r44 verification's P1: the locked probe deadlocked against the new kernel's own
        # boot gate (it polls the SAME update flock before /version ever binds) — every real
        # sync reported RESTARTFAIL. The fixture kernel WAITS ON THE FLOCK exactly like the
        # real gate, so SYNCED here is only reachable if the wrapper releases before probing.
        # And the negative leg: with no kernel ever answering, SYNCED must NOT appear — the
        # healthy() verdict has executed coverage (the r44 mutant lens: killing the probe
        # survived 262 textual tests).
        import socket
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        km._remotes = {"TESTHOST": {"host": "TESTHOST", "kernel_port": port}}
        calls = self._wire(apply_out="SYNCED:1111111")
        km._update_remote("TESTHOST")
        km._remotes = {}
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        with tempfile.TemporaryDirectory() as td:
            fix = pathlib.Path(td) / "romp"
            gd = fix / ".git"
            (fix / "bin").mkdir(parents=True)
            gd.mkdir(parents=True)
            (gd / "romp-update-channel").write_text("dev\n")
            fakebin = pathlib.Path(td) / "bin"
            fakebin.mkdir()
            commitdir = pathlib.Path(td) / "committed-tree"
            (commitdir / "bin").mkdir(parents=True)
            (commitdir / "install.sh").write_text("#!/bin/sh\nexit 0\n")
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' merge-base '*) rm -f \"$(dirname \"$0\")/.head-moved\"; exit 0;;\n"
                "  *' archive '*) tar -c -C '%s' install.sh bin;;\n"
                "  *' rev-parse --short=8 '*) echo 11111111;;\n"
                "  *' merge '*) touch \"$(dirname \"$0\")/.head-moved\";;\n"
                "  *' rev-parse HEAD'*) if [ -e \"$(dirname \"$0\")/.head-moved\" ]; then echo 1111111111111111111111111111111111111111; else echo 0000000000000000000000000000000000000000; fi;;\n"
                "esac\nexit 0\n" % (gd, commitdir))
            (fakebin / "git").chmod(0o755)
            # the wrapper execs the SNAPSHOT manager via `node` (the pinned byte stream, the
            # v1.3.17 audit's P1.2); the fixture manager is a shell stub, so the fixture node
            # is a shell trampoline
            (fakebin / "node").write_text("#!/bin/sh\nf=\"$1\"; shift; exec sh \"$f\" \"$@\"\n")
            (fakebin / "node").chmod(0o755)
            # NEVER the real pkill: this executed leg reaches the kill line
            (fakebin / "pkill").write_text("#!/bin/sh\nexit 0\n")
            (fakebin / "pkill").chmod(0o755)
            (fix / "bin" / "romp-serve").write_text("#!/bin/sh\nexit 0\n")
            (fix / "bin" / "romp-serve").chmod(0o755)
            (commitdir / "bin" / "romp-serve").write_text("#!/bin/sh\nexit 0\n")
            (commitdir / "bin" / "romp-serve").chmod(0o755)
            # the fixture kernel: a gate-faithful /version server — it BLOCKS on the update
            # flock first, exactly like bin/romp-serve's pre-exec gate, then binds
            serve_py = pathlib.Path(td) / "serve.py"
            serve_py.write_text(
                "import fcntl,json,http.server,sys\n"
                "lockp,port,sha=sys.argv[1],int(sys.argv[2]),sys.argv[3]\n"
                "f=open(lockp,'a+')\n"
                "fcntl.flock(f,fcntl.LOCK_EX)\n"
                "fcntl.flock(f,fcntl.LOCK_UN)\n"
                "class H(http.server.BaseHTTPRequestHandler):\n"
                "    def do_GET(self):\n"
                "        b=json.dumps({'kernel_sha':sha,'boot':'boot-2'}).encode()\n"
                "        self.send_response(200)\n"
                "        self.send_header('Content-Length',str(len(b)))\n"
                "        self.end_headers()\n"
                "        self.wfile.write(b)\n"
                "    def log_message(self,*a):\n"
                "        pass\n"
                "http.server.HTTPServer(('127.0.0.1',port),H).serve_forever()\n")
            (commitdir / "bin" / "romp-manager").write_text(
                "#!/bin/sh\n[ \"$1\" = ensure ] || exit 0\n"
                # nohup, not setsid: macOS has no setsid, and the fixture kernel silently never
                # started there — both macOS python gate jobs read the r44 deadlock's RESTARTFAIL
                # from a healthy wrapper (the v1.3.17 first gate attempt)
                "nohup python3 '%s' '%s' %d 11111111 >/dev/null 2>&1 &\nexit 0\n"
                % (serve_py, gd / "romp-update.lock", port))
            (commitdir / "bin" / "romp-manager").chmod(0o755)
            (gd / "romp-run-deadbeef").mkdir()          # a STALE generation: pruned on success
            env = dict(os.environ, PATH="%s%s%s" % (fakebin, os.pathsep, os.environ.get("PATH", "")),
                       ROMP_LAUNCH_TRIES="8")
            apply_r = apply.replace("R=/home/u/romp;", "R=%s;" % fix)
            try:
                a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True,
                              timeout=90)
                self.assertIn("SYNCED:", a.stdout,
                              "a gate-faithful kernel comes up ONLY if the wrapper released the "
                              "flock before probing — RESTARTFAIL here is the r44 deadlock")
                self.assertFalse((gd / "romp-restart-needed").exists(),
                                 "a verified healthy launch clears the durable restart intent")
                self.assertTrue((gd / "romp-run-11111111" / "bin" / "romp-serve").exists(),
                                 "the runtime generation is DURABLE — the manager's respawns "
                                 "exec it for this build's lifetime (the v1.3.18 audit's P1)")
                self.assertFalse((gd / "romp-run-deadbeef").exists(),
                                 "…and stale generations are pruned only after a healthy launch")
                # negative leg: a manager that starts nothing → no healthy verdict → no SYNCED.
                # The generation is idempotent per commit, so the stale one (whose manager DOES
                # start the server) must be dropped for the rebuilt one to carry the new stub.
                import shutil as _sh
                _sh.rmtree(gd / "romp-run-11111111", ignore_errors=True)
                (commitdir / "bin" / "romp-manager").write_text("#!/bin/sh\nexit 0\n")
                env["ROMP_LAUNCH_TRIES"] = "1"
                a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True,
                              timeout=90)
                self.assertIn("RESTARTFAIL", a.stdout)
                self.assertNotIn("SYNCED", a.stdout,
                                 "SYNCED without a healthy() verdict is the exact dishonesty "
                                 "the probe exists to prevent")
                self.assertEqual((gd / "romp-restart-needed").read_text().strip(),
                                 "1111111111111111111111111111111111111111",
                                 "a failed launch leaves DURABLE restart evidence (the v1.3.17 "
                                 "audit's P1.2: exec failure after the latch spend left silence)")
            finally:
                subprocess.run(["pkill", "-f", str(serve_py)], capture_output=True)

    def test_a_swapped_child_script_never_executes(self):
        # the v1.3.16 audit's P1.1 repro (pinned_parent=yes observed=CHILD_V2): install.sh's
        # bytes were pinned, but it executed its shell CHILDREN from the live tree — a racing
        # writer swapped one mid-install. The whole target tree materializes into the snapshot
        # now, and dirname-$0 resolution keeps every child inside it.
        calls = self._wire(apply_out="SYNCED:1111111")
        km._update_remote("TESTHOST")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        with tempfile.TemporaryDirectory() as td:
            fix = pathlib.Path(td) / "romp"
            gd = fix / ".git"
            gd.mkdir(parents=True)
            (gd / "romp-update-channel").write_text("dev\n")
            fakebin = pathlib.Path(td) / "bin"
            fakebin.mkdir()
            ops = pathlib.Path(td) / "ops.log"
            commitdir = pathlib.Path(td) / "committed-tree"
            (commitdir / "bin").mkdir(parents=True)
            (commitdir / "install.sh").write_text(
                '#!/bin/sh\nD="$(cd "$(dirname "$0")" && pwd)"\nsh "$D/bin/child.sh"\nexit 0\n')
            (commitdir / "bin" / "child.sh").write_text(
                "#!/bin/sh\necho CHILD_V1 >> '%s'\n" % ops)
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' merge-base '*) rm -f \"$(dirname \"$0\")/.head-moved\"; exit 0;;\n"
                "  *' archive '*) tar -c -C '%s' install.sh bin/child.sh;;\n"
                "  *' rev-parse --short=8 '*) echo deadbee2;;\n"
                "  *' merge '*) touch \"$(dirname \"$0\")/.head-moved\";;\n"
                "  *' rev-parse HEAD'*) if [ -e \"$(dirname \"$0\")/.head-moved\" ]; then echo 1111111111111111111111111111111111111111; else echo 0000000000000000000000000000000000000000; fi;;\n"
                "esac\nexit 0\n" % (gd, commitdir))
            (fakebin / "git").chmod(0o755)
            # the RACING WRITER's replacements sit in the live tree, both parent and child
            (fix / "bin").mkdir()
            (fix / "install.sh").write_text("#!/bin/sh\necho PARENT_V2 >> '%s'\n" % ops)
            (fix / "bin" / "child.sh").write_text("#!/bin/sh\necho CHILD_V2 >> '%s'\n" % ops)
            env = dict(os.environ, PATH="%s%s%s" % (fakebin, os.pathsep, os.environ.get("PATH", "")))
            apply_r = apply.replace("R=/home/u/romp;", "R=%s;" % fix)
            self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            log = ops.read_text() if ops.exists() else ""
            self.assertIn("CHILD_V1", log, "the COMMITTED child executed, from the snapshot")
            self.assertNotIn("CHILD_V2", log,
                             "the racing writer's child NEVER executes (the audit's repro)")
            self.assertNotIn("PARENT_V2", log)

    def test_both_install_entries_inherit_the_update_flock(self):
        # the r43 mutant hunt: dropping pass_fds from the rewritten bash -s calls stayed
        # suite-green — the inherited locked fd is what keeps the update flock alive when the
        # ssh wrapper dies mid-install (the 2026-08-17 double-install class)
        calls = self._wire(apply_out="SYNCED:1111111")
        km._update_remote("TESTHOST")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        self.assertEqual(apply.count("snap_install("), 3,
                         "one snapshot installer, called by BOTH legs (heal + main)")
        self.assertIn('cwd=r,env=env,pass_fds=(fd,)', apply,
                      "the installer subtree INHERITS the locked fd")
        self.assertIn('env["ROMP_INSTALL_TARGET"]=r', apply,
                      "the snapshot's scripts aim their outputs at the real checkout")
        self.assertIn('["git","-C",r,"archive",commit]', apply,
                      "the COMPLETE target tree is materialized — pinning install.sh alone "
                      "still executed its shell children from the live tree (v1.3.16 P1.1)")

    def test_an_empty_committed_install_refuses_and_never_falls_to_the_tree(self):
        # the r42 mutant hunt's M5: a git show that SUCCEEDS with empty output must refuse
        # (exit 4, latch armed) — a fallback to the tree's install.sh re-opens the exact
        # mutable-entry hole the commit closes
        import tempfile
        from pathlib import Path
        calls = self._wire(apply_out="SYNCED:1111111")
        km._update_remote("TESTHOST")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        with tempfile.TemporaryDirectory() as td:
            fix = Path(td) / "romp"
            gd = fix / ".git"
            gd.mkdir(parents=True)
            (gd / "romp-update-channel").write_text("dev\n")
            fakebin = Path(td) / "bin"
            fakebin.mkdir()
            ops = Path(td) / "ops.log"
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' merge-base '*) rm -f \"$(dirname \"$0\")/.head-moved\"; exit 0;;\n"
                "  *' show '*) exit 0;;\n"
                "  *' rev-parse --short=8 '*) echo deadbee2;;\n"
                "  *' merge '*) touch \"$(dirname \"$0\")/.head-moved\";;\n"
                "  *' rev-parse HEAD'*) if [ -e \"$(dirname \"$0\")/.head-moved\" ]; then echo 1111111111111111111111111111111111111111; else echo 0000000000000000000000000000000000000000; fi;;\n"
                "esac\nexit 0\n" % gd)
            (fakebin / "git").chmod(0o755)
            (fix / "install.sh").write_text("#!/bin/sh\necho TREE_RAN >> '%s'\nexit 0\n" % ops)
            (fix / "install.sh").chmod(0o755)
            env = dict(os.environ, PATH="%s%s%s" % (fakebin, os.pathsep, os.environ.get("PATH", "")))
            apply_r = apply.replace("R=/home/u/romp;", "R=%s;" % fix)
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("INSTALLFAIL", a.stdout)
            self.assertFalse(ops.exists(),
                             "the TREE's install.sh never executes when the committed entry "
                             "is empty (the r42 mutant hunt's M5)")
            self.assertEqual((gd / "romp-install-failed").read_text().strip(), "deadbee2",
                             "the latch stays armed")

    def test_a_stable_remote_marker_refuses_the_push_absolutely(self):
        # the v1.3.12 audit's P1, hardened by its own review: the rule is absolute in both peer
        # directions — a stable-channel remote never receives unsigned peer commits, whatever
        # the latch says (the common crash form has the marker already stable with no latch)
        import tempfile
        from pathlib import Path
        calls = self._wire(apply_out="SYNCED:1111111")
        km._update_remote("TESTHOST")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        with tempfile.TemporaryDirectory() as td:
            fix = Path(td) / "romp"
            gd = fix / ".git"
            gd.mkdir(parents=True)
            fakebin = Path(td) / "bin"
            fakebin.mkdir()
            ops = Path(td) / "ops.log"
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' rev-parse --short=8 '*) echo deadbee2;;\n"
                "  *' merge '*) touch \"$(dirname \"$0\")/.head-moved\";;\n"
                "  *' rev-parse HEAD'*) if [ -e \"$(dirname \"$0\")/.head-moved\" ]; then echo 1111111111111111111111111111111111111111; else echo 0000000000000000000000000000000000000000; fi;;\n"
                "  *' merge --ff-only '*) echo MOVED >> '%s';;\n"
                "  *' show '*) cat \"$2/install.sh\";;\n"
                "  *' archive '*) tar -c -C \"$2\" install.sh;;\n"
                "esac\nexit 0\n" % (gd, ops))
            (fakebin / "git").chmod(0o755)
            (fix / "install.sh").write_text("#!/bin/sh\necho INSTALLED >> '%s'\nexit 0\n" % ops)
            (fix / "install.sh").chmod(0o755)
            env = dict(os.environ, PATH="%s%s%s" % (fakebin, os.pathsep, os.environ.get("PATH", "")))
            apply_r = apply.replace("R=/home/u/romp;", "R=%s;" % fix)
            (gd / "romp-update-channel").write_text("stable\n")
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("STABLENOW", a.stdout)
            self.assertFalse((gd / "romp-install-failed").exists(),
                             "refused at entry: nothing armed, nothing moved")
            self.assertFalse(ops.exists(),
                             "PLACEMENT is the point: a gate moved after the merge+install kept "
                             "STABLENOW in stdout while unsigned code landed (the r28 "
                             "verification's mutant) — neither op may have run")
            # a MARKERLESS remote reads STABLE too (_update_channel's default) — the legacy
            # repo-config key is the only other dev opt-in, and this fixture sets neither
            (gd / "romp-update-channel").unlink()
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("STABLENOW", a.stdout)
            self.assertFalse(ops.exists(), "a markerless machine is a stable machine")
            # the legacy MAIN-checkout git-config dev opt-in still proceeds (pre-marker dev
            # machines must not wedge): the fake git answers the config probe with dev
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' config --get romp.updateChannel'*) echo dev;;\n"
                "  *' rev-parse --short=8 '*) echo deadbee2;;\n"
                "  *' merge '*) touch \"$(dirname \"$0\")/.head-moved\";;\n"
                "  *' rev-parse HEAD'*) if [ -e \"$(dirname \"$0\")/.head-moved\" ]; then echo 1111111111111111111111111111111111111111; else echo 0000000000000000000000000000000000000000; fi;;\n"
                "  *' merge --ff-only '*) echo MOVED >> '%s';;\n"
                "  *' show '*) cat \"$2/install.sh\";;\n"
                "  *' archive '*) tar -c -C \"$2\" install.sh;;\n"
                "esac\nexit 0\n" % (gd, ops))
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertNotIn("STABLENOW", a.stdout)
            self.assertTrue(ops.exists(), "a legacy config-dev machine still receives the push")

    def test_the_wrapper_verifies_the_move_landed_before_install(self):
        # the r32 verification's P1: the push wrapper was the ONE mover without the landed
        # verify — merge --ff-only no-ops at rc0 when a seam commit contains the target, install
        # ran at the seam under the target's latch, and the moot rule then ERASED the
        # failed-install protection
        import tempfile
        from pathlib import Path
        calls = self._wire(apply_out="SYNCED:1111111")
        km._update_remote("TESTHOST")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        with tempfile.TemporaryDirectory() as td:
            fix = Path(td) / "romp"
            gd = fix / ".git"
            gd.mkdir(parents=True)
            (gd / "romp-update-channel").write_text("dev\n")
            fakebin = Path(td) / "bin"
            fakebin.mkdir()
            ops = Path(td) / "ops.log"
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' rev-parse --short=8 '*) echo deadbee2;;\n"
                "  *' rev-parse HEAD'*) echo cccccccccccccccccccccccccccccccccccccccc;;\n"
                "  *' merge --ff-only '*) echo MOVED >> '%s';;\n"
                "  *' show '*) cat \"$2/install.sh\";;\n"
                "  *' archive '*) tar -c -C \"$2\" install.sh;;\n"
                "esac\nexit 0\n" % (gd, ops))          # HEAD sits on a SEAM commit post-merge
            (fakebin / "git").chmod(0o755)
            (fix / "install.sh").write_text("#!/bin/sh\necho INSTALLED >> '%s'\nexit 0\n" % ops)
            (fix / "install.sh").chmod(0o755)
            env = dict(os.environ, PATH="%s%s%s" % (fakebin, os.pathsep, os.environ.get("PATH", "")))
            apply_r = apply.replace("R=/home/u/romp;", "R=%s;" % fix)
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("NOTLANDED", a.stdout)
            self.assertFalse((gd / "romp-install-failed").exists(),
                             "no carry: the armed latch is disarmed — it must not name a sha "
                             "HEAD is not on")
            self.assertNotIn("INSTALLED", ops.read_text() if ops.exists() else "",
                             "nothing installs at the seam commit")
            # with a CARRIED prior (a HEAD-matching record whose heal fails), the not-landed
            # refusal restores it verbatim. DISTINCT target/HEAD shorts: with both echoing the
            # same sha, a mutant restoring the two-line BODY instead of the carry was
            # byte-identical and stayed green (the r33 mutant hunt)
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' rev-parse --short=8 HEAD'*) echo deadbee2;;\n"
                "  *' rev-parse --short=8 '*) echo 1c432642;;\n"
                "  *' rev-parse HEAD'*) echo cccccccccccccccccccccccccccccccccccccccc;;\n"
                "  *' show '*) cat \"$2/install.sh\";;\n"
                "  *' archive '*) tar -c -C \"$2\" install.sh;;\n"
                "esac\nexit 0\n" % gd)
            (gd / "romp-install-failed").write_text("deadbee2 dev")
            (fix / "install.sh").write_text("#!/bin/sh\nexit 1\n")   # the carried heal fails
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("NOTLANDED", a.stdout)
            self.assertEqual((gd / "romp-install-failed").read_text().strip(), "deadbee2 dev",
                             "the carried record survives the refusal verbatim — never the "
                             "two-line armed body")
            # a restore that FAILS reports its actual state (armed latch, boot heal settles it),
            # not RESETFAIL's "couldn't check out the new code" (the r33 verification)
            (gd / "romp-install-failed").write_text("deadbee2 dev")
            shim2 = Path(td) / "shim2"
            shim2.mkdir(exist_ok=True)
            (shim2 / "sitecustomize.py").write_text(
                "import os\n"
                "_real = os.write\n"
                "_state = {'n': 0}\n"
                "def _short(fd, data):\n"
                "    if fd > 2 and len(data) > 6:\n"
                "        _state['n'] += 1\n"
                "        if _state['n'] == 2:\n"          # the ARM is write 1 (the entry-file
                #                                             writes retired with the snapshot
                #                                             installer, r44), the RESTORE is 2
                "            return _real(fd, data[:5])\n"
                "    return _real(fd, data)\n"
                "os.write = _short\n")
            env2 = dict(env, PYTHONPATH=str(shim2))
            a = self._run(["bash", "-c", apply_r], env=env2, capture_output=True, text=True, timeout=60)
            self.assertIn("RESTOREFAIL", a.stdout)
            self.assertNotIn("RESETFAIL", a.stdout)
            (gd / "romp-install-failed").unlink()
            # an UNREADABLE head is unknown, not "not landed": the latch stays ARMED
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' rev-parse --short=8 '*) echo deadbee2;;\n"
                "  *' rev-parse HEAD'*) exit 1;;\n"
                "  *' show '*) cat \"$2/install.sh\";;\n"
                "  *' archive '*) tar -c -C \"$2\" install.sh;;\n"
                "esac\nexit 0\n" % gd)
            (fix / "install.sh").write_text("#!/bin/sh\nexit 0\n")
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("HEADUNKNOWN", a.stdout)
            self.assertEqual((gd / "romp-install-failed").read_text().strip(), "deadbee2",
                             "unknown beats erased: the armed latch stays for the boot heal")

    def test_a_settle_heal_that_publishes_stable_refuses_the_unsigned_reset(self):
        # the v1.3.12 audit's P1, remote edition: the heal published stable, removed the latch,
        # and the wrapper still moved HEAD and ran the peer installer — executed: it now stops
        # with STABLENOW, the marker keeps the published choice, and nothing moves
        import tempfile
        from pathlib import Path
        calls = self._wire(apply_out="SYNCED:1111111")
        km._update_remote("TESTHOST")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        with tempfile.TemporaryDirectory() as td:
            fix = Path(td) / "romp"
            gd = fix / ".git"
            gd.mkdir(parents=True)
            fakebin = Path(td) / "bin"
            fakebin.mkdir()
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' rev-parse --short=8 '*) echo deadbee2;;\n"
                "  *' merge '*) touch \"$(dirname \"$0\")/.head-moved\";;\n"
                "  *' rev-parse HEAD'*) if [ -e \"$(dirname \"$0\")/.head-moved\" ]; then echo 1111111111111111111111111111111111111111; else echo 0000000000000000000000000000000000000000; fi;;\n"
                "  *' merge --ff-only '*) echo MOVED >> '%s/ops.log';;\n"
                "  *' show '*) cat \"$2/install.sh\";;\n"
                "  *' archive '*) tar -c -C \"$2\" install.sh;;\n"
                "esac\nexit 0\n" % (gd, td))
            (fakebin / "git").chmod(0o755)
            (fix / "install.sh").write_text("#!/bin/sh\nexit 0\n")
            (fix / "install.sh").chmod(0o755)
            env = dict(os.environ, PATH="%s%s%s" % (fakebin, os.pathsep, os.environ.get("PATH", "")))
            apply_r = apply.replace("R=/home/u/romp;", "R=%s;" % fix)
            (gd / "romp-install-failed").write_text("deadbee2 stable")
            (gd / "romp-update-channel").write_text("dev\n")
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("STABLENOW", a.stdout)
            self.assertEqual((gd / "romp-update-channel").read_text().strip(), "stable",
                             "the completed switch keeps its published marker")
            self.assertFalse((Path(td) / "ops.log").exists(),
                             "nothing moved onto the freshly stable checkout")

    def test_the_wrapper_refuses_short_writes_executed(self):
        # the write-count checks had zero coverage — deleting them passed the full suite (the
        # adversarial review, 2026-08-21). A sitecustomize shim shortens the wrapper's first
        # large os.write: the ARM write must refuse (RESETFAIL, nothing armed) instead of
        # persisting a 5-byte truncated record.
        import tempfile
        from pathlib import Path
        calls = self._wire(apply_out="SYNCED:1111111")
        km._update_remote("TESTHOST")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        with tempfile.TemporaryDirectory() as td:
            fix = Path(td) / "romp"
            gd = fix / ".git"
            gd.mkdir(parents=True)
            fakebin = Path(td) / "bin"
            fakebin.mkdir()
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' rev-parse --short=8 '*) echo deadbee2;;\n"
                "  *' merge '*) touch \"$(dirname \"$0\")/.head-moved\";;\n"
                "  *' rev-parse HEAD'*) if [ -e \"$(dirname \"$0\")/.head-moved\" ]; then echo 1111111111111111111111111111111111111111; else echo 0000000000000000000000000000000000000000; fi;;\n"
                "  *' show '*) cat \"$2/install.sh\";;\n"
                "  *' archive '*) tar -c -C \"$2\" install.sh;;\n"
                "esac\nexit 0\n" % gd)
            (fakebin / "git").chmod(0o755)
            (fix / "install.sh").write_text("#!/bin/sh\nexit 0\n")
            (fix / "install.sh").chmod(0o755)
            (gd / "romp-update-channel").write_text("dev\n")   # a dev remote: mechanics under test
            shim = Path(td) / "shim"
            shim.mkdir()
            (shim / "sitecustomize.py").write_text(
                "import os\n"
                "_real = os.write\n"
                "_state = {'done': False}\n"
                "def _short(fd, data):\n"
                "    if fd > 2 and len(data) > 6 and not _state['done']:\n"
                "        _state['done'] = True\n"
                "        return _real(fd, data[:5])\n"
                "    return _real(fd, data)\n"
                "os.write = _short\n")
            env = dict(os.environ, PATH="%s%s%s" % (fakebin, os.pathsep, os.environ.get("PATH", "")),
                       PYTHONPATH=str(shim))
            apply_r = apply.replace("R=/home/u/romp;", "R=%s;" % fix)
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("RESETFAIL", a.stdout,
                          "a truncated arm write must refuse — persisting 5 bytes of a record "
                          "is the split-record class")
            self.assertFalse((gd / "romp-install-failed").exists(),
                             "nothing armed from a write that did not land whole")

    def test_the_wrapper_heal_publishes_a_staged_intent_and_nonhex_is_LATCHSTUCK(self):
        # the v1.3.8 audit's hard-death repro, p2p edition: the remote checkout carries a crashed
        # bootstrap's latch + intent — the wrapper's settle-heal must publish the intent before
        # spending the latch, and a torn NON-COMMIT line is never moot
        import tempfile
        from pathlib import Path
        calls = self._wire(apply_out="SYNCED:1111111")
        km._update_remote("TESTHOST")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        with tempfile.TemporaryDirectory() as td:
            fix = Path(td) / "romp"
            gd = fix / ".git"
            gd.mkdir(parents=True)
            fakebin = Path(td) / "bin"
            fakebin.mkdir()
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' rev-parse --short=8 '*) echo deadbee2;;\n"
                "  *' merge '*) touch \"$(dirname \"$0\")/.head-moved\";;\n"
                "  *' rev-parse HEAD'*) if [ -e \"$(dirname \"$0\")/.head-moved\" ]; then echo 1111111111111111111111111111111111111111; else echo 0000000000000000000000000000000000000000; fi;;\n"
                "  *' show '*) cat \"$2/install.sh\";;\n"
                "  *' archive '*) tar -c -C \"$2\" install.sh;;\n"
                "esac\nexit 0\n" % gd)
            (fakebin / "git").chmod(0o755)
            (fix / "install.sh").write_text("#!/bin/sh\nexit 0\n")
            (fix / "install.sh").chmod(0o755)
            env = dict(os.environ, PATH="%s%s%s" % (fakebin, os.pathsep, os.environ.get("PATH", "")))
            apply_r = apply.replace("R=/home/u/romp;", "R=%s;" % fix)
            (gd / "romp-install-failed").write_text("deadbee2 stable")
            (gd / "romp-update-channel").write_text("dev\n")
            os.mkfifo(str(gd / "romp-update-channel.pub"))   # a planted FIFO at the FIXED
            #                                                  staging name blocked the publish
            #                                                  open() (the r32 verification)
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=30)
            # the fixture has no bin/romp-serve, so a COMPLETED transaction reports NOLAUNCH;
            # the refusal verdicts are what must be absent
            self.assertNotIn("LATCHSTUCK", a.stdout)
            self.assertNotIn("INSTALLFAIL", a.stdout)
            self.assertEqual((gd / "romp-update-channel").read_text().strip(), "stable",
                             "the healed build wears the channel its update intended")
            self.assertFalse((gd / "romp-install-failed").exists())
            # a carried channel CHOICE survives a tokenless heal (the v1.3.10 audit's P1)
            (gd / "romp-install-failed").write_text("deadbee2\n0ddba11d stable")
            (gd / "romp-update-channel").write_text("dev\n")
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertNotIn("LATCHSTUCK", a.stdout)
            self.assertEqual((gd / "romp-update-channel").read_text().strip(), "stable",
                             "the carried explicit choice publishes when the healed line "
                             "stages nothing")
            # a failing heal with a PENDING STABLE refuses outright — resetting the remote onto
            # unsigned peer commits across that choice is the converge-gate rule (the
            # adversarial review, 2026-08-21); the record survives untouched
            (gd / "romp-install-failed").write_text("deadbee2\n0ddba11d stable")
            (gd / "romp-update-channel").write_text("dev\n")
            (fix / "install.sh").write_text("#!/bin/sh\nexit 1\n")
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("PENDINGSTABLE", a.stdout)
            self.assertEqual((gd / "romp-install-failed").read_text().strip(),
                             "deadbee2\n0ddba11d stable", "the pending record survives untouched")
            # a landed DEV choice over a superseded stable is NOT pending-stable: the gate keys
            # on the direction-guarded CARRY like the converge, never any-line — an any-line
            # predicate wedged the very push carrying the install fix (the adversarial review,
            # 2026-08-21, third pass)
            (gd / "romp-install-failed").write_text("deadbee2 dev\n0ddba11d stable")
            (gd / "romp-update-channel").write_text("dev\n")
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("INSTALLFAIL", a.stdout,
                          "a landed dev choice proceeds — no false pending-stable wedge")
            # and a reversed UNLANDED stable (line 1 never moved HEAD) does not gate either
            (gd / "romp-install-failed").write_text("aaaa1111 stable\ndeadbee2")
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("INSTALLFAIL", a.stdout)
            self.assertNotIn("stable", (gd / "romp-install-failed").read_text(),
                             "the unlanded token dies with its line")
            # a failing heal with a pending DEV merges the token onto the surviving carry (the
            # lossy carry destroyed the choice — same review)
            (gd / "romp-install-failed").write_text("deadbee2\n0ddba11d dev")
            (gd / "romp-update-channel").write_text("dev\n")
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("INSTALLFAIL", a.stdout)
            self.assertEqual((gd / "romp-install-failed").read_text().strip(), "deadbee2 dev",
                             "the pending choice rides the surviving line")
            # reversed direction: HEAD matches line 2, line 1 is an unlanded "sha dev" — the
            # failing heal must not launder the token (the adversarial review, 2026-08-21);
            # the marker stays dev — a stable marker refuses at entry now (v1.3.12 P1)
            (gd / "romp-install-failed").write_text("aaaa1111 dev\ndeadbee2")
            (gd / "romp-update-channel").write_text("dev\n")
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("INSTALLFAIL", a.stdout)
            self.assertNotIn("dev", (gd / "romp-install-failed").read_text(),
                             "the unlanded token dies with its line")
            (fix / "install.sh").write_text("#!/bin/sh\nexit 0\n")
            # healing the CARRIED line never inherits the unlanded line-1 token (the v1.3.11
            # audit's P1) — the remote is a dev machine; a wrongful inherit would flip it stable
            (gd / "romp-install-failed").write_text("aaaa1111 stable\ndeadbee2")
            (gd / "romp-update-channel").write_text("dev\n")
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertNotIn("LATCHSTUCK", a.stdout)
            self.assertEqual((gd / "romp-update-channel").read_text().strip(), "dev",
                             "an unlanded intent's channel is never published")
            # a PLAIN sha line publishes nothing — in-channel heals change no marker
            (gd / "romp-install-failed").write_text("deadbee2")
            (gd / "romp-update-channel").write_text("dev\n")
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertNotIn("LATCHSTUCK", a.stdout)
            self.assertEqual((gd / "romp-update-channel").read_text().strip(), "dev",
                             "a plain line stages no channel")
            # an UNPUBLISHABLE channel keeps the latch armed (exit 4): the marker write is
            # UNBUFFERED — a buffered close-failure was an unraisable CPython swallowed,
            # installing a 0-byte marker over 'dev' with the latch spent (the adversarial
            # review, 2026-08-20, reproduced with an LD_PRELOAD write shim)
            (gd / "romp-install-failed").write_text("deadbee2 stable")
            (gd / "romp-update-channel").write_text("dev\n")   # readable — the entry gate passes
            (gd / "romp-update-channel.pub").mkdir()   # os.open O_WRONLY on a dir raises: the
            #                                            staged publish cannot land
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("PENDINGSTABLE", a.stdout,
                          "an unpublishable STABLE choice refuses the unsigned reset outright "
                          "(the 2026-08-21 gate) — the latch stays")
            self.assertEqual((gd / "romp-install-failed").read_text().strip(), "deadbee2 stable",
                             "the record and its channel survive for the retry")
            (gd / "romp-update-channel.pub").rmdir()
            # an EXISTING marker the wrapper cannot READ is UNKNOWN — refuse at entry, latch
            # untouched (the r28 verification: an unreadable 'stable' marker fell open while an
            # unreadable latch three lines later failed closed)
            (gd / "romp-update-channel").chmod(0o000)
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("CHANNELUNKNOWN", a.stdout)
            self.assertEqual((gd / "romp-install-failed").read_text().strip(), "deadbee2 stable",
                             "nothing spent, nothing moved on an unknown channel")
            (gd / "romp-update-channel").chmod(0o644)
            # an UNDECODABLE marker is unknown too — it killed the wrapper uncaught and
            # surfaced as RESETFAIL, pointing the operator at the wrong subsystem (the r29
            # verification)
            (gd / "romp-update-channel").write_bytes(b"\xff\xfe garbage")
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("CHANNELUNKNOWN", a.stdout)
            self.assertNotIn("RESETFAIL", a.stdout)
            self.assertEqual((gd / "romp-install-failed").read_text().strip(), "deadbee2 stable")
            (gd / "romp-update-channel").write_text("dev\n")
            # an UNDECODABLE latch is unknown too — it killed the wrapper uncaught (RESETFAIL,
            # wrong subsystem) instead of the designed refusal (the r30 verification)
            (gd / "romp-install-failed").write_bytes(b"\xff\xfe garbage \x80")
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("LATCHSTUCK", a.stdout)
            self.assertNotIn("RESETFAIL", a.stdout)
            # a FIFO latch refuses WITHOUT blocking (open() on a writerless FIFO hangs forever,
            # holding the remote's update lock — the r30 verification)
            (gd / "romp-install-failed").unlink()
            os.mkfifo(str(gd / "romp-install-failed"))
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=20)
            self.assertIn("LATCHSTUCK", a.stdout)
            (gd / "romp-install-failed").unlink()
            # a FIFO MARKER refuses without blocking too, before the latch is even considered
            os.mkfifo(str(gd / "romp-update-channel.fifo"))
            os.replace(str(gd / "romp-update-channel.fifo"), str(gd / "romp-update-channel"))
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=20)
            self.assertIn("CHANNELUNKNOWN", a.stdout)
            (gd / "romp-update-channel").unlink()
            (gd / "romp-update-channel").write_text("dev\n")
            # torn, EMPTY, and malformed records are never moot — LATCHSTUCK, executed (the
            # v1.3.9 audit: strict grammar in every reader)
            for bad in ("quarantin", "", "deadbee2 sta", "abcd1234\nffff9999\neeee1111"):
                (gd / "romp-install-failed").write_text(bad)
                a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
                self.assertIn("LATCHSTUCK", a.stdout, "%r must refuse" % bad)
                self.assertEqual((gd / "romp-install-failed").read_text(), bad,
                                 "the unknown record survives, unerased")
            (gd / "romp-install-failed").unlink()

    def test_the_wrapper_itself_recheck_dirt_heals_priors_and_carries_on_failure(self):
        # the wrapper's OWN under-lock legs, executed (the adversarial review, 2026-08-19: the
        # outer shell probes fired first in every prior test, leaving the wrapper's dead code)
        import tempfile
        from pathlib import Path
        calls = self._wire(apply_out="SYNCED:1111111")
        km._update_remote("TESTHOST")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        with tempfile.TemporaryDirectory() as td:
            fix = Path(td) / "romp"
            gd = fix / ".git"
            gd.mkdir(parents=True)
            fakebin = Path(td) / "bin"
            fakebin.mkdir()
            (gd / "romp-update-channel").write_text("dev\n")   # a dev remote: mechanics under test
            marker = Path(td) / "status-called"
            # exit-8: the OUTER status probe (call 1) reports clean; the wrapper's under-lock
            # status (call 2) sees the edit that landed in the gap
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' merge-base '*) rm -f \"$(dirname \"$0\")/.head-moved\"; exit 0;;\n"
                "  *' status '*) if [ -e '%s' ]; then echo ' M raced-edit.py'; else touch '%s'; fi;;\n"
                "  *' rev-parse --short=8 '*) echo deadbee2;;\n"
                "  *' show '*) cat \"$2/install.sh\";;\n"
                "  *' archive '*) tar -c -C \"$2\" install.sh;;\n"
                "esac\nexit 0\n" % (gd, marker, marker))
            (fakebin / "git").chmod(0o755)
            (fix / "install.sh").write_text("#!/bin/sh\nexit 0\n")
            (fix / "install.sh").chmod(0o755)
            env = dict(os.environ, PATH="%s%s%s" % (fakebin, os.pathsep, os.environ.get("PATH", "")))
            apply_r = apply.replace("R=/home/u/romp;", "R=%s;" % fix)
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("DIRTYNOW", a.stdout, "the wrapper's own locked status check refused")
            self.assertFalse((gd / "romp-install-failed").exists())
            # heal-first executed: a prior latch naming HEAD, install fails → INSTALLFAIL with
            # the record CARRIED (two lines); install fixed → the whole latch is spent
            marker.unlink(missing_ok=True)
            (fakebin / "git").write_text(
                "#!/bin/sh\ncase \" $* \" in\n"
                "  *' rev-parse --absolute-git-dir'*) echo '%s';;\n"
                "  *' merge-base '*) rm -f \"$(dirname \"$0\")/.head-moved\"; exit 0;;\n"
                "  *' rev-parse --short=8 HEAD'*) echo 01dbd11d;;\n"
                "  *' rev-parse --short=8 '*) echo deadbee2;;\n"
                "  *' merge '*) touch \"$(dirname \"$0\")/.head-moved\";;\n"
                "  *' rev-parse HEAD'*) if [ -e \"$(dirname \"$0\")/.head-moved\" ]; then echo 1111111111111111111111111111111111111111; else echo 0000000000000000000000000000000000000000; fi;;\n"
                "  *' show '*) cat \"$2/install.sh\";;\n"
                "  *' archive '*) tar -c -C \"$2\" install.sh;;\n"
                "esac\nexit 0\n" % gd)
            (gd / "romp-install-failed").write_text("01dbd11d")
            (fix / "install.sh").write_text("#!/bin/sh\nexit 1\n")
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("INSTALLFAIL", a.stdout)
            self.assertEqual([l.strip() for l in (gd / "romp-install-failed").read_text().splitlines()],
                             ["deadbee2", "01dbd11d"],
                             "the arm names the new intent AND CARRIES the prior — never overwritten")
            (fix / "install.sh").write_text("#!/bin/sh\nexit 0\n")
            a = self._run(["bash", "-c", apply_r], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("NOLAUNCH", a.stdout,
                          "the fixture has no launcher — reaching the launch check proves the "
                          "heal+reset+install transaction completed")
            self.assertFalse((gd / "romp-install-failed").exists(),
                             "healed + reset + installed: everything spent")

    def test_the_apply_recheck_catches_an_edit_landing_after_the_probe(self):
        # the discover-step dirty probe is an ssh round-trip old by apply time; an edit landing in
        # that window must be re-caught IN THE SAME SHELL as the reset, or reset --hard destroys it
        # on the strength of a stale answer (the user's audit, 2026-08-17)
        calls = self._wire(apply_out="DIRTYNOW")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("uncommitted work between the check and the apply", detail)
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        self.assertNotIn("reset --hard", apply,
                         "the move is ff-only everywhere (the v1.3.12 audit's P1)")
        pre, sep, post = apply.partition('"merge","--ff-only",target')
        self.assertTrue(sep, "the ff-only move must exist in the wrapper")
        self.assertIn("status --porcelain", pre,
                      "the recheck sits between the ancestry gate and the move, same shell")
        self.assertIn("DIRTYNOW", pre)
        self.assertNotIn("status --porcelain", post, "and never after the move, where it is moot")

    def test_no_romp_clone_fails_loudly(self):
        self._wire(disc_out="NOROMP")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("not installed", detail)

    def test_a_failed_push_surfaces_the_git_error(self):
        self._wire(push_rc=1, push_err="Permission denied (publickey)")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("git push", detail)
        self.assertIn("Permission denied", detail)

    def test_no_github_origin_in_the_remote_commands(self):
        # peer-to-peer: NOTHING should pull from origin / touch GitHub
        calls = self._wire()
        km._update_remote("TESTHOST")
        for a in calls:
            cmd = a[-1] if isinstance(a[-1], str) else ""
            self.assertNotIn("git pull", cmd, "no pull-from-origin anywhere")
            self.assertNotIn("origin", cmd)

    def test_restart_goes_through_the_manager_then_falls_back(self):
        # the user 2026-07-04: the restart should keep the remote MANAGER-owned (romp's durable supervisor, no
        # orphan) — kill the kernel, `romp-manager ensure` (respawns via a live manager, or STARTS one that spawns
        # a supervised kernel — upgrading an attach-bootstrapped bare host), then port-poll; bare romp-serve is a
        # LAST-RESORT fallback only when the port never returns. It must NOT rely on `romp --refresh` (the stuck bug).
        km._remotes = {"TESTHOST": {"host": "TESTHOST", "kernel_port": 29855}}
        calls = self._wire()
        km._update_remote("TESTHOST")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        self.assertIn('["pkill","-f","bin/romp-kern[e]l"]', apply,
                      "kills the running kernel (self-match-guarded, the user 2026-07-22)")
        self.assertIn('["node",mg,"ensure"]', apply,
                      "prefers the manager (ensure = idempotent supervised start) — exec'd from "
                      "the DURABLE runtime generation (the v1.3.18 audit's P1)")
        self.assertIn('["git","-C",r,"archive",target]', apply,
                      "the generation is the COMPLETE verified tree — manager, serve, kernel and "
                      "modules, never live files")
        self.assertIn("ROMP_DIR=r,ROMP_SERVE_ROOT=r", apply,
                      "the launch env roots serve and manager on the REAL checkout; the "
                      "generation itself is resolved PER SPAWN by serve/manager (the r46 "
                      "verification: an env pin froze every respawn on the pinning build)")
        self.assertNotIn("ROMP_SERVE_BIN=", apply,
                         "no per-manager-lifetime pin — later LOCAL updates must not respawn "
                         "old bytes under a fresh sha")
        self.assertNotIn("ROMP_KERNEL_BIN=", apply)
        self.assertNotIn("romp-manage[r]", apply,
                         "the manager SURVIVES: killing it rippled across sibling checkouts, "
                         "raced its own drain, and the supervised respawn undid it anyway")
        self.assertIn('"romp-restart-needed"', apply)
        self.assertLess(apply.index('"romp-restart-needed"'), apply.index("os.remove(lp)\ngen="),
                        "durable restart intent is armed BEFORE the latch is spent (the earlier "
                        "os.remove(lp) occurrences are the heal branch's)")
        self.assertIn("if mgr_ok:", apply,
                      "a manager that failed to launch skips straight to the fallback")
        self.assertIn("/version", apply, "polls the running kernel's build, not just its TCP port")
        self.assertIn("if not target.startswith(ks):", apply,
                      "the restarted kernel must report the exact installed commit")
        self.assertIn("old_ks,old_boot=probe()", apply,
                      "a surviving old kernel process cannot satisfy the restart")
        self.assertIn("bid!=old_boot", apply)
        self.assertLess(apply.index("os.remove(lp)\ngen="),
                        apply.index('["pkill","-f"'),
                        "the launch runs INSIDE the locked python — the transaction no longer "
                        "ends before the spawn (the v1.3.16 audit's P1.2)")
        self.assertIn("if not ks or len(ks)<7 or not bid:", apply,
                      "a /version with no boot id must fail healthy() (r43; python-side r44)")
        self.assertEqual(apply.count("for i in range(tries):"), 2,
                         "both restart waits fit a real (~17s) manager boot — 8s read a landed "
                         "install as RESTARTFAIL (r43); default 12, env-tunable for tests")
        self.assertIn('int(os.environ.get("ROMP_LAUNCH_TRIES") or 12)', apply)
        self.assertIn("fcntl.flock(fd,fcntl.LOCK_UN)", apply)
        self.assertLess(apply.index('["node",mg,"ensure"]'), apply.index("fcntl.flock(fd,fcntl.LOCK_UN)"),
                        "the kill + spawn request run INSIDE the lock…")
        self.assertLess(apply.index("fcntl.flock(fd,fcntl.LOCK_UN)"), apply.index("for i in range(tries):"),
                        "…and the probe runs UNLOCKED: the new kernel's boot gate polls this "
                        "same flock, so a locked probe deadlocked into RESTARTFAIL (the r44 "
                        "verification's P1)")
        self.assertIn("subprocess.Popen([sv]", apply,
                      "bare romp-serve only as a last resort — the SNAPSHOT copy, spawned "
                      "detached, no lock fd")
        self.assertIn("start_new_session=True", apply)
        self.assertNotIn("--refresh", apply, "does NOT rely on `romp --refresh` (needs a manager) — the stuck bug")

    def test_apply_is_detached_from_the_ssh_session(self):
        # the user 2026-07-11 (TESTHOST): the apply kills the running kernel before booting its
        # replacement, so an ssh drop between the two halves left the host kernel-LESS — and every
        # banner Retry re-killed whatever a previous attempt had booted. The apply now runs in its
        # own session (setsid, plain-bash fallback where setsid is missing), so once started the
        # kill+boot pair always completes on the remote even if the connection dies.
        km._remotes = {"TESTHOST": {"host": "TESTHOST", "kernel_port": 29855}}
        calls = self._wire()
        km._update_remote("TESTHOST")
        wrapper = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        self.assertTrue(wrapper.startswith("APPLY="), "the apply script rides a variable, quoted once")
        self.assertIn('exec setsid bash -c "$APPLY"', wrapper)
        self.assertIn('else exec bash -c "$APPLY"', wrapper, "hosts without setsid still work")

    def test_apply_timeout_says_the_restart_keeps_running(self):
        # the local 60s confirmation window can expire while a slow host is still mid-restart; the
        # detached apply keeps going, so the message must say that instead of implying a dead host
        def fake(argv, **kw):
            if argv[0] == "git" and "rev-parse" in argv:
                return _R(out=self.LFULL)
            if argv[0] == "git" and "push" in argv:
                return _R()
            cmd = argv[-1]
            if "for d in" in cmd:
                return _R(out="DIR:/home/u/romp\nHEAD:%s\nDIRTY:" % self.RHEAD)
            raise km.subprocess.TimeoutExpired(argv, 60)
        km.subprocess.run = fake
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("keeps", detail)
        self.assertIn("running", detail)


class UpdateEndpoint(unittest.TestCase):
    def test_post_tunnels_update_calls_update_remote_and_reports(self):
        import inspect
        src = inspect.getsource(km)
        self.assertIn('if u.path == "/tunnels/update":', src)
        self.assertIn("ok, detail = _update_remote(host)", src)
        self.assertIn('json.dumps({"ok": ok, "detail": detail})', src)
        # a failed update returns a non-2xx so the CLI/banner can tell (fail loudly)
        self.assertIn("200 if ok else 502", src)

    def test_supervisor_polls_the_remote_version(self):
        import inspect
        src = inspect.getsource(km._tunnel_supervisor)
        self.assertIn("_poll_remote_version(r)", src)
        self.assertIn('r["kernel_sha"] = rsha', src)


class UpdateUI(unittest.TestCase):
    def test_drift_banner_uses_the_update_framing(self):
        # mirrors the #rstale reload banner, but asks to bring the remote onto the local build (the user
        # 2026-07-04). ONE neutral word since 2026-07-28: the same button covers a push we run and an ask
        # a checked-in peer runs for itself, and what the user agrees to — that machine ends up on this
        # build — is identical either way.
        self.assertIn("id=rdrift", km._RDRIFT_HTML)
        self.assertIn(">Update<", km._RDRIFT_HTML, "the action button says Update")
        self.assertIn("Update it to this one?", km._RDRIFT_JS, "the prompt asks to bring the remote onto this build")
        self.assertIn("/tunnels/update", km._RDRIFT_JS)
        self.assertIn("outOfDate", km._RDRIFT_JS)
        self.assertIn("_rdrift_block()", inspect_src())

    def test_drift_banner_shows_live_progress_success_and_failure(self):
        # the user 2026-07-04: the banner must stay up through the work with a spinner + status, a success
        # confirmation, and a persistent actionable error — not silently flip back to the prompt.
        self.assertIn("rd-spin", km._RDRIFT_HTML)
        self.assertIn("romp-swirl-glyph.svg", km._RDRIFT_CSS)   # the spinner is the romp loader glyph
        self.assertIn("Updating ", km._RDRIFT_JS, "an 'updating…' progress message")
        self.assertIn("waiting for", km._RDRIFT_JS, "a 'waiting for it to restart' verify phase")
        self.assertIn("Up to date", km._RDRIFT_JS, "a success confirmation")
        self.assertIn("Update failed", km._RDRIFT_JS, "a persistent, specific failure message")
        self.assertIn("phase", km._RDRIFT_JS, "a state machine drives the flow")

    def test_popover_shows_behind_and_a_push_button(self):
        self.assertIn("behind", km._LANDING_REMOTES_JS)
        self.assertIn(">Push</button>", km._LANDING_REMOTES_JS)
        self.assertIn("/tunnels/update", km._LANDING_REMOTES_JS)
        self.assertIn("data-u=", km._LANDING_REMOTES_JS)


ru = SourceFileLoader("romp_update", os.path.join(BIN, "romp-update")).load_module()


class RompUpdateCLI(unittest.TestCase):
    def setUp(self):
        self._k, self._g, self._p = ru._kernel, ru._get, ru._post
        ru._kernel = lambda: "http://127.0.0.1:29855"
        self.posted = []
        ru._post = lambda u, path, body: (self.posted.append((path, body)) or {"ok": True, "detail": "updated"})

    def tearDown(self):
        ru._kernel, ru._get, ru._post = self._k, self._g, self._p

    def test_dispatch_routes_update_in_the_bash_cli(self):
        # Dash-only since 2026-07-25: bare `update` names a session (the retired-word
        src = open(os.path.join(BIN, "romp")).read()
        # Round 3 (2026-07-25): commands are bare words again — `update` is the
        # spelling, and the retired `--update` flag fails naming it.
        self.assertIn('"${1:-}" == "update"', src, "bare `update` routes to romp-update")
        self.assertIn('--update)', src, "the retired --update spelling gets a loud hint")
        self.assertIn("exec romp-update", src)

    def test_no_kernel_errors_cleanly(self):
        ru._kernel = lambda: None
        self.assertEqual(ru.main([]), 2)

    def test_named_host_updates_that_remote(self):
        self.assertEqual(ru.main(["TESTHOST"]), 0)
        self.assertEqual(self.posted, [("/tunnels/update", {"host": "TESTHOST"})])

    def test_no_arg_updates_only_out_of_date_remotes(self):
        ru._get = lambda u, path: {"tunnels": [{"host": "TESTHOST", "outOfDate": True},
                                               {"host": "gpu1", "outOfDate": False}]}
        self.assertEqual(ru.main([]), 0)
        self.assertEqual(self.posted, [("/tunnels/update", {"host": "TESTHOST"})], "only the stale remote is updated")

    def test_no_arg_all_current_updates_nothing(self):
        ru._get = lambda u, path: {"tunnels": [{"host": "gpu1", "outOfDate": False}]}
        self.assertEqual(ru.main([]), 0)
        self.assertEqual(self.posted, [], "nothing to do when every remote is current")

    def test_a_failed_update_returns_nonzero(self):
        ru._post = lambda u, path, body: {"ok": False, "detail": "git pull failed"}
        self.assertEqual(ru.main(["TESTHOST"]), 1)


def inspect_src():
    import inspect
    return inspect.getsource(km)


class BehindInfo(unittest.TestCase):
    """The popover's drift wording data (the user 2026-07-11: 'something more informative than just
    behind'): _behind_info measures HOW an out-of-date remote differs — commits behind, commits ahead
    (a push would clobber those, so 'behind' would be a lie), and the remote commit's date."""
    LOCAL_FULL = "abc1234000000000"
    REMOTE_FULL = "def5678000000000"

    def setUp(self):
        self._hc = dict(km._HEAD_CACHE)
        km._HEAD_CACHE.update(ts=9e18, full=self.LOCAL_FULL, short="abc1234")
        km._BEHIND_CACHE.clear()
        self._run = km.subprocess.run

    def tearDown(self):
        km._HEAD_CACHE.clear(); km._HEAD_CACHE.update(self._hc)
        km._BEHIND_CACHE.clear()
        km.subprocess.run = self._run

    def _mock_git(self, behind="12", ahead="0", date="2026-07-08", known=True, calls=None):
        loc, rem = self.LOCAL_FULL, self.REMOTE_FULL

        def run(argv, **kw):
            if calls is not None:
                calls.append(list(argv))
            j = " ".join(argv)
            if "rev-parse" in j and "^{commit}" in j:
                return _R(out=rem + "\n") if known else _R(rc=1)
            if "rev-list" in j and (rem + ".." + loc) in j:
                return _R(out=behind + "\n")
            if "rev-list" in j and (loc + ".." + rem) in j:
                return _R(out=ahead + "\n")
            if "log" in j:
                return _R(out=date + "\n")
            return _R(rc=1)
        km.subprocess.run = run

    def test_behind_counts_and_the_remote_commits_date(self):
        self._mock_git(behind="12", ahead="0", date="2026-07-08")
        self.assertEqual(km._behind_info("def5678"),
                         {"behind": 12, "ahead": 0, "date": "2026-07-08"})

    def test_ahead_is_distinguished_from_behind(self):
        # the remote has its own commits (updated from another machine, or local was rolled back):
        # a push would CLOBBER them, so the row must not claim 'behind'
        self._mock_git(behind="0", ahead="3")
        info = km._behind_info("def5678")
        self.assertEqual((info["behind"], info["ahead"]), (0, 3))

    def test_unknown_sha_reports_none_not_a_guess(self):
        self._mock_git(known=False)
        self.assertEqual(km._behind_info("def5678"), {"behind": None, "ahead": None, "date": ""})

    def test_memoized_per_sha_pair(self):
        calls = []
        self._mock_git(calls=calls)
        km._behind_info("def5678")
        n = len(calls)
        self.assertGreater(n, 0)
        km._behind_info("def5678")
        self.assertEqual(len(calls), n, "the second read is served from the memo — git never re-runs")

    def test_remote_public_carries_the_drift_fields(self):
        self._mock_git(behind="12", ahead="0", date="2026-07-08")
        pub = km._remote_public({"host": "TESTHOST", "kernel_port": 29855, "local_port": 8801,
                                 "status": "up", "kernel_sha": "def5678"})
        self.assertTrue(pub["outOfDate"])
        self.assertEqual((pub["behindBy"], pub["aheadBy"], pub["kernelDate"]), (12, 0, "2026-07-08"))

    def test_in_sync_remote_never_touches_git(self):
        def boom(argv, **kw):
            raise AssertionError("an in-sync row must not pay for drift measurement: %s" % argv)
        km.subprocess.run = boom
        pub = km._remote_public({"host": "TESTHOST", "kernel_port": 29855, "local_port": 8801,
                                 "status": "up", "kernel_sha": "abc1234"})
        self.assertFalse(pub["outOfDate"])
        self.assertEqual((pub["behindBy"], pub["aheadBy"], pub["kernelDate"]), (0, 0, ""))


class DriftWordingUI(unittest.TestCase):
    """The popover row names HOW the remote differs, not just 'behind' (the user 2026-07-11)."""

    def test_row_names_how_the_remote_differs(self):
        js = km._LANDING_REMOTES_JS
        self.assertIn("down=bb>0?('behind '+bb):''", js)   # said in words since 2026-07-30
        self.assertIn("up=ab>0?('ahead '+ab):''", js)
        self.assertIn("'diverged: '", js)
        self.assertIn("'different build'", js, "an unknown sha says so instead of guessing")

    def test_a_buildless_connected_host_reads_unversioned_not_blank(self):
        # A connected host that reports NO build at all (a plain file copy, no git checkout) used to
        # show a bare "connected" — indistinguishable from healthy-and-in-sync, while running
        # arbitrarily old code drift detection cannot see (the user 2026-08-11, whose devbox sat
        # months behind beside a blank). The row says "unversioned copy" where the build word sits,
        # and the tooltip says why and what restores updates. Fail loudly, never a blank that reads
        # as fine.
        js = km._LANDING_REMOTES_JS
        self.assertIn("else if(t.status==='up'){ver=' \\u00b7 <span class=\"rnet-old\"", js)
        self.assertIn("unversioned copy", js)
        self.assertIn("Reinstall it as a git clone to restore the build name and updates.", js)
        # the VS Code strip's popover row carries the same word — the two surfaces must not drift
        strip = (pathlib.Path(__file__).resolve().parents[1] / "ui" / "webview" / "strip.ts").read_text()
        self.assertIn('ver = " · unversioned copy";', strip)
        self.assertIn("!t.kernelSha && !t.kernelVer", strip)

    def test_tooltip_carries_the_shas_and_date(self):
        js = km._LANDING_REMOTES_JS
        # since 2026-07-30 each side is named by RELEASE and commit together (buildWord), not the sha
        # alone — the tag is the one number both machines already agree on
        self.assertIn("running '+(buildWord(t.kernelVer,t.kernelSha)||'?')", js)
        self.assertIn("this machine is at '+(buildWord(t.localVer,t.localSha)||'?')", js)
        self.assertIn("t.kernelDate", js)

    def test_popover_js_parses(self):
        # the inline JS ships unparsed inside the kernel's HTML — a stray brace only surfaces
        # when the popover breaks in the browser; parse it the way the browser will
        node = shutil.which("node")
        if not node:
            self.skipTest("node unavailable")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(km._LANDING_REMOTES_JS)
            path = f.name
        try:
            r = subprocess.run([node, "--check", path], capture_output=True, text=True, timeout=15)
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()


from unittest import mock


class RestartVerdictHonesty(unittest.TestCase):
    """the v1.3.18 audit: RESTARTED:0 — the port never answered even after the fallback — was
    reported as a successful restart on the tag alone."""

    def _run(self, out):
        with mock.patch.object(km, "_discover_remote_clone",
                               return_value=("/x/repo", "a" * 40, "", "0", None)), \
             mock.patch.object(km.subprocess, "run") as r:
            km._remotes = {"TESTHOST": {"host": "TESTHOST", "kernel_port": 1}}
            r.return_value = mock.Mock(stdout=out, stderr="", returncode=0)
            ok, detail = km._restart_remote_kernel("TESTHOST")
            cmd = r.call_args[0][0][-1]
            km._remotes = {}
        return ok, detail, cmd

    def test_restarted_zero_is_not_success(self):
        ok, detail, _ = self._run("RESTARTED:0\n")
        self.assertFalse(ok)
        self.assertIn("never answered", detail)

    def test_restarted_one_is_success(self):
        ok, detail, _ = self._run("RESTARTED:1\n")
        self.assertTrue(ok, detail)

    def test_the_rr_leg_uses_a_durable_generation_and_never_rms_it_same_run(self):
        _, _, cmd = self._run("RESTARTED:1\n")
        self.assertIn("romp-run-$H8", cmd, "the per-commit durable generation is BUILT here")
        self.assertNotIn("RRSNAP", cmd)
        self.assertNotIn('rm -rf "$GEN"', cmd,
                         "the generation the detached processes exec is never deleted same-run "
                         "(the r45 rr race)")
        self.assertNotIn("romp-run-*", cmd,
                         "no pruning here at all — that is the p2p wrapper's job, under its flock")

    def test_the_rr_script_never_matches_its_own_pkill_patterns(self):
        # the r46 verification's P1: the first cut's env-pin literals
        # (ROMP_KERNEL_BIN="$GEN/bin/romp-kernel", MGR="$R/bin/romp-manager") matched the
        # script's own pkill bracket patterns — the apply shell SIGTERM'd itself and the host
        # was left with no manager and no kernels (the 2026-07-22 self-kill bug reintroduced)
        import re as _re
        _, _, cmd = self._run("RESTARTED:1\n")
        for pat in _re.findall(r'pkill -f "([^"]+)"', cmd):
            self.assertIsNone(_re.search(pat, cmd),
                              "pkill pattern %r matches the apply script's own text" % pat)


class KernelShaAttribution(unittest.TestCase):
    """the r46 verification: a generation kernel reported the CHECKOUT's HEAD as its running
    build — green verifiers over stale bytes after any respawn past a checkout move. The
    RUNNING build's identity is the generation's own name."""

    def setUp(self):
        self._saved = (km.HERE, km._SHA)
        km._SHA = None

    def tearDown(self):
        km.HERE, km._SHA = self._saved
        os.environ.pop("ROMP_CHECKOUT", None)

    def test_a_generation_kernel_reports_its_generation(self):
        km.HERE = pathlib.Path("/x/.git/romp-run-aabbccdd/kernel")
        os.environ["ROMP_CHECKOUT"] = "/x"
        self.assertEqual(km._kernel_sha(), "aabbccdd",
                         "the RUNNING bytes' commit, never the checkout's moving HEAD")

    def test_a_manual_checkout_use_outside_a_generation_falls_back(self):
        km.HERE = pathlib.Path("/x/somewhere/kernel")
        os.environ["ROMP_CHECKOUT"] = "/x"
        with mock.patch.object(km.subprocess, "run",
                               return_value=mock.Mock(returncode=0, stdout="1234abcd\n")):
            self.assertEqual(km._kernel_sha(), "1234abcd")


class KernelRunsFromAGeneration(unittest.TestCase):
    """the v1.3.18 audit's P1: the kernel's CODE loads from the pinned generation while every
    ROOT-relative operation stays on the checkout ROMP_CHECKOUT names."""

    def test_romp_checkout_overrides_root(self):
        import subprocess as sp
        ksrc = os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")
        with tempfile.TemporaryDirectory() as td:
            out = sp.run(
                ["python3", "-c",
                 "import os, pathlib, re\n"
                 "src = open(os.environ['KSRC']).read()\n"
                 "m = re.search(r'ROOT = .*', src)\n"
                 "HERE = pathlib.Path(os.environ['KSRC']).resolve().parent\n"
                 "Path = pathlib.Path\n"
                 "ROOT = eval(m.group(0).split('= ', 1)[1])\n"
                 "print(ROOT)"],
                env=dict(os.environ, KSRC=ksrc, ROMP_CHECKOUT=td),
                capture_output=True, text=True, timeout=20)
            self.assertEqual(out.stdout.strip(), str(pathlib.Path(td).resolve()),
                             out.stderr[:300])
            out2 = sp.run(
                ["python3", "-c",
                 "import os, pathlib, re\n"
                 "os.environ.pop('ROMP_CHECKOUT', None)\n"
                 "src = open(os.environ['KSRC']).read()\n"
                 "m = re.search(r'ROOT = .*', src)\n"
                 "HERE = pathlib.Path(os.environ['KSRC']).resolve().parent\n"
                 "Path = pathlib.Path\n"
                 "ROOT = eval(m.group(0).split('= ', 1)[1])\n"
                 "print(ROOT)"],
                env=dict(os.environ, KSRC=ksrc),
                capture_output=True, text=True, timeout=20)
            self.assertEqual(out2.stdout.strip(), str(pathlib.Path(ksrc).resolve().parent.parent),
                             "unset = the classic layout, byte-identical behavior")
