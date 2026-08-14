"""Click-to-open a RELATIVE path (the user 2026-07-06: click `design/foo.md` in the chat and open it). A bare
relative path is relative to the SESSION's repo, not the kernel's launch cwd, so _resolve_open_path joins it
against _cwd_of(sid). Absolute paths (incl. file:// caption links) pass through; ~ expands. Pure resolver test."""
import os
import unittest
from importlib.machinery import SourceFileLoader
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_orp", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
BASE = "/tmp/TESTHOST-repo"


class ResolveOpenPath(unittest.TestCase):
    def setUp(self):
        self._orig = km._cwd_of
        km._cwd_of = lambda sid: BASE if sid == SID else ""

    def tearDown(self):
        km._cwd_of = self._orig

    def test_relative_path_resolves_against_the_session_cwd(self):
        self.assertEqual(km._resolve_open_path("design/judge-simplification-plan.md", SID),
                         os.path.join(BASE, "design/judge-simplification-plan.md"))

    def test_absolute_path_passes_through_untouched(self):
        self.assertEqual(km._resolve_open_path("/var/log/x.txt", SID), "/var/log/x.txt")

    def test_tilde_is_expanded_not_left_literal(self):
        out = km._resolve_open_path("~/notes.md", SID)
        self.assertFalse(out.startswith("~"), "the ~ must be expanded")
        self.assertTrue(out.endswith("/notes.md"))
        self.assertTrue(os.path.isabs(out))

    def test_relative_with_no_sid_is_left_as_is(self):
        # best-effort: no session context → don't guess a base (never silently open the wrong file)
        self.assertEqual(km._resolve_open_path("design/foo.md", None), "design/foo.md")

    def test_relative_with_unknown_sid_is_left_as_is(self):
        self.assertEqual(km._resolve_open_path("design/foo.md", "no-such-sid"), "design/foo.md")

    def test_handler_threads_the_session_id_into_the_resolver(self):
        # the openFile handler passes the message's session id so relatives resolve against the right session
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('_open_file(str(msg["path"]), sid=msg.get("id"))', src)
        self.assertIn('_reply(client, {"type": "warn", "text": err})', src)

    def test_linux_open_uses_xdg_open(self):
        old_platform, old_which, old_popen, old_exists = (
            km.sys.platform, km.shutil.which, km.subprocess.Popen, km.os.path.exists)
        calls = []
        try:
            km.sys.platform = "linux"
            km.shutil.which = lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None
            km.os.path.exists = lambda _path: True
            km.subprocess.Popen = lambda argv, **kwargs: calls.append(argv)
            self.assertIsNone(km._open_file("design/foo.md", SID))
            self.assertEqual(calls, [["xdg-open", os.path.join(BASE, "design/foo.md")]])
        finally:
            km.sys.platform, km.shutil.which, km.subprocess.Popen, km.os.path.exists = (
                old_platform, old_which, old_popen, old_exists)

    def test_linux_open_reports_when_no_desktop_opener_exists(self):
        old_platform, old_which, old_exists = km.sys.platform, km.shutil.which, km.os.path.exists
        try:
            km.sys.platform = "linux"
            km.shutil.which = lambda _name: None
            km.os.path.exists = lambda _path: True
            self.assertIn("install xdg-utils", km._open_file("design/foo.md", SID))
        finally:
            km.sys.platform, km.shutil.which, km.os.path.exists = old_platform, old_which, old_exists


if __name__ == "__main__":
    unittest.main()
