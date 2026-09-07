"""`romp keysource` — the guided setup of the 1Password key source, the `--check` preflight `romp
refresh` runs, and the `--probe` that releases held sessions on the running kernel (the user
2026-09-07, after a box on API-key billing with no key line in its env file lost its board for twenty
minutes and nothing said what to write where).

What these pin:
  GuidedSetup   — the interactive walk: a bad reference is re-asked, the token is taken through the
                  no-echo reader and written BY NAME as OP_SERVICE_ACCOUNT_TOKEN, the reference lands as
                  ROMP_API_KEY_REF, every other line survives, the mode is 0600, and the restart is only
                  ever run after a yes. A partial write says what LANDED before the error. --path to a
                  file the kernel does not read says the write is NOT live. A running kernel is probed at
                  the end so its held sessions resume without a restart.
  NonInteractive — --ref/--token-stdin/--no-token/--yes/--restart/--path; a bad reference fails loudly
                  and touches nothing; no terminal + no flags is a refusal, not a hang — but a pipe at
                  the RESTART question (both lines already written) is --yes without --restart, exit 0.
  NothingLeaks  — no token or reference value on stdout or stderr in any branch; the only rendered
                  form of the reference is its fingerprint.
  Check         — the file logic, mirroring the kernel's own hold predicate: configured → 0; an ALIVE
                  reg with an explicit key pick, or a remembered `key` Billing pick, with no source and
                  no apiKeyHelper → 1 with the remedy; ROMP_EXPECTED_AUTH=key alone holds nobody → 0.
  KernelFirst   — the running kernel is asked before the file (GET /keysource with the serve token):
                  configured → 0 in one line; not configured / predates the route / refuses → the file.
  Unsupervised  — no kernel and no login service → a warning and 0, never a refusal on the file alone.
  Probe         — POST /keysource/probe: ok/note/lifted rendered by name, exit 0 only when resolved.

Synthetic secrets only (`ops_TEST…`, `op://vault-test/item/field`), a temp service.env under
ROMP_SERVICE_ENV_FILE, a temp state dir holding one synthetic reg, and a fake kernel on a loopback port
of its own (ROMP_KERNEL_PORT) — no test dials this machine's real kernel, service or `op`.
"""
import atexit
import http.server
import io
import shutil
import json
import os
import stat
import tempfile
import threading
import unittest
from importlib.machinery import SourceFileLoader
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
# Hermetic BEFORE the load: a state root and an env-file path of our own, so a bare run of this file can
# never read the machine's real service.env, registry, serve token or Claude settings.
_TMP = tempfile.mkdtemp()
atexit.register(shutil.rmtree, _TMP, True)          # scratch, cleaned on exit
os.environ["XDG_STATE_HOME"] = os.path.join(_TMP, "state")
for _v in ("ROMP_STATE_DIR", "ROMP_SUPERVISED", "ROMP_API_KEY_REF", "ANTHROPIC_API_KEY",
           "ROMP_KERNEL_PORT", "ROMP_SERVE_PORT", "ROMP_SERVE_TOKEN", "OP_SERVICE_ACCOUNT_TOKEN"):
    os.environ.pop(_v, None)
os.environ["ROMP_SERVICE_ENV_FILE"] = os.path.join(_TMP, "no-such-service.env")
os.environ["ROMP_SERVICE_ENV"] = os.environ["ROMP_SERVICE_ENV_FILE"]
os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(_TMP, "claude")

cli = SourceFileLoader("romp_keysource_cli_test", os.path.join(BIN, "romp-keysource")).load_module()
ks = cli.ks

REF = "op://vault-test/item/field"
REF2 = "op://vault-test/other-item/section/field"
TOKEN = "ops_TEST_0000000000000000000000000000000000000000"
TOKEN2 = "ops_TEST_1111111111111111111111111111111111111111"
SERVE_TOKEN = "serve-TEST-token-0000"
SID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"       # a private synthetic sid for this module's reg


