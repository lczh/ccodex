#!/usr/bin/env python3
"""Picker revive-from-disk (the user 2026-07-05). _revive_session shelled `romp-postal-service revive`,
a subcommand 2b5e181 removed (live-only postal addressing) — the CLI printed 'unknown command' and
EXITED 0, the output was DEVNULL'd, and the kernel then focused a still-dead session: the picker's
Revive silently did nothing for a week. The kernel now owns revive per backend (SDK resume+connect /
tmux `romp <name> --resume <sid> --detach` in the recorded dir), CHECKS the result, and on failure
sends the chat a reviveFailed event (clears the client's revive loader, shows why) instead of
pretending it worked. Synthetic fixtures only."""
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_rev", os.path.join(BIN, "romp-kernel")).load_module()
sb = SourceFileLoader("romp_sdk_backend_rev", os.path.join(BIN, "romp_sdk_backend.py")).load_module()

SID = "11111111-2222-3333-4444-555555555555"


class FakeSdk:
    def __init__(self, owns=True, resume_ok=True, connect_ok=True):
        self.calls = []
        self._owns, self._resume_ok, self._connect_ok = owns, resume_ok, connect_ok

    def owns(self, sid):
        return self._owns

    def resume(self, name, sid, cwd=None):
        self.calls.append(("resume", name, sid))
        return self._resume_ok

    def connect(self, sid):
        self.calls.append(("connect", sid))
        return self._connect_ok


class ReviveSession(unittest.TestCase):
    """Drives km._revive_session with the collaborators stubbed; asserts the per-backend action and
    that success → focus while failure → reviveFailed (loud), never both."""

    def setUp(self):
        self.saved = (km._sdk, km._name_of, km._cwd_of, km._push_all, km._reveal_chat,
                      km._send_to_app, subprocess.run)
        self.sent, self.focused, self.runs = [], [], []
        km._name_of = lambda sid: "testsess"
        km._cwd_of = lambda sid: "/nonexistent-dir-for-test"
        km._push_all = lambda: None
        km._reveal_chat = lambda msg: self.focused.append(msg)
        km._send_to_app = lambda app, msg: self.sent.append((app, msg))

    def tearDown(self):
        (km._sdk, km._name_of, km._cwd_of, km._push_all, km._reveal_chat,
         km._send_to_app, subprocess.run) = self.saved

    def _stub_run(self, returncode=0, stderr=""):
        def run(cmd, **kw):
            self.runs.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr=stderr)
        subprocess.run = run

    def test_sdk_session_revives_via_resume_and_connect(self):
        be = FakeSdk()
        km._sdk = lambda: be
        km._revive_session(SID)
        self.assertEqual(be.calls, [("resume", "testsess", SID), ("connect", SID)],
                         "SDK-owned dead session → registry alive again + eager connect (resumes lastSid)")
        self.assertEqual([m["type"] for m in self.focused], ["focus"], "success lands the chat on the tab")
        self.assertEqual(self.sent, [], "no failure event on success")

    def test_sdk_failure_is_loud_and_does_not_focus(self):
        km._sdk = lambda: FakeSdk(connect_ok=False)
        km._revive_session(SID)
        self.assertEqual(self.focused, [], "a failed revive must not focus a still-dead session")
        self.assertEqual(len(self.sent), 1)
        app, msg = self.sent[0]
        self.assertEqual((app, msg["type"], msg["id"]), ("chat", "reviveFailed", SID))

    def test_tmux_session_revives_via_romp_resume_detach(self):
        km._sdk = lambda: None
        self._stub_run(returncode=0)
        km._revive_session(SID)
        cmd, kw = self.runs[0]
        self.assertEqual(cmd, [os.path.join(BIN, "romp"), "resume", SID, "--name", "testsess", "--detach"],
                         "the launcher resume path the old postal revive used, now owned by the kernel "
                         "(round-3 spelling, 2026-07-25: resume <id> --name <name>)")
        self.assertEqual(kw.get("cwd"), os.path.expanduser("~"),
                         "a missing recorded dir falls back to $HOME (old postal behavior)")
        self.assertEqual([m["type"] for m in self.focused], ["focus"])
        self.assertEqual(self.sent, [])

    def test_tmux_failure_carries_the_launcher_error(self):
        km._sdk = lambda: None
        self._stub_run(returncode=3, stderr="no transcript for that uuid")
        km._revive_session(SID)
        self.assertEqual(self.focused, [])
        app, msg = self.sent[0]
        self.assertEqual(msg["type"], "reviveFailed")
        self.assertIn("no transcript", msg["text"], "the launcher's stderr reaches the user, not DEVNULL")

    def test_the_removed_postal_subcommand_is_gone(self):
        # the regression pin: 2b5e181 removed `romp-postal-service revive`; the kernel must never
        # shell it again (it exits 0 on unknown commands, so the failure is undetectable). The
        # docstring may NAME the old path as history — the pin is on the invocation form.
        import inspect
        self.assertNotIn('HERE / "romp-postal-service"', inspect.getsource(km._revive_session))


class SdkResumePreservesLastSid(unittest.TestCase):
    """resume() marks a dormant session alive for _ensure/connect. It must PRESERVE the registry's
    lastSid — the NEWEST transcript fsid (a /clear or relaunch mints new fsids under the same romp
    sid) that SdkSession actually resumes from; stamping the original sid would silently resume an
    OLD conversation state."""

    def _resume(self, reg):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        state = Path(td.name)
        (state / "sdk").mkdir(parents=True, exist_ok=True)
        if reg is not None:
            sb.write_reg(state, SID, reg)

        class FakeBackend:
            def __init__(self):
                self.state_dir = state
                self._reg_lock = threading.RLock()
            _update_reg = sb.SdkBackend._update_reg
            def _poke(self):
                pass
        sb.SdkBackend.resume(FakeBackend(), "testsess", SID)
        return sb.read_reg(state, SID)

    def test_newer_lastsid_survives_revive(self):
        reg = self._resume({"sid": SID, "name": "testsess", "cwd": "/tmp", "mode": "auto",
                            "effort": "high", "lastSid": "99999999-8888-7777-6666-555555555555",
                            "alive": False})
        self.assertEqual(reg["lastSid"], "99999999-8888-7777-6666-555555555555",
                         "the newest fsid is the resume point — never clobbered back to the birth sid")
        self.assertTrue(reg["alive"])
        self.assertEqual(reg["effort"], "high", "the rest of the registry survives the revive too")

    def test_empty_lastsid_falls_back_to_the_sid(self):
        reg = self._resume({"sid": SID, "name": "testsess", "cwd": "/tmp", "lastSid": "", "alive": False})
        self.assertEqual(reg["lastSid"], SID, "a never-relaunched session resumes from its own transcript")


if __name__ == "__main__":
    unittest.main()
