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
import time
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




class R57AuditFixes(unittest.TestCase):
    """the v1.3.29 audit: the quarantine linked a PATHNAME a locked writer had replaced
    (P1.2), invalid stored rows silently retired live gestures (P1.3), one flags EIO
    un-isolated a session and CACHED the fabrication (P1.4), control-ledger RMWs folded
    faults to defaults — reversing explicit user stops (P1.5) — dispatched:0 passed for a
    bool and a float-inf rekey raised into a 500 (P2.14), a foreign-claimed retirement was
    skipped without being NAMED (P2.12), and {}-shaped order bytes read proved-empty and
    were then overwritten (P2.16)."""

    SID = "12121212-3434-5656-7878-909090909090"
    ROW = {"host": "TESTHOST-A", "gid": 7, "edit": {}, "inverse": {}, "rt": {},
           "name": "pool", "dispatched": False}

    def setUp(self):
        _scrub_state()
        km._union_claims.clear()
        km._union_retired_tombs.clear()
        km._retry_paused_cache[0] = None
        km._union_tombs_loaded[0] = False
        for name in ("union-gestures.json", "union-gestures.json.unproved",
                     "union-tombs.json", "session-order.json", "auto-nudge.json",
                     "retry-paused.json"):
            try:
                (km.jd.STATE / name).unlink()
            except OSError:
                pass
        for f in km.jd.STATE.glob("*.corrupt-*"):
            try:
                f.unlink()
            except OSError:
                pass

    def tearDown(self):
        self.setUp()

    def test_a_quarantine_spares_a_concurrent_valid_replacement(self):
        # r57 P1.2, executed there: the fingerprint was checked against the PATH, then the
        # path was linked — a locked writer's valid commit in between was quarantined and
        # the store lost. The fix links the VERIFIED INODE via its open fd.
        p = km.jd.STATE / "union-gestures.json"
        p.write_text("{{{not json")
        st = p.stat()
        fp = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        tmp = km.jd.STATE / "union-gestures.json.new"
        tmp.write_text("[]")                         # the concurrent writer's valid commit
        os.replace(tmp, p)                           # …atomically, on a NEW inode
        with contextlib.redirect_stderr(io.StringIO()):
            km._quarantine_state_bytes(p, "union-gestures", fingerprint=fp)
        self.assertEqual(p.read_text(), "[]", "the valid replacement SURVIVES")
        self.assertEqual(list(km.jd.STATE.glob("union-gestures.json.corrupt-*")), [],
                         "nothing moved aside — the judged bytes are already gone")

    def test_b_quarantine_moves_exactly_the_judged_inode(self):
        p = km.jd.STATE / "union-gestures.json"
        p.write_text("{{{not json")
        st = p.stat()
        with contextlib.redirect_stderr(io.StringIO()):
            km._quarantine_state_bytes(
                p, "union-gestures",
                fingerprint=(st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns))
        qs = list(km.jd.STATE.glob("union-gestures.json.corrupt-*"))
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0].read_text(), "{{{not json", "the judged bytes are preserved")
        self.assertFalse(p.exists(), "…and the source is retired")

    def test_c_dispatched_must_be_a_real_bool(self):
        # r57 P2.14: 0 passed the falsy check, so int poison rode into the dispatch ratchet
        self.assertTrue(km._union_row_valid(dict(self.ROW)))
        self.assertFalse(km._union_row_valid(dict(self.ROW, dispatched=0)))
        self.assertFalse(km._union_row_valid(dict(self.ROW, dispatched=1)))

    def test_d_inf_rekey_refuses_instead_of_500(self):
        # r57 P2.14: json.loads accepts bare Infinity; int(float('inf')) raised
        # OverflowError past the (TypeError, ValueError) net and 500'd the POST.
        # Since r60 P1.1 the refusal is the STRICT malformed-ids arm (never coerced),
        # still a clean refusal, never a 500 — the point this test pins.
        self.assertTrue(km._union_ops_set([dict(self.ROW)]))
        ok, _, reason, _u = km._union_ops_merge(
            [], [], ckey="ws:x", rekey={"ogid": float("inf"), "gid": 8, "epoch": 1})
        self.assertFalse(ok)
        self.assertIn("malformed", reason, "an unparseable rekey refuses loudly, never 500s")

    def test_e_foreign_claimed_retirement_is_skipped_and_named(self):
        # r57 P2.12: the skip protected the claimant but was SILENT — the bystander panel
        # dropped its ledger row and the retirement never re-sent after the claim cleared
        self.assertTrue(km._union_ops_set([dict(self.ROW, gid=901)]))
        self.assertTrue(km._union_claim_grant(901, "ws:owner"))
        ok, _, _, unret = km._union_ops_merge([], [901], ckey="ws:bystander")
        self.assertTrue(ok)
        self.assertIn(901, unret, "the skipped retirement is NAMED so the panel re-ledgers")
        self.assertEqual([r["gid"] for r in km._union_ops_load()], [901],
                         "the claimed gesture survives the bystander's stale diff")

    @unittest.skipIf(os.geteuid() == 0, "chmod 0 does not block reads for root")
    def test_f_flags_fault_serves_last_known_good_and_caches_no_fabrication(self):
        p = km.jd.STATE / "session-flags.json"
        p.write_text(json.dumps({self.SID: {"postalServiceOff": True}}))
        self.assertTrue(km._session_flag(self.SID, "postalServiceOff"))   # primes the copy
        os.utime(p, (1, 1))                          # a new mtime key defeats the cache hit
        os.chmod(p, 0)
        try:
            self.assertTrue(km._session_flag(self.SID, "postalServiceOff"),
                            "an unreadable window serves the last-known-good copy (r57 "
                            "P1.4) — one EIO used to deliver into an isolated session")
        finally:
            os.chmod(p, 0o644)
        km._flags_cache.clear()                      # a fresh process with NO good read yet
        os.utime(p, (2, 2))
        os.chmod(p, 0)
        try:
            self.assertEqual(km._session_flags(), {})
            self.assertEqual(km._flags_cache, {},
                             "the fabricated {} must never enter the cache — cached under "
                             "the real mtime key it kept serving after the fault cleared")
        finally:
            os.chmod(p, 0o644)
        self.assertTrue(km._session_flag(self.SID, "postalServiceOff"),
                        "…so the cleared fault reads the REAL flags again")

    @unittest.skipIf(os.geteuid() == 0, "chmod 0 does not block reads for root")
    def test_g_control_ledger_mutations_read_proved(self):
        # r57 P1.5, executed there: one EIO folded auto-nudge to its default — flipping an
        # explicit enabled:false back ON and erasing the standing nudge memos on the write
        p = km.jd.STATE / "auto-nudge.json"
        d = km._proved_ledger_read(p, "auto-nudge", {"enabled": True})
        self.assertEqual(d, {"enabled": True})
        d["x"] = 1                                   # the return is a COPY of the default
        self.assertEqual(km._proved_ledger_read(p, "auto-nudge", {"enabled": True}),
                         {"enabled": True})
        p.write_text("[1, 2]")                       # valid bytes, wrong shape
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._proved_ledger_read(p, "auto-nudge", {"enabled": True}),
                             {"enabled": True})
        self.assertEqual(len(list(km.jd.STATE.glob("auto-nudge.json.corrupt-*"))), 1,
                         "wrong-shaped bytes quarantine aside — never silently replaced")
        p.write_text(json.dumps({"enabled": False, "nudged": {"g": 1}}))
        os.chmod(p, 0)
        try:
            with self.assertRaises(km._StateUnreadable):
                km._proved_ledger_read(p, "auto-nudge", {"enabled": True})
            with self.assertRaises(km._StateUnreadable):
                km._set_auto_nudge(True)             # the MUTATION refuses — never defaults
        finally:
            os.chmod(p, 0o644)
        self.assertEqual(json.loads(p.read_text()), {"enabled": False, "nudged": {"g": 1}},
                         "the refused write changed NOTHING")
        km._set_auto_nudge(True)
        d2 = json.loads(p.read_text())
        self.assertIs(d2["enabled"], True)
        self.assertEqual(d2["nudged"], {"g": 1}, "the honored write keeps sibling keys")

    @unittest.skipIf(os.geteuid() == 0, "chmod 0 does not block reads for root")
    def test_h_retry_pause_folds_toward_paused(self):
        # r57 P1.5: the lenient reader answered False on EIO — un-stopping retries the user
        # had explicitly paused
        p = km.jd.STATE / "retry-paused.json"
        self.assertFalse(km._retry_paused_on(), "provably no file: not paused")
        p.write_text(json.dumps({"paused": True}))
        self.assertTrue(km._retry_paused_on())
        os.chmod(p, 0)
        try:
            self.assertTrue(km._retry_paused_on(), "the fault serves last-known-good")
            km._retry_paused_cache[0] = None         # a fresh process with no history
            self.assertTrue(km._retry_paused_on(),
                            "…and with none folds toward PAUSED — the safe direction")
        finally:
            os.chmod(p, 0o644)
        p.write_text(json.dumps({"paused": False}))
        self.assertFalse(km._retry_paused_on())

    def test_i_wrong_shaped_order_quarantines_not_proved_empty(self):
        # r57 P2.16: {}-shaped bytes read as [] and the next drag PERSISTED over them —
        # the user's saved order was unrecoverable
        p = km.jd.STATE / "session-order.json"
        p.write_text(json.dumps({"a": 1}))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._session_order_proved(), [])
        self.assertFalse(p.exists())
        self.assertEqual(len(list(km.jd.STATE.glob("session-order.json.corrupt-*"))), 1,
                         "the bytes move aside — a later writer can't silently erase them")




class R57Wave2(unittest.TestCase):
    """the r57 wave-2 verification round (30 confirmed second-order findings over the
    wave-1 fixes, each reproduced by an adversarial skeptic): the whole-journal quarantine
    read authoritative-empty one push later, the flags setter's cache clear destroyed the
    last-known-good copy every fault arm depends on, the control caches primed on reads
    only, wrong-shape valid bytes failed open in every safety reader, and /deliver's
    kernel-side isolation gate failed open with no history."""

    SID = "13131313-4242-5757-6868-424242424242"
    SID2 = "14141414-4242-5757-6868-424242424242"

    def setUp(self):
        _scrub_state()
        km._union_claims.clear()
        km._union_retired_tombs.clear()
        km._retry_paused_cache[0] = None
        km._retry_pause_lkg["reason"] = ""
        km._retry_pause_lkg["ts"] = 0.0
        km._notify_cards_cache.clear()
        km._autonudge_cache.clear()
        km._union_tombs_loaded[0] = False
        km._postal_isolated_warned[0] = False
        for name in ("union-gestures.json", "union-gestures.json.unproved",
                     "union-tombs.json", "session-order.json", "auto-nudge.json",
                     "retry-paused.json", "notify-cards.json"):
            try:
                (km.jd.STATE / name).unlink()
            except OSError:
                pass
        for f in km.jd.STATE.glob("*.corrupt-*"):
            try:
                f.unlink()
            except OSError:
                pass

    def tearDown(self):
        self.setUp()

    @unittest.skipIf(os.geteuid() == 0, "chmod 0 does not block reads for root")
    def test_a_toggle_then_fault_serves_the_last_known_good(self):
        km._set_session_flag(self.SID, "hideFromFeed", True)
        self.assertTrue(km._session_flag(self.SID, "hideFromFeed"))
        km._set_session_flag(self.SID2, "postalServiceOff", True)   # the toggle that CLEARED
        p = km.jd.STATE / "session-flags.json"
        os.utime(p, (1, 1))
        os.chmod(p, 0)
        try:
            self.assertTrue(km._session_flag(self.SID, "hideFromFeed"),
                            "wave 2, reproduced: the setter's cache clear destroyed the "
                            "last-known-good copy — every toggle re-opened the {} window")
            self.assertTrue(km._session_flag(self.SID2, "postalServiceOff"),
                            "…and the just-written flag answers from the seed")
        finally:
            os.chmod(p, 0o644)

    def test_b_wrong_shape_flags_are_a_fault(self):
        km._set_session_flag(self.SID, "postalServiceOff", True)
        self.assertTrue(km._session_flag(self.SID, "postalServiceOff"))
        p = km.jd.STATE / "session-flags.json"
        p.write_text("[]")                           # valid bytes, wrong shape
        self.assertTrue(km._session_flag(self.SID, "postalServiceOff"),
                        "wave 2, reproduced: []-shaped bytes took the success path and "
                        "un-isolated every session past a primed last-known-good")
        km._flags_cache.clear()
        self.assertEqual(km._session_flags_read(), ({}, False),
                         "no history: the {} is marked UNPROVED, never evidence")

    @unittest.skipIf(os.geteuid() == 0, "chmod 0 does not block reads for root")
    def test_c_postal_gate_fails_closed_on_unproved_flags(self):
        p = km.jd.STATE / "session-flags.json"
        p.write_text(json.dumps({}))
        os.utime(p, (1, 1))
        os.chmod(p, 0)
        km._flags_cache.clear()
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertTrue(km._postal_isolated(self.SID),
                                "wave 2, reproduced: /deliver's ONLY gate read the "
                                "fabricated {} and injected into an isolated session")
        finally:
            os.chmod(p, 0o644)
        self.assertFalse(km._postal_isolated(self.SID), "a readable store answers normally")

    @unittest.skipIf(os.geteuid() == 0, "chmod 0 does not block reads for root")
    def test_d_stop_then_fault_stays_paused(self):
        self.assertFalse(km._retry_paused_on())      # caches the pre-Stop False
        km._set_retry_paused(True, reason="spend")   # the user's Stop
        p = km.jd.STATE / "retry-paused.json"
        os.chmod(p, 0)
        try:
            self.assertTrue(km._retry_paused_on(),
                            "wave 2, reproduced: the read-only cache served the pre-Stop "
                            "False and retries kept firing over the explicit Stop")
            self.assertEqual(km._retry_pause_reason(), "spend",
                             "…and the spend classification survives the same window")
            self.assertGreater(km._retry_pause_ts(), 0.0)
        finally:
            os.chmod(p, 0o644)
        km._set_retry_paused(False)
        self.assertFalse(km._retry_paused_on())

    @unittest.skipIf(os.geteuid() == 0, "chmod 0 does not block reads for root")
    def test_e_master_bell_toggle_reads_proved(self):
        p = km.jd.STATE / "notify-cards.json"
        p.write_text(json.dumps({"card-1": False}))  # a standing per-card override
        os.chmod(p, 0)
        km._notify_cards_cache.clear()
        try:
            with self.assertRaises(km._StateUnreadable):
                km._set_notify_all(True)
        finally:
            os.chmod(p, 0o644)
        self.assertEqual(json.loads(p.read_text()), {"card-1": False},
                         "wave 2, reproduced: the lenient snapshot persisted the fabricated "
                         "{} and erased every override under a 200")
        km._set_notify_all(True)
        d = json.loads(p.read_text())
        self.assertIs(d.get(km.NOTIFY_ALL_KEY), True)
        self.assertIs(d.get("card-1"), False, "the honored write keeps the overrides")

    def test_f_inf_claim_gid_refuses_not_500(self):
        self.assertIsNone(km._union_claim_grant(float("inf"), "cid:x"),
                          "wave 2, reproduced: int(inf) raised OverflowError into a 500 on "
                          "POST /union-claim, and the WS arm dropped the ack — the panel's "
                          "_pendingClaims latched forever")

    def test_g_malformed_journal_holds_unproved_until_reconstructed(self):
        # r58 P1.1, executed there: the r57 re-mint-[] answer made corruption read as an
        # AUTHORITATIVE empty one push later — None once, then [], and panels retired
        # their recovery copies. Corruption now stands JUDGED (bytes at the path + a
        # durable marker) and every read answers "no information" until a panel's mirror
        # sync RECONSTRUCTS the store through the merge.
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._union_ops_path().write_text("{{{garbage")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(km._union_ops_echo(), "the judged push has no information")
            self.assertIsNone(km._union_ops_echo(),
                              "…and the NEXT push too — never an authoritative []")
        self.assertEqual(km._union_ops_path().read_text(), "{{{garbage",
                         "the judged bytes STAND at the path (failure-atomic, r58 P1.2)")
        self.assertTrue(km._union_unproved_marker().exists(), "…under a durable marker")
        self.assertEqual(len(list(km.jd.STATE.glob("union-gestures.json.corrupt-*"))), 1,
                         "one forensic copy — later reads never re-quarantine")
        # a bare retirement proves nothing to rebuild from: refused, retryable
        with contextlib.redirect_stderr(io.StringIO()):
            ok, _, reason, _u = km._union_ops_merge([], [7], ckey="ws:x")
        self.assertFalse(ok)
        self.assertIn("reconstruction", reason)
        # a panel's mirror rows ARE the reconstruction evidence
        with contextlib.redirect_stderr(io.StringIO()):
            ok, _, _, _u = km._union_ops_merge(
                [{"host": "TESTHOST-A", "gid": 9, "edit": {}, "inverse": {}, "rt": {},
                  "name": "pool", "dispatched": False}], [], ckey="ws:x")
        self.assertTrue(ok)
        self.assertFalse(km._union_unproved_marker().exists(),
                         "the commit clears the marker in the same locked pass")
        self.assertEqual([r["gid"] for r in km._union_ops_echo()], [9],
                         "…and the store is proven again")
        km._union_unproved_marker().unlink(missing_ok=True)
        for q in km.jd.STATE.glob("union-gestures.json.corrupt-*"):
            q.unlink()

    def test_h_echo_quarantine_runs_under_the_identity_lock(self):
        # the wave-2 verification reproduced the stat→unlink TOCTOU by scheduling a locked
        # merge inside the UNLOCKED echo's quarantine window — the writer's fresh valid
        # commit was deleted and the next echo answered authoritative-empty. The lock is
        # the fix: every judgment and salvage now commits under the same lock the writers
        # hold, so no rename can interleave.
        src = open(os.path.join(BIN, "romp-kernel")).read()
        fn = src.index("def _union_ops_echo():")
        body = src[fn:src.index("def ", fn + 10)]
        self.assertIn("with jd._identity_file_lock():", body)




