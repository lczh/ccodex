#!/usr/bin/env python3
"""The awaiting-peer MATRIX (the user 2026-08-24, after three reports of idle sessions reading
"awaiting a peer"): message kind (delegate/coordinate/question) x direction (sent/received) x
reply-expectation x closer involvement -> does awaiting-peer fire, via which writer, retiring on
which event. The rule under test: awaiting-a-peer requires an un-answered kind=question the session
ITSELF sent — the wait-map's post-2026-08-15 semantics — and the judge writers (the closer's verdict
path, the nudge planner's awaiting op) may not widen it: a DELEGATE transfers ownership, a
COORDINATE requests nothing, and an idle recipient reads idle. Every kept state names its exact
retiring event (the peer's any-kind reply); the write-time supersede key is pinned on BOTH stamp
readers (the 2026-08-19 audit fixed only one twin). All fixtures SYNTHETIC."""
import json
import os
import tempfile
import time
import unittest
from unittest import mock
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_awmx", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"   # the session under test (a worker)
MGR = "66666666-7777-8888-9999-aaaaaaaaaaaa"   # its manager peer
NOW = 1_781_100_000
T0 = NOW - 3600


def _msg(i, f, t_, ts, kind=None, body="x"):
    r = {"id": "m%d" % i, "ev": "sent", "from": "web", "from_id": f, "to_id": t_, "t": ts, "body": body}
    if kind:
        r["kind"] = kind
    return json.dumps(r)


class _Base(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self.td = tempfile.TemporaryDirectory()
        jd._rebind_state(Path(self.td.name))
        jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)
        self._reset_caches()

    def tearDown(self):
        jd._rebind_state(self._saved)
        self._reset_caches()
        self.td.cleanup()

    def _reset_caches(self):
        jd._PEER_ASK_CACHE[:] = [None, ({}, {}, {})]
        km._POSTAL_WAIT_CACHE[:] = [None, None]
        km._SESSION_STAMP_CACHE.clear()

    def _log(self, rows):
        jd.MESSAGES.write_text("\n".join(rows) + ("\n" if rows else ""))
        self._reset_caches()

    def _store(self):
        s = {"rompUuid": SID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
             "placements": {}, "status": {}}
        jd.apply_plan(s, "s1", T0, [{"do": "mint", "why": "x", "text": "Ship the exporter"}], [])
        return s, SID + ":g1"

    def _close_peer(self, s, why="waiting to hear back from the manager"):
        # one closer sweep filing awaiting kind=peer on menu goal 1 — the writer under test
        menu = jd.open_menu(s)
        jd.apply_close(s, menu, {"done": {}, "block": {},
                                 "awaiting": {1: {"why": why, "kind": "peer"}}}, t=T0 + 500)


class MatrixWaitMap(_Base):
    """The deterministic writer (fixed 2026-08-15): sent-question-only, any-kind reply retires."""

    def test_sent_kind_grid(self):
        for kind, fires in (("delegate", False), ("coordinate", False), ("question", True)):
            self._log([_msg(1, SID, MGR, T0, kind)])
            g = km._wait_for_graph(NOW, {SID, MGR})
            self.assertEqual(SID in g, fires, "sent %s -> edge %s" % (kind, fires))
            if fires:
                self.assertEqual(g[SID]["peerSid"], MGR)

    def test_received_kind_grid_recipient_never_waits(self):
        # direction=received: no inbound kind may mark the RECIPIENT as awaiting — idle reads idle
        for kind in ("delegate", "coordinate", "question"):
            self._log([_msg(1, MGR, SID, T0, kind)])
            self.assertNotIn(SID, km._wait_for_graph(NOW, {SID, MGR}),
                             "an inbound %s never marks the recipient awaiting" % kind)

    def test_retiring_event_is_the_reply_of_any_kind(self):
        self._log([_msg(1, SID, MGR, T0, "question"), _msg(2, MGR, SID, T0 + 60, "coordinate")])
        self.assertNotIn(SID, km._wait_for_graph(NOW, {SID, MGR}),
                         "the peer's reply — any kind — is the exact retiring event")


