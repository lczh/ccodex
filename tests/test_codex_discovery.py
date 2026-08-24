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
import threading
import time
import unittest
from unittest import mock
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

    def test_a_quarantined_tid_store_never_lets_the_journal_merge(self):
        # the r40 verification, executed there: a corrupt tid store quarantined by any goals
        # walk left the tid file ABSENT and the next judge pass minted a fresh sid store — the
        # absence-inferred gate read that as "settled" and merged pre-upgrade verdicts onto a
        # card born later. The merge rides the move EVENT now.
        tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(tmp)
        for d in (jd.GOALDIR, jd.CODEXDIR):
            d.mkdir(parents=True, exist_ok=True)
        (jd.CODEXDIR / "registry.json").write_text(json.dumps(
            {SID: {"tid": TID, "name": "cx", "cwd": "/TESTDIR", "dead": False}}))
        # the tid store is GONE (quarantined last boot); a fresh sid store exists (minted by
        # the first post-upgrade judge pass, numbering restarted)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "nodes": {SID + ":g1": {"t": 1000}}, "status": {}}))
        ov = jd._overrides_dir()
        ov.mkdir(parents=True, exist_ok=True)
        (ov / (TID + ".jsonl")).write_text(json.dumps(
            {"op": "resolve", "node": TID + ":g1", "t": 5}) + "\n")
        jd.migrate_codex_identity()
        self.assertTrue((ov / (TID + ".jsonl")).exists(),
                        "absence is not the move: a pre-birth resolve must never complete a "
                        "card minted later")
        self.assertFalse((ov / (SID + ".jsonl")).exists())

    def test_a_lingering_archive_holds_the_journal_too(self):
        # the r40 verification's P3: restore rows replay against the ARCHIVE — a dismissed
        # card resurrected when the live store moved but the archive lingered
        tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(tmp)
        for d in (jd.GOALDIR, jd.GOALARCHDIR, jd.CODEXDIR):
            d.mkdir(parents=True, exist_ok=True)
        (jd.CODEXDIR / "registry.json").write_text(json.dumps(
            {SID: {"tid": TID, "name": "cx", "cwd": "/TESTDIR", "dead": False}}))
        (jd.GOALDIR / (TID + ".json")).write_text(json.dumps(
            {"rompUuid": TID, "nodes": {TID + ":g1": {"t": 1}}, "status": {}}))
        (jd.GOALARCHDIR / (TID + ".json")).write_text("{ corrupt")   # the archive move FAILS
        ov = jd._overrides_dir()
        ov.mkdir(parents=True, exist_ok=True)
        (ov / (TID + ".jsonl")).write_text(json.dumps(
            {"op": "restore", "t": 6, "nodes": {TID + ":g2": {}}, "status": {}}) + "\n")
        jd.migrate_codex_identity()
        self.assertTrue((ov / (TID + ".jsonl")).exists(),
                        "EVERY tid-keyed goal store must move before the journal does")

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
        ov = jd._overrides_dir()
        ov.mkdir(parents=True, exist_ok=True)
        (ov / (TID + ".jsonl")).write_text(json.dumps(
            {"op": "resolve", "node": TID + ":g1", "t": 5}) + "\n")
        jd.migrate_codex_identity()
        self.assertIn("fresh", json.loads((jd.GOALDIR / (SID + ".json")).read_text())["nodes"],
                      "post-fix work under the SID always wins; the TID relic stays a dead file")
        self.assertTrue((ov / (TID + ".jsonl")).exists(),
                        "sid-store-won SKEW: no store moved this run, so the journal never "
                        "merges — its verdicts reference the tid store's numbering, not the "
                        "sid's (the r40 verification's unpinned half)")
        self.assertFalse((ov / (SID + ".jsonl")).exists())


