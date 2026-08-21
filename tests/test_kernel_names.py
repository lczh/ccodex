#!/usr/bin/env python3
"""Session-name reservation is ATOMIC across create, fork, and promotion (the v1.3.9 audit raced
two same-name /fork POSTs: both returned 200, both sessions were created, and one became
unaddressable by name — poisoning every by-name surface). The claim and the live-store check are
one locked step; the /fork and /new routes also refuse non-object JSON bodies with a 400 instead
of crashing .get() into a 500 traceback.

Synthetic only — hermetic temp state, placeholder names.
"""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from importlib.machinery import SourceFileLoader
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()


class NameClaims(unittest.TestCase):
    def tearDown(self):
        with km._name_claims_lock:
            km._NAME_CLAIMS.clear()

    def test_the_claim_and_the_live_check_are_one_locked_step(self):
        with mock.patch.object(km, "_tmux_sessions", return_value={}):
            self.assertTrue(km._claim_name("web"))
            self.assertFalse(km._claim_name("web"), "a second claim for one name must lose")
            km._release_name("web")
            self.assertTrue(km._claim_name("web"), "released names are claimable again")
            km._release_name("web")

    def test_a_live_name_is_never_claimable(self):
        with mock.patch.object(km, "_tmux_sessions", return_value={"sid1": {}}):
            with mock.patch.object(km, "_name_of", return_value="web"):
                self.assertFalse(km._claim_name("web"))

    def test_two_concurrent_forks_for_one_name_yield_exactly_one_winner(self):
        # the audit's exact schedule, at the seam every fork path shares: the loser must get the
        # refusal STRING, and the winner's inner fork must run exactly once
        gate = threading.Event()
        ran = []

        def slow_inner(parent_sid, cut, nm, now=None, client=None):
            ran.append(nm)
            gate.wait(10)                    # hold the claim while the rival tries
            return None
        results = []
        with mock.patch.object(km, "_fork_session_inner", side_effect=slow_inner):
            with mock.patch.object(km, "_tmux_sessions", return_value={}):
                t1 = threading.Thread(target=lambda: results.append(
                    km._fork_session("sid1", "", "web")))
                t1.start()
                while not ran:               # the winner is inside, holding the claim
                    pass
                results.append(km._fork_session("sid2", "", "web"))
                gate.set()
                t1.join(10)
        self.assertEqual(len(ran), 1, "exactly one fork ran")
        self.assertEqual(sorted(x is None for x in results), [False, True],
                         "one winner (None), one refusal string")
        self.assertIn("already running or being created",
                      next(x for x in results if x is not None))

    def test_creators_refuse_a_claimed_name(self):
        with mock.patch.object(km, "_tmux_sessions", return_value={}):
            self.assertTrue(km._claim_name("web"))
            try:
                sid, extra = km._create_sdk_session("web", "/tmp")
                self.assertEqual(sid, "")
                self.assertIn("already running or being created", extra.get("error", ""))
                self.assertEqual(km._create_codex_session("web", "/tmp"), "")
            finally:
                km._release_name("web")


class ForkRouteBody(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _post(self, path, body):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method="POST",
                                     data=body, headers={"X-Romp-Token": km.TOKEN,
                                                         "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_non_object_bodies_are_a_400_never_a_traceback(self):
        # arrays, strings, and numbers crashed .get() into a 500 (the v1.3.9 audit)
        for path in ("/fork", "/new"):
            for junk in (b"[1,2]", b'"web"', b"7"):
                code, out = self._post(path, junk)
                self.assertEqual(code, 400, "%s with %r" % (path, junk))
                self.assertIn("JSON object", out)

    def test_fork_requires_parent_and_name(self):
        code, out = self._post("/fork", b"{}")
        self.assertEqual(code, 400)
        self.assertIn("parent and name", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
