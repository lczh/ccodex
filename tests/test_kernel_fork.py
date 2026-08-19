#!/usr/bin/env python3
"""Fork a session (the user 2026-08-13): a NEW parallel session branches from the parent's conversation —
from just before a chosen user message (the chat's fork button; the same _rewind_target resolution the
edit/delete rewind uses) or from the tip — and the parent is untouched.

The load-bearing half is STORE HYGIENE: the fork's transcript carries the parent's history VERBATIM
(uuids + timestamps survive the CLI's fork copy — verified live 2026-08-13), and an unseeded new sid
would re-judge all of it as fresh work (the v3 replay storm: every copied prompt re-minted as a card,
every turn re-captioned). _seed_fork_stores runs BEFORE the session becomes discoverable and writes:
  1. an episode seed+boundary → episode_floor(new) = the cut record's t (the planner's retire gate);
  2. a transform-copy of the parent's captions (no Haiku re-caption storm);
  3. a goal store born with the parent's placements SEALED (value None) — the courier's cover.
run_courier additionally grew its own episode-floor guard: a fork's copied history is the first shape
that leaves OLD peer segments visible to it (a /clear's null-rooted head drops them from the parse).

SYNTHETIC fixtures only (placeholder UUIDs, invented text)."""
import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
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
km = SourceFileLoader("romp_kernel_fork", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd

PARENT = "11111111-2222-3333-4444-555555555555"
NEWSID = "99999999-8888-7777-6666-555555555555"
T_U1, T_A1, T_U2, T_A2 = 1781100000, 1781100010, 1781100100, 1781100110


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": text}, "promptSource": "typed"}


def aline(t, text, uuid, parent):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


def write_transcript(path):
    recs = [uline(T_U1, "set up the api", "u1"),
            aline(T_A1, "done — the api is up", "a1", "u1"),
            uline(T_U2, "now add the tests", "u2", "a1"),
            aline(T_A2, "tests added", "a2", "u2")]
    Path(path).write_text("\n".join(json.dumps(r) for r in recs) + "\n")


class ForkStoreSeeding(unittest.TestCase):
    """_seed_fork_stores: the three writes, keyed to the cut."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.td.name, PARENT + ".jsonl")
        write_transcript(self.path)

    def tearDown(self):
        for d in (jd.EPIDIR, jd.CAPDIR, jd.GOALDIR):
            for f in Path(d).glob("*"):
                f.unlink()
        self.td.cleanup()

    def test_episode_floor_lands_on_the_cut(self):
        err = km._seed_fork_stores(PARENT, NEWSID, self.path, "a1")   # fork from before u2
        self.assertIsNone(err)
        rows = jd.episode_rows(NEWSID)
        self.assertEqual([r["head"] for r in rows], ["u1", "a1"],
                         "seed = the conversation ROOT (dedups the boundary tick's sighting); boundary = the cut")
        self.assertEqual(rows[0]["fsid"], PARENT, "the seed names the file the fork copies from")
        self.assertEqual(rows[1]["fsid"], NEWSID, "the boundary is the fork's own (pinned) fsid")
        self.assertEqual(jd.episode_floor(NEWSID), T_A1,
                         "floor = the cut record's t — the planner retires every copied unit below it")

    def test_fork_from_tip_floors_at_the_leaf(self):
        self.assertIsNone(km._seed_fork_stores(PARENT, NEWSID, self.path, ""))
        self.assertEqual(jd.episode_floor(NEWSID), T_A2)

    def test_captions_transform_copy(self):
        jd.CAPDIR.mkdir(parents=True, exist_ok=True)
        (jd.CAPDIR / (PARENT + ".jsonl")).write_text("\n".join([
            json.dumps({"id": "%s:%d:aaaa" % (PARENT, T_U1), "cap": "set up the api"}),
            json.dumps({"id": "%s:%d:bbbb" % (PARENT, T_U2), "cap": "add the tests"}),   # after the cut
            json.dumps({"id": "%s:%d:cccc" % (PARENT, T_U1), "cap": "working…", "live": True}),
            json.dumps({"id": "othersid:%d:dddd" % T_U1, "cap": "not ours"}),
        ]) + "\n")
        self.assertIsNone(km._seed_fork_stores(PARENT, NEWSID, self.path, "a1"))
        got = [json.loads(l) for l in (jd.CAPDIR / (NEWSID + ".jsonl")).read_text().splitlines()]
        self.assertEqual([o["id"] for o in got], ["%s:%d:aaaa" % (NEWSID, T_U1)],
                         "kept: settled rows at/before the cut, resid'd; dropped: post-cut, live, foreign")
        self.assertEqual(got[0]["cap"], "set up the api")

    def test_placements_arrive_sealed_and_no_nodes_copy(self):
        pstore = jd.load_goals(PARENT)
        pstore["placements"] = {"%s:%d:aaaa" % (PARENT, T_U1): "g1",
                                "%s:%d:bbbb#p" % (PARENT, T_U2): None}
        pstore["nodes"] = {"%s:g1" % PARENT: {"text": "a card the fork must NOT inherit"}}
        jd.save_goals(PARENT, pstore)
        self.assertIsNone(km._seed_fork_stores(PARENT, NEWSID, self.path, "a1"))
        store = jd.load_goals(NEWSID)
        self.assertEqual(store["placements"],
                         {"%s:%d:aaaa" % (NEWSID, T_U1): None, "%s:%d:bbbb#p" % (NEWSID, T_U2): None},
                         "every parent placement arrives resid'd and SEALED (processed, no goal)")
        self.assertEqual(store["nodes"], {}, "no cards copy — they would duplicate across both boards")

    def test_empty_transcript_fails_loudly(self):
        empty = os.path.join(self.td.name, "empty.jsonl")
        Path(empty).write_text("")
        self.assertIn("nothing to fork", km._seed_fork_stores(PARENT, NEWSID, empty, "") or "")


class _FakeForkBackend:
    def __init__(self):
        self.events = []

    def fork(self, name, parent_sid, cut_uuid="", bg="", fg="", sid=None):
        self.events.append(("fork", name, parent_sid, cut_uuid, sid))
        return sid

    def connect(self, sid):
        self.events.append(("connect", sid))


class ForkSessionOp(unittest.TestCase):
    """_fork_session: validation, the rewind-family cut resolution, and seeding BEFORE the backend."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.td.name, PARENT + ".jsonl")
        write_transcript(self.path)
        self.be = _FakeForkBackend()
        self.saved = (km.Sessions.backend_for, km._sdk_ready, km._sessions, km._pick_identity_color,
                      km._reveal_chat_for, km._mark_views_dirty, km._push_session_now, km._seed_fork_stores)
        km.Sessions.backend_for = lambda sid: self.be
        km._sdk_ready = lambda: True
        km._sessions = lambda now: [{"sid": PARENT, "path": self.path}]
        km._pick_identity_color = lambda: ("#123456", "#ffffff")
        km._reveal_chat_for = lambda c, m: None
        km._mark_views_dirty = lambda: None
        km._push_session_now = lambda sid: None
        self._real_seed = km._seed_fork_stores
        km._seed_fork_stores = lambda *a: self.be.events.append(("seed",) + a[:2]) or None

    def tearDown(self):
        (km.Sessions.backend_for, km._sdk_ready, km._sessions, km._pick_identity_color,
         km._reveal_chat_for, km._mark_views_dirty, km._push_session_now, km._seed_fork_stores) = self.saved
        for d in (jd.EPIDIR, jd.CAPDIR, jd.GOALDIR):
            for f in Path(d).glob("*"):
                f.unlink()
        self.td.cleanup()

    def test_bad_name_refused(self):
        self.assertIn("letters, digits", km._fork_session(PARENT, "", "bad name!") or "")
        self.assertEqual(self.be.events, [])

    def test_tmux_backend_refused(self):
        km.Sessions.backend_for = lambda sid: object()   # no .fork
        self.assertIn("SDK backend", km._fork_session(PARENT, "", "api-fork") or "")

    def test_seeding_precedes_the_backend_and_the_cut_resolves_like_a_rewind(self):
        self.assertIsNone(km._fork_session(PARENT, "u2", "api-fork"))
        kinds = [e[0] for e in self.be.events]
        self.assertEqual(kinds, ["seed", "fork", "connect"],
                         "stores are seeded BEFORE the names/ entry exists (discoverability)")
        fork = next(e for e in self.be.events if e[0] == "fork")
        self.assertEqual(fork[3], "a1", "forking 'before u2' cuts at its nearest message ancestor — "
                                        "the same _rewind_target the edit/delete rewind uses")
        self.assertEqual(fork[4], self.be.events[0][2], "the backend gets the sid the seeds were written for")

    def test_fork_from_tip_passes_no_cut(self):
        self.assertIsNone(km._fork_session(PARENT, "", "api-fork"))
        fork = next(e for e in self.be.events if e[0] == "fork")
        self.assertEqual(fork[3], "", "no message uuid → the whole conversation")

    def test_seed_failure_aborts_the_fork(self):
        km._seed_fork_stores = lambda *a: "fork not created — seeding its judge state failed: boom"
        self.assertIn("seeding", km._fork_session(PARENT, "", "api-fork") or "")
        self.assertEqual([e[0] for e in self.be.events], [],
                         "an unprotected fork must never be created (the replay storm)")


class CourierEpisodeFloor(unittest.TestCase):
    """run_courier skips segments below the episode floor — a fork's copied history is the first
    shape that leaves OLD peer segments visible to it (mirrors the planner's pre-episode retire)."""

    RECIP = "11111111-2222-3333-4444-555555555555"
    SENDER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    MID = "1781100000.11111_22222.TESTHOST"
    T0 = 1781100000

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        munged = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        (proj / munged).mkdir(parents=True)
        self.proj_dir = proj / munged
        names = td / "names"; names.mkdir()
        (names / self.RECIP).write_text("recip\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.CAPDIR, jd.ARCHDIR, jd.PCACHE,
                      jd.MESSAGES, jd.ERRORS, jd.EPIDIR, jd.courier_llm)
        jd.NAMES, jd.PROJECTS = names, proj
        jd.GOALDIR = td / "goals"
        jd.CAPDIR, jd.ARCHDIR, jd.PCACHE = td / "captions", td / "archive", td / "pcache"
        jd.EPIDIR = td / "episodes"
        jd.MESSAGES = td / "messages.jsonl"
        jd.ERRORS = td / "judge-errors.jsonl"
        jd.MESSAGES.write_text(json.dumps(
            {"t": self.T0 - 5, "ev": "sent", "id": self.MID, "from": "sender", "from_id": self.SENDER,
             "to_id": self.RECIP, "body": "what subnet is the new box on?"}) + "\n")
        self.calls = []
        jd.courier_llm = lambda *a, **k: self.calls.append(1) or '{"verdict": "delegating", "goal": 0, "text": "x"}'
        jd._PARSE_CACHE.clear()
        jd._discover_cache.clear()
        jd._postal_from_memo["key"] = None
        # the marker rides the comment-wrapped wire form (see test_courier_origin_host)
        recs = [uline(self.T0, "what subnet is the new box on?\n<!-- romp-msg-id: %s -->" % self.MID, "u1"),
                aline(self.T0 + 30, "It's on the flat /24.", "a1", "u1")]
        (self.proj_dir / (self.RECIP + ".jsonl")).write_text(
            "\n".join(json.dumps(r) for r in recs) + "\n")

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.CAPDIR, jd.ARCHDIR, jd.PCACHE,
         jd.MESSAGES, jd.ERRORS, jd.EPIDIR, jd.courier_llm) = self.saved
        jd._postal_from_memo["key"] = None
        jd._PARSE_CACHE.clear()
        jd._discover_cache.clear()
        self.td.cleanup()

    def test_pre_floor_peer_segment_is_skipped(self):
        jd.append_episode(self.RECIP, "u0", "oldfsid", self.T0 - 100)   # seed
        jd.append_episode(self.RECIP, "cut", self.RECIP, self.T0 + 50)  # boundary → floor ABOVE the message
        jd.run_courier(now=self.T0 + 100)
        store = jd.load_goals(self.RECIP)
        self.assertEqual(store["nodes"], {}, "nothing planted from pre-episode history")
        self.assertEqual(store["placements"], {}, "not even placed — skipped outright, no model call")
        self.assertEqual(self.calls, [])

    def test_current_episode_peer_segment_still_plants(self):
        jd.append_episode(self.RECIP, "u0", "oldfsid", self.T0 - 100)
        jd.append_episode(self.RECIP, "cut", self.RECIP, self.T0 - 50)  # floor BELOW the message
        jd.run_courier(now=self.T0 + 100)
        store = jd.load_goals(self.RECIP)
        self.assertEqual(len(self.calls), 1, "an in-episode delegation still reaches the courier model")
        self.assertTrue(store["placements"], "and gets placed")


if __name__ == "__main__":
    unittest.main()
