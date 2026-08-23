#!/usr/bin/env python3
"""Codex transcript-root discovery (kernel/judge.py _codex_rows): sessions materialized under
STATE/codex/projects are discovered alongside the Claude roots — same (fsid, path, anchor_sid, name)
tuple contract — and the discover cache's fingerprint sees a registry change exactly when the list
would change. Synthetic data throughout per CLAUDE.md.

Run:    python3 tests/test_codex_discovery.py
"""
import json
import os
import re
import tempfile
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
jd = SourceFileLoader("romp_judge_codexdisc", os.path.join(ROOT, "bin", "romp-judge")).load_module()

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
TID = "01911111-2222-7333-8444-555555555555"


class CodexDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(self.tmp)
        jd.NAMES.mkdir(parents=True, exist_ok=True)

    def _mint(self, cwd="/TESTDIR", name="cx", sid=SID, tid=TID, dead=False, mtime=None):
        enc = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd))
        proj = jd.CODEXDIR / "projects" / enc
        proj.mkdir(parents=True, exist_ok=True)
        p = proj / (tid + ".jsonl")
        p.write_text(json.dumps({"type": "user", "uuid": "u1", "parentUuid": None,
                                 "timestamp": "2026-06-10T14:00:00.000Z",
                                 "message": {"role": "user", "content": "synthetic prompt"}}) + "\n")
        os.utime(p, (mtime or NOW, mtime or NOW))
        reg = {}
        try:
            reg = json.loads((jd.CODEXDIR / "registry.json").read_text())
        except Exception:
            pass
        reg[sid] = {"tid": tid, "name": name, "cwd": cwd, "dead": dead}
        (jd.CODEXDIR / "registry.json").write_text(json.dumps(reg))
        return p

    def test_codex_session_discovered_with_tuple_contract(self):
        # the STABLE SID is the identity slot (the v1.3.13 audit's P1: the app-server TID there
        # meant live rows never joined liveness — keyed on SID — the picker offered a not-running
        # TID row, and reviving it shelled `romp resume <TID>` through the tmux path); the TID
        # rides only in the transcript PATH
        p = self._mint()
        rows = jd._discover_impl(NOW)
        cx = [r for r in rows if r[0] == SID]
        self.assertEqual(len(cx), 1)
        fsid, path, anchor, name = cx[0]
        self.assertEqual((fsid, str(path), anchor, name), (SID, str(p), SID, "cx"))
        self.assertFalse(any(r[0] == TID for r in rows),
                         "the thread id is transcript metadata, never a session identity")

    def test_window_ages_out(self):
        self._mint(mtime=NOW - 10 * 24 * 3600)
        rows = jd._discover_impl(NOW)          # default window is far shorter than 10 days
        self.assertEqual([r for r in rows if r[2] == SID], [],
                         "aged out by the window — the old filter-by-TID was unsatisfiable by "
                         "construction after the identity fix and passed with age-out broken "
                         "(the r37 mutant hunt)")

    def test_registry_change_invalidates_discover_cache(self):
        self._mint()
        first = jd.discover(NOW)
        self.assertEqual(len([r for r in first if r[0] == SID]), 1)
        # a second session lands: only the registry + a new file change — the fingerprint must move
        time.sleep(0.02)
        tid2 = TID.replace("01911111", "01922222")
        sid2 = SID.replace("1111", "2222", 1)
        self._mint(name="cx2", sid=sid2, tid=tid2)
        # bump the registry mtime past same-second granularity for the stat-based signature
        os.utime(jd.CODEXDIR / "registry.json", (NOW + 5, NOW + 5))
        second = jd.discover(NOW)
        self.assertEqual(len([r for r in second if r[0] == sid2]), 1,
                         "new codex session invisible — fingerprint missed the registry change")

    def test_missing_or_broken_registry_is_harmless(self):
        self.assertEqual(jd._codex_rows(0, set()), [])
        jd.CODEXDIR.mkdir(parents=True, exist_ok=True)
        (jd.CODEXDIR / "registry.json").write_text("{not json")
        self.assertEqual(jd._codex_rows(0, set()), [])


