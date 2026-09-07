"""The kernel's key-SOURCE doors (2026-09-07), over the REAL handler on loopback — the KeycycleRoute
pattern in tests/test_keyswap.py:

  * GET  /keysource        → the backend's keysource_status(): kind, fingerprint, held sessions, health.
  * POST /keysource/probe  → keysource_probe(): ONE retrieval, then the held sessions it lifts.
  * POST /keycycle         → forgets the reuse memo before it resolves, so a rotation behind the same
                             reference is seen (the memo would otherwise report "current" and cycle nothing).
  * the pusher cycle       → _keysrc_hold_sweep_tick(), the O(1)-when-idle lift of key-source holds.

Every door is serve-token gated like /keycycle, answers 503 with no SDK backend, and never carries a
value. The backend methods are STUBBED here (their contract is the interface, not sdk_backend's
body): synthetic keys and references only.
"""
import inspect
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ.pop("ROMP_SUPERVISED", None)
os.environ["ROMP_SERVICE_ENV_FILE"] = os.path.join(os.environ["XDG_STATE_HOME"], "no-such-service.env")
os.environ["ROMP_SERVICE_ENV"] = os.environ["ROMP_SERVICE_ENV_FILE"]

sb = SourceFileLoader("romp_sdk_backend_keysource_routes", os.path.join(BIN, "romp_sdk_backend.py")).load_module()
ks = sb._keysrc      # ONE keysource module: the kernel (via judge) reads through sys.modules["romp_keysource"]

REF = "op://vault-test/item/field"
OLD_KEY = "sk-ant-TEST-rotated-away"
NEW_KEY = "sk-ant-TEST-rotated-in"


class _Routes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.km = (sys.modules.get("romp_kernel_keyswap")
                  or SourceFileLoader("romp_kernel_keysource_routes", os.path.join(BIN, "romp-kernel")).load_module())
        assert cls.km.jd._keysrc is ks, "the route must clear the memo the backend's resolve() reads"
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), cls.km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _call(self, path, method="GET", body=None, token=True):
        import urllib.error
        import urllib.request
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Romp-Token"] = self.km.TOKEN
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method=method,
                                     data=(json.dumps(body).encode() if body is not None else None),
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                return e.code, json.loads(raw or "{}")
            except ValueError:
                return e.code, {"raw": raw}

    def _with(self, fake, path, method="GET", body=None):
        woke = []
        with mock.patch.object(self.km, "_sdk", lambda: fake), \
             mock.patch.object(self.km, "_sid_of", lambda w: "s-" + w), \
             mock.patch.object(self.km, "_name_of", lambda sid: str(sid)[2:]), \
             mock.patch.object(self.km, "_push_soon", lambda: woke.append(1)):
            code, resp = self._call(path, method, body)
        return code, resp, woke


class _StatusFake:
    """Just the surface the doors use — the shared interface, stubbed."""

    def __init__(self, status=None, probe=None):
        self.sessions = {}
        self.status = status if status is not None else {
            "configured": True, "kind": "op", "sourceFp": ks.fingerprint("op:" + REF), "held": ["web"],
            "health": {"kind": "op", "sourceFp": ks.fingerprint("op:" + REF), "lastOkT": 0.0,
                       "lastFailT": 1.0, "note": "1Password credential retrieval failed"}}
        self.probe = probe if probe is not None else {"ok": True, "note": "", "lifted": ["web"],
                                                      "kind": "op", "sourceFp": ks.fingerprint("op:" + REF)}
        self.probed = 0

    def keysource_status(self):
        return dict(self.status)

    def keysource_probe(self):
        self.probed += 1
        return dict(self.probe)


