"""Runtime credentials at the SDK boundary; all providers and sessions are synthetic."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from importlib.machinery import SourceFileLoader

ROOT = Path(__file__).resolve().parents[1]
_IMPORT_STATE = tempfile.mkdtemp(prefix="romp-runtime-auth-")
os.environ["XDG_STATE_HOME"] = _IMPORT_STATE
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_SERVICE_ENV_FILE"] = _IMPORT_STATE + "/absent.env"
os.environ["ROMP_SERVICE_ENV"] = os.environ["ROMP_SERVICE_ENV_FILE"]
os.environ.pop("ROMP_API_KEY_REF", None)
sb = SourceFileLoader("romp_sdk_runtime_auth", str(ROOT / "kernel/sdk_backend.py")).load_module()
ks = sb._keysrc
REF = "op://test-vault/test-item/credential"
KEY = "synthetic-runtime-credential"
# keysource.remedy()'s shape (one %s: the service env path). Patched in — the text is keysource's to own;
# these tests pin only that the backend carries it verbatim to every surface.
REMEDY_TEXT = ("No API key source is configured for API-key billing. Run `romp keysource`, or add "
               "ROMP_API_KEY_REF=op://vault/item/field (and OP_SERVICE_ACCOUNT_TOKEN for a headless service) "
               "to ~/service.env; held sessions resume on their own once it is there.")
HEALTH = {"kind": "op", "sourceFp": "", "lastOkT": 0.0, "lastFailT": 1700000000.0,
          "note": "1Password credential retrieval failed; check op authentication and vault access"}


def patch_keysource_probes(helper: str = ""):
    """The keysource module's host probes, pinned so no test reads THIS machine's Claude Code settings
    (an apiKeyHelper on the box would silently turn the refusal path into a helper launch). create=True:
    the backend imports them by name, and the module may still be growing them."""
    patch.object(ks, "cli_self_auth", create=True, return_value=helper).start()
    patch.object(ks, "remedy", create=True, return_value=REMEDY_TEXT).start()
    patch.object(ks, "health", create=True, return_value=dict(HEALTH)).start()
    # keysource's in-process retrieval memory (reuse window + single flight) and its retry delay: off and
    # instant here, so a launch resolves exactly when these tests say it does and a failure costs no wall time
    os.environ["ROMP_OP_REUSE_S"] = "0"
    patch.object(ks, "_SLEEP", create=True, new=lambda s: None).start()
    patch.object(ks, "_RESOLVED", create=True, new={}).start()
    patch.object(ks, "_RESOLVING", create=True, new={}).start()


def op_result(returncode=0, stdout=KEY.encode(), stderr=b""):
    """What the patched subprocess.run hands keysource for one `op read`: it reads returncode, stdout and
    (on failure, reduced to a fixed vocabulary) stderr."""
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class RuntimeSdkAuth(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "service.env"
        self.path.write_text("ROMP_API_KEY_REF=" + REF + "\n")
        self.env = patch.dict(os.environ, {
            "ROMP_SERVICE_ENV_FILE": str(self.path), "ROMP_SERVICE_ENV": str(self.path),
            "ROMP_API_KEY_REF": "", "ANTHROPIC_API_KEY": "synthetic-old-startup-key",
            "ANTHROPIC_AUTH_TOKEN": "synthetic-bearer", "CLAUDE_CODE_OAUTH_TOKEN": "synthetic-oauth",
            "CLAUDE_CONFIG_DIR": self.tmp.name + "/claude-config",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(patch.stopall)
        patch_keysource_probes()
        patch.object(sb, "_WORK_KEY", None).start()
        patch.object(sb, "_STARTUP_AUTH_ENV", None).start()
        patch.object(sb, "_KEY_FILE_CHECKED", True).start()
        patch.object(ks, "_CACHE", ((), ks.KeySource("none"))).start()
        patch.object(ks, "_AUTHORITATIVE_PATHS", {}).start()
        patch.object(sb, "_FAST_ORG_VERDICTS", {}).start()
        patch.object(sb, "_fetch_key_fast_org", return_value=True).start()
        self.provider = patch.object(ks.subprocess, "run", return_value=op_result()).start()
        fake_sdk = ModuleType("claude_agent_sdk")
        fake_sdk.HookMatcher = lambda **kw: kw
        fake_sdk.ClaudeAgentOptions = dict
        fake_sdk.ClaudeSDKClient = unittest.mock.Mock()
        for name in ("AssistantMessage", "ResultMessage", "SystemMessage"):
            setattr(fake_sdk, name, type(name, (), {}))
        self.client_factory = fake_sdk.ClaudeSDKClient
        patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk}).start()
        self.logs = []
        self.be = sb.SdkBackend(str(Path(self.tmp.name) / "state"), "/bin/true",
                                lambda *a, **k: None, log=self.logs.append)

    def session(self, auth="", **extra):
        sid = self.be.spawn("synthetic", "/tmp", auth=auth)
        reg = sb.read_reg(self.be.state_dir, sid)
        reg.update(extra)
        return sb.SdkSession(self.be, reg)

    def test_ui_defaults_and_auth_selection_never_retrieve_a_key(self):
        sess = self.session()
        for _ in range(3):
            self.assertTrue(self.be.work_key_configured)
            self.assertEqual(self.be.work_key_source_fp(), ks.KeySource("op", REF).fingerprint())
            self.assertEqual(self.be.default_auth({}), "key")
            self.assertEqual(sess.effective_auth(), "key")
            self.assertEqual(sess.snapshot()["auth"], "key")
        self.assertTrue(self.be.set_auth(sess.sid, "key"))
        self.provider.assert_not_called()
        self.assertEqual(sb._WORK_KEY, "", "the replaced startup key is discarded")

    def test_each_key_launch_resolves_once_and_keeps_secrets_out_of_files_and_caches(self):
        self.provider.side_effect = [op_result(), op_result(stdout=b"synthetic-rotated-key")]
        sess = self.session("key", env={"FEATURE_FLAG": "yes"})
        first = self.be._options(sess, dict)
        second = self.be._options(sess, dict)
        self.assertEqual(first["env"]["ANTHROPIC_API_KEY"], KEY)
        self.assertEqual(second["env"]["ANTHROPIC_API_KEY"], "synthetic-rotated-key")
        self.assertEqual(self.provider.call_count, 2)
        self.assertEqual(self.provider.call_args.args[0], ["op", "read", "--no-newline", REF])
        for name in sb.AUTH_ENV_NAMES:
            self.assertNotIn(name, os.environ)
        for name in sb.AUTH_ENV_NAMES[1:]:
            self.assertNotIn(name, first["env"])
        self.assertEqual(sb._WORK_KEY, "")
        self.assertNotIn(KEY, repr(sb._FAST_ORG_VERDICTS))
        for file in Path(self.tmp.name).rglob("*"):
            if file.is_file():
                self.assertNotIn(KEY.encode(), file.read_bytes(), str(file))
        self.assertNotIn(KEY, "\n".join(self.logs))

    def test_login_does_not_invoke_a_broken_provider_and_restores_only_login_tokens(self):
        self.provider.side_effect = FileNotFoundError()
        options = self.be._options(self.session("login"), dict)
        self.provider.assert_not_called()
        self.assertNotIn("ANTHROPIC_API_KEY", options["env"])
        self.assertEqual(options["env"]["ANTHROPIC_AUTH_TOKEN"], "synthetic-bearer")
        self.assertEqual(options["env"]["CLAUDE_CODE_OAUTH_TOKEN"], "synthetic-oauth")

    def test_failed_runtime_retrieval_never_constructs_a_client_and_records_launch_error(self):
        self.provider.return_value = op_result(1, b"provider-output-must-not-leak", b"op: stderr-must-not-leak")
        sess = self.session("key")
        with patch.object(self.be, "_record_launch_error") as record:
            # a credential failure HOLDS the session — _amain returns cleanly, no raise (2026-09-07)
            self.assertIsNone(asyncio.run(sess._amain()))
        record.assert_called_once()
        self.assertIsInstance(record.call_args.args[1], ks.KeySourceError)
        self.client_factory.assert_not_called()
        self.assertEqual(sess.effective_auth(), "key")
        self.assertNotIn("provider-output", str(record.call_args))

    def test_empty_explicit_key_refuses_login_fallback(self):
        self.be.work_key = ""
        sess = self.session("key")
        self.assertEqual(sess.effective_auth(), "key")
        with self.assertRaisesRegex(ks.KeySourceError, "No API key source"):
            self.be._options(sess, dict)
        self.provider.assert_not_called()

    def test_provider_failure_record_does_not_reuse_a_previous_clis_stderr(self):
        sess = self.session("key")
        with patch.object(sess, "stderr_tail", return_value="old CLI output"):
            self.be._record_launch_error(sess, ks.KeySourceError("1Password credential retrieval failed"))
        error = sb.read_reg(self.be.state_dir, sess.sid)["launchError"]
        self.assertIn("1Password credential retrieval failed", error["text"])
        self.assertNotIn("old CLI output", error["text"])

    def test_removed_runtime_reference_keeps_the_failure_explicit(self):
        self.path.write_text("ROMP_PERF=1\n")
        self.assertTrue(self.be.work_key_configured)
        with self.assertRaises(ks.KeySourceError):
            self.be._options(self.session(), dict)
        self.provider.assert_not_called()

    def test_runtime_mode_refuses_a_persisted_api_key_and_filters_legacy_settings(self):
        # A per-session API key competes with the runtime source: refused at the door, filtered at launch.
        self.assertIn("reserved", sb.env_request_error({"ANTHROPIC_API_KEY": "synthetic-legacy-secret"}))
        with self.assertRaisesRegex(ValueError, "reserved"):
            self.be.spawn("bad", "/tmp", env={"ANTHROPIC_API_KEY": "synthetic-legacy-secret"})
        sess = self.session("key", env={**{name: "synthetic-legacy-secret" for name in sb.AUTH_ENV_NAMES},
                                        "FEATURE_FLAG": "yes"})
        options = self.be._options(sess, dict)
        saved_env = json.loads(Path(options["settings"]).read_text())["env"]
        self.assertEqual(saved_env, {"FEATURE_FLAG": "yes"}, "a KEYED launch carries no competing credential of any kind")

    def test_a_login_session_keeps_its_own_token_override_under_runtime_mode(self):
        """A login session never touches the key source; its per-session OAuth token bills the account the
        user chose for it. Stripping it re-billed the machine login with only a log line (review find,
        2026-09-05). The door agrees: only the API key is reserved while runtime retrieval governs."""
        for name in sb.AUTH_ENV_NAMES[1:]:
            self.assertEqual(sb.env_request_error({name: "synthetic-second-login"}, "login"), "")
            self.assertIn("reserved", sb.env_request_error({name: "synthetic-second-login"}, "key"))
            self.assertIn("reserved", sb.env_request_error({name: "synthetic-second-login"}),
                          "no pick with a configured source launches keyed, so the door refuses a competing token")
        sess = self.session("login", env={"CLAUDE_CODE_OAUTH_TOKEN": "synthetic-second-login", "FEATURE_FLAG": "yes"})
        options = self.be._options(sess, dict)
        saved_env = json.loads(Path(options["settings"]).read_text())["env"]
        self.assertEqual(saved_env, {"CLAUDE_CODE_OAUTH_TOKEN": "synthetic-second-login", "FEATURE_FLAG": "yes"})
        self.assertNotIn("ANTHROPIC_API_KEY", options["env"], "a login launch resolves no key")
        self.provider.assert_not_called()

    def test_cycles_skip_login_dormant_and_busy_sessions_before_retrieval(self):
        login = self.session("login")
        self.be.sessions[login.sid] = login
        dormant = self.session("key")
        busy = self.session("key")
        self.be.sessions[busy.sid] = busy
        busy.inflight = 1
        self.assertEqual(self.be.cycle_key(login.sid), "login")
        self.assertEqual(self.be.cycle_key(dormant.sid), "dormant")
        self.assertEqual(self.be.cycle_key(busy.sid), "working")
        self.provider.assert_not_called()

    def test_cycle_resolves_once_for_currentness_and_logging(self):
        sess = self.session("key")
        self.be.sessions[sess.sid] = sess
        with patch.object(sess, "request_reconnect") as reconnect:
            self.assertEqual(self.be.cycle_key(sess.sid), "cycling")
        reconnect.assert_called_once_with(defer=False)
        self.assertEqual(self.provider.call_count, 1)
        sess._launched_key_fp = ks.fingerprint(KEY)
        with patch.object(sess, "request_reconnect") as reconnect:
            self.assertEqual(self.be.cycle_key(sess.sid), "current")
        reconnect.assert_not_called()
        self.assertEqual(self.provider.call_count, 2)


if __name__ == "__main__":
    unittest.main()


class OpCredentialAndDiscardNotice(unittest.TestCase):
    """Review finds of 2026-09-05, at the SDK boundary."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "service.env"
        self.path.write_text("ROMP_API_KEY_REF=" + REF + "\n")
        self.env = patch.dict(os.environ, {
            "ROMP_SERVICE_ENV_FILE": str(self.path), "ROMP_SERVICE_ENV": str(self.path),
            "ROMP_API_KEY_REF": "", "ANTHROPIC_API_KEY": "synthetic-old-startup-key",
            "OP_SERVICE_ACCOUNT_TOKEN": "synthetic-op-token", "OP_SESSION_acct": "synthetic-op-session",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(patch.stopall)
        os.environ.pop("ROMP_SUPERVISED", None)
        patch.object(sb, "_WORK_KEY", None).start()
        patch.object(sb, "_STARTUP_AUTH_ENV", None).start()
        patch.object(sb, "_STARTUP_KEY_DISCARD_SAID", False).start()
        patch.object(sb, "_KEY_FILE_CHECKED", True).start()
        patch.object(ks, "_CACHE", ((), ks.KeySource("none"))).start()
        patch.object(ks, "_AUTHORITATIVE_PATHS", {}).start()
        patch.object(ks, "_OP_ENV", {}).start()
        patch.object(sb, "_FAST_ORG_VERDICTS", {}).start()
        patch.object(sb, "_fetch_key_fast_org", return_value=True).start()
        self.provider = patch.object(ks.subprocess, "run", return_value=SimpleNamespace(
            returncode=0, stdout=KEY.encode())).start()
        fake_sdk = ModuleType("claude_agent_sdk")
        fake_sdk.HookMatcher = lambda **kw: kw
        fake_sdk.ClaudeAgentOptions = dict
        fake_sdk.ClaudeSDKClient = unittest.mock.Mock()
        for name in ("AssistantMessage", "ResultMessage", "SystemMessage"):
            setattr(fake_sdk, name, type(name, (), {}))
        patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk}).start()
        self.logs = []
        self.err = patch("sys.stderr", new_callable=lambda: __import__("io").StringIO()).start()
        self.be = sb.SdkBackend(str(Path(self.tmp.name) / "state"), "/bin/true",
                                lambda *a, **k: None, log=self.logs.append)

    def session(self, auth=""):
        sid = self.be.spawn("synthetic", "/tmp", auth=auth)
        return sb.SdkSession(self.be, sb.read_reg(self.be.state_dir, sid))

    def forget_file_source(self):
        """A box that NEVER selected the reference from its file: since 2026-09-06 that memory is also on
        disk (keysource.marker_path), written when setUp's backend read the file — a test standing up a
        fresh box must drop it too, or it is testing the (correct) removed-reference refusal instead."""
        Path(ks.marker_path(str(self.path))).unlink(missing_ok=True)

    def test_op_credentials_never_reach_a_session_but_do_reach_op(self):
        for name in ("OP_SERVICE_ACCOUNT_TOKEN", "OP_SESSION_acct"):
            self.assertNotIn(name, os.environ, "claimed at backend init, like the token credentials")
        options = self.be._options(self.session("key"), dict)
        self.assertEqual(options["env"]["ANTHROPIC_API_KEY"], KEY)
        for name in ("OP_SERVICE_ACCOUNT_TOKEN", "OP_SESSION_acct"):
            self.assertNotIn(name, options["env"])
        sub_env = self.provider.call_args.kwargs["env"]
        self.assertEqual(sub_env["OP_SERVICE_ACCOUNT_TOKEN"], "synthetic-op-token")
        self.assertEqual(sub_env["OP_SESSION_acct"], "synthetic-op-session")

    def test_the_ignored_startup_key_is_said_once_with_its_fingerprint_never_its_value(self):
        self.be._work_key_source(); self.be._work_key_source()
        out = self.err.getvalue()
        self.assertEqual(out.count("startup key (sha256:"), 1)
        self.assertIn(ks.fingerprint("synthetic-old-startup-key"), out)
        self.assertNotIn("synthetic-old-startup-key", out)
        self.assertIn("1Password source", out)

    def test_a_supervised_manager_names_the_file_only_rule_when_it_ignores_a_startup_key(self):
        # a FRESH supervised kernel: no memory of a selected source (that memory is the resurrection guard,
        # exercised elsewhere), the manager's inherited key still in the environment, no file line
        self.path.unlink()
        self.forget_file_source()
        os.environ["ROMP_SUPERVISED"] = "1"
        os.environ.pop("ROMP_API_KEY_REF", None)              # an (even empty) reference in the env is a selection
        os.environ["ANTHROPIC_API_KEY"] = "synthetic-old-startup-key"
        ks._CACHE = ((), ks.KeySource("none")); ks._AUTHORITATIVE_PATHS.clear()
        sb._WORK_KEY = None; sb._STARTUP_KEY_DISCARD_SAID = False; self.err.truncate(0); self.err.seek(0)
        src = sb.work_api_key_source()
        self.assertEqual((src.kind, src.value, src.configured), ("file", "", False), "no file line: no key")
        out = self.err.getvalue()
        self.assertIn("supervised managers read", out); self.assertIn("launch on the login", out)
        self.assertIn(ks.fingerprint("synthetic-old-startup-key"), out)

    def test_an_unreadable_file_fails_loudly_but_discards_nothing(self):
        if os.geteuid() == 0:
            self.skipTest("root reads through chmod 0")
        # a fresh kernel whose file is unreadable from the start: the startup key stays claimed
        self.path.write_text("ROMP_API_KEY_REF=" + REF + "\n")
        self.path.chmod(0)
        self.addCleanup(lambda: self.path.chmod(0o600))
        os.environ["ANTHROPIC_API_KEY"] = "synthetic-old-startup-key"
        ks._CACHE = ((), ks.KeySource("none")); ks._AUTHORITATIVE_PATHS.clear(); sb._WORK_KEY = None
        src = sb.work_api_key_source()
        self.assertEqual(src.kind, "error")
        with self.assertRaises(ks.KeySourceError):
            src.resolve()
        self.assertEqual(sb._WORK_KEY, "synthetic-old-startup-key", "a read error is not a selection")
        self.path.chmod(0o600)
        ks._CACHE = ((), ks.KeySource("none"))
        self.assertEqual(sb.work_api_key_source().kind, "op", "…and the file governs again once readable")

    def test_a_cycle_handed_the_requests_fingerprint_retrieves_nothing(self):
        sess = self.session("key")
        self.be.sessions[sess.sid] = sess
        sess._launched_key_fp = ks.fingerprint(KEY)
        self.provider.reset_mock()
        self.assertEqual(self.be.cycle_key(sess.sid, current_key_fp=ks.fingerprint(KEY)), "current")
        with patch.object(sess, "request_reconnect") as reconnect:
            self.assertEqual(self.be.cycle_key(sess.sid, current_key_fp=ks.fingerprint("rotated")), "cycling")
        reconnect.assert_called_once_with(defer=False)
        self.provider.assert_not_called()

    def test_the_standard_supervised_install_gets_no_ignored_key_notice(self):
        """EnvironmentFile exports the file's own key line into the manager environment, so the same key on
        both sides is the everyday case — nothing is ignored, nothing is said (review find, 2026-09-05)."""
        self.path.write_text("ANTHROPIC_API_KEY=synthetic-old-startup-key\n")
        os.environ["ROMP_SUPERVISED"] = "1"; os.environ.pop("ROMP_API_KEY_REF", None)
        os.environ["ANTHROPIC_API_KEY"] = "synthetic-old-startup-key"
        ks._CACHE = ((), ks.KeySource("none")); ks._AUTHORITATIVE_PATHS.clear()
        sb._WORK_KEY = None; sb._STARTUP_KEY_DISCARD_SAID = False; self.err.truncate(0); self.err.seek(0)
        for _ in range(2):
            self.assertEqual(sb.work_api_key_source().kind, "file")
        self.assertNotIn("IGNORED", self.err.getvalue())

    def test_the_notice_names_the_reference_when_the_environment_selected_it(self):
        self.path.unlink()
        self.forget_file_source()
        os.environ["ROMP_API_KEY_REF"] = REF; os.environ["ANTHROPIC_API_KEY"] = "synthetic-old-startup-key"
        ks._CACHE = ((), ks.KeySource("none")); ks._AUTHORITATIVE_PATHS.clear(); ks._ENV_PROVIDER_PATHS.clear()
        sb._WORK_KEY = None; sb._STARTUP_KEY_DISCARD_SAID = False; self.err.truncate(0); self.err.seek(0)
        self.assertEqual(sb.work_api_key_source().kind, "op")
        out = self.err.getvalue()
        self.assertIn("ROMP_API_KEY_REF selects the 1Password source", out)
        self.assertNotIn("env file", out)

    def test_a_forked_login_session_keeps_its_parents_token_override(self):
        parent = self.session("login")
        reg = sb.read_reg(self.be.state_dir, parent.sid)
        reg["env"] = {"CLAUDE_CODE_OAUTH_TOKEN": "synthetic-second-login", "FEATURE_FLAG": "yes"}
        sb.write_reg(self.be.state_dir, parent.sid, reg)
        child = self.be.fork("synthetic-child", parent.sid) if hasattr(self.be, "fork") else None
        if child is None:
            self.skipTest("this backend build has no fork")
        creg = sb.read_reg(self.be.state_dir, child if isinstance(child, str) else child.sid)
        self.assertEqual(creg.get("env"), {"CLAUDE_CODE_OAUTH_TOKEN": "synthetic-second-login", "FEATURE_FLAG": "yes"})
        self.assertEqual(creg.get("auth"), "login")

    def test_a_cycle_probe_classifies_without_retrieving_and_a_request_failure_lands_only_where_a_key_was_needed(self):
        login = self.session("login"); self.be.sessions[login.sid] = login
        keyed = self.session("key"); self.be.sessions[keyed.sid] = keyed
        busy = self.session("key"); self.be.sessions[busy.sid] = busy; busy.inflight = 1
        self.provider.reset_mock()
        self.assertEqual(self.be.cycle_key(login.sid, probe=True), "login")
        self.assertEqual(self.be.cycle_key(busy.sid, probe=True), "working")
        self.assertEqual(self.be.cycle_key("11111111-2222-3333-4444-aaaaaaaaaaa9", probe=True), "unknown")
        self.assertEqual(self.be.cycle_key(keyed.sid, probe=True), "cycle")
        self.provider.assert_not_called()
        self.assertEqual(self.be.cycle_key(login.sid, resolve_error="1Password credential retrieval failed"), "login",
                         "a session that needs no key is not told the retrieval failed")
        with self.assertRaisesRegex(ks.KeySourceError, "retrieval failed"):
            self.be.cycle_key(keyed.sid, resolve_error="1Password credential retrieval failed")
        self.provider.assert_not_called()