class _FakeKernel:
    """A kernel stand-in on a loopback port of its own: /version auth-exempt, /keysource and
    /keysource/probe behind the serve token. `keysource`/`probe` None → 404 (a kernel predating the
    route). Records every (method, path, token) it saw."""

    def __init__(self, keysource=None, probe=None, status=200):
        self.keysource, self.probe, self.status, self.calls = keysource, probe, status, []
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _gated(self, body):
                if self.headers.get("X-Romp-Token") != SERVE_TOKEN:
                    return self._reply(401, {"error": "bad token"})
                if body is None:
                    return self._reply(404, {"error": "no such route"})
                return self._reply(fake.status, body)

            def do_GET(self):
                fake.calls.append(("GET", self.path, self.headers.get("X-Romp-Token")))
                if self.path == "/version":
                    return self._reply(200, {"version": "TEST"})
                if self.path == "/keysource":
                    return self._gated(fake.keysource)
                return self._reply(404, {})

            def do_POST(self):
                fake.calls.append(("POST", self.path, self.headers.get("X-Romp-Token")))
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                if self.path == "/keysource/probe":
                    return self._gated(fake.probe)
                return self._reply(404, {})

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class _Base(unittest.TestCase):
    OTHER_LINES = ["# romp service environment", "ROMP_PERF=1", "ROMP_EXPECTED_AUTH=key"]

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, "service.env")
        self.state = os.path.join(self.d, "state")
        os.makedirs(os.path.join(self.state, "sdk"))
        self._env = {v: os.environ.get(v) for v in ("ROMP_SERVICE_ENV_FILE", "ROMP_SERVICE_ENV", "ROMP_STATE_DIR",
                                                    "ROMP_KERNEL_PORT", "ROMP_SERVE_TOKEN", "OP_SERVICE_ACCOUNT_TOKEN")}
        os.environ["ROMP_SERVICE_ENV_FILE"] = self.path
        os.environ["ROMP_SERVICE_ENV"] = self.path
        os.environ["ROMP_STATE_DIR"] = self.state
        ks._CACHE = ((), "")
        ks._AUTHORITATIVE_PATHS.clear()
        self.out, self.err = [], []
        self.ran = []
        self._stderr = mock.patch("sys.stderr", new_callable=io.StringIO)
        self.stderr = self._stderr.start()
        # no test may touch a tmux server: the marker write that follows write_source is file-only, but the
        # claim the resolve path runs is not on any path here — pin the runner anyway
        self._tmux = mock.patch.object(ks, "_TMUX_RUN")
        self._tmux.start()
        # no test may dial this machine's kernel or ask its service manager: by default no kernel answers
        # and a login service supervises (the conservative side); the fake-kernel tests lift the first
        self._kernel = mock.patch.object(cli, "_kernel", return_value=None)
        self._kernel.start()
        self._svc = mock.patch.object(cli, "service_is_active", return_value=True)
        self._svc.start()
        self.fake = None

    def tearDown(self):
        if self.fake:
            self.fake.close()
        self._svc.stop()
        self._kernel.stop()
        self._tmux.stop()
        self._stderr.stop()
        for v, was in self._env.items():
            if was is None:
                os.environ.pop(v, None)
            else:
                os.environ[v] = was
        ks._CACHE = ((), "")
        ks._AUTHORITATIVE_PATHS.clear()
        shutil.rmtree(self.d, ignore_errors=True)

    def fake_kernel(self, **kw):
        """Start the fake and point the CLI's port discovery at it (the real _kernel, real token header)."""
        self.fake = _FakeKernel(**kw)
        self._kernel.stop()
        os.environ["ROMP_KERNEL_PORT"] = str(self.fake.port)
        os.environ["ROMP_SERVE_TOKEN"] = SERVE_TOKEN
        return self.fake

    def write_env(self, *lines):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(self.OTHER_LINES + list(lines)) + "\n")
        os.chmod(self.path, 0o600)
        ks._CACHE = ((), "")

    def lines(self):
        return open(self.path, encoding="utf-8").read().splitlines()

    def names(self):
        return [ln.partition("=")[0] for ln in self.lines() if "=" in ln and not ln.startswith("#")]

    def run_cli(self, argv, answers=(), secrets=(), stdin="", run_rc=0):
        answers, secrets = list(answers), list(secrets)

        def ask(prompt):
            self.out.append("? " + prompt)
            if not answers:
                raise AssertionError("asked more than the test scripted: %r" % prompt)
            return answers.pop(0)

        def secret(prompt):
            self.out.append("? " + prompt)
            if not secrets:
                raise AssertionError("asked for a secret the test did not script: %r" % prompt)
            return secrets.pop(0)

        def run(cmd):
            self.ran.append(list(cmd))
            return run_rc

        rc = cli.main(argv, out=self.out.append, ask=ask, secret=secret, run=run, stdin=io.StringIO(stdin))
        self.assertFalse(answers, "unused scripted answers: %r" % answers)
        return rc

    def surfaces(self):
        return "\n".join(self.out) + "\n" + self.stderr.getvalue()

    def assert_nothing_leaked(self):
        text = self.surfaces()
        for secret in (TOKEN, TOKEN2, REF, REF2, "vault-test", SERVE_TOKEN):
            self.assertNotIn(secret, text, "a value reached a surface")

    def write_reg(self, alive=True, auth="key", sid=SID, name="web"):
        reg = {"sid": sid, "name": name, "alive": alive, "auth": auth, "lastSid": sid}
        with open(os.path.join(self.state, "sdk", sid + ".json"), "w") as fh:
            json.dump(reg, fh)

    def write_defaults(self, **d):
        with open(os.path.join(self.state, "sdk-defaults.json"), "w") as fh:
            json.dump(d, fh)


