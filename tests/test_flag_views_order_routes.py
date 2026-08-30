#!/usr/bin/env python3
"""POST /flag, /views and /order (the v1.3.16 audit's P1.6/P2.17): the Obsidian timeline's state
writers, routed through the kernel's LOCKED, canonicalizing setters instead of raw whole-file
replaces that lost concurrent fields, deleted sibling rows, and recreated migrated TIDs — muting
and postal ISOLATION are safety state. Drives the REAL Handler over HTTP (the
test_rename_route.py pattern). Synthetic only."""
import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from unittest import mock
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

    def test_a_failed_head_op_holds_the_queue_in_order(self):
        # the r46 verification: retrying a failed op AFTER newer gestures applied re-ordered
        # the user's actions — strict FIFO with head-of-line retry
        (self.spdir / "100-hh.json").write_text(json.dumps(
            {"op": "flag", "target": SID, "flag": "hideFromFeed", "value": True}))
        (self.spdir / "200-ii.json").write_text(json.dumps(
            {"op": "flag", "target": SID, "flag": "hideFromFeed", "value": False}))

        def boom(*a, **kw):
            raise OSError(5, "setter died")

        saved = km._set_session_flag
        km._set_session_flag = boom
        try:
            km._replay_ui_op_spool()
        finally:
            km._set_session_flag = saved
        self.assertTrue((self.spdir / "100-hh.json").exists())
        self.assertTrue((self.spdir / "200-ii.json").exists(),
                        "the LATER op never applied around the failed head — order holds")
        km._replay_ui_op_spool()
        flags = json.loads((km.jd.STATE / "session-flags.json").read_text())
        self.assertNotIn(SID, flags, "both applied in order: the un-hide is the last word")

    def test_a_persistently_failing_op_quarantines_after_five_passes(self):
        (self.spdir / "100-qq.json").write_text(json.dumps(
            {"op": "flag", "target": SID, "flag": "hideFromFeed", "value": True}))

        def boom(*a, **kw):
            raise OSError(5, "always")

        saved = km._set_session_flag
        km._set_session_flag = boom
        km._SPOOL_FAILS.clear()
        try:
            for _ in range(5):
                km._replay_ui_op_spool()
        finally:
            km._set_session_flag = saved
        self.assertFalse((self.spdir / "100-qq.json").exists())
        self.assertTrue((self.spdir / "100-qq.json.failed").exists(),
                        "quarantined loudly, never silently deleted — and the queue unwedges")

    def test_a_stage_file_is_never_read_and_never_raced(self):
        # the r46 re-verify: sweeping tmps INSIDE the spool dir raced a live writer mid-stage
        # (its rename hit ENOENT — the gesture silently lost). Writers stage in the SIBLING
        # dir now; the replay neither reads nor deletes there, so a mid-stage writer can
        # always complete its rename.
        stage = km.jd.STATE / "pending-ui-ops.stage"
        stage.mkdir(parents=True, exist_ok=True)
        (stage / "100-tt.json").write_text('{"half": ')
        km._replay_ui_op_spool()
        self.assertTrue((stage / "100-tt.json").exists(),
                        "a mid-stage writer's file is untouchable — no sweep race")
        (stage / "100-tt.json").unlink()

    def test_a_partial_legacy_conversion_keeps_only_the_remainder(self):
        # the r46 re-verify: a partial conversion left the WHOLE file behind — the converted
        # prefix re-converted next pass and already-applied gestures replayed over later state
        (km.jd.STATE / "pending-ui-ops.jsonl").write_text(
            json.dumps({"op": "flag", "target": SID, "flag": "hideFromFeed", "value": True}) + "\n"
            + json.dumps({"op": "flag", "target": SID, "flag": "hideFromFeed", "value": False}) + "\n")
        real_replace = km.os.replace
        state = {"n": 0}

        def failing_second(src, dst):
            if "0legacy" in str(dst) and str(dst).endswith(".json"):
                state["n"] += 1
                if state["n"] == 2:
                    raise OSError(28, "ENOSPC")
            return real_replace(src, dst)

        with mock.patch.object(km.os, "replace", side_effect=failing_second):
            km._replay_ui_op_spool()
        legacy = km.jd.STATE / "pending-ui-ops.jsonl"
        self.assertTrue(legacy.exists(), "the unconverted remainder survives")
        rows = [json.loads(l) for l in legacy.read_text().splitlines() if l.strip()]
        self.assertEqual([r["value"] for r in rows], [False],
                         "ONLY the remainder — the converted prefix never re-converts")
        km._replay_ui_op_spool()
        flags = json.loads((km.jd.STATE / "session-flags.json").read_text())
        self.assertNotIn(SID, flags, "final order holds: the un-hide is the last word")

    def test_legacy_ops_convert_to_files_and_survive_a_failure(self):
        # the r46 verification: the inline legacy path was still delete-on-failure — the
        # audited P1 alive for pre-upgrade writers. Conversion makes them ordinary queue
        # members with the same retained-failure rights.
        (km.jd.STATE / "pending-ui-ops.jsonl").write_text(json.dumps(
            {"op": "flag", "target": SID, "flag": "postalServiceOff", "value": True}) + "\n")

        def boom(*a, **kw):
            raise OSError(5, "setter died")

        saved = km._set_session_flag
        km._set_session_flag = boom
        km._SPOOL_FAILS.clear()
        try:
            km._replay_ui_op_spool()
        finally:
            km._set_session_flag = saved
        self.assertFalse((km.jd.STATE / "pending-ui-ops.jsonl").exists(),
                         "the legacy file converted away")
        self.assertTrue(list(self.spdir.glob("0legacy-*.json")),
                        "…into a retained per-op file, not a deleted gesture")
        km._replay_ui_op_spool()
        flags = json.loads((km.jd.STATE / "session-flags.json").read_text())
        self.assertTrue(flags[SID]["postalServiceOff"], "…which then applies")

    def test_both_legacy_spools_convert_without_collision(self):
        # the v1.3.19 audit's P1: each legacy file reset seq, so .replay.jsonl and .jsonl
        # collided on 0legacy-<pid>-1.json and os.replace silently discarded a SAFETY op —
        # of two independent writes (hideFromFeed, postalServiceOff), only one survived
        (km.jd.STATE / "pending-ui-ops.replay.jsonl").write_text(json.dumps(
            {"op": "flag", "target": SID, "flag": "hideFromFeed", "value": True}) + "\n")
        (km.jd.STATE / "pending-ui-ops.jsonl").write_text(json.dumps(
            {"op": "flag", "target": SID, "flag": "postalServiceOff", "value": True}) + "\n")
        km._replay_ui_op_spool()
        flags = json.loads((km.jd.STATE / "session-flags.json").read_text())
        self.assertTrue(flags[SID].get("hideFromFeed"), "the first file's op survived")
        self.assertTrue(flags[SID].get("postalServiceOff"), "…and the second's — no overwrite")

    def test_legacy_conversion_preserves_order_past_nine_ops(self):
        # the r47 verification, executed: the converted names' one sequence was UNPADDED and
        # the replay consumes files in lexicographic sort, where '-10' orders before '-2' —
        # past nine ops the user's later gestures replayed under earlier ones (the executed
        # repro lost the final toggle). Zero-padding keeps sort order equal to write order.
        legacy = km.jd.STATE / "pending-ui-ops.jsonl"
        legacy.write_text("".join(
            json.dumps({"op": "flag", "target": SID, "flag": "hideFromFeed",
                        "value": bool(i % 2), "i": i}) + "\n"
            for i in range(12)))
        order = []
        saved = km._apply_one_ui_op
        km._apply_one_ui_op = lambda op: (order.append(op["i"]), True)[1]
        try:
            km._replay_ui_op_spool()
        finally:
            km._apply_one_ui_op = saved
        self.assertEqual(order, list(range(12)),
                         "twelve legacy ops replay in write order, not lexicographic-unpadded")

    def test_actives_and_tag_order_ride_the_targeted_op_grammar(self):
        # the v1.3.20 audit's grammar extension: the lens picks and the union drag are
        # absolute-state gestures — as whole-blob CAS writes they pipelined guessed revisions
        # (a stale sibling could coincide with a foreign commit's rev and erase it) and the
        # kernel-down spool reduced them to a bare {active}, dropping actives/tagOrder/new tags
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "#DD42FF",
                                         "members": []}}])
        rev, _confl = km._apply_views_ops([
            {"actives": {"timeline": {"tags": ["pool"]}, "chat": {"all": True}}},
            {"create": {"id": "g2", "name": "crew", "color": "#4EC9B0", "members": []}},
            {"tagOrder": ["crew", "pool"]}])
        self.assertEqual(_confl, [], "an all-clean ops list names no conflicts (r50)")
        v = km._timeline_views()
        self.assertEqual(v["rev"], rev)
        self.assertEqual(v["actives"]["timeline"], {"tags": ["pool"]},
                         "the lens pick landed exactly — no reduction to a bare active")
        self.assertEqual(v.get("tagOrder"), ["crew", "pool"], "the drag's order landed")
        self.assertEqual([t["name"] for t in v["tags"]], ["crew", "pool"],
                         "…and the kernel resorted its own tags to match, as the client did")

    def test_an_actives_op_merges_per_surface_never_replaces_the_dict(self):
        # the r48 verification: the first cut REPLACED the whole actives dict, so a client
        # posting every surface's lens overwrote OTHER panes' concurrent picks with its own
        # stale copies — the erase the ops exist to end, one level down. Clients post only
        # the changed surface now, and the kernel merges.
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "", "members": []}}])
        km._apply_views_ops([{"actives": {"chat": {"tags": ["pool"]}}}])      # pane A's pick
        km._apply_views_ops([{"actives": {"outline": {"none": True}}}])       # pane B's pick
        v = km._timeline_views()
        self.assertEqual(v["actives"]["chat"], {"tags": ["pool"]},
                         "pane A's lens SURVIVES pane B's later single-surface op")
        self.assertEqual(v["actives"]["outline"], {"none": True})

    def test_rename_and_delete_migrate_lens_and_order_references(self):
        # the v1.3.21 audit's P2: lenses and tagOrder key tags by NAME — a rename stranded the
        # old name in both (the lens silently emptied) and deleting a selected tag hid every row
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "", "members": []}},
                             {"create": {"id": "g2", "name": "crew", "color": "", "members": []}},
                             {"actives": {"chat": {"tags": ["pool"]}}},
                             {"tagOrder": ["pool", "crew"]}])
        km._apply_views_ops([{"tag": "g1", "rename": "squad"}])
        v = km._timeline_views()
        self.assertEqual(v["actives"]["chat"], {"tags": ["squad"]}, "the lens followed the rename")
        self.assertEqual(v["tagOrder"], ["squad", "crew"], "…and the order did too")
        km._apply_views_ops([{"tag": "g1", "delete": True}])
        v = km._timeline_views()
        self.assertEqual(v["actives"]["chat"], {"all": True},
                         "the emptied lens falls to All — a deleted selection never hides "
                         "every row (the v1.3.21 audit's P2)")
        self.assertEqual(v.get("tagOrder"), ["crew"], "the order dropped the dead name")

    def test_duplicate_names_are_refused_on_create_and_rename(self):
        # tags are name-addressed across every edit wire (the v1.3.21 audit's P2)
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "", "members": []}}])
        km._apply_views_ops([{"create": {"id": "g9", "name": "pool", "color": "", "members": []}},
                             {"create": {"id": "g2", "name": "crew", "color": "", "members": []}},
                             {"tag": "g2", "rename": "pool"}])
        v = km._timeline_views()
        self.assertEqual(sorted(t["name"] for t in v["tags"]), ["crew", "pool"],
                         "the duplicate create dropped and the duplicate rename was a no-op")

    def test_the_union_journal_round_trips_and_replaces_whole(self):
        # the v1.3.21 audit's P1.5: the multi-host compensation journal is kernel-durable now —
        # a panel reload re-seeds from this store instead of forgetting in-flight gestures
        rows = [{"host": "TESTHOST-A", "name": "pool", "gid": 3,
                 "edit": {"remove": ["s1"]}, "inverse": {"tag": "g1", "add": ["s1"]},
                 "rt": {"id": "TESTHOST-A:r1", "host": "TESTHOST-A", "name": "pool"},
                 "oldName": "pool", "oldColor": "", "post": {}, "confirmed": False}]
        self.assertTrue(km._union_ops_set(rows))
        self.assertEqual(km._union_ops_load(), rows)
        # per-gid MERGE (the r49 verification): a second panel's sync must not clobber this
        # entry — omission is not retirement
        other = [{"host": "TESTHOST-B", "name": "crew", "gid": 9, "edit": {}, "inverse": {},
                  "rt": {}, "oldName": "crew", "oldColor": "", "post": {}, "confirmed": False}]
        self.assertTrue(km._union_ops_set(other))
        got = km._union_ops_load()
        self.assertEqual(len(got), 2, "both panels' entries coexist — no last-writer-wins")
        self.assertTrue(km._union_ops_set([], retired=[3]))
        self.assertEqual([r["gid"] for r in km._union_ops_load()], [9],
                         "retirement is an EXPLICIT tombstone, and it lands")
        self.assertTrue(km._union_ops_set(["junk", {"host": "TESTHOST-B", "gid": 9}],
                                          retired=[9]))
        self.assertEqual(km._union_ops_load(), [],
                         "non-dict junk drops, a retired gid wins over its own upsert")

    def test_ops_compose_with_a_foreign_edit_instead_of_erasing_it(self):
        # the audited erase shape, executed at the store: a foreign client's tag lands between
        # two of our gestures — the second, a targeted op, composes with it; a stale whole blob
        # would have erased it
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "", "members": []}}])
        km._apply_views_ops([{"create": {"id": "gF", "name": "foreign", "color": "",
                                         "members": []}}])          # the foreign commit
        km._apply_views_ops([{"actives": {"chat": {"tags": ["pool"]}}}])   # our later gesture
        v = km._timeline_views()
        self.assertTrue(any(t["id"] == "gF" for t in v["tags"]),
                        "the foreign tag SURVIVES our op — nothing whole-blob rode over it")
        self.assertEqual(v["actives"]["chat"], {"tags": ["pool"]})

    def test_the_legacy_append_spool_is_still_consumed_once(self):
        (km.jd.STATE / "pending-ui-ops.jsonl").write_text(json.dumps(
            {"op": "flag", "target": SID, "flag": "hideFromFeed", "value": True}) + "\n")
        km._replay_ui_op_spool()
        flags = json.loads((km.jd.STATE / "session-flags.json").read_text())
        self.assertTrue(flags[SID]["hideFromFeed"])
        self.assertFalse((km.jd.STATE / "pending-ui-ops.jsonl").exists())