class MatrixCloserGate(_Base):
    """The closer's verdict path (strand 1/2): kind=peer admits ONLY over an open sent-question."""

    def _stamp(self, s, gid):
        return s["nodes"][gid].get("awaitingWhy"), s["nodes"][gid].get("awaitingKind")

    def test_no_postal_history_drops_the_peer_stamp(self):
        s, gid = self._store()
        self._close_peer(s)
        self.assertEqual(self._stamp(s, gid), (None, None),
                         "an idle session with no open ask reads idle — the closer stands down")
        self.assertFalse(any(r.get("kind") == "awaiting" for r in s["nodes"][gid].get("log", [])),
                         "nothing is filed at all, not even a lifted row")

    def test_sent_delegate_drops_sent_coordinate_drops_sent_question_admits(self):
        for kind, admitted in (("delegate", False), ("coordinate", False), ("question", True)):
            s, gid = self._store()
            self._log([_msg(1, SID, MGR, T0 + 10, kind)])
            self._close_peer(s)
            why, k = self._stamp(s, gid)
            if admitted:
                self.assertEqual(k, "peer", "an open sent-question admits the closer's peer stamp")
                self.assertTrue(why)
                self.assertEqual(s["nodes"][gid].get("awaitingPeers"), [MGR],
                                 "…and records WHICH peer it awaits — the pair-aware supersede's key")
            else:
                self.assertEqual((why, k), (None, None),
                                 "a sent %s never mints awaiting-peer (ownership moved / nothing asked)" % kind)

    def test_an_answered_question_no_longer_admits(self):
        s, gid = self._store()
        self._log([_msg(1, SID, MGR, T0 + 10, "question"), _msg(2, MGR, SID, T0 + 20, "coordinate")])
        self._close_peer(s)
        self.assertEqual(self._stamp(s, gid), (None, None),
                         "the reply already landed — there is no outstanding ask to wait on")

    def test_received_question_alone_never_admits(self):
        # the strand-2 shape: a worker that dispatched NOTHING replies (coordinate) to its manager's
        # mail; the closer's old "or asked" gloss read that reply as a peer wait
        s, gid = self._store()
        self._log([_msg(1, MGR, SID, T0 + 10, "question"), _msg(2, SID, MGR, T0 + 20, "coordinate")])
        self._close_peer(s, why="reported results; continues when the manager responds")
        self.assertEqual(self._stamp(s, gid), (None, None),
                         "an idle recipient reads idle — its own reply is not an ask")

    def test_other_kinds_pass_the_gate_untouched(self):
        s, gid = self._store()
        menu = jd.open_menu(s)
        jd.apply_close(s, menu, {"done": {}, "block": {},
                                 "awaiting": {1: {"why": "CI run 12 still going", "kind": "job"}}}, t=T0 + 500)
        self.assertEqual(self._stamp(s, gid), ("CI run 12 still going", "job"),
                         "the gate is peer-scoped: job/agents/task/timer file as before")

    def test_a_rejected_reassert_is_a_stand_down_not_a_lift(self):
        s, gid = self._store()
        self._log([_msg(1, SID, MGR, T0 + 10, "question")])
        self._close_peer(s)                       # admitted while the ask is open
        self.assertEqual(self._stamp(s, gid)[1], "peer")
        self._log([_msg(1, SID, MGR, T0 + 10, "question"), _msg(2, MGR, SID, T0 + 20, "coordinate")])
        self._close_peer(s)                       # re-assert now that the ask is answered: rejected
        self.assertEqual(self._stamp(s, gid)[1], "peer",
                         "the standing stamp is NOT lifted by the stand-down (no new information "
                         "was filed); its retirement stays the read-side answered supersede")

    def test_the_two_readers_of_the_postal_log_agree(self):
        # jd._open_peer_asks mirrors km._postal_wait_maps (question-only + alias re-key); pin them
        # against one fixture so they cannot drift apart. The comparison is the raw MAPS, not the
        # alive-filtered _wait_for_graph: the gate deliberately keeps a dead/unknown peer's open ask
        # (the dead-wait sweep owns that ending, not the write gate).
        def km_open(sid):
            last_any, last_ask, _aw = km._postal_wait_maps()
            return any(f == sid and last_any.get((p, sid), 0) < meta[0]
                       for (f, p), meta in last_ask.items())
        grid = [
            [_msg(1, SID, MGR, T0, "delegate")],
            [_msg(1, SID, MGR, T0, "coordinate")],
            [_msg(1, SID, MGR, T0, "question")],
            [_msg(1, SID, MGR, T0, "question"), _msg(2, MGR, SID, T0 + 5, "delegate")],
            [_msg(1, SID, MGR, T0, None, "QUESTION: which port?")],       # legacy kindless ask
            [_msg(1, SID, "peer:otherbox", T0, "question")],              # cross-host, relay-keyed
        ]
        for rows in grid:
            self._log(rows)
            self.assertEqual(jd._open_peer_asks(SID), km_open(SID),
                             "gate and wait-maps disagree on: %s" % rows)
        # and where the peer IS alive, the user-facing graph agrees with the gate too
        self._log([_msg(1, SID, MGR, T0, "question")])
        self.assertTrue(jd._open_peer_asks(SID) and SID in km._wait_for_graph(NOW, {SID, MGR}))