class StagingStraysNeverDiscovered(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(self.tmp)
        jd.NAMES.mkdir(parents=True, exist_ok=True)

    def test_a_leaked_names_tmp_is_not_a_session(self):
        # the r33 verification: discover() is THE primary NAMES consumer and read a leaked
        # staging stray as a phantom session — it even stole a live session's fork lane. The
        # stray is RESOLVABLE here (its transcript exists on disk): with the filter removed —
        # or filtering the wrong variable, f.stem, which strips the suffix (the r34 mutant
        # hunt) — discover returns the phantom and this test fails
        stray = "99999999-8888-7777-6666-555555555555.tmp"
        (jd.NAMES / "11111111-2222-3333-4444-555555555555").write_text("web\t/TESTDIR\t\t\n")
        (jd.NAMES / stray).write_text("phantom\t/TESTDIR\t\t\n")
        import re as _re
        with tempfile.TemporaryDirectory() as pd:
            proj = Path(pd) / _re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath("/TESTDIR"))
            proj.mkdir(parents=True)
            tr = proj / (stray + ".jsonl")
            tr.write_text(json.dumps({"type": "user", "uuid": "u1", "parentUuid": None,
                                      "timestamp": "2026-06-10T14:00:00.000Z",
                                      "message": {"role": "user",
                                                  "content": "synthetic prompt"}}) + "\n")
            os.utime(tr, (NOW, NOW))
            from unittest import mock
            with mock.patch.object(jd, "PROJECTS", Path(pd)):
                found = jd.discover(NOW)
        sids = [t[2] for t in found]
        self.assertNotIn(stray, sids)
        self.assertFalse(any(str(s).endswith(".tmp") for s in sids), sids)
        # and the fingerprint ignores it too: a stray appearing must not invalidate caches
        fp1 = jd._discover_fingerprint()
        (jd.NAMES / "99999999-8888-7777-6666-555555555555.tmp").write_text("changed\t/x\t\t\n")
        self.assertEqual(fp1, jd._discover_fingerprint(),
                         "staging churn is invisible to the discovery fingerprint")