class MigrationTransaction(unittest.TestCase):
    """The v1.3.14 audit's fix requirement: a durable per-session transaction, crash-injected at
    every publish/unlink boundary, over archive-only / live-only / both-store / corrupt-store /
    both-old-and-new states."""

    def _state(self, goals=True, arch=False, captions=False, episodes=False, journal=False):
        tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(tmp)
        for d in (jd.GOALDIR, jd.GOALARCHDIR, jd.CAPDIR, jd.ARCHDIR, jd.EPIDIR, jd.CODEXDIR):
            d.mkdir(parents=True, exist_ok=True)
        (jd.CODEXDIR / "registry.json").write_text(json.dumps(
            {SID: {"tid": TID, "name": "cx", "cwd": "/TESTDIR", "dead": False}}))
        if goals:
            (jd.GOALDIR / (TID + ".json")).write_text(json.dumps(
                {"rompUuid": TID, "nodes": {TID + ":g1": {"t": 1}}, "status": {},
                 "placements": {}}))
        if arch:
            (jd.GOALARCHDIR / (TID + ".json")).write_text(json.dumps(
                {"rompUuid": TID, "nodes": {TID + ":g9": {"t": 9, "cleared": True}},
                 "status": {}}))
        if captions:
            (jd.CAPDIR / (TID + ".jsonl")).write_text(
                json.dumps({"id": TID + ":100:aaa", "text": "did x"}) + "\n"
                + json.dumps({"id": "turn:u1", "text": "asked y"}) + "\n")
            (jd.CAPDIR / (TID + ".fails.json")).write_text(json.dumps({TID + ":100:bbb": 2}))
        if episodes:
            (jd.EPIDIR / (TID + ".jsonl")).write_text(
                json.dumps({"head": "u-head-1", "fsid": TID, "t": 50}) + "\n")
        if journal:
            ov = jd._overrides_dir()
            ov.mkdir(parents=True, exist_ok=True)
            (ov / (TID + ".jsonl")).write_text(json.dumps(
                {"op": "resolve", "node": TID + ":g1", "t": 5}) + "\n")
        return tmp

    def test_a_crash_between_publish_and_unlink_completes_at_the_retry(self):
        # the v1.3.14 audit's P1: the pre-transactional migration left both files after this
        # exact crash, and every retry saw the sid file and skipped — the journal never reached
        # the active store
        self._state(goals=True, journal=True)
        calls = {"n": 0}
        real = jd._mig_atomic

        def crash_after_goals_publish(path, text):
            real(path, text)
            if path.name == SID + ".json" and path.parent == jd.GOALDIR:
                raise RuntimeError("crash: sid store published, tid store not yet unlinked")
        with mock.patch.object(jd, "_mig_atomic", side_effect=crash_after_goals_publish):
            jd.migrate_codex_identity()
        self.assertTrue((jd.GOALDIR / (TID + ".json")).exists(), "the crash left both files")
        self.assertTrue((jd.GOALDIR / (SID + ".json")).exists())
        jd.migrate_codex_identity()                   # the RETRY, un-crashed
        self.assertFalse((jd.GOALDIR / (TID + ".json")).exists(),
                         "the standing intent makes the old file authoritative — re-published "
                         "and unlinked, never skipped (the audit's executed strand)")
        ov = jd._overrides_dir()
        self.assertTrue((ov / (SID + ".jsonl")).exists(),
                        "and the journal reaches the ACTIVE sid store")
        self.assertFalse((ov / (TID + ".jsonl")).exists())
        self.assertTrue((jd.CODEXDIR / "migrated" / (SID + ".done")).exists())

    def test_a_journal_publish_unlink_crash_never_duplicates_rows_on_retry(self):
        self._state(goals=True, journal=True)
        ov = jd._overrides_dir()
        real_unlink = Path.unlink

        def crash_at_journal_unlink(p, *a, **kw):
            if p == ov / (TID + ".jsonl"):
                raise SystemExit("crash: journal published, tid journal not yet unlinked")
            return real_unlink(p, *a, **kw)

        with mock.patch.object(Path, "unlink", crash_at_journal_unlink):
            with self.assertRaises(SystemExit):
                jd.migrate_codex_identity()
        self.assertTrue((ov / (TID + ".jsonl")).exists())
        self.assertTrue((ov / (SID + ".jsonl")).exists())
        jd.migrate_codex_identity()
        rows = [json.loads(ln) for ln in (ov / (SID + ".jsonl")).read_text().splitlines()]
        self.assertEqual(len(rows), 1,
                         "retry must not prepend the same migrated verdict a second time")
        self.assertEqual(rows[0]["node"], SID + ":g1")
        self.assertFalse((ov / (TID + ".jsonl")).exists())

    def test_shared_goal_state_waits_for_a_proven_store_move(self):
        self._state(goals=False)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "nodes": {SID + ":g1": {"t": 999, "fresh": True}},
             "status": {SID + ":g1": "working"}}))
        (jd.STATE / "cleared.jsonl").write_text(json.dumps(
            {"id": TID + ":g1", "t": 1, "op": "clear"}) + "\n")
        (jd.STATE / "notify-cards.json").write_text(json.dumps({TID + ":g1": "all"}))
        (jd.STATE / "auto-nudge.json").write_text(json.dumps(
            {"nudged": {TID + ":g1": {"count": 1}},
             "intrBlocked": {TID: TID + ":g1"},
             "debtNudged": {TID + ">peer:10": 20}}))
        jd.migrate_codex_identity()
        self.assertIn(TID + ":g1", (jd.STATE / "cleared.jsonl").read_text(),
                      "an orphaned clear cannot attach to a fresh SID:g1")
        self.assertIn(TID + ":g1", json.loads(
            (jd.STATE / "notify-cards.json").read_text()))
        an = json.loads((jd.STATE / "auto-nudge.json").read_text())
        self.assertIn(TID + ":g1", an["nudged"])
        self.assertIn(TID, an["intrBlocked"])
        self.assertEqual(an["debtNudged"], {},
                         "an orphan debt row must not load a fresh SID goal store")

    def test_an_archive_only_session_never_merges_the_journal(self):
        # the v1.3.14 audit's P1: archive-only used to count as "complete" and the journal's
        # old resolve completed a future store's g1
        self._state(goals=False, arch=True, journal=True)
        jd.migrate_codex_identity()
        ov = jd._overrides_dir()
        self.assertTrue((ov / (TID + ".jsonl")).exists(),
                        "no goals store moved — numbering continuity cannot hold, the journal "
                        "stays an orphaned relic")
        self.assertFalse((ov / (SID + ".jsonl")).exists())
        self.assertTrue((jd.GOALARCHDIR / (SID + ".json")).exists(), "the archive itself moves")
        self.assertFalse((jd.CODEXDIR / "migrated" / (SID + ".done")).exists(),
                         "the explicitly orphaned journal keeps the transaction visible")

    def test_caption_rows_and_the_fail_ledger_rewrite_their_unit_ids(self):
        # the v1.3.14 audit's P1: a bare file rename left every row id TID-prefixed — the
        # indexer saw nothing captioned and re-billed the whole window
        self._state(goals=False, captions=True)
        jd.migrate_codex_identity()
        done = jd.captioned_ids(SID)
        self.assertIn(SID + ":100:aaa", done,
                      "the row ids move with the file — no re-captioning, no re-billing")
        self.assertIn("turn:u1", done, "non-identity ids ride untouched")
        fails = jd._caption_fails(SID)
        self.assertIn(SID + ":100:bbb", fails, "the retry ledger migrates too")
        self.assertFalse((jd.CAPDIR / (TID + ".jsonl")).exists())
        self.assertFalse((jd.CAPDIR / (TID + ".fails.json")).exists())

    def test_the_episode_log_moves_by_filename_with_contents_untouched(self):
        # the v1.3.14 audit's P1 (the /clear seed mistake) + its schema note: an episode row's
        # fsid legitimately names the PHYSICAL tid transcript — never blindly rewritten
        self._state(goals=False, episodes=True)
        jd.migrate_codex_identity()
        rows, _settles = jd._episode_read(SID)
        self.assertEqual(len(rows), 1, "the history joins the SID — the next /clear is a "
                                       "boundary, not a seed")
        self.assertEqual(rows[0]["fsid"], TID,
                         "the row's fsid names the physical transcript and rides untouched")
        self.assertFalse((jd.EPIDIR / (TID + ".jsonl")).exists())

    def test_preexisting_append_only_sid_stores_merge_instead_of_stranding_history(self):
        self._state(goals=False, captions=True, episodes=True)
        (jd.CAPDIR / (SID + ".jsonl")).write_text(
            json.dumps({"id": SID + ":200:new", "text": "new caption"}) + "\n")
        (jd.CAPDIR / (SID + ".fails.json")).write_text(json.dumps({SID + ":200:fail": 1}))
        (jd.EPIDIR / (SID + ".jsonl")).write_text(
            json.dumps({"head": "u-head-2", "fsid": SID, "t": 60}) + "\n")
        jd.migrate_codex_identity()
        cap = (jd.CAPDIR / (SID + ".jsonl")).read_text()
        self.assertIn(SID + ":100:aaa", cap)
        self.assertIn(SID + ":200:new", cap)
        fails = json.loads((jd.CAPDIR / (SID + ".fails.json")).read_text())
        self.assertEqual((fails[SID + ":100:bbb"], fails[SID + ":200:fail"]), (2, 1))
        heads = [r["head"] for r in jd.episode_rows(SID)]
        self.assertEqual(heads, ["u-head-1", "u-head-2"])
        for p in (jd.CAPDIR / (TID + ".jsonl"), jd.CAPDIR / (TID + ".fails.json"),
                  jd.EPIDIR / (TID + ".jsonl")):
            self.assertFalse(p.exists(), "the reconciled source is no longer an active relic")
        self.assertTrue((jd.CODEXDIR / "migrated" / (SID + ".done")).exists())

    def test_a_late_legacy_writer_reopens_a_done_transaction(self):
        self._state(goals=False)
        jd.migrate_codex_identity()
        done = jd.CODEXDIR / "migrated" / (SID + ".done")
        self.assertTrue(done.exists())
        (jd.GOALDIR / (TID + ".json")).write_text(json.dumps(
            {"rompUuid": TID, "nodes": {TID + ":g1": {"old": True}}, "status": {}}))
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "nodes": {SID + ":g9": {"fresh": True}}, "status": {}}))
        jd.migrate_codex_identity()
        self.assertFalse(done.exists(), "completion is not a permanent blind skip")
        self.assertTrue((jd.GOALDIR / (TID + ".json")).exists())
        self.assertIn(SID + ":g9", json.loads(
            (jd.GOALDIR / (SID + ".json")).read_text())["nodes"])

    def test_a_late_non_goal_writer_preserves_completed_goal_continuity(self):
        self._state(goals=True)
        jd.migrate_codex_identity()
        self.assertEqual(jd._CODEX_GOAL_IDENTITY_MAP.get(TID), SID)
        (jd.CAPDIR / (TID + ".jsonl")).write_text(
            json.dumps({"id": TID + ":200:late", "caption": "late"}) + "\n")
        jd.migrate_codex_identity()
        self.assertEqual(jd._CODEX_GOAL_IDENTITY_MAP.get(TID), SID,
                         "a late caption cannot erase the durable proof that goals moved")
        done = json.loads((jd.CODEXDIR / "migrated" / (SID + ".done")).read_text())
        self.assertTrue(done["moved"]["goals"])

    def test_reopen_intent_is_durable_before_the_old_goal_proof_is_retired(self):
        self._state(goals=True)
        jd.migrate_codex_identity()
        done = jd.CODEXDIR / "migrated" / (SID + ".done")
        intent = jd.CODEXDIR / "migrated" / (SID + ".intent")
        (jd.CAPDIR / (TID + ".jsonl")).write_text(
            json.dumps({"id": TID + ":200:late", "caption": "late"}) + "\n")
        real = jd._mig_atomic

        def die_after_reopen(path, text):
            real(path, text)
            if path == intent:
                raise SystemExit("hard death after reopened intent publish")

        with mock.patch.object(jd, "_mig_atomic", side_effect=die_after_reopen):
            with self.assertRaises(SystemExit):
                jd.migrate_codex_identity()
        self.assertTrue(done.exists(), "old proof survives until its successor is durable")
        self.assertTrue(intent.exists())
        jd.migrate_codex_identity()
        self.assertEqual(jd._CODEX_GOAL_IDENTITY_MAP.get(TID), SID)

    def test_a_truthy_nonboolean_done_ledger_cannot_fabricate_goal_continuity(self):
        self._state(goals=False)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "nodes": {SID + ":g1": {"fresh": True}}, "status": {}}))
        (jd.STATE / "cleared.jsonl").write_text(json.dumps(
            {"id": TID + ":g1", "t": 1, "op": "clear"}) + "\n")
        mig = jd.CODEXDIR / "migrated"
        mig.mkdir(parents=True, exist_ok=True)
        (mig / (SID + ".done")).write_text(json.dumps(
            {"tid": TID, "moved": {"goals": "corrupt-truthy"}, "present": {}}))
        jd.migrate_codex_identity()
        self.assertIn(TID + ":g1", (jd.STATE / "cleared.jsonl").read_text())
        self.assertTrue(json.loads((jd.GOALDIR / (SID + ".json")).read_text())
                        ["nodes"][SID + ":g1"]["fresh"])

    def test_a_moved_namespace_absent_from_the_intent_cannot_fabricate_continuity(self):
        self._state(goals=False)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "nodes": {SID + ":g1": {"fresh": True}}, "status": {}}))
        (jd.STATE / "cleared.jsonl").write_text(json.dumps(
            {"id": TID + ":g1", "t": 1, "op": "clear"}) + "\n")
        mig = jd.CODEXDIR / "migrated"
        mig.mkdir(parents=True, exist_ok=True)
        absent = {ns: False for ns in jd._MIG_NAMESPACES}
        (mig / (SID + ".done")).write_text(json.dumps(
            {"tid": TID, "moved": {"goals": True}, "present": absent,
             "preexist": absent, "targets": {}}))
        jd.migrate_codex_identity()
        self.assertIn(TID + ":g1", (jd.STATE / "cleared.jsonl").read_text(),
                      "a contradictory ledger must not prove that goal numbering moved")

    def test_archive_writer_cannot_land_inside_the_migration_publish_window(self):
        self._state(goals=False)
        old = jd.ARCHDIR / (TID + ".json")
        old.write_text(json.dumps({"headline": "legacy", "abstract": "old"}))
        go = threading.Event()
        finished = threading.Event()

        def writer():
            go.wait()
            jd.write_archive(SID, {"headline": "fresh", "abstract": "new"})
            finished.set()

        thread = threading.Thread(target=writer)
        thread.start()
        real = jd._mig_atomic_bytes

        def pause_before_archive_publish(path, payload):
            if path == jd.ARCHDIR / (SID + ".json"):
                go.set()
                time.sleep(0.05)       # without the identity lock, the SID writer lands here
            return real(path, payload)

        with mock.patch.object(jd, "_mig_atomic_bytes", side_effect=pause_before_archive_publish):
            jd.migrate_codex_identity()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(jd.load_archive(SID), {"headline": "fresh", "abstract": "new"},
                         "migration must not overwrite an archive published during its move")
        self.assertFalse(old.exists())

    def test_a_source_born_after_the_intent_is_preserved_and_never_marks_moved(self):
        self._state(goals=False, captions=True)
        mig = jd.CODEXDIR / "migrated"
        mig.mkdir(parents=True, exist_ok=True)
        present = {ns: ns in ("captions", "capfails") for ns in jd._MIG_NAMESPACES}
        (mig / (SID + ".intent")).write_text(json.dumps(
            {"tid": TID, "moved": {}, "present": present,
             "preexist": {ns: False for ns in jd._MIG_NAMESPACES}, "targets": {}}))
        late = jd.GOALDIR / (TID + ".json")
        late.write_text(json.dumps(
            {"rompUuid": TID, "nodes": {TID + ":g1": {"late": True}}, "status": {}}))
        (jd.STATE / "cleared.jsonl").write_text(json.dumps(
            {"id": TID + ":g1", "t": 1, "op": "clear"}) + "\n")
        jd.migrate_codex_identity()
        jd.migrate_codex_identity()
        self.assertTrue(late.exists(), "ownership of a post-intent source is unknown")
        self.assertFalse((jd.GOALDIR / (SID + ".json")).exists())
        self.assertFalse((mig / (SID + ".done")).exists())
        intent = json.loads((mig / (SID + ".intent")).read_text())
        self.assertNotIn("goals", intent["moved"])
        self.assertIn(TID + ":g1", (jd.STATE / "cleared.jsonl").read_text())

    def test_duplicate_or_overlapping_registry_identities_leave_evidence_untouched(self):
        tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(tmp)
        for d in (jd.GOALDIR, jd.CODEXDIR):
            d.mkdir(parents=True, exist_ok=True)
        sid2 = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        tid2 = "01922222-3333-7444-8555-666666666666"
        # TID is claimed by two SIDs, while SID2 is also (corruptly) used as SID's TID.
        (jd.CODEXDIR / "registry.json").write_text(json.dumps({
            SID: {"tid": TID, "cwd": "/TESTDIR"},
            sid2: {"tid": TID, "cwd": "/TESTDIR2"},
            "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff": {"tid": sid2, "cwd": "/TESTDIR3"},
            "cccccccc-dddd-4eee-8fff-000000000000": {"tid": tid2, "cwd": "/TESTDIR4"},
        }))
        valid_sid = "cccccccc-dddd-4eee-8fff-000000000000"
        sources = (TID, sid2, tid2)
        for ident in sources:
            (jd.GOALDIR / (ident + ".json")).write_text(json.dumps(
                {"rompUuid": ident, "nodes": {ident + ":g1": {}}, "status": {}}))
        jd.migrate_codex_identity()
        self.assertTrue((jd.GOALDIR / (TID + ".json")).exists(),
                        "a duplicate source has no authoritative destination")
        self.assertTrue((jd.GOALDIR / (sid2 + ".json")).exists(),
                        "a current SID cannot simultaneously be consumed as a legacy source")
        self.assertFalse((jd.GOALDIR / (tid2 + ".json")).exists(),
                         "an unrelated valid mapping can still migrate")
        self.assertTrue((jd.GOALDIR / (valid_sid + ".json")).exists())
        self.assertNotIn(TID, jd._CODEX_IDENTITY_MAP)
        self.assertNotIn(sid2, jd._CODEX_IDENTITY_MAP)

    def test_invalid_registry_ids_cannot_escape_namespaces_or_crash_boot(self):
        tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(tmp)
        for d in (jd.GOALDIR, jd.CODEXDIR):
            d.mkdir(parents=True, exist_ok=True)
        (jd.CODEXDIR / "registry.json").write_text(json.dumps({
            "../owned": {"tid": TID, "cwd": "/TESTDIR"},
            SID: {"tid": 7, "cwd": "/TESTDIR2"},
            "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee": {
                "tid": "../legacy", "cwd": "/TESTDIR3"},
        }))
        (jd.GOALDIR / (TID + ".json")).write_text(json.dumps(
            {"rompUuid": TID, "nodes": {}, "status": {}}))
        jd.migrate_codex_identity()
        self.assertTrue((jd.GOALDIR / (TID + ".json")).exists())
        self.assertFalse((jd.STATE / "owned.json").exists())

    @unittest.skipUnless(hasattr(os, "mkfifo") and os.path.isdir("/dev/fd"),
                         "POSIX descriptor regression")
    def test_rejected_identity_lock_files_do_not_leak_descriptors(self):
        tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(tmp)
        jd.CODEXDIR.mkdir(parents=True, exist_ok=True)
        os.mkfifo(jd.CODEXDIR / "identity-migration.lock")
        before = len(os.listdir("/dev/fd"))
        for _ in range(20):
            with self.assertRaises(OSError):
                with jd._identity_file_lock():
                    pass
        after = len(os.listdir("/dev/fd"))
        self.assertLessEqual(after, before + 1)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO regression")
    def test_a_fifo_intent_fails_closed_without_blocking_boot(self):
        self._state(goals=True)
        mig = jd.CODEXDIR / "migrated"
        mig.mkdir(parents=True, exist_ok=True)
        os.mkfifo(mig / (SID + ".intent"))
        started = time.monotonic()
        jd.migrate_codex_identity()
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertTrue((jd.GOALDIR / (TID + ".json")).exists())
        self.assertFalse((mig / (SID + ".done")).exists())

    def test_a_corrupt_goals_store_holds_the_transaction_open(self):
        self._state(goals=False, journal=True)
        (jd.GOALDIR / (TID + ".json")).write_text("{ corrupt")
        jd.migrate_codex_identity()
        self.assertFalse((jd.CODEXDIR / "migrated" / (SID + ".done")).exists(),
                         "a failed move never settles — the retry keeps its chance")
        self.assertTrue((jd.CODEXDIR / "migrated" / (SID + ".intent")).exists())
        self.assertTrue((jd._overrides_dir() / (TID + ".jsonl")).exists(),
                        "and the journal held with it")

    def test_both_goal_files_with_no_intent_preserve_both_and_stay_open(self):
        # a pre-transactional (v1.3.14) crash relic: the sid file may hold real post-upgrade
        # work — it wins, the relic stays inert, and the session settles
        self._state(goals=True)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "nodes": {SID + ":g1": {"t": 999, "fresh": True}}}))
        jd.migrate_codex_identity()
        g = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        self.assertTrue(g["nodes"][SID + ":g1"].get("fresh"), "the sid store's work survives")
        self.assertTrue((jd.GOALDIR / (TID + ".json")).exists(), "the relic stays, inert")
        self.assertFalse((jd.CODEXDIR / "migrated" / (SID + ".done")).exists(),
                         "an ambiguous relic is preserved and never hidden behind a permanent skip")

    def test_the_shared_identity_files_rewrite_and_stay_stable(self):
        # the v1.3.14 audit's P2 cluster
        self._state(goals=True)
        (jd.STATE / "cleared.jsonl").write_text(
            json.dumps({"id": TID + ":g1", "t": 1, "op": "clear"}) + "\n"
            + json.dumps({"id": "other:g2", "t": 2, "op": "clear"}) + "\n")
        (jd.STATE / "notify-cards.json").write_text(json.dumps({TID + ":g1": "all", "*": "off"}))
        (jd.STATE / "auto-nudge.json").write_text(json.dumps(
            {"enabled": True,
             "nudged": {TID + ":g1": {"why": TID + ": literal",
                                          "lastTurnId": TID + ":100:abc"}},
             "deferred": {TID + ":g1": {"sid": TID, "why": "the judge is running"}},
             "intrBlocked": {TID: TID + ":g1", SID: SID + ":g9"},
             "debtNudged": {TID + ">peer:10": 20, "peer>" + TID + ":11": 21}}))
        (jd.STATE / "nudge-events.jsonl").write_text(json.dumps(
            {"sid": TID, "gid": TID + ":g1", "t": 4, "count": 1}) + "\n")
        (jd.STATE / "judge-auth.json").write_text(json.dumps(
            {TID: {"t": 9, "mode": "login", "note": TID + ": literal"}}))
        (jd.STATE / "session-flags.json").write_text(json.dumps(
            {TID: {"hideFromFeed": True, "notify": True},
             SID: {"notify": False, "postalServiceOff": True}}))
        (jd.STATE / "session-order.json").write_text(json.dumps([TID, "zzz", SID]))
        (jd.STATE / "retry-suppressed.json").write_text(json.dumps(
            {TID: 20, SID: 10}))
        (jd.STATE / "timeline-views.json").write_text(json.dumps(
            {"groups": [{"id": "gA", "name": TID + ": literal",
                          "sessions": [TID, "zzz"]}], "hidden": [TID]}))
        (jd.STATE / "timeline-dismissed.json").write_text(json.dumps([TID, "zzz"]))
        jd.migrate_codex_identity()
        self.assertIn(SID + ":g1", (jd.STATE / "cleared.jsonl").read_text())
        self.assertNotIn(TID, (jd.STATE / "cleared.jsonl").read_text())
        self.assertIn(SID + ":g1", json.loads((jd.STATE / "notify-cards.json").read_text()))
        self.assertIn(SID + ":g1",
                      json.loads((jd.STATE / "auto-nudge.json").read_text())["nudged"])
        self.assertEqual(json.loads((jd.STATE / "auto-nudge.json").read_text())
                         ["nudged"][SID + ":g1"]["why"], TID + ": literal")
        auto = json.loads((jd.STATE / "auto-nudge.json").read_text())
        self.assertEqual(auto["nudged"][SID + ":g1"]["lastTurnId"], SID + ":100:abc")
        self.assertEqual(auto["deferred"][SID + ":g1"]["sid"], SID)
        self.assertEqual(auto["intrBlocked"], {SID: SID + ":g9"},
                         "the already-native stop record wins a relic collision")
        debt = json.loads((jd.STATE / "auto-nudge.json").read_text())["debtNudged"]
        self.assertIn(SID + ">peer:10", debt)
        self.assertIn("peer>" + SID + ":11", debt)
        ev = json.loads((jd.STATE / "nudge-events.jsonl").read_text().splitlines()[0])
        self.assertEqual((ev["sid"], ev["gid"]), (SID, SID + ":g1"))
        auth = json.loads((jd.STATE / "judge-auth.json").read_text())
        self.assertIn(SID, auth)
        self.assertEqual(auth[SID]["note"], TID + ": literal")
        self.assertEqual(json.loads((jd.STATE / "session-flags.json").read_text()),
                         {SID: {"hideFromFeed": True, "notify": False,
                                "postalServiceOff": True}},
                         "disjoint relic fields survive while native wins a same-flag collision")
        self.assertEqual(json.loads((jd.STATE / "session-order.json").read_text()),
                         [SID, "zzz"],
                         "the standalone migration owns shared identity stores, not the kernel")
        self.assertEqual(json.loads((jd.STATE / "retry-suppressed.json").read_text()),
                         {SID: 20},
                         "the newest interrupt floor wins a TID/SID collision")
        tv = json.loads((jd.STATE / "timeline-views.json").read_text())
        self.assertEqual(tv["groups"][0]["sessions"], [SID, "zzz"])
        self.assertEqual(tv["groups"][0]["name"], TID + ": literal")
        self.assertEqual(tv["hidden"], [SID])
        self.assertEqual(json.loads((jd.STATE / "timeline-dismissed.json").read_text()),
                         [SID, "zzz"],
                         "a dead Codex lane dismissed before the identity flip must not reappear")
        # idempotent: a second pass rewrites nothing (mtimes stable)
        mt = {n: (jd.STATE / n).stat().st_mtime_ns
              for n in ("cleared.jsonl", "notify-cards.json", "session-flags.json",
                        "session-order.json", "retry-suppressed.json", "timeline-views.json")}
        jd.migrate_codex_identity()
        for n, v in mt.items():
            self.assertEqual((jd.STATE / n).stat().st_mtime_ns, v,
                             "%s rewritten with no change — churn on every boot" % n)

    def test_migration_rewrites_identity_fields_without_rewriting_user_text(self):
        self._state(goals=True, captions=True, journal=True)
        goal_path = jd.GOALDIR / (TID + ".json")
        goal = json.loads(goal_path.read_text())
        goal["nodes"][TID + ":g1"].update(
            {"id": TID + ":g1", "text": TID + ":g1",
             "blockWhy": TID + ":g2", "doneWhy": TID + ":g3",
             "log": [{"seg": TID + ":100:aaa", "why": TID + ":g4"}],
             "origin": {"peer": TID, "goalId": TID + ":g2", "msgId": "m1"},
             "handoff": {"peer": TID, "msgId": "m2"}})
        goal["closedTurns"] = [TID + ":100:aaa"]
        goal["closedSig"] = {TID + ":100:aaa": 3}
        goal_path.write_text(json.dumps(goal))
        cap_path = jd.CAPDIR / (TID + ".jsonl")
        rows = [json.loads(line) for line in cap_path.read_text().splitlines()]
        rows[0]["caption"] = TID + ":g5"
        cap_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        with (jd._overrides_dir() / (TID + ".jsonl")).open("a") as fh:
            fh.write(json.dumps(
                {"op": "block", "node": TID + ":g1", "why": TID + ":g6", "t": 8}) + "\n")
        jd.migrate_codex_identity()
        moved = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        node = moved["nodes"][SID + ":g1"]
        self.assertEqual(node["id"], SID + ":g1")
        self.assertEqual(node["text"], TID + ":g1")
        self.assertEqual(node["blockWhy"], TID + ":g2")
        self.assertEqual(node["doneWhy"], TID + ":g3")
        self.assertEqual(node["log"][0]["seg"], SID + ":100:aaa")
        self.assertEqual(node["log"][0]["why"], TID + ":g4")
        self.assertEqual(node["origin"], {"peer": SID, "goalId": SID + ":g2",
                                          "msgId": "m1"})
        self.assertEqual(node["handoff"], {"peer": SID, "msgId": "m2"})
        self.assertEqual(moved["closedTurns"], [SID + ":100:aaa"])
        self.assertEqual(moved["closedSig"], {SID + ":100:aaa": 3})
        caption = json.loads((jd.CAPDIR / (SID + ".jsonl")).read_text().splitlines()[0])
        self.assertTrue(caption["id"].startswith(SID + ":"))
        self.assertEqual(caption["caption"], TID + ":g5")
        journal = [json.loads(line) for line in
                   (jd._overrides_dir() / (SID + ".jsonl")).read_text().splitlines()]
        block = next(row for row in journal if row.get("op") == "block")
        self.assertEqual(block["node"], SID + ":g1")
        self.assertEqual(block["why"], TID + ":g6")

    def test_canonicalizing_a_loaded_goal_store_preserves_guarded_nodes(self):
        self._state(goals=True)
        jd.migrate_codex_identity()
        store = jd.load_goals(SID)
        node = store["nodes"][SID + ":g1"]
        self.assertIsInstance(node, jd.GuardedNode)
        jd.save_goals(SID, store)
        node = store["nodes"][SID + ":g1"]
        self.assertIsInstance(node, jd.GuardedNode)
        with self.assertRaises(TypeError):
            node["blocked"] = True