class KeysourceStatusRoute(_Routes):
    def test_it_returns_the_backends_status_with_ok_and_never_a_value(self):
        fake = _StatusFake()
        code, resp, woke = self._with(fake, "/keysource")
        self.assertEqual(code, 200)
        self.assertTrue(resp.pop("ok"))
        self.assertEqual(resp, fake.status)
        self.assertEqual(woke, [], "a read wakes nothing")
        self.assertNotIn(REF, json.dumps(resp), "a reference is configuration, but the door carries the fingerprint")
        for k in ("configured", "kind", "sourceFp", "held", "health"):
            self.assertIn(k, resp)

    def test_no_backend_is_a_503(self):
        code, resp, _ = self._with(None, "/keysource")
        self.assertEqual((code, resp), (503, {"ok": False, "error": "no SDK backend"}))

    def test_a_backend_without_the_surface_is_a_503_not_a_500(self):
        class Older:
            sessions = {}
        code, resp, _ = self._with(Older(), "/keysource")
        self.assertEqual(code, 503)
        self.assertFalse(resp["ok"])

    def test_a_raising_status_is_a_fixed_note_never_the_exception_text(self):
        fake = _StatusFake()
        fake.keysource_status = lambda: (_ for _ in ()).throw(RuntimeError("boom " + NEW_KEY))
        code, resp, _ = self._with(fake, "/keysource")
        self.assertEqual(code, 200)
        self.assertEqual(resp, {"ok": False, "error": "API credential source failed"})

    def test_a_key_source_error_keeps_its_own_safe_sentence(self):
        fake = _StatusFake()
        fake.keysource_status = lambda: (_ for _ in ()).throw(ks.KeySourceError("Cannot read the configured API key source"))
        code, resp, _ = self._with(fake, "/keysource")
        self.assertEqual(resp, {"ok": False, "error": "Cannot read the configured API key source"})

    def test_the_read_needs_the_serve_token(self):
        with mock.patch.object(self.km, "_sdk", lambda: _StatusFake()):
            code, _ = self._call("/keysource", token=False)
        self.assertEqual(code, 403)


class KeysourceProbeRoute(_Routes):
    def test_it_runs_one_probe_and_wakes_the_pusher_when_something_lifted(self):
        fake = _StatusFake()
        code, resp, woke = self._with(fake, "/keysource/probe", "POST", {})
        self.assertEqual(code, 200)
        self.assertEqual(resp, fake.probe)
        self.assertEqual(fake.probed, 1)
        self.assertEqual(woke, [1], "held sessions are launching: the board changed")

    def test_a_probe_that_lifts_nothing_wakes_nothing(self):
        fake = _StatusFake(probe={"ok": False, "note": "1Password credential retrieval failed", "lifted": [],
                                  "kind": "op", "sourceFp": "abc"})
        code, resp, woke = self._with(fake, "/keysource/probe", "POST", {})
        self.assertEqual(code, 200)
        self.assertFalse(resp["ok"])
        self.assertEqual(woke, [])

    def test_no_backend_is_a_503(self):
        code, resp, _ = self._with(None, "/keysource/probe", "POST", {})
        self.assertEqual((code, resp), (503, {"ok": False, "error": "no SDK backend"}))

    def test_a_raising_probe_is_ok_false_with_a_fixed_note(self):
        fake = _StatusFake()
        fake.keysource_probe = lambda: (_ for _ in ()).throw(RuntimeError("boom " + NEW_KEY))
        code, resp, woke = self._with(fake, "/keysource/probe", "POST", {})
        self.assertEqual(code, 200)
        self.assertEqual(resp, {"ok": False, "note": "API credential source failed", "lifted": []})
        self.assertEqual(woke, [])

    def test_the_probe_needs_the_serve_token(self):
        with mock.patch.object(self.km, "_sdk", lambda: _StatusFake()):
            code, _ = self._call("/keysource/probe", "POST", {}, token=False)
        self.assertEqual(code, 403)

    def test_get_on_the_probe_path_is_not_a_read(self):
        code, _, _ = self._with(_StatusFake(), "/keysource/probe")
        self.assertNotEqual(code, 200, "only POST retrieves; a GET of the probe path is no door")


class _CycleFake:
    """The /keycycle surface with a probe that always says a session needs the key, so the route's
    resolve runs; what it resolves with is recorded."""

    def __init__(self, source):
        self.source = source
        self.sessions = {"s-web": object()}
        self.cycled = []

    def _work_key_source(self):
        return self.source

    def _work_key_and_source(self, selected=None):
        return (selected or self.source).resolve(), self.source.kind

    def cycle_key(self, sid, probe=False, expected_source_fp=None, current_key_fp=None, resolve_error=None):
        if probe:
            return "cycle"
        self.cycled.append((sid, current_key_fp, resolve_error))
        return "cycling"