class MatrixPlannerGate(_Base):
    """The nudge planner's awaiting op: kind=peer demotes to kindless without an open ask (the op's
    anti-false-interrupt job survives; the false classification does not)."""

    def test_peer_without_ask_demotes_to_kindless(self):
        s, gid = self._store()
        jd.apply_plan(s, "s2", T0 + 100,
                      [{"do": "awaiting", "goal": 1, "why": "holding for the manager's next batch",
                        "kind": "peer"}], jd.open_menu(s))
        nd = s["nodes"][gid]
        self.assertEqual(nd.get("awaitingWhy"), "holding for the manager's next batch",
                         "the why survives — silence would convert to a false needs-you block")
        self.assertIsNone(nd.get("awaitingKind"), "…but the peer claim does not")

    def test_peer_with_open_ask_files_as_peer(self):
        s, gid = self._store()
        self._log([_msg(1, SID, MGR, T0 + 10, "question")])
        jd.apply_plan(s, "s2", T0 + 100,
                      [{"do": "awaiting", "goal": 1, "why": "asked the manager which port",
                        "kind": "peer"}], jd.open_menu(s))
        self.assertEqual(s["nodes"][gid].get("awaitingKind"), "peer")


class MatrixSupersedeTwins(_Base):
    """The write-time supersede key holds on BOTH stamp readers (the 2026-08-19 audit fixed
    _goal_awaiting_stamp_full only; _session_stamp_read stayed anchor-keyed, so the card and the
    chip answered one fact two ways — found 2026-08-24)."""

    def _stamped_store_on_disk(self, ev_t):
        s, gid = self._store()
        self._log([_msg(1, SID, MGR, T0 + 10, "question")])
        menu = jd.open_menu(s)
        jd.apply_close(s, menu, {"done": {}, "block": {},
                                 "awaiting": {1: {"why": "asked the manager which port", "kind": "peer"}}},
                       t=ev_t)
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(s))
        km._SESSION_STAMP_CACHE.clear()
        return s, gid

    def test_a_restamp_written_after_the_reply_survives_on_both_readers(self):
        # anchor (ev_t) BEFORE the peer's reply; write time (now) after it. Anchor-keyed reading
        # superseded this instantly; write-time reading keeps it on the card AND the chip.
        s, gid = self._stamped_store_on_disk(ev_t=T0 + 100)
        self._log([_msg(1, SID, MGR, T0 + 10, "question"), _msg(2, MGR, SID, T0 + 200, "coordinate")])
        answered = km._peer_answered_at(SID)
        self.assertEqual(answered, T0 + 200, "the reply is the supersede clock")
        nodes = s["nodes"]
        full = km._goal_awaiting_stamp_full(nodes, gid, answered_at=answered)
        self.assertIsNotNone(full, "write-time keyed: a stamp filed after the reply stands (card)")
        got = km._session_stamp_read(SID)
        self.assertEqual(got[0][2], "asked the manager which port",
                         "…and the session-level reader agrees (chip/lane/pip) — the twins match")

    def test_a_stamp_the_reply_postdates_retires_on_both_readers(self):
        # the reply lands AFTER the stamp's write time (a future-t fixture row beats time.time()):
        # both readers retire it — the peer's answer is the exact retiring event
        s, gid = self._stamped_store_on_disk(ev_t=T0 + 100)
        future = int(time.time()) + 10_000
        self._log([_msg(1, SID, MGR, T0 + 10, "question"), _msg(2, MGR, SID, future, "coordinate")])
        answered = km._peer_answered_at(SID)
        self.assertEqual(answered, future)
        nodes = s["nodes"]
        self.assertIsNone(km._goal_awaiting_stamp_full(nodes, gid, answered_at=answered),
                          "the peer answered after the stamp was written -> superseded (card)")
        self.assertIsNone(km._session_stamp_read(SID)[0][0],
                          "…and on the session surfaces alike — one fact, one answer")