class MigrationResumeSafety(unittest.TestCase):
    """The r42 verification's P1 pair: the sid-wins protection was gated on the in-process
    `resumed` flag — any persisted intent turned the retry into a clobber that re-published the
    tid relic over real post-upgrade work and unlinked the evidence."""

    def _state(self):
        tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(tmp)
        for d in (jd.GOALDIR, jd.CAPDIR, jd.CODEXDIR):
            d.mkdir(parents=True, exist_ok=True)
        (jd.CODEXDIR / "registry.json").write_text(json.dumps(
            {SID: {"tid": TID, "name": "cx", "cwd": "/TESTDIR", "dead": False}}))
        (jd.GOALDIR / (TID + ".json")).write_text(json.dumps(
            {"rompUuid": TID, "nodes": {TID + ":g1": {"t": 1}}, "status": {}}))
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "nodes": {SID + ":g2": {"t": 999, "fresh": True}}}))
        return tmp

    def test_a_retry_never_clobbers_a_preexisting_sid_store(self):
        # arming path 1 (executed by the verifier): a crash right after the intent lands
        self._state()
        real = jd._mig_atomic

        def crash_after_intent(path, text):
            real(path, text)
            if path.name.endswith(".intent"):
                raise SystemExit("crash: intent persisted, nothing moved yet")
        with mock.patch.object(jd, "_mig_atomic", side_effect=crash_after_intent):
            with self.assertRaises(SystemExit):
                jd.migrate_codex_identity()
        self.assertTrue((jd.CODEXDIR / "migrated" / (SID + ".intent")).exists())
        jd.migrate_codex_identity()                   # the RETRY
        g = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        self.assertIn(SID + ":g2", g["nodes"],
                      "the PRE-EXISTING sid store survives every retry — the decision is "
                      "durable in the intent, never the in-process resumed flag")
        self.assertTrue(g["nodes"][SID + ":g2"].get("fresh"))
        self.assertTrue((jd.GOALDIR / (TID + ".json")).exists(), "the relic stays inert")

    def test_a_corrupt_sibling_namespace_never_arms_the_clobber(self):
        # arming path 2 (executed by the verifier): no crash injection at all — a corrupt
        # sibling namespace persisted the intent and the next boot's retry did the damage
        self._state()
        (jd.CAPDIR / (TID + ".fails.json")).write_text("{ corrupt")
        jd.migrate_codex_identity()                   # run 1: capfails fails, intent persists
        jd.migrate_codex_identity()                   # run 2: the old retry clobbered here
        g = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        self.assertIn(SID + ":g2", g["nodes"])
        self.assertTrue((jd.GOALDIR / (TID + ".json")).exists())

    def test_a_corrupt_intent_fails_closed_instead_of_clobbering_the_sid_store(self):
        self._state()
        mig = jd.CODEXDIR / "migrated"
        mig.mkdir(parents=True, exist_ok=True)
        (mig / (SID + ".intent")).write_text("{ corrupt")
        jd.migrate_codex_identity()
        g = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        self.assertIn(SID + ":g2", g["nodes"],
                      "an unreadable transaction record cannot erase post-upgrade SID work")
        self.assertTrue(g["nodes"][SID + ":g2"].get("fresh"))
        self.assertTrue((jd.GOALDIR / (TID + ".json")).exists(),
                        "unknown ownership leaves both stores for explicit recovery")
        self.assertFalse((mig / (SID + ".done")).exists(),
                         "unknown ownership must not be declared settled")

    def test_a_writer_after_a_caught_failure_is_never_clobbered_on_retry(self):
        tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(tmp)
        for d in (jd.GOALDIR, jd.CODEXDIR):
            d.mkdir(parents=True, exist_ok=True)
        (jd.CODEXDIR / "registry.json").write_text(json.dumps(
            {SID: {"tid": TID, "name": "cx", "cwd": "/TESTDIR", "dead": False}}))
        (jd.GOALDIR / (TID + ".json")).write_text(json.dumps(
            {"rompUuid": TID, "nodes": {TID + ":g1": {"text": "old"}}, "status": {}}))
        real = jd._mig_atomic

        def refuse_goal_publish(path, text):
            if path == jd.GOALDIR / (SID + ".json"):
                raise OSError("synthetic publish failure")
            return real(path, text)

        with mock.patch.object(jd, "_mig_atomic", side_effect=refuse_goal_publish):
            jd.migrate_codex_identity()
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "nodes": {SID + ":g9": {"text": "new", "fresh": True}},
             "status": {}}))
        jd.migrate_codex_identity()
        g = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        self.assertIn(SID + ":g9", g["nodes"],
                      "a post-failure SID writer is legitimate, not our partial publish")
        self.assertTrue((jd.GOALDIR / (TID + ".json")).exists())
        self.assertFalse((jd.CODEXDIR / "migrated" / (SID + ".done")).exists())

    def test_the_ledger_lands_before_the_unlink(self):
        # the r42 verification's P3: publish→unlink→persist was write-BEHIND — a crash in the
        # unlink gap left the store moved with an empty ledger, the retry skipped the
        # namespace, and .done sealed the journal orphaned though the merge was safe
        tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(tmp)
        for d in (jd.GOALDIR, jd.CODEXDIR):
            d.mkdir(parents=True, exist_ok=True)
        (jd.CODEXDIR / "registry.json").write_text(json.dumps(
            {SID: {"tid": TID, "name": "cx", "cwd": "/TESTDIR", "dead": False}}))
        (jd.GOALDIR / (TID + ".json")).write_text(json.dumps(
            {"rompUuid": TID, "nodes": {TID + ":g1": {"t": 1}}, "status": {}}))
        ov = jd._overrides_dir()
        ov.mkdir(parents=True, exist_ok=True)
        (ov / (TID + ".jsonl")).write_text(json.dumps(
            {"op": "resolve", "node": TID + ":g1", "t": 5}) + "\n")
        real_unlink = Path.unlink

        def crash_at_goals_unlink(p, *a, **kw):
            if p.name == TID + ".json" and p.parent == jd.GOALDIR:
                raise SystemExit("crash: store published + ledger persisted, unlink not yet")
            return real_unlink(p, *a, **kw)
        with mock.patch.object(Path, "unlink", crash_at_goals_unlink):
            with self.assertRaises(SystemExit):
                jd.migrate_codex_identity()
        intent = json.loads((jd.CODEXDIR / "migrated" / (SID + ".intent")).read_text())
        self.assertTrue(intent["moved"].get("goals"),
                        "the ledger is write-AHEAD of the unlink — the retry knows the move "
                        "was OURS (the r42 verification's orphaned-journal gap)")
        jd.migrate_codex_identity()                   # the retry completes and merges
        self.assertTrue((ov / (SID + ".jsonl")).exists(),
                        "the journal reaches the store that DID move")
        self.assertFalse((ov / (TID + ".jsonl")).exists())

    def test_the_intent_lands_before_the_first_move(self):
        # the r42 mutant hunt's M1: intent-after-first-move survived because the crash
        # injection was a caught Exception — SystemExit models the real kill
        self._state()
        (jd.GOALDIR / (SID + ".json")).unlink()       # single-store shape: the move runs
        real = jd._mig_atomic
        seen = []

        def crash_at_first_store_publish(path, text):
            seen.append(path.name)
            if path.parent in (jd.GOALDIR, jd.CAPDIR):
                raise SystemExit("crash: first store publish")
            real(path, text)
        with mock.patch.object(jd, "_mig_atomic", side_effect=crash_at_first_store_publish):
            with self.assertRaises(SystemExit):
                jd.migrate_codex_identity()
        self.assertTrue((jd.CODEXDIR / "migrated" / (SID + ".intent")).exists(),
                        "the intent is DURABLE before anything moves: %r" % seen)


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
