#!/usr/bin/env python3
"""Session views (the user 2026-08-18): one timeline-views.json blob under STATE — {"active", "hidden",
"groups"} — deciding which sessions show on the timeline lanes AND the chat tab strip. "all" shows
everything except the hidden set (a hidden session is a BACKGROUND session: still judged and carded,
surfaced by the feed and pickers); a named group shows exactly its members, membership beating the
hidden bit. Local-kernel persisted (a viewer display pref, not federated). These pin the storage
helpers, the visibility decision, the normalizer, the churn heal, the WS op, and the payload echoes.
Synthetic only."""
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = SourceFileLoader("romp_kernel_tv", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd

G1 = {"id": "g1", "name": "pool", "color": "#DD42FF", "members": ["s2", "s3"]}


class TimelineViews(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        km._flags_cache.clear()

    def tearDown(self):
        jd.STATE = self.saved
        self.td.cleanup()

    def test_default_shows_everything(self):
        v = km._timeline_views()
        self.assertEqual(v, {"active": "all", "hidden": [], "groups": []})
        self.assertTrue(km._view_visible(v, "anything"))

    def test_hidden_hides_in_all_but_membership_beats_it(self):
        km._set_timeline_views({"active": "all", "hidden": ["s2"], "groups": [G1]})
        km._flags_cache.clear()
        v = km._timeline_views()
        self.assertFalse(km._view_visible(v, "s2"), "hidden in the all view")
        self.assertTrue(km._view_visible(v, "s1"))
        km._set_timeline_views({"active": "g1", "hidden": ["s2"], "groups": [G1]})
        km._flags_cache.clear()
        v = km._timeline_views()
        self.assertTrue(km._view_visible(v, "s2"), "explicit membership beats the hidden bit")
        self.assertFalse(km._view_visible(v, "s1"), "a group shows exactly its members")

    def test_normalizer_drops_junk_and_falls_back_to_all(self):
        v = km._norm_timeline_views({"active": "ghost", "hidden": ["a", 7, "", "a"],
                                     "groups": [{"id": "g1", "name": "x" * 99, "members": ["m", 3]},
                                                {"noid": True}, "junk"]})
        self.assertEqual(km._norm_timeline_views({"hidden": 7, "groups": "nope"}),
                         {"active": "all", "hidden": [], "groups": []},
                         "wrong-TYPED fields drop instead of raising")
        self.assertEqual(km._norm_timeline_views({"groups": [{"id": "g", "members": 3}]})["groups"][0]["members"],
                         [], "a wrong-typed members list drops, never raises")
        self.assertEqual(v["active"], "all", "an active group that does not exist falls back to all")
        self.assertEqual(v["hidden"], ["a"], "junk and duplicates dropped")
        self.assertEqual(len(v["groups"]), 1)
        self.assertEqual(len(v["groups"][0]["name"]), km._VIEWS_MAX_NAME)
        self.assertEqual(v["groups"][0]["members"], ["m"])

    def test_cache_invalidates_on_write(self):
        self.assertEqual(km._timeline_views()["hidden"], [])
        km._set_timeline_views({"hidden": ["s9"]})
        self.assertEqual(km._timeline_views()["hidden"], ["s9"], "mtime+size key sees the write")

    def test_churn_heal_copies_hidden_and_membership(self):
        # COPY, never move: stripping the old sid un-hid its dead lane (it lingers on the timeline for
        # hours), and a still-alive same-name session would have its state stolen
        km._set_timeline_views({"active": "all", "hidden": ["old"], "groups": [
            {"id": "g1", "name": "pool", "members": ["old", "other"]}]})
        km._heal_timeline_views("old", "new")
        v = km._timeline_views()
        self.assertEqual(v["hidden"], ["new", "old"], "the fork inherits the hidden bit; the old sid keeps it")
        self.assertEqual(v["groups"][0]["members"], ["new", "old", "other"], "membership copies the same way")
        before = json.loads((jd.STATE / "timeline-views.json").read_text())
        km._heal_timeline_views("stranger", "new2")   # untouched sid → no write at all
        self.assertEqual(json.loads((jd.STATE / "timeline-views.json").read_text()), before)

    def test_ordered_fork_splice_heals_views(self):
        # the same name-keyed splice that inherits the ORDER slot carries the views state with it
        km._write_session_order(["old"])
        (jd.STATE / "names").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "names" / "old").write_text("web\t/tmp\t#123456\twhite\n")
        km._set_timeline_views({"active": "all", "hidden": ["old"], "groups": []})
        km._ordered([{"sid": "old", "name": "web"}, {"sid": "new", "name": "web"}])
        self.assertEqual(km._timeline_views()["hidden"], ["new", "old"], "copied, so the dead lane stays hidden too")

    def test_ws_op_persists_via_normalizer(self):
        # the handler body is _set_timeline_views + _mark_views_dirty; pin the setter's normalization
        km._set_timeline_views({"active": "g9", "hidden": ["x"], "groups": []})
        v = json.loads((jd.STATE / "timeline-views.json").read_text())
        self.assertEqual(v["active"], "all")
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('msg.get("type") == "setTimelineViews"', src)
        self.assertIn('_set_timeline_views(msg["views"])', src)

    def test_payloads_echo_the_views_blob(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('"views": _timeline_views(),', src, "the timeline payload carries it")
        self.assertIn('"palette": pal.colors(_palette_name()),', src, "and the palette, for group colors in every host")
        self.assertIn('"tabs": tab_meta, "views": _timeline_views()', src, "tabOrder pushes carry it")
        self.assertIn('"tabs": _tabs, "views": _timeline_views()', src, "the connect-time tabOrder carries it")

    def test_web_boot_exposes_the_set_views_hook(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("window.__rompTimelineSetViews=function(views)", src)
        self.assertIn('post({type:"setTimelineViews",views:views});', src)

    def test_focus_switches_to_a_view_that_shows_the_session(self):
        # the reveal rule, default-group model (the user 2026-08-19): focusing switches the active
        # view and never mutates membership — peeking at a pool worker must not drag it back into
        # the default group. Order: the default group, else the first named group holding it, else
        # (in no view at all) re-add to the default group.
        G = {"id": "g1", "name": "pool", "members": ["s2"]}
        km._set_timeline_views({"active": "g1", "hidden": [], "groups": [G]})
        km._reveal_chat_for({"wid": "w1"}, {"type": "focus", "id": "s9"})
        v = km._timeline_views()
        self.assertEqual(v["active"], "all", "still in the default group → switch to it")
        km._set_timeline_views({"active": "all", "hidden": ["s2"], "groups": [G]})
        km._reveal_chat_for({"wid": "w1"}, {"type": "focus", "id": "s2"})
        v = km._timeline_views()
        self.assertEqual(v["active"], "g1", "out of default but in a group → switch to that group")
        self.assertEqual(v["hidden"], ["s2"], "membership untouched — the peek is temporary")
        km._set_timeline_views({"active": "all", "hidden": ["sX"], "groups": [G]})
        km._reveal_chat_for({"wid": "w1"}, {"type": "focus", "id": "sX"})
        v = km._timeline_views()
        self.assertEqual(v["hidden"], [], "in NO view at all → re-added to the default group")
        km._set_timeline_views({"active": "g1", "hidden": [], "groups": [G]})
        km._reveal_chat_for({"wid": "w1"}, {"type": "focus", "id": "s2"})
        self.assertEqual(km._timeline_views()["active"], "g1", "a member's focus changes nothing")
