#!/usr/bin/env python3
"""POST /rename (the user 2026-08-23): the renameSession WS op as a one-shot token-gated route, the
exact sibling of /fork — agents rename sessions without WS surgery. Sessions are uuid-keyed with the
name as a label, so nothing breaks; the by-name poisoning guard mirrors /fork's. Drives the REAL
Handler over HTTP (the test_new_route_prefs.py pattern). Synthetic only."""
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


class RenameRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.renames = []
        renames = self.renames

        class BE:
            def rename(self, sid, name):
                renames.append((sid, name))
                return True
        self._saved = (km.Sessions.backend_for, km._tmux_sessions, km._live_names, km._mark_views_dirty)
        km.Sessions.backend_for = staticmethod(lambda sid: BE())
        km._tmux_sessions = lambda: {}
        km._live_names = lambda tm: {"web": SID,
                                     "api": "22222222-3333-4444-5555-666666666666"}
        km._mark_views_dirty = lambda: None
        km._NAME_CLAIMS.clear()

    def tearDown(self):
        (km.Sessions.backend_for, km._tmux_sessions, km._live_names, km._mark_views_dirty) = self._saved

    def _post(self, body):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/rename" % self.port, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def test_live_name_renames_through_the_backend(self):
        st, r = self._post({"target": "web", "name": "cross_model"})
        self.assertEqual(st, 200)
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r.get("id"), SID)
        self.assertEqual(self.renames, [(SID, "cross_model")],
                         "routed through be.rename so every surface resyncs")

    def test_a_sid_target_reaches_the_backend_directly(self):
        st, r = self._post({"target": SID, "name": "intuition_building"})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(self.renames[0][0], SID)

    def test_the_poisoning_guard_refuses_a_live_new_name(self):
        st, r = self._post({"target": SID, "name": "api"})   # another session's live name
        self.assertFalse(r.get("ok"))
        self.assertIn("already running", r.get("error") or "")
        self.assertEqual(self.renames, [])

    def test_renaming_to_its_own_name_is_an_idempotent_success(self):
        st, r = self._post({"target": SID, "name": "web"})
        self.assertTrue(r.get("ok"), "the WS path's own-name semantics: ok, nothing to claim")
        self.assertEqual(self.renames, [], "no backend rename for a no-op")

    def test_two_concurrent_renames_to_one_name_admit_exactly_one(self):
        # the v1.3.16 audit's P1: snapshot-check-rename raced — both got ok and one session went
        # unaddressable. The route now takes the same ATOMIC reservation as the WS path.
        import time as _time
        renames = self.renames

        class SlowBE:
            def rename(self, sid, name):
                _time.sleep(0.15)                     # hold the claim across the second request
                renames.append((sid, name))
                return True
        km.Sessions.backend_for = staticmethod(lambda sid: SlowBE())
        results = []

        def go(target):
            results.append(self._post({"target": target, "name": "same"})[1])

        a = threading.Thread(target=go, args=(SID,))
        b = threading.Thread(target=go, args=("22222222-3333-4444-5555-666666666666",))
        a.start(); b.start(); a.join(10); b.join(10)
        oks = [r for r in results if r.get("ok")]
        self.assertEqual(len(oks), 1, "exactly one rename may win the name: %r" % results)
        self.assertIn("already running", next(r for r in results if not r.get("ok"))["error"])
        self.assertEqual(len(renames), 1)

    def test_a_non_object_body_is_a_400_not_a_traceback(self):
        # the v1.3.16 audit: [1] crashed .get() into a traceback-bearing 500 with an absolute path
        for path in ("/rename", "/color", "/tag", "/watch-pr"):
            req = urllib.request.Request(
                "http://127.0.0.1:%d%s" % (self.port, path), data=b"[1]",
                headers={"Content-Type": "application/json",
                         "X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    st, body = r.status, r.read().decode()
            except urllib.error.HTTPError as e:
                st, body = e.code, e.read().decode()
            self.assertEqual(st, 400, "%s: %s" % (path, body))
            self.assertIn("JSON object", body)
            self.assertNotIn("Traceback", body)

    def test_bad_names_and_unknown_targets_are_loud(self):
        st, r = self._post({"target": "web", "name": "bad name!"})
        self.assertIn("letters, digits", r.get("error") or "")
        st, r = self._post({"target": "nope", "name": "fine-name"})
        self.assertIn("no live session named", r.get("error") or "")
        st, r = self._post({"target": "web"})
        self.assertEqual(st, 400)


if __name__ == "__main__":
    unittest.main()
