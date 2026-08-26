#!/usr/bin/env python3
"""The remote-apply script must not kill ITSELF.

`pkill -f <pat>` matches against every process's FULL COMMAND LINE. The apply script romp ships to a
remote over ssh contains its own pkill pattern as literal text, so a plain `pkill -f "bin/romp-kernel"`
matched the apply shell and killed it at that line — before it restarted the kernel or echoed SYNCED.
The git reset had already run, so the code WAS synced, yet romp reported "remote apply failed" and left
the host with no kernel. That single line produced both long-standing symptoms: pushes that "always
fail for no clear reason", and a remote whose kernel kept vanishing (the user 2026-07-22).

The fix is a pattern that matches the target but not its own spelling: `bin/romp-kern[e]l`.

The behavioural test below is the important one — it runs a REAL pkill against a decoy process whose
command line embeds the pattern, which is exactly the condition that bit us. Synthetic only.
"""
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()


def _apply_script():
    """The apply script _update_remote ships to the remote, captured via a stubbed ssh."""
    seen = {}

    def fake_run(argv, **kw):
        joined = " ".join(argv)
        if argv[0] == "git":
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "merge-base" in joined:                      # the APPLY leg — capture and stop
            seen["apply"] = argv[-1]
            return subprocess.CompletedProcess(argv, 0, "SYNCED:abc1234\n", "")
        return subprocess.CompletedProcess(argv, 0, "DIR:/home/u/romp\nHEAD:deadbeef\nDIRTY:\n", "")

    from unittest import mock
    with mock.patch.object(km.subprocess, "run", side_effect=fake_run), \
         mock.patch.object(km, "_fresh_local_head", return_value="cafe1234" * 5):
        km._update_remote("TESTHOST")
    return seen.get("apply", "")


class ApplyScriptPkillPattern(unittest.TestCase):
    # The apply now carries TWO pkills (the v1.3.18 audit's P1: the MANAGER dies first, or its
    # exit handler respawns the killed kernel through the live checkout's serve before the pinned
    # ensure runs). Every pattern gets the self-match guard; the SET must still cover both the
    # real kernel and the real manager command lines.

    def test_no_pattern_matches_its_own_text(self):
        script = _apply_script()
        pats = re.findall(r'\["pkill","-f","([^"]+)"\]', script)
        self.assertGreaterEqual(len(pats), 2, "the apply kills the manager AND the old kernel "
                                "(inside the locked python since r44; manager since the "
                                "v1.3.18 audit's P1)")
        for pat in pats:
            # THE bug: a pattern, applied to the script that carries it, must find nothing.
            self.assertIsNone(re.search(pat, script),
                              "pkill pattern %r matches the apply script's own text — it would kill "
                              "the apply shell mid-run, exactly the bug this guards" % pat)

    def test_the_patterns_still_match_the_real_command_lines(self):
        script = _apply_script()
        pats = re.findall(r'\["pkill","-f","([^"]+)"\]', script)
        for real in ("/usr/bin/python3 /home/u/GitRepos/romp/bin/romp-kernel",
                     "python3.12 /home/u/romp/bin/romp-kernel --port 29855",
                     "node /home/u/romp/bin/romp-manager ensure"):
            self.assertTrue(any(re.search(p, real) for p in pats),
                            "patterns %r must still kill the real process (%r)" % (pats, real))


@unittest.skipUnless(sys.platform.startswith("linux") or sys.platform == "darwin",
                     "needs a POSIX pkill/pgrep")
class RealPkillBehaviour(unittest.TestCase):
    """Run the actual pattern through the real pgrep against a decoy whose command line embeds it —
    the precise condition that killed the apply shell. A source-pin alone would not catch a change in
    pkill semantics; this does."""

    def test_pgrep_does_not_match_a_process_merely_carrying_the_pattern(self):
        script = _apply_script()
        # every shipped pattern (kernel + manager since the v1.3.18 audit's P1) gets the check
        for pat in re.findall(r'\["pkill","-f","([^"]+)"\]', script):
            # a decoy that merely CONTAINS the pattern text, like the apply shell does
            decoy = subprocess.Popen(["/bin/sh", "-c",
                                      'echo "pkill -f \\"%s\\"" >/dev/null; sleep 5' % pat])
            try:
                time.sleep(0.4)
                r = subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True)
                hits = [p for p in (r.stdout or "").split() if p.strip()]
                self.assertNotIn(str(decoy.pid), hits,
                                 "pattern %r matched a process that merely carries its text — that "
                                 "is the self-kill condition" % pat)
            finally:
                decoy.kill()
                decoy.wait(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