class TidToSidMigration(unittest.TestCase):
    def test_pre_identity_stores_move_to_the_sid_and_placements_rewrite(self):
        # the r37 verification's P2, executed there: every store written under the old TID
        # identity orphaned at upgrade — cards vanished, captions re-billed, and the planner
        # re-minted finished work because placement keys embed the fsid
        tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(tmp)
        for d in (jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.GOALARCHDIR, jd.CODEXDIR):
            d.mkdir(parents=True, exist_ok=True)
        (jd.CODEXDIR / "registry.json").write_text(json.dumps(
            {SID: {"tid": TID, "name": "cx", "cwd": "/TESTDIR", "dead": False}}))
        (jd.CAPDIR / (TID + ".jsonl")).write_text('{"unit": "turn:u1"}\n')
        (jd.ARCHDIR / (TID + ".json")).write_text(json.dumps({"headline": "did things"}))
        (jd.GOALDIR / (TID + ".json")).write_text(json.dumps(
            {"rompUuid": TID,
             "nodes": {TID + ":g1": {"t": 1, "parentId": None},
                       TID + ":g2": {"t": 2, "parentId": TID + ":g1"}},
             "status": {TID + ":g1": "working"},
             "lastNode": TID + ":g2",
             "placements": {TID + ":123:abc": TID + ":g1", "other:1:x": None}}))
        ov = jd._overrides_dir()
        ov.mkdir(parents=True, exist_ok=True)
        (ov / (TID + ".jsonl")).write_text(
            json.dumps({"op": "block", "node": TID + ":g1", "t": 5}) + "\n"
            + json.dumps({"op": "restore", "t": 6,
                          "nodes": {TID + ":g2": {"parentId": TID + ":g1"}},
                          "status": {TID + ":g2": "working"}}) + "\n")
        (ov / (SID + ".jsonl")).write_text(json.dumps(
            {"op": "resolve", "node": SID + ":g9", "t": 9}) + "\n")
        (jd.GOALARCHDIR / (TID + ".json")).write_text(json.dumps(
            {"rompUuid": TID, "nodes": {}, "status": {}}))
        jd.migrate_codex_identity()
        self.assertTrue((jd.CAPDIR / (SID + ".jsonl")).exists())
        self.assertFalse((jd.CAPDIR / (TID + ".jsonl")).exists())
        self.assertEqual(json.loads((jd.ARCHDIR / (SID + ".json")).read_text())["headline"],
                         "did things")
        g = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        self.assertEqual(g["rompUuid"], SID)
        self.assertIn(SID + ":123:abc", g["placements"],
                      "fsid-prefixed placement keys rewrite — sealed work must keep gating "
                      "the planner under the new identity")
        self.assertIn("other:1:x", g["placements"], "foreign keys ride untouched")
        self.assertNotIn(TID + ":123:abc", g["placements"])
        self.assertIn(SID + ":g1", g["nodes"],
                      "NODE IDS rewrite too — every gesture derives the owning session from "
                      "the id prefix, so a TID-keyed node's Clear died against the deleted "
                      "store (the r38 verification, executed)")
        self.assertEqual(g["nodes"][SID + ":g2"]["parentId"], SID + ":g1",
                         "parent references move with their nodes")
        self.assertEqual(g["placements"][SID + ":123:abc"], SID + ":g1",
                         "placement VALUES name node ids — they move too")
        self.assertEqual(g["lastNode"], SID + ":g2")
        self.assertIn(SID + ":g1", g["status"])
        self.assertNotIn(TID, json.dumps(g), "no tid identity survives anywhere in the store")
        self.assertTrue((jd.GOALARCHDIR / (SID + ".json")).exists())
        self.assertFalse((jd.GOALDIR / (TID + ".json")).exists(),
                         "no TID relic: stale stores double-counted the failure banner and "
                         "burned judge retries (the r38 mutant hunt)")
        self.assertFalse((jd.GOALARCHDIR / (TID + ".json")).exists())
        jlines = (ov / (SID + ".jsonl")).read_text().splitlines()
        self.assertEqual(len(jlines), 3, "the override journal MERGES: tid rows then sid rows")
        self.assertEqual(json.loads(jlines[0])["node"], SID + ":g1",
                         "journal node references move with the store they replay over")
        restore = json.loads(jlines[1])
        self.assertIn(SID + ":g2", restore["nodes"],
                      "restore payloads carry node ids as nested dict KEYS — a values-only "
                      "rewrite resurrected TID nodes into the SID store (the r39 mutant hunt)")
        self.assertEqual(restore["nodes"][SID + ":g2"]["parentId"], SID + ":g1")
        self.assertIn(SID + ":g2", restore["status"])
        self.assertEqual(json.loads(jlines[2])["node"], SID + ":g9")
        self.assertFalse((ov / (TID + ".jsonl")).exists())
        # idempotent: a second boot moves nothing and destroys nothing
        jd.migrate_codex_identity()
        self.assertIn(SID + ":123:abc",
                      json.loads((jd.GOALDIR / (SID + ".json")).read_text())["placements"])

    def test_the_journal_never_merges_while_the_tid_store_lingers(self):
        # the r39 verification, executed there: merging while the goal store stayed behind
        # aliased user verdicts by bare g-number onto cards the sid store minted LATER — a
        # fresh card born completed from a resolve stamped before its birth
        tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(tmp)
        for d in (jd.GOALDIR, jd.CODEXDIR):
            d.mkdir(parents=True, exist_ok=True)
        (jd.CODEXDIR / "registry.json").write_text(json.dumps(
            {SID: {"tid": TID, "name": "cx", "cwd": "/TESTDIR", "dead": False}}))
        (jd.GOALDIR / (TID + ".json")).write_text("{ not json")   # the move FAILS this run
        ov = jd._overrides_dir()
        ov.mkdir(parents=True, exist_ok=True)
        (ov / (TID + ".jsonl")).write_text(json.dumps(
            {"op": "resolve", "node": TID + ":g1", "t": 5}) + "\n")
        jd.migrate_codex_identity()
        self.assertTrue((ov / (TID + ".jsonl")).exists(),
                        "the journal rides the STORE's settlement — never merged ahead of it")
        self.assertFalse((ov / (SID + ".jsonl")).exists())

    def test_a_corrupt_registry_row_never_crashes_the_boot(self):
        # the r38 verification: a truthy non-dict row hit .get() outside every guard, and
        # main() calls the migrations bare — the kernel crash-looped
        tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(tmp)
        jd.NAMES.mkdir(parents=True, exist_ok=True)   # discovery must get PAST its names walk
        jd.CODEXDIR.mkdir(parents=True, exist_ok=True)
        (jd.CODEXDIR / "registry.json").write_text(json.dumps(
            {SID: "not a dict", "x": 7, TID: {"tid": "t2", "cwd": "/y"}}))
        jd.migrate_codex_identity()                   # must simply not raise
        rows = jd._discover_impl(NOW)                 # and DISCOVERY survives the same shape —
        self.assertIsInstance(rows, list)             # it killed every cold feed/picker build
        #                                               one call later (the r39 verification)

    def test_an_existing_sid_store_is_never_clobbered(self):
        tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(tmp)
        for d in (jd.GOALDIR, jd.CODEXDIR):
            d.mkdir(parents=True, exist_ok=True)
        (jd.CODEXDIR / "registry.json").write_text(json.dumps(
            {SID: {"tid": TID, "name": "cx", "cwd": "/TESTDIR", "dead": False}}))
        (jd.GOALDIR / (TID + ".json")).write_text(json.dumps({"rompUuid": TID, "nodes": {}}))
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "nodes": {"fresh": {"t": 2}}}))
        jd.migrate_codex_identity()
        self.assertIn("fresh", json.loads((jd.GOALDIR / (SID + ".json")).read_text())["nodes"],
                      "post-fix work under the SID always wins; the TID relic stays a dead file")