class R58AuditFixes(unittest.TestCase):
    """the v1.3.30 audit, kernel half (11 P1 / 9 P2 against 4aeec698): corrupt union state
    became authoritative absence one push later (P1.1), the salvage moved the only live
    inode aside before publishing (P1.2), a stale permissive flags copy crossed a newer
    isolation (P1.4), auto-nudge RMWs fabricated-and-overwrote (P1.5), non-dicts were
    filtered before validation and nested Infinity rode the wire (P1.6), tombstones were
    process-local (P2.14), and 2**53 passed the safe-integer gate (P2.19)."""

    SID = "15151515-6262-7373-8484-616161616161"
    ROW = {"host": "TESTHOST-A", "gid": 7, "edit": {}, "inverse": {}, "rt": {},
           "name": "pool", "dispatched": False}

    def setUp(self):
        _scrub_state()
        km._union_claims.clear()
        km._union_retired_tombs.clear()
        km._union_tombs_loaded[0] = False
        km._retry_paused_cache[0] = None
        km._autonudge_cache.clear()
        km._auto_nudge_skip_warned[0] = False
        km._postal_isolated_warned[0] = False
        for name in ("union-gestures.json", "union-gestures.json.unproved",
                     "union-tombs.json", "session-order.json", "auto-nudge.json",
                     "retry-paused.json", "notify-cards.json"):
            try:
                (km.jd.STATE / name).unlink()
            except OSError:
                pass
        for f in km.jd.STATE.glob("*.corrupt-*"):
            try:
                f.unlink()
            except OSError:
                pass

    def tearDown(self):
        self.setUp()

    def test_a_salvage_is_failure_atomic(self):
        # r58 P1.2, reproduced there: the quarantine moved the only live inode aside BEFORE
        # the salvage published — one injected write fault left ENOENT, and the next read
        # answered authoritative-[]. Copy-first: the poisoned file stands until the very
        # rename that publishes the valid rows.
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        _good = ('{"host": "TESTHOST-B", "gid": 7, "edit": {}, "inverse": {}, "rt": {}, '
                 '"name": "pool", "dispatched": false}')
        poisoned = '[%s, {"host": "TESTHOST-A", "gid": 0.5}]' % _good
        km._union_ops_path().write_text(poisoned)
        real = km._atomic_write

        def boom(path, data):
            if str(path) == str(km._union_ops_path()):
                raise OSError(5, "EIO")
            return real(path, data)

        km._atomic_write = boom
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertIsNone(km._union_ops_echo(), "the failed salvage holds")
        finally:
            km._atomic_write = real
        self.assertEqual(km._union_ops_path().read_text(), poisoned,
                         "the judged bytes STAND at the path — never an ENOENT window")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(km._union_ops_echo(),
                              "the healed salvage is an UNPROVED base (r59 P1.2) — echoes "
                              "hold until a live panel reconstructs over it")
        self.assertEqual([r["gid"] for r in json.loads(km._union_ops_path().read_text())],
                         [7], "…the valid rows re-minted as that base")
        self.assertTrue(km._union_unproved_marker().exists())
        with contextlib.redirect_stderr(io.StringIO()):
            ok, _, _, _u = km._union_ops_merge(
                [{"host": "TESTHOST-B", "gid": 7, "edit": {}, "inverse": {}, "rt": {},
                  "name": "pool", "dispatched": False}], [], ckey="ws:x")
        self.assertTrue(ok)
        self.assertEqual([r["gid"] for r in km._union_ops_echo()], [7],
                         "…and the mirror-vouched reconstruction proves it")

    def test_b_non_dict_rows_are_judged_not_silently_dropped(self):
        # r58 P1.6: the isinstance pre-filter acked a mixed journal while silently dropping
        # its invalid rows — the drop IS the silent retirement the whole chain forbids
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        _good = ('{"host": "TESTHOST-B", "gid": 7, "edit": {}, "inverse": {}, "rt": {}, '
                 '"name": "pool", "dispatched": false}')
        km._union_ops_path().write_text('[123, %s]' % _good)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(km._union_ops_echo(),
                              "a mixed journal is UNPROVED (r59 P1.2): a partial view "
                              "declared the dropped groups retired")
        self.assertEqual(len(list(km.jd.STATE.glob("union-gestures.json.corrupt-*"))), 1,
                         "the mixed journal was JUDGED (bytes aside), not silently filtered")
        self.assertTrue(km._union_unproved_marker().exists())

    def test_c_nested_infinity_is_rejected_at_the_gate(self):
        # r58 P1.6: a nested non-finite passed the top-level checks and either raised at
        # the strict dump or rode the wire as JSON the browser refused whole
        self.assertFalse(km._union_row_valid(dict(self.ROW, edit={"x": float("inf")})))
        self.assertFalse(km._union_row_valid(dict(self.ROW, rt={"a": [{"b": float("nan")}]})))
        self.assertTrue(km._union_row_valid(dict(self.ROW, edit={"x": [1, "y", None, 2.5]})))
        ok, _, reason, _u = km._union_ops_merge(
            [dict(self.ROW, inverse={"deep": {"deeper": float("inf")}})])
        self.assertFalse(ok)
        self.assertIn("malformed", reason or "")

    def test_d_safe_integer_bound_is_exclusive_of_2_53(self):
        # r58 P2.19: the server accepted 2**53 — outside JavaScript's exact domain
        self.assertFalse(km._union_row_valid(dict(self.ROW, gid=2 ** 53)))
        self.assertTrue(km._union_row_valid(dict(self.ROW, gid=2 ** 53 - 1)))
        self.assertIsNone(km._union_claim_grant(2 ** 53, "cid:x"))
        self.assertFalse(km._union_row_valid(dict(self.ROW, rid=2 ** 53)))
        self.assertTrue(km._union_row_valid(dict(self.ROW, rid=5)),
                        "the stable root id rides the schema (r58 P2.19)")

    def test_e_tombstones_survive_a_kernel_restart(self):
        # r58 P2.14, reproduced there: claims and tombstones were process-local — a stale
        # panel's replay after a restart resurrected a RETIRED gesture and both completion
        # CAS operations succeeded
        self.assertTrue(km._union_ops_set([dict(self.ROW, gid=911)]))
        ok, _, _, _u = km._union_ops_merge([], [911], ckey="ws:x")
        self.assertTrue(ok)
        self.assertTrue((km.jd.STATE / "union-tombs.json").exists(), "minted durable")
        km._union_retired_tombs.clear()              # the process died
        km._union_tombs_loaded[0] = False
        ok, unclaimed, _, _u = km._union_ops_merge(
            [dict(self.ROW, gid=911)], [], ckey="ws:y")   # the stale replay
        self.assertTrue(ok)
        self.assertIn(911, unclaimed,
                      "the reloaded tombstone names the replay back — the gate yields")
        self.assertEqual(km._union_ops_load(), [], "the retired gesture stays retired")

    @unittest.skipIf(os.geteuid() == 0, "chmod 0 does not block reads for root")
    def test_f_stale_permissive_flags_never_cross_a_newer_generation(self):
        # r58 P1.4, reproduced there: an EIO on a NEWER inode let the stale not-isolated
        # copy deliver into a durably-isolated session
        km._set_session_flag(self.SID, "postalServiceOff", False)
        self.assertFalse(km._postal_isolated(self.SID))   # primes a PERMISSIVE copy
        p = km.jd.STATE / "session-flags.json"
        p.write_text("{not valid json")              # a NEWER generation, unreadable
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(km._postal_isolated(self.SID),
                            "the stale permissive copy is never proof across it — closed")
        d, proved = km._session_flags_read()
        self.assertFalse(proved)
        p.unlink()
        km._set_session_flag(self.SID, "hideFromFeed", True)
        os.chmod(p, 0)                               # SAME generation, unreadable window
        try:
            self.assertTrue(km._session_flag(self.SID, "hideFromFeed"),
                            "…while a same-generation fault still serves the proven copy")
            _, proved = km._session_flags_read()
            self.assertTrue(proved)
        finally:
            os.chmod(p, 0o644)

    @unittest.skipIf(os.geteuid() == 0, "chmod 0 does not block reads for root")
    def test_g_unproved_nudge_snapshots_never_persist(self):
        # r58 P1.5, reproduced there: a fault fabricated the default snapshot and the next
        # writer PERSISTED it — enabled:false flipped back on, suppressions/debt erased
        p = km.jd.STATE / "auto-nudge.json"
        p.write_text(json.dumps({"enabled": False, "nudged": {"g1": {"count": 2}},
                                 "intrBlocked": {"s1": "g1"}}))
        os.utime(p, (1, 1))                          # a generation the cache has not seen
        os.chmod(p, 0)
        try:
            d = km._auto_nudge_data()
            self.assertTrue(d.get("_unproved"), "the fabricated snapshot wears its tag")
            with contextlib.redirect_stderr(io.StringIO()):
                km._set_intr_blocked("s2", "g9")     # one of the seventeen RMW sites
        finally:
            os.chmod(p, 0o644)
        self.assertEqual(json.loads(p.read_text()),
                         {"enabled": False, "nudged": {"g1": {"count": 2}},
                          "intrBlocked": {"s1": "g1"}},
                         "the refused write changed NOTHING — no fault launders itself "
                         "into durable state through any RMW site")
        km._set_intr_blocked("s2", "g9")             # the healed store accepts the mutation
        d2 = json.loads(p.read_text())
        self.assertEqual(d2["intrBlocked"], {"s1": "g1", "s2": "g9"})
        self.assertIs(d2["enabled"], False, "…and the explicit stop survives")

    def test_h_claim_grant_reads_four_state(self):
        # r58 wave 2, reproduced: this branch read through _read_state_json, whose
        # malformed arm quarantined WITH UNLINK and armed no marker — one client claim
        # against a corrupt store minted the authoritative-empty the round closed
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._union_ops_path().write_text("{{{garbage")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(km._union_claim_grant(7, "cid:x"), "no proof, no claim")
        self.assertEqual(km._union_ops_path().read_text(), "{{{garbage",
                         "the judged bytes STAND at the path")
        self.assertTrue(km._union_unproved_marker().exists(), "…under the durable marker")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(km._union_ops_echo(), "the next read still holds")

    def test_i_reconstruction_needs_surviving_evidence(self):
        # r58 wave 2, reproduced twice: a lone refusal row and a tombstoned-only replay
        # each cleared the marker and minted a (near-)empty store from nothing; and the
        # live SUCCESSOR of a tombstoned gesture was dropped because cur=[] blinded the
        # resident exemption
        self.assertTrue(km._union_ops_set([dict(self.ROW, gid=911)]))
        ok, _, _, _u = km._union_ops_merge([], [911], ckey="ws:x")   # durable tombstone
        self.assertTrue(ok)
        km._union_ops_path().write_text("{{{garbage")
        with contextlib.redirect_stderr(io.StringIO()):
            km._union_ops_echo()                     # judge: marker armed
        with contextlib.redirect_stderr(io.StringIO()):
            ok, _, reason, _u = km._union_ops_merge(
                [{"host": "TESTHOST-A", "gid": -5, "refusal": True, "t": 1, "name": "x"}])
        self.assertTrue(ok, "a refusal JOURNALS even during the hold (r59 wave 2: memory-"
                            "queued refusals were lost on restart)…")
        self.assertTrue(km._union_unproved_marker().exists(),
                        "…but it reconstructs NOTHING — the hold stands")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(km._union_ops_echo(), "echoes still hold")
        with contextlib.redirect_stderr(io.StringIO()):
            ok, _, reason, _u = km._union_ops_merge(
                [dict(self.ROW, gid=911)], [], ckey="ws:y")
        self.assertFalse(ok, "a tombstoned-only replay reconstructs NOTHING")
        with contextlib.redirect_stderr(io.StringIO()):
            ok, unclaimed, _, _u = km._union_ops_merge(
                [dict(self.ROW, gid=912, ogid=911, olin=[911])], [], ckey="ws:z")
        self.assertTrue(ok, "the SUCCESSOR of a tombstoned gesture is live evidence")
        self.assertNotIn(912, unclaimed, "…and is never tombstone-dropped mid-rebuild")
        self.assertFalse(km._union_unproved_marker().exists())
        self.assertEqual([r["gid"] for r in km._union_ops_load()], [912])

    @unittest.skipIf(os.geteuid() == 0, "chmod 0 does not block reads for root")
    def test_j_tombs_never_truncate_through_a_load_fault(self):
        # r58 wave 2, reproduced: one transient load fault + any retire overwrote the
        # durable ledger with only the in-memory map
        (km.jd.STATE / "union-tombs.json").write_text(
            json.dumps({"777": time.time()}))
        km._union_tombs_loaded[0] = False
        os.chmod(km.jd.STATE / "union-tombs.json", 0)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                ok, _, reason, _u = km._union_ops_merge([dict(self.ROW, gid=920)], [],
                                                        ckey="ws:x")
            # r60 wave 2 tightened this contract: while the shield is UNFOLDED even
            # ACCEPTANCE refuses (the filter would run against an empty map and a
            # replay of a retired gesture would journal and be claimed)
            self.assertFalse(ok, "gesture writes refuse while the shield is unfolded")
            self.assertIn("shield", reason or "")
            with contextlib.redirect_stderr(io.StringIO()):
                ok, _, reason, _u = km._union_ops_merge([], [920], ckey="ws:x")
            self.assertFalse(ok, "r59 P1.3: a retirement whose SHIELD cannot persist is "
                                 "refused retryable, never acked")
        finally:
            os.chmod(km.jd.STATE / "union-tombs.json", 0o644)
        self.assertIn("777", json.loads((km.jd.STATE / "union-tombs.json").read_text()),
                      "the unfolded ledger was never overwritten")
        km._union_retired_tombs.clear()
        km._union_tombs_loaded[0] = False
        self.assertTrue(km._union_ops_set([dict(self.ROW, gid=921)]))
        with contextlib.redirect_stderr(io.StringIO()):
            ok, _, _, _u = km._union_ops_merge([], [921], ckey="ws:x")
        self.assertTrue(ok)
        d = json.loads((km.jd.STATE / "union-tombs.json").read_text())
        self.assertIn("777", d, "the healed load folds the old entries in…")
        self.assertIn("921", d, "…and the save keeps both")

    @unittest.skipIf(os.geteuid() == 0, "chmod 0 does not block reads for root")
    def test_k_nudge_fire_pauses_on_unproved(self):
        # r58 wave 2, reproduced: the write refusal met an unguarded FIRE side — the
        # dedupe record never persisted, so the same goal nudged EVERY tick, and the
        # fabricated enabled:True overrode an explicit OFF
        p = km.jd.STATE / "auto-nudge.json"
        p.write_text(json.dumps({"enabled": True, "nudged": {}}))
        self.assertTrue(km._auto_nudge_on())
        p.write_text("{not valid json")              # a NEW, unreadable generation
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(km._auto_nudge_on(),
                             "never ON by fabrication — nudging pauses while unproved")
            km._auto_nudge_tick(int(time.time()), {})   # returns without firing or raising

    def test_l_tombstones_expire_on_the_7d_backstop(self):
        # r58 wave 2: gids mint from a random 30-bit per-load seed — an everlasting shield
        # eventually swallows a legitimately-reused id
        (km.jd.STATE / "union-tombs.json").write_text(json.dumps(
            {"801": time.time() - 8 * 86400, "802": time.time()}))
        km._union_retired_tombs.clear()
        km._union_tombs_loaded[0] = False
        with km.jd._identity_file_lock():
            km._union_tombs_load_locked()
        self.assertNotIn(801, km._union_retired_tombs, "the stale shield expired")
        self.assertIn(802, km._union_retired_tombs, "the fresh one stands")