class R50UnionJournalLock(unittest.TestCase):
    """the v1.3.22 audit's P1.3: _union_ops_set performed an UNLOCKED load-merge-write — two
    concurrent syncs (two panels, or a panel and the WS handler) both read the same base,
    merged their own entry, and the second atomic write silently erased the first's gesture.
    The whole transaction rides jd._identity_file_lock now."""

    def setUp(self):
        _scrub_state()
        try:
            km._union_ops_path().unlink()
        except OSError:
            pass

    def tearDown(self):
        self.setUp()

    def test_concurrent_merges_lose_nothing(self):
        # the executed race shape: many writers, each upserting a DISTINCT (gid, host) entry.
        # Unlocked, the load-merge-write interleaves and entries vanish; locked, all land.
        n, per = 4, 12
        def worker(w):
            for i in range(per):
                gid = w * 1000 + i + 1
                km._union_ops_set([{"host": "TESTHOST-%d" % w, "name": "t%d" % gid,
                                    "gid": gid, "edit": {}, "inverse": {}, "rt": {},
                                    "oldName": "", "oldColor": "", "post": {},
                                    "confirmed": False}])
        ts = [threading.Thread(target=worker, args=(w,)) for w in range(n)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        gids = sorted(r["gid"] for r in km._union_ops_load())
        self.assertEqual(len(gids), n * per,
                         "every concurrent upsert survives — the merge is one locked "
                         "transaction, not a read-modify-write race")

    def test_the_transaction_holds_the_identity_lock(self):
        # the revert detector for the mechanism itself: the merge body must sit under
        # jd._identity_file_lock (the threaded race above is probabilistic; this is not)
        import inspect
        src = inspect.getsource(km._union_ops_merge)   # _union_ops_set is its shim since r55
        self.assertIn("with jd._identity_file_lock():", src)
        self.assertLess(src.index("_identity_file_lock"),
                        src.index('_read_state_json(_union_ops_path()'),
                        "the LOAD happens inside the lock — locking after reading is the bug")


class R50RefusalExpiryAndCap(unittest.TestCase):
    """the r50 verification round on the refusal journal: an ownerless refusal row had no
    retiring event (immortal), and the 200-row cap truncated the NEWEST rows while acking
    ok:true — the panel was told a write landed that was dropped on the floor."""

    def setUp(self):
        try:
            km._union_ops_path().unlink()
        except OSError:
            pass

    def tearDown(self):
        self.setUp()

    def test_an_aged_refusal_row_expires_and_a_fresh_one_stays(self):
        now = int(__import__("time").time())
        km._union_ops_set([
            {"refusal": True, "host": "TESTHOST-A", "gid": -11, "opId": "11",
             "t": now - 8 * 86400},
            {"refusal": True, "host": "TESTHOST-A", "gid": -12, "opId": "12", "t": now}])
        km._union_ops_set([])            # any later merge sweeps the aged row
        gids = [r["gid"] for r in km._union_ops_load()]
        self.assertNotIn(-11, gids, "an 8-day-old ownerless refusal expires (bounded staleness)")
        self.assertIn(-12, gids, "a fresh refusal stays for its owner to consume")

    def test_gesture_entries_never_expire_by_age(self):
        now = int(__import__("time").time())
        km._union_ops_set([{"host": "TESTHOST-A", "gid": 5, "t": now - 30 * 86400,
                            "edit": {}, "inverse": {}, "rt": {}, "name": "pool"}])
        km._union_ops_set([])
        self.assertEqual([r["gid"] for r in km._union_ops_load()], [5],
                         "in-flight GESTURES settle on evidence, never a clock")

    def test_the_cap_never_evicts_an_unresolved_row(self):
        # the v1.3.23 audit's P1.3, executed there as a 201-row probe: the r50 cap dropped the
        # OLDEST rows past _UNION_OPS_MAX and still acked ok:true — the oldest ACTIVE gesture
        # silently left the journal, and a reload had nothing to compensate. Unretired rows
        # are the panel's un-acknowledged writes: an ok ack means EVERY one of them persisted;
        # _UNION_OPS_MAX is a loud high-water mark now, never an eviction.
        rows = [{"host": "TESTHOST-A", "gid": i, "edit": {}, "inverse": {}, "rt": {},
                 "name": "t%d" % i} for i in range(1, km._UNION_OPS_MAX + 1)]
        self.assertTrue(km._union_ops_set(rows))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertTrue(km._union_ops_set([{"host": "TESTHOST-B", "gid": 9999, "edit": {},
                                                "inverse": {}, "rt": {}, "name": "fresh"}]))
        gids = [r["gid"] for r in km._union_ops_load()]
        self.assertIn(9999, gids, "the row the ok:true ack just promised persisted")
        self.assertEqual(len(gids), km._UNION_OPS_MAX + 1,
                         "…and every unresolved row it joined is still there")
        self.assertIn(1, gids, "the oldest active gesture is NOT the cap's price anymore")
        self.assertIn("high-water mark", err.getvalue(), "the overflow is loud, not silent")

    def test_the_load_side_never_truncates_an_over_full_journal(self):
        # the same audit finding's second half: _union_ops_load()[:MAX] silently dropped the
        # tail of an over-full journal before any merge saw it
        rows = [{"host": "TESTHOST-A", "gid": i, "edit": {}, "inverse": {}, "rt": {},
                 "name": "t%d" % i} for i in range(1, km._UNION_OPS_MAX + 2)]
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._union_ops_path().write_text(json.dumps(rows))
        self.assertEqual(len(km._union_ops_load()), km._UNION_OPS_MAX + 1)


class R50UnionOpsAckAndConflicts(unittest.TestCase):
    """the v1.3.22 audit's P1.3 (correlated union-journal acks) and P2.8 (duplicate-name
    conflicts were silently acked ok with a bumped rev): against the REAL Handler."""

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
        try:
            km._union_ops_path().unlink()
        except OSError:
            pass

    def tearDown(self):
        km._mark_views_dirty = self._dirty
        _scrub_state()
        try:
            km._union_ops_path().unlink()
        except OSError:
            pass

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

    def test_union_ops_post_echoes_the_opid(self):
        # the twin advances its synced-gid watermark only on a CORRELATED success (P1.3): an
        # answer that cannot be matched to its write is an answer the twin must ignore
        st, ans = self._post("/union-ops", {"entries": [], "retired": [], "opId": "u7"})
        self.assertEqual(st, 200)
        self.assertTrue(ans["ok"])
        self.assertEqual(ans.get("opId"), "u7")

    def test_the_ws_merge_call_sits_inside_the_ack_try(self):
        # the r50 verification round: _union_ops_set was called OUTSIDE the try — a raise
        # (the identity lock propagates loudly by design) was swallowed by the dispatcher and
        # NO unionOpsAck went out, leaking the panel's pending sync with no retry ever
        src = open(os.path.join(BIN, "romp-kernel")).read()
        i = src.index('_uok, _unclaimed, _ureason = _union_ops_merge(')
        window = src[max(0, i - 200):i]
        self.assertIn("try:", window, "the merge call is guarded")
        after = src[i:i + 500]
        self.assertIn('_uok, _unclaimed, _ureason = False, [], "internal error"', after,
                      "…and a raise answers ok:false, never silence")

    def test_union_ops_save_failure_answers_ok_false_with_the_opid(self):
        # the audited hole: the WS handler DISCARDED _union_ops_set's failure — the panel
        # advanced its watermark on hope and the failed retirement was never re-sent
        with mock.patch.object(km, "_atomic_write", side_effect=OSError("disk")):
            st, ans = self._post("/union-ops",
                                 {"entries": [{"host": "TESTHOST-A", "gid": 5}],
                                  "retired": [], "opId": "u9"})
        self.assertEqual(st, 500)
        self.assertFalse(ans["ok"])
        self.assertEqual(ans.get("opId"), "u9", "the failure is correlated too")

    def test_duplicate_create_is_a_named_conflict_and_bumps_nothing(self):
        # P2.8, executed at the store: the SECOND create of the name used to vanish under an
        # ok ack and a bumped rev — the dialog kept showing a tag the store never held
        rev0, c0 = km._apply_views_ops([{"create": {"id": "g1", "name": "pool",
                                                    "color": "", "members": []}}])
        self.assertEqual(c0, [])
        rev1, c1 = km._apply_views_ops([{"create": {"id": "g2", "name": "pool",
                                                    "color": "", "members": []}}])
        self.assertEqual(rev1, rev0, "nothing applied — the rev must NOT advance")
        self.assertEqual(len(c1), 1)
        self.assertIn("pool", c1[0])
        self.assertIn("already taken", c1[0])

    def test_a_replayed_same_id_create_stays_quiet(self):
        # the reconnect re-pump legitimately re-sends a landed create: same id, same name —
        # idempotence, not a conflict (alarming here would flag every recovered gesture)
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "",
                                         "members": []}}])
        rev, confl = km._apply_views_ops([{"create": {"id": "g1", "name": "pool",
                                                      "color": "", "members": []}}])
        self.assertEqual(confl, [])

    def test_duplicate_rename_is_a_named_conflict(self):
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "", "members": []}},
                             {"create": {"id": "g2", "name": "crew", "color": "", "members": []}}])
        rev0 = km._timeline_views()["rev"]
        rev1, confl = km._apply_views_ops([{"tag": "g2", "rename": "pool"}])
        self.assertEqual(len(confl), 1)
        self.assertIn("crew", confl[0])
        self.assertIn("pool", confl[0])
        self.assertEqual(rev1, rev0,
                         "a refused rename applied NOTHING — bumping the rev 409'd other "
                         "panes' CAS writes for a zero-byte change (the r50 round: applied "
                         "fired at tag LOOKUP, before the refusal)")
        self.assertEqual(km._timeline_views()["rev"], rev0, "…and the store was not rewritten")

    def test_no_op_field_edits_bump_nothing(self):
        # the r50 verification round: applied=True at the tag lookup meant a same-value color,
        # an add of an existing member, or a same-name rename rewrote the store with rev+1
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "#DD42FF",
                                         "members": ["s1"]}}])
        rev0 = km._timeline_views()["rev"]
        for op in ({"tag": "g1", "color": "#DD42FF"},
                   {"tag": "g1", "add": ["s1"]},
                   {"tag": "g1", "remove": ["ghost-sid"]},
                   {"tag": "g1", "rename": "pool"}):
            rev1, confl = km._apply_views_ops([op])
            self.assertEqual(rev1, rev0, "no-op %r must not bump" % op)
            self.assertEqual(confl, [], "…and a no-op is not a conflict either: %r" % op)
        rev2, _ = km._apply_views_ops([{"tag": "g1", "color": "#4EC9B0"}])
        self.assertEqual(rev2, rev0 + 1, "a REAL change still bumps")

    def test_unknown_tag_ops_apply_nothing_and_bump_nothing(self):
        # the deliberate quiet drop (a replayed gesture over a deleted tag) must not
        # advertise a change: no mutation, no rev bump, no conflict
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "", "members": []}}])
        rev0 = km._timeline_views()["rev"]
        rev1, confl = km._apply_views_ops([{"tag": "ghost", "add": ["s1"]}])
        self.assertEqual(rev1, rev0)
        self.assertEqual(confl, [])
        self.assertEqual(km._timeline_views()["rev"], rev0)

    def test_post_views_ops_response_carries_the_conflicts(self):
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "", "members": []}}])
        st, ans = self._post("/views", {"ops": [{"create": {"id": "g2", "name": "pool",
                                                            "color": "", "members": []}}]})
        self.assertEqual(st, 200)
        self.assertTrue(ans["ok"])
        self.assertEqual(len(ans.get("conflicts") or []), 1,
                         "the partial application is NAMED in the response — the twin "
                         "surfaces it instead of trusting a plain ok")