class GuidedSetup(_Base):
    def test_the_walk_writes_both_lines_by_name_keeps_the_rest_and_never_echoes(self):
        self.write_env("ANTHROPIC_API_KEY=sk-ant-TEST-0000")
        rc = self.run_cli([], answers=["op://not-a-ref", REF, "1", "n"], secrets=[TOKEN])
        self.assertEqual(rc, 0, self.surfaces())
        self.assertIn("ROMP_API_KEY_REF=" + REF, self.lines())
        self.assertIn("OP_SERVICE_ACCOUNT_TOKEN=" + TOKEN, self.lines())
        self.assertNotIn("ANTHROPIC_API_KEY", self.names(), "the competing static key line is removed")
        for ln in self.OTHER_LINES:
            self.assertIn(ln, self.lines(), "every other line survives")
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(self.ran, [], "no restart without a yes")
        text = self.surfaces()
        self.assertIn("try again", text, "the bad reference was re-asked, not accepted")
        self.assertIn("OP_SERVICE_ACCOUNT_TOKEN added", text)
        self.assertIn("ROMP_API_KEY_REF added, ANTHROPIC_API_KEY= removed", text)
        self.assertIn("mode 600", text)
        self.assertIn("live at once", text)
        self.assertNotIn("probing the route", text, "no kernel answers: nothing to probe")
        self.assertIn("systemctl --user restart romp-manager.service", text) if cli.sys.platform != "darwin" \
            else self.assertIn("launchctl kickstart -k", text)
        self.assertIn(ks.KeySource("op", REF).fingerprint(), text, "the reference shows as its fingerprint")
        self.assert_nothing_leaked()

    def test_a_yes_to_the_restart_question_runs_the_platform_command_once(self):
        self.write_env()
        rc = self.run_cli([], answers=[REF, "1", "y"], secrets=[TOKEN])
        self.assertEqual(rc, 0, self.surfaces())
        self.assertEqual(self.ran, [cli.restart_command()])
        self.assertIn("restart     done", self.surfaces())
        self.assert_nothing_leaked()

    def test_a_failed_restart_is_said_and_exits_nonzero_with_the_file_already_written(self):
        self.write_env()
        rc = self.run_cli([], answers=[REF, "1", "y"], secrets=[TOKEN], run_rc=5)
        self.assertEqual(rc, 1)
        self.assertIn("FAILED (exit 5)", self.surfaces())
        self.assertIn("ROMP_API_KEY_REF=" + REF, self.lines())

    def test_the_desktop_route_writes_no_token_line_and_prints_the_headless_caveat(self):
        self.write_env()
        rc = self.run_cli([], answers=[REF, "2"])
        self.assertEqual(rc, 0, self.surfaces())
        self.assertNotIn("OP_SERVICE_ACCOUNT_TOKEN", self.names())
        self.assertIn("ROMP_API_KEY_REF=" + REF, self.lines())
        self.assertIn("no desktop app to unlock", self.surfaces())
        self.assertEqual(self.ran, [], "nothing to restart for: no token line changed")
        self.assert_nothing_leaked()

    def test_an_empty_token_is_re_asked_and_a_rotated_token_says_replaced(self):
        self.write_env("OP_SERVICE_ACCOUNT_TOKEN=" + TOKEN, "ROMP_API_KEY_REF=" + REF)
        rc = self.run_cli([], answers=[REF2, "1", "n"], secrets=["", "has space", TOKEN2])
        self.assertEqual(rc, 0, self.surfaces())
        self.assertEqual(self.lines().count("OP_SERVICE_ACCOUNT_TOKEN=" + TOKEN2), 1)
        self.assertNotIn("OP_SERVICE_ACCOUNT_TOKEN=" + TOKEN, self.lines())
        self.assertIn("OP_SERVICE_ACCOUNT_TOKEN replaced", self.surfaces())
        self.assertIn("ROMP_API_KEY_REF replaced", self.surfaces())
        self.assertIn("rotated", self.surfaces())
        self.assert_nothing_leaked()

    def test_the_report_names_the_op_credential_by_name_only(self):
        self.write_env("OP_SERVICE_ACCOUNT_TOKEN=" + TOKEN, "ROMP_API_KEY_REF=" + REF)
        cli.report(self.path, self.out.append)
        text = self.surfaces()
        self.assertIn("credential present in the file: OP_SERVICE_ACCOUNT_TOKEN", text)
        self.assertIn("1Password reference " + ks.KeySource("op", REF).fingerprint(), text)
        self.assertNotIn("also set in this shell", text, "nothing in the environment: nothing to say about it")
        self.assert_nothing_leaked()

    def test_the_report_says_the_files_line_wins_when_the_shell_carries_the_same_name(self):
        # C2h: an operator who exported a fresh token in their shell is told the kernel's `op read` reads
        # the FILE's line (keysource.op_subprocess_env), by name — neither value is shown
        self.write_env("OP_SERVICE_ACCOUNT_TOKEN=" + TOKEN, "ROMP_API_KEY_REF=" + REF)
        os.environ["OP_SERVICE_ACCOUNT_TOKEN"] = TOKEN2
        cli.report(self.path, self.out.append)
        text = self.surfaces()
        self.assertIn("OP_SERVICE_ACCOUNT_TOKEN also set in this shell's environment", text)
        self.assertIn("uses the file's line", text)
        self.assertEqual(sum("op auth" in ln for ln in self.out), 2, "one line for it, not a paragraph")
        self.assert_nothing_leaked()

    def test_a_missing_file_is_created_0600_with_both_lines(self):
        rc = self.run_cli([], answers=[REF, "1", "n"], secrets=[TOKEN])
        self.assertEqual(rc, 0, self.surfaces())
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(sorted(self.names()), ["OP_SERVICE_ACCOUNT_TOKEN", "ROMP_API_KEY_REF"])

    def test_a_failure_between_the_two_writes_says_what_landed(self):
        # C2f: the token rewrite succeeded, the reference rewrite failed — the file now carries the
        # (inert) token and no reference, and the report says exactly that before the error
        self.write_env()
        with mock.patch.object(ks, "write_source", side_effect=OSError("disk full TEST")):
            rc = self.run_cli(["--ref", REF, "--token-stdin", "--yes"], stdin=TOKEN + "\n")
        self.assertEqual(rc, 1)
        self.assertIn("landed      OP_SERVICE_ACCOUNT_TOKEN added; ROMP_API_KEY_REF NOT written", self.surfaces())
        self.assertIn("could not rewrite", self.stderr.getvalue())
        self.assertIn("disk full TEST", self.stderr.getvalue())
        self.assertIn("OP_SERVICE_ACCOUNT_TOKEN=" + TOKEN, self.lines())
        self.assertNotIn("ROMP_API_KEY_REF", self.names())
        self.assert_nothing_leaked()

    def test_a_failure_on_the_first_write_says_neither_landed(self):
        self.write_env()
        with mock.patch.object(ks, "write_assignment", side_effect=OSError("read-only TEST")):
            rc = self.run_cli(["--ref", REF, "--token-stdin", "--yes"], stdin=TOKEN + "\n")
        self.assertEqual(rc, 1)
        self.assertIn("landed      OP_SERVICE_ACCOUNT_TOKEN NOT written; ROMP_API_KEY_REF NOT written", self.surfaces())
        self.assertEqual(self.names(), ["ROMP_PERF", "ROMP_EXPECTED_AUTH"])

    def test_a_reachable_kernel_is_probed_at_the_end_and_the_released_sessions_named(self):
        # C2d: the operator's fix releases held sessions without a restart; the probe's verdict is said
        # and the setup's own exit code stands
        self.write_env()
        fake = self.fake_kernel(probe={"ok": True, "note": "", "lifted": ["web", "api"], "kind": "op",
                                       "sourceFp": "ref:TESTFP"})
        rc = self.run_cli(["--ref", REF, "--token-stdin", "--yes"], stdin=TOKEN + "\n")
        self.assertEqual(rc, 0, self.surfaces())
        text = self.surfaces()
        self.assertIn("probing the route on the running kernel…", text)
        self.assertIn("probe       ok", text)
        self.assertIn("released    web, api", text)
        self.assertIn(("POST", "/keysource/probe", SERVE_TOKEN), fake.calls, "the serve token rides the call")
        self.assertLess(text.index("probing the route"), text.index("systemctl --user restart") if cli.sys.platform != "darwin"
                        else text.index("launchctl kickstart"), "probed before the restart question")
        self.assert_nothing_leaked()

    def test_a_failed_probe_is_said_but_does_not_fail_the_setup(self):
        self.write_env()
        self.fake_kernel(probe={"ok": False, "note": "op could not read the reference (auth)", "lifted": []})
        rc = self.run_cli(["--ref", REF, "--no-token", "--yes"])
        self.assertEqual(rc, 0, self.surfaces())
        self.assertIn("probe       FAILED — op could not read the reference (auth)", self.surfaces())
        self.assertIn("released    no held session", self.surfaces())
        self.assertIn("ROMP_API_KEY_REF=" + REF, self.lines())

    def test_a_kernel_predating_the_route_is_said_so_at_the_probe(self):
        self.write_env()
        self.fake_kernel(probe=None)
        rc = self.run_cli(["--ref", REF, "--no-token", "--yes"])
        self.assertEqual(rc, 0, self.surfaces())
        self.assertIn("predates `romp keysource --probe`", self.surfaces())