class R59AuditFixes(unittest.TestCase):
    """the v1.3.31 audit, kernel half (9 P1 / 13 P2 against 4fb688fe): coercive wire
    identities claimed/retired NEIGHBORS (P1.5), the suppression ledger failed open and
    erased siblings (P1.4), refusal rows short-circuited the finite checks (P2.3), and
    refusal correlation died past the lineage cap (P2.1)."""

    ROW = {"host": "TESTHOST-A", "gid": 7, "edit": {}, "inverse": {}, "rt": {},
           "name": "pool", "dispatched": False}

    def setUp(self):
        _scrub_state()
        km._union_claims.clear()
        km._union_retired_tombs.clear()
        km._union_tombs_loaded[0] = False
        km._retry_suppress_cache.clear()
        km._retry_suppress_pending.clear()
        for name in ("union-gestures.json", "union-gestures.json.unproved",
                     "union-tombs.json", "retry-suppressed.json"):
            try:
                (km.jd.STATE / name).unlink()
            except OSError:
                pass
        for f in km.jd.STATE.glob("*.corrupt-*"):
            try:
                f.unlink()
            except OSError:
                pass

    def tearDown(self):
        self.setUp()

    def test_a_float_identities_never_touch_neighbors(self):
        # r59 P1.5, reproduced there: retirement id 1.9 deleted gid 1, claim id 1.9
        # claimed gid 1 — int() coercion named a NEIGHBOR the sender never meant
        self.assertTrue(km._union_ops_set([dict(self.ROW, gid=1)]))
        self.assertIsNone(km._union_claim_grant(1.9, "cid:x"))
        ok, _, reason, _u = km._union_ops_merge([], [1.9], ckey="ws:x")
        self.assertFalse(ok, "a float retirement rejects the WHOLE write")
        self.assertIn("malformed", reason or "")
        self.assertEqual([r["gid"] for r in km._union_ops_load()], [1],
                         "gid 1 was never touched")

    def test_b_refusal_rows_validate_their_payloads(self):
        # r59 P2.3: the refusal branch short-circuited the finite checks — Infinity/NaN in
        # a refusal payload poisoned the whole browser frame
        self.assertTrue(km._union_row_valid(
            {"refusal": True, "gid": -3, "name": "pool", "t": 5}))
        self.assertFalse(km._union_row_valid(
            {"refusal": True, "gid": -3, "name": "pool", "why": {"x": float("inf")}}))
        self.assertFalse(km._union_row_valid(
            {"refusal": True, "gid": -3, "t": float("nan")}))

    @unittest.skipIf(os.geteuid() == 0, "chmod 0 does not block reads for root")
    def test_c_suppression_lands_in_memory_and_never_erases_siblings(self):
        # r59 P1.4, reproduced there: an EIO read {} cached under the real generation, and
        # arming s3 persisted ONLY s3 — the user-interrupted retry storms restarted
        p = km.jd.STATE / "retry-suppressed.json"
        km._suppress_session_retry("11111111-2222-3333-4444-555555555551")
        km._suppress_session_retry("11111111-2222-3333-4444-555555555552")
        before = json.loads(p.read_text())
        self.assertEqual(len(before), 2)
        os.chmod(p, 0)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                km._suppress_session_retry("11111111-2222-3333-4444-555555555553")
            self.assertTrue(km._session_retry_suppressed(
                "11111111-2222-3333-4444-555555555553"),
                "the interrupt's suppression LANDS (memory overlay) even under the fault")
        finally:
            os.chmod(p, 0o644)
        self.assertEqual(json.loads(p.read_text()), before,
                         "…and the fault never persisted a fabricated {} over the siblings")
        km._suppress_session_retry("11111111-2222-3333-4444-555555555554")
        after = json.loads(p.read_text())
        self.assertEqual(len(after), 4, "the healed RMW folds the pending overlay in")

    def test_d_rid_resolves_through_any_generation(self):
        # r59 P2.1: a refusal naming an intermediate generation past the lineage cap was
        # never compensated — the kernel resolves the stable ROOT id from its journal
        self.assertTrue(km._union_ops_set(
            [dict(self.ROW, gid=905, ogid=903, olin=[901, 902, 903], rid=901)]))
        for named in (901, 902, 903, 905):
            self.assertEqual(km._union_rid_for(named), 901,
                             "generation %d resolves to the root" % named)
        self.assertEqual(km._union_rid_for(999), 0, "an unknown id resolves to nothing")


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
                 "oldName": "pool", "oldColor": "", "post": {}, "confirmed": False,
                 "dispatched": False}]
        self.assertTrue(km._union_ops_set(rows))
        self.assertEqual(km._union_ops_load(), rows)
        # per-gid MERGE (the r49 verification): a second panel's sync must not clobber this
        # entry — omission is not retirement
        other = [{"host": "TESTHOST-B", "name": "crew", "gid": 9, "edit": {}, "inverse": {},
                  "rt": {}, "oldName": "crew", "oldColor": "", "post": {}, "confirmed": False,
                  "dispatched": False}]
        self.assertTrue(km._union_ops_set(other))
        got = km._union_ops_load()
        self.assertEqual(len(got), 2, "both panels' entries coexist — no last-writer-wins")
        self.assertTrue(km._union_ops_set([], retired=[3]))
        self.assertEqual([r["gid"] for r in km._union_ops_load()], [9],
                         "retirement is an EXPLICIT tombstone, and it lands")
        self.assertFalse(km._union_ops_set(["junk", {"host": "TESTHOST-B", "gid": 9}],
                                           retired=[9]),
                         "r59 P1.5: non-dict junk REJECTS the whole write — the silent "
                         "drop acked a journal missing a row its sender believed durable")
        self.assertTrue(km._union_ops_set(
            [{"host": "TESTHOST-B", "gid": 9, "dispatched": False,
              "edit": {}, "inverse": {}, "rt": {}}], retired=[9]))
        self.assertEqual(km._union_ops_load(), [],
                         "a retired gid wins over its own upsert")

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
        km._union_retired_tombs.clear()   # tests reuse small gids; production mints unique
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
                                    "confirmed": False, "dispatched": False}])
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
                        src.index("_union_store_read_locked("),
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
                            "edit": {}, "inverse": {}, "rt": {}, "name": "pool",
                            "dispatched": False}])
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
                 "name": "t%d" % i, "dispatched": False}
                for i in range(1, km._UNION_OPS_MAX + 1)]
        self.assertTrue(km._union_ops_set(rows))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertTrue(km._union_ops_set([{"host": "TESTHOST-B", "gid": 9999, "edit": {},
                                                "inverse": {}, "rt": {}, "name": "fresh",
                                                "dispatched": False}]))
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
        i = src.index('_uok, _unclaimed, _ureason, _unret = _union_ops_merge(')
        window = src[max(0, i - 200):i]
        self.assertIn("try:", window, "the merge call is guarded")
        after = src[i:i + 500]
        self.assertIn('_uok, _unclaimed, _ureason, _unret = False, [], "internal error", []', after,
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
                                            "inverse": {}, "rt": {}, "name": "pool",
                                            "dispatched": False}]))
        real = km._union_ops_path
        try:
            km._union_ops_path = lambda: self._BadPath(real())
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                ok = km._union_ops_set([{"host": "TESTHOST-B", "gid": 2, "edit": {},
                                         "inverse": {}, "rt": {}, "name": "crew",
                                         "dispatched": False}])
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
                                            "inverse": {}, "rt": {}, "name": "pool",
                                            "dispatched": False}]))
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
        # a re-keyed completion row still matches through its carried original id —
        # a FRESH gesture completed through the GATED transaction (r61 wave 2: the
        # plain successor+retired shape is the CAS's sibling door and refuses now; and
        # gesture 11 above carries a standing refusal, which rightly blocks completion)
        self.assertTrue(km._union_ops_set(
            [{"host": "TESTHOST-B", "gid": 21, "edit": {}, "inverse": {}, "rt": {},
              "name": "pool", "dispatched": False}]))
        _cep = km._union_claim_grant(21, "ws:completer")
        _sok, _, _srsn, _u5 = km._union_ops_merge(
            [{"host": "TESTHOST-B", "gid": 77, "ogid": 21, "olin": [21],
              "edit": {}, "inverse": {}, "rt": {}, "name": "pool", "dispatched": False}],
            [21], ckey="ws:completer", rekey={"ogid": 21, "gid": 77, "epoch": _cep})
        self.assertTrue(_sok, _srsn)
        self.assertTrue(km._union_ops_mark_dispatched("21", "TESTHOST-B"))
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
        km._union_retired_tombs.clear()
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
        ok, unclaimed, _, _u4 = km._union_ops_merge(rows21, ckey="ws:9")
        self.assertTrue(ok)
        self.assertEqual(unclaimed, [], "the writer claims its own gesture at journal time")
        self.assertIsNone(km._union_claim_grant(21, "ws:other"), "the writer holds 21")
        km._union_claims.clear()
        km._union_retired_tombs.clear()
        self.assertIsNotNone(km._union_claim_grant(21, "ws:completer"))
        ok, unclaimed, _, _u4 = km._union_ops_merge(rows21, ckey="ws:9")
        self.assertTrue(ok)
        self.assertEqual(unclaimed, [21],
                         "a completer-held gesture is named back to the writer")

    def test_the_rekey_write_is_a_cas_against_the_claim_epoch(self):
        # r55 P1.4, executed there: a bystander's claim outlived the owner's retirement and
        # the stale claimant re-keyed gid 7001 to 7002 — resurrecting the settled operation
        self._seed_rows(7001)
        epoch = km._union_claim_grant(7001, "ws:comp")
        self.assertIsNotNone(epoch)
        # a LIVE claim now blocks foreign retirement outright (r56 wave 2: a stale panel's
        # diff-derived retired list erased a gesture mid-completion) …
        self.assertTrue(km._union_ops_set([], [7001]))
        self.assertEqual([r["gid"] for r in km._union_ops_load()], [7001],
                         "…the rows SURVIVE a retirement over another client's claim")
        # …so the settled-resurrection scenario needs the claimant DEAD first: its socket
        # closes (claim released), the owner's retirement lands, and only then does the
        # stale epoch attempt its rekey
        km._union_claims_release("ws:comp")
        self.assertTrue(km._union_ops_set([], [7001]))
        ok, _, reason, _u4 = km._union_ops_merge(
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
        ok, _, reason, _u4 = km._union_ops_merge(
            [{"host": "TESTHOST-A", "gid": 7004, "ogid": 7003, "edit": {}, "inverse": {},
              "rt": {}, "name": "pool", "dispatched": False}],
            [7003], ckey="ws:comp", rekey={"ogid": 7003, "gid": 7004, "epoch": epoch})
        self.assertTrue(ok, reason)
        self.assertEqual([r["gid"] for r in km._union_ops_load()], [7004])
        # a WRONG epoch (someone re-claimed since) refuses too
        self.assertTrue(km._union_ops_set([], [7004]))
        self._seed_rows(7005)
        km._union_claim_grant(7005, "ws:comp")
        ok, _, reason, _u4 = km._union_ops_merge(
            [], [7005], ckey="ws:comp", rekey={"ogid": 7005, "gid": 7006, "epoch": 999999})
        self.assertFalse(ok)
        self.assertIn("stale", reason or "")
        # claims retire WITH their rows (r55 P3.19) — retired by the HOLDER itself, the one
        # writer the r56 claim-guard lets through
        self._seed_rows(7007)
        km._union_claim_grant(7007, "ws:x")
        ok, _, _, _u4 = km._union_ops_merge([], [7007], ckey="ws:x")
        self.assertTrue(ok)
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

    def test_tombstones_block_replay_resurrection(self):
        # the r55 wave-2 verification: claims retiring with their rows freed a completed
        # gesture's gid — a dead writer's reconnect replay re-inserted the retired rows as a
        # plain merge, was GRANTED the freed claim, and its gate ran the effects a completer
        # had already run. Retired gids tombstone now; a replay's rows (gid OR ogid) drop
        # and come back named unclaimed, so the replaying gate yields.
        self._seed_rows(801)
        epoch = km._union_claim_grant(801, "ws:completer")
        ok, _, _, _u4 = km._union_ops_merge(
            [{"host": "TESTHOST-A", "gid": 802, "ogid": 801, "edit": {}, "inverse": {},
              "rt": {}, "name": "pool", "dispatched": True}],
            [801], ckey="ws:completer", rekey={"ogid": 801, "gid": 802, "epoch": epoch})
        self.assertTrue(ok)
        # the dead writer reconnects and replays its pre-ack rows (plain merge, gid 801)
        ok, unclaimed, _, _u4 = km._union_ops_merge(
            [{"host": "TESTHOST-A", "gid": 801, "edit": {}, "inverse": {}, "rt": {},
              "name": "pool", "dispatched": False}], ckey="ws:writer")
        self.assertTrue(ok)
        self.assertIn(801, unclaimed, "the tombstone names the gid back — the gate yields")
        self.assertNotIn(801, [r["gid"] for r in km._union_ops_load()],
                         "…and the retired rows never re-enter the journal")
        # a replayed COMPLETION gate (its rows carry the tombstoned ogid) yields too
        ok, unclaimed, _, _u4 = km._union_ops_merge(
            [{"host": "TESTHOST-A", "gid": 803, "ogid": 801, "edit": {}, "inverse": {},
              "rt": {}, "name": "pool", "dispatched": False}], ckey="ws:writer")
        self.assertTrue(ok)
        self.assertIn(803, unclaimed, "…through ogid as well — no CAS bypass by re-key")
        self.assertNotIn(803, [r["gid"] for r in km._union_ops_load()])

    def test_rmw_writers_quarantine_wrong_shape_bytes(self):
        # the r55 wave-2 verification: the readers marked []-shaped stores unproved, but the
        # WRITERS folded them to {} and overwrote — the garbage bytes were gone forever
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._views_path().write_text("[]")
        km._flags_cache.clear()
        with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
            km._apply_views_ops([{"active": "all"}])
        q = list(km.jd.STATE.glob("timeline-views.json.corrupt-*"))
        self.assertEqual(len(q), 1, "the targeted-ops writer quarantines first")
        self.assertEqual(q[0].read_text(), "[]", "…bytes preserved")
        q[0].unlink()

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


class R56AuditFixes(unittest.TestCase):
    """the v1.3.28 audit: tombstones dropped resident successors (P1.1), quarantine acted on
    a pathname a concurrent writer had replaced (P1.4), union rows accepted schema poison
    (P1.5), one flags EIO erased safety state (P1.6), lineage lost the immediate predecessor
    (P1.3), and an order-file EIO overwrote the saved order (P2.12)."""

    SID = "11111111-2222-3333-4444-555555555555"

    def setUp(self):
        _scrub_state()
        km._union_claims.clear()
        km._union_retired_tombs.clear()
        km._union_tombs_loaded[0] = False
        for name in ("union-gestures.json", "union-gestures.json.unproved",
                     "union-tombs.json", "session-order.json"):
            try:
                (km.jd.STATE / name).unlink()
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

    def _seed(self, *gids):
        self.assertTrue(km._union_ops_set(
            [{"host": "TESTHOST-A", "gid": g, "edit": {}, "inverse": {}, "rt": {},
              "name": "pool", "dispatched": False} for g in gids]))

    def test_a_resident_successor_survives_its_own_flip(self):
        # r56 P1.1, executed there: the committed successor's dispatched:true re-post
        # carries the tombstoned ogid — the filter dropped it, DELETED the healthy
        # completion's compensation journal, and answered unclaimed over it
        self._seed(801)
        epoch = km._union_claim_grant(801, "ws:c")
        ok, _, _, _u4 = km._union_ops_merge(
            [{"host": "TESTHOST-A", "gid": 802, "ogid": 801, "olin": [801], "edit": {},
              "inverse": {}, "rt": {}, "name": "pool", "dispatched": False}],
            [801], ckey="ws:c", rekey={"ogid": 801, "gid": 802, "epoch": epoch})
        self.assertTrue(ok)
        ok, unclaimed, _, _u4 = km._union_ops_merge(
            [{"host": "TESTHOST-A", "gid": 802, "ogid": 801, "olin": [801], "edit": {},
              "inverse": {}, "rt": {}, "name": "pool", "dispatched": True}], ckey="ws:c")
        self.assertTrue(ok)
        self.assertNotIn(802, unclaimed, "the resident gesture's own update never yields")
        rows = km._union_ops_load()
        self.assertEqual([(r["gid"], r["dispatched"]) for r in rows], [(802, True)],
                         "…and the compensation journal SURVIVES the healthy flip")
        # a REPLAY of the retired original (not resident) still tombstone-drops
        ok, unclaimed, _, _u4 = km._union_ops_merge(
            [{"host": "TESTHOST-A", "gid": 801, "edit": {}, "inverse": {}, "rt": {},
              "name": "pool", "dispatched": False}], ckey="ws:w")
        self.assertTrue(ok)
        self.assertIn(801, unclaimed)

    def test_quarantine_never_touches_a_replaced_file(self):
        # r56 P1.4, executed there: an unlocked observer judged malformed bytes, a concurrent
        # writer committed a VALID journal, and the pathname-keyed quarantine unlinked it
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        p = km.jd.STATE / "union-gestures.json"
        p.write_text("{malformed")
        st = p.stat()
        stale_fp = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        km._atomic_write(p, json.dumps([]))          # the concurrent VALID commit
        with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
            km._quarantine_state_bytes(p, "union-gestures", fingerprint=stale_fp)
        self.assertTrue(p.exists(), "the valid replacement SURVIVES the stale judgment")
        self.assertEqual(p.read_text(), "[]")
        self.assertEqual(list(km.jd.STATE.glob("union-gestures.json.corrupt-*")), [])

    def test_schema_poison_is_rejected_at_the_gate(self):
        # r56 P1.5, executed there: gid:[] wedged every later merge with a TypeError;
        # gid:1e309 persisted as Infinity and the browser refused the whole timeline frame
        ok, _, reason, _u4 = km._union_ops_merge(
            [{"host": "TESTHOST-A", "gid": [], "edit": {}, "inverse": {}, "rt": {},
              "name": "pool", "dispatched": False}])
        self.assertFalse(ok)
        self.assertIn("malformed", reason or "")
        ok, _, _, _u4 = km._union_ops_merge(
            [{"host": "TESTHOST-A", "gid": 1e309, "edit": {}, "inverse": {}, "rt": {},
              "name": "pool", "dispatched": False}])
        self.assertFalse(ok, "non-finite ids never persist")
        self.assertEqual(km._union_ops_load(), [])
        # legacy poison already ON DISK is SALVAGED at judgment (r57 P1.3 + wave 2: the r56
        # silent filter let an acked merge retire the poisoned gesture; the wave-1
        # refuse-and-quarantine-whole left the store ENOENT, which the NEXT echo read as
        # authoritative-empty and panels retired every VALID recovery copy — reproduced.
        # The salvage moves the original bytes aside whole and re-mints the valid rows.)
        _good = ('{"host": "TESTHOST-B", "gid": 7, "edit": {}, "inverse": {}, "rt": {}, '
                 '"name": "pool", "dispatched": false}')
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._union_ops_path().write_text(
            '[%s, {"host": "TESTHOST-A", "gid": Infinity}]' % _good)
        with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
            self.assertIsNone(km._union_ops_echo(),
                              "the poison judged: the salvaged base is UNPROVED (r59 "
                              "P1.2) — no partial silent filter, no all-or-nothing wipe, "
                              "and no partial view either")
        q = list(km.jd.STATE.glob("union-gestures.json.corrupt-*"))
        self.assertEqual(len(q), 1, "…the original bytes quarantined aside whole")
        self.assertIn("Infinity", q[0].read_text())
        q[0].unlink()
        self.assertEqual([r["gid"] for r in json.loads(km._union_ops_path().read_text())],
                         [7], "the valid rows stand re-minted as the base")
        # a MERGE carrying live-panel rows reconstructs OVER the base and proves it
        with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
            ok, _, reason, _u4 = km._union_ops_merge(
                [{"host": "TESTHOST-B", "gid": 5, "edit": {}, "inverse": {}, "rt": {},
                  "name": "pool", "dispatched": False}])
        self.assertTrue(ok, reason)
        self.assertEqual(sorted(r["gid"] for r in km._union_ops_load()), [5, 7],
                         "the base SURVIVES reconstruction (r59 P1.2: rebuilding from [] "
                         "dropped every group whose owner had not yet synced)")
        self.assertEqual(sorted(r["gid"] for r in km._union_ops_echo()), [5, 7])
        # mark_dispatched still refuses over an unproved store
        km._union_ops_path().write_text(
            '[%s, {"host": "TESTHOST-A", "gid": Infinity}]' % _good)
        with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
            self.assertFalse(km._union_ops_mark_dispatched(7, "TESTHOST-B"),
                             "no flip while the store is held (the client-side flip "
                             "remains the writer)")
        km._union_unproved_marker().unlink(missing_ok=True)
        for q in km.jd.STATE.glob("union-gestures.json.corrupt-*"):
            q.unlink()

    def test_mark_dispatched_matches_through_the_lineage(self):
        # r56 P1.3 (kernel half): dispatch evidence for a twice-completed gesture must match
        # the middle ancestor, not only the oldest
        self.assertTrue(km._union_ops_set(
            [{"host": "TESTHOST-A", "gid": 903, "ogid": 901, "olin": [901, 902],
              "edit": {}, "inverse": {}, "rt": {}, "name": "pool", "dispatched": False}]))
        self.assertTrue(km._union_ops_mark_dispatched("902", "TESTHOST-A"))
        self.assertEqual([r["dispatched"] for r in km._union_ops_load()], [True])

    def test_a_flags_read_fault_never_erases_safety_state(self):
        # r56 P1.6, executed there: one EIO folded the store to {} and the next toggle
        # persisted only the toggled session — every isolation and mute row deleted
        km._set_session_flag(self.SID, "postalServiceOff", True)
        p = km.jd.STATE / "session-flags.json"
        self.assertTrue(p.exists())
        with mock.patch.object(km, "_read_state_json",
                               side_effect=km._StateUnreadable(5, "EIO")):
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                with self.assertRaises(km._StateUnreadable):
                    km._set_session_flag("99999999-8888-7777-6666-555555555555",
                                         "hideFromFeed", True)
        #  ^ the refusal RAISES the held-queue type (r56 wave 2: a swallowed False made the
        #    SPOOL count the refused toggle as applied and delete the op)
        flags = km._session_flags_proved()
        self.assertIn(self.SID, flags, "…and the standing isolation row SURVIVES")

    def test_an_order_read_fault_never_overwrites_the_saved_order(self):
        # r56 P2.12, executed there: an EIO folded the order to [] and healing persisted
        # DISCOVERY order over the user's saved one
        km._write_session_order([self.SID])
        sessions = [{"sid": "99999999-8888-7777-6666-555555555555", "name": "api"},
                    {"sid": self.SID, "name": "web"}]
        with mock.patch.object(km, "_read_state_json",
                               side_effect=km._StateUnreadable(5, "EIO")):
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                out = km._ordered_locked(list(sessions))
        self.assertEqual([s["sid"] for s in out], [s["sid"] for s in sessions],
                         "input-ordered render this pass")
        self.assertEqual(km._session_order(), [self.SID], "…and the saved order is UNTOUCHED")


class R60AuditFixes(unittest.TestCase):
    """the v1.3.32 audit, kernel half (9 P1 / 5 P2 against c8607b6f): the rekey CAS never
    required its successor rows (P1.1), the tomb/journal transaction was not failure-atomic
    (P1.2), a judged-bad tomb ledger failed OPEN (P1.3), the row validator accepted missing
    dispatched and string booleans (P1.4), the suppression overlay vanished on cache-hit and
    ENOENT arms (P2.3), and the portable quarantine restore could clobber a newer commit
    (P2.5)."""

    ROW = {"host": "TESTHOST-A", "gid": 801, "edit": {}, "inverse": {}, "rt": {},
           "name": "pool", "dispatched": False}
    SUCC = {"host": "TESTHOST-A", "gid": 802, "ogid": 801, "olin": [801], "rid": 801,
            "edit": {}, "inverse": {}, "rt": {}, "name": "pool", "dispatched": False}

    def setUp(self):
        _scrub_state()
        km._union_claims.clear()
        km._union_retired_tombs.clear()
        km._union_tombs_loaded[0] = False
        km._retry_suppress_cache.clear()
        km._retry_suppress_pending.clear()
        for name in ("union-gestures.json", "union-gestures.json.unproved",
                     "union-tombs.json", "union-tombs.json.hold", "retry-suppressed.json"):
            try:
                (km.jd.STATE / name).unlink()
            except OSError:
                pass
        for f in list(km.jd.STATE.glob("*.corrupt-*")) + list(km.jd.STATE.glob("pq-*")):
            try:
                f.unlink()
            except OSError:
                pass

    def tearDown(self):
        self.setUp()

    def _fold_tombs(self):
        with km.jd._identity_file_lock():
            km._union_tombs_load_locked()

    def test_a_completion_with_no_successor_rows_is_refused(self):
        # r60 P1.1, the audit's exact executed shape: a queued Electron completion whose
        # gesture a refusal consumed rebuilt its body at the send head — entries=[] with
        # the STALE rekey — and the kernel accepted it, retiring the original against
        # nothing while the acked gate ran the rolled-back effects
        self.assertTrue(km._union_ops_set([dict(self.ROW)]))
        ep = km._union_claim_grant(801, "ws:x")
        self.assertIsNotNone(ep)
        ok, _, reason, _u = km._union_ops_merge(
            [], [801], ckey="ws:x", rekey={"ogid": 801, "gid": 802, "epoch": ep})
        self.assertFalse(ok, "an empty-bodied completion is refused")
        self.assertIn("successor", reason or "")
        self.assertEqual([r["gid"] for r in km._union_ops_load()], [801],
                         "the original row stands — nothing was retired against nothing")
        ok, _, _, _u = km._union_ops_merge(
            [dict(self.SUCC)], [801], ckey="ws:x",
            rekey={"ogid": 801, "gid": 802, "epoch": ep})
        self.assertTrue(ok, "the same completion CARRYING its successor commits")
        self.assertEqual([r["gid"] for r in km._union_ops_load()], [802])

    def test_b_rekey_ids_are_strict_never_coerced(self):
        # r60 P1.1: int() accepted fractional ids — a rekey naming ogid 801.9 cleared the
        # CAS against gid 801, a neighbor the panel never named
        self.assertTrue(km._union_ops_set([dict(self.ROW)]))
        ep = km._union_claim_grant(801, "ws:x")
        for bad in ({"ogid": 801.9, "gid": 802, "epoch": ep},
                    {"ogid": 801, "gid": 802.5, "epoch": ep},
                    {"ogid": 801, "gid": 802, "epoch": float(ep)},
                    {"ogid": 801, "gid": 0, "epoch": ep},
                    {"ogid": 801, "gid": True, "epoch": ep}):
            ok, _, reason, _u = km._union_ops_merge(
                [dict(self.SUCC)], [801], ckey="ws:x", rekey=bad)
            self.assertFalse(ok, "coerced rekey ids are refused: %r" % (bad,))
            self.assertIn("malformed", reason or "")
        self.assertEqual([r["gid"] for r in km._union_ops_load()], [801])

    def test_c_half_committed_retirement_retry_keeps_the_successor(self):
        # r60 P1.2(a), reproduced by the audit: tomb saved, journal write failed, restart —
        # the retry's successor hit its ogid's standing tomb, was DROPPED, and the merge
        # acked an EMPTY journal over the live gesture. A row whose ancestor still stands
        # in cur is a live completion of an UNRETIRED gesture, never a replay.
        self.assertTrue(km._union_ops_set([dict(self.ROW)]))
        self._fold_tombs()
        with km._union_claims_lock:
            km._union_retired_tombs[801] = __import__("time").time()
            km._union_tombs_save_locked()
        # the retry rides a FRESH rekey completion (r61 wave 2: the plain form was the
        # CAS's sibling door — the twin re-claims and re-keys after any failed commit)
        _rep = km._union_claim_grant(801, "ws:retry")
        ok, _, _, _u = km._union_ops_merge(
            [dict(self.SUCC)], [801], ckey="ws:retry",
            rekey={"ogid": 801, "gid": 802, "epoch": _rep})
        self.assertTrue(ok)
        self.assertEqual([r["gid"] for r in km._union_ops_load()], [802],
                         "the retry lands the successor and retires the original")
        # …while a replay of a FULLY-retired gesture (rows gone) still drops
        ok, _, _, _u = km._union_ops_merge([dict(self.ROW)], [], ckey="ws:ghost")
        self.assertTrue(ok)
        self.assertNotIn(801, [r["gid"] for r in km._union_ops_load()],
                         "a tombstoned replay with no resident ancestor stays dead")

    def test_d_tomb_save_failure_restores_the_evicted_shields(self):
        # r60 P1.2(b), reproduced by the audit: an ENOSPC save at the cap evicted an older
        # shield from MEMORY even though nothing persisted — a same-process replay of that
        # retired gesture was accepted
        self._fold_tombs()
        now = __import__("time").time()
        with km._union_claims_lock:
            for g in (11, 12, 13):
                km._union_retired_tombs[g] = now
            self.assertTrue(km._union_tombs_save_locked())
        real_max = km._UNION_TOMBS_MAX
        km._UNION_TOMBS_MAX = 3
        real_write = km._atomic_write
        def failing_write(path, data):
            if "union-tombs" in str(path):
                raise OSError(28, "No space left on device")
            return real_write(path, data)
        try:
            km._atomic_write = failing_write
            with contextlib.redirect_stderr(io.StringIO()):
                ok, _, reason, _u = km._union_ops_merge([], [99], ckey="ws:x")
        finally:
            km._atomic_write = real_write
            km._UNION_TOMBS_MAX = real_max
        self.assertFalse(ok)
        self.assertIn("tombstone", reason or "")
        self.assertEqual(sorted(km._union_retired_tombs), [11, 12, 13],
                         "the failed transaction left the shield map EXACTLY as it was — "
                         "no eviction spent, no unpersisted mint standing")

    def test_e_judged_bad_tombs_ledger_holds_then_expires(self):
        # r60 P1.3, reproduced by the audit: judged-bad bytes moved aside and the shield
        # restarted EMPTY — a stale replay of a retired completion was accepted and
        # claimable again. Now: a durable hold for the shield's own 7d horizon (the r59
        # permanent wedge is gone — the hold retires itself when every tomb it could have
        # protected has expired anyway).
        (km.jd.STATE / "union-tombs.json").write_text("{corrupt")
        with contextlib.redirect_stderr(io.StringIO()):
            ok, _, reason, _u = km._union_ops_merge([], [55], ckey="ws:x")
        self.assertFalse(ok, "a retirement under the judged-bad shield refuses retryable")
        self.assertIn("shield", reason or "")
        self.assertTrue((km.jd.STATE / "union-tombs.json.hold").exists(),
                        "the hold marker is durable")
        self.assertFalse((km.jd.STATE / "union-tombs.json").exists(),
                         "the judged bytes moved aside for forensics")
        # the hold gates ACCEPTANCE too (r60 wave 2, reproduced: during the hold a dead
        # writer's replay of a retired gesture journaled, took the freed claim, and its
        # gate re-ran the retired effects — the filter ran against an EMPTY map)
        with contextlib.redirect_stderr(io.StringIO()):
            ok, _, reason, _u = km._union_ops_merge([dict(self.ROW)], [], ckey="ws:x")
        self.assertFalse(ok, "gesture rows are refused while the shield is unavailable")
        self.assertIn("shield", reason or "")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(km._union_claim_grant(801, "ws:dead2"),
                              "no shield, no claim (r60 wave 2)")
        # refusal-only journaling (compensation records, no effects) still flows
        with contextlib.redirect_stderr(io.StringIO()):
            ok, _, _, _u = km._union_ops_merge(
                [{"refusal": True, "gid": -9, "name": "pool", "t": 5}], [], ckey="ws:x")
        self.assertTrue(ok, "refusal-only journaling is not hostage to the hold")
        # the horizon passes: the hold retires itself and the shield restarts empty
        (km.jd.STATE / "union-tombs.json.hold").write_text(
            "%d" % int(__import__("time").time() - 8 * 86400))
        km._union_tombs_loaded[0] = False
        with contextlib.redirect_stderr(io.StringIO()):
            ok, _, _, _u = km._union_ops_merge([], [55], ckey="ws:x")
        self.assertTrue(ok, "after 7d every held shield has expired — retirement resumes")
        self.assertFalse((km.jd.STATE / "union-tombs.json.hold").exists())

    def test_f_a_fifo_at_the_tombs_path_never_blocks_the_lock(self):
        # r60 P2.13: a FIFO planted at the ledger path blocked the read while the identity
        # lock was held — every union write on the kernel stalled behind it
        os.mkfifo(km.jd.STATE / "union-tombs.json")
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                ok, _, reason, _u = km._union_ops_merge([], [66], ckey="ws:x")
            self.assertFalse(ok, "the non-regular ledger is judged, not read")
            self.assertTrue((km.jd.STATE / "union-tombs.json.hold").exists())
        finally:
            try:
                (km.jd.STATE / "union-tombs.json").unlink()
            except OSError:
                pass

    def test_g_the_row_schema_is_exact(self):
        # r60 P1.4, reproduced by the audit: executed rows missing dispatched, carrying
        # confirmed:"false" / lapplied:"false", and malformed operation fields were
        # accepted and persisted — the UI reads those differently from literal booleans
        good = dict(self.ROW)
        self.assertTrue(km._union_row_valid(good))
        self.assertFalse(km._union_row_valid({k: v for k, v in good.items()
                                              if k != "dispatched"}),
                         "dispatched is REQUIRED on a normal row")
        self.assertFalse(km._union_row_valid({k: v for k, v in good.items()
                                              if k != "host"}),
                         "host is REQUIRED on a normal row")
        self.assertFalse(km._union_row_valid(dict(good, confirmed="false")))
        self.assertFalse(km._union_row_valid(dict(good, lapplied="false")))
        self.assertTrue(km._union_row_valid(dict(good, confirmed=True, lapplied=False)))
        self.assertFalse(km._union_row_valid(dict(good, edit="rename")),
                         "operation fields are objects, never strings")
        self.assertFalse(km._union_row_valid(dict(good, post={"x": float("inf")})),
                         "the WHOLE row is finite-checked, post included")
        self.assertTrue(km._union_row_valid(
            {"refusal": True, "gid": -3, "name": "pool", "t": 5, "error": "x"}))
        self.assertFalse(km._union_row_valid(
            {"refusal": True, "gid": -3, "t": "yesterday"}),
            "a refusal's t is a number — it feeds the 7d staleness compare")
        self.assertFalse(km._union_row_valid(
            {"refusal": True, "gid": -3, "error": ["x"]}))

    def test_h_suppression_overlay_rides_every_return_arm(self):
        # r60 P2.3, reproduced by the audit: a pending suppression minted during EIO was
        # OMITTED once a durable cache hit existed — the healthy-session rearm never saw
        # it and could never clear it. ENOENT had the same gap.
        p = km.jd.STATE / "retry-suppressed.json"
        sid = "66666666-7777-8888-9999-000000000001"
        km._retry_suppress_pending[sid] = 1.0
        self.assertIn(sid, km._retry_suppress_data(), "ENOENT arm merges the overlay")
        p.write_text("{}")
        d1 = km._retry_suppress_data()               # fresh read, primes the cache
        self.assertIn(sid, d1)
        d2 = km._retry_suppress_data()               # cache-hit arm
        self.assertIn(sid, d2, "the cache-hit arm merges the overlay too")
        st = p.stat()
        self.assertEqual(km._retry_suppress_cache[str(p)][1], {},
                         "…without mutating the cached copy")

    def test_i_the_carried_rid_survives_any_lineage_depth(self):
        # r60 P2.1: with the named generation aged past the 32-entry retained lineage,
        # _union_rid_for answers 0 — the DISPATCH-carried rid correlates the refusal
        # anyway, and rides the journaled refusal row for reloading panels
        sent = []
        real_fwd, real_send = km._forward_tag_edit, km._send_to_view
        real_journal = km._journal_refusal
        journaled = []
        km._forward_tag_edit = lambda host, body: (None, "owner down")
        km._send_to_view = lambda view, m, wid="": sent.append(m)
        km._journal_refusal = lambda row: journaled.append(row)
        try:
            km.Handler._dispatch_ws(None, {"type": "editTag",
                                           "edit": {"host": "TESTHOST-B", "name": "pool",
                                                    "opId": "101", "rid": "100",
                                                    "add": []}},
                                    {"send": lambda s: None, "wid": ""})
        finally:
            km._forward_tag_edit, km._send_to_view = real_fwd, real_send
            km._journal_refusal = real_journal
        fails = [m for m in sent if m.get("type") == "tagEditFailed"]
        self.assertEqual(len(fails), 1)
        self.assertEqual(fails[0].get("rid"), 100,
                         "the refusal carries the dispatch-stamped root id even though "
                         "the journal holds NO row for generation 101")
        self.assertEqual(journaled[0].get("rid"), 100,
                         "…and the reload-surviving journal twin carries it too")

    def test_j_portable_restore_never_clobbers_a_newer_commit(self):
        # r60 P2.5, executed by the audit: on a no-hardlink filesystem the innocent-grab
        # restore fell back to os.replace and put the OLDER grabbed inode back over a
        # THIRD writer's newer commit (A -> B -> C ended with B replacing C)
        p = km.jd.STATE / "pq-restore.json"
        p.write_text("JUDGED")
        real_rename = km.Path.rename
        real_replace = os.replace
        def fake_rename(self_p, target):
            if str(self_p) == str(p):
                tmp = p.with_name("pq-w2.tmp")
                tmp.write_text("WRITER2")
                real_replace(str(tmp), str(p))       # writer 2 lands before the grab
                real_rename(self_p, target)          # the grab takes WRITER2's inode
                tmp3 = p.with_name("pq-w3.tmp")
                tmp3.write_text("WRITER3")
                real_replace(str(tmp3), str(p))      # writer 3 lands after the grab
                return
            return real_rename(self_p, target)
        replaces = []
        def forbidden_replace(a, b):
            replaces.append((str(a), str(b)))
            raise AssertionError("pathname replace after a failed exclusive restore")
        with mock.patch.object(km.Path, "rename", fake_rename), \
             mock.patch.object(km.os, "link", side_effect=OSError("no links")), \
             mock.patch.object(km.os, "replace", side_effect=forbidden_replace), \
             contextlib.redirect_stderr(io.StringIO()):
            km._quarantine_state_bytes(p, "pq-restore")
        self.assertEqual(p.read_text(), "WRITER3", "the newest commit stands at the path")
        self.assertEqual(replaces, [], "no pathname replace ever ran")
        self.assertEqual([f for f in km.jd.STATE.glob("pq-restore.json.corrupt-*")], [],
                         "the older grab is DISCARDED when a newer commit stands "
                         "(the hardlink branch's own rule)")

    def test_j2_portable_restore_refills_an_emptied_path(self):
        # r60 wave 2: with NO third writer, the wave-1 shelving left the path ABSENT —
        # and absence reads as proved-empty downstream. The exclusive COPY restores the
        # innocently-grabbed commit instead.
        p = km.jd.STATE / "pq-restore.json"
        p.write_text("JUDGED")
        real_rename = km.Path.rename
        real_replace = os.replace
        def fake_rename(self_p, target):
            if str(self_p) == str(p):
                tmp = p.with_name("pq-w2.tmp")
                tmp.write_text("WRITER2")
                real_replace(str(tmp), str(p))       # writer 2 lands before the grab
                real_rename(self_p, target)          # the grab takes WRITER2's inode
                return                               # NO third writer this time
            return real_rename(self_p, target)
        with mock.patch.object(km.Path, "rename", fake_rename), \
             mock.patch.object(km.os, "link", side_effect=OSError("no links")), \
             contextlib.redirect_stderr(io.StringIO()):
            km._quarantine_state_bytes(p, "pq-restore")
        self.assertEqual(p.read_text(), "WRITER2",
                         "the grabbed commit is restored by exclusive copy — the path "
                         "never reads as an authoritative absence")
        self.assertEqual([f for f in km.jd.STATE.glob("pq-restore.json.corrupt-*")], [],
                         "the restored grab does not linger aside")

    def test_k_two_origins_same_mid_wear_two_cards(self):
        # r60 P1.5 (kernel leg), reproduced by the audit: two origins holding the same mid
        # shared one card id — a Clear dismissed both and the decision named only the mid
        qdir = km.jd.STATE / "postal" / "quarantine"
        qdir.mkdir(parents=True, exist_ok=True)
        try:
            (qdir / "a.json").write_text(json.dumps(
                {"mid": "m1", "to": "web", "toId": SID, "frm": "api", "origin": "hostA",
                 "body": "hello from A", "at": 5}))
            (qdir / "b.json").write_text(json.dumps(
                {"mid": "m1", "to": "web", "toId": SID, "frm": "api", "origin": "hostB",
                 "body": "hello from B", "at": 6}))
            cards = km._quarantine_cards(10, set())
            self.assertEqual(len(cards), 2)
            ids = {c["itemId"] for c in cards}
            self.assertEqual(len(ids), 2, "each origin's hold wears its OWN card id")
            self.assertEqual({c["blocked"]["originId"] for c in cards},
                             {"hostA", "hostB"},
                             "the raw origin rides the card for the decision body")
            self.assertEqual(km._quarantine_cards(10, {"quarantine:m1"}), [],
                             "a legacy bare-mid dismissal still hides both")
        finally:
            import shutil
            shutil.rmtree(km.jd.STATE / "postal", ignore_errors=True)


class R60Wave2(unittest.TestCase):
    """the r60 verify round's confirmed second-order defects, kernel half: the hold gated
    saves but not ACCEPTANCE (the replay journaled and was claimed during the hold), a
    completion retiring its own successor emptied the lineage under ok:true, expiry
    re-minted a fresh 7d hold over week-old garbage, symlinks landed in the transient arm
    as a silent forever-hold, the refusal writer minted rows the exact schema rejects (and
    v1.3.32 journals hold such rows), and a tombed successor's replay rode a colliding
    resident ogid back in."""

    ROW = {"host": "TESTHOST-A", "gid": 801, "edit": {}, "inverse": {}, "rt": {},
           "name": "pool", "dispatched": False}

    def setUp(self):
        _scrub_state()
        km._union_claims.clear()
        km._union_retired_tombs.clear()
        km._union_tombs_loaded[0] = False
        with km._pending_refusal_lock:
            km._pending_refusal_rows[:] = []
        for name in ("union-gestures.json", "union-gestures.json.unproved",
                     "union-tombs.json", "union-tombs.json.hold"):
            try:
                (km.jd.STATE / name).unlink()
            except OSError:
                pass
        for f in km.jd.STATE.glob("*.corrupt-*"):
            try:
                f.unlink()
            except OSError:
                pass

    def tearDown(self):
        self.setUp()

    def test_a_replay_is_not_accepted_while_the_shield_is_unavailable(self):
        # the verify round's P1, reproduced end-to-end there: gesture retired, ledger
        # corrupted, restart — the replay was journaled, granted the freed claim, and its
        # gate re-ran the retired effects while retirements sat refused for 7 days
        self.assertTrue(km._union_ops_set([dict(self.ROW)]))
        with km.jd._identity_file_lock():
            km._union_tombs_load_locked()
        ok, _, _, _u = km._union_ops_merge([], [801], ckey="ws:writer")
        self.assertTrue(ok, "the retirement lands while the shield is healthy")
        # the restart under corruption
        km._union_retired_tombs.clear()
        km._union_tombs_loaded[0] = False
        (km.jd.STATE / "union-tombs.json").write_text("{corrupt")
        with contextlib.redirect_stderr(io.StringIO()):
            ok, _, reason, _u = km._union_ops_merge([dict(self.ROW)], [], ckey="ws:dead")
        self.assertFalse(ok, "the replay is REFUSED, not accepted, during the hold")
        self.assertIn("shield", reason or "")
        self.assertEqual(km._union_ops_load(), [], "nothing was journaled")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(km._union_claim_grant(801, "ws:dead"),
                              "…and nothing is claimable")

    def test_b_a_completion_may_not_retire_its_own_successor(self):
        self.assertTrue(km._union_ops_set([dict(self.ROW, gid=7)]))
        ep = km._union_claim_grant(7, "ws:x")
        succ = dict(self.ROW, gid=9, ogid=7, olin=[7])
        ok, _, reason, _u = km._union_ops_merge(
            [succ], [7, 9], ckey="ws:x", rekey={"ogid": 7, "gid": 9, "epoch": ep})
        self.assertFalse(ok, "retiring the successor in the same write emptied the "
                             "lineage under ok:true (r60 wave 2)")
        self.assertIn("successor", reason or "")
        self.assertEqual([r["gid"] for r in km._union_ops_load()], [7],
                         "the original stands; nothing was tombstoned")
        self.assertNotIn(9, km._union_retired_tombs)

    def test_c_expiry_never_remints_a_hold_over_the_same_bytes(self):
        # the verify round: a crash between the marker mint and the move-aside left the
        # judged bytes standing at expiry — re-judging them re-minted a FRESH 7d hold,
        # doubling the outage past the horizon every shield had already expired at
        (km.jd.STATE / "union-tombs.json").write_text("{corrupt")
        (km.jd.STATE / "union-tombs.json.hold").write_text(
            "%d" % int(time.time() - 8 * 86400))
        with contextlib.redirect_stderr(io.StringIO()):
            ok, _, _, _u = km._union_ops_merge([], [55], ckey="ws:x")
        self.assertTrue(ok, "the horizon passed — retirement resumes immediately")
        self.assertFalse((km.jd.STATE / "union-tombs.json.hold").exists(),
                         "no fresh hold was minted over week-old garbage")
        self.assertTrue(any(km.jd.STATE.glob("union-tombs.json.corrupt-*")),
                        "the bytes still moved aside for forensics")

    def test_d_a_symlink_at_the_tombs_path_is_judged_not_transient(self):
        # the verify round: ELOOP landed in the transient arm — a silent PERMANENT hold
        # with no self-expiry and no forensic copy
        (km.jd.STATE / "union-tombs.json").symlink_to("/nonexistent-target")
        with contextlib.redirect_stderr(io.StringIO()):
            ok, _, _, _u = km._union_ops_merge([], [66], ckey="ws:x")
        self.assertFalse(ok)
        self.assertTrue((km.jd.STATE / "union-tombs.json.hold").exists(),
                        "the symlink is JUDGED: the durable, self-expiring hold arms")

    def test_e_the_refusal_writer_is_sanitized_end_to_end(self):
        # the verify round: a peer's /tag reply rides into `error` verbatim — an object
        # failed the exact schema and the compensation record never landed
        sent, journaled = [], []
        real_fwd, real_send = km._forward_tag_edit, km._send_to_view
        km._forward_tag_edit = lambda host, body: ({"ok": False,
                                                    "error": {"code": 5}}, None)
        km._send_to_view = lambda view, m, wid="": sent.append(m)
        try:
            km.Handler._dispatch_ws(None, {"type": "editTag",
                                           "edit": {"host": "TESTHOST-B", "name": "pool",
                                                    "opId": "301", "add": []}},
                                    {"send": lambda s: None, "wid": ""})
        finally:
            km._forward_tag_edit, km._send_to_view = real_fwd, real_send
        rows = [r for r in km._union_ops_load() if r.get("refusal")]
        self.assertEqual(len(rows), 1, "the refusal LANDED despite the object error")
        self.assertIsInstance(rows[0].get("error"), str)

    def test_f_an_invalid_queued_refusal_never_wedges_the_fifo(self):
        with km._pending_refusal_lock:
            km._pending_refusal_rows[:] = [
                {"refusal": True, "gid": -5, "error": {"bad": True}, "opId": "5"},
                {"refusal": True, "gid": -6, "name": "pool", "t": 5}]
        real_dirty = km._mark_views_dirty
        km._mark_views_dirty = lambda: None
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                km._flush_pending_refusals()
        finally:
            km._mark_views_dirty = real_dirty
        with km._pending_refusal_lock:
            self.assertEqual(km._pending_refusal_rows, [],
                             "the invalid head was DROPPED loudly and the valid row "
                             "behind it landed (the FIFO held order for transient "
                             "faults only)")
        self.assertIn(-6, [r.get("gid") for r in km._union_ops_load()])

    def test_g_a_v1332_journal_with_an_object_error_refusal_stays_proved(self):
        # the verify round: one dict-error refusal row (legal under the old validator)
        # judged the whole journal row-poisoned on upgrade — quarantined, salvaged, and
        # held UNPROVED behind a reconstruction a headless kernel can never perform
        (km.jd.STATE / "union-gestures.json").write_text(json.dumps([
            dict(self.ROW),
            {"refusal": True, "gid": -301, "host": "TESTHOST-B", "name": "pool",
             "error": {"code": 5}, "t": 100}]))
        rows = km._union_ops_echo()
        self.assertIsNotNone(rows, "the legacy journal reads PROVED — no upgrade hold")
        self.assertEqual(len(rows), 2)
        _ref = [r for r in rows if r.get("refusal")][0]
        self.assertIsInstance(_ref.get("error"), str,
                              "the legacy refusal's error is coerced on read")
        self.assertFalse((km.jd.STATE / "union-gestures.json.unproved").exists())

    def test_h_a_tombed_successors_replay_never_rides_a_colliding_ogid(self):
        # the verify round: gids mint from a random 30-bit seed, so a RETIRED successor's
        # replay could name an ogid that collides with an unrelated LIVE gid — the
        # wave-1 exemption kept it
        self.assertTrue(km._union_ops_set([dict(self.ROW)]))   # unrelated live gid 801
        with km.jd._identity_file_lock():
            km._union_tombs_load_locked()
        with km._union_claims_lock:
            km._union_retired_tombs[900] = time.time()         # 900 was retired
            km._union_tombs_save_locked()
        replay = dict(self.ROW, gid=900, ogid=801)             # the collision
        ok, unclaimed, _, _u = km._union_ops_merge([replay], [], ckey="ws:replayer")
        self.assertTrue(ok)
        self.assertNotIn(900, [r["gid"] for r in km._union_ops_load()],
                         "the shield's own retirement outranks resident ancestry")
        self.assertIn(900, unclaimed, "the replaying gate is told to yield")


class R61AuditFixes(unittest.TestCase):
    """the v1.3.33 audit, kernel half (10 P1 / 3 P2 / 1 P3 against cc2833af): the CAS was
    not a whole-group transaction (P1.2), a standing refusal did not block completion
    (P1.1), bare residency admitted plain unclaimed successors (P1.3), a part-corrupt
    tombs map folded fail-open (P1.4), the cap evicted by insertion order (P1.5), the
    discriminator was truthiness-based and op objects optional (P1.6), the suppression
    overlay regressed newer floors (P2.12), and legacy-t deletion made refusals
    immortal (P2.13)."""

    ROW = {"host": "TESTHOST-A", "gid": 801, "edit": {}, "inverse": {}, "rt": {},
           "name": "pool", "dispatched": False}
    SUCC = {"host": "TESTHOST-A", "gid": 802, "ogid": 801, "olin": [801], "rid": 801,
            "edit": {}, "inverse": {}, "rt": {}, "name": "pool", "dispatched": False}

    def setUp(self):
        _scrub_state()
        km._union_claims.clear()
        km._union_retired_tombs.clear()
        km._union_tombs_loaded[0] = False
        km._retry_suppress_cache.clear()
        km._retry_suppress_pending.clear()
        for name in ("union-gestures.json", "union-gestures.json.unproved",
                     "union-tombs.json", "union-tombs.json.hold", "retry-suppressed.json"):
            try:
                (km.jd.STATE / name).unlink()
            except OSError:
                pass
        for f in km.jd.STATE.glob("*.corrupt-*"):
            try:
                f.unlink()
            except OSError:
                pass

    def tearDown(self):
        self.setUp()

    def test_a_a_standing_refusal_blocks_the_completion(self):
        # r61 P1.1 (kernel leg), reproduced through the real bridge: a completion packet
        # captured BEFORE the refusal transmitted after it — the CAS passed and the
        # acked gate re-ran effects every panel's compensation had rolled back
        self.assertTrue(km._union_ops_set([dict(self.ROW)]))
        self.assertTrue(km._union_ops_set(
            [{"refusal": True, "gid": -801, "opId": "801", "host": "TESTHOST-A",
              "name": "pool", "error": "refused", "t": int(time.time())}]))
        ep = km._union_claim_grant(801, "ws:x")
        ok, _, reason, _u = km._union_ops_merge(
            [dict(self.SUCC)], [801], ckey="ws:x",
            rekey={"ogid": 801, "gid": 802, "epoch": ep})
        self.assertFalse(ok, "the refusal is the gesture's VERDICT")
        self.assertIn("refused", reason or "")
        self.assertIn(801, [r["gid"] for r in km._union_ops_load() if not r.get("refusal")],
                      "nothing was retired against the standing refusal")

    def test_b_the_completion_is_a_whole_group_transaction(self):
        # r61 P1.2, executed three ways there: no retirement left two claimable
        # identities; a partial host set lost host B's operation; ogid/olin/rid=999
        # accepted for original 801
        rows = [dict(self.ROW), dict(self.ROW, host="TESTHOST-B")]
        self.assertTrue(km._union_ops_set(rows))
        ep = km._union_claim_grant(801, "ws:x")
        succ_a = dict(self.SUCC)
        succ_b = dict(self.SUCC, host="TESTHOST-B")
        ok, _, reason, _u = km._union_ops_merge(
            [succ_a, succ_b], [], ckey="ws:x",
            rekey={"ogid": 801, "gid": 802, "epoch": ep})
        self.assertFalse(ok, "a rekey WITHOUT the retirement is refused")
        self.assertIn("retire", reason or "")
        ok, _, reason, _u = km._union_ops_merge(
            [succ_a], [801], ckey="ws:x", rekey={"ogid": 801, "gid": 802, "epoch": ep})
        self.assertFalse(ok, "a PARTIAL host set is refused")
        self.assertIn("every host", reason or "")
        ok, _, reason, _u = km._union_ops_merge(
            [dict(succ_a, ogid=999, olin=[999], rid=999),
             dict(succ_b, ogid=999, olin=[999], rid=999)],
            [801], ckey="ws:x", rekey={"ogid": 801, "gid": 802, "epoch": ep})
        self.assertFalse(ok, "a foreign lineage is refused")
        self.assertIn("lineage", reason or "")
        ok, _, _, _u = km._union_ops_merge(
            [succ_a, succ_b], [801], ckey="ws:x",
            rekey={"ogid": 801, "gid": 802, "epoch": ep})
        self.assertTrue(ok, "the whole-group transaction commits")
        self.assertEqual(sorted({r["host"] for r in km._union_ops_load()}),
                         ["TESTHOST-A", "TESTHOST-B"])

    def test_c_bare_residency_admits_no_plain_successor(self):
        # r61 P1.3, executed: a plain unclaimed merge inserted a "successor" beside its
        # LIVING original through bare residency — no rekey, no claim, no retirement —
        # and both stood independently claimable
        self.assertTrue(km._union_ops_set([dict(self.ROW)]))
        with km.jd._identity_file_lock():
            km._union_tombs_load_locked()
        with km._union_claims_lock:
            km._union_retired_tombs[802] = time.time()
            km._union_tombs_save_locked()
        ok, unclaimed, _, _u = km._union_ops_merge([dict(self.SUCC)], [], ckey="ws:replay")
        self.assertTrue(ok)
        self.assertEqual([r["gid"] for r in km._union_ops_load()], [801],
                         "the plain successor was tomb-filtered, never admitted")
        self.assertIn(802, unclaimed)
        # …while the half-committed RETRY still lands — as a FRESH, fully-gated rekey
        # completion (r61 wave 2: the plain retirement-coupled form was the CAS's
        # sibling door; the twin re-claims and re-keys after any failed commit)
        with km._union_claims_lock:
            km._union_retired_tombs.pop(802, None)
            km._union_retired_tombs[801] = time.time()
            km._union_tombs_save_locked()
        _rep = km._union_claim_grant(801, "ws:retry")
        ok, _, _, _u = km._union_ops_merge(
            [dict(self.SUCC)], [801], ckey="ws:retry",
            rekey={"ogid": 801, "gid": 802, "epoch": _rep})
        self.assertTrue(ok)
        self.assertEqual([r["gid"] for r in km._union_ops_load()], [802],
                         "half-committed recovery, fully gated (r60 P1.2 + r61 wave 2)")

    def test_d_a_part_corrupt_tombs_map_is_judged_whole(self):
        # r61 P1.4, executed: {"801":"not-a-time"} was skipped, the rest folded, loaded
        # latched True — gid 801's replay was accepted and claimable with no hold
        (km.jd.STATE / "union-tombs.json").write_text(
            json.dumps({"801": "not-a-time", "802": time.time()}))
        with contextlib.redirect_stderr(io.StringIO()):
            ok, _, reason, _u = km._union_ops_merge([dict(self.ROW)], [], ckey="ws:dead")
        self.assertFalse(ok, "one invalid entry judges the WHOLE ledger")
        self.assertIn("shield", reason or "")
        self.assertTrue((km.jd.STATE / "union-tombs.json.hold").exists(),
                        "the same hold as top-level corruption")

    def test_e_the_cap_evicts_the_oldest_stamp_never_insertion_order(self):
        # r61 P1.5, executed: a refreshed gid kept its dict position and next(iter(...))
        # evicted the freshly re-shielded entry — its replay was accepted
        with km.jd._identity_file_lock():
            km._union_tombs_load_locked()
        now = time.time()
        with km._union_claims_lock:
            km._union_retired_tombs.update({11: now - 30, 12: now - 20, 13: now - 10})
            km._union_tombs_save_locked()
        real_max = km._UNION_TOMBS_MAX
        km._UNION_TOMBS_MAX = 3
        try:
            ok, _, _, _u = km._union_ops_merge([], [11], ckey="ws:x")   # REFRESH 11
            self.assertTrue(ok)
            ok, _, _, _u = km._union_ops_merge([], [14], ckey="ws:x")   # overflow
            self.assertTrue(ok)
        finally:
            km._UNION_TOMBS_MAX = real_max
        self.assertIn(11, km._union_retired_tombs,
                      "the refreshed shield SURVIVES the cap")
        self.assertNotIn(12, km._union_retired_tombs,
                         "the oldest STAMP was the eviction")

    def test_f_the_discriminator_is_a_literal_bool_and_op_objects_are_required(self):
        # r61 P1.6, executed: refusal:"false" (truthy string) validated as a refusal and
        # the browser treated it as a compensation event; {gid,host,dispatched} alone
        # validated and the real completion pass raised TypeError reading rt.host
        self.assertFalse(km._union_row_valid(
            {"refusal": "false", "gid": -3, "name": "pool", "t": 5}))
        self.assertFalse(km._union_row_valid(
            {"gid": 5, "host": "TESTHOST-A", "dispatched": False}),
            "rt/edit/inverse are REQUIRED operation objects")
        self.assertTrue(km._union_row_valid(dict(self.ROW)))
        self.assertTrue(km._union_row_valid(dict(self.ROW, refusal=False)))
        # …and a LEGACY stored refusal:"false" row coerces on read instead of holding
        # the journal unproved on upgrade
        (km.jd.STATE / "union-gestures.json").write_text(json.dumps([
            dict(self.ROW),
            {"refusal": "yes", "gid": -7, "name": "pool", "t": 100, "error": "x"}]))
        rows = km._union_ops_echo()
        self.assertIsNotNone(rows, "the legacy journal reads PROVED")
        self.assertIs([r for r in rows if r.get("gid") == -7][0]["refusal"], True)

    def test_g_a_legacy_bad_timestamp_becomes_the_files_mtime_not_immortal(self):
        # r61 P2.2, executed: deleting the invalid t left the refusal outside the 7d
        # staleness backstop — immortal once persisted by the next healthy merge
        p = km.jd.STATE / "union-gestures.json"
        p.write_text(json.dumps([
            {"refusal": True, "gid": -9, "name": "pool", "t": "yesterday",
             "error": "x", "host": "TESTHOST-A"}]))
        rows = km._union_ops_echo()
        self.assertIsNotNone(rows)
        _t = rows[0].get("t")
        self.assertIsInstance(_t, (int, float),
                              "the coerced row carries a NUMERIC stamp")
        self.assertEqual(int(_t), int(p.stat().st_mtime),
                         "…anchored to the file's own mtime — the 7d backstop applies")

    def test_h_the_overlay_never_regresses_a_newer_durable_floor(self):
        # r61 P2.12, executed: dict.update let an OLDER pending stamp overwrite a newer
        # durable one — the healthy-turn rearm then cleared a suppression the user's
        # later interrupt had refreshed, and the retry storm resumed
        sid = "66666666-7777-8888-9999-000000000002"
        p = km.jd.STATE / "retry-suppressed.json"
        p.write_text(json.dumps({sid: 200}))
        km._retry_suppress_pending[sid] = 100.0
        self.assertEqual(km._retry_suppress_data().get(sid), 200,
                         "the newer durable floor WINS over the stale overlay")
        km._retry_suppress_pending[sid] = 300.0
        self.assertEqual(km._retry_suppress_data().get(sid), 300.0,
                         "…and a newer overlay still lands")


class R61Wave2(unittest.TestCase):
    """the r61 verify round's confirmed second-order defects, kernel half: the completion
    CAS was rekey-gated only — a PLAIN write with successor rows + retired=[original]
    performed the whole rekey-and-retire unvetted (the round's P1); a refusal queued in
    the retry spool was invisible to the standing-refusal arm; and rows v1.3.33 already
    persisted t-less stayed immortal."""

    ROW = {"host": "TESTHOST-A", "gid": 801, "edit": {}, "inverse": {}, "rt": {},
           "name": "pool", "dispatched": False}
    SUCC = {"host": "TESTHOST-A", "gid": 802, "ogid": 801, "olin": [801], "rid": 801,
            "edit": {}, "inverse": {}, "rt": {}, "name": "pool", "dispatched": False}

    def setUp(self):
        _scrub_state()
        km._union_claims.clear()
        km._union_retired_tombs.clear()
        km._union_tombs_loaded[0] = False
        with km._pending_refusal_lock:
            km._pending_refusal_rows[:] = []
        for name in ("union-gestures.json", "union-gestures.json.unproved",
                     "union-tombs.json", "union-tombs.json.hold"):
            try:
                (km.jd.STATE / name).unlink()
            except OSError:
                pass
        for f in km.jd.STATE.glob("*.corrupt-*"):
            try:
                f.unlink()
            except OSError:
                pass

    def tearDown(self):
        self.setUp()

    def test_a_the_plain_sibling_door_is_closed(self):
        # the round's P1, reproduced end-to-end there: with a STANDING refusal for the
        # gesture, the rekey completion was refused — and the IDENTICAL successor rows
        # sent as a plain write with retired=[original] returned ok:True, retiring the
        # original and committing the successors past every gate
        self.assertTrue(km._union_ops_set([dict(self.ROW)]))
        self.assertTrue(km._union_ops_set(
            [{"refusal": True, "gid": -801, "opId": "801", "host": "TESTHOST-A",
              "name": "pool", "error": "refused", "t": int(time.time())}]))
        ok, _, reason, _u = km._union_ops_merge([dict(self.SUCC)], [801], ckey="ws:x")
        self.assertFalse(ok, "the plain rekey-and-retire shape is REFUSED")
        self.assertIn("completion transaction", reason or "")
        gids = [r["gid"] for r in km._union_ops_load() if not r.get("refusal")]
        self.assertEqual(gids, [801], "nothing was retired, nothing committed")
        # an ORDINARY plain retirement (no successor rows) still settles fine
        ok, _, _, _u = km._union_ops_merge([], [801], ckey="ws:x")
        self.assertTrue(ok, "a bare retirement is not a completion shape")

    def test_b_a_spooled_refusal_blocks_the_completion_too(self):
        # r61 wave 2: a refusal whose journal save FAILED sat in _pending_refusal_rows,
        # invisible to the standing-refusal arm — the exact packet-after-refusal window
        # the arm exists to close, re-opened during a disk-fault episode
        self.assertTrue(km._union_ops_set([dict(self.ROW)]))
        with km._pending_refusal_lock:
            km._pending_refusal_rows.append(
                {"refusal": True, "gid": -801, "opId": "801", "host": "TESTHOST-A",
                 "name": "pool", "error": "refused", "t": int(time.time())})
        ep = km._union_claim_grant(801, "ws:x")
        ok, _, reason, _u = km._union_ops_merge(
            [dict(self.SUCC)], [801], ckey="ws:x",
            rekey={"ogid": 801, "gid": 802, "epoch": ep})
        self.assertFalse(ok, "the QUEUED refusal is a standing verdict too")
        self.assertIn("refused", reason or "")

    def test_c_a_v1333_tless_refusal_row_gets_the_files_mtime(self):
        # r61 wave 2: the r61 P2.13 fix stamped only PRESENT invalid t — rows v1.3.33
        # had already persisted WITHOUT t stayed outside the 7d backstop forever
        p = km.jd.STATE / "union-gestures.json"
        p.write_text(json.dumps([
            {"refusal": True, "gid": -9, "name": "pool", "error": "x",
             "host": "TESTHOST-A"}]))
        rows = km._union_ops_echo()
        self.assertIsNotNone(rows)
        self.assertEqual(int(rows[0].get("t")), int(p.stat().st_mtime),
                         "the ABSENT t is stamped like the invalid one — mortal again")


class R62AuditFixes(unittest.TestCase):
    """the v1.3.34 audit, kernel half (5 P1 / 6 P2 against 92e2e3a9): a valid completion
    smuggled a second group's completion past its CAS (P1.1), host SETS ignored row
    multiplicity (P1.2), a rid-only successor bypassed the transaction (P1.3), the tomb
    ledger accepted alias keys and future stamps and skipped the cap at load (P1.4),
    gesture identity collided silently (P2.6), a future mtime defeated the refusal
    expiry (P2.7), and the suppression overlay accepted NaN and iterated unlocked (P2.8)."""

    ROW = {"host": "TESTHOST-A", "gid": 801, "edit": {}, "inverse": {}, "rt": {},
           "name": "pool", "dispatched": False}
    SUCC = {"host": "TESTHOST-A", "gid": 802, "ogid": 801, "olin": [801], "rid": 801,
            "edit": {}, "inverse": {}, "rt": {}, "name": "pool", "dispatched": False}

    def setUp(self):
        _scrub_state()
        km._union_claims.clear()
        km._union_retired_tombs.clear()
        km._union_tombs_loaded[0] = False
        km._retry_suppress_cache.clear()
        with km._retry_suppress_lock:
            km._retry_suppress_pending.clear()
        for name in ("union-gestures.json", "union-gestures.json.unproved",
                     "union-tombs.json", "union-tombs.json.hold", "retry-suppressed.json"):
            try:
                (km.jd.STATE / name).unlink()
            except OSError:
                pass
        for f in km.jd.STATE.glob("*.corrupt-*"):
            try:
                f.unlink()
            except OSError:
                pass

    def tearDown(self):
        self.setUp()

    def test_a_one_completion_cannot_smuggle_another(self):
        # r62 P1.1, executed: a valid completion for A carried an unauthorized completion
        # for B past B's STANDING refusal — the plain-successor guard was disabled
        # request-wide by A's rekey
        self.assertTrue(km._union_ops_set([dict(self.ROW), dict(self.ROW, gid=811)]))
        self.assertTrue(km._union_ops_set(
            [{"refusal": True, "gid": -811, "opId": "811", "host": "TESTHOST-A",
              "name": "pool", "error": "refused", "t": int(time.time())}]))
        ep = km._union_claim_grant(801, "ws:x")
        b_succ = dict(self.SUCC, gid=812, ogid=811, olin=[811], rid=811)
        ok, _, reason, _u = km._union_ops_merge(
            [dict(self.SUCC), b_succ], [801, 811], ckey="ws:x",
            rekey={"ogid": 801, "gid": 802, "epoch": ep})
        self.assertFalse(ok, "B's successor rides no CAS — the write refuses")
        self.assertIn("completion transaction", reason or "")
        gids = sorted(r["gid"] for r in km._union_ops_load() if not r.get("refusal"))
        self.assertEqual(gids, [801, 811], "neither group moved")

    def test_b_successors_map_one_to_one(self):
        # r62 P1.2, executed: {A,B} -> {A,A,B} passed the host-SET compare; the panel
        # dispatched host A twice
        self.assertTrue(km._union_ops_set([dict(self.ROW), dict(self.ROW, host="TESTHOST-B")]))
        ep = km._union_claim_grant(801, "ws:x")
        ok, _, reason, _u = km._union_ops_merge(
            [dict(self.SUCC), dict(self.SUCC), dict(self.SUCC, host="TESTHOST-B")],
            [801], ckey="ws:x", rekey={"ogid": 801, "gid": 802, "epoch": ep})
        self.assertFalse(ok)
        self.assertIn("exactly once", reason or "")
        ok, _, reason, _u = km._union_ops_merge(
            [dict(self.ROW, gid=900), dict(self.ROW, gid=900)], [], ckey="ws:x")
        self.assertFalse(ok, "duplicate (gid, host) rows are malformed anywhere")
        self.assertIn("duplicate", reason or "")

    def test_c_a_rid_only_successor_is_vetted_like_any_other(self):
        # r62 P1.3, executed: {gid:802, rid:801} + retired=[801] rode through — the
        # detector knew only ogid/olin
        self.assertTrue(km._union_ops_set([dict(self.ROW)]))
        rid_only = {"host": "TESTHOST-A", "gid": 802, "rid": 801, "edit": {}, "inverse": {},
                    "rt": {}, "name": "pool", "dispatched": False}
        ok, _, reason, _u = km._union_ops_merge([rid_only], [801], ckey="ws:x")
        self.assertFalse(ok, "rid ancestry is ancestry")
        self.assertIn("completion transaction", reason or "")
        # …and a STRAY rid-only successor beside its living original is dropped and
        # named unclaimed, never inserted
        ok, unclaimed, _, _u = km._union_ops_merge([rid_only], [], ckey="ws:x")
        self.assertTrue(ok)
        self.assertEqual([r["gid"] for r in km._union_ops_load()], [801])
        self.assertIn(802, unclaimed)

    def test_d_tomb_keys_are_canonical_stamps_bounded_cap_held_at_load(self):
        # r62 P1.4, executed: "01"/"1" aliases folded into one gid keeping the OLDER
        # stamp; century-future entries outlived every eviction while a current shield
        # was evicted in their place
        now = time.time()
        (km.jd.STATE / "union-tombs.json").write_text(json.dumps({"01": now, "1": now - 5}))
        with contextlib.redirect_stderr(io.StringIO()):
            ok, _, reason, _u = km._union_ops_merge([], [5], ckey="ws:x")
        self.assertFalse(ok, "an alias key judges the whole ledger")
        self.assertTrue((km.jd.STATE / "union-tombs.json.hold").exists())
        # a fresh process: future stamps clamp, and the cap holds at LOAD
        (km.jd.STATE / "union-tombs.json.hold").unlink()
        km._union_tombs_loaded[0] = False
        km._union_retired_tombs.clear()
        real_max = km._UNION_TOMBS_MAX
        km._UNION_TOMBS_MAX = 3
        try:
            (km.jd.STATE / "union-tombs.json").write_text(json.dumps(
                {"11": now + 100 * 365 * 86400, "12": now - 40, "13": now - 30,
                 "14": now - 20, "15": now - 10}))
            with km.jd._identity_file_lock():
                km._union_tombs_load_locked()
        finally:
            km._UNION_TOMBS_MAX = real_max
        self.assertTrue(km._union_tombs_loaded[0])
        self.assertLessEqual(km._union_retired_tombs.get(11, 0), now + 5,
                             "the future stamp is clamped to now")
        self.assertEqual(len(km._union_retired_tombs), 3, "the cap holds at load")
        self.assertNotIn(12, km._union_retired_tombs, "oldest stamps evicted first")
        self.assertIn(11, km._union_retired_tombs, "the clamped entry is current, kept")

    def test_f_an_identity_collision_never_overwrites_the_claimed_row(self):
        # r62 P2.6, executed: beta's same-(gid,host) row overwrote alpha's durable row
        # BEFORE beta was told to yield
        alpha = dict(self.ROW, gid=42, name="alpha")
        ok, unclaimed, _, _u = km._union_ops_merge([alpha], [], ckey="ws:alpha")
        self.assertTrue(ok)
        self.assertEqual(unclaimed, [])
        beta = dict(self.ROW, gid=42, name="beta")
        ok, unclaimed, _, _u = km._union_ops_merge([beta], [], ckey="ws:beta")
        self.assertTrue(ok)
        self.assertIn(42, unclaimed, "beta is told to yield…")
        self.assertEqual([r["name"] for r in km._union_ops_load()], ["alpha"],
                         "…and alpha's durable row was never overwritten")

    def test_g_the_standing_refusal_check_is_name_scoped(self):
        self.assertTrue(km._union_ops_set([dict(self.ROW)]))
        self.assertTrue(km._union_ops_set(
            [{"refusal": True, "gid": -801, "opId": "801", "host": "TESTHOST-A",
              "name": "otherTag", "error": "refused", "t": int(time.time())}]))
        ep = km._union_claim_grant(801, "ws:x")
        ok, _, reason, _u = km._union_ops_merge(
            [dict(self.SUCC)], [801], ckey="ws:x",
            rekey={"ogid": 801, "gid": 802, "epoch": ep})
        self.assertTrue(ok, "a colliding refusal for ANOTHER tag never blocks this "
                            "completion (r62 P2.6): %s" % reason)

    def test_h_future_timestamps_never_defeat_the_refusal_expiry(self):
        # r62 P2.7, executed: a century-ahead file mtime was copied into a legacy refusal
        p = km.jd.STATE / "union-gestures.json"
        p.write_text(json.dumps([
            {"refusal": True, "gid": -9, "name": "pool", "error": "x", "host": "TESTHOST-A"}]))
        far = time.time() + 100 * 365 * 86400
        os.utime(p, (far, far))
        rows = km._union_ops_echo()
        self.assertLessEqual(rows[0]["t"], time.time() + 5, "the migrated stamp clamps to now")
        ok, _, reason, _u = km._union_ops_merge(
            [{"refusal": True, "gid": -3, "name": "pool", "t": far}], [], ckey="ws:x")
        self.assertFalse(ok, "an implausibly future live t is refused at the WIRE")
        self.assertIn("future", reason or "")

    def test_i_the_overlay_is_finite_and_lock_safe(self):
        # r62 P2.8, executed: a NaN durable floor made every max-merge answer False and
        # cleared a suppression despite a newer pending floor; an unlocked iteration
        # raised RuntimeError under a concurrent writer
        sid = "66666666-7777-8888-9999-000000000003"
        p = km.jd.STATE / "retry-suppressed.json"
        p.write_text('{"%s": NaN}' % sid)
        with km._retry_suppress_lock:
            km._retry_suppress_pending[sid] = 300.0
        self.assertEqual(km._retry_suppress_data().get(sid), 300.0,
                         "the pending floor wins over a NaN durable value")
        self.assertEqual(km.jd.canonicalize_retry_suppressed_identity({sid: float("nan")}),
                         {}, "the canonicalizer drops non-finite stamps")
        # the iteration is lock-safe under a racing writer
        stop = [False]
        errs = []
        def writer():
            i = 0
            while not stop[0]:
                with km._retry_suppress_lock:
                    km._retry_suppress_pending["w%d" % i] = float(i)
                    if i > 50:
                        km._retry_suppress_pending.pop("w%d" % (i - 50), None)
                i += 1
        def reader():
            try:
                for _ in range(300):
                    km._suppress_overlay({})
            except RuntimeError as e:
                errs.append(e)
        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        t1.start(); t2.start(); t2.join(); stop[0] = True; t1.join()
        self.assertEqual(errs, [], "no 'dictionary changed size during iteration'")



class R62Wave2(unittest.TestCase):
    """the r62 verify round's confirmed second-order defects, kernel half: ancestry
    vetting was skipped for a SALVAGED store (real rows, plain door open while the gated
    path refused); a colliding gid on a NEW host merged a foreign row into a stranger's
    claimed group; and the future-t bound judged STORED rows, poisoning a journal over one
    v1.3.34-persisted stamp or a backward clock step."""

    ROW = {"host": "TESTHOST-A", "gid": 801, "edit": {}, "inverse": {}, "rt": {},
           "name": "pool", "dispatched": False}
    SUCC = {"host": "TESTHOST-A", "gid": 802, "ogid": 801, "olin": [801], "rid": 801,
            "edit": {}, "inverse": {}, "rt": {}, "name": "pool", "dispatched": False}

    def setUp(self):
        _scrub_state()
        km._union_claims.clear()
        km._union_retired_tombs.clear()
        km._union_tombs_loaded[0] = False
        for name in ("union-gestures.json", "union-gestures.json.unproved",
                     "union-tombs.json", "union-tombs.json.hold"):
            try:
                (km.jd.STATE / name).unlink()
            except OSError:
                pass
        for f in km.jd.STATE.glob("*.corrupt-*"):
            try:
                f.unlink()
            except OSError:
                pass

    def tearDown(self):
        self.setUp()

    def test_a_a_salvaged_store_vets_ancestry_too(self):
        # the round's headline, reproduced: valid rows under the .unproved marker set the
        # reconstruction flag, the vetting block was skipped, and a plain successor +
        # retired=[original] performed the rekey-and-retire unvetted — while the gated
        # rekey path refused "held for reconstruction"
        self.assertTrue(km._union_ops_set([dict(self.ROW)]))
        km._union_unproved_marker().write_text("1")          # a salvaged store
        with contextlib.redirect_stderr(io.StringIO()):
            ok, _, reason, _u = km._union_ops_merge([dict(self.SUCC)], [801],
                                                    ckey="ws:bystander")
        self.assertFalse(ok, "the plain door is shut in the salvaged state too")
        self.assertIn("completion transaction", reason or "")
        self.assertEqual([r["gid"] for r in km._union_ops_load()], [801])

    def test_b_a_new_host_cannot_join_a_strangers_claimed_group(self):
        alpha = dict(self.ROW, gid=500, name="pool")
        ok, _, _, _u = km._union_ops_merge([alpha], [], ckey="ws:alpha")
        self.assertTrue(ok)
        beta = dict(self.ROW, gid=500, host="TESTHOST-B", name="other")
        ok, unclaimed, _, _u = km._union_ops_merge([beta], [], ckey="ws:beta")
        self.assertTrue(ok)
        self.assertIn(500, unclaimed, "beta yields…")
        self.assertEqual([(r["host"], r["name"]) for r in km._union_ops_load()],
                         [("TESTHOST-A", "pool")],
                         "…and its foreign row never joined alpha's group")
        # the OWNER extending its own group is still fine
        mine = dict(self.ROW, gid=500, host="TESTHOST-B", name="pool")
        ok, unclaimed, _, _u = km._union_ops_merge([mine], [], ckey="ws:alpha")
        self.assertTrue(ok)
        self.assertEqual(len(km._union_ops_load()), 2)

    def test_c_a_stored_future_stamp_clamps_instead_of_poisoning(self):
        far = int(time.time()) + 2 * 86400
        (km.jd.STATE / "union-gestures.json").write_text(json.dumps([
            dict(self.ROW),
            {"refusal": True, "gid": -777, "opId": "777", "name": "lake",
             "host": "TESTHOST-B", "error": "x", "t": far}]))
        rows = km._union_ops_echo()
        self.assertIsNotNone(rows, "the journal reads PROVED — no upgrade hold")
        self.assertFalse(km._union_unproved_marker().exists())
        _ref = [r for r in rows if r.get("refusal")][0]
        self.assertLessEqual(_ref["t"], time.time() + 5, "the stored stamp clamps on read")
