#!/usr/bin/env python3
"""POST /new's per-spawn model/effort (the user 2026-08-14): applied via the park-aware setters on
CREATE and on the idempotent existing:true open (a nightly re-brief re-asserts them), echoed in the
response so a caller can be loud when ignored; absent keys touch nothing.

Drives the REAL Handler over HTTP (the test_kernel_ws_auth.py pattern). Synthetic only — placeholder
UUIDs, temp dirs, no session state touched (the setters are recorded, never executed).
"""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
SID2 = "66666666-7777-8888-9999-000000000000"


class NewRoutePrefs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.dir = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.calls = []
        self._saved = (km._live_names, km._tmux_sessions, km._set_model_or_park,
                       km._set_effort_or_park, km.Sessions.backend_for,
                       km._sdk_ready, km._create_sdk_session, km._push_soon)
        km._tmux_sessions = lambda: []
        km._set_model_or_park = lambda be, sid, v: self.calls.append(("model", sid, v))
        km._set_effort_or_park = lambda be, sid, v: self.calls.append(("effort", sid, v))
        km.Sessions.backend_for = staticmethod(lambda sid: object())
        km._push_soon = lambda: None

    def tearDown(self):
        (km._live_names, km._tmux_sessions, km._set_model_or_park,
         km._set_effort_or_park, km.Sessions.backend_for,
         km._sdk_ready, km._create_sdk_session, km._push_soon) = self._saved

    def _post(self, body):
        req = urllib.request.Request("http://127.0.0.1:%d/new" % self.port,
                                     data=json.dumps(body).encode(),
                                     headers={"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"],
                                              "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def test_existing_open_reasserts_model_and_effort_and_echoes_them(self):
        km._live_names = lambda *_: {"opt": SID}
        r = self._post({"name": "opt", "dir": self.dir,
                        "model": "claude-fable-5", "effort": "ultracode"})
        self.assertTrue(r["ok"])
        self.assertTrue(r["existing"])
        self.assertEqual(r.get("model"), "claude-fable-5")
        self.assertEqual(r.get("effort"), "ultracode")
        self.assertIn(("model", SID, "claude-fable-5"), self.calls)
        self.assertIn(("effort", SID, "ultracode"), self.calls)

    def test_existing_open_without_prefs_touches_nothing(self):
        km._live_names = lambda *_: {"opt": SID}
        r = self._post({"name": "opt", "dir": self.dir})
        self.assertTrue(r["ok"])
        self.assertNotIn("model", r)
        self.assertNotIn("effort", r)
        self.assertEqual(self.calls, [])

    def test_fresh_sdk_create_applies_both_and_echoes_them(self):
        km._live_names = lambda *_: {}
        km._sdk_ready = lambda: True
        # the create path owns the prefs now — applied between spawn and connect, so the FIRST
        # connect carries them (the 2026-08-16 -m drop); the stub mirrors that seam
        km._create_sdk_session = (lambda nm, cwd, auth="", prefs=None:
                                  (SID2, km._apply_new_session_prefs(SID2, prefs or {})))
        r = self._post({"name": "opt", "dir": self.dir,
                        "model": "claude-fable-5", "effort": "ultracode"})
        self.assertTrue(r["ok"])
        self.assertEqual(r.get("model"), "claude-fable-5")
        self.assertEqual(r.get("effort"), "ultracode")
        self.assertIn(("model", SID2, "claude-fable-5"), self.calls)
        self.assertIn(("effort", SID2, "ultracode"), self.calls)

    def test_model_alone_applies_and_echoes_only_model(self):
        km._live_names = lambda *_: {"opt": SID}
        r = self._post({"name": "opt", "dir": self.dir, "model": "claude-fable-5"})
        self.assertTrue(r["ok"])
        self.assertEqual(r.get("model"), "claude-fable-5")
        self.assertNotIn("effort", r)
        self.assertEqual(self.calls, [("model", SID, "claude-fable-5")])


if __name__ == "__main__":
    unittest.main()