class NonInteractive(_Base):
    def test_a_bad_reference_given_on_the_command_line_fails_and_touches_nothing(self):
        self.write_env("ANTHROPIC_API_KEY=sk-ant-TEST-0000")
        before = self.lines()
        rc = self.run_cli(["--ref", "op://only-two/parts", "--yes"])
        self.assertEqual(rc, 2)
        self.assertIn("op://vault/item/[section/]field", self.stderr.getvalue())
        self.assertIn("file untouched", self.stderr.getvalue())
        self.assertEqual(self.lines(), before)

    def test_token_on_stdin_with_yes_writes_both_and_does_not_restart(self):
        self.write_env()
        rc = self.run_cli(["--ref", REF, "--token-stdin", "--yes"], stdin=TOKEN + "\n")
        self.assertEqual(rc, 0, self.surfaces())
        self.assertIn("OP_SERVICE_ACCOUNT_TOKEN=" + TOKEN, self.lines())
        self.assertIn("ROMP_API_KEY_REF=" + REF, self.lines())
        self.assertEqual(self.ran, [])
        self.assertIn("not run: --yes without --restart", self.surfaces())
        self.assert_nothing_leaked()

    def test_token_on_a_pipe_without_yes_writes_both_and_leaves_the_restart_to_the_operator(self):
        # C2e: only a PRE-write question may take the exit-2 path; here both lines are already in the
        # file when the restart question finds no terminal, so it is --yes without --restart, exit 0
        self.write_env()
        with mock.patch.object(cli.sys, "stdin", io.StringIO(TOKEN + "\n")):
            rc = cli.main(["--ref", REF, "--token-stdin"], out=self.out.append, run=lambda cmd: self.ran.append(cmd) or 0)
        self.assertEqual(rc, 0, self.surfaces())
        self.assertIn("OP_SERVICE_ACCOUNT_TOKEN=" + TOKEN, self.lines())
        self.assertIn("ROMP_API_KEY_REF=" + REF, self.lines())
        self.assertEqual(self.ran, [])
        self.assertIn("(not run: stdin is not a terminal; run %s yourself)" % " ".join(cli.restart_command()),
                      self.surfaces())
        self.assertNotIn("pass --ref", self.stderr.getvalue(), "the pre-write refusal must not fire after a write")
        self.assert_nothing_leaked()

    def test_a_pre_write_question_on_a_pipe_is_still_the_refusal_with_the_file_untouched(self):
        self.write_env()
        before = self.lines()
        with mock.patch.object(cli.sys, "stdin", io.StringIO("")):
            rc = cli.main(["--ref", REF], out=self.out.append)        # the op-auth question has no terminal
        self.assertEqual(rc, 2)
        self.assertIn("not a terminal", self.stderr.getvalue())
        self.assertEqual(self.lines(), before)

    def test_restart_flag_runs_the_command_without_asking(self):
        self.write_env()
        rc = self.run_cli(["--ref", REF, "--token-stdin", "--restart"], stdin=TOKEN + "\n")
        self.assertEqual(rc, 0, self.surfaces())
        self.assertEqual(self.ran, [cli.restart_command()])

    def test_an_empty_stdin_token_is_a_refusal(self):
        self.write_env()
        rc = self.run_cli(["--ref", REF, "--token-stdin", "--yes"], stdin="\n")
        self.assertEqual(rc, 2)
        self.assertIn("token is empty", self.stderr.getvalue())
        self.assertNotIn("ROMP_API_KEY_REF", self.names())

    def test_no_token_route_and_an_explicit_path_that_the_kernel_does_not_read(self):
        # C2g: the write lands, but nothing in that file is live until the kernel is pointed at it — and
        # no kernel is probed for a file it does not read
        other = os.path.join(self.d, "other.env")
        fake = self.fake_kernel(probe={"ok": True, "lifted": []})
        rc = self.run_cli(["--ref", REF, "--no-token", "--yes", "--path", other])
        self.assertEqual(rc, 0, self.surfaces())
        self.assertFalse(os.path.exists(self.path), "--path wins over ROMP_SERVICE_ENV_FILE")
        self.assertEqual(open(other).read(), "ROMP_API_KEY_REF=%s\n" % REF)
        self.assertEqual(stat.S_IMODE(os.stat(other).st_mode), 0o600)
        self.assertIn("no desktop app to unlock", self.surfaces())
        text = self.surfaces()
        self.assertIn("effect      NOT live: this is not the file the kernel reads (%s)" % self.path, text)
        self.assertIn("romp keyswap <name>", text)
        self.assertIn("ROMP_SERVICE_ENV_FILE", text)
        self.assertNotIn("live at once", text)
        self.assertEqual(fake.calls, [], "a file the kernel does not read is not probed on it")

    def test_a_path_that_resolves_to_the_kernels_file_is_live(self):
        link = os.path.join(self.d, "service-link.env")
        self.write_env()
        os.symlink(self.path, link)
        rc = self.run_cli(["--ref", REF, "--no-token", "--yes", "--path", link])
        self.assertEqual(rc, 0, self.surfaces())
        self.assertIn("live at once", self.surfaces())
        self.assertNotIn("NOT live", self.surfaces())

    def test_yes_without_ref_and_conflicting_flags_are_refused(self):
        self.write_env()
        self.assertEqual(self.run_cli(["--yes"]), 2)
        self.assertIn("--yes needs --ref", self.stderr.getvalue())
        self.assertEqual(self.run_cli(["--ref", REF, "--token-stdin", "--no-token"]), 2)
        self.assertIn("exclude each other", self.stderr.getvalue())
        self.assertEqual(self.run_cli(["--check", "--probe"]), 2)
        self.assertEqual(self.run_cli(["--bogus"]), 2)
        self.assertEqual(self.names(), ["ROMP_PERF", "ROMP_EXPECTED_AUTH"])

    def test_no_terminal_and_no_flags_is_a_refusal_not_a_hang(self):
        self.write_env()
        with mock.patch.object(cli.sys, "stdin", io.StringIO("")):
            rc = cli.main([], out=self.out.append)
        self.assertEqual(rc, 2)
        self.assertIn("not a terminal", self.stderr.getvalue())

    def test_the_restart_command_per_platform_names_the_service_units(self):
        self.assertEqual(cli.restart_command("linux"), ["systemctl", "--user", "restart", "romp-manager.service"])
        mac = cli.restart_command("darwin", uid=501)
        self.assertEqual(mac[:3], ["launchctl", "kickstart", "-k"])
        self.assertEqual(mac[3], "gui/501/" + cli.launchd_label())
        self.assertEqual(cli.launchd_label(), "com.romp.manager", "bin/romp-service's LABEL, read live")