class JudgeEntryMigration(unittest.TestCase):
    def test_the_standalone_entry_migrates_before_dispatch(self):
        # the r39 mutant hunt: deleting main()'s migration call stayed green suite-wide — a
        # standalone `romp-judge --once` between install and first kernel boot re-billed
        # captions and then blocked the caption move forever under sid-store-wins
        import inspect
        src = inspect.getsource(jd.main)
        self.assertIn("migrate_codex_identity()", src)
        self.assertLess(src.index("migrate_codex_identity()"), src.index('"--once"'),
                        "the migration runs before any dispatch touches the stores")


class SpawnToReviveIntegration(unittest.TestCase):
    def test_the_stable_sid_is_the_identity_from_spawn_through_revive(self):
        # the v1.3.13 audit's asked-for integration: spawn → discovery → the liveness join →
        # stop → revive, all on the REAL backend and REAL discovery — the TID-as-identity split
        # was invisible to every unit test because each side was exercised alone
        import sys as _sys
        _sys.path.insert(0, os.path.join(ROOT, "kernel"))
        import codex_backend as cb
        tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(tmp)
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        be = cb.CodexBackend(str(tmp), client_factory=lambda: None)
        sid = be.spawn("webby", "/TESTDIR")
        # the transcript materializes where discovery looks (registry tid)
        p = be.transcript_path(sid)
        p.write_text(json.dumps({"type": "user", "uuid": "u1", "parentUuid": None,
                                 "timestamp": "2026-06-10T14:00:00.000Z",
                                 "message": {"role": "user", "content": "x"}}) + "\n")
        os.utime(p, (NOW, NOW))
        os.utime(jd.CODEXDIR / "registry.json", (NOW, NOW))
        rows = [r for r in jd._discover_impl(NOW) if r[2] == sid]
        self.assertEqual(len(rows), 1, "spawned session discovered")
        self.assertEqual(rows[0][0], sid,
                         "discovery identity == the liveness key: the join that broke")
        self.assertIn(rows[0][0], be.live_sessions(),
                      "a live session's row joins the alive set by its own identity")
        be.kill(sid)
        self.assertNotIn(sid, be.live_sessions(), "stopped: out of the live set")
        rows2 = [r for r in jd._discover_impl(NOW) if r[2] == sid]
        self.assertEqual(len(rows2), 1, "dead sessions keep discovering — history stays browsable")
        self.assertIsNotNone(be._session(sid),
                             "the registry probe a revive routes on still knows it")
        self.assertTrue(be.resume("webby", sid), "the revive lands on the Codex backend")
        self.assertIn(sid, be.live_sessions(), "revived: back in the live set under the SAME sid")


if __name__ == "__main__":
    unittest.main(verbosity=2)