class R50RemoteRefMigration(unittest.TestCase):
    """the v1.3.22 audit's P2.7: a REMOTE-ONLY rename/delete left this kernel's own lens and
    tagOrder references on the old name (the twin migrated only tags with a localId) — a
    selected lens silently showed no rows indefinitely."""

    def setUp(self):
        _scrub_state()

    def tearDown(self):
        _scrub_state()

    def _seed_remote_refs(self):
        # a lens and an order slot referencing a name that exists ONLY on a linked kernel —
        # _norm_lens keeps unknown names by design (they may match remoteTags at read time)
        km._apply_views_ops([{"create": {"id": "g1", "name": "local", "color": "", "members": []}},
                             {"actives": {"chat": {"tags": ["farpool"]}}},
                             {"tagOrder": ["farpool", "local"]}])

    def test_a_landed_remote_rename_migrates_lens_and_order(self):
        self._seed_remote_refs()
        rev0 = km._timeline_views()["rev"]
        km._migrate_refs_after_remote_edit("TESTHOST-A", "farpool", {"rename": "nearpool"})
        v = km._timeline_views()
        self.assertEqual(v["actives"]["chat"], {"tags": ["nearpool"]},
                         "the lens followed the REMOTE rename")
        self.assertEqual(v["tagOrder"], ["nearpool", "local"])
        self.assertGreater(v["rev"], rev0)

    def test_a_landed_remote_delete_drops_the_refs(self):
        self._seed_remote_refs()
        km._migrate_refs_after_remote_edit("TESTHOST-A", "farpool", {"delete": True})
        v = km._timeline_views()
        self.assertEqual(v["actives"]["chat"], {"all": True},
                         "the emptied lens falls to All, never an empty screen")
        self.assertEqual(v.get("tagOrder"), ["local"])

    def test_no_reference_means_no_rev_bump(self):
        self._seed_remote_refs()
        rev0 = km._timeline_views()["rev"]
        km._migrate_refs_after_remote_edit("TESTHOST-A", "elsewhere", {"rename": "elsewhere2"})
        self.assertEqual(km._timeline_views()["rev"], rev0,
                         "nothing referenced the name — a bump would claim a change "
                         "nobody made (the P2.8 rule)")

    def test_an_empty_rename_migrates_nothing(self):
        # the guard: rename="" must not be read as a delete — the forward's answer said ok
        # to a no-op, not to dropping the refs
        self._seed_remote_refs()
        km._migrate_refs_after_remote_edit("TESTHOST-A", "farpool", {"rename": "   "})
        v = km._timeline_views()
        self.assertEqual(v["actives"]["chat"], {"tags": ["farpool"]})

    def test_a_same_name_local_tag_pins_the_refs_through_a_remote_rename(self):
        # the r50 verification round: lens names are UNIONS — renaming only the REMOTE 'pool'
        # moved the refs wholesale and the still-existing LOCAL 'pool' dropped out of every
        # lens and lost its order slot
        km._apply_views_ops([{"create": {"id": "gl", "name": "farpool", "color": "",
                                         "members": []}},
                             {"actives": {"chat": {"tags": ["farpool"]}}},
                             {"tagOrder": ["farpool"]}])
        km._migrate_refs_after_remote_edit("TESTHOST-A", "farpool", {"rename": "nearpool"})
        v = km._timeline_views()
        self.assertEqual(v["actives"]["chat"], {"tags": ["farpool"]},
                         "the local twin still holds the name — the refs stay")
        self.assertEqual(v["tagOrder"], ["farpool"])

    def test_another_hosts_remote_twin_also_pins_the_refs(self):
        self._seed_remote_refs()
        saved = dict(km._remotes)
        km._remotes.clear()
        km._remotes["x"] = {"host": "TESTHOST-B", "views":
                            {"tags": [{"id": "r9", "name": "farpool"}]}}
        try:
            km._migrate_refs_after_remote_edit("TESTHOST-A", "farpool", {"rename": "nearpool"})
            v = km._timeline_views()
            self.assertEqual(v["actives"]["chat"], {"tags": ["farpool"]},
                             "TESTHOST-B still owns a 'farpool' — the union survives, refs stay")
        finally:
            km._remotes.clear()
            km._remotes.update(saved)

    def test_the_edited_hosts_stale_cache_cannot_defeat_the_delete_migration(self):
        # the r50 verification round: _views_drop_refs' survival check read the EDITED host's
        # cached views — still showing the just-deleted tag when the inline fast-echo refresh
        # hiccuped — and returned early, with nothing ever re-running the migration
        self._seed_remote_refs()
        saved = dict(km._remotes)
        km._remotes.clear()
        km._remotes["x"] = {"host": "TESTHOST-A", "views":
                            {"tags": [{"id": "r1", "name": "farpool"}]}}   # the STALE cache
        try:
            km._migrate_refs_after_remote_edit("TESTHOST-A", "farpool", {"delete": True})
            v = km._timeline_views()
            self.assertEqual(v["actives"]["chat"], {"all": True},
                             "the landed delete outranks the edited host's stale cache")
        finally:
            km._remotes.clear()
            km._remotes.update(saved)

    def test_the_ws_and_tag_routes_both_migrate_on_success(self):
        # the two forward paths (the dialog's WS editTag, the /tag --host route) share the
        # helper — a success on either migrates this kernel's refs, off the owner's
        # authoritative reply (the v1.3.23 audit's P2.5), never a re-read of the raw request
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertEqual(src.count("_migrate_refs_after_remote_edit(host, nm, body, ans)"), 1)
        self.assertEqual(src.count("_migrate_refs_after_remote_edit(th, name, b, ans)"), 1)