class PairAwareSupersede(_Base):
    """Hole (b), 2026-08-24: an identity-carrying stamp ends only on the AWAITED pair's answer; an
    unrelated exchange no longer hides a real wait. Legacy identity-less stamps keep the pair-blind
    read — nothing strands. One predicate for every reader (pinned below)."""

    OTHER = "77777777-8888-9999-aaaa-bbbbbbbbbbbb"    # an unrelated peer on the same log

    def _stamped(self, ev_t=T0 + 100):
        s, gid = self._store()
        self._log([_msg(1, SID, MGR, T0 + 10, "question")])
        jd.apply_close(s, jd.open_menu(s), {"done": {}, "block": {},
                       "awaiting": {1: {"why": "asked the manager which port", "kind": "peer"}}}, t=ev_t)
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(s))
        km._SESSION_STAMP_CACHE.clear()
        return s, gid

    def test_an_unrelated_pair_answer_no_longer_supersedes(self):
        s, gid = self._stamped()
        future = int(time.time()) + 10_000
        # an answered exchange with a DIFFERENT peer, after the stamp's write time
        self._log([_msg(1, SID, MGR, T0 + 10, "question"),
                   _msg(2, SID, self.OTHER, T0 + 20, "question"),
                   _msg(3, self.OTHER, SID, future, "coordinate")])
        answered = km._peer_answered(SID)
        self.assertGreater(answered[0], 0, "the pair-blind scalar WOULD have superseded")
        nodes = s["nodes"]
        self.assertIsNotNone(km._goal_awaiting_stamp_full(nodes, gid, answered_at=answered),
                             "the stamp awaits MGR — mail from another peer cannot end it (card)")
        self.assertEqual(km._session_stamp_read(SID)[0][2], "asked the manager which port",
                         "…and the session-level twin agrees (chip/lane)")

    def test_the_awaited_pair_answer_still_supersedes_instantly(self):
        s, gid = self._stamped()
        future = int(time.time()) + 10_000
        self._log([_msg(1, SID, MGR, T0 + 10, "question"), _msg(2, MGR, SID, future, "coordinate")])
        answered = km._peer_answered(SID)
        nodes = s["nodes"]
        self.assertIsNone(km._goal_awaiting_stamp_full(nodes, gid, answered_at=answered),
                          "the awaited peer answered after the write -> superseded (card)")
        self.assertIsNone(km._session_stamp_read(SID)[0][0], "…both readers, one predicate")

    def test_a_legacy_identity_less_stamp_keeps_the_pair_blind_read(self):
        s, gid = self._stamped()
        del s["nodes"][gid]["awaitingPeers"]           # a pre-identity stamp, as stored stores hold
        future = int(time.time()) + 10_000
        self._log([_msg(1, SID, MGR, T0 + 10, "question"),
                   _msg(2, SID, self.OTHER, T0 + 20, "question"),
                   _msg(3, self.OTHER, SID, future, "coordinate")])
        self.assertIsNone(km._goal_awaiting_stamp_full(s["nodes"], gid, answered_at=km._peer_answered(SID)),
                          "no identity on the stamp -> today's behavior exactly (never strand legacy)")

    def test_every_reader_shares_the_one_predicate(self):
        # the twins rule, extended (the manager's ask): no reader may inline its own compare — the
        # write-time-vs-answer compare exists ONLY inside _peer_stamp_superseded
        src = open(os.path.join(BIN, "romp-kernel")).read()
        inline = [l for l in src.splitlines()
                  if "_stamp_written_at(nd) <" in l or "_stamp_written_at(nd) >=" in l]
        self.assertEqual(len(inline), 1, "one compare, inside the predicate: %r" % inline)
        self.assertGreaterEqual(src.count("_peer_stamp_superseded(nd,"), 4,
                                "card reader, session twin, sweep lift, fire-gate — all through it")


