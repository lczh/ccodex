#!/usr/bin/env python3
"""tmux is an OPTIONAL dependency: a host without it runs romp fully on the SDK backend, and the tmux
backend simply stays disabled until a tmux appears on PATH (the user 2026-07-27, first Linux install,
which had no tmux). "Disabled" has to mean genuinely inert — no subprocess spawned per producer tick,
no error spam — and it has to come back BY ITSELF once tmux is installed, with no kernel restart.

Synthetic only: no real session data; the fake tmux is a stub path.
"""
import os
import unittest
from importlib.machinery import SourceFileLoader
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_tmuxopt", os.path.join(BIN, "romp-kernel")).load_module()


class _SpawnCounter:
    """Stands in for subprocess.run and RECORDS what it was asked to spawn.

    It must not raise to signal the failure: every tmux primitive wraps its spawn in a bare
    `except Exception`, so an assertion thrown from in here would be swallowed by the very code
    under test and the test would pass against the unfixed backend. Record, return a plausible
    result, and assert on the log afterwards."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, *a, **kw):
        self.calls.append(argv)
        return _FakeCompleted()


class _FakeCompleted:
    returncode = 0
    stdout = ""
    stderr = ""


class TmuxOptional(unittest.TestCase):
    def setUp(self):
        self.tb = km.TmuxBackend()
        self._real_which = km.shutil.which
        self._real_run = km.subprocess.run
        # Sibling test modules set ROMP_TMUX_AVAILABLE=1 at import to declare a tmux host, and
        # os.environ is process-wide — so clear it here or a full-suite run would mask the probe
        # these tests exist to check.
        self._real_env = os.environ.pop("ROMP_TMUX_AVAILABLE", None)

    def tearDown(self):
        km.shutil.which = self._real_which
        km.subprocess.run = self._real_run
        if self._real_env is None:
            os.environ.pop("ROMP_TMUX_AVAILABLE", None)
        else:
            os.environ["ROMP_TMUX_AVAILABLE"] = self._real_env

    def _no_tmux(self):
        km.shutil.which = lambda name, *a, **k: None

    def _has_tmux(self):
        km.shutil.which = lambda name, *a, **k: "/usr/bin/tmux" if name == "tmux" else None

    # ── availability tracks PATH ──────────────────────────────────────────────
    def test_available_false_without_tmux(self):
        self._no_tmux()
        self.assertFalse(self.tb.available())

    def test_available_true_with_tmux(self):
        self._has_tmux()
        self.assertTrue(self.tb.available())

    # ── disabled means inert, not "failing quietly" ───────────────────────────
    def test_no_subprocess_spawned_when_tmux_is_absent(self):
        """The producer loop calls list_lines on every tick. Without tmux that must cost nothing —
        the old code spawned, caught FileNotFoundError, and returned None, once per tick forever."""
        self._no_tmux()
        spawn = _SpawnCounter()
        km.subprocess.run = spawn

        self.assertEqual(self.tb.list_lines(km.TmuxBackend.NAME_FMT), [])
        self.assertIsNone(self.tb._run(["list-sessions"]))
        self.tb._fire(["set", "-t", "web", "@romp", "1"])       # must not raise
        self.tb.send_keys("web", "hello")
        self.tb.kill_by_name("web")
        self.tb.rename_by_name("web", "api")

        self.assertEqual(spawn.calls, [], "no tmux on PATH, yet the backend still shelled out")

    def test_live_sessions_is_empty_not_broken_without_tmux(self):
        """An empty fleet, not an exception: the dashboard shows the SDK sessions and nothing else."""
        self._no_tmux()
        spawn = _SpawnCounter()
        km.subprocess.run = spawn
        self.assertEqual(self.tb.list_lines(km.TmuxBackend.LANE_FMT), [])
        self.assertEqual(spawn.calls, [])

    # ── the explicit override ─────────────────────────────────────────────────
    def test_env_override_forces_the_backend_on_or_off(self):
        """ROMP_TMUX_AVAILABLE beats the PATH probe in both directions: it is how the tmux-behaviour
        tests declare a tmux host, and how a host that HAS tmux can be told not to use it."""
        self._no_tmux()
        os.environ["ROMP_TMUX_AVAILABLE"] = "1"
        self.assertTrue(self.tb.available())

        self._has_tmux()
        for off in ("0", ""):
            os.environ["ROMP_TMUX_AVAILABLE"] = off
            self.assertFalse(self.tb.available(), "%r should force the backend off" % off)

    # ── and it heals the moment tmux is installed ─────────────────────────────
    def test_reprobes_so_installing_tmux_needs_no_restart(self):
        """available() must not be cached at import or first call: `apt install tmux` has to go live
        on the next tick. Caching would strand a freshly-installed tmux behind a kernel restart."""
        self._no_tmux()
        self.assertFalse(self.tb.available())

        self._has_tmux()
        self.assertTrue(self.tb.available())        # same instance, no restart, no expiry wait

        seen = []
        km.subprocess.run = lambda argv, *a, **k: seen.append(argv)
        self.tb._fire(["set", "-t", "web", "@romp", "1"])
        self.assertTrue(seen, "with tmux present the backend must actually shell out again")
        self.assertEqual(seen[0][0], "tmux")

    def test_rename_uses_the_profile_tmux_socket(self):
        self._has_tmux()
        spawn = _SpawnCounter()
        km.subprocess.run = spawn
        old = os.environ.get("ROMP_TMUX_SOCKET")
        try:
            os.environ["ROMP_TMUX_SOCKET"] = "romp-alt"
            self.tb.rename_by_name("web", "api")
        finally:
            if old is None:
                os.environ.pop("ROMP_TMUX_SOCKET", None)
            else:
                os.environ["ROMP_TMUX_SOCKET"] = old
        self.assertEqual(spawn.calls, [["tmux", "-L", "romp-alt", "rename-session", "-t", "web", "api"]])


if __name__ == "__main__":
    unittest.main()
