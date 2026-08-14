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

    def tearDown(self):
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
        for flag in ("exec", "--ephemeral", "--skip-git-repo-check", "read-only", "--color"):
            self.assertIn(flag, argv)
        self.assertNotIn("-m", argv, "a claude alias must never be sent to codex (plan accounts 400)")
        self.assertIn("model_reasoning_effort=low", argv, "index tier defaults to low effort")
        self.assertEqual((self.rec / "stdin").read_text(), "SYS PROMPT\n\nUSER TEXT")

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