class CrossHostDelegation(_Base):
    """Hole (a), 2026-08-24: a cross-host delegate plants the sender-side tracking node from the
    sent row (declared kind, horizon-bounded, idempotent), and the recipient's REPLY mail — never
    the relay ack — completes it."""

    RHOST, RNAME = "TESTHOST-B", "web"
    RSID = "eeeeeeee-ffff-0000-1111-222222222222"     # the remote recipient's sid, learned on reply

    def _xrow(self, i, ts, kind="delegate", body="own the exporter work"):
        return json.dumps({"id": "px-%d.mail.TESTHOST-A" % i, "ev": "sent", "from": "api",
                           "from_id": SID, "to_id": "peer:%s" % self.RHOST,
                           "toName": "%s:%s" % (self.RHOST, self.RNAME), "t": ts,
                           "kind": kind, "body": body})

    def _reply(self, i, ts):
        return json.dumps({"id": "rx-%d.mail.%s" % (i, self.RHOST), "ev": "sent", "from": self.RNAME,
                           "from_id": self.RSID, "from_host": self.RHOST, "to_id": SID,
                           "t": ts, "kind": "coordinate", "body": "done: exporter shipped"})

    def _fleet_stub(self):
        # run_courier/run_propagate discover the fleet from names+transcripts; stub the discovery
        # to the one local sender (the recipient is REMOTE by construction)
        self._saved_discover = jd.discover
        jd.discover = lambda now: [(SID, "/tmp/none.jsonl", None, "api")]
        self.addCleanup(lambda: setattr(jd, "discover", self._saved_discover))

    def _handoffs(self):
        st = jd.load_goals(SID)
        return [nd for nd in st["nodes"].values() if isinstance(nd.get("handoff"), dict)]

    def test_a_cross_host_delegate_plants_the_tracking_node(self):
        self._fleet_stub()
        self._log([self._xrow(1, T0 + 10)])
        jd.run_courier(now=T0 + 100)
        hs = self._handoffs()
        self.assertEqual(len(hs), 1, "the sent row is the authoritative record — planted from it")
        self.assertEqual(hs[0]["handoff"]["peer"], "TESTHOST-B:web",
                         "the identity is toName — displays resolve it, the remote arm re-keys from it")
        self.assertIn("↪ delegated to TESTHOST-B:web", hs[0]["text"])
        self.assertNotIn("tracked", hs[0]["handoff"], "tracked never rides the relay — the boundary")
        jd.run_courier(now=T0 + 200)
        self.assertEqual(len(self._handoffs()), 1, "idempotent by msgId — one plant per message ever")

    def test_declared_only_and_horizon_bounded(self):
        self._fleet_stub()
        self._log([self._xrow(1, T0 + 10, kind="coordinate"),
                   self._xrow(2, T0 - jd.COURIER_RETRY_HORIZON - 60)])
        jd.run_courier(now=T0 + 100)
        self.assertEqual(self._handoffs(), [], "a non-delegate never plants; ancient rows never backfill")

    def test_the_reply_completes_and_the_relay_ack_does_not(self):
        self._fleet_stub()
        self._log([self._xrow(1, T0 + 10)])
        jd.run_courier(now=T0 + 100)
        # the far host's delivery ack lands — the ASK arrived; the work is NOT done
        rows = [self._xrow(1, T0 + 10),
                json.dumps({"id": "px-1.mail.TESTHOST-A", "ev": "relayed", "t": T0 + 100})]
        self._log(rows)
        jd.run_propagate(now=T0 + 200)
        self.assertFalse(self._handoffs()[0].get("nodeComplete"),
                         "relayed = delivered, not completed — delivery cannot check work off")
        # a mere REPLY is not the report-back event any more (the v1.3.16 audit's P1.3: one
        # reply completed every outstanding delegation to that peer) — the per-message receipt is
        rows.append(self._reply(1, T0 + 300))
        self._log(rows)
        jd.run_propagate(now=T0 + 400)
        self.assertFalse(self._handoffs()[0].get("nodeComplete"),
                         "a reply alone completes nothing — the join is the receipt's mid")
        rows.append(json.dumps({"id": "px-1.mail.TESTHOST-A", "ev": "handoff-done",
                                "t": T0 + 500, "from_host": "TESTHOST-B"}))
        self._log(rows)
        jd._HANDOFF_DONE_MEMO["key"] = None
        jd.run_propagate(now=T0 + 600)
        nd = self._handoffs()[0]
        self.assertTrue(nd.get("nodeComplete"), "the completion receipt checks the handoff off")
        self.assertIn("completion receipt", nd.get("doneWhy") or "")

    def test_a_completed_cross_host_planted_goal_reports_its_receipt_home(self):
        # the recipient side of the lane: a completed planted goal whose origin crossed hosts
        # POSTs {mid, host} to the local bus on every propagate pass (the bus dedups durably)
        self._fleet_stub()
        store = jd.load_goals(SID)
        store["seq"] = store.get("seq", 0) + 1
        nid = "%s:g%d" % (SID, store["seq"])
        store["nodes"][nid] = jd.GuardedNode({
            "id": nid, "text": "own the exporter work", "parentId": None,
            "nodeComplete": True, "blocked": False, "cleared": False, "trail": [],
            "t": T0, "log": [],
            "origin": {"peer": "ffffffff-0000-1111-2222-333333333333",
                       "msgId": "px-9.mail.TESTHOST-A", "peerHost": "TESTHOST-A"}})
        jd.save_goals(SID, store)
        posted = []
        with mock.patch.object(jd, "_post_handoff_receipt",
                               side_effect=lambda mid: posted.append(mid)):
            jd.run_propagate(now=T0 + 100)
        self.assertIn("px-9.mail.TESTHOST-A", posted,
                      "the completion reports its DELIVERY id — the bus translates (r44)")

    def test_one_receipt_completes_exactly_its_own_delegation(self):
        # the v1.3.16 audit's P1.3 repro: two incomplete delegated tasks + one report produced
        # two completed tasks under the per-peer heuristic. The receipt joins on the mid.
        self._fleet_stub()
        rows = [self._xrow(1, T0 + 10), self._xrow(2, T0 + 20, body="own the importer too")]
        self._log(rows)
        jd.run_courier(now=T0 + 100)
        self.assertEqual(len(self._handoffs()), 2)
        rows.append(json.dumps({"id": "px-1.mail.TESTHOST-A", "ev": "handoff-done",
                                "t": T0 + 300, "from_host": self.RHOST}))
        self._log(rows)
        jd._HANDOFF_DONE_MEMO["key"] = None
        jd.run_propagate(now=T0 + 400)
        done = sorted(bool(nd.get("nodeComplete")) for nd in self._handoffs())
        self.assertEqual(done, [False, True],
                         "one receipt completes ONE delegation — its own, never the sibling's")

    def test_a_coordinate_after_the_reply_never_hides_the_answer(self):
        # the v1.3.16 audit's P2.11 repro: question@10 -> reply@150 -> coordinate@200 read as
        # peer_answered=0, because the answered-clock was the newest outbound of ANY kind
        self._fleet_stub()
        peer = "eeeeeeee-1111-2222-3333-444444444444"
        self._log([json.dumps({"id": "q1", "ev": "sent", "from": "api", "from_id": SID,
                               "to_id": peer, "t": T0 + 10, "kind": "question", "body": "port?"}),
                   json.dumps({"id": "r1", "ev": "sent", "from": "peer", "from_id": peer,
                               "to_id": SID, "t": T0 + 150, "kind": "coordinate", "body": "8080"}),
                   json.dumps({"id": "c1", "ev": "sent", "from": "api", "from_id": SID,
                               "to_id": peer, "t": T0 + 200, "kind": "coordinate",
                               "body": "fyi shipping"})])
        km._POSTAL_WAIT_CACHE[0] = None
        self.assertEqual(km._peer_answered_at(SID), T0 + 150,
                         "a coordinate requests nothing — it must not reset the answered-clock")

    def test_the_stable_sid_join_survives_a_remote_rename(self):
        # the v1.3.16 audit's P1.5: the recipient renamed before its first reply, so the
        # name-keyed alias never joined — the sent row's to_sid snapshot is rename-proof
        self._fleet_stub()
        ask = json.dumps({"id": "px-7.mail.TESTHOST-A", "ev": "sent", "from": "api",
                          "from_id": SID, "to_id": "peer:%s" % self.RHOST,
                          "toName": "%s:%s" % (self.RHOST, self.RNAME),
                          "to_sid": self.RSID, "t": T0 + 10, "kind": "question",
                          "body": "QUESTION: the staging port?"})
        reply = json.dumps({"id": "rx-7.mail.%s" % self.RHOST, "ev": "sent", "from": "worker",
                            "from_id": self.RSID, "from_host": self.RHOST, "to_id": SID,
                            "t": T0 + 300, "kind": "coordinate", "body": "8080"})
        self._log([ask, reply])
        km._POSTAL_WAIT_CACHE[0] = None
        self.assertEqual(km._peer_answered_at(SID), T0 + 300,
                         "the reply's from_id matches the to_sid snapshot whatever the rename")
        self._log([ask])                              # without the reply the ask is an open edge
        km._POSTAL_WAIT_CACHE[0] = None
        self.assertEqual(km._peer_answered_at(SID), 0)

    def test_a_legacy_row_joins_through_the_durable_alias_binding(self):
        # the v1.3.17 audit's P2.14: a pre-to_sid row sent under the peer's OLD name never
        # joined its reply — the alias map was built only from rows the peer spoke in, and a
        # renamed peer speaks only under the new name. The bus now logs each presence binding
        # as an ev:"peer-alias" row wearing the reader's own fields; the OLD binding is history.
        self._fleet_stub()
        legacy_ask = json.dumps({"id": "px-14.mail.TESTHOST-A", "ev": "sent", "from": "api",
                                 "from_id": SID, "to_id": "peer:%s" % self.RHOST,
                                 "toName": "%s:oldworker" % self.RHOST,
                                 "t": T0 + 10, "kind": "question", "body": "the port?"})
        binding = json.dumps({"t": T0 + 5, "ev": "peer-alias", "from_host": self.RHOST,
                              "from": "oldworker", "from_id": self.RSID})
        reply = json.dumps({"id": "rx-14.mail.%s" % self.RHOST, "ev": "sent", "from": "worker",
                            "from_id": self.RSID, "from_host": self.RHOST, "to_id": SID,
                            "t": T0 + 300, "kind": "coordinate", "body": "8080"})
        self._log([binding, legacy_ask, reply])
        km._POSTAL_WAIT_CACHE[0] = None
        self.assertEqual(km._peer_answered_at(SID), T0 + 300,
                         "the logged old-name binding resolves the legacy row to the stable sid")

    def test_a_later_rebinding_never_rekeys_an_answered_legacy_ask(self):
        # the r45 verification: the alias map was last-write-wins per host:name, so a binding
        # logged AFTER the ask re-keyed an already-answered legacy ask onto whatever session now
        # wears the name — a false Awaiting minted with no new information (the flap rule).
        self._fleet_stub()
        other = "99999999-8888-7777-6666-555555555555"
        rows = [json.dumps({"t": T0 + 5, "ev": "peer-alias", "from_host": self.RHOST,
                            "from": "oldworker", "from_id": self.RSID}),
                json.dumps({"id": "px-21.mail.TESTHOST-A", "ev": "sent", "from": "api",
                            "from_id": SID, "to_id": "peer:%s" % self.RHOST,
                            "toName": "%s:oldworker" % self.RHOST,
                            "t": T0 + 10, "kind": "question", "body": "the port?"}),
                json.dumps({"id": "rx-21.mail.%s" % self.RHOST, "ev": "sent", "from": "worker",
                            "from_id": self.RSID, "from_host": self.RHOST, "to_id": SID,
                            "t": T0 + 300, "kind": "coordinate", "body": "8080"}),
                json.dumps({"t": T0 + 400, "ev": "peer-alias", "from_host": self.RHOST,
                            "from": "oldworker", "from_id": other})]   # the name moved on
        self._log(rows)
        km._POSTAL_WAIT_CACHE[0] = None
        self.assertEqual(km._peer_answered_at(SID), T0 + 300,
                         "the ask resolves through the binding active WHEN IT WAS SENT")

    def test_a_phantom_sent_row_never_mints_an_awaiting_edge(self):
        # the v1.3.18 audit: deliver() logs its row before publishing, and a failed publish
        # compensates with an ev:"unpublished" row — the wait maps must honor it
        self._fleet_stub()
        rows = [json.dumps({"id": "ph-1", "ev": "sent", "from": "api", "from_id": SID,
                            "to_id": "eeeeeeee-1111-2222-3333-444444444444",
                            "t": T0 + 10, "kind": "question", "body": "QUESTION: alive?"}),
                json.dumps({"t": T0 + 10, "ev": "unpublished", "id": "ph-1"})]
        self._log(rows)
        km._POSTAL_WAIT_CACHE[0] = None
        last_any, last_ask, last_await = km._postal_wait_maps()
        self.assertEqual(last_await, {}, "the mail never existed — no edge, no debt")

    def test_same_second_rebindings_resolve_in_log_order(self):
        # the v1.3.18 audit: (t, id) tuples tie-broke lexicographically by sid — the bus stamps
        # seq, and the readers order by (t, seq)
        self._fleet_stub()
        other = "ffffffff-9999-8888-7777-666666666666"   # lexicographically ABOVE self.RSID:
        #                                                  the old (t, id) tie-break picks IT
        rows = [json.dumps({"t": T0 + 5, "ev": "peer-alias", "seq": 1, "from_host": self.RHOST,
                            "from": "w", "from_id": other}),
                json.dumps({"t": T0 + 5, "ev": "peer-alias", "seq": 2, "from_host": self.RHOST,
                            "from": "w", "from_id": self.RSID}),
                json.dumps({"id": "px-31.mail.TESTHOST-A", "ev": "sent", "from": "api",
                            "from_id": SID, "to_id": "peer:%s" % self.RHOST,
                            "toName": "%s:w" % self.RHOST,
                            "t": T0 + 10, "kind": "question", "body": "the port?"}),
                json.dumps({"id": "rx-31.mail.%s" % self.RHOST, "ev": "sent", "from": "w",
                            "from_id": self.RSID, "from_host": self.RHOST, "to_id": SID,
                            "t": T0 + 300, "kind": "coordinate", "body": "8080"})]
        self._log(rows)
        km._POSTAL_WAIT_CACHE[0] = None
        self.assertEqual(km._peer_answered_at(SID), T0 + 300,
                         "seq 2 is the binding at ask time — JSONL order, not sid order")

    def test_an_unrelated_peers_reply_never_completes(self):
        self._fleet_stub()
        self._log([self._xrow(1, T0 + 10)])
        jd.run_courier(now=T0 + 100)
        other = json.dumps({"id": "rx-9.mail.TESTHOST-C", "ev": "sent", "from": "tests",
                            "from_id": "dddddddd-0000-1111-2222-333333333333",
                            "from_host": "TESTHOST-C", "to_id": SID, "t": T0 + 300,
                            "kind": "coordinate", "body": "unrelated news"})
        self._log([self._xrow(1, T0 + 10), other])
        jd.run_propagate(now=T0 + 400)
        self.assertFalse(self._handoffs()[0].get("nodeComplete"),
                         "only the delegated peer's own reply is the report-back event")


if __name__ == "__main__":
    unittest.main()