class V1323NoOpOpsAndMintCollision(unittest.TestCase):
    """the v1.3.23 audit's P2.7 (exact no-op ops bumped the views revision — repeating current
    values advanced rev 1→2→3→4, and the false bumps then 409'd another client's meaningful
    CAS write) and P2.6's server half (a same-id, different-name create read as a replay:
    both panels acked ok while only the first tag existed)."""

    def setUp(self):
        _scrub_state()

    def tearDown(self):
        _scrub_state()

    def test_repeating_current_values_never_bumps_rev(self):
        rev1, _ = km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "",
                                                   "members": []}},
                                       {"actives": {"chat": {"tags": ["pool"]}}},
                                       {"tagOrder": ["pool"]}])
        for _i in range(3):
            rev, confl = km._apply_views_ops([{"active": km._timeline_views()["active"]},
                                              {"actives": {"chat": {"tags": ["pool"]}}},
                                              {"tagOrder": ["pool"]}])
            self.assertEqual(rev, rev1, "an exact no-op leaves the revision alone")
            self.assertEqual(confl, [])

    def test_a_real_change_still_bumps(self):
        rev1, _ = km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "",
                                                   "members": []}}])
        rev2, _ = km._apply_views_ops([{"tag": "pool", "color": "#123456"}])
        self.assertEqual(rev2, rev1 + 1)

    def test_a_same_id_same_name_create_is_a_quiet_replay(self):
        cr = {"create": {"id": "g1", "name": "pool", "color": "", "members": []}}
        rev1, _ = km._apply_views_ops([cr])
        rev2, confl = km._apply_views_ops([cr])
        self.assertEqual((rev2, confl), (rev1, []),
                         "the spool's replayed create stays idempotent and quiet")

    def test_a_same_id_create_stays_quiet_even_after_a_concurrent_rename(self):
        # the r51 sibling verification: the collision-conflict branch misfired on the
        # reconnect re-pump of a LANDED create whose tag another pane had since renamed —
        # the toast told the user to retry a create that succeeded, and obeying it minted a
        # duplicate under a fresh id. Same-id is always replay idempotence; the cross-panel
        # millisecond collision is closed at the MINT (random-suffix ids, pinned by
        # union-sync-transport.test.ts).
        rev1, _ = km._apply_views_ops([{"create": {"id": "g1", "name": "alpha", "color": "",
                                                   "members": []}}])
        km._apply_views_ops([{"tag": "g1", "rename": "omega"}])   # a concurrent pane's rename
        rev0 = km._timeline_views()["rev"]
        rev2, confl = km._apply_views_ops([{"create": {"id": "g1", "name": "alpha", "color": "",
                                                       "members": []}}])
        self.assertEqual(confl, [], "the client's own landed gesture is never a loud conflict")
        self.assertEqual(rev2, rev0, "…and applies nothing")
        self.assertEqual([t["name"] for t in km._timeline_views()["tags"]], ["omega"],
                         "the rename stands — the replay resurrects nothing")


class V1323RefusalRetryQueue(unittest.TestCase):
    """the v1.3.23 audit's P1.3: a refusal row whose journal save failed was swallowed
    (`except Exception: pass`) — the reload-surviving twin of the transient tagEditFailed
    frame never existed, and the multi-host split went silent again."""

    def setUp(self):
        try:
            km._union_ops_path().unlink()
        except OSError:
            pass
        km._pending_refusal_rows[:] = []

    def tearDown(self):
        self.setUp()

    def test_a_failed_save_queues_and_the_supervisor_retry_lands_it(self):
        row = {"refusal": True, "host": "TESTHOST-A", "name": "pool", "opId": "77",
               "error": "synthetic", "t": 1, "gid": -77}
        real = km._union_ops_set
        km._union_ops_set = lambda entries, retired=None: False
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                km._journal_refusal(row)
        finally:
            km._union_ops_set = real
        self.assertEqual(km._pending_refusal_rows, [row], "the failed save queues, never drops")
        self.assertEqual(km._union_ops_load(), [], "nothing landed yet")
        with mock.patch.object(km, "_mark_views_dirty") as dirty:
            km._flush_pending_refusals()                      # the disk healed
            self.assertTrue(dirty.called, "panels learn from the next payload's echo")
        self.assertEqual([r["gid"] for r in km._union_ops_load()], [-77])
        self.assertEqual(km._pending_refusal_rows, [], "the queue drains on success")

    def test_a_still_failing_store_keeps_the_queue(self):
        km._pending_refusal_rows[:] = [{"refusal": True, "gid": -1, "t": 1}]
        real = km._union_ops_set
        km._union_ops_set = lambda entries, retired=None: False
        try:
            km._flush_pending_refusals()
        finally:
            km._union_ops_set = real
        self.assertEqual(len(km._pending_refusal_rows), 1, "held for the next pass")


class R51SiblingVerifyMigrations(unittest.TestCase):
    """the r51 sibling verification round on the migration retry machinery: the queue
    clobbered a prior boot's durable intents, an unreadable store read as 'moot', and a
    replayed intent still ignored the edited host's cache long after it had re-polled."""

    def setUp(self):
        _scrub_state()
        km._pending_ref_migrations[:] = []
        try:
            km._ref_migrations_path().unlink()
        except OSError:
            pass

    def tearDown(self):
        self.setUp()

    def test_the_queue_adopts_a_prior_boots_durable_intents_before_writing(self):
        # the executed clobber: intent B persisted, the kernel restarted, and a NEW failure's
        # queue write overwrote the file with only this boot's rows before the pass-tail
        # flush ever adopted B — B's remote half had committed, refs stale forever
        km._ref_migrations_path().write_text(json.dumps(
            [{"host": "TESTHOST-A", "name": "farpool", "new": "nearpool", "deleted": False}]))
        km._pending_ref_migrations[:] = []            # the restart
        km._queue_ref_migration({"host": "TESTHOST-B", "name": "crew", "new": "squad",
                                 "deleted": False})
        rows = json.loads(km._ref_migrations_path().read_text())
        self.assertEqual([(r["host"], r["name"]) for r in rows],
                         [("TESTHOST-A", "farpool"), ("TESTHOST-B", "crew")],
                         "the survivor intent is adopted, never clobbered")

    def test_an_unreadable_store_is_retryable_not_moot(self):
        real = km._views_path
        class _BadPath:
            def read_text(self):
                raise OSError(5, "EIO")

            def stat(self):
                raise OSError(5, "EIO")   # _timeline_views' cache key probes stat too

            def exists(self):
                return True
        try:
            km._views_path = lambda: _BadPath()
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                ok = km._apply_ref_migration("TESTHOST-A", "farpool", "nearpool", False)
        finally:
            km._views_path = real
        self.assertFalse(ok, "a flaky read must NOT retire the intent as 'no reference held "
                             "the name' — that was the permanent-stale-refs outcome disguised "
                             "as success")

    def test_a_replayed_intent_respects_a_reminted_same_name_tag(self):
        # fresh=False: the edited host's cache exemption is for the one echo window after the
        # edit — a replayed intent runs after the cache re-polled, and ignoring the host then
        # renamed away a tag the user had re-minted there since
        km._apply_views_ops([{"create": {"id": "g1", "name": "local", "color": "", "members": []}},
                             {"actives": {"chat": {"tags": ["farpool"]}}}])
        saved = dict(km._remotes)
        km._remotes.clear()
        km._remotes["x"] = {"host": "TESTHOST-A", "views":
                            {"tags": [{"id": "r2", "name": "farpool"}]}}   # the RE-MINTED tag
        try:
            self.assertTrue(km._apply_ref_migration("TESTHOST-A", "farpool", "nearpool",
                                                    False, fresh=False))
            v = km._timeline_views()
            self.assertEqual(v["actives"]["chat"], {"tags": ["farpool"]},
                             "the replayed rename yields to the live re-minted name")
            self.assertTrue(km._apply_ref_migration("TESTHOST-A", "farpool", "nearpool",
                                                    False, fresh=True))
            v = km._timeline_views()
            self.assertEqual(v["actives"]["chat"], {"tags": ["nearpool"]},
                             "…while the FRESH call still outranks the one-echo-stale cache")
        finally:
            km._remotes.clear()
            km._remotes.update(saved)


class V1323MigrationAuthority(unittest.TestCase):
    """the v1.3.23 audit's P2.5: the migration helper re-read the RAW request instead of the
    owner's authoritative result — {delete:true, rename:...} renamed the viewer's refs to a
    name the owner never minted (the owner deletes first and returns), a whitespace-heavy
    rename kept a 40-char viewer ref for a 30-char owner name (strip-only vs the owner's
    clamp-then-strip), and a local write failure after the remote committed left stale refs
    forever, silently."""

    def setUp(self):
        _scrub_state()
        km._pending_ref_migrations[:] = []
        try:
            km._ref_migrations_path().unlink()
        except OSError:
            pass

    def tearDown(self):
        self.setUp()

    def _seed(self):
        km._apply_views_ops([{"create": {"id": "g1", "name": "local", "color": "",
                                         "members": []}},
                             {"actives": {"chat": {"tags": ["farpool"]}}},
                             {"tagOrder": ["farpool", "local"]}])

    def test_delete_wins_over_a_riding_rename(self):
        self._seed()
        km._migrate_refs_after_remote_edit("TESTHOST-A", "farpool",
                                           {"delete": True, "rename": "nearpool"})
        v = km._timeline_views()
        self.assertEqual(v["actives"]["chat"], {"all": True},
                         "the owner deleted — the refs drop; renaming them to 'nearpool' "
                         "pointed every lens at a tag that never existed")
        self.assertEqual(v.get("tagOrder"), ["local"])

    def test_rename_normalization_matches_the_owner(self):
        # the owner clamps THEN strips (_edit_tag): 30 y's + 10 spaces + 10 z's clamps to
        # 40 (the y's and the spaces), strips to the 30 y's — strip-only kept 40 chars
        self._seed()
        raw = "y" * 30 + " " * 10 + "z" * 10
        km._migrate_refs_after_remote_edit("TESTHOST-A", "farpool", {"rename": raw})
        v = km._timeline_views()
        self.assertEqual(v["actives"]["chat"], {"tags": ["y" * 30]},
                         "the viewer ref wears the OWNER's post-normalize name")

    def test_the_owners_reply_name_outranks_the_local_reconstruction(self):
        self._seed()
        km._migrate_refs_after_remote_edit("TESTHOST-A", "farpool", {"rename": "nearpool"},
                                           ans={"ok": True, "tag": {"name": "nearpool-owner"}})
        v = km._timeline_views()
        self.assertEqual(v["actives"]["chat"], {"tags": ["nearpool-owner"]})

    def test_a_failed_local_write_queues_a_retryable_intent(self):
        self._seed()
        real = km._atomic_write

        def failing(path, data):
            if str(path).endswith("timeline-views.json"):
                raise OSError("ENOSPC")
            return real(path, data)

        km._atomic_write = failing
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                km._migrate_refs_after_remote_edit("TESTHOST-A", "farpool",
                                                   {"rename": "nearpool"})
        finally:
            km._atomic_write = real
        self.assertEqual(len(km._pending_ref_migrations), 1,
                         "the committed remote half is never forgotten")
        v = km._timeline_views()
        self.assertEqual(v["actives"]["chat"], {"tags": ["farpool"]}, "not yet migrated")
        with mock.patch.object(km, "_mark_views_dirty"):
            km._flush_pending_ref_migrations()                # the disk healed
        v = km._timeline_views()
        self.assertEqual(v["actives"]["chat"], {"tags": ["nearpool"]},
                         "the supervisor retry lands the migration")
        self.assertEqual(km._pending_ref_migrations, [], "…and the intent retires")


