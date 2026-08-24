#!/usr/bin/env python3
"""A TOP-LEVEL courier handoff tracking node's card titles the WORK and wears the delegation as a
provenance badge (the user 2026-08-24, screenshot of a manager-board card titled
"↪ delegated to <peer>: <work>", arrow and all).

_plant_handoff_track parents its tracking node under the sender's related open goal when one exists,
else top-level — a designed fallback whose seams showed: a top-level node's text IS the card title.
Presentation only (mint/retire/propagate untouched): build_feed now derives the card's title and a
handoffTo badge through _handoff_card_fields — the badge mirrors the recipient-side "↪ from <peer>"
(origin): identity color, quiet host: prefix for a recipient this kernel can't resolve, click opens
the recipient. Identity resolves registry-first (a rename reads fresh), then the courier's
plant-time label, then the sid stub; the title falls back through why/summary before keeping the
de-arrowed label. Nested handoff rows are untouched. SYNTHETIC fixtures only."""
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from inspect import getsource
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_hocard", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"    # the delegating session
PEER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"   # the recipient
T0 = 1_781_100_000


class _Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self._saved_state, self._saved_names = jd.STATE, km.NAMES
        jd._rebind_state(td)
        km.NAMES = td / "names"          # _name_of reads the kernel-module global, not jd.STATE live
        km.NAMES.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        jd._rebind_state(self._saved_state)
        km.NAMES = self._saved_names
        self.td.cleanup()

    def _store_with_track(self, text="Build tracked exporter with delegator-homed card",
                          peer_name="worker_two", parent=None):
        """A sender store whose handoff tracking node was minted by the REAL planter, so the label
        format under test is the one production writes."""
        store = {"rompUuid": SID, "seq": 0, "placements": {}, "status": {}, "nodes": {}}
        nid = jd._plant_handoff_track(store, parent, text, PEER, peer_name, T0, "m1")
        return store, nid

    def _name(self, sid, name):
        (km.NAMES / sid).write_text(name + "\t/tmp\t#123456\t#ffffff")


class HandoffCardFields(_Base):
    def test_top_level_track_titles_the_work_and_badges_the_peer(self):
        store, nid = self._store_with_track()
        badge, title = km._handoff_card_fields(store["nodes"], nid)
        self.assertEqual(title, "Build tracked exporter with delegator-homed card",
                         "the WORK is the title — no arrow, no provenance phrase")
        self.assertNotIn("↪", title)
        self.assertEqual(badge["peerSid"], PEER, "click target: the recipient session")
        self.assertEqual(badge["peer"], "worker_two",
                         "registry miss → the plant-time label names the recipient")
        self.assertIn("color", badge, "identity color rides the badge like origin's")

    def test_registry_name_outranks_the_plant_time_label(self):
        store, nid = self._store_with_track(peer_name="stale_old_name")
        self._name(PEER, "renamed_worker")
        badge, _ = km._handoff_card_fields(store["nodes"], nid)
        self.assertEqual(badge["peer"], "renamed_worker", "a rename reads fresh, same as origin")
        self.assertEqual(badge["peerHost"], "", "a local resolve means a local peer — no host prefix")

    def test_federated_label_splits_into_quiet_host_prefix(self):
        store, nid = self._store_with_track(peer_name="boxa:worker_two")
        badge, _ = km._handoff_card_fields(store["nodes"], nid)
        self.assertEqual((badge["peerHost"], badge["peer"]), ("boxa", "worker_two"),
                         "host: prefix renders the way remote names do everywhere else")

    def test_label_only_node_falls_back_to_why_then_summary(self):
        store, nid = self._store_with_track(text="")          # planter writes "(work)"
        store["nodes"][nid]["why"] = "port the exporter to the new card schema"
        _, title = km._handoff_card_fields(store["nodes"], nid)
        self.assertEqual(title, "port the exporter to the new card schema")
        store["nodes"][nid]["why"] = None
        store["nodes"][nid]["summary"] = "the exporter now ships cards"
        _, title = km._handoff_card_fields(store["nodes"], nid)
        self.assertEqual(title, "the exporter now ships cards")

    def test_a_plain_goal_is_untouched(self):
        nodes = {SID + ":g9": {"id": SID + ":g9", "text": "Ship the exporter", "parentId": None}}
        badge, title = km._handoff_card_fields(nodes, SID + ":g9")
        self.assertIsNone(badge, "no handoff → no badge")
        self.assertEqual(title, "Ship the exporter", "…and the text passes through verbatim")


class BuildFeedWiring(_Base):
    """Source pins: build_feed routes the card's title/badge through the helper, and NESTED handoff
    rows keep their raw text (they render as identity rows in the delegations section — the label
    never was the problem there)."""

    def test_the_item_ships_the_derived_title_and_badge(self):
        src = getsource(km.build_feed)
        self.assertIn("handoff_to, card_text = _handoff_card_fields(nodes, nid)", src)
        self.assertIn('"text": card_text', src)
        self.assertIn('**({"handoffTo": handoff_to} if handoff_to else {})', src)

    def test_nested_tree_rows_keep_their_raw_text(self):
        src = getsource(km.build_feed)
        self.assertIn('"kind": "handoff" if _ho_sid else "ask", "text": nd["text"]', src,
                      "flatten's rows are untouched — nested handoff rows render as before")


if __name__ == "__main__":
    unittest.main()