class Check(_Base):
    """The file logic (no kernel answers, a login service supervises)."""

    def check(self, *extra):
        return cli.main(["--check", *extra], out=self.out.append)

    def test_a_configured_reference_passes_with_one_line(self):
        self.write_env("ROMP_API_KEY_REF=" + REF)
        self.assertEqual(self.check(), 0)
        self.assertEqual(len(self.out), 1)
        self.assertIn("1Password reference " + ks.KeySource("op", REF).fingerprint(), self.out[0])
        self.assert_nothing_leaked()

    def test_a_static_key_passes_by_fingerprint(self):
        self.write_env("ANTHROPIC_API_KEY=sk-ant-TEST-0000")
        self.assertEqual(self.check(), 0)
        self.assertIn("sha256:" + ks.fingerprint("sk-ant-TEST-0000"), self.out[0])
        self.assertNotIn("sk-ant-TEST", self.surfaces())

    def test_the_declaration_alone_holds_nobody_and_is_mentioned_only_as_context(self):
        # C2b: ROMP_EXPECTED_AUTH=key in the file, a remembered login pick, alive login-billed sessions —
        # the kernel would hold none of them (an unpicked session launches login-side; the declaration is
        # inert once a pick exists), so the preflight passes and says why the declaration does not count
        self.write_env()                                       # ROMP_EXPECTED_AUTH=key, no key line
        self.write_defaults(auth="login", model="default")
        self.write_reg(alive=True, auth="login")
        self.write_reg(alive=True, auth="", sid="bbbbbbbb-cccc-4ddd-8eee-ffffffffffff", name="api")
        with mock.patch.object(ks, "cli_self_auth", return_value=""):
            self.assertEqual(self.check(), 0)
        self.assertEqual(len(self.out), 1)
        self.assertIn("nothing selects API-key billing", self.out[0])
        self.assertIn("ROMP_EXPECTED_AUTH=key is declared", self.out[0])
        self.assertIn("only an explicit API-key pick holds", self.out[0])
        self.assertNotIn(ks.REMEDY % self.path, self.surfaces())

    def test_an_alive_key_billed_session_alone_selects_key_billing(self):
        self.OTHER_LINES = ["ROMP_PERF=1"]                      # no declaration in the file
        self.write_env()
        self.write_reg(alive=True, auth="key")
        with mock.patch.object(ks, "cli_self_auth", return_value=""):
            self.assertEqual(self.check(), 1)
        self.assertIn("session web bills an API key", self.surfaces())
        self.assertIn(ks.REMEDY % self.path, self.surfaces())

    def test_a_dead_key_billed_reg_or_a_login_one_selects_nothing(self):
        self.OTHER_LINES = ["ROMP_PERF=1"]
        self.write_env()
        self.write_reg(alive=False, auth="key")
        self.write_reg(alive=True, auth="login", sid="bbbbbbbb-cccc-4ddd-8eee-ffffffffffff")
        with mock.patch.object(ks, "cli_self_auth", return_value=""):
            self.assertEqual(self.check(), 0)
        self.assertIn("nothing selects API-key billing", self.out[0])
        self.assertNotIn("ROMP_EXPECTED_AUTH", self.out[0], "no declaration in the file: no context about one")

    def test_a_remembered_key_pick_alone_selects_key_billing(self):
        self.OTHER_LINES = ["ROMP_PERF=1"]
        self.write_env()
        self.write_defaults(auth="key", model="default")
        with mock.patch.object(ks, "cli_self_auth", return_value=""):
            self.assertEqual(self.check(), 1)
        self.assertIn("remembered Billing pick is API key (every new session is seeded from it)", self.surfaces())
        self.assertIn(ks.REMEDY % self.path, self.surfaces())

    def test_an_apikeyhelper_box_passes_because_launches_ride_the_helper(self):
        self.write_env()
        self.write_reg(alive=True, auth="key")
        with mock.patch.object(ks, "cli_self_auth", return_value="apiKeyHelper"):
            self.assertEqual(self.check(), 0)
        self.assertIn("apiKeyHelper", self.out[0])

    def test_an_absent_file_and_empty_state_pass(self):
        self.assertEqual(self.check(), 0)
        self.assertIn("nothing selects API-key billing", self.out[0])

    def test_a_removed_reference_remembered_by_the_marker_fails_like_no_source(self):
        self.OTHER_LINES = ["ROMP_PERF=1"]
        self.write_env()
        with open(ks.marker_path(self.path), "w") as fh:
            fh.write("op\n")
        self.assertEqual(self.check(), 1)
        self.assertIn("reference was removed", self.surfaces())
        self.assertIn(ks.REMEDY % self.path, self.surfaces())

    def test_an_unreadable_source_line_fails_with_its_own_error(self):
        with open(self.path, "wb") as fh:
            fh.write(b"ROMP_API_KEY_REF=op://vault-test/\xff\xfe/field\n")
        ks._CACHE = ((), "")
        self.assertEqual(self.check(), 1)
        self.assertIn("not valid UTF-8", self.surfaces())

    def test_check_with_a_path_the_kernel_does_not_read_is_a_dry_run_of_the_file_alone(self):
        # C2g: the kernel's answer is about ITS file, so it is not asked; the file logic runs and says so
        other = os.path.join(self.d, "other.env")
        with open(other, "w") as fh:
            fh.write("ROMP_API_KEY_REF=%s\n" % REF)
        fake = self.fake_kernel(keysource={"configured": False, "kind": "none", "sourceFp": "", "held": ["web"]})
        self.assertEqual(self.check("--path", other), 0)
        self.assertEqual(self.out[0], "dry run against %s (the kernel reads %s)" % (other, self.path))
        self.assertIn("1Password reference " + ks.KeySource("op", REF).fingerprint(), self.out[1])
        self.assertEqual(fake.calls, [], "the kernel is not asked about a file it does not read")
        self.assert_nothing_leaked()