class R52ProvedReads(unittest.TestCase):
    """the v1.3.24 audit's P1.1/P1.2 (and P2.6's sibling): every state writer folded ANY read
    failure to empty and then published — one injected EIO deleted a seeded union gesture, and
    a create acked success over a write that erased every existing tag. Only FileNotFoundError
    means empty now; unreadable REFUSES; malformed quarantines aside."""

    def setUp(self):
        _scrub_state()
        for pfn in (km._union_ops_path, km._ref_migrations_path):
            try:
                pfn().unlink()
            except OSError:
                pass
        km._pending_ref_migrations[:] = []

    def tearDown(self):
        self.setUp()

    class _BadPath:
        def __init__(self, real):
            self._real = real

        def read_text(self):
            raise OSError(5, "EIO")

        def __getattr__(self, k):
            return getattr(self._real, k)

    def test_the_union_merge_refuses_an_unproved_read(self):
        self.assertTrue(km._union_ops_set([{"host": "TESTHOST-A", "gid": 1, "edit": {},
                                            "inverse": {}, "rt": {}, "name": "pool"}]))
        real = km._union_ops_path
        try:
            km._union_ops_path = lambda: self._BadPath(real())
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                ok = km._union_ops_set([{"host": "TESTHOST-B", "gid": 2, "edit": {},
                                         "inverse": {}, "rt": {}, "name": "crew"}])
        finally:
            km._union_ops_path = real
        self.assertFalse(ok, "EIO used to read as an EMPTY journal — the merge then "
                             "overwrote every valid gesture and returned success")
        self.assertEqual([r["gid"] for r in km._union_ops_load()], [1],
                         "the seeded gesture SURVIVES the refused merge")

    def test_apply_views_ops_refuses_an_unproved_read(self):
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "", "members": []}}])
        real = km._views_path
        try:
            km._views_path = lambda: self._BadPath(real())
            with self.assertRaises(OSError):
                km._apply_views_ops([{"create": {"id": "g2", "name": "crew",
                                                 "color": "", "members": []}}])
        finally:
            km._views_path = real
        self.assertEqual([t["name"] for t in km._timeline_views()["tags"]], ["pool"],
                         "one transient EIO used to erase every existing tag and lens")

    def test_set_timeline_views_refuses_an_unproved_read(self):
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "", "members": []}}])
        real = km._views_path
        try:
            km._views_path = lambda: self._BadPath(real())
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                ok, rev = km._set_timeline_views({"active": "all", "tags": []})
        finally:
            km._views_path = real
        self.assertFalse(ok)
        self.assertIsNone(rev, "no rev is KNOWN — none is claimed")
        self.assertEqual([t["name"] for t in km._timeline_views()["tags"]], ["pool"])

    def test_malformed_views_json_is_quarantined_never_overwritten_silently(self):
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._views_path().write_text("{not json")
        with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
            rev, confl = km._apply_views_ops([{"create": {"id": "g1", "name": "pool",
                                                          "color": "", "members": []}}])
        self.assertEqual([t["name"] for t in km._timeline_views()["tags"]], ["pool"],
                         "a fresh store starts — corrupt content can never merge")
        quarantined = list(km.jd.STATE.glob("timeline-views.json.corrupt-*"))
        self.assertEqual(len(quarantined), 1, "…but the bytes survive for forensics")
        for q in quarantined:
            q.unlink()

    def test_unreadable_intent_storage_is_never_clobbered(self):
        km._ref_migrations_path().write_text(json.dumps(
            [{"host": "TESTHOST-A", "name": "farpool", "new": "nearpool", "deleted": False}]))
        real = km._ref_migrations_path
        realp = real()
        try:
            km._ref_migrations_path = lambda: self._BadPath(realp)
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                km._queue_ref_migration({"host": "TESTHOST-B", "name": "crew",
                                         "new": "squad", "deleted": False})
        finally:
            km._ref_migrations_path = real
        rows = json.loads(km._ref_migrations_path().read_text())
        self.assertEqual([r["name"] for r in rows], ["farpool"],
                         "the committed intent SURVIVES — EIO used to read as 'nothing to "
                         "adopt' and the queue replaced the file with only the newest intent")

    def test_identical_whole_blob_writes_bump_nothing(self):
        # the v1.3.24 audit's P2.9: remote-only chat edits repost the local blob (their
        # remoteTags mutation is derived state the kernel discards) — rev advanced 1->2 over
        # a byte-identical store and could 409 another client's real CAS write
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "", "members": []}}])
        cur = km._timeline_views()
        rev0 = cur["rev"]
        blob = {k: json.loads(json.dumps(v)) for k, v in cur.items()
                if k not in ("rev", "remoteTags")}
        ok, rev = km._set_timeline_views(blob)
        self.assertTrue(ok)
        self.assertEqual(rev, rev0, "the store already holds this — nothing to claim")
        self.assertEqual(km._timeline_views()["rev"], rev0)

    def test_the_survival_check_rides_the_identity_transaction(self):
        # the v1.3.24 audit's P2.7 (barrier-reproduced there): a local tag created between
        # the survival check and the lock kept its name while the lens was rewritten
        import inspect
        src = inspect.getsource(km._apply_ref_migration)
        self.assertLess(src.index("jd._identity_file_lock()"),
                        src.index("_tag_name_survives_elsewhere"),
                        "check, mutation and write share ONE identity-lock hold")


class R52VerifyRound(unittest.TestCase):
    """the r52 verification round on this round's own fixes: the ignored whole-blob refusal
    acked success over a no-op (and federated callers migrated refs off it), and the adopt
    short-circuit read memory-non-empty as adopted, deferring the clobber by one write."""

    def setUp(self):
        _scrub_state()
        km._pending_ref_migrations[:] = []
        km._ref_migrations_adopted[0] = False
        try:
            km._ref_migrations_path().unlink()
        except OSError:
            pass

    def tearDown(self):
        self.setUp()

    class _BadPath:
        def __init__(self, real):
            self._real = real

        def read_text(self):
            raise OSError(5, "EIO")

        def __getattr__(self, k):
            return getattr(self._real, k)

    def test_edit_tag_honors_the_stores_refusal(self):
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "", "members": []}}])
        real = km._views_path
        try:
            km._views_path = lambda: self._BadPath(real())
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                out, err = km._edit_tag("pool", rename="crew")
        finally:
            km._views_path = real
        self.assertIsNone(out, "no renamed row is returned for a write that never landed")
        self.assertIn("did not land", err or "",
                      "the r52 round, reproduced: err=None acked {ok:true, tag:'crew'} while "
                      "the store held 'pool' — and the federated leg migrated the viewer's "
                      "refs to a name never persisted")
        self.assertEqual([t["name"] for t in km._timeline_views()["tags"]], ["pool"])

    def test_the_deferred_clobber_is_closed(self):
        # the r52 round: refused adopt -> intent held in memory -> the NEXT healthy write
        # short-circuited on memory-non-empty and clobbered the crash-survivor file anyway
        km._ref_migrations_path().write_text(json.dumps(
            [{"host": "TESTHOST-A", "name": "farpool", "new": "nearpool", "deleted": False}]))
        real = km._ref_migrations_path
        realp = real()
        try:
            km._ref_migrations_path = lambda: self._BadPath(realp)
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                km._queue_ref_migration({"host": "TESTHOST-B", "name": "crew",
                                         "new": "squad", "deleted": False})   # refused adopt
        finally:
            km._ref_migrations_path = real
        with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
            km._queue_ref_migration({"host": "TESTHOST-C", "name": "gang",
                                     "new": "band", "deleted": False})        # the disk healed
        rows = json.loads(km._ref_migrations_path().read_text())
        self.assertIn("farpool", [r["name"] for r in rows],
                      "the crash-survivor intent is ADOPTED before any healthy write — the "
                      "short-circuit on memory-non-empty deferred the clobber, not closed it")


