#!/usr/bin/env python3
"""The rename ping (the user 2026-08-24): a renamed session hears its OWN new name on its next
wake — rename() stamps the reg (renameNote, restart-proof), and send() delivers RENAME_NUDGE one
line ahead of whatever next enters the session, never a wake of its own. Voice pinned in
test_injected_voice.py. Deterministic: reg-level + source pins, no real claude processes."""
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = SourceFileLoader("romp_sdk_backend_renameping", os.path.join(BIN, "romp_sdk_backend.py")).load_module()

SID = "aaaaaaaa-1111-2222-3333-444444444444"
SRC = open(os.path.join(BIN, "romp_sdk_backend.py")).read()


def _backend(d):
    return sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)


class RenamePing(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.be = _backend(self.td.name)
        (Path(self.td.name) / "names").mkdir(parents=True, exist_ok=True)
        sb.write_reg(Path(self.td.name), SID, {"sid": SID, "name": "web", "cwd": "/tmp"})

    def tearDown(self):
        self.td.cleanup()

    def test_rename_stamps_the_pending_note_beside_the_name(self):
        self.assertTrue(self.be.rename(SID, "tests"))
        reg = sb.read_reg(Path(self.td.name), SID)
        self.assertEqual(reg.get("name"), "tests")
        self.assertEqual(reg.get("renameNote"), "tests",
                         "reg-persisted, so the ping survives a kernel restart unspoken")

    def test_rename_of_an_unknown_sid_stamps_nothing(self):
        self.assertFalse(self.be.rename("99999999-0000-1111-2222-333333333333", "tests"))

    def test_send_delivers_the_ping_once_and_skips_slash_commands(self):
        # send()'s consume, pinned at source (driving a real send spawns a CLI): the ping rides
        # ahead of the next NON-command text, clears after one delivery, and never re-authors the
        # host message (marker-free — see RENAME_NUDGE's own comment and the voice test)
        self.assertIn('if not text.lstrip().startswith("/"):', SRC, "a bare /compact must reach the CLI bare")
        self.assertIn('if _reg.get("renameNote"):', SRC)
        self.assertIn('text = "%s\\n\\n%s" % (RENAME_NUDGE % _reg["renameNote"], text)', SRC)
        self.assertIn('self._update_reg(sid, renameNote=None)', SRC, "one delivery, then the note is spent")
        self.assertNotIn("romp-injected", sb.RENAME_NUDGE, "marker-free: it joins an EXISTING message")


if __name__ == "__main__":
    unittest.main()
