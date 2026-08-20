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
from unittest import mock
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
        self.assertEqual(chmods, [],
                         "a fresh mint needs NO repair: the temp is born 0600 and os.replace "
                         "carries the inode's mode — a chmod here would mean the mode came from a "
                         "repair window again (the read path owns the tighten now)")

    def test_a_pre_existing_loose_file_is_still_tightened(self):
        # An empty serve-token left at 0644 (a stale remnant) re-mints through the locked path,
        # and the os.replace swaps in a fresh 0600 inode — the loose mode goes with the old one.
        self.f.write_text("")                # empty → falsy → falls through to the mint
        os.chmod(self.f, 0o644)
        tok = km._load_token()
        self.assertEqual(self.f.read_text().strip(), tok)
        self.assertEqual(_mode(self.f), 0o600,
                         "a token file that already existed at 0644 must not keep that mode")

    def test_a_nonempty_loose_file_is_tightened_on_read(self):
        # The READ path is where a pre-existing loose file survives: the loader returns the token
        # without minting, so nothing replaces the inode — it must chmod what it keeps (the user's
        # audit, 2026-08-19: a 0644 nonempty token was returned as-is and never tightened).
        self.f.write_text("keep-me-token\n")
        os.chmod(self.f, 0o644)
        self.assertEqual(km._load_token(), "keep-me-token",
                         "an existing token is kept, never re-minted")
        self.assertEqual(_mode(self.f), 0o600,
                         "the read path must tighten the file it keeps — the mint path never sees "
                         "a nonempty file, so nobody else will")

    def test_a_stale_mint_temp_never_wedges_the_loader(self):
        # a mint that died between the O_EXCL open and the replace left serve-token.<pid>.tmp;
        # the next mint's O_EXCL then failed FOREVER for this pid — silently, so the loader
        # returned a fresh, never-persisted token on EVERY call (the adversarial review,
        # 2026-08-19, reproduced live)
        tmp = self.f.with_name("%s.%d.tmp" % (self.f.name, os.getpid()))
        tmp.write_text("stale")
        self.addCleanup(lambda: tmp.unlink(missing_ok=True))
        tok = km._load_token()
        self.assertEqual(self.f.read_text().strip(), tok,
                         "the minted token must be PERSISTED despite the stale temp")
        self.assertEqual(km._load_token(), tok, "and stable across calls")

    def test_a_failing_flock_REFUSES_and_still_leaks_no_fd(self):
        # the v1.3.8 audit: the lockless fallback minted without serialization and split tokens
        # after ENOLCK — a token state that cannot be established SECURELY now refuses startup
        # (and the opened lock fd still must not leak while doing so)
        if not os.path.isdir("/proc/self/fd"):
            self.skipTest("needs /proc (Linux)")
        real = km.fcntl.flock

        def no_locks(fd, op):
            raise OSError(37, "No locks available")
        km.fcntl.flock = no_locks
        try:
            before = len(os.listdir("/proc/self/fd"))
            for _ in range(10):
                with self.assertRaises(RuntimeError):
                    km._load_token()
            after = len(os.listdir("/proc/self/fd"))
        finally:
            km.fcntl.flock = real
        self.assertEqual(after, before, "refusal must not leak the lock fd either")
        self.assertFalse(self.f.exists(), "and nothing was minted locklessly")

    def test_an_unreadable_existing_token_is_never_rotated(self):
        # the v1.3.8 audit: an unreadable EXISTING token read as absent and was minted over —
        # rotating the credential out from under every live client holding it
        if os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        self.f.write_text("live-token")
        os.chmod(self.f, 0)
        try:
            with self.assertRaises(RuntimeError):
                km._load_token()
        finally:
            os.chmod(self.f, 0o600)
        self.assertEqual(self.f.read_text(), "live-token", "the existing token survives, unrotated")

    def test_a_failed_replace_never_returns_an_unpersisted_token(self):
        # the v1.3.8 audit: successive DISTINCT tokens with no file after a simulated ENOSPC —
        # the loader returned what it failed to persist
        with mock.patch.object(km.os, "replace",
                               side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(RuntimeError):
                km._load_token()
        self.assertFalse(self.f.exists(), "nothing persisted, and nothing pretended otherwise")

    def test_a_failed_tighten_refuses(self):
        # a 0644 token any local user can read is the boundary itself broken: a tighten that
        # cannot land is a refusal, not a shrug (the v1.3.8 audit)
        self.f.write_text("tok")
        os.chmod(self.f, 0o644)
        with mock.patch.object(km.os, "chmod", side_effect=OSError(30, "Read-only file system")):
            with self.assertRaises(RuntimeError):
                km._load_token()

    def test_the_env_override_never_writes_the_file(self):
        # Guards this test file's own premise: with ROMP_SERVE_TOKEN set there is nothing on disk to
        # have a mode, so the cases above would be vacuous if the pop in setUp ever stopped working.
        os.environ["ROMP_SERVE_TOKEN"] = "env-token-DO-NOT-USE"
        try:
            self.assertEqual(km._load_token(), "env-token-DO-NOT-USE")
        finally:
            os.environ.pop("ROMP_SERVE_TOKEN", None)
        self.assertFalse(self.f.exists(), "the env override must not mint or persist anything")


class ServeTokenFlock(unittest.TestCase):
    """The read-or-mint is serialized by <state>/serve-token.lock. Every lock-free scheme lost a
    schedule — the last let two starters both take the empty-remnant path and run on DIFFERENT
    tokens until restart (the user's audit, 2026-08-19, deterministic two-process repro). This is
    that repro, made deterministic in both directions by the lock itself: the test HOLDS the lock
    as minter A mid-critical-section, a real second process announces its lock attempt, A then
    publishes its token and releases — the loser must come back with A's token, never its own."""

    _CHILD = r"""
import os, sys
os.environ["ROMP_SERVE_TOKEN"] = "import-shield"   # module import loads TOKEN; keep it off disk
os.environ["ROMP_STATE_DIR"] = sys.argv[2]         # pin to the PARENT test's state root: sibling
#                                                    test modules each set XDG_STATE_HOME at import,
#                                                    and pytest imports them all before running, so
#                                                    the inherited env points at the LAST module's
#                                                    temp dir, not this one's
from importlib.machinery import SourceFileLoader
BIN = sys.argv[1]
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()
os.environ.pop("ROMP_SERVE_TOKEN", None)
import fcntl
_real = fcntl.flock
def _spy(fd, op):
    print("FLOCK", flush=True)                     # announce the attempt BEFORE it can block
    return _real(fd, op)
fcntl.flock = _spy
print(km._load_token(), flush=True)
"""

    def test_a_concurrent_starter_blocks_and_adopts_the_winner_token(self):
        import fcntl
        import select
        import subprocess
        import sys
        f = km.jd.STATE / "serve-token"
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        f.write_text("")                             # the audit's remnant: exists, empty
        env_tok = os.environ.pop("ROMP_SERVE_TOKEN", None)
        script = f.with_name("flock-child.py")
        script.write_text(self._CHILD)
        lfd = os.open(str(f) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(lfd, fcntl.LOCK_EX)              # we are minter A, inside the critical section
        p = subprocess.Popen([sys.executable, str(script), BIN, str(km.jd.STATE)],
                             stdout=subprocess.PIPE, text=True)

        def _cleanup():
            try:
                os.close(lfd)
            except OSError:
                pass
            if p.poll() is None:
                p.kill()
            p.stdout.close()
            if env_tok is not None:
                os.environ["ROMP_SERVE_TOKEN"] = env_tok
            for leftover in (f, f.with_name(f.name + ".lock"), script):
                try:
                    leftover.unlink()
                except OSError:
                    pass
        self.addCleanup(_cleanup)

        def _line(why):
            r, _, _ = select.select([p.stdout], [], [], 120)
            self.assertTrue(r, "no output within 120s — " + why)
            return p.stdout.readline().strip()

        self.assertEqual(_line("the loader never spoke"), "FLOCK",
                         "the loader must try the lock BEFORE reading: a first line that is "
                         "already a token means it minted against the empty remnant lock-free — "
                         "the split-brain schedule")
        tmp = f.with_name("winner.tmp")              # A publishes and leaves the critical section
        tmp.write_text("winner-token")
        os.replace(tmp, f)
        os.close(lfd)
        self.assertEqual(_line("the loader stayed blocked after the lock was freed"),
                         "winner-token",
                         "the blocked starter must adopt the token the lock-holder published — "
                         "coming back with any other value is the two-tokens-in-memory bug")
        self.assertEqual(p.wait(timeout=60), 0)
        self.assertEqual(f.read_text().strip(), "winner-token",
                         "the loser must not overwrite the winner's persisted token")


if __name__ == "__main__":
    unittest.main(verbosity=2)
