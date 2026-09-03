#!/usr/bin/env python3
"""The closer caps its RIDERS per call (CLOSE_RIDER_CAP, 2026-09-03) — never the turn's own menu.

The incident: one session's closer calls were alarm-killed 192 times in a row inside ONE
_close_session walk (6h22m during which every judge for every session was silent). The kills were
healthy-but-slow calls, not hangs: served closer duration tracks OUTPUT tokens, and the output size
comes from the menu — _close_turn appended every rider (steps-finished + starved + status + lifted)
with no count cap, 24 riders on most of the backlog, so every reply was a 24-entry JSON plus
unbounded thinking, and did not fit under the alarm. Self-sustaining, because riders retire only
through the closerLookT stamp a LANDED reply writes: a killed call stamps nothing, the identical
menu rides the next turn's call, and dies identically.

The cap is a DRAIN, not a fairness cap (the 2026-06-30 no-fairness-caps stance stands): lifted /
steps-finished / starved riders re-nominate until stamped, so whatever is cut rides a later landed
call (later, not always the next: a verdict a landed call files re-arms its earlier-stamped siblings)
— a finite backlog, the same argument DEATH_DRAIN_PER_PASS makes. STATUS riders are never cut: they
are turn-scoped and one-shot (no watermark), so a cut one would be lost from the very reply it was to
be judged from, not deferred; they take their room off the cap first, and the re-nominating riders
fill what is left.

Synthetic fixtures only: a PRIVATE synthetic sid (goal-minting fixtures never share the placeholder
sid — its override journal is replayed on every load), invented text, hostname TESTHOST."""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = SourceFileLoader("romp_judge_close_riders", os.path.join(BIN, "romp-judge")).load_module()

SID = "33333333-4444-5555-6666-777777777777"      # private synthetic sid — never the shared placeholder
NOW = 1781100000
T0 = NOW - 3600
T1 = T0 + 100                                     # the sibling's filing: newer than every rider's mint


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _uline(t, text, uuid, parent=None, ps="typed"):
    return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": ps, "message": {"role": "user", "content": text}}


def _aline(t, text, uuid, parent):
    return {"type": "assistant", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


def _store():
    return {"rompUuid": SID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
            "placements": {}, "status": {}}


def _node(s, g, text, parent=None, t=T0, done=False, trail=(), log=None):
    nd = {"id": SID + ":" + g, "text": text, "parentId": (SID + ":" + parent) if parent else None,
          "nodeComplete": done, "blocked": False, "cleared": False, "trail": list(trail), "t": t,
          "log": log if log is not None else
                 ([{"ev_t": T1, "src": "closer", "kind": "done", "at": T1}] if done else [])}
    s["nodes"][nd["id"]] = nd
    return nd


def _starved_leaves(s, parent, n, first):
    """n starved-shaped open leaves under `parent` — mint-only trail, empty diary, distinct mints
    (t = T0 + first + i, so oldest-first order is exact) — plus one DONE sibling filed at T1, the
    'other work in the same effort settled' event that nominates them."""
    ids = []
    for i in range(n):
        ids.append(_node(s, "%s-s%d" % (parent, i), "starved step %d of %s" % (i, parent),
                         parent=parent, t=T0 + first + i)["id"])
    _node(s, "%s-done" % parent, "shipped step of %s" % parent, parent=parent, done=True)
    return ids


class _Riders(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))
        jd._PARSE_CACHE.clear()
        self._llm, self._menu_text = jd.closer_llm, jd._menu_text
        self.menus = []                                  # [[node id, ...] per closer call], as sent
        self.stamped_at_call = []                        # ids carrying closerLookT when each call was built
        real = self._menu_text

        def recording(store, menu):
            self.menus.append([nd["id"] for nd in menu])
            self.stamped_at_call.append({nid for nid, nd in store["nodes"].items() if nd.get("closerLookT")})
            return real(store, menu)
        jd._menu_text = recording
        jd.closer_llm = lambda tt, mt, *_a: '{"done": [], "block": []}'   # a LANDED empty verdict

    def tearDown(self):
        jd.closer_llm, jd._menu_text = self._llm, self._menu_text
        try:
            (jd._overrides_dir() / (SID + ".jsonl")).unlink()   # this sid's journal never outlives the test
        except OSError:
            pass
        shutil.rmtree(self.td, ignore_errors=True)


