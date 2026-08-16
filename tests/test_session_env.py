"""The session-identity environment surface (the user 2026-08-15 sid, 2026-08-16 name).

Every SDK session's CLI process — and every Bash it runs — carries its romp identity in env:
ROMP_SID (the stable uuid; what `romp end self` resolves through) and ROMP_SESSION_NAME (the
human name at spawn). The name is a GENERIC capability for child processes that need to know
which session they belong to (attribution, logging), deliberately coupled to no consumer.
Env is spawn-frozen, so a post-spawn rename is not reflected — the sid is the address, the
name a label. Source pins over _options' env line (the SDK merges options.env OVER the
inherited environment, so both ride the same additive overlay as the bin PATH)."""
import os
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
SDK = Path(os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py")).read_text()


class SessionIdentityEnv(unittest.TestCase):
    def test_sid_and_name_ride_the_spawn_env(self):
        self.assertIn('"ROMP_SID": str(sess.sid),', SDK,
                      "the stable identity — addressing (romp end self)")
        self.assertIn('"ROMP_SESSION_NAME": str(sess.name)', SDK,
                      "the human name at spawn — attribution/logging for child processes")

    def test_the_name_is_documented_as_spawn_frozen(self):
        # the caveat is the contract: a rename after spawn is NOT reflected in a live session's
        # env, so nothing may treat the name as an address — the comment must keep saying so
        self.assertIn("a rename after spawn is NOT reflected", SDK)

    def test_the_terminal_launcher_exports_the_same_identity(self):
        # both backends: the tmux launch line carries ROMP_SID + ROMP_SESSION_NAME into the CLI's
        # environment (the user 2026-08-16 — external tools attribute env-first, never via tmux)
        launcher = Path(os.path.join(os.path.dirname(HERE), "bin", "romp")).read_text()
        self.assertIn('claude_cmd="ROMP_SID=$sid ROMP_SESSION_NAME=\\"$display\\" $claude_cmd"', launcher)

    def test_one_env_overlay_only(self):
        # both vars ride _options' single env= overlay (additive over os.environ via
        # _bin_on_path_env) — a second env assembly would fork the truth
        self.assertEqual(SDK.count('"ROMP_SESSION_NAME":'), 1)
        self.assertEqual(SDK.count('env={**_bin_on_path_env(os.environ)'), 1)


if __name__ == "__main__":
    unittest.main()
