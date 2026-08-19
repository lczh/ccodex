#!/usr/bin/env python3
"""A peer's reply lifts the awaiting stamp again — for handoffs, not just questions (2026-08-18).

The 2026-08-15 change that stopped DELEGATES from making chip edges (ownership transferred is not a
dependency) also emptied last_ask of them — which silently removed _peer_answered_at's release, so a
delegated peer's reply no longer superseded a judge kind=peer stamp: the audit found a cross-host
handoff answered in 23 minutes still wearing Awaiting six hours later, with the 6h wake as the only
exit. The release now reads every OUTBOUND from last_any, restoring the designed exact ending event
for questions and handoffs alike, while the chip edge stays question-only exactly as intended.

Also pinned: an UNRESOLVABLE cross-host ask (the peer never sent a row, so the alias map cannot know
its sid) keys on the NAMED recipient — two asks to different sessions on one detached host used to
collapse onto the single (from, relay) pair, the later silently overwriting the earlier (a
29.6h-invisible ask found eaten this way). Synthetic rows only (placeholder ids, TESTHOST)."""
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_awaitrel", os.path.join(BIN, "romp-kernel")).load_module()

A = "11111111-2222-3333-4444-000000000001"
B = "11111111-2222-3333-4444-000000000002"


class AwaitRelease(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._saved = km.jd.MESSAGES
        km.jd.MESSAGES = Path(self.td.name) / "messages.jsonl"
        km._POSTAL_WAIT_CACHE[:] = [None, ({}, {})]

    def tearDown(self):
        km.jd.MESSAGES = self._saved
        km._POSTAL_WAIT_CACHE[:] = [None, ({}, {})]
        self.td.cleanup()

    def _write(self, rows):
        km.jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)
        km.jd.MESSAGES.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        km._POSTAL_WAIT_CACHE[:] = [None, ({}, {})]

    def test_a_delegated_peers_reply_supersedes_the_stamp(self):
        self._write([
            {"from_id": A, "to_id": B, "t": 100, "kind": "delegate", "body": "own this now"},
            {"from_id": B, "to_id": A, "t": 200, "kind": "coordinate", "body": "done, shipped"},
        ])
        last_any, last_ask = km._postal_wait_maps()
        self.assertNotIn((A, B), last_ask, "a DELEGATE still makes no chip edge — ownership transferred")
        self.assertEqual(km._peer_answered_at(A), 200, "…but the reply IS the stamp's ending event again")

    def test_a_reply_older_than_the_newest_outbound_does_not_supersede(self):
        self._write([
            {"from_id": A, "to_id": B, "t": 100, "kind": "delegate", "body": "own this"},
            {"from_id": B, "to_id": A, "t": 150, "kind": "coordinate", "body": "ack"},
            {"from_id": A, "to_id": B, "t": 300, "kind": "coordinate", "body": "one more thing"},
        ])
        self.assertEqual(km._peer_answered_at(A), 0, "the newest outbound is unanswered — nothing supersedes")

    def test_question_release_unchanged(self):
        self._write([
            {"from_id": A, "to_id": B, "t": 100, "kind": "question", "body": "which port?"},
            {"from_id": B, "to_id": A, "t": 180, "kind": "coordinate", "body": "8080"},
        ])
        last_any, last_ask = km._postal_wait_maps()
        self.assertIn((A, B), last_ask, "a QUESTION still makes the chip edge")
        self.assertEqual(km._peer_answered_at(A), 180)

    def test_unresolvable_relay_asks_key_per_named_recipient(self):
        self._write([
            {"from_id": A, "to_id": "peer:TESTHOST", "toName": "TESTHOST:web", "t": 100,
             "kind": "question", "body": "status of the web thing?"},
            {"from_id": A, "to_id": "peer:TESTHOST", "toName": "TESTHOST:api", "t": 120,
             "kind": "question", "body": "status of the api thing?"},
        ])
        _any, last_ask = km._postal_wait_maps()
        self.assertIn((A, "peer:TESTHOST:web"), last_ask, "the earlier ask survives")
        self.assertIn((A, "peer:TESTHOST:api"), last_ask, "…beside the later one — no overwrite")

    def test_the_alias_rekeys_everything_once_the_peer_speaks(self):
        W = "11111111-2222-3333-4444-000000000003"
        self._write([
            {"from_id": A, "to_id": "peer:TESTHOST", "toName": "TESTHOST:web", "t": 100,
             "kind": "question", "body": "status?"},
            {"from_id": W, "to_id": A, "t": 150, "kind": "coordinate", "body": "all good",
             "from_host": "TESTHOST", "from": "web"},
        ])
        _any, last_ask = km._postal_wait_maps()
        self.assertIn((A, W), last_ask, "the maps rebuild from the full log — the row re-keys to the real sid")
        self.assertNotIn((A, "peer:TESTHOST:web"), last_ask)
        self.assertEqual(km._peer_answered_at(A), 150, "and the reply releases the wait")


if __name__ == "__main__":
    unittest.main()