class KernelFirst(_Base):
    """C2c: the running kernel's answer outranks the file."""

    def check(self):
        return cli.main(["--check"], out=self.out.append)

    def test_a_kernel_reporting_a_configured_source_passes_in_one_line_whatever_the_file_says(self):
        self.write_reg(alive=True, auth="key")                 # the file logic alone would refuse: no file
        fake = self.fake_kernel(keysource={"configured": True, "kind": "op", "sourceFp": "ref:TESTFP",
                                           "held": [], "health": {}})
        with mock.patch.object(ks, "cli_self_auth", return_value=""):
            self.assertEqual(self.check(), 0)
        self.assertEqual(self.out, ["key source  the running kernel reports its source: op ref:TESTFP"])
        self.assertIn(("GET", "/keysource", SERVE_TOKEN), fake.calls, "the serve token rides the read")
        self.assert_nothing_leaked()

    def test_a_kernel_reporting_no_source_falls_to_the_file_and_names_what_it_holds(self):
        self.OTHER_LINES = ["ROMP_PERF=1"]
        self.write_env()
        self.write_reg(alive=True, auth="key")
        self.fake_kernel(keysource={"configured": False, "kind": "none", "sourceFp": "", "held": ["web"]})
        with mock.patch.object(ks, "cli_self_auth", return_value=""):
            self.assertEqual(self.check(), 1)
        self.assertIn("kernel      holding web", self.surfaces())
        self.assertIn(ks.REMEDY % self.path, self.surfaces())

    def test_a_kernel_predating_the_route_is_treated_as_no_answer(self):
        self.OTHER_LINES = ["ROMP_PERF=1"]
        self.write_env()
        self.write_reg(alive=True, auth="key")
        self.fake_kernel(keysource=None)                        # 404
        with mock.patch.object(ks, "cli_self_auth", return_value=""):
            self.assertEqual(self.check(), 1)
        self.assertIn(ks.REMEDY % self.path, self.surfaces())
        self.assertNotIn("HTTP", self.surfaces())

    def test_a_kernel_refusing_the_token_is_said_and_the_file_decides(self):
        self.write_env("ROMP_API_KEY_REF=" + REF)
        self.fake_kernel(keysource={"configured": True})
        os.environ["ROMP_SERVE_TOKEN"] = "wrong-TEST-token"
        self.assertEqual(self.check(), 0)
        self.assertIn("answered HTTP 401 on /keysource", self.surfaces())
        self.assertIn("1Password reference " + ks.KeySource("op", REF).fingerprint(), self.surfaces())

    def test_an_unusable_port_override_is_no_answer_not_a_dial_of_the_default_ports(self):
        self.write_env("ROMP_API_KEY_REF=" + REF)
        self._kernel.stop()
        os.environ["ROMP_KERNEL_PORT"] = "not-a-port"
        self.assertEqual(self.check(), 0)
        self.assertIn("1Password reference", self.out[0])

    def test_a_kernel_in_a_running_shell_reports_nothing_when_no_route_answers(self):
        # the default-port list is never dialled here: no kernel means the file speaks
        self.OTHER_LINES = ["ROMP_PERF=1"]
        self.write_env("ANTHROPIC_API_KEY=sk-ant-TEST-0000")
        self.assertEqual(self.check(), 0)
        self.assertIn("static key", self.out[0])


