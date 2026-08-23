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

    def test_the_claim_lock_is_load_bearing_under_a_slow_live_check(self):
        # removing _name_claims_lock passed every test (the adversarial review, 2026-08-21): with
        # a deliberately slow live-store check, two unserialized claims BOTH pass the check and
        # both win — the lock is what makes check+claim one step
        import time as _t

        def slow_live(tmux):
            _t.sleep(0.05)
            return {}
        wins = []
        gate = threading.Barrier(2)

        def contender():
            gate.wait(5)
            wins.append(km._claim_name("web"))
        with mock.patch.object(km, "_live_names", side_effect=slow_live):
            with mock.patch.object(km, "_tmux_sessions", return_value={}):
                t1 = threading.Thread(target=contender)
                t2 = threading.Thread(target=contender)
                t1.start(); t2.start()
                t1.join(10); t2.join(10)
        self.assertEqual(sorted(wins), [False, True],
                         "exactly one winner even when the live check is slow")

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

    def test_rename_refuses_a_claimed_name_and_the_backend_never_writes(self):
        # rename wrote registries blindly (the v1.3.10 audit's P2): it now takes the same
        # reservation create, fork, and promotion hold
        sent = []
        client = {"send": sent.append}
        be = mock.MagicMock()
        be.rename.return_value = True
        with mock.patch.object(km, "_tmux_sessions", return_value={}):
            with mock.patch.object(km, "_kernel_knows", return_value=True):
                with mock.patch.object(km.Sessions, "backend_for", return_value=be):
                    with km._name_claims_lock:
                        km._NAME_CLAIMS.add("web")
                    try:
                        km._drive({"type": "renameSession",
                                   "id": "11111111-2222-3333-4444-555555555555",
                                   "name": "web"}, client)
                    finally:
                        with km._name_claims_lock:
                            km._NAME_CLAIMS.discard("web")
        self.assertTrue(any("already running or being created" in m for m in sent),
                        "renaming onto a claimed name warns instead of writing blindly: %r" % sent)
        self.assertFalse(be.rename.called, "the backend never writes for a refused rename")

    def test_a_permitted_rename_claims_then_releases(self):
        sent = []
        client = {"send": sent.append}
        be = mock.MagicMock()
        be.rename.return_value = True
        with mock.patch.object(km, "_tmux_sessions", return_value={}):
            with mock.patch.object(km, "_kernel_knows", return_value=True):
                with mock.patch.object(km.Sessions, "backend_for", return_value=be):
                    km._drive({"type": "renameSession",
                               "id": "11111111-2222-3333-4444-555555555555",
                               "name": "web"}, client)
        self.assertTrue(be.rename.called)
        self.assertTrue(any('"renamed"' in m for m in sent), sent)
        with km._name_claims_lock:
            self.assertNotIn("web", km._NAME_CLAIMS, "the reservation is released after the write")

    def test_revive_refuses_a_claimed_name_loudly(self):
        # revive resumed a dead session under a name another live sid already owned (the v1.3.11
        # audit's P2) — it now takes the same reservation and answers with reviveFailed
        events = []
        with mock.patch.object(km, "_name_of", return_value="web"):
            with mock.patch.object(km, "_tmux_sessions", return_value={}):
                with mock.patch.object(km, "_send_to_view",
                                       side_effect=lambda *a, **kw: events.append(a)):
                    with mock.patch.object(km, "_revive_session_claimed",
                                           side_effect=AssertionError("must not revive past the claim")):
                        with km._name_claims_lock:
                            km._NAME_CLAIMS.add("web")
                        try:
                            km._revive_session("11111111-2222-3333-4444-555555555555")
                        finally:
                            with km._name_claims_lock:
                                km._NAME_CLAIMS.discard("web")
        self.assertTrue(any(a and a[1].get("type") == "reviveFailed" for a in events),
                        "the asker hears the refusal: %r" % events)

    def test_a_refused_tmux_rename_is_never_acked(self):
        # tmux's nonzero rc was swallowed two layers deep, so the UI showed the new name while
        # tmux kept the old one (the v1.3.11 audit's P2)
        sent = []
        client = {"send": sent.append}
        be = mock.MagicMock()
        be.rename.side_effect = lambda sid, new: km._rename_session(sid, new) is not None
        with mock.patch.object(km, "_tmux_sessions", return_value={}):
            with mock.patch.object(km, "_kernel_knows", return_value=True):
                with mock.patch.object(km, "_tmux_name_of", return_value="old"):
                    with mock.patch.object(km._TMUX, "rename_by_name", return_value=False):
                        with mock.patch.object(km.Sessions, "backend_for", return_value=be):
                            km._drive({"type": "renameSession",
                                       "id": "11111111-2222-3333-4444-555555555555",
                                       "name": "web"}, client)
        self.assertFalse(any('"renamed"' in m for m in sent),
                         "a rename tmux refused is never acked: %r" % sent)
        self.assertTrue(any("did not take" in m for m in sent),
                        "the asker hears the refusal instead of a 90s timeout: %r" % sent)

    def test_a_dead_codex_rename_reaches_the_codex_registry(self):
        # the shared file moved while the durable Codex registry kept the old name (the v1.3.12
        # audit's P2) — the dead-tab branch now renames the registry too
        cx = mock.MagicMock()
        cx._session.return_value = object()
        set_names = []
        with mock.patch.object(km, "_tmux_name_of", return_value=""):
            with mock.patch.object(km, "_codex", return_value=cx):
                with mock.patch.object(km, "_set_name",
                                       side_effect=lambda s, n2: (set_names.append(n2), True)[1]):
                    out = km._rename_session("11111111-2222-3333-4444-555555555555", "webby")
        self.assertEqual(out, "webby")
        cx.rename.assert_called_once_with("11111111-2222-3333-4444-555555555555", "webby")
        self.assertEqual(set_names, ["webby"])

    def test_a_raising_codex_registry_rename_publishes_nothing(self):
        # r28 ordering: the durable registry write goes FIRST — when it raises, the shared names
        # file must not have moved, or the two stores disagree forever (the v1.3.12 audit's P2,
        # atomicity half)
        cx = mock.MagicMock()
        cx._session.return_value = object()
        cx.rename.side_effect = OSError("disk full")
        set_names = []
        with mock.patch.object(km, "_tmux_name_of", return_value=""):
            with mock.patch.object(km, "_codex", return_value=cx):
                with mock.patch.object(km, "_set_name", side_effect=lambda s, n2: set_names.append(n2)):
                    out = km._rename_session("11111111-2222-3333-4444-555555555555", "webby")
        self.assertIsNone(out, "a failed registry write is never acked")
        self.assertEqual(set_names, [], "the shared file must not move when the registry raised")

    def test_a_live_tmux_rename_writes_the_names_file_before_returning(self):
        # the tmux hook publishes ASYNCHRONOUSLY, and the claim released before it ran — the
        # target name was briefly claimable by a rival (the v1.3.12 audit's P2)
        set_names = []
        with mock.patch.object(km, "_tmux_name_of", return_value="old"):
            with mock.patch.object(km._TMUX, "rename_by_name", return_value=True):
                with mock.patch.object(km, "_set_name",
                                       side_effect=lambda s, n2: (set_names.append(n2), True)[1]):
                    out = km._rename_session("11111111-2222-3333-4444-555555555555", "webby")
        self.assertEqual(out, "webby")
        self.assertEqual(set_names, ["webby"], "the names file lands synchronously")

    def test_a_raising_codex_create_answers_the_picker(self):
        # the r28 verification: the generic dispatcher handler logged the raise to stderr only —
        # the picker's "Opening…" cue got no answer
        sent = []
        client = {"send": sent.append, "wid": "w1"}
        with mock.patch.object(km, "_codex_ready", return_value=True):
            with mock.patch.object(km, "_create_codex_session",
                                   side_effect=RuntimeError("registry down")):
                with mock.patch.object(km, "_resolve_create_dir", return_value=("/tmp", "")):
                    with mock.patch.object(km, "_tmux_sessions", return_value={}):
                        with mock.patch.object(km, "_live_names", return_value={}):
                            km.Handler._dispatch_ws(object.__new__(km.Handler),
                                                    {"type": "createSession", "name": "webby",
                                                     "backend": "codex", "dir": "/tmp"}, client)
        self.assertTrue(any('"warn"' in m and "registry down" in m for m in sent),
                        "the failure reaches the asker, not just stderr: %r" % sent)

    def test_a_raising_send_never_erases_the_create_error(self):
        # the r30 verification: with the warn attempted FIRST, a raising send erased the
        # creation error from every record — stderr goes first now
        import io
        err = io.StringIO()

        def boom_send(_):
            raise BrokenPipeError("client gone")
        client = {"send": boom_send, "wid": "w1"}
        with mock.patch.object(km, "_codex_ready", return_value=True):
            with mock.patch.object(km, "_create_codex_session",
                                   side_effect=RuntimeError("registry down")):
                with mock.patch.object(km, "_resolve_create_dir", return_value=("/tmp", "")):
                    with mock.patch.object(km, "_tmux_sessions", return_value={}):
                        with mock.patch.object(km, "_live_names", return_value={}):
                            with mock.patch.object(km.sys, "stderr", err):
                                km.Handler._dispatch_ws(object.__new__(km.Handler),
                                                        {"type": "createSession", "name": "webby",
                                                         "backend": "codex", "dir": "/tmp"},
                                                        client)
        self.assertIn("registry down", err.getvalue(),
                      "stderr keeps the error even when the send raises")

    def test_a_live_codex_session_joins_the_alive_set_by_its_stable_sid(self):
        # the v1.3.13 audit's P1, the kernel half: liveness keys on the SID while discovery used
        # to emit the app-server TID as identity — live Codex rows never joined the alive set,
        # so lanes vanished and the picker offered a not-running row
        sid = "11111111-2222-3333-4444-555555555555"
        row = (sid, __import__("pathlib").Path("/tmp/x.jsonl"), sid, "webby")
        with mock.patch.object(km.jd, "discover", return_value=[row]):
            alive = km._alive_sessions(1781100000, {sid: {"backend": "codex"}})
        self.assertEqual([a["sid"] for a in alive], [sid],
                         "the discovery identity slot must equal the liveness key")

    def test_a_dead_codex_session_labels_itself_codex(self):
        # the v1.3.13 audit's P3: the live-only owns() probe read every STOPPED Codex session
        # as "tmux", and every label-keyed surface mis-described it
        cx = mock.MagicMock()
        cx.owns.return_value = False               # dead: live-only ownership says no
        cx._session.return_value = object()        # but the durable registry knows it
        with mock.patch.object(km, "_sdk", return_value=None):
            with mock.patch.object(km, "_codex", return_value=cx):
                self.assertEqual(km._session_backend("11111111-2222-3333-4444-555555555555",
                                                     None), "codex")

    def test_a_dead_codex_revive_falls_back_to_the_registry_name_not_the_sid(self):
        # the v1.3.13 audit's P2, executed there: with the names file missing, the bare
        # `_name_of(sid) or sid` fallback flowed into CodexBackend.resume, which PERSISTED the
        # sid over the durable name "history" and freed "history" for a rival claim
        sid = "11111111-2222-3333-4444-555555555555"
        cx = mock.MagicMock()
        cx._session.return_value = mock.MagicMock(name="row")
        cx._session.return_value.name = "history"
        cx.resume.return_value = True
        with mock.patch.object(km, "_name_of", return_value=""):
            with mock.patch.object(km, "_codex", return_value=cx):
                with mock.patch.object(km, "_codex_ready", return_value=True):
                    with mock.patch.object(km, "_sdk", return_value=None):
                        with mock.patch.object(km, "_commands_for_cwd", return_value=None):
                            with mock.patch.object(km, "_cwd_of", return_value="/tmp"):
                                with mock.patch.object(km, "_reveal_chat_for"):
                                    with mock.patch.object(km, "_mark_views_dirty"):
                                        with mock.patch.object(km, "_push_session_now"):
                                            km._revive_session(sid, None)
        self.assertTrue(cx.resume.called)
        self.assertEqual(cx.resume.call_args[0][0], "history",
                         "the registry's own name, never the sid, reaches resume")

    def test_a_dead_rename_over_corrupt_identity_bytes_heals_and_publishes(self):
        # the v1.3.13 audit's P2: _set_name silently swallowed the read failure, tmux renamed,
        # the shared bytes stayed corrupt, and the "reserved" target stayed claimable
        import tempfile
        from pathlib import Path
        sid = "11111111-2222-3333-4444-555555555555"
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(km, "NAMES", Path(td)):
                (Path(td) / sid).write_bytes(b"\xff\xfe corrupt \x80")
                self.assertTrue(km._set_name(sid, "webby"),
                                "corrupt residue heals by whole rewrite, like the codex twin")
                self.assertEqual((Path(td) / sid).read_bytes(), b"webby\t\t\t\n")

    def test_a_failed_name_publish_is_never_acked(self):
        import tempfile
        from pathlib import Path
        sid = "11111111-2222-3333-4444-555555555555"
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(km, "NAMES", Path(td)):
                with mock.patch.object(km, "_atomic_write", side_effect=OSError(28, "full")):
                    self.assertFalse(km._set_name(sid, "webby"))
        with mock.patch.object(km, "_tmux_name_of", return_value="old"):
            with mock.patch.object(km._TMUX, "rename_by_name", return_value=True):
                with mock.patch.object(km, "_set_name", return_value=False):
                    self.assertIsNone(km._rename_session(sid, "webby"),
                                      "tmux moved but the shared name did not — never acked")

    def test_a_raising_revive_thread_start_answers_the_asker(self):
        # the v1.3.13 audit: the reviveFailed branch had no committed regression test, contrary
        # to the round's own every-fix-has-a-detector rule
        sid = "11111111-2222-3333-4444-555555555555"
        sent = []

        class BoomThread(km.threading.Thread):
            def start(self2):
                if self2._target is km._revive_session:
                    raise RuntimeError("no threads today")
                return super().start()
        with mock.patch.object(km.threading, "Thread", BoomThread):
            with mock.patch.object(km, "_send_to_view",
                                   side_effect=lambda v, m, wid: sent.append(m)):
                with mock.patch.object(km, "_name_of", return_value="webby"):
                    try:
                        km.Handler._dispatch_ws(object.__new__(km.Handler),
                                                {"type": "reviveSession", "id": sid},
                                                {"send": lambda m: None, "wid": "w1"})
                    except RuntimeError:
                        pass                       # the raise still propagates after the answer
        self.assertTrue(any(m.get("type") == "reviveFailed" for m in sent),
                        "the asker's loader is cleared with a reason, not left forever: %r" % sent)

    def test_a_rename_retry_with_tmux_already_at_the_target_still_publishes(self):
        # the r37 verification: after a failed publish, the retry short-circuited on live==name
        # — skipping BOTH writes — and acked the stale names file forever
        set_names = []
        with mock.patch.object(km, "_tmux_name_of", return_value="webby"):
            with mock.patch.object(km._TMUX, "rename_by_name") as rn:
                with mock.patch.object(km, "_set_name",
                                       side_effect=lambda s, n2: (set_names.append(n2), True)[1]):
                    out = km._rename_session("11111111-2222-3333-4444-555555555555", "webby")
        self.assertEqual(out, "webby")
        self.assertFalse(rn.called, "tmux already holds the target — no rename issued")
        self.assertEqual(set_names, ["webby"], "but the names file is STILL published")
        with mock.patch.object(km, "_tmux_name_of", return_value="webby"):
            with mock.patch.object(km, "_set_name", return_value=False):
                self.assertIsNone(km._rename_session("11111111-2222-3333-4444-555555555555",
                                                     "webby"),
                                  "and a publish that fails again is still never acked")

    def test_renames_are_serialized_under_one_lock(self):
        # two interleaved renames of one sid left the registry at one name and the shared file
        # at the other (the v1.3.12 audit's P2)
        sent = []
        client = {"send": sent.append}
        be = mock.MagicMock()

        def rename_asserting_serialized(sid, new):
            self.assertTrue(km._rename_serial.locked(),
                            "the backend write happens INSIDE the serializer")
            return True
        be.rename.side_effect = rename_asserting_serialized
        with mock.patch.object(km, "_tmux_sessions", return_value={}):
            with mock.patch.object(km, "_kernel_knows", return_value=True):
                with mock.patch.object(km.Sessions, "backend_for", return_value=be):
                    km._drive({"type": "renameSession",
                               "id": "11111111-2222-3333-4444-555555555555",
                               "name": "webby"}, client)
        self.assertTrue(be.rename.called)
        self.assertTrue(any('"renamed"' in m for m in sent), sent)

    def test_promotion_refuses_a_claimed_name(self):
        # promotion wrote names/ blindly (the adversarial review, 2026-08-21) — it now takes the
        # same reservation as create and fork
        with mock.patch.object(km, "_tmux_sessions", return_value={}):
            self.assertTrue(km._claim_name("web"))
            try:
                err = km._comment_promote("sid1", "t1", "web")
                self.assertIn("already running or being created", err or "")
            finally:
                km._release_name("web")

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

    def test_a_tmux_new_with_a_claimed_name_refuses_BEFORE_the_ack(self):
        # /new spawned the tmux worker and returned pending:true; the claim happened inside the
        # thread, so a collision was reported only to stderr while the caller saw success (the
        # v1.3.10 audit's P2)
        with km._name_claims_lock:
            km._NAME_CLAIMS.add("web")
        try:
            code, out = self._post("/new", json.dumps({"name": "web", "dir": "/tmp",
                                                       "backend": "tmux"}).encode())
        finally:
            with km._name_claims_lock:
                km._NAME_CLAIMS.discard("web")
        self.assertEqual(code, 200)
        body = json.loads(out)
        self.assertFalse(body.get("ok"), "the collision is the CALLER'S answer, not a stderr line")
        self.assertIn("already running or being created", body.get("error", ""))

    def test_a_failed_thread_start_releases_the_claim(self):
        # Thread.start() raising after the claim (thread/fd exhaustion) leaked the name FOREVER —
        # the worker's releasing finally never ran, and every later create/fork/rename of that
        # name got NAME_TAKEN until restart (the adversarial review, 2026-08-21)
        real_thread = km.threading.Thread

        class BoomThread(real_thread):
            def start(self):
                if getattr(self, "_target", None) is km._spawn_session_preclaimed:
                    raise RuntimeError("can't start new thread")
                return real_thread.start(self)       # the test server's own request threads
                #                                      must keep working — threading is shared
        with mock.patch.object(km.threading, "Thread", BoomThread):
            code, out = self._post("/new", json.dumps({"name": "web", "dir": "/tmp",
                                                       "backend": "tmux"}).encode())
        self.assertEqual(code, 500, "the launch failure is loud")
        with km._name_claims_lock:
            self.assertNotIn("web", km._NAME_CLAIMS,
                             "a launch that never ran must not hold the name forever")

    def test_fork_requires_parent_and_name(self):
        code, out = self._post("/fork", b"{}")
        self.assertEqual(code, 400)
        self.assertIn("parent and name", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
