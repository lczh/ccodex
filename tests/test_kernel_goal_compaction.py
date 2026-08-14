"""Goal-store compaction: archive DISMISSED (cleared) cards out of the live goal tree (the user 2026-06-25).

build_feed re-derives the WHOLE goal store every push, for every session — and ~92% of all nodes ever created
are CLEARED (crossed off the feed), so the store grew monotonically and the feed got "slower and slower over
time". The kernel now MOVES each cleared top + its subtree into goals-archive/<sid>.json, keyed purely on
cleared (exactly the cards the feed already hides), so the live store stays ≈ what's on the board. The judge's
(segment-id, phase) dedup lives in store["placements"], which compaction LEAVES in the live store, so the judge
never re-mints an archived node. Undo-clear restores from the archive. Synthetic stores only (no live data).
"""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"


def _node(nid, parent, **kw):
    n = {"id": nid, "text": nid, "parentId": parent, "nodeComplete": False,
         "blocked": False, "cleared": False, "trail": [nid + "seg"], "t": 1, "mt": 1}
    n.update(kw)
    return n


class GoalCompactionTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        km._compact_seen.clear()
        # g1 = a DISMISSED (cleared) top + a done child; g2 = an ACTIVE (working) top with a done + an open child.
        g = lambda n: "%s:%s" % (SID, n)
        nodes = {
            g("g1"): _node(g("g1"), None, cleared=True),
            g("g1a"): _node(g("g1a"), g("g1"), cleared=True),
            g("g2"): _node(g("g2"), None),
            g("g2a"): _node(g("g2a"), g("g2"), nodeComplete=True),
            g("g2b"): _node(g("g2b"), g("g2")),
        }
        self.store = {
            "rompUuid": SID, "seq": 5, "lastNode": g("g2b"), "nodes": nodes,
            "status": {g("g1"): "cleared", g("g2"): "working"},
            # the judge's segment dedup — one key per placed segment; MUST survive compaction
            "placements": {g("g1") + "seg": g("g1"), g("g1a") + "seg": g("g1a"),
                           g("g2") + "seg": g("g2"), g("g2a") + "seg": g("g2a"), g("g2b") + "seg": g("g2b")},
        }
        jd.save_goals(SID, self.store)
        # the durable view-cleared seal (cleared.jsonl) for g1
        (jd.STATE).mkdir(parents=True, exist_ok=True)
        (jd.STATE / "cleared.jsonl").write_text(json.dumps({"id": g("g1"), "t": 1, "op": "clear"}) + "\n")
        self.g = g

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def test_a_cleared_root_and_subtree_leave_the_live_store_for_the_archive(self):
        moved = km._compact_goal_store(SID)
        self.assertEqual(moved, 2, "g1 + its child g1a move out")
        live = jd.load_goals(SID)["nodes"]
        self.assertNotIn(self.g("g1"), live)
        self.assertNotIn(self.g("g1a"), live)
        arch = jd.load_goal_archive(SID)["nodes"]
        self.assertIn(self.g("g1"), arch, "the dismissed card is preserved in the archive, not deleted")
        self.assertIn(self.g("g1a"), arch)

    def test_b_an_active_root_with_a_done_child_is_NOT_archived(self):
        km._compact_goal_store(SID)
        live = jd.load_goals(SID)["nodes"]
        # g2 is working → its whole subtree (incl. the done g2a + the open g2b) stays live → roll-up intact
        for n in ("g2", "g2a", "g2b"):
            self.assertIn(self.g(n), live, "an active card never loses children to the archive")

    def test_c_the_judge_dedup_keys_survive_so_an_archived_node_is_never_re_minted(self):
        km._compact_goal_store(SID)
        store = jd.load_goals(SID)
        # the archived nodes' segments are STILL in placements → the judge's `if key in placements: continue`
        # skips them → it never re-creates an archived node (the no-re-bloat guarantee, by construction)
        self.assertIn(self.g("g1") + "seg", store["placements"])
        self.assertIn(self.g("g1a") + "seg", store["placements"])
        # a fresh roll-up over the compacted store does not resurrect the archived top
        jd.rollup_status(store, False)
        self.assertNotIn(self.g("g1"), store["nodes"])
        self.assertNotIn(self.g("g1"), store.get("status", {}))

    def test_d_undo_clear_restores_an_archived_card_from_the_archive(self):
        km._compact_goal_store(SID)
        self.assertNotIn(self.g("g1"), jd.load_goals(SID)["nodes"])
        km._restore_goal_archive([self.g("g1")])
        live = jd.load_goals(SID)["nodes"]
        self.assertIn(self.g("g1"), live, "undo pulls the top back into the live store")
        self.assertIn(self.g("g1a"), live, "...with its whole subtree")
        self.assertNotIn(self.g("g1"), jd.load_goal_archive(SID)["nodes"], "and out of the archive")

    def test_e_the_sweep_skips_a_store_whose_file_did_not_change(self):
        calls = []
        orig = km._compact_goal_store
        km._compact_goal_store = lambda f: calls.append(f) or orig(f)
        try:
            km._compact_goal_stores()                 # first sweep → visits the store (migrates g1)
            self.assertIn(SID, calls)
            calls.clear()
            km._compact_goal_stores()                 # nothing changed since → the mtime gate skips it
            self.assertNotIn(SID, calls, "an unchanged store is not re-swept (steady state is just stats)")
        finally:
            km._compact_goal_store = orig

    def test_f_undo_clear_wires_in_the_archive_restore_before_unsetting_the_flag(self):
        import inspect
        body = inspect.getsource(km._undo_clear)
        self.assertIn("_restore_goal_archive(restored)", body)
        # restore must precede the flag un-set so _mark_nodes_cleared finds the nodes
        self.assertLess(body.index("_restore_goal_archive(restored)"),
                        body.index("_mark_nodes_cleared(restored, False)"))

    def test_g_simultaneous_archive_remove_and_add_retain_both_edits(self):
        old, keep, added = self.g("old"), self.g("keep"), self.g("added")
        jd.save_goal_archive(SID, {
            "rompUuid": SID, "status": {},
            "nodes": {old: _node(old, None), keep: _node(keep, None)},
        })
        remover_entered = threading.Event()
        release_remover = threading.Event()
        adder_done = threading.Event()

        def remove_one(arch):
            remover_entered.set()
            self.assertTrue(release_remover.wait(2))
            arch["nodes"].pop(old)

        def add_one(arch):
            arch["nodes"][added] = _node(added, None)
            adder_done.set()

        remove_thread = threading.Thread(target=jd.mutate_goal_archive,
                                         args=(SID, remove_one))
        add_thread = threading.Thread(target=jd.mutate_goal_archive,
                                      args=(SID, add_one))
        remove_thread.start()
        self.assertTrue(remover_entered.wait(1))
        add_thread.start()
        time.sleep(0.05)
        self.assertFalse(adder_done.is_set(), "the second mutation must wait for the archive transaction")
        release_remover.set()
        remove_thread.join(2)
        add_thread.join(2)
        self.assertFalse(remove_thread.is_alive())
        self.assertFalse(add_thread.is_alive())
        self.assertEqual(set(jd.load_goal_archive(SID)["nodes"]), {keep, added})


if __name__ == "__main__":
    unittest.main()