class RiderCap(_Riders):
    def test_riders_are_capped_per_call_and_drain_across_landed_calls(self):
        # 10 starved riders, a transcript with two ended turns touching no goal (n_touched = 0). The
        # first call carries CLOSE_RIDER_CAP riders (the oldest mints); the landed reply stamps exactly
        # those; the second turn's call carries the rest — the backlog drains across landed calls.
        s = _store()
        _node(s, "P", "Ship the notes-api search", trail=["seg-a", "seg-b"])   # reachable normally: not a rider
        leaves = _starved_leaves(s, "P", 10, first=0)
        jd.save_goals(SID, s)
        path = os.path.join(self.td, SID + ".jsonl")
        recs = [_uline(T0, "look at the search index", "u1"),
                _aline(T0 + 30, "Looked; nothing to change.", "a1", "u1"),
                _uline(T0 + 200, "and the tests?", "u2", "a1"),
                _aline(T0 + 230, "Suite green.", "a2", "u2")]
        Path(path).write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        cap = jd.CLOSE_RIDER_CAP
        self.assertLess(cap, 10, "the fixture must exceed the cap or the test is vacuous")
        jd._close_session(SID, path, T0 + 5000)
        self.assertEqual(len(self.menus), 2, "one call per ended turn")
        self.assertLessEqual(len(self.menus[0]), cap)
        self.assertEqual(self.menus[0], leaves[:cap], "the first call carries the CAP oldest riders")
        self.assertEqual(self.stamped_at_call[0], set(), "nothing looked at before the first call")
        self.assertEqual(self.stamped_at_call[1], set(leaves[:cap]),
                         "the landed reply stamped exactly the riders it carried")
        self.assertEqual(self.menus[1], leaves[cap:],
                         "the cut riders ride the next landed call (an empty verdict re-arms nothing)")
        store = jd.load_goals(SID)
        self.assertTrue(all(store["nodes"][i].get("closerLookT") for i in leaves),
                        "two landed calls drained the whole backlog")
        self.assertEqual(len(store.get("closedTurns") or []), 2)

    def test_never_looked_riders_outrank_re_armed_ones_under_the_cap(self):
        # review find 2026-09-03: every landed reply's own filing re-arms the riders an EARLIER call stamped
        # (_filed_since), and those are older-minted — so under plain mint order a backlog past twice the
        # room never drained while the top kept receiving filings: the same six rode every call, the rest
        # never rode once. Here every landed call files one more done under the top (the wrapped
        # apply_close); with 20 riders against a room of CLOSE_RIDER_CAP each call must still admit riders
        # that have never been looked at, so the whole backlog rides within ceil(20 / room) landed calls.
        s = _store()
        _node(s, "P", "Ship the notes-api search", trail=["seg-a", "seg-b"])
        leaves = _starved_leaves(s, "P", 20, first=0)
        jd.save_goals(SID, s)
        n_turns = 6
        recs = []
        for k in range(n_turns):
            t = T0 + 200 * (k + 1)
            recs.append(_uline(t, "step %d please" % k, "u%d" % k, ("a%d" % (k - 1)) if k else None))
            recs.append(_aline(t + 30, "Done with step %d." % k, "a%d" % k, "u%d" % k))
        path = os.path.join(self.td, SID + ".jsonl")
        Path(path).write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        real_apply = jd.apply_close
        filed = [0]

        def apply_and_file(store, menu, verdicts, t=None, **kw):
            out = real_apply(store, menu, verdicts, t=t, **kw)
            filed[0] += 1                                  # the top receives one more filing per landed call
            _node(store, "P-filed%d" % filed[0], "another shipped step", parent="P", done=True,
                  log=[{"ev_t": (t or T0) + 1, "src": "planner", "kind": "done", "at": (t or T0) + 1}])
            return out
        jd.apply_close = apply_and_file
        try:
            jd._close_session(SID, path, T0 + 5000)
        finally:
            jd.apply_close = real_apply
        room = jd.CLOSE_RIDER_CAP
        self.assertEqual(len(self.menus), n_turns, "one landed call per ended turn")
        need = -(-20 // room)                              # ceil(20 / room)
        rode_by_need = set()
        for m in self.menus[:need]:
            rode_by_need |= set(m)
        self.assertTrue(set(leaves) <= rode_by_need,
                        "every rider must ride within %d calls; never rode: %s"
                        % (need, sorted(set(leaves) - rode_by_need)))
        for k in range(1, n_turns):
            self.assertTrue(set(self.menus[k]) & set(leaves), "each call still carries riders (the drain never idles)")

    def test_the_turns_own_menu_is_never_capped(self):
        # the cap is on RIDERS: a turn whose own menu exceeds the cap (a segment placed on the deepest
        # step of a long open chain — _turn_menu lists the node plus every open ancestor) still asks
        # about every one of its goals; only the riders behind them are counted against the cap
        s = _store()
        n = jd.CLOSE_RIDER_CAP + 3
        chain = [_node(s, "c%d" % i, "step %d of the chain" % i, parent=("c%d" % (i - 1)) if i else None,
                       t=T0 + i, trail=["seg-x", "seg-y"])["id"] for i in range(n)]
        leaves = _starved_leaves(s, "c0", 8, first=50)
        path = os.path.join(self.td, SID + ".jsonl")
        Path(path).write_text("\n".join(json.dumps(r) for r in [
            _uline(T0 + 300, "finish the chain", "u1"),
            _aline(T0 + 330, "Chain finished.", "a1", "u1")]) + "\n")
        turn = jd.parsed_session(SID, [path], T0 + 5000)["turns"][0]
        segs = jd._segs(turn, s)
        self.assertTrue(segs)
        s["placements"] = {segs[0]["id"]: chain[-1]}    # the segment landed on the deepest step
        self.assertEqual(len(jd._turn_menu(turn, s)), n, "fixture: the turn's own menu exceeds the cap")
        jd._close_turn(s, turn)
        self.assertEqual(len(self.menus), 1)
        menu = self.menus[0]
        self.assertEqual(set(menu[:n]), set(chain), "every goal of the turn's own menu rides, first")
        self.assertEqual(menu[n:], leaves[:jd.CLOSE_RIDER_CAP],
                         "…and only the riders behind them are capped")


class StatusPriority(_Riders):
    def test_status_riders_take_priority_over_renominating_riders(self):
        # a nudge-triggered turn (the status-report channel) on a board with two open working tops
        # (each with 4 starved leaves + a done sibling) — 8 starved riders + 2 status riders. Status
        # riders are one-shot per status turn; behind 8 re-nominating riders they would be silently
        # starved every status turn, so they are kept first and the starved fill what is left.
        s = _store()
        _node(s, "A", "Ship the notes-api search", trail=["seg-a", "seg-b"])
        _node(s, "B", "Fix the web login flow", t=T0 + 1, trail=["seg-c", "seg-d"])
        la = _starved_leaves(s, "A", 4, first=10)
        lb = _starved_leaves(s, "B", 4, first=20)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / (SID + ".jsonl")
            nudge = "<!-- romp-injected -->Where does each of these stand? <!-- romp-goal-id: %s -->" % (SID + ":A")
            p.write_text("\n".join(json.dumps(r) for r in [
                _uline(T0 + 300, nudge, "n1", ps="sdk"),
                _aline(T0 + 330, "Search shipped; login still in progress.", "a1", "n1")]) + "\n")
            sess = jd.em.parse_session(str(p), rompuuid=SID, candidate_files=[str(p)], now=T0 + 900)
        turn = sess["turns"][0]
        self.assertEqual({nd["id"] for nd in jd._status_report_candidates(s, turn)},
                         {SID + ":A", SID + ":B"}, "fixture: both tops ride the status channel")
        self.assertEqual(len(jd._starved_candidates(s)), 8, "fixture: eight starved riders")
        cap = jd.CLOSE_RIDER_CAP
        self.assertEqual(cap, 6, "the fixture's arithmetic (2 status + 4 starved) is written for cap 6")
        jd._close_turn(s, turn)
        self.assertEqual(len(self.menus), 1)
        menu = self.menus[0]
        self.assertEqual(len(menu), cap)
        self.assertIn(SID + ":A", menu, "a status rider is never cut behind re-nominating riders")
        self.assertIn(SID + ":B", menu)
        self.assertEqual([i for i in menu if i not in (SID + ":A", SID + ":B")], la,
                         "the starved fill the remaining slots, oldest mints first")
        self.assertFalse(any(i in menu for i in lb), "the newer starved wait for a later landed call")
        store = s["nodes"]
        self.assertTrue(all(store[i].get("closerLookT") for i in la), "carried riders are stamped")
        self.assertFalse(any(store[i].get("closerLookT") for i in lb), "cut riders stay armed")

    def test_status_riders_ride_uncapped_and_take_the_room(self):
        # a board with MORE open working tops than the cap: every top rides the status turn — a cut one
        # would never be judged from THIS reply (the one the user asked for) and never deferred, since the
        # channel has no watermark — and the re-nominating riders get no room at all on this call
        s = _store()
        n = jd.CLOSE_RIDER_CAP + 2
        tops = [_node(s, "T%d" % i, "Top-level effort %d" % i, t=T0 + i, trail=["seg-a", "seg-b"])["id"]
                for i in range(n)]
        lt = _starved_leaves(s, "T0", 4, first=50)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / (SID + ".jsonl")
            nudge = "<!-- romp-injected -->Where does each of these stand? <!-- romp-goal-id: %s -->" % tops[0]
            p.write_text("\n".join(json.dumps(r) for r in [
                _uline(T0 + 300, nudge, "n1", ps="sdk"),
                _aline(T0 + 330, "All of them are moving.", "a1", "n1")]) + "\n")
            sess = jd.em.parse_session(str(p), rompuuid=SID, candidate_files=[str(p)], now=T0 + 900)
        turn = sess["turns"][0]
        self.assertEqual(len(jd._status_report_candidates(s, turn)), n, "fixture: every top rides the status channel")
        self.assertEqual(len(jd._starved_candidates(s)), 4, "fixture: four starved riders behind them")
        jd._close_turn(s, turn)
        self.assertEqual(len(self.menus), 1)
        menu = self.menus[0]
        self.assertEqual(set(menu), set(tops), "every status rider rides, past the cap; no starved fits this call")
        self.assertFalse(any(s["nodes"][i].get("closerLookT") for i in lt), "the cut riders stay armed")


class SweepCutDrain(_Riders):
    def test_a_cut_walk_rotates_a_dead_session_to_the_back_of_the_death_drain(self):
        # review find 2026-09-03: a dead session's marker stays pending after a cut walk (its turns are
        # reachable only through the death drain), and _death_pending drains the OLDEST marker first — so
        # a turn whose call dies the same way every pass would hold the head of the queue for good, and
        # DEATH_DRAIN_PER_PASS such sessions would starve every newer dead session. The cut now touches the
        # marker: it waits at the back, one doomed call per pass, and is never finalized off the cut walk.
        s = _store()
        _node(s, "P", "Ship the notes-api search", trail=["seg-a", "seg-b"])
        _starved_leaves(s, "P", 3, first=0)
        jd.save_goals(SID, s)
        path = os.path.join(self.td, SID + ".jsonl")
        recs = [_uline(T0, "look at the search index", "u1"),
                _aline(T0 + 30, "Looked; nothing to change.", "a1", "u1")]
        Path(path).write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        jd.GONEDIR.mkdir(parents=True, exist_ok=True)
        jd._write_death_marker(SID, {"t": T0 + 100})
        marker = jd.GONEDIR / (SID + ".json")
        os.utime(marker, (T0, T0))                         # the oldest marker in the queue
        before = marker.stat().st_mtime_ns

        def dead_call(*a, **k):                           # the call died: _judge_run's failure traces, no reply
            jd._judge_ctx.last_call_fail = {"note": "exit -14", "model": "sonnet"}
            return ""
        jd.closer_llm = dead_call
        jd._judge_ctx.paused = False
        jd._close_session(SID, path, T0 + 5000)
        self.assertGreater(marker.stat().st_mtime_ns, before, "the cut rotated the marker to the back of the queue")
        m = json.loads(marker.read_text())
        self.assertNotIn("endedAt", m, "a cut walk never finalizes the marker (its turns are still unswept)")
        rows = [json.loads(l) for l in open(jd.ERRORS) if l.strip()]
        self.assertTrue(any(r.get("err") == "sweep-cut" for r in rows), "the cut is said loudly")


if __name__ == "__main__":
    unittest.main()
