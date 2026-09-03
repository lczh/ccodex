#!/usr/bin/env python3
"""The judge engine switch (STATE/judge-engine, kernel/judge.py): "codex" runs every judge as a
one-shot `codex exec` instead of `claude -p`, so a machine with no Claude login keeps the board
thinking. Pins: the codex argv's isolation flags, the stdin prompt concatenation, the reply coming
off the -o file, model/effort mapping (claude aliases never sent to codex; plan accounts refuse
them), the Claude-account rate gate NOT gating codex calls, and the usage row's honest shape
(bracket + engine, no faked tokens). A stub binary stands in for codex; nothing hits a network.

Run:    python3 tests/test_judge_engine.py
"""
import json
import os
import io
import contextlib
import stat
import tempfile
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
jd = SourceFileLoader("romp_judge_engine_t", os.path.join(ROOT, "bin", "romp-judge")).load_module()

STUB = r"""#!/usr/bin/env bash
# stub codex: record argv + stdin, honor -o, reply with canned JSON
rec="$STUB_RECORD"
printf '%s\n' "$@" > "$rec/argv"
cat > "$rec/stdin"
out=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then out="$a"; fi
  prev="$a"
done
[ -n "$out" ] && printf '{"caption":"stub-reply"}' > "$out"
exit 0
"""


class JudgeEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(self.tmp)
        jd._state_cache.clear()
        self.rec = self.tmp / "rec"
        self.rec.mkdir()
        stub = self.tmp / "codex-stub"
        stub.write_text(STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        os.environ["ROMP_CODEX_BIN"] = str(stub)
        os.environ["STUB_RECORD"] = str(self.rec)
        # the judge folds the machine's standing user notes into prose judges' prompts (_with_user_notes
        # reads a file OUTSIDE the rebound state dir) — isolate, or a developer's real notes land in the
        # stdin pin and the byte-exact prompt assertion fails on their box while CI stays green
        from unittest import mock
        self._notes = mock.patch.object(jd, "_user_notes", return_value="")
        self._notes.start()

    def tearDown(self):
        self._notes.stop()
        os.environ.pop("ROMP_CODEX_BIN", None)
        os.environ.pop("STUB_RECORD", None)

    def _engine(self, name):
        p = self.tmp / "judge-engine"
        p.write_text(name)
        os.utime(p, (time.time() + 60, time.time() + 60))   # defeat same-second _state_cache reuse
        jd._state_cache.clear()

    def _argv(self):
        return (self.rec / "argv").read_text().splitlines()

    def test_codex_engine_runs_the_stub_and_returns_its_reply(self):
        self._engine("codex")
        out = jd._judge_run("sonnet", "SYS PROMPT", "USER TEXT", judge="captioner", tier="index")
        self.assertEqual(out, '{"caption":"stub-reply"}')
        argv = self._argv()
        for flag in ("exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                     "--strict-config", "--skip-git-repo-check",
                     'default_permissions="ccodex_judge"', "--color"):
            self.assertIn(flag, argv)
        profile = next((x for x in argv if x.startswith("permissions.ccodex_judge=")), "")
        self.assertIn('":minimal" = "read"', profile)
        self.assertIn('":workspace_roots" = { "." = "read"', profile)
        self.assertIn("network = { enabled = false }", profile)
        self.assertNotIn('":root"', profile, "judge profile must not restore host-wide reads")
        self.assertNotIn("-s", argv, "legacy read-only permits host-wide reads")
        self.assertNotIn("-m", argv, "a claude alias must never be sent to codex (plan accounts 400)")
        self.assertIn("model_reasoning_effort=low", argv, "index tier defaults to low effort")
        self.assertEqual((self.rec / "stdin").read_text(), "SYS PROMPT\n\nUSER TEXT")

    def test_successful_codex_call_clears_the_sessions_auth_latch(self):
        self._engine("codex")
        sid = "11111111-2222-3333-4444-555555555555"
        jd._judge_ctx.fsid = sid
        jd._auth_down_mark(sid, "login", "Not logged in")
        self.assertIn(sid, jd._auth_down_map())
        try:
            self.assertTrue(jd._judge_run("sonnet", "S", "U", judge="planner", tier="triage"))
        finally:
            del jd._judge_ctx.fsid
        self.assertNotIn(sid, jd._auth_down_map())

    def test_gpt_override_and_triage_default_effort(self):
        self._engine("codex")
        jd._judge_run("gpt-5.6-sol", "S", "U", judge="planner", tier="triage")
        argv = self._argv()
        self.assertIn("-m", argv)
        self.assertIn("gpt-5.6-sol", argv)
        self.assertFalse(any(a.startswith("model_reasoning_effort=") for a in argv),
                         "triage keeps the model's default reasoning unless an effort is set")

    def test_usage_row_is_honest(self):
        self._engine("codex")
        jd._judge_run("sonnet", "S", "U", judge="captioner", tier="index")
        rows = [json.loads(l) for l in (self.tmp / "judge-usage.jsonl").read_text().splitlines()]
        self.assertEqual(rows[-1]["model"], "codex-default")
        self.assertEqual(rows[-1]["judge"], "captioner")
        self.assertIsNone(rows[-1]["in"], "codex exec reports no tokens — absent, not faked")
        self.assertIsNone(rows[-1]["cost"])
        self.assertIsInstance(rows[-1]["ms"], int)

    def test_claude_rate_gate_does_not_gate_codex(self):
        (self.tmp / "usage.json").write_text(json.dumps(
            {"five_hour": {"pct": 100, "resets_at": time.time() + 3600}}))
        self._engine("codex")
        out = jd._judge_run("sonnet", "S", "U", judge="captioner", tier="index")
        self.assertEqual(out, '{"caption":"stub-reply"}',
                         "the CLAUDE account's exhausted window must not gate codex-engine calls")
        # and the same limited window DOES gate the claude engine, before any exec
        self._engine("claude")
        (self.rec / "argv").unlink()
        out = jd._judge_run("sonnet", "S", "U", judge="captioner", tier="index")
        self.assertEqual(out, "")
        self.assertTrue(getattr(jd._judge_ctx, "paused", False))
        self.assertFalse((self.rec / "argv").exists(), "gated call must not exec anything")

    def test_empty_reply_logs_a_call_error(self):
        self._engine("codex")
        # a stub that writes NO -o file (a refused/dead call)
        dead = self.tmp / "codex-dead"
        dead.write_text("#!/usr/bin/env bash\ncat >/dev/null\nexit 1\n")
        dead.chmod(dead.stat().st_mode | stat.S_IEXEC)
        os.environ["ROMP_CODEX_BIN"] = str(dead)
        out = jd._judge_run("sonnet", "S", "U", judge="captioner", tier="index")
        self.assertEqual(out, "")
        rows = [json.loads(l) for l in (self.tmp / "judge-errors.jsonl").read_text().splitlines()]
        self.assertEqual(rows[-1]["err"], "call")
        self.assertIn("codex empty reply", rows[-1]["note"])

    def test_effort_mapping(self):
        self.assertEqual(jd._codex_effort(None, "index"), "low")
        self.assertIsNone(jd._codex_effort(None, "triage"))
        self.assertEqual(jd._codex_effort("minimal", "triage"), "low")
        self.assertEqual(jd._codex_effort("max", "triage"), "xhigh")
        self.assertEqual(jd._codex_effort("xhigh", "index"), "xhigh")
        self.assertIsNone(jd._codex_effort("ultracode", "triage"))

    def test_default_engine_is_claude_and_cmd_unchanged(self):
        jd._state_cache.clear()
        self.assertEqual(jd._judge_engine(), "claude")
        cmd = jd._judge_cmd("sonnet", "S", None)
        self.assertIn("--safe-mode", cmd)
        self.assertIn("--output-format", cmd)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class CodexChildEnvIsVendorScoped(unittest.TestCase):
    """The Anthropic key never reaches the codex judge child (upstream PR #885 review, back-ported):
    _judge_env re-injects it for key-billed sessions, and another vendor's process has no use for it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(self.tmp)
        jd._state_cache.clear()
        (jd.STATE / "judge-engine").write_text("codex")

    def test_the_codex_subprocess_env_lacks_the_anthropic_key(self):
        from unittest import mock
        seen = {}

        class _P:
            returncode = 0; stdout = ""; stderr = ""

        def fake_run(cmd, **kw):
            seen["env"] = kw.get("env")
            outp = [a for a in cmd if str(a).endswith(".out")]
            if outp:
                Path(outp[-1]).write_text("ok")
            return _P()

        fake_env = dict(os.environ, ANTHROPIC_API_KEY="sk-test-not-real", ROMP_SUMMARIZING="1")
        with mock.patch.object(jd, "_judge_env", return_value=fake_env), \
             mock.patch.object(jd.subprocess, "run", side_effect=fake_run), \
             contextlib.redirect_stderr(io.StringIO()):
            try:
                jd._judge_run("gpt-5", "SYS", "payload", judge="captioner", tier="index")
            except Exception:
                pass                                  # the reply shape is not under test here
        self.assertIsNotNone(seen.get("env"), "the codex branch ran through subprocess.run")
        self.assertNotIn("ANTHROPIC_API_KEY", seen["env"], "the Anthropic key is stripped from the codex child")
        self.assertEqual(seen["env"].get("ROMP_SUMMARIZING"), "1", "…and the rest of the judge env rides as before")