class Unsupervised(_Base):
    """C2c: no kernel answers and no login service runs the manager — the file cannot show a key the
    manager may hold in its environment, so warn, never refuse on the file alone."""

    def check(self):
        with mock.patch.object(ks, "cli_self_auth", return_value=""):
            return cli.main(["--check"], out=self.out.append)

    def setUp(self):
        super().setUp()
        self.OTHER_LINES = ["ROMP_PERF=1"]
        self.write_env()
        self.write_reg(alive=True, auth="key")

    def test_no_service_and_no_kernel_is_a_warning_and_exit_0(self):
        self._svc.stop()
        with mock.patch.object(cli, "service_is_active", return_value=False):
            self.assertEqual(self.check(), 0)
        text = self.surfaces()
        self.assertIn("warning     no login service runs the manager here (and no kernel answered)", text)
        self.assertIn("session web bills an API key", text, "the file's finding is still said")
        self.assertIn(ks.REMEDY % self.path, text, "and the remedy, for the case the sessions do hold")
        self.assertNotIn("\n" + ks.REMEDY % self.path + "\n", "\n" + "\n".join(self.out) + "\n",
                         "but not as its own refusal line")

    def test_a_service_manager_that_cannot_be_asked_keeps_the_refusal(self):
        self._svc.stop()
        with mock.patch.object(cli, "service_is_active", return_value=None):
            self.assertEqual(self.check(), 1)
        self.assertNotIn("warning", self.surfaces())

    def test_an_active_service_keeps_the_refusal(self):
        self.assertEqual(self.check(), 1)
        self.assertIn(ks.REMEDY % self.path, self.surfaces())

    def test_a_kernel_that_answers_is_never_downgraded(self):
        self.fake_kernel(keysource={"configured": False, "kind": "none", "sourceFp": "", "held": ["web"]})
        self._svc.stop()
        with mock.patch.object(cli, "service_is_active", return_value=False):
            self.assertEqual(self.check(), 1)
        self.assertNotIn("warning", self.surfaces())

    def test_supervised_marker_in_the_shell_is_a_yes_without_asking(self):
        self._svc.stop()                                       # the real function, not the class-level stand-in
        os.environ["ROMP_SUPERVISED"] = "1"
        try:
            with mock.patch.object(cli.subprocess, "run", side_effect=AssertionError("must not run")):
                self.assertIs(cli.service_is_active("linux"), True)
        finally:
            os.environ.pop("ROMP_SUPERVISED", None)

    def test_service_is_active_reads_the_platform_tools_and_no_tool_means_unsupervised(self):
        self._svc.stop()                                       # the real function, not the class-level stand-in
        with mock.patch.object(cli.subprocess, "run", return_value=mock.Mock(stdout="active\n", returncode=0)) as r:
            self.assertIs(cli.service_is_active("linux"), True)
            self.assertEqual(r.call_args[0][0], ["systemctl", "--user", "is-active", "romp-manager.service"])
        with mock.patch.object(cli.subprocess, "run", return_value=mock.Mock(stdout="inactive\n", returncode=3)):
            self.assertIs(cli.service_is_active("linux"), False)
        with mock.patch.object(cli.subprocess, "run", return_value=mock.Mock(returncode=0)) as r:
            self.assertIs(cli.service_is_active("darwin"), True)
            self.assertEqual(r.call_args[0][0][:2], ["launchctl", "print"])
        with mock.patch.object(cli.subprocess, "run", side_effect=FileNotFoundError()):
            self.assertIs(cli.service_is_active("linux"), False)
        with mock.patch.object(cli.subprocess, "run", side_effect=cli.subprocess.TimeoutExpired("x", 5)):
            self.assertIsNone(cli.service_is_active("linux"))


