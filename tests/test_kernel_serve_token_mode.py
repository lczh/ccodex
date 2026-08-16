#!/usr/bin/env python3
"""The serve-token file's MODE is the same-user gate on the whole kernel: anything that reads it can
drive every session. `_load_token` used to `write_text()` and then `chmod 0600`, so between those two
calls the token sat on disk at whatever the umask allowed (0644 on a stock account, 0666 under a
permissive one). Opening the file 0600 closes that window.

This is consistency, not an exploit: `judge.py` chmods the state root to 0700 at import, long before
the token is minted, so the loose file already sat behind a tight door. But the credential's own mode
is what the docstring above `_load_token` names as the gate, so it should be right from birth rather
than a moment later — and the state root's mode is not something this function gets to assume.

The mode test has to INTERPOSE on os.chmod. Without that the trailing chmod repairs the mode before
anything can observe it, and the assertion passes on the unfixed code too.

Synthetic only — hermetic temp STATE, placeholder token.
"""
import os
import stat
import tempfile
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


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


class ServeTokenFileMode(unittest.TestCase):
    def setUp(self):
        self.f = km.jd.STATE / "serve-token"
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        if self.f.exists():
            self.f.unlink()
        # _load_token returns ROMP_SERVE_TOKEN verbatim and never touches the file while it is set,
        # so the mint path is only reachable with it out of the way.
        self._env = os.environ.pop("ROMP_SERVE_TOKEN", None)
        self._umask = os.umask(0)            # the widest case: only the open mode protects the file
        self.addCleanup(self._restore)

    def _restore(self):
        os.umask(self._umask)
        if self._env is not None:
            os.environ["ROMP_SERVE_TOKEN"] = self._env
        if self.f.exists():
            self.f.unlink()

    def test_minted_0600_before_any_chmod_can_repair_it(self):
        chmods = []
        real_chmod = os.chmod

        def _no_chmod(path, mode, *a, **k):
            """Record the repair and DON'T perform it — the mode the open() gave the file is the
            whole question, and a chmod one line later hides the answer."""
            chmods.append((str(path), mode))

        os.chmod = _no_chmod
        try:
            tok = km._load_token()
        finally:
            os.chmod = real_chmod

        self.assertTrue(self.f.exists(), "the mint path never ran — this test proves nothing")
        self.assertEqual(self.f.read_text().strip(), tok, "the file must hold the token it returned")
        self.assertEqual(_mode(self.f), 0o600,
                         "the serve token must be 0600 from the open() that created it: writing it "
                         "first and chmod'ing after leaves the credential at the umask's mercy for "
                         "the gap between the two calls")
        self.assertEqual(chmods, [(str(self.f), 0o600)],
                         "the chmod after the write must stay — it is what tightens a PRE-EXISTING "
                         "file, whose mode O_CREAT does not touch")

    def test_a_pre_existing_loose_file_is_still_tightened(self):
        # O_CREAT applies its mode only when it actually creates the file. An empty serve-token left
        # at 0644 (a stale one, or one written before this change) re-mints through the same path and
        # must come out 0600 — that is the chmod's remaining job, so it does not get dropped.
        self.f.write_text("")                # empty → falsy → falls through to the mint
        os.chmod(self.f, 0o644)
        tok = km._load_token()
        self.assertEqual(self.f.read_text().strip(), tok)
        self.assertEqual(_mode(self.f), 0o600,
                         "a token file that already existed at 0644 must not keep that mode")

    def test_the_env_override_never_writes_the_file(self):
        # Guards this test file's own premise: with ROMP_SERVE_TOKEN set there is nothing on disk to
        # have a mode, so the cases above would be vacuous if the pop in setUp ever stopped working.
        os.environ["ROMP_SERVE_TOKEN"] = "env-token-DO-NOT-USE"
        try:
            self.assertEqual(km._load_token(), "env-token-DO-NOT-USE")
        finally:
            os.environ.pop("ROMP_SERVE_TOKEN", None)
        self.assertFalse(self.f.exists(), "the env override must not mint or persist anything")


if __name__ == "__main__":
    unittest.main(verbosity=2)
