#!/usr/bin/env python3
"""save_goals SKIPS a publish that would rewrite the file with identical content (the user 2026-07-22).

Callers save unconditionally by design: _plan_session ends every pass with a rollup + save whether or not
the pass placed anything. On an idle fleet that rewrote ~24 goal stores with byte-identical content roughly
ten times a second, with `rev` counters past 10,000 as the receipt.

The write itself is cheap; the damage is downstream. The kernel's _compact_goal_stores skips any store whose
mtime hasn't moved ("the steady state is just stats"), so a no-op republish moved every mtime every pass and
kept the sweep re-processing the whole live fleet forever. Skipping is safe BECAUSE nothing changed: a writer
with no events to contribute can neither lose its own work nor clobber a concurrent writer's.

All fixtures SYNTHETIC: placeholder UUID, invented goal text.
"""
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1781100000
T0 = NOW - 3600


class NoOpPublish(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self.td = tempfile.TemporaryDirectory()
        jd._rebind_state(Path(self.td.name))

    def tearDown(self):
        jd._rebind_state(self._saved)
        self.td.cleanup()

    def _file(self):
        return jd.GOALDIR / (SID + ".json")

    def _seed(self):
        s = {"rompUuid": SID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
             "placements": {}, "status": {}}
        jd.apply_plan(s, "s1", T0, [{"do": "mint", "why": "x", "text": "A goal"}], [])
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(SID, s)

    # ── the fix ─────────────────────────────────────────────────────────────
    def test_an_unchanged_store_is_not_republished(self):
        self._seed()
        rev, mt = jd._disk_rev(SID), os.stat(self._file()).st_mtime_ns
        jd.save_goals(SID, jd.load_goals(SID))
        self.assertEqual(jd._disk_rev(SID), rev, "a no-op publish does not advance the revision")
        self.assertEqual(os.stat(self._file()).st_mtime_ns, mt,
                         "...and does not touch the file, so the compaction sweep can skip it")

    def test_a_storm_of_no_op_passes_leaves_the_store_alone(self):
        """The reported symptom: pass after pass rewriting the same bytes."""
        self._seed()
        rev, mt = jd._disk_rev(SID), os.stat(self._file()).st_mtime_ns
        for _ in range(10):
            jd.save_goals(SID, jd.load_goals(SID))
        self.assertEqual(jd._disk_rev(SID), rev, "ten idle passes, zero publications")
        self.assertEqual(os.stat(self._file()).st_mtime_ns, mt)

    # ── what must still happen ──────────────────────────────────────────────
    def test_a_changed_store_is_published(self):
        self._seed()
        rev = jd._disk_rev(SID)
        s = jd.load_goals(SID)
        jd.apply_plan(s, "s2", T0 + 60, [{"do": "mint", "why": "y", "text": "A second goal"}],
                      jd.open_menu(s) if hasattr(jd, "open_menu") else [])
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(SID, s)
        self.assertGreater(jd._disk_rev(SID), rev, "a real change still advances the revision")
        self.assertEqual(len(json.loads(self._file().read_text())["nodes"]), 2,
                         "...and the new node is on disk")

    def test_a_first_publish_creates_the_file(self):
        self.assertFalse(self._file().exists())
        self._seed()
        self.assertTrue(self._file().exists(), "an absent file is a publish, never a no-op")

    def test_a_store_built_without_load_goals_still_publishes(self):
        """No _baseRev means no known base to compare against — keep the old unconditional behaviour."""
        self._seed()
        rev = jd._disk_rev(SID)
        raw = json.loads(self._file().read_text())      # hand-built store, never through load_goals
        self.assertNotIn("_baseRev", raw)
        jd.save_goals(SID, raw)
        self.assertGreater(jd._disk_rev(SID), rev)

    def test_a_no_op_save_cannot_clobber_a_concurrent_writer(self):
        """Pass A loads and changes nothing; writer B publishes a real event meanwhile. The skip compares
        against DISK, and disk has moved, so A does NOT skip here — it takes the rebase path and folds B's
        event in. That is the correct outcome and the guarantee that matters: an empty-handed writer never
        rolls a concurrent one back. (The storm this fix targets is the single-writer case below, where disk
        is unchanged too.)"""
        self._seed()
        a = jd.load_goals(SID)                          # A's snapshot, held across its model call
        b = jd.load_goals(SID)
        gid = "%s:g1" % SID
        jd.record_verdict(b, b["nodes"][gid], "romp", "block", T0 + 30, why="needs a decision")
        jd.rollup_status(b, session_closed=False)
        jd.save_goals(SID, b)
        jd.save_goals(SID, a)                           # A has nothing of its own to add
        disk = json.loads(self._file().read_text())
        self.assertTrue(any(r.get("kind") == "block" for r in disk["nodes"][gid].get("log", [])),
                        "B's block survives A's save")

    def test_content_signature_ignores_rev_and_the_transient_base(self):
        s = {"rompUuid": SID, "nodes": {}, "rev": 7, "_baseRev": 7}
        t = {"nodes": {}, "rompUuid": SID, "rev": 999}
        self.assertEqual(jd._store_content(s), jd._store_content(t),
                         "same content, different revision + key order → the same publish")

    def test_a_corrupt_file_is_quarantined_before_recovery(self):
        self._seed()
        self._file().write_text("{not json")
        s = jd.load_goals(SID)                          # load recovers fresh without destroying evidence
        quarantined = list(jd.GOALDIR.glob(SID + ".json.corrupt.*"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_text(), "{not json")
        self.assertFalse(self._file().exists())
        jd.save_goals(SID, s)
        self.assertEqual(json.loads(self._file().read_text())["rompUuid"], SID,
                         "a new store can be published after the corrupt original is preserved")

    def test_a_direct_save_refuses_to_overwrite_corrupt_state(self):
        self._seed()
        self._file().write_text("{not json")
        with self.assertRaises(ValueError):
            jd.save_goals(SID, {"rompUuid": SID, "nodes": {}, "status": {}})
        self.assertEqual(self._file().read_text(), "{not json")


if __name__ == "__main__":
    unittest.main()
