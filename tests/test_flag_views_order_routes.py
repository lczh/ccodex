#!/usr/bin/env python3
"""POST /flag, /views and /order (the v1.3.16 audit's P1.6/P2.17): the Obsidian timeline's state
writers, routed through the kernel's LOCKED, canonicalizing setters instead of raw whole-file
replaces that lost concurrent fields, deleted sibling rows, and recreated migrated TIDs — muting
and postal ISOLATION are safety state. Drives the REAL Handler over HTTP (the
test_rename_route.py pattern). Synthetic only."""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()

# PRIVATE synthetic sid (the goal-store fixture rule, tests/test_model_fallback_card.py
# DedupeBackstop): at run time every module shares ONE state dir (each import of "romp_judge"/
# "romp_kernel" re-executes the SAME module objects, so the last importer's tmpdir serves the
# whole run). These tests arm hideFromFeed/postalServiceOff through the REAL setters — under the
# shared placeholder sid, the leftover flags took the judge suite's sessions out of task tracking
# (run_plan/sweep skip _hidden_from_feed sessions): 15 test_judge failures, full-suite only.
SID = "66666666-7777-8888-9999-000000000000"


def _scrub_state():
    """Remove every state file these tests write through the REAL setters — leftovers leak into
    the run-wide shared state dir (see the SID note above); writers call this in setUp AND
    tearDown so nothing survives the module."""
    import shutil
    shutil.rmtree(km.jd.STATE / "pending-ui-ops", ignore_errors=True)
    for name in ("pending-ui-ops.jsonl", "pending-ui-ops.replay.jsonl",
                 "session-flags.json", "timeline-views.json"):
        try:
            (km.jd.STATE / name).unlink()
        except OSError:
            pass
    km._flags_cache.clear()


class StateWriteRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.flags, self.views, self.orders = [], [], []
        self._saved = (km._tmux_sessions, km._live_names, km._mark_views_dirty,
                       km._set_session_flag, km._set_notify_session,
                       km._set_timeline_views, km._merge_and_write_session_order)
        km._tmux_sessions = lambda: {}
        km._live_names = lambda tm: {"web": SID}
        km._mark_views_dirty = lambda: None
        km._set_session_flag = lambda sid, flag, value: self.flags.append((sid, flag, value))
        km._set_notify_session = lambda sid, value: self.flags.append((sid, "notify", value))
        km._set_timeline_views = lambda v, base_rev=None: (self.views.append(v), (True, 1))[1]
        km._merge_and_write_session_order = lambda o: self.orders.append(o)

    def tearDown(self):
        (km._tmux_sessions, km._live_names, km._mark_views_dirty,
         km._set_session_flag, km._set_notify_session,
         km._set_timeline_views, km._merge_and_write_session_order) = self._saved

    def _post(self, path, body, raw=None):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path),
            data=(raw if raw is not None else json.dumps(body).encode()),
            headers={"Content-Type": "application/json",
                     "X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode())
            except Exception:
                return e.code, {}

    def test_flag_routes_to_the_locked_setter_by_name_or_sid(self):
        st, r = self._post("/flag", {"target": "web", "flag": "hideFromFeed", "value": True})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(self.flags, [(SID, "hideFromFeed", True)])
        st, r = self._post("/flag", {"target": SID, "flag": "notify", "value": False})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(self.flags[-1], (SID, "notify", False),
                         "the tri-state bell rides its OWN setter, same as the WS op")

    def test_flag_value_takes_a_real_boolean_only(self):
        st, r = self._post("/flag", {"target": "web", "flag": "hideFromFeed", "value": "false"})
        self.assertEqual(st, 400, r)
        self.assertIn("boolean", r.get("error") or "")
        self.assertEqual(self.flags, [], "bool('false') must never UNDO a mute")

    def test_views_route_to_the_locked_normalizing_setter(self):
        blob = {"active": "all", "tags": [{"id": "g", "name": "g", "members": []}]}
        st, r = self._post("/views", {"views": blob})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(self.views, [blob])
        st, r = self._post("/views", {"views": "not-a-dict"})
        self.assertEqual(st, 400)

    def test_order_routes_to_the_locked_merge(self):
        st, r = self._post("/order", {"order": ["a", "b"]})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(self.orders, [["a", "b"]],
                         "the MERGE keeps untouched lanes' slots — never the raw overwrite")
        st, r = self._post("/order", {"order": ["a", 7]})
        self.assertEqual(st, 400)

    def test_non_object_bodies_are_400s(self):
        for path in ("/flag", "/views", "/order"):
            st, r = self._post(path, None, raw=b"[1]")
            self.assertEqual(st, 400, "%s: %r" % (path, r))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class ViewsRevisionAndOps(unittest.TestCase):
    """the v1.3.17 audit's P2.15 (whole-blob last-writer-wins even through the lock) and the
    P1.5 replay grammar: rev-gated blobs and server-applied targeted ops, against the REAL
    setter and state file."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self._dirty = km._mark_views_dirty
        km._mark_views_dirty = lambda: None
        _scrub_state()

    def tearDown(self):
        km._mark_views_dirty = self._dirty
        _scrub_state()

    def _post(self, path, body):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path), data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode())
            except Exception:
                return e.code, {}

    def test_a_stale_whole_blob_write_is_refused_not_merged_over(self):
        # writer A commits a tag at rev 0; writer B, still holding the rev-0 world, posts a
        # blob WITHOUT A's tag — the audit's exact lost-update schedule. B must be refused.
        a = {"active": "all", "tags": [{"id": "ta", "name": "alpha", "color": "",
                                        "members": [SID]}], "baseRev": 0}
        st, r = self._post("/views", {"views": a})
        self.assertEqual((st, r.get("ok")), (200, True))
        b = {"active": "all", "tags": [], "baseRev": 0}   # stale: never saw A's write
        st, r = self._post("/views", {"views": b})
        self.assertEqual(st, 409, "a stale baseRev is refused, never last-writer-wins")
        v = km._timeline_views()
        self.assertEqual([t_["name"] for t_ in v["tags"]], ["alpha"],
                         "writer A's tag survives the stale writer")

    def test_the_payload_clone_carries_the_rev_and_commits_against_it(self):
        st, r = self._post("/views", {"views": {"active": "all", "tags": [], "baseRev": 0}})
        self.assertEqual(st, 200)
        cur = km._timeline_views()                     # rev rides every read
        self.assertEqual(cur.get("rev"), 1)
        clone = json.loads(json.dumps(cur))            # a client edits its payload clone
        clone["tags"] = [{"id": "tb", "name": "beta", "color": "", "members": []}]
        st, r = self._post("/views", {"views": clone})   # echoed rev = the CAS base
        self.assertEqual((st, r.get("rev")), (200, 2))

    def test_targeted_ops_compose_instead_of_overwriting(self):
        self._post("/views", {"views": {"active": "all",
                                        "tags": [{"id": "tc", "name": "gamma", "color": "",
                                                  "members": []}]}})
        st, r = self._post("/views", {"ops": [{"tag": "gamma", "add": [SID]}]})
        self.assertEqual((st, r.get("ok")), (200, True))
        st, r = self._post("/views", {"ops": [{"tag": "gamma", "rename": "delta",
                                               "color": "#123456"}]})
        self.assertEqual(st, 200)
        v = km._timeline_views()
        t_ = v["tags"][0]
        self.assertEqual((t_["name"], t_["color"]), ("delta", "#123456"))
        self.assertEqual(t_["members"], [{"host": "", "sid": SID}],
                         "both editors' gestures landed — nothing overwrote")
        st, r = self._post("/views", {"ops": [{"tag": "delta", "remove": [SID]},
                                              {"tag": "delta", "delete": True}]})
        self.assertEqual(st, 200)
        self.assertEqual(km._timeline_views()["tags"], [])

    def test_ops_speak_the_remote_member_spelling(self):
        # the r45 verification: clients post viewer-relative "host:sid" strings for REMOTE
        # members — the bare-sid wrapper stored a corrupt {"host":"","sid":"host:sid"} pair on
        # add, and remove could never match one
        self._post("/views", {"ops": [{"create": {"id": "tr", "name": "remote-taggable",
                                                  "color": "", "members": []}}]})
        st, r = self._post("/views", {"ops": [{"tag": "tr", "add": ["TESTHOST:" + SID]}]})
        self.assertEqual(st, 200)
        v = km._timeline_views()
        self.assertEqual(v["tags"][0]["members"], [{"host": "TESTHOST", "sid": SID}],
                         "the canonical pair, not a corrupt home-sid wrap")
        st, r = self._post("/views", {"ops": [{"tag": "tr", "remove": ["TESTHOST:" + SID]}]})
        self.assertEqual(st, 200)
        self.assertEqual(km._timeline_views()["tags"][0]["members"], [],
                         "a remote member is removable through the same spelling")

    def test_an_op_on_a_deleted_tag_drops_quietly(self):
        st, r = self._post("/views", {"ops": [{"tag": "ghost", "add": [SID]}]})
        self.assertEqual((st, r.get("ok")), (200, True),
                         "a replayed gesture over a deleted tag has nothing to do")


class BoolGates(unittest.TestCase):
    """the v1.3.17 audit's P2.13: bool("false") is True — four route boundaries coerced instead
    of rejecting."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _post(self, path, body):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path), data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_notify_all_rejects_a_string_false(self):
        recorded = []
        saved = km._set_notify_all
        km._set_notify_all = lambda on: recorded.append(on)
        try:
            st, _ = self._post("/notify-all", {"on": "false"})
            self.assertEqual(st, 400)
            self.assertEqual(recorded, [], "the string never reached the setter")
            st, _ = self._post("/notify-all", {"on": True})
            self.assertEqual(st, 200)
            self.assertEqual(recorded, [True])
        finally:
            km._set_notify_all = saved

    def test_autoupdate_rejects_a_string_false(self):
        st, body = self._post("/tunnels/autoupdate", {"on": "false"})
        self.assertEqual(st, 400)
        self.assertIn("boolean", body)

    def test_checkin_rejects_a_string_false(self):
        st, body = self._post("/tunnels/checkin", {"host": "TESTHOST", "on": "false"})
        self.assertEqual(st, 400)
        self.assertIn("boolean", body)

    def test_new_session_rejects_a_string_mkdir(self):
        st, body = self._post("/new", {"name": "wontexist", "dir": "/tmp", "mkdir": "true"})
        self.assertEqual(st, 400)
        self.assertIn("boolean", body)


class FeedJsonIsAPureRead(unittest.TestCase):
    """the v1.3.17 audit's P2.12: a cold GET /feed.json entered the stateful cold-build path —
    advancing notification baselines, pushing badges, pruning notify-cards.json — despite its
    read-only contract."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _get(self, path):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path),
            headers={"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())

    def test_a_cold_get_builds_without_supervisor_transitions(self):
        saved = (km.build_feed, km._cached_feed, km._tmux_sessions, list(km._built_feed))
        km._built_feed[:] = [None, None, 0, 0]        # cold kernel
        km._tmux_sessions = lambda: {}
        km.build_feed = lambda now, tmux: {"items": [], "marker": "pure-build"}

        def poisoned(*a, **kw):
            raise AssertionError("GET /feed.json must never enter the stateful cold-build path")

        km._cached_feed = poisoned
        try:
            st, body = self._get("/feed.json")
            self.assertEqual(st, 200)
            self.assertEqual(body.get("marker"), "pure-build")
        finally:
            km.build_feed, km._cached_feed, km._tmux_sessions = saved[0], saved[1], saved[2]
            km._built_feed[:] = saved[3]

    def test_repeated_cold_gets_build_once(self):
        # the r45 verification: every cold GET paid the full ~1.5s build. The pure path gets
        # its OWN short cache — never _built_feed, whose diff baseline a read must not seed.
        builds = []
        saved = (km.build_feed, km._tmux_sessions, list(km._built_feed), list(km._PURE_FEED))
        km._built_feed[:] = [None, None, 0, 0]
        km._PURE_FEED[:] = [None, 0.0]
        km._tmux_sessions = lambda: {}
        km.build_feed = lambda now, tmux: (builds.append(1), {"items": [], "n": len(builds)})[1]
        try:
            st, b1 = self._get("/feed.json")
            st, b2 = self._get("/feed.json")
            self.assertEqual(len(builds), 1, "the second cold GET serves the pure cache")
            self.assertEqual(b1.get("buildId"), 0, "an unversioned read, marked as such")
            self.assertEqual(km._built_feed[1], None,
                             "the pure cache never seeds the pusher's diff baseline")
        finally:
            km.build_feed, km._tmux_sessions = saved[0], saved[1]
            km._built_feed[:] = saved[2]
            km._PURE_FEED[:] = saved[3]

    def test_a_warmed_build_serves_as_is(self):
        saved = (km.build_feed, list(km._built_feed))
        km._built_feed[:] = ["sig", {"items": [], "marker": "warmed"}, 1.0, 1.0]

        def poisoned(*a, **kw):
            raise AssertionError("a warmed kernel never rebuilds for a GET")

        km.build_feed = poisoned
        try:
            st, body = self._get("/feed.json")
            self.assertEqual((st, body.get("marker")), (200, "warmed"))
        finally:
            km.build_feed = saved[0]
            km._built_feed[:] = saved[1]


class UiOpSpoolReplay(unittest.TestCase):
    """the v1.3.17 audit's P1.5, kernel half: gestures an Electron surface queued while no
    kernel ran replay through the locked, canonicalizing setters at boot and every supervisor
    pass — never a raw whole-file write."""

    def setUp(self):
        _scrub_state()
        self._dirty = km._mark_views_dirty
        km._mark_views_dirty = lambda: None

    def tearDown(self):
        km._mark_views_dirty = self._dirty
        _scrub_state()

    def test_flag_and_views_ops_replay_through_the_locked_setters(self):
        sp = km.jd.STATE / "pending-ui-ops.jsonl"
        sp.parent.mkdir(parents=True, exist_ok=True)
        with sp.open("a") as fh:
            fh.write(json.dumps({"op": "flag", "target": SID, "flag": "postalServiceOff",
                                 "value": True}) + "\n")
            fh.write(json.dumps({"op": "views", "ops": [{"create": {
                "id": "sp1", "name": "spooled", "color": "", "members": [SID]}}]}) + "\n")
        km._replay_ui_op_spool()
        flags = json.loads((km.jd.STATE / "session-flags.json").read_text())
        self.assertTrue(flags[SID]["postalServiceOff"])
        v = km._timeline_views()
        self.assertEqual([t_["name"] for t_ in v["tags"]], ["spooled"])
        self.assertEqual(v["tags"][0]["members"], [{"host": "", "sid": SID}])
        self.assertFalse(sp.exists(), "a replayed spool is consumed")
        self.assertFalse((km.jd.STATE / "pending-ui-ops.replay.jsonl").exists())

    def test_a_crashed_replay_resumes_and_a_torn_line_drops(self):
        work = km.jd.STATE / "pending-ui-ops.replay.jsonl"
        work.parent.mkdir(parents=True, exist_ok=True)
        with work.open("a") as fh:
            fh.write(json.dumps({"op": "flag", "target": SID, "flag": "hideFromFeed",
                                 "value": True}) + "\n")
            fh.write('{"op": "flag", "target": "')   # a writer died mid-append
        km._replay_ui_op_spool()
        flags = json.loads((km.jd.STATE / "session-flags.json").read_text())
        self.assertTrue(flags[SID]["hideFromFeed"],
                        "a replay that died before unlink re-runs its idempotent ops")
        self.assertFalse(work.exists())

    def test_a_string_boolean_never_replays(self):
        sp = km.jd.STATE / "pending-ui-ops.jsonl"
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps({"op": "flag", "target": SID, "flag": "hideFromFeed",
                                  "value": "false"}) + "\n")
        km._replay_ui_op_spool()
        self.assertFalse((km.jd.STATE / "session-flags.json").exists(),
                         "the replay is a boundary too — string booleans are refused (P2.13)")


class PerFileOpSpool(unittest.TestCase):
    """the v1.3.18 audit's P1 pair: the single append-file's rename-aside handoff raced a
    writer onto an unlinked inode, and a replay failure unlinked EVERY queued op. One file per
    op now: only successfully applied ops are consumed; a failed op's file is retained."""

    def setUp(self):
        _scrub_state()
        self.spdir = km.jd.STATE / "pending-ui-ops"
        self.spdir.mkdir(parents=True, exist_ok=True)
        self._dirty = km._mark_views_dirty
        km._mark_views_dirty = lambda: None

    def tearDown(self):
        km._mark_views_dirty = self._dirty
        _scrub_state()

    def test_op_files_apply_in_name_order_and_are_consumed(self):
        (self.spdir / "100-aa.json").write_text(json.dumps(
            {"op": "views", "ops": [{"create": {"id": "s1", "name": "first", "color": "",
                                                "members": []}}]}))
        (self.spdir / "200-bb.json").write_text(json.dumps(
            {"op": "views", "ops": [{"tag": "first", "rename": "renamed"}]}))
        km._replay_ui_op_spool()
        v = km._timeline_views()
        self.assertEqual([x["name"] for x in v["tags"]], ["renamed"],
                         "ordered replay: the create landed before the rename")
        self.assertEqual(list(self.spdir.glob("*.json")), [], "applied ops are consumed")

    def test_a_failed_op_is_retained_and_retries(self):
        (self.spdir / "100-cc.json").write_text(json.dumps(
            {"op": "flag", "target": SID, "flag": "hideFromFeed", "value": True}))

        def boom(*a, **kw):
            raise OSError(5, "setter died")

        saved = km._set_session_flag
        km._set_session_flag = boom
        try:
            km._replay_ui_op_spool()
        finally:
            km._set_session_flag = saved
        self.assertTrue((self.spdir / "100-cc.json").exists(),
                        "a failed gesture is never silently deleted (the v1.3.18 audit's P1)")
        km._replay_ui_op_spool()
        self.assertFalse((self.spdir / "100-cc.json").exists(), "…and it retries to success")
        flags = json.loads((km.jd.STATE / "session-flags.json").read_text())
        self.assertTrue(flags[SID]["hideFromFeed"])

    def test_an_unparseable_op_file_drops_loudly_and_blocks_nothing(self):
        (self.spdir / "100-dd.json").write_text('{"op": "fl')
        (self.spdir / "200-ee.json").write_text(json.dumps(
            {"op": "flag", "target": SID, "flag": "postalServiceOff", "value": True}))
        km._replay_ui_op_spool()
        self.assertEqual(list(self.spdir.glob("*.json")), [])
        flags = json.loads((km.jd.STATE / "session-flags.json").read_text())
        self.assertTrue(flags[SID]["postalServiceOff"])

    def test_the_legacy_append_spool_is_still_consumed_once(self):
        (km.jd.STATE / "pending-ui-ops.jsonl").write_text(json.dumps(
            {"op": "flag", "target": SID, "flag": "hideFromFeed", "value": True}) + "\n")
        km._replay_ui_op_spool()
        flags = json.loads((km.jd.STATE / "session-flags.json").read_text())
        self.assertTrue(flags[SID]["hideFromFeed"])
        self.assertFalse((km.jd.STATE / "pending-ui-ops.jsonl").exists())
