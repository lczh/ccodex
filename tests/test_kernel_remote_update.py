"""Remote version-drift detection + `romp update` (the user 2026-07-04): the local kernel polls each attached
remote's /version, flags one running an OLDER commit (outOfDate), and offers to pull+restart it behind the
scenes. `POST /tunnels/update` runs the ssh git-pull + restart; the rail popover + a top banner surface it.
SYNTHETIC hosts; subprocess/http are stubbed so nothing actually launches or connects."""
import json
import os
import pathlib
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

    def _wire(self, rhead=None, dirty="", disc_out=None, push_rc=0, push_err="", apply_out="SYNCED:abcdef0"):
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
            if "merge-base" in cmd or "reset --hard" in cmd:
                return _R(out=apply_out)
            return _R()
        km.subprocess.run = fake
        return calls

    def test_no_host_is_a_no_op(self):
        self.assertEqual(km._update_remote(""), (False, "no host"))

    def test_a_clean_ancestor_remote_is_pushed_reset_and_restarted(self):
        calls = self._wire(apply_out="SYNCED:abcdef0")
        ok, detail = km._update_remote("TESTHOST")
        self.assertTrue(ok)
        self.assertIn("synced to abcdef0", detail)
        # it force-pushed local HEAD to a scratch ref at host:remote-dir
        push = next(a for a in calls if a[0] == "git" and "push" in a)
        self.assertIn("--force", push)
        self.assertIn("TESTHOST:/home/u/romp", push)
        self.assertTrue(any(str(x).startswith("HEAD:refs/heads/") for x in push), "pushes HEAD to a scratch ref")

    def test_already_up_to_date_short_circuits(self):
        self._wire(rhead=self.LFULL)          # remote already at local HEAD
        ok, detail = km._update_remote("TESTHOST")
        self.assertTrue(ok)
        self.assertIn("already up to date", detail)

    def test_a_dirty_local_is_not_refused_it_pushes_committed_head(self):
        # "just take what is committed on local" (the user 2026-07-04): a dirty working tree is NOT a blocker —
        # _update_remote pushes the committed HEAD and never asks you to commit first.
        self._wire(apply_out="SYNCED:abcdef0")
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
        self.assertIn("STATERR", apply.partition("reset --hard")[0],
                      "the rc check sits before the reset, same shell")
        disc = next(a[-1] for a in calls if isinstance(a[-1], str) and "for d in" in a[-1])
        self.assertIn("STATERR", disc, "the discover probe distinguishes error from clean too")

    def test_the_generated_shell_actually_emits_STATERR_when_status_dies(self):
        # the string-level pins above never EXECUTE the shell: replanting the audited bug (a dead
        # status reading as clean) passed all of them (the adversarial review, 2026-08-17). Run
        # the real generated scripts against a fixture checkout whose git can't report status.
        import tempfile
        from pathlib import Path
        calls = self._wire(apply_out="SYNCED:abcdef0")
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
                "  *' rev-parse HEAD'*) echo 1111111111111111111111111111111111111111;;\n"
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
            self.assertNotIn("reset --hard", ops, "and the reset never ran")

    def test_the_apply_recheck_catches_an_edit_landing_after_the_probe(self):
        # the discover-step dirty probe is an ssh round-trip old by apply time; an edit landing in
        # that window must be re-caught IN THE SAME SHELL as the reset, or reset --hard destroys it
        # on the strength of a stale answer (the user's audit, 2026-08-17)
        calls = self._wire(apply_out="DIRTYNOW")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("uncommitted work between the check and the apply", detail)
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        pre, _, post = apply.partition("reset --hard")
        self.assertIn("status --porcelain", pre,
                      "the recheck sits between the ancestry gate and the reset, same shell")
        self.assertIn("DIRTYNOW", pre)
        self.assertNotIn("status --porcelain", post, "and never after the reset, where it is moot")

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
        self.assertIn("pkill -f", apply, "kills the running kernel")
        self.assertIn('"$R/bin/romp-manager" ensure', apply, "prefers the manager (ensure = idempotent supervised start)")
        self.assertIn("/dev/tcp/127.0.0.1/29855", apply, "polls the remote's kernel port to confirm it came back")
        self.assertIn('if [ "$UP" = 0 ]; then nohup "$R/bin/romp-serve"', apply, "bare romp-serve only as a last resort")
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