class Probe(_Base):
    """C2d: `romp keysource --probe`."""

    def probe(self):
        return cli.main(["--probe"], out=self.out.append)

    def test_a_resolving_kernel_names_the_released_sessions_and_exits_0(self):
        fake = self.fake_kernel(probe={"ok": True, "note": "", "lifted": ["web"], "kind": "op", "sourceFp": "ref:TESTFP"})
        self.assertEqual(self.probe(), 0)
        self.assertEqual(self.out, ["probe       ok — the kernel resolved its op source ref:TESTFP", "released    web"])
        self.assertEqual([c for c in fake.calls if c[0] == "POST"], [("POST", "/keysource/probe", SERVE_TOKEN)])
        self.assert_nothing_leaked()

    def test_a_failed_resolve_prints_the_kernels_fixed_note_and_exits_1(self):
        self.fake_kernel(probe={"ok": False, "note": "1Password could not read the reference (auth)", "lifted": []})
        self.assertEqual(self.probe(), 1)
        self.assertIn("probe       FAILED — 1Password could not read the reference (auth)", self.out)
        self.assertIn("released    no held session", self.surfaces())

    def test_no_kernel_is_exit_1_with_what_picks_the_source_up_instead(self):
        self.assertEqual(self.probe(), 1)
        self.assertIn("no running kernel answered", self.stderr.getvalue())
        self.assertIn("romp refresh", self.stderr.getvalue())

    def test_a_kernel_predating_the_route_is_exit_1_and_said(self):
        self.fake_kernel(probe=None)
        self.assertEqual(self.probe(), 1)
        self.assertIn("predates `romp keysource --probe`", self.surfaces())

    def test_a_refused_token_is_exit_1_and_names_the_status(self):
        self.fake_kernel(probe={"ok": True})
        os.environ["ROMP_SERVE_TOKEN"] = "wrong-TEST-token"
        self.assertEqual(self.probe(), 1)
        self.assertIn("HTTP 401 on /keysource/probe", self.stderr.getvalue())


class SourceShape(unittest.TestCase):
    """The bin/romp side: dispatch, help, and the refresh preflight reading THIS tree's binary."""

    def setUp(self):
        self.src = open(os.path.join(BIN, "romp"), encoding="utf-8").read()

    def test_dispatch_and_help(self):
        self.assertIn('if [[ "${1:-}" == "keysource" ]]; then\n    shift\n    exec romp-keysource "$@"\nfi', self.src)
        self.assertIn('_romp_cmd "romp keysource [--check]"   romp-keysource', self.src)

    def test_the_refresh_preflight_runs_this_trees_binary_and_refuses_on_exit_1_only(self):
        i = self.src.index("refresh)   # restart EVERYTHING")
        block = self.src[i:self.src.index("status)    exec", i)]
        self.assertIn('${ROMP_KEYSOURCE_BIN:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/romp-keysource}', block,
                      "the code about to be deployed is the one that must be satisfied")
        self.assertIn('"$_rf_ks" --check', block)
        self.assertIn('if [[ "$_rf_rc" -eq 1 ]]; then', block)
        self.assertIn("pass --force to restart anyway", block)
        self.assertIn("preflight could not run", block)
        self.assertLess(block.index('"$_rf_ks" --check'), block.index('>> "$_ra_dir/restart-audit.jsonl"'),
                        "the preflight runs BEFORE the audit row: a refused refresh leaves no row claiming one")
        self.assertIn('RA_WHEN="${2:-}"', block, "--force is consumed by re-setting the positionals, not by renaming $2")
        # C2a: an EMPTY array under `set -u` is "unbound" to bash < 4.4 (macOS /bin/bash 3.2), so a
        # flagless `romp refresh` aborted there; the file's own `[@]+` idiom is the fix
        self.assertIn('set -- refresh ${_rf_rest[@]+"${_rf_rest[@]}"}', block)
        self.assertNotIn('set -- refresh "${_rf_rest[@]}"', block)

    def test_the_cli_exits_3_on_a_crash_so_a_crash_never_reads_as_refuse(self):
        src = open(os.path.join(BIN, "romp-keysource"), encoding="utf-8").read()
        self.assertEqual(cli.EXIT_UNEXPECTED, 3)
        self.assertIn("sys.exit(EXIT_UNEXPECTED)", src)

    def test_the_kernel_discovery_matches_keyswaps(self):
        # one port list, one token resolution: a renumbered instance never hands its token to the default port
        swap = open(os.path.join(BIN, "romp-keyswap"), encoding="utf-8").read()
        self.assertIn("KPORTS = %r" % cli.KPORTS, swap.replace('"', "'"))
        with mock.patch.dict(os.environ, {"ROMP_KERNEL_PORT": "1234"}):
            self.assertEqual(cli._kernel_urls(), ["http://127.0.0.1:1234"])
        with mock.patch.dict(os.environ, {"ROMP_KERNEL_PORT": "nope"}):
            self.assertRaises(ValueError, cli._kernel_urls)


if __name__ == "__main__":
    unittest.main()