class KeycycleForgetsTheMemo(_Routes):
    def test_a_cycle_reads_the_store_even_inside_the_reuse_window(self):
        source = ks.KeySource("op", REF)
        fake = _CycleFake(source)
        ks.forget_resolved()
        try:
            with mock.patch.dict(os.environ, {"ROMP_OP_REUSE_S": "60"}), \
                 mock.patch.object(ks.KeySource, "_op_read", side_effect=[OLD_KEY, NEW_KEY]) as op:
                self.assertEqual(source.resolve(), OLD_KEY)
                self.assertEqual(source.resolve(), OLD_KEY)
                self.assertEqual(op.call_count, 1, "the memo is live: a second resolve inside the window reads nothing")
                code, resp, woke = self._with(fake, "/keycycle", "POST", {"sessions": ["web"]})
                self.assertEqual(code, 200)
                self.assertEqual(resp["rows"], [{"session": "web", "status": "cycling"}])
                self.assertEqual(op.call_count, 2, "the cycle went to the store: the memo would have hidden the rotation")
                self.assertEqual(fake.cycled, [("s-web", ks.fingerprint(NEW_KEY), None)],
                                 "the sessions are compared against the ROTATED key")
                self.assertEqual(woke, [1])
                self.assertNotIn(NEW_KEY, json.dumps(resp))
                self.assertNotIn(OLD_KEY, json.dumps(resp))
        finally:
            ks.forget_resolved()

    def test_a_status_read_forgets_nothing_and_retrieves_nothing(self):
        source = ks.KeySource("op", REF)
        fake = _CycleFake(source)
        fake.sessions = {}
        ks.forget_resolved()
        try:
            with mock.patch.dict(os.environ, {"ROMP_OP_REUSE_S": "60"}), \
                 mock.patch.object(ks.KeySource, "_op_read", side_effect=[OLD_KEY, NEW_KEY]) as op:
                self.assertEqual(source.resolve(), OLD_KEY)
                code, resp, _ = self._with(fake, "/keycycle", "POST", {"sessions": []})
                self.assertEqual(code, 200)
                self.assertEqual(op.call_count, 1, "a read names no session: nothing to resolve, nothing to forget")
                self.assertEqual(source.resolve(), OLD_KEY, "the memo survived the read")
                self.assertEqual(op.call_count, 1)
        finally:
            ks.forget_resolved()


class HoldSweepTick(_Routes):
    def test_the_tick_runs_the_backends_sweep_and_reports_the_lifted_count(self):
        class Fake:
            swept = 0

            def keysrc_hold_sweep(self):
                self.swept += 1
                return 2
        fake = Fake()
        with mock.patch.object(self.km, "_sdk", lambda: fake):
            self.assertEqual(self.km._keysrc_hold_sweep_tick(), 2)
        self.assertEqual(fake.swept, 1)

    def test_no_backend_or_no_surface_sweeps_nothing(self):
        with mock.patch.object(self.km, "_sdk", lambda: None):
            self.assertEqual(self.km._keysrc_hold_sweep_tick(), 0)
        with mock.patch.object(self.km, "_sdk", lambda: object()):
            self.assertEqual(self.km._keysrc_hold_sweep_tick(), 0)

    def test_the_pusher_cycle_runs_the_tick_in_its_own_guard(self):
        src = inspect.getsource(self.km._pusher_cycle_jobs)
        self.assertIn("_keysrc_hold_sweep_tick()", src)
        self.assertIn('"keysrc-hold-sweep: %s\\n"', src, "a raise is logged and the rest of the cycle runs")
        self.assertLess(src.index("_idle_queue_drive_tick(now, tmux)"), src.index("_keysrc_hold_sweep_tick()"),
                        "after the idle-queue drive, the other stand-down site for a launch error")


if __name__ == "__main__":
    unittest.main()
