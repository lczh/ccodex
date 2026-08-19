#!/usr/bin/env python3
"""The judge rate-limit gate (the user 2026-07-07): while the ACCOUNT is limit-exhausted (usage.json,
written by the SDK backend's /usage poll), every judge LLM call fleet-wide is a doomed API retry — the
archiver postmortem counted ~1160 wasted calls in one 90-minute window. _judge_run skips the call and
rides the SAME `paused` flag as a retry-pause skip, so no give-up counter ever counts it as a failure.
`resets_at` makes the gate self-expiring (a stale "limited" stops gating the moment the window resets —
event-based, no age heuristics); a missing/unreadable usage.json never gates. Synthetic fixtures."""
import json
import shutil
import tempfile
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
import os

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = SourceFileLoader("romp_judge_rategate", os.path.join(BIN, "romp-judge")).load_module()


class RateLimitGate(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))
        jd._RATE_GATE_LOGGED.clear()
        self.calls = []
        self._saved_run = jd.subprocess.run

        class _FakeDone:
            stdout = '{"result": "the-model-reply"}'

        def fake_run(*a, **k):
            self.calls.append(a)
            return _FakeDone()
        jd.subprocess.run = fake_run

    def tearDown(self):
        jd.subprocess.run = self._saved_run
        shutil.rmtree(self.td, ignore_errors=True)

    def _usage(self, pct, resets_in, bucket="five_hour"):
        (jd.STATE / "usage.json").write_text(json.dumps(
            {"t": int(time.time()),
             bucket: {"pct": pct, "resets_at": int(time.time()) + resets_in}}))

    def test_exhausted_window_skips_the_call_as_a_pause_not_a_failure(self):
        self._usage(100, 3600)
        out = jd._judge_run("m", "sys", "user")
        self.assertEqual(out, "")
        self.assertEqual(self.calls, [], "no subprocess launched — the call never went out")
        self.assertTrue(jd._judge_ctx.paused, "skip rides the paused flag: give-ups don't count it")
        errs = (jd.STATE / "judge-errors.jsonl").read_text()
        self.assertIn("rate-limited", errs, "…and the window is announced once")
        jd._judge_run("m", "sys", "user")             # second skip in the same window: no second line
        self.assertEqual((jd.STATE / "judge-errors.jsonl").read_text().count("rate-limited"), 1)

    def test_high_but_not_exhausted_never_gates(self):
        self._usage(99, 3600)
        self.assertEqual(jd._judge_run("m", "sys", "user"), "the-model-reply")
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(jd._judge_ctx.paused)

    def test_expired_window_self_disables(self):
        self._usage(100, -60)                         # says limited, but resets_at already passed
        self.assertEqual(jd._judge_run("m", "sys", "user"), "the-model-reply")
        self.assertEqual(len(self.calls), 1, "a stale 'limited' stops gating at reset — event-based")

    def test_missing_usage_never_gates(self):
        self.assertEqual(jd._judge_run("m", "sys", "user"), "the-model-reply")

    def test_fable_bucket_gates_exactly_the_calls_that_bill_it(self):
        # The gated buckets FOLLOW THE CALL'S MODEL (2026-08-18): the old tuple ignored `fable`
        # ("judges run Sonnet") — stale once the judge-model pin read fable, and ~22,400 doomed
        # retries burned across 08-16/17 while the fable window sat at 100%.
        self._usage(100, 3600, bucket="fable")
        self.assertEqual(jd._judge_run("sonnet", "sys", "user"), "the-model-reply",
                         "a sonnet call does not bill the fable window — never gated by it")
        self.assertEqual(jd._judge_run("fable", "sys", "user"), "",
                         "a fable call IS gated by the fable window")
        self.assertTrue(jd._judge_ctx.paused)

    def test_the_gate_latches_the_loud_limit_banner(self):
        # a limit-down judge layer must SAY so (the user 2026-08-18), never fail quietly: the gate
        # writes the judge-limit latch, self-expiring at the window reset, cleared by a success
        self._usage(100, 3600, bucket="fable")
        jd._judge_run("fable", "sys", "user")
        row = jd._limit_down()
        self.assertEqual(row["bucket"], "fable")
        self.assertEqual(row["model"], "fable")
        # a successful call (model switched to sonnet, say) clears the latch — the deciding event
        self.assertEqual(jd._judge_run("sonnet", "sys", "user"), "the-model-reply")
        self.assertIsNone(jd._limit_down())

    def test_the_latch_self_expires_at_the_reset(self):
        jd._limit_mark("fable", 100, int(time.time()) - 5, "fable")   # already past its reset
        self.assertIsNone(jd._limit_down(), "the window reset IS the deciding event — no age heuristics")

    def test_limit_shaped_envelope_latches_and_pokes_a_usage_poll(self):
        # get_usage rides turn ends, so an idle fleet's usage.json goes stale; the FIRST doomed call
        # is the event that refreshes it — the envelope latches the banner and fires the wired poll
        class _FakeErr:
            stdout = '{"is_error": true, "result": "Claude AI usage limit reached — resets 12:00"}'
        jd.subprocess.run = lambda *a, **k: _FakeErr()
        poked = []
        saved = jd._USAGE_REFRESH_FN
        try:
            jd._USAGE_REFRESH_FN = lambda: poked.append(1)
            self.assertEqual(jd._judge_run("fable", "sys", "user"), "")
            self.assertEqual(poked, [1], "one exact usage poll fired")
            self.assertEqual((jd._limit_down() or {}).get("bucket"), "account")
        finally:
            jd._USAGE_REFRESH_FN = saved


class SegKeyUnified(unittest.TestCase):
    def test_kernel_delegates_to_the_judge_seg_key(self):
        # the two copies had to never drift; since 2026-07-07 the kernel's is a delegation, so they cannot.
        import inspect
        os.environ.setdefault("ROMP_KERNEL_NO_OPEN", "1")
        os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
        km = SourceFileLoader("romp_kernel_segkey", os.path.join(BIN, "romp-kernel")).load_module()
        self.assertIn("jd._seg_key(seg_id)", inspect.getsource(km._seg_key))
        for sid in ("u:123:h", "u:123:h#p", None, "", "plain", "a:b"):
            self.assertEqual(km._seg_key(sid), km.jd._seg_key(sid))


if __name__ == "__main__":
    unittest.main()