class R53ProvedSnapshots(unittest.TestCase):
    """the v1.3.25 audit: the LENIENT cache fed mutation snapshots (P1.1 — an EIO'd read
    became an empty view that the proved write then published, deleting every tag), the
    journal echo read [] for EIO (P1.2 — panels took it as authoritative retirement), the
    quarantine could lose its bytes (P2.9), and write faults poisoned the spool (P2.8)."""

    def setUp(self):
        _scrub_state()
        km._pending_heals[:] = []
        km._heals_adopted[0] = False
        for pfn in (km._union_ops_path, km._heals_path):
            try:
                pfn().unlink()
            except OSError:
                pass

    def tearDown(self):
        self.setUp()

    class _BadPath:
        def __init__(self, real):
            self._real = real

        def read_text(self):
            raise OSError(5, "EIO")

        def stat(self):
            raise OSError(5, "EIO")

        def exists(self):
            return True

        def __getattr__(self, k):
            return getattr(self._real, k)

    def test_the_mutation_snapshot_never_edits_an_empty_ghost(self):
        # the executed r53 P1.1: seed keep-a/keep-b, EIO the read, edit — v1.3.25 acked
        # {"acked":true,"persisted":["new-tag"]} and BOTH seeded tags were deleted
        km._apply_views_ops([{"create": {"id": "ga", "name": "keep-a", "color": "", "members": []}},
                             {"create": {"id": "gb", "name": "keep-b", "color": "", "members": []}}])
        real = km._views_path
        try:
            km._views_path = lambda: self._BadPath(real())
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                out, err = km._edit_tag("new-tag", add=[])
        finally:
            km._views_path = real
        self.assertIsNone(out)
        self.assertIn("unreadable", err or "")
        self.assertEqual(sorted(t["name"] for t in km._timeline_views()["tags"]),
                         ["keep-a", "keep-b"], "nothing was erased under a success ack")

    def test_the_journal_echo_marks_unavailability(self):
        self.assertTrue(km._union_ops_set([{"host": "TESTHOST-A", "gid": 7, "edit": {},
                                            "inverse": {}, "rt": {}, "name": "pool"}]))
        real = km._union_ops_path
        try:
            km._union_ops_path = lambda: self._BadPath(real())
            self.assertIsNone(km._union_ops_echo(),
                              "an unproved read is NO INFORMATION — [] read as an "
                              "authoritative owner retirement and panels deleted durable "
                              "entries (the r53 audit's P1.2)")
        finally:
            km._union_ops_path = real
        self.assertEqual([r["gid"] for r in km._union_ops_echo()], [7])

    def test_a_failed_quarantine_never_reads_as_empty(self):
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._views_path().write_text("{corrupt")
        real_rename = km.Path.rename
        real_link = km.os.link
        try:
            # both quarantine arms fail: the no-replace link (r54 P3.13) and the rename fallback
            km.os.link = lambda *a, **kw: (_ for _ in ()).throw(OSError(1, "EPERM"))
            km.Path.rename = lambda self, *a, **kw: (_ for _ in ()).throw(OSError(13, "EACCES"))
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                with self.assertRaises(OSError):
                    km._read_state_json(km._views_path(), "timeline-views")
        finally:
            km.Path.rename = real_rename
            km.os.link = real_link
        self.assertEqual(km._views_path().read_text(), "{corrupt",
                         "the bytes survive — empty-on-failed-quarantine let the caller "
                         "overwrite them (the r53 audit's P2.9)")
        with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
            self.assertIs(km._read_state_json(km._views_path(), "timeline-views"),
                          km._QUARANTINED,
                          "quarantined bytes read as the DISTINCT sentinel — None meant "
                          "missing and the union echo answered [] over data loss (r55 P2.10)")
        q = list(km.jd.STATE.glob("timeline-views.json.corrupt-*"))
        self.assertEqual(len(q), 1, "…and a WORKING quarantine still moves them aside")
        rand = q[0].name.rsplit("-", 1)[1]
        self.assertEqual(len(rand), 32,
                         "128 random bits (r54 P3.13: 32-bit names collided under a forced "
                         "clock, and the replacing rename destroyed the earlier quarantine)")
        self.assertFalse(km._views_path().exists(), "the source retired with the quarantine")
        q[0].unlink()

    def test_a_write_fault_is_the_held_queue_type(self):
        # an ENOSPC/EACCES in the atomic publisher used to fall into the spool's poison
        # counter and .failed valid gestures (the r53 audit's P2.8)
        rodir = km.jd.STATE / "ro-write-fault"
        rodir.mkdir(parents=True, exist_ok=True)
        os.chmod(rodir, 0o500)
        try:
            with self.assertRaises(km._StateUnreadable):
                km._atomic_write(rodir / "f.json", "x")
        finally:
            os.chmod(rodir, 0o700)
            import shutil
            shutil.rmtree(rodir, ignore_errors=True)

    def test_heal_intents_are_durable_and_flushed(self):
        # the r53 audit's P2.7: order discovery marks the sid known regardless of the heal's
        # outcome — nothing else ever retried a failed heal, so a /clear during one store
        # hiccup dropped the session out of its tags forever
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "",
                                         "members": ["11111111-2222-3333-4444-555555555555"]}}])
        real = km._views_path
        try:
            km._views_path = lambda: self._BadPath(real())
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                ok = km._heal_timeline_views("11111111-2222-3333-4444-555555555555",
                                             "66666666-7777-8888-9999-aaaaaaaaaaaa")
        finally:
            km._views_path = real
        self.assertIs(ok, False, "the refused heal reports itself")
        self.assertTrue(km._heals_path().exists(), "…and persists a durable intent")
        km._flush_pending_heals()
        v = km._timeline_views()
        members = [m["sid"] for t in v["tags"] for m in t["members"]]
        self.assertIn("66666666-7777-8888-9999-aaaaaaaaaaaa", members,
                      "the flush completes the heal once the store recovers")
        self.assertFalse(km._heals_path().exists(), "the landed intent retires")


class R53VerifyRound(unittest.TestCase):
    """the r53 verification round's own findings: the P1.2 fix site was unpinned (a revert
    to _union_ops_load would pass every test), the heal flush starved this process's own
    memory intents behind an unreadable intent FILE, and a /clear chained onto a still-queued
    heal read as moot — the membership never walked the second hop."""

    OLD = "11111111-2222-3333-4444-555555555555"
    MID = "66666666-7777-8888-9999-aaaaaaaaaaaa"
    NEW = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def setUp(self):
        _scrub_state()
        km._pending_heals[:] = []
        km._heals_adopted[0] = False
        import shutil
        for pfn in (km._union_ops_path, km._heals_path):
            p = pfn()
            shutil.rmtree(p, ignore_errors=True)
            try:
                p.unlink()
            except OSError:
                pass

    def tearDown(self):
        self.setUp()

    def test_the_payload_publishes_the_marked_echo(self):
        # the r53 P1.2 fix routed the timeline payload through _union_ops_echo (None on an
        # unproved read) — but nothing pinned the SITE: reverting it to _union_ops_load kept
        # every behavior test green while panels went back to reading EIO as retirement
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('"unionOps": _union_ops_echo()', src,
                      "build_timeline must publish the unavailability-marked echo")
        self.assertNotIn('"unionOps": _union_ops_load()', src,
                         "the lenient loader must never feed the payload directly")

    def test_memory_intents_flush_even_when_the_intent_file_is_unreadable(self):
        # the r53 verification round: _flush_pending_heals returned at the failed adoption —
        # intents queued BY THIS PROCESS never flushed while the file stayed broken, though
        # the views store they needed was healthy the whole time
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "",
                                         "members": [self.OLD]}}])
        km._heals_path().mkdir(parents=True, exist_ok=True)   # a directory: reads raise OSError
        with contextlib.redirect_stderr(io.StringIO()):
            km._queue_heal_intent(self.OLD, self.NEW)         # memory-only (adoption fails)
            km._flush_pending_heals()
        members = [m["sid"] for t in km._timeline_views()["tags"] for m in t["members"]]
        self.assertIn(self.NEW, members, "the in-memory intent flushed — the broken intent "
                      "FILE withholds only the file rewrite, never the heal")
        self.assertEqual(km._pending_heals, [], "the landed intent left memory")
        self.assertTrue(km._heals_path().is_dir(), "…and the unreadable file was not touched")

    def test_a_clear_chained_onto_a_queued_heal_is_not_moot(self):
        # the r53 verification round: OLD->MID queued behind a store fault, then a /clear
        # mints MID->NEW while MID is not yet in any tag — the moot early-return dropped the
        # second hop and the live session never regained its tags
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "",
                                         "members": [self.OLD]}}])
        with contextlib.redirect_stderr(io.StringIO()):
            km._queue_heal_intent(self.OLD, self.MID)
            ok = km._heal_timeline_views(self.MID, self.NEW)
        self.assertIs(ok, False, "queued behind the pending hop, not moot")
        self.assertIn({"old": self.MID, "new": self.NEW}, km._pending_heals)
        km._flush_pending_heals()
        km._flush_pending_heals()   # hop order in the queue is not guaranteed — one more pass
        members = [m["sid"] for t in km._timeline_views()["tags"] for m in t["members"]]
        self.assertIn(self.MID, members, "the first hop landed")
        self.assertIn(self.NEW, members, "…and the chained hop walked the membership through")
        self.assertFalse(km._heals_path().exists(), "both intents retired")

    def test_the_merge_ratchets_dispatched_true(self):
        # the r53 wave-3 verification: an adopter panel's full-replace mirror of rows it
        # seeded BEFORE the writer's flip regressed the journal to dispatched:false and
        # re-armed the completion pass into re-running an executed gesture
        self.assertTrue(km._union_ops_set([{"host": "TESTHOST-A", "gid": 9, "edit": {},
                                            "inverse": {}, "rt": {}, "name": "pool",
                                            "dispatched": True}]))
        self.assertTrue(km._union_ops_set([{"host": "TESTHOST-A", "gid": 9, "edit": {},
                                            "inverse": {}, "rt": {}, "name": "pool",
                                            "dispatched": False}]))
        rows = km._union_ops_load()
        self.assertEqual([r["dispatched"] for r in rows], [True],
                         "'the effects ran' is one-way evidence — a merge never regresses it")
        # …but a FRESH row's false is honest state, not a regression
        self.assertTrue(km._union_ops_set([{"host": "TESTHOST-B", "gid": 9, "edit": {},
                                            "inverse": {}, "rt": {}, "name": "pool",
                                            "dispatched": False}]))
        rows = {r["host"]: r["dispatched"] for r in km._union_ops_load()}
        self.assertEqual(rows, {"TESTHOST-A": True, "TESTHOST-B": False})

    def test_an_arriving_dispatch_flips_the_journal(self):
        # the r53 wave-3 verification: the writer's own dispatched:true re-post can be lost
        # (a dying webview, a refused flip write) — the arriving editTag carrying the gid as
        # opId IS the dispatch evidence, flipped kernel-side before the (possibly slow) forward
        self.assertTrue(km._union_ops_set([
            {"host": "TESTHOST-A", "gid": 11, "edit": {}, "inverse": {}, "rt": {},
             "name": "pool", "dispatched": False},
            {"host": "TESTHOST-B", "gid": 11, "edit": {}, "inverse": {}, "rt": {},
             "name": "pool", "dispatched": False},
            {"refusal": True, "gid": -11, "opId": "11", "host": "TESTHOST-A",
             "name": "pool", "t": int(__import__("time").time())}]))
        self.assertTrue(km._union_ops_mark_dispatched("11", "TESTHOST-A"))
        rows = {r["host"]: r.get("dispatched") for r in km._union_ops_load()
                if not r.get("refusal")}
        self.assertEqual(rows, {"TESTHOST-A": True, "TESTHOST-B": False},
                         "the flip is scoped to the ONE host the edit addressed (r54 P1.1: "
                         "the gid-wide flip marked unsent B dispatched when only A arrived — "
                         "a renderer dying between sends left B stranded, unadoptable)")
        self.assertNotIn("dispatched", [k for r in km._union_ops_load() if r.get("refusal")
                                        for k in ("dispatched",) if k in r],
                         "refusal rows are events, never flipped")
        # a re-keyed completion row still matches through its carried original id
        self.assertTrue(km._union_ops_set([{"host": "TESTHOST-B", "gid": 77, "ogid": 11,
                                            "edit": {}, "inverse": {}, "rt": {},
                                            "name": "pool", "dispatched": False}], [11]))
        self.assertTrue(km._union_ops_mark_dispatched("11", "TESTHOST-B"))
        self.assertEqual([r.get("dispatched") for r in km._union_ops_load()
                          if r.get("gid") == 77], [True], "ogid carries the correlation")
        self.assertFalse(km._union_ops_mark_dispatched("web7", "TESTHOST-A"),
                         "a web-minted opId names no gesture")
        self.assertFalse(km._union_ops_mark_dispatched(-11, "TESTHOST-A"))
        self.assertFalse(km._union_ops_mark_dispatched("11", ""),
                         "no host, no evidence")
        # …and the editTag handler flips BEFORE the forward, which can block for seconds
        src = open(os.path.join(BIN, "romp-kernel")).read()
        i_flip = src.index("_union_ops_mark_dispatched(op_id, host)")
        i_fwd = src.index("ans, err = _forward_tag_edit(host, body)")
        self.assertLess(i_flip, i_fwd, "arrival is the event — the flip never waits on the owner")

    def test_a_failed_adoption_queues_the_chained_heal_conservatively(self):
        # the r53 wave-3 verification: a restart with the intent file unreadable scanned an
        # EMPTY memory list, read a real second hop as moot, and dropped it forever
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "",
                                         "members": [self.OLD]}}])
        km._heals_path().mkdir(parents=True, exist_ok=True)   # unreadable: reads raise OSError
        with contextlib.redirect_stderr(io.StringIO()):
            ok = km._heal_timeline_views(self.MID, self.NEW)
        self.assertIs(ok, False, "no adoption = no information — never moot")
        self.assertIn({"old": self.MID, "new": self.NEW}, km._pending_heals,
                      "the hop is queued; a spurious intent moots itself at flush")

    def test_a_truly_unknown_sid_is_still_moot(self):
        # the chained check must not turn every unknown-sid heal into an immortal intent
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "",
                                         "members": [self.OLD]}}])
        ok = km._heal_timeline_views(self.MID, self.NEW)
        self.assertIsNone(ok, "no tag holds it and no pending intent will add it: moot")
        self.assertEqual(km._pending_heals, [])