class KeySourceHold(unittest.TestCase):
    """An API-key pick on a box that holds NO key source (the user 2026-09-07, whose sessions died at
    launch — "crashed", never relaunched, judges failing every pass — and stayed down until an operator
    rewrote service.env and restarted the service). The session HOLDS instead: with Claude Code's own
    apiKeyHelper present it launches on that (the pre-runtime-source behaviour), else it records the
    remedy on its reg, ends its thread cleanly, and resumes on its own at the next event once a source
    exists. A CONFIGURED source that fails to resolve keeps today's hard failure, said once per streak."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "service.env"
        self.path.write_text("")                      # a supervised box with an EMPTY env file: no source at all
        self.env = patch.dict(os.environ, {
            "ROMP_SERVICE_ENV_FILE": str(self.path), "ROMP_SERVICE_ENV": str(self.path),
            "ROMP_SUPERVISED": "1", "ANTHROPIC_API_KEY": "synthetic-old-startup-key",
            "CLAUDE_CONFIG_DIR": self.tmp.name + "/claude-config",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(patch.stopall)
        os.environ.pop("ROMP_API_KEY_REF", None)      # an (even empty) reference in the env is a selection
        os.environ.pop("ROMP_EXPECTED_AUTH", None)
        patch_keysource_probes()
        patch.object(sb, "_WORK_KEY", None).start()
        patch.object(sb, "_STARTUP_AUTH_ENV", None).start()
        patch.object(sb, "_STARTUP_KEY_DISCARD_SAID", False).start()
        patch.object(sb, "_KEY_FILE_CHECKED", True).start()
        patch.object(ks, "_CACHE", ((), ks.KeySource("none"))).start()
        patch.object(ks, "_AUTHORITATIVE_PATHS", {}).start()
        patch.object(sb, "_FAST_ORG_VERDICTS", {}).start()
        patch.object(sb, "_fetch_key_fast_org", return_value=True).start()
        self.provider = patch.object(ks.subprocess, "run", return_value=op_result()).start()
        fake_sdk = ModuleType("claude_agent_sdk")
        fake_sdk.HookMatcher = lambda **kw: kw
        fake_sdk.ClaudeAgentOptions = dict
        fake_sdk.ClaudeSDKClient = unittest.mock.Mock()
        for name in ("AssistantMessage", "ResultMessage", "SystemMessage"):
            setattr(fake_sdk, name, type(name, (), {}))
        self.client_factory = fake_sdk.ClaudeSDKClient
        patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk}).start()
        patch("sys.stderr", new_callable=lambda: __import__("io").StringIO()).start()
        self.logs = []
        self.be = sb.SdkBackend(str(Path(self.tmp.name) / "state"), "/bin/true",
                                lambda *a, **k: None, log=self.logs.append)
        self.be._sdk_missing = False       # the fake SDK module has no spec; launch_error() must read the reg
        Path(ks.marker_path(str(self.path))).unlink(missing_ok=True)

    def session(self, auth="key", name="synthetic"):
        sid = self.be.spawn(name, "/tmp", auth=auth)
        return sb.SdkSession(self.be, sb.read_reg(self.be.state_dir, sid))

    def configure_source(self):
        """The operator writes the reference line: the exact event that lifts the hold."""
        self.path.write_text("ROMP_API_KEY_REF=" + REF + "\n")
        ks._CACHE = ((), ks.KeySource("none"))
        ks._AUTHORITATIVE_PATHS.clear()

    def problems(self):
        return [p["text"] for p in self.be._problems]

    def test_the_box_is_unconfigured_the_way_the_incident_left_it(self):
        src = self.be._work_key_source()
        self.assertEqual((src.kind, src.configured), ("file", False))
        self.assertFalse(self.be.work_key_configured)

    def test_a_helper_launches_without_an_injected_key_and_says_so_once(self):
        ks.cli_self_auth.return_value = "apiKeyHelper"
        sess = self.session("key")
        opts = self.be._options(sess, dict)
        self.assertNotIn("ANTHROPIC_API_KEY", opts["env"], "the helper IS the key")
        for name in sb.AUTH_ENV_NAMES:
            self.assertNotIn(name, opts["env"], "no competing login token beside the helper")
        self.assertTrue(sess._launched_via_helper)
        self.assertFalse(sess._launched_keyed)
        self.assertEqual(sess._launched_key_fp, "")
        self.provider.assert_not_called()
        notices = [l for l in self.logs if "apiKeyHelper" in l]
        self.assertEqual(len(notices), 1)
        self.assertIn("1 session(s) pick API-key billing but romp holds no key source", notices[0])
        self.assertIn(REMEDY_TEXT, notices[0])
        self.assertIn(notices[0], self.problems(), "a source romp cannot see is a problem row")
        self.be._options(sess, dict)
        self.be._options(self.session("key", name="second"), dict)
        self.assertEqual(sum("apiKeyHelper" in l for l in self.logs), 1, "one row per process")

    def test_a_keyed_landing_on_a_helper_launch_is_the_intended_one(self):
        ks.cli_self_auth.return_value = "apiKeyHelper"
        sess = self.session("key")
        self.be._options(sess, dict)
        self.be._note_auth_source(sess, "ANTHROPIC_API_KEY")
        self.assertFalse([l for l in self.logs if "is billing the" in l], "no false mismatch row")
        self.assertEqual(sess.auth_live, "key")
        self.be._note_auth_source(sess, "none")
        mismatch = [l for l in self.logs if "is billing the login" in l]
        self.assertEqual(len(mismatch), 1, "…and a login landing still rings")
        self.assertIn("launched for the API key", mismatch[0])

    def test_no_helper_holds_the_session_with_the_remedy_and_no_crash_line(self):
        sess = self.session("key")
        with self.assertRaisesRegex(ks.KeySourceError, "^" + __import__("re").escape(REMEDY_TEXT) + "$"):
            self.be._options(sess, dict)
        self.provider.assert_not_called()
        # the whole thread path: _run → _amain → hold → clean exit, the way the kernel runs it
        sess._run()
        rec = sb.read_reg(self.be.state_dir, sess.sid)["launchError"]
        self.assertIs(rec["keysrc"], True)
        self.assertEqual(rec["text"], REMEDY_TEXT, "the card text is the remedy alone")
        self.assertFalse(rec["limit"]); self.assertFalse(rec["dep"])
        self.client_factory.assert_not_called()
        joined = "\n".join(self.logs)
        self.assertNotIn("crashed", joined)
        self.assertNotIn("failed to start", joined)
        self.assertNotIn("Traceback", joined)
        holding = [l for l in self.logs if l.startswith("session synthetic: holding — ")]
        self.assertEqual(len(holding), 1)
        self.assertIn(REMEDY_TEXT, holding[0])
        self.assertIn(holding[0], self.problems())
        self.assertEqual(self.be.launch_error(sess.sid)["text"], REMEDY_TEXT)

    def test_the_card_text_carries_no_class_name_prefix(self):
        self.assertEqual(sb.launch_failure_text(ks.KeySourceError(REMEDY_TEXT)), REMEDY_TEXT)
        self.assertEqual(sb.launch_failure_text(ks.KeySourceError(REMEDY_TEXT), "stale CLI stderr"), REMEDY_TEXT)
        self.assertIn("ValueError", sb.launch_failure_text(ValueError("still framed")), "only key-source errors")

    def test_a_queued_send_stays_parked_through_the_hold(self):
        sess = self.session("key")
        sess.enqueue("hello from before the source existed")
        sess._run()
        reg = sb.read_reg(self.be.state_dir, sess.sid)
        self.assertEqual(reg["queue"], ["hello from before the source existed"])
        self.assertTrue(reg["launchError"]["keysrc"])

    def test_the_hold_lifts_when_the_file_gains_a_reference(self):
        sess = self.session("key")
        sess._run()
        self.assertFalse(self.be._keysrc_hold_lifted(sess.sid), "no source yet: the hold stands")
        self.assertTrue(sb.read_reg(self.be.state_dir, sess.sid)["launchError"]["keysrc"])
        self.configure_source()
        self.assertTrue(self.be.work_key_configured)
        self.assertTrue(self.be._keysrc_hold_lifted(sess.sid))
        self.assertIsNone(sb.read_reg(self.be.state_dir, sess.sid)["launchError"])
        self.assertTrue([l for l in self.logs if "hold is lifted" in l])
        self.assertFalse(self.be._keysrc_hold_lifted(sess.sid), "nothing left to lift")

    def test_the_hold_lifts_when_a_helper_appears(self):
        sess = self.session("key")
        sess._run()
        ks.cli_self_auth.return_value = "apiKeyHelper"
        self.assertTrue(self.be._keysrc_hold_lifted(sb.read_reg(self.be.state_dir, sess.sid)))
        self.assertIsNone(sb.read_reg(self.be.state_dir, sess.sid)["launchError"])

    def test_other_launch_errors_are_not_lifted_by_a_source(self):
        sess = self.session("key")
        self.be._record_launch_error(sess, RuntimeError("the CLI binary is gone"))
        self.configure_source()
        self.assertFalse(self.be._keysrc_hold_lifted(sess.sid))
        self.assertNotIn("keysrc", sb.read_reg(self.be.state_dir, sess.sid)["launchError"])

    def test_a_configured_source_that_failed_lifts_only_on_new_information(self):
        """A CONFIGURED source whose `op read` failed holds the session too — and the next drive tick
        is not a reason to run `op` again (two attempts, up to 30 s, per held session, against a store
        that is down). The hold lifts on exactly two events: the source's fingerprint moved (a keyswap,
        a rewritten line) or a resolve succeeded AFTER the hold (any consumer's, read off
        keysource.health); nothing else."""
        self.configure_source()
        self.provider.side_effect = ks.subprocess.TimeoutExpired(cmd="op", timeout=15)
        sess = self.session("key")
        with patch.object(ks, "_SLEEP", lambda s: None, create=True):
            sess._run()
        rec = sb.read_reg(self.be.state_dir, sess.sid)["launchError"]
        self.assertIs(rec["keysrc"], True)
        self.assertEqual(rec["sourceFp"], ks.KeySource("op", REF).fingerprint(),
                         "the hold names the source it failed against")
        self.assertIn("timed out", rec["text"])
        self.assertTrue(self.be.work_key_configured)
        self.assertFalse(self.be._keysrc_hold_lifted(sess.sid),
                         "same source, no success since the hold: a tick is not new information")
        self.assertTrue(sb.read_reg(self.be.state_dir, sess.sid)["launchError"]["keysrc"])
        # keysource.health() is the backend's only clock for "a resolve succeeded since": stand in for it
        # (this class already stubs health() with fixed synthetic times, so the stub is what moves)
        with patch.object(ks, "health", return_value={**ks.health(), "lastOkT": rec["at"] - 1}):
            self.assertFalse(self.be._keysrc_hold_lifted(sess.sid), "a success BEFORE the hold is not new either")
        with patch.object(ks, "health", return_value={**ks.health(), "lastOkT": rec["at"] + 5}):
            self.assertTrue(self.be._keysrc_hold_lifted(sess.sid), "a resolve that succeeded after the hold lifts it")
        self.assertIsNone(sb.read_reg(self.be.state_dir, sess.sid)["launchError"])
        self.assertTrue([l for l in self.logs if "resolved again — the hold is lifted" in l])
        # the other event: the source itself moved (a keyswap, a rewritten reference line)
        other = self.session("key", name="second")
        with patch.object(ks, "_SLEEP", lambda s: None, create=True):
            other._run()
        self.assertTrue(sb.read_reg(self.be.state_dir, other.sid)["launchError"]["keysrc"])
        self.assertFalse(self.be._keysrc_hold_lifted(other.sid))
        self.path.write_text("ROMP_API_KEY_REF=op://test-vault/other-item/credential\n")
        ks._CACHE = ((), ks.KeySource("none"))
        ks._AUTHORITATIVE_PATHS.clear()
        self.assertTrue(self.be._keysrc_hold_lifted(other.sid), "a changed source is new information")
        self.assertIsNone(sb.read_reg(self.be.state_dir, other.sid)["launchError"])
        self.assertTrue([l for l in self.logs if "configured now — the hold is lifted" in l])

    def _drive(self, sess):
        cands = [{"sid": sess.sid, "path": "/tmp/TESTHOST.jsonl", "mark": (3, "2026-09-07T00:00:03"),
                  "entries": [{"wrapper": True, "text": "wake", "pos": 3, "ts": "2026-09-07T00:00:03"}]}]
        with patch.object(self.be, "_drive_deliver") as deliver:
            self.be.drive_idle_queue(cands, wait=True)
        return deliver

    def test_the_idle_queue_drive_stands_down_on_the_hold_and_proceeds_once_it_lifts(self):
        sess = self.session("key")
        sess._run()
        self.assertFalse(self._drive(sess).called, "a held session cannot start: stand down")
        self.configure_source()
        deliver = self._drive(sess)
        self.assertTrue(deliver.called, "the source appeared: the hold clears and the drive proceeds")
        self.assertEqual(deliver.call_args.args[0][0][0], sess.sid)
        self.assertIsNone(sb.read_reg(self.be.state_dir, sess.sid)["launchError"])

    def test_the_drive_lifts_a_hold_recorded_on_a_reg_file_that_omits_its_sid(self):
        """read_reg returns the raw file, and a raw reg may carry no `sid`: the drive hands the helper the
        sid it already knows, so the clear lands on the right record instead of on ""."""
        sess = self.session("key")
        sess._run()
        reg = sb.read_reg(self.be.state_dir, sess.sid)
        reg.pop("sid", None)
        sb.write_reg(self.be.state_dir, sess.sid, reg)
        self.configure_source()
        self.assertTrue(self._drive(sess).called)
        self.assertIsNone(sb.read_reg(self.be.state_dir, sess.sid)["launchError"])
        self.assertFalse(self.be._keysrc_hold_lifted({"launchError": {"keysrc": True}}), "a sid-less dict lifts nothing")

    def test_boot_preflight_names_the_held_sessions_once_and_skips_their_revive(self):
        keyed = self.session("key", name="web")
        keyed.enqueue("a reply typed before the restart")
        keyed._run()                                   # held, with a persisted queue
        login = self.session("login", name="tests")
        regs = [r for r in sb.list_regs(self.be.state_dir) if r.get("alive")]
        with patch.object(self.be, "_ensure") as ensure:
            self.be._boot_reconcile(regs)
        ensure.assert_not_called()
        rows = [l for l in self.logs if "are set to API-key billing but no API key source is configured" in l]
        self.assertEqual(len(rows), 1)
        self.assertIn("1 session(s) [web]", rows[0])
        self.assertNotIn("tests", rows[0], "a login session is not waiting on the key")
        self.assertIn("they will hold until it is. " + REMEDY_TEXT, rows[0])
        self.assertIn(rows[0], self.problems())
        self.assertEqual(sum("holding — " in l for l in self.logs), 1, "the boot adds no per-session holding line")
        # the source appears → the next boot lifts the hold and resumes the queued session
        self.configure_source()
        with patch.object(self.be, "_ensure") as ensure:
            self.be._boot_reconcile([r for r in sb.list_regs(self.be.state_dir) if r.get("alive")])
        self.assertEqual([c.args[0] for c in ensure.call_args_list], [keyed.sid])
        self.assertIsNone(sb.read_reg(self.be.state_dir, keyed.sid)["launchError"])
        self.assertEqual(sum("are set to API-key billing" in l for l in self.logs), 1, "quiet once configured")

    def test_boot_preflight_is_quiet_with_a_helper_or_no_keyed_session(self):
        self.session("login", name="tests")
        self.be._boot_reconcile([r for r in sb.list_regs(self.be.state_dir) if r.get("alive")])
        self.assertFalse([l for l in self.logs if "set to API-key billing" in l])
        self.session("key", name="web")
        ks.cli_self_auth.return_value = "apiKeyHelper"
        self.be._boot_reconcile([r for r in sb.list_regs(self.be.state_dir) if r.get("alive")])
        self.assertFalse([l for l in self.logs if "set to API-key billing" in l])

    def test_a_configured_source_that_fails_still_raises_and_says_the_streak_once(self):
        self.configure_source()
        self.provider.return_value = op_result(1, b"provider-output-must-not-leak", b"op: stderr-must-not-leak")
        sess = self.session("key")
        for _ in range(3):
            with self.assertRaisesRegex(ks.KeySourceError, "1Password credential retrieval failed"):
                self.be._options(sess, dict)
        self.assertEqual(self.provider.call_count, 3 * ks.OP_ATTEMPTS,
                         "the backend adds no retry of its own: every launch asks keysource afresh")
        failing = [l for l in self.logs if l.startswith("API key source: retrieval failing since ")]
        self.assertEqual(len(failing), 1, "one line per streak, never per call")
        self.assertIn(HEALTH["note"] + "; sessions and judges hold", failing[0])
        self.assertIn(failing[0], self.problems())
        self.assertNotIn("provider-output", "\n".join(self.logs))
        self.assertNotIn("stderr-must-not-leak", "\n".join(self.logs))
        # the hold record for a configured-source failure is a key-source hold too: it lifts on the event
        sess._run()
        self.assertTrue(sb.read_reg(self.be.state_dir, sess.sid)["launchError"]["keysrc"])
        self.assertNotIn("crashed", "\n".join(self.logs))
        # recovery: one plain line, not a problem
        self.provider.return_value = op_result()
        opts = self.be._options(sess, dict)
        self.assertEqual(opts["env"]["ANTHROPIC_API_KEY"], KEY)
        self.be._options(sess, dict)
        recovered = [l for l in self.logs if l == "API key source: retrieval recovered"]
        self.assertEqual(len(recovered), 1)
        self.assertNotIn(recovered[0], self.problems())
        self.assertNotIn(KEY, "\n".join(self.logs))

    # ---- the 2026-09-07 review folds: what lifts a hold, and what re-checks one ----

    def hold(self, name="synthetic", queue=None):
        """A session held against the CONFIGURED op source (the store is down); returns it with its record."""
        sess = self.session("key", name=name)
        if queue:
            sess.enqueue(queue)
        sess._run()
        rec = sb.read_reg(self.be.state_dir, sess.sid)["launchError"]
        self.assertIs(rec["keysrc"], True)
        return sess, rec

    def real_health(self):
        """keysource.health() as the module really keeps it — the class stubs it with fixed times, and
        these tests need to see a probe STAMP the clock (the stub is replaced, never _HEALTH written)."""
        return patch.object(ks, "health", side_effect=lambda: dict(ks._HEALTH))

    def ensure_stub(self, be=None):
        """_ensure stood in for: records the sids it was asked to revive and fires the boot-stagger slot
        release the real one would, so a lift never leaves the shared semaphore a slot short."""
        def fake(sid, on_boot_settled=None):
            if on_boot_settled:
                on_boot_settled()
            return None
        return patch.object(be or self.be, "_ensure", side_effect=fake)

    def test_a_helper_never_lifts_a_hold_against_a_configured_source_that_is_failing(self):
        """A helper box with a CONFIGURED op source whose `op read` fails: the launch decision runs the
        configured branch (a helper is consulted only when NO source exists), so the lift must too — the
        helper check running first lifted this hold with a false reason and relaunched into the same
        two-attempt, ~31 s failure on every event (review find, 2026-09-07)."""
        self.configure_source()
        ks.cli_self_auth.return_value = "apiKeyHelper"
        self.provider.side_effect = ks.subprocess.TimeoutExpired(cmd="op", timeout=15)
        sess, rec = self.hold()
        calls = self.provider.call_count
        self.assertFalse(self.be._keysrc_hold_lifted(sess.sid), "a bare tick with a helper present lifts nothing")
        self.assertFalse(self.be.keysrc_hold_sweep())
        self.assertEqual(sb.read_reg(self.be.state_dir, sess.sid)["launchError"], rec, "the record survives intact")
        self.assertEqual(self.provider.call_count, calls, "the check never runs op")
        self.assertFalse([l for l in self.logs if "hold is lifted" in l])
        # the configured branch's own events still lift it — a success after the hold…
        with patch.object(ks, "health", return_value={**ks.health(), "lastOkT": rec["atT"] + 5}):
            self.assertTrue(self.be._keysrc_hold_lifted(sess.sid))
        self.assertTrue([l for l in self.logs if "resolved again — the hold is lifted" in l])
        # …and with NO source configured the helper remains the event (the pre-existing rule)
        other = sb.SdkBackend(str(Path(self.tmp.name) / "state2"), "/bin/true", lambda *a, **k: None, log=self.logs.append)
        other._sdk_missing = False
        self.path.write_text("")
        ks._CACHE = ((), ks.KeySource("none")); ks._AUTHORITATIVE_PATHS.clear()
        Path(ks.marker_path(str(self.path))).unlink(missing_ok=True)
        ks.cli_self_auth.return_value = ""
        sid = other.spawn("bare", "/tmp", auth="key")
        sb.SdkSession(other, sb.read_reg(other.state_dir, sid))._run()
        self.assertTrue(sb.read_reg(other.state_dir, sid)["launchError"]["keysrc"])
        ks.cli_self_auth.return_value = "apiKeyHelper"
        self.assertTrue(other._keysrc_hold_lifted(sid))

    def test_an_unchanged_unreadable_file_does_not_lift_the_hold_it_caused(self):
        """The `error` kind (a garbled reference line) has no fingerprint, and an EMPTY recorded identity
        read as "no source existed at the hold" — so every event lifted the hold as "configured now"
        and relaunched into the same unreadable file. The record carries a stable non-empty identity
        (`kind:error`) instead, compared against the same expression (review find, 2026-09-07)."""
        self.configure_source()
        self.path.write_bytes(b"ROMP_API_KEY_REF=op://test-vault/\xff\xfe/credential\n")
        ks._CACHE = ((), ks.KeySource("none")); ks._AUTHORITATIVE_PATHS.clear()
        src = self.be._work_key_source()
        self.assertEqual((src.kind, src.configured, src.fingerprint()), ("error", True, ""))
        sess, rec = self.hold()
        self.assertEqual(rec["sourceFp"], "kind:error", "a non-empty identity, never the empty fingerprint")
        self.assertIn("not valid UTF-8", rec["text"])
        self.provider.assert_not_called()
        self.assertFalse(self.be._keysrc_hold_lifted(sess.sid), "the same unreadable file is not new information")
        self.assertEqual(self.be.keysrc_hold_sweep(), 0)
        self.assertTrue(sb.read_reg(self.be.state_dir, sess.sid)["launchError"]["keysrc"])
        # the operator repairs the line: the identity moves, the hold lifts
        self.configure_source()
        self.assertTrue(self.be._keysrc_hold_lifted(sess.sid))
        self.assertTrue([l for l in self.logs if "configured now — the hold is lifted" in l])

    def test_the_hold_time_is_kept_to_the_fraction_of_a_second(self):
        """`at` is int seconds for the card; comparing health's float lastOkT against it made a success
        that happened BEFORE the hold, in the hold's own second, read as after it. The record carries
        the float twin `atT`, and the lift compares against that (review find, 2026-09-07)."""
        self.configure_source()
        self.provider.side_effect = ks.subprocess.TimeoutExpired(cmd="op", timeout=15)
        sess, rec = self.hold()
        self.assertIsInstance(rec["atT"], float)
        self.assertEqual(int(rec["atT"]), rec["at"])
        with patch.object(ks, "health", return_value={**ks.health(), "lastOkT": rec["atT"] - 0.01}):
            self.assertFalse(self.be._keysrc_hold_lifted(sess.sid), "a success just before the hold is not new")
        with patch.object(ks, "health", return_value={**ks.health(), "lastOkT": rec["atT"] + 0.01}):
            self.assertTrue(self.be._keysrc_hold_lifted(sess.sid), "…one just after it is")
        # a record from before atT existed still lifts on its int second
        other, rec2 = self.hold(name="older")
        rec2.pop("atT")
        self.be._update_reg(other.sid, launchError=rec2)
        with patch.object(ks, "health", return_value={**ks.health(), "lastOkT": rec2["at"] + 1}):
            self.assertTrue(self.be._keysrc_hold_lifted(other.sid))

    def test_boot_probes_the_current_source_once_and_revives_what_it_proves(self):
        """A kernel restart forgets keysource.health(), so a hold recorded against the CURRENT configured
        source could never see a success after itself and its session was skipped at every boot until
        some other consumer happened to resolve (review find, 2026-09-07). Boot runs ONE resolve when a
        held session waits on the current identity — single flight, however many are held — so the sweep
        that follows sees a fresh clock; a failing source keeps them held and says the streak once."""
        self.configure_source()
        self.provider.side_effect = ks.subprocess.TimeoutExpired(cmd="op", timeout=15)
        web, rec_web = self.hold(name="web", queue="a reply typed before the restart")
        api, rec_api = self.hold(name="api")
        ident = ks.KeySource("op", REF).fingerprint()
        self.assertEqual((rec_web["sourceFp"], rec_api["sourceFp"]), (ident, ident))
        state = str(Path(self.tmp.name) / "state")
        alive = lambda: [r for r in sb.list_regs(self.be.state_dir) if r.get("alive")]
        # the store is still down at the restart: held, one resolve, one transition row, nothing revived
        calls = self.provider.call_count
        logs2: list = []
        be2 = sb.SdkBackend(state, "/bin/true", lambda *a, **k: None, log=logs2.append)
        be2._sdk_missing = False
        self.assertEqual(be2._keysrc_held, {web.sid, api.sid}, "the new kernel watches the old holds from boot")
        with self.real_health(), self.ensure_stub(be2) as ensure:
            be2._boot_reconcile(alive())
        ensure.assert_not_called()
        self.assertEqual(self.provider.call_count - calls, ks.OP_ATTEMPTS, "ONE resolve for two held sessions")
        self.assertEqual(sum(l.startswith("API key source: retrieval failing since ") for l in logs2), 1)
        self.assertFalse([l for l in logs2 if "hold is lifted" in l])
        for s in (web, api):
            self.assertTrue(sb.read_reg(self.be.state_dir, s.sid)["launchError"]["keysrc"])
        # the store is back at the next restart: one resolve stamps the clock, the sweep revives the queue
        self.provider.side_effect = None
        self.provider.return_value = op_result()
        calls = self.provider.call_count
        logs3: list = []
        be3 = sb.SdkBackend(state, "/bin/true", lambda *a, **k: None, log=logs3.append)
        be3._sdk_missing = False
        with self.real_health(), self.ensure_stub(be3) as ensure:
            be3._boot_reconcile(alive())
        self.assertEqual(self.provider.call_count - calls, 1, "one op read, shared by both holds")
        self.assertEqual([c.args[0] for c in ensure.call_args_list], [web.sid],
                         "the queued session resumes; the idle one is lifted but stays lazy, as any boot leaves it")
        for s in (web, api):
            self.assertIsNone(sb.read_reg(self.be.state_dir, s.sid)["launchError"])
        self.assertEqual(sb.read_reg(self.be.state_dir, web.sid)["queue"], ["a reply typed before the restart"])
        self.assertEqual(sum("resolved again — the hold is lifted" in l for l in logs3), 2)
        self.assertEqual(be3._keysrc_held, set())
        self.assertNotIn(KEY, "\n".join(logs2 + logs3))
        # nothing held against the current identity → no probe at all
        calls = self.provider.call_count
        be4 = sb.SdkBackend(state, "/bin/true", lambda *a, **k: None, log=self.logs.append)
        with self.ensure_stub(be4):
            be4._boot_reconcile(alive())
        self.assertEqual(self.provider.call_count, calls, "no hold waits on this source: op is not asked")

    def test_the_per_cycle_sweep_is_free_when_nothing_is_held_and_lifts_a_hold_the_operator_fixed(self):
        """Nothing re-checked a hold when the operator wrote the reference at runtime: only a drive tick,
        a boot or a send did, so a held session with nothing queued stayed held on a box long since
        fixed (review find, 2026-09-07). keysrc_hold_sweep is the kernel's per-cycle job — O(1) when no
        session is held (no reg read at all), one reg read per held session otherwise, never `op`."""
        self.assertEqual(self.be._keysrc_held, set())
        with patch.object(sb, "read_reg") as rr, patch.object(sb, "list_regs") as lr:
            self.assertEqual(self.be.keysrc_hold_sweep(), 0)
        rr.assert_not_called(); lr.assert_not_called()
        sess = self.session("key", name="web")
        sess._run()                                       # held: no source at all
        self.assertEqual(self.be._keysrc_held, {sess.sid})
        with self.ensure_stub() as ensure:
            self.assertEqual(self.be.keysrc_hold_sweep(), 0, "no source yet: the hold stands")
        ensure.assert_not_called()
        self.assertEqual(self.be._keysrc_held, {sess.sid}, "a standing hold stays watched")
        self.configure_source()                           # the operator writes the reference line
        with self.ensure_stub() as ensure:
            self.assertEqual(self.be.keysrc_hold_sweep(), 1)
        self.assertEqual([c.args[0] for c in ensure.call_args_list], [sess.sid])
        self.assertIsNone(sb.read_reg(self.be.state_dir, sess.sid)["launchError"])
        self.assertEqual(self.be._keysrc_held, set(), "pruned with the record")
        self.provider.assert_not_called()
        self.assertTrue([l for l in self.logs if "session web: an API key source is configured now — the hold is lifted" in l])
        with patch.object(sb, "read_reg") as rr:
            self.assertEqual(self.be.keysrc_hold_sweep(), 0)
        rr.assert_not_called()
        # a real failure replacing a hold leaves the watched set: its own release rules apply
        held = self.session("key", name="api")
        self.be._record_launch_error(held, ks.KeySourceError(REMEDY_TEXT))
        self.assertIn(held.sid, self.be._keysrc_held)
        self.be._record_launch_error(held, RuntimeError("the CLI binary is gone"))
        self.assertNotIn(held.sid, self.be._keysrc_held)
        self.assertEqual(self.be.keysrc_hold_sweep(), 0)

    def test_the_sweep_respects_the_spawn_stagger_and_keeps_the_hold_until_a_slot_frees(self):
        sess = self.session("key")
        sess._run()
        self.configure_source()
        slots = [self.be._spawn_sem.acquire(timeout=0) for _ in range(sb.BOOT_RESUME_CONCURRENCY)]
        self.assertTrue(all(slots))
        with self.ensure_stub() as ensure:
            self.assertEqual(self.be.keysrc_hold_sweep(), 0, "the stagger is full: nothing spawns")
        ensure.assert_not_called()
        self.assertTrue(sb.read_reg(self.be.state_dir, sess.sid)["launchError"]["keysrc"], "…and the hold stands, unread")
        self.assertIn(sess.sid, self.be._keysrc_held)
        self.be._spawn_sem.release()
        with self.ensure_stub() as ensure:
            self.assertEqual(self.be.keysrc_hold_sweep(), 1)
        ensure.assert_called_once()
        for _ in slots[1:]:
            self.be._spawn_sem.release()

    def test_keysource_status_reports_the_selection_and_the_holds_without_running_op(self):
        sess = self.session("key", name="web")
        sess._run()
        self.session("login", name="tests")
        st = self.be.keysource_status()
        self.assertEqual((st["configured"], st["kind"], st["sourceFp"], st["held"]), (False, "file", "", ["web"]))
        self.assertEqual(st["health"], HEALTH)
        self.configure_source()
        st = self.be.keysource_status()
        self.assertEqual((st["configured"], st["kind"], st["sourceFp"]),
                         (True, "op", ks.KeySource("op", REF).fingerprint()))
        self.assertEqual(st["held"], ["web"], "status reads; it lifts nothing")
        self.provider.assert_not_called()
        self.assertNotIn(REF, json.dumps(st)); self.assertNotIn(KEY, json.dumps(st))
        self.assertEqual(set(st), {"configured", "kind", "sourceFp", "held", "health"})

    def test_the_probe_resolves_once_and_releases_the_holds_it_proves(self):
        """The operator's verification gesture: when the held sessions are the ONLY key consumers nothing
        else ever moves health's clock, so the probe resolves once (shared by every hold) and sweeps."""
        self.configure_source()
        self.provider.side_effect = ks.subprocess.TimeoutExpired(cmd="op", timeout=15)
        web, rec = self.hold(name="web")
        api, _ = self.hold(name="api")
        calls = self.provider.call_count
        with self.real_health(), self.ensure_stub() as ensure:
            out = self.be.keysource_probe()
        self.assertEqual((out["ok"], out["lifted"], out["kind"]), (False, [], "op"))
        self.assertEqual(out["note"], rec["text"], "the fixed sentence the hold itself carries")
        self.assertIn("timed out", out["note"])
        self.assertEqual(out["sourceFp"], ks.KeySource("op", REF).fingerprint())
        self.assertEqual(self.provider.call_count - calls, ks.OP_ATTEMPTS, "one resolve for the whole board")
        ensure.assert_not_called()
        for s in (web, api):
            self.assertTrue(sb.read_reg(self.be.state_dir, s.sid)["launchError"]["keysrc"])
        self.provider.side_effect = None
        self.provider.return_value = op_result()
        calls = self.provider.call_count
        with self.real_health(), self.ensure_stub() as ensure:
            out = self.be.keysource_probe()
        self.assertEqual((out["ok"], sorted(out["lifted"])), (True, ["api", "web"]))
        self.assertEqual(self.provider.call_count - calls, 1)
        self.assertEqual(sorted(c.args[0] for c in ensure.call_args_list), sorted([web.sid, api.sid]))
        for s in (web, api):
            self.assertIsNone(sb.read_reg(self.be.state_dir, s.sid)["launchError"])
        self.assertEqual(self.be._keysrc_held, set())
        self.assertNotIn(KEY, json.dumps(out)); self.assertNotIn(REF, json.dumps(out))
        self.assertEqual(set(out), {"ok", "note", "lifted", "kind", "sourceFp"})
        # no source at all: the remedy is the note, and a helper appearing is what the sweep honours
        self.path.write_text("")
        ks._CACHE = ((), ks.KeySource("none")); ks._AUTHORITATIVE_PATHS.clear()
        Path(ks.marker_path(str(self.path))).unlink(missing_ok=True)
        bare = self.session("key", name="bare")
        bare._run()
        out = self.be.keysource_probe()
        self.assertEqual((out["ok"], out["note"], out["lifted"]), (False, REMEDY_TEXT, []))

    def test_the_helper_row_counts_only_the_sessions_that_launch_on_it(self):
        """With no source configured an UNPICKED session on a declared-key box launches login-side, so it
        is not riding the helper: the row counts explicit picks only (review find, 2026-09-07)."""
        os.environ["ROMP_EXPECTED_AUTH"] = "key"
        ks.cli_self_auth.return_value = "apiKeyHelper"
        picked = self.session("key", name="web")
        self.session("", name="api")                      # unpicked: declared key, launches login-side
        self.assertEqual(sorted(self.be._keyed_reg_names()), ["api", "web"], "the declaration counts both…")
        self.be._options(picked, dict)
        rows = [l for l in self.logs if "apiKeyHelper" in l]
        self.assertEqual(len(rows), 1)
        self.assertIn("1 session(s) pick API-key billing", rows[0], "…the helper row counts the pick alone")

    def test_a_hold_that_ends_a_billing_switch_clears_its_pending_dots(self):
        """set_auth('key') marks authPending until the applying reconnect lands; a key-source HOLD ends
        that reconnect's thread instead, and left set the badge wore switching-dots for the whole hold
        (review find, 2026-09-07). The thread's exit clears it, and the boot sweep heals a dormant one."""
        self.configure_source()
        self.provider.side_effect = ks.subprocess.TimeoutExpired(cmd="op", timeout=15)
        sess = self.session("key")
        self.be._update_reg(sess.sid, authPending=True)   # the switch's mark, placed after construction
        sess._auth_pending = "key"
        sess._run()
        reg = sb.read_reg(self.be.state_dir, sess.sid)
        self.assertTrue(reg["launchError"]["keysrc"])
        self.assertIs(reg["authPending"], False)
        self.assertEqual(sess._auth_pending, "")
        self.assertEqual(reg["auth"], "key", "the pick itself persists; only the dots clear")
        # a dormant session's stranded flag heals at boot like effort/model do
        self.be._update_reg(sess.sid, authPending=True)
        with self.ensure_stub():
            self.be._boot_reconcile([r for r in sb.list_regs(self.be.state_dir) if r.get("alive")])
        self.assertIs(sb.read_reg(self.be.state_dir, sess.sid)["authPending"], False)

    def test_an_explicit_cycle_never_re_presents_a_memoized_key(self):
        """keysource hands a value resolved within ROMP_OP_REUSE_S out again without a read; a keyswap
        --cycle says the world moved (a rotated item), so cycle_key drops that memory before it resolves
        — otherwise the cycle re-presented the very key the operator was replacing (review find)."""
        self.configure_source()
        os.environ["ROMP_OP_REUSE_S"] = "60"              # the production window (tests floor it to 0)
        self.assertEqual(self.be._work_key_and_source()[0], KEY)
        self.assertEqual(self.be._work_key_and_source()[0], KEY)
        self.assertEqual(self.provider.call_count, 1, "the window is live: the second resolve reused")
        sess = self.session("key")
        self.be.sessions[sess.sid] = sess
        rotated = "synthetic-rotated-credential"
        self.provider.return_value = op_result(stdout=rotated.encode())
        with patch.object(sess, "request_reconnect") as reconnect:
            self.assertEqual(self.be.cycle_key(sess.sid), "cycling")
        reconnect.assert_called_once_with(defer=False)
        self.assertEqual(self.provider.call_count, 2, "the cycle forgot the memo and read afresh")
        self.assertIn("(sha256:%s)" % ks.fingerprint(rotated), "\n".join(self.logs))
        self.assertNotIn(rotated, "\n".join(self.logs))
        # the request-level path (a fingerprint resolved once for many sessions) retrieves nothing here
        with patch.object(sess, "request_reconnect"):
            self.be.cycle_key(sess.sid, current_key_fp=ks.fingerprint(rotated))
        self.assertEqual(self.provider.call_count, 2)