class R54AuditFixes(unittest.TestCase):
    """the v1.3.26 audit: gid-wide dispatch marking stranded mixed groups (P1.1), completion
    claims did not exist (P1.3), an EIO payload judged settlements (P1.4), the proved reader
    skipped the hidden migration (P2.6), order published sids over memory-only heal intents
    (P2.8), a quarantined journal echoed as authoritative [] (P2.11), parent-dir faults
    bypassed the retryable type (P2.12), and quarantine names could collide+replace (P3.13)."""

    OLD = "11111111-2222-3333-4444-555555555555"
    NEW = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def setUp(self):
        _scrub_state()
        km._pending_heals[:] = []
        km._heals_adopted[0] = False
        km._union_claims.clear()
        import shutil
        for pfn in (km._union_ops_path, km._heals_path):
            p = pfn()
            shutil.rmtree(p, ignore_errors=True)
            try:
                p.unlink()
            except OSError:
                pass
        try:
            (km.jd.STATE / "session-order.json").unlink()
        except OSError:
            pass

    def tearDown(self):
        self.setUp()

    class _BadPath:
        def __init__(self, real):
            self._real = real

        def read_text(self):
            raise OSError(5, "EIO")

        def stat(self):
            raise OSError(5, "EIO")

        def exists(self):
            return True

        def __getattr__(self, k):
            return getattr(self._real, k)

    def _seed_rows(self, *gids):
        self.assertTrue(km._union_ops_set(
            [{"host": "TESTHOST-A", "gid": g, "edit": {}, "inverse": {}, "rt": {},
              "name": "pool", "dispatched": False} for g in gids]))

    def test_claims_are_exclusive_and_journal_verified(self):
        # r54 P1.3 + r55 P1.4: a grant now proves an extant UNDISPATCHED row under the same
        # lock the merge commits under — the free-floating grant let a bystander claim a gid
        # the owner had just retired, and the stale claimant re-keyed a settled gesture back
        self.assertIsNone(km._union_claim_grant(9, "ws:1"),
                          "no journal row, no claim — nothing exists to complete")
        self._seed_rows(9, 10)
        e1 = km._union_claim_grant(9, "ws:1")
        self.assertIsNotNone(e1)
        self.assertIsNone(km._union_claim_grant(9, "ws:2"), "a live holder refuses")
        self.assertEqual(km._union_claim_grant(9, "ws:1"), e1,
                         "a re-grant refreshes and keeps the SAME epoch — the held token "
                         "stays valid for the rekey CAS")
        km._union_claims_release("ws:1")
        self.assertIsNotNone(km._union_claim_grant(9, "ws:2"),
                             "the socket-close release frees a dead writer's gestures")
        # POST claimants have no close event — the TTL backstop yields a stale one
        self.assertIsNotNone(km._union_claim_grant(10, "cid:a"))
        self.assertIsNone(km._union_claim_grant(10, "cid:b"))
        km._union_claims[10]["t"] -= km._UNION_CLAIM_TTL + 1
        self.assertIsNotNone(km._union_claim_grant(10, "cid:b"), "a stale POST claim yields")
        self.assertNotIn(10, {g for g, c in km._union_claims.items()
                              if c["ckey"] == "cid:a"}, "…and the sweep dropped the corpse")
        self.assertIsNone(km._union_claim_grant(0, "ws:1"))
        self.assertIsNone(km._union_claim_grant("x", "ws:1"))
        # a DISPATCHED row is not completable — no claim over settled work
        self.assertTrue(km._union_ops_set([{"host": "TESTHOST-A", "gid": 11, "edit": {},
                                            "inverse": {}, "rt": {}, "name": "pool",
                                            "dispatched": True}]))
        self.assertIsNone(km._union_claim_grant(11, "ws:3"))

    def test_the_journal_write_claims_for_its_writer(self):
        # the implicit writer claim: a live writer is never raced by a completer, and the
        # ack NAMES gestures a completer already holds so the gate yields
        rows21 = [{"host": "TESTHOST-A", "gid": 21, "edit": {}, "inverse": {}, "rt": {},
                   "name": "pool", "dispatched": False}]
        ok, unclaimed, _ = km._union_ops_merge(rows21, ckey="ws:9")
        self.assertTrue(ok)
        self.assertEqual(unclaimed, [], "the writer claims its own gesture at journal time")
        self.assertIsNone(km._union_claim_grant(21, "ws:other"), "the writer holds 21")
        km._union_claims.clear()
        self.assertIsNotNone(km._union_claim_grant(21, "ws:completer"))
        ok, unclaimed, _ = km._union_ops_merge(rows21, ckey="ws:9")
        self.assertTrue(ok)
        self.assertEqual(unclaimed, [21],
                         "a completer-held gesture is named back to the writer")

    def test_the_rekey_write_is_a_cas_against_the_claim_epoch(self):
        # r55 P1.4, executed there: a bystander's claim outlived the owner's retirement and
        # the stale claimant re-keyed gid 7001 to 7002 — resurrecting the settled operation
        self._seed_rows(7001)
        epoch = km._union_claim_grant(7001, "ws:comp")
        self.assertIsNotNone(epoch)
        # the owner retires the gesture while the claim is held
        self.assertTrue(km._union_ops_set([], [7001]))
        ok, _, reason = km._union_ops_merge(
            [{"host": "TESTHOST-A", "gid": 7002, "ogid": 7001, "edit": {}, "inverse": {},
              "rt": {}, "name": "pool", "dispatched": False}],
            [7001], ckey="ws:comp", rekey={"ogid": 7001, "gid": 7002, "epoch": epoch})
        self.assertFalse(ok, "the settled gesture NEVER resurrects")
        self.assertTrue("stale" in (reason or "") or "settled" in (reason or ""),
                        "the retirement dropped the claim (P3.19), so the CAS refuses as "
                        "stale — either arm blocks the resurrection")
        self.assertEqual(km._union_ops_load(), [], "…and the journal stays empty")
        # the healthy path: rows standing, epoch valid → the rekey commits atomically
        self._seed_rows(7003)
        epoch = km._union_claim_grant(7003, "ws:comp")
        ok, _, reason = km._union_ops_merge(
            [{"host": "TESTHOST-A", "gid": 7004, "ogid": 7003, "edit": {}, "inverse": {},
              "rt": {}, "name": "pool", "dispatched": False}],
            [7003], ckey="ws:comp", rekey={"ogid": 7003, "gid": 7004, "epoch": epoch})
        self.assertTrue(ok, reason)
        self.assertEqual([r["gid"] for r in km._union_ops_load()], [7004])
        # a WRONG epoch (someone re-claimed since) refuses too
        self.assertTrue(km._union_ops_set([], [7004]))
        self._seed_rows(7005)
        km._union_claim_grant(7005, "ws:comp")
        ok, _, reason = km._union_ops_merge(
            [], [7005], ckey="ws:comp", rekey={"ogid": 7005, "gid": 7006, "epoch": 999999})
        self.assertFalse(ok)
        self.assertIn("stale", reason or "")
        # claims retire WITH their rows (r55 P3.19)
        self._seed_rows(7007)
        km._union_claim_grant(7007, "ws:x")
        self.assertTrue(km._union_ops_set([], [7007]))
        self.assertNotIn(7007, km._union_claims)

    def test_the_payload_views_carry_the_unproved_marker(self):
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "",
                                         "members": []}}])
        self.assertNotIn("unproved", km._views_client(), "a healthy store is proved")
        real = km._views_path
        try:
            km._views_path = lambda: self._BadPath(real())
            v = km._views_client()
        finally:
            km._views_path = real
        self.assertIs(v.get("unproved"), True,
                      "an EIO store renders the lenient shape but SAYS so (r54 P1.4: the "
                      "silent empty read as a pending delete's postimage and the recovery "
                      "rows retired on zero information)")
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._views_path().write_text("{garbage")
        v2 = km._views_client()
        self.assertIs(v2.get("unproved"), True, "malformed bytes are a fabrication too")
        km._views_path().unlink()
        km._flags_cache.clear()
        self.assertNotIn("unproved", km._views_client(), "missing = honestly empty, proved")

    def test_the_proved_reader_migrates_hidden_memberships(self):
        # r54 P2.6, executed there: the same stored blob kept sid-hidden on the lenient
        # reader and LOST it through the proved mutation snapshot — the first post-upgrade
        # tag edit erased every hidden membership
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._views_path().write_text(json.dumps({"active": "all", "tags": [],
                                                "hidden": [self.OLD], "rev": 3}))
        v = km._timeline_views_proved()
        arch = next((t for t in v["tags"] if t["name"] == "archived"), None)
        self.assertIsNotNone(arch, "the proved snapshot runs the SAME migration")
        self.assertIn(self.OLD, [m["sid"] for m in arch["members"]])
        with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
            out, err = km._edit_tag("new-tag", add=[])
        self.assertIsNone(err)
        v2 = km._timeline_views()
        arch2 = next((t for t in v2["tags"] if t["name"] == "archived"), None)
        self.assertIsNotNone(arch2, "…so the first post-upgrade edit PRESERVES them")
        self.assertIn(self.OLD, [m["sid"] for m in arch2["members"]])

    def test_order_withholds_a_sid_whose_heal_intent_is_memory_only(self):
        # r54 P2.8, executed there: a crash after _write_session_order left the sid known
        # while the memory-only intent died with the process — the heal never retried
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "",
                                         "members": [self.OLD]}}])
        km._write_session_order([self.OLD])
        km._heals_path().mkdir(parents=True, exist_ok=True)   # intent file unwritable
        real = km._views_path
        sessions = [{"sid": self.OLD, "name": "web"}, {"sid": self.NEW, "name": "web"}]
        try:
            km._views_path = lambda: self._BadPath(real())    # the heal itself fails too
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                km._ordered_locked(list(sessions))
        finally:
            km._views_path = real
        self.assertNotIn(self.NEW, km._session_order(),
                         "no durable intent, no published slot — the sid stays new")
        import shutil
        shutil.rmtree(km._heals_path(), ignore_errors=True)   # the stores recover
        with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
            km._ordered_locked(list(sessions))
        self.assertIn(self.NEW, km._session_order(), "…and the next pass heals AND publishes")
        members = [m["sid"] for t in km._timeline_views()["tags"] for m in t["members"]]
        self.assertIn(self.NEW, members)

    def test_a_quarantined_journal_echoes_none_not_empty(self):
        # r54 P2.11: the quarantine preserved the bytes but the [] echo read as authoritative
        # owner retirement — panels deleted their usable in-memory recovery copies
        self.assertEqual(km._union_ops_echo(), [], "missing = honestly empty")
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._union_ops_path().write_text("{garbage")
        with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
            self.assertIsNone(km._union_ops_echo(),
                              "quarantined garbage is DATA LOSS — no information, never []")
        for q in km.jd.STATE.glob("union-gestures.json.corrupt-*"):
            q.unlink()

    def test_a_parent_dir_fault_is_the_held_queue_type(self):
        # r54 P2.12: the mkdir ran OUTSIDE the _StateUnreadable wrap — a repeated ENOSPC
        # there fell into the spool's poison counter and .failed a valid gesture
        rodir = km.jd.STATE / "ro-parent-fault"
        rodir.mkdir(parents=True, exist_ok=True)
        os.chmod(rodir, 0o500)
        try:
            with self.assertRaises(km._StateUnreadable):
                km._atomic_write(rodir / "sub" / "f.json", "x")
        finally:
            os.chmod(rodir, 0o700)
            import shutil
            shutil.rmtree(rodir, ignore_errors=True)

    def test_the_marker_and_the_shape_come_from_one_read(self):
        # the r54 wave-2 verification: the wave-1 probe was a SECOND read — a fault between
        # the probe and the render sent fabricated-empty tags out unmarked. One read decides
        # both now; pin the construction and the remote-leg guards.
        src = open(os.path.join(BIN, "romp-kernel")).read()
        fn = src.index("def _views_payload_marked():")
        window = src[fn:fn + 2600]
        self.assertIn("_migrate_hidden_blob(_pd", window,
                      "the proved arm builds the payload from the PROBE's own parse")
        # the SHARED helper serves both the local payload and GET /views (r55 P1.6: the
        # route kept its own two-read probe and re-opened the fabricated-empty window for
        # the POLLING kernel), and both poller cache sites keep last-good over unproved
        fcl = src.index("def _views_client():")
        self.assertIn("v = _views_payload_marked()", src[fcl:fcl + 1400])
        self.assertIn("json.dumps(_views_payload_marked())", src)
        self.assertIn('if rv is not None and not (isinstance(rv, dict) and rv.get("unproved")):',
                      src)
        self.assertIn('if rviews is not None and not (isinstance(rviews, dict)', src)

    def test_a_withheld_sid_still_renders_in_its_inherited_slot(self):
        # the r54 wave-2 verification: sorting the withheld sid LAST made the watched lane
        # jump to the end and snap back when the store recovered — two unprompted reorders
        OTHER = "99999999-8888-7777-6666-555555555555"
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "",
                                         "members": [self.OLD]}}])
        km._write_session_order([self.OLD, OTHER])
        km._heals_path().mkdir(parents=True, exist_ok=True)
        real = km._views_path
        sessions = [{"sid": self.OLD, "name": "web"}, {"sid": OTHER, "name": "api"},
                    {"sid": self.NEW, "name": "web"}]
        try:
            km._views_path = lambda: self._BadPath(real())
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                out = km._ordered_locked(list(sessions))
        finally:
            km._views_path = real
        self.assertEqual([s["sid"] for s in out], [self.OLD, self.NEW, OTHER],
                         "the fork RENDERS right after its sibling even while the persisted "
                         "slot waits on the durable heal")
        self.assertNotIn(self.NEW, km._session_order(), "…and the persisted order still waits")

    def test_a_wrong_shaped_store_never_reads_as_proved_empty(self):
        # r55 P2.9, executed there: a stored null (and []) returned without unproved, and
        # the next edit overwrote it under a success ack
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._views_path().write_text("null")
        v = km._views_payload_marked()
        self.assertIs(v.get("unproved"), True, "valid bytes, garbage shape — a fabrication")
        km._flags_cache.clear()
        with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
            pv = km._timeline_views_proved()
        self.assertEqual(pv["tags"], [], "the WRITER's snapshot quarantines the bytes aside")
        q = list(km.jd.STATE.glob("timeline-views.json.corrupt-*"))
        self.assertEqual(len(q), 1, "…preserved, never overwritten")
        self.assertEqual(q[0].read_text(), "null")
        q[0].unlink()

    def test_every_views_rmw_runs_the_hidden_migration(self):
        # r55 P2.11, executed there: the targeted-ops writer and the reference migration
        # both normalized `hidden` away WITHOUT archiving — one lens pick (or one remote
        # rename echo) erased legacy memberships the r54 fix had preserved elsewhere
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._views_path().write_text(json.dumps({"active": "all", "tags": [],
                                                "hidden": [self.OLD], "rev": 1}))
        km._flags_cache.clear()
        km._apply_views_ops([{"create": {"id": "gz", "name": "zz", "color": "",
                                         "members": []}}])
        v = km._timeline_views()
        arch = next((x for x in v["tags"] if x["name"] == "archived"), None)
        self.assertIsNotNone(arch, "the targeted-ops writer migrates")
        self.assertIn(self.OLD, [m["sid"] for m in arch["members"]])
        # the ref-migration writer too
        km._views_path().write_text(json.dumps({"active": "all",
                                                "tags": [{"id": "g1", "name": "pool",
                                                          "color": "", "members": []}],
                                                "hidden": [self.OLD], "rev": 1}))
        km._flags_cache.clear()
        with mock.patch.object(km, "_tag_name_survives_elsewhere", return_value=False):
            self.assertTrue(km._apply_ref_migration("TESTHOST-A", "pool", "crew", False))
        v2 = km._timeline_views()
        arch2 = next((x for x in v2["tags"] if x["name"] == "archived"), None)
        self.assertIsNotNone(arch2, "the reference migration migrates too")
        self.assertIn(self.OLD, [m["sid"] for m in arch2["members"]])

    def test_a_lock_fault_is_the_held_queue_type(self):
        # r55 P2.12, executed there: an EIO acquiring the identity lock raised a plain
        # OSError, the spool's poison counter counted it against the op, and a VALID queued
        # gesture was quarantined into .failed on the fifth pass
        def _boom():
            raise OSError(5, "EIO")
        with mock.patch.object(km.jd, "_identity_file_lock", side_effect=_boom):
            with self.assertRaises(km._StateUnreadable):
                km._apply_views_ops([{"active": "all"}])

    def test_withheld_same_name_forks_keep_their_order(self):
        # r55 P3.18: anchoring every fork to the persisted sibling REVERSED them (insert-
        # after put the newest first), and the order flipped again at recovery
        OTHER = "99999999-8888-7777-6666-555555555555"
        NEW2 = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
        km._apply_views_ops([{"create": {"id": "g1", "name": "pool", "color": "",
                                         "members": [self.OLD]}}])
        km._write_session_order([self.OLD, OTHER])
        km._heals_path().mkdir(parents=True, exist_ok=True)
        real = km._views_path
        sessions = [{"sid": self.OLD, "name": "web"}, {"sid": OTHER, "name": "api"},
                    {"sid": self.NEW, "name": "web"}, {"sid": NEW2, "name": "web"}]
        try:
            km._views_path = lambda: self._BadPath(real())
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                out = km._ordered_locked(list(sessions))
        finally:
            km._views_path = real
        self.assertEqual([s["sid"] for s in out], [self.OLD, self.NEW, NEW2, OTHER],
                         "the fork CHAIN renders oldest-first — the same order the eventual "
                         "persistence will freeze, so recovery moves nothing")

    def test_two_quarantines_both_survive(self):
        # r54 P3.13: 32-bit names under a forced clock collided and the replacing rename
        # destroyed the first quarantine's bytes — 128-bit + no-replace now
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        for payload in ("{first", "{second"):
            km._views_path().write_text(payload)
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                self.assertIs(km._read_state_json(km._views_path(), "timeline-views"),
                              km._QUARANTINED)
        q = sorted(km.jd.STATE.glob("timeline-views.json.corrupt-*"))
        self.assertEqual(len(q), 2, "both corruptions kept their bytes")
        self.assertEqual(sorted(p.read_text() for p in q), ["{first", "{second"])
        for p in q:
            p.unlink()
