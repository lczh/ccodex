#!/usr/bin/env python3
"""Segment-identity canary (the user 2026-07-09, the cleared-cards-reappear regression).

Placement keys are `fsid:t:texthash`. If ANY deployed change shifts the derivation (em.segments'
atom-text reconstruction, the hash input, _seg_key normalization), every placement recorded under the
old derivation stops matching, and the planner replays dormant history as junk cards — cleared work
pops back onto the feed with old timestamps. That is exactly what happened on 07-07/07-08: a segment-
text change stepped the hash without a PLACEMENTS_V bump, and each kernel restart re-minted cards the
user had already cleared.

Identity has TWO dimensions, and the fixture exercises both: (1) the id derivation itself (t + text
hash), and (2) WHICH atoms parse out of a transcript at all — the 2026-07-10 absorbed-atom fix grew
the atom set (previously-lost spliced messages became visible) without a bump, and two dormant
sessions replayed their morning history as fresh goals within minutes (planned, done, auto-nudged).
The fixture therefore includes a mid-turn absorbed splice behind a dead enqueue, so its segment is
part of the pinned set.

This canary pins the derived seg ids for a fixed synthetic transcript. If it fails, the derivation
changed: bump jd.PLACEMENTS_V (sealing every older store's ready-unplaced history at the next pass)
and update these pins IN THE SAME commit. See the PLACEMENTS_V comment in bin/romp-judge for the
deploy rule. Synthetic fixtures only."""
import json
import os
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
jd = SourceFileLoader("romp_judge_canary", os.path.join(BIN, "romp-judge")).load_module()
em = SourceFileLoader("romp_em_canary", os.path.join(BIN, "romp-event-model")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
T0 = 1780000000


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": text}, "promptSource": "typed"}


def aline(t, text, uuid, parent, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": stop}}


# One transcript, four segment flavors: a plain typed ask, a romp-injected goal nudge (marker-bearing
# text exercises the authorship/marker-sensitive path), a follow-up typed ask, and a mid-turn ABSORBED
# splice sitting behind a dead enqueue (a resolution the killed CLI never wrote) — the atom the old
# FIFO pairing lost entirely, pinned so the atom SET is part of placement identity too (2026-07-10).
RECORDS = [
    uline(T0, "please add a dark-mode toggle to the settings page", "u1"),
    aline(T0 + 60, "Added the toggle and wired the persistence.", "a1", "u1"),
    uline(T0 + 120, "<!-- romp-injected -->[romp] Status check: is goal g3 finished? <!-- romp-goal-id: g3 -->",
          "u2", "a1"),
    aline(T0 + 180, "Yes, g3 is finished.", "a2", "u2"),
    uline(T0 + 240, "now rename the exported CSV columns to snake_case", "u3", "a2"),
    aline(T0 + 300, "Working on the rename.", "a3", "u3", stop=None),
    {"type": "queue-operation", "timestamp": iso(T0 + 310), "operation": "enqueue",
     "content": "<task-notification>\n<task-id>t000</task-id>\n</task-notification>"},   # dead: never resolved
    {"type": "queue-operation", "timestamp": iso(T0 + 330), "operation": "enqueue", "content": None},
    {"type": "attachment", "timestamp": iso(T0 + 330), "uuid": "att1", "parentUuid": "a3",
     "attachment": {"type": "queued_command", "commandMode": "prompt",
                    "prompt": [{"type": "text", "text": "also gzip the exported CSV"}]}},
    {"type": "queue-operation", "timestamp": iso(T0 + 335), "operation": "remove", "content": None},
    aline(T0 + 360, "Renamed the columns, updated the importer tests, gzipped the export.", "a4", "att1"),
    # a COMPACTION much later (2026-07-13): the boundary opens its OWN turn (the phantom pre-compaction
    # work-bar fix) — pinned here so the compact-turn split is part of placement identity too.
    {"type": "system", "subtype": "compact_boundary", "timestamp": iso(T0 + 4000), "uuid": "cb1",
     "parentUuid": None, "logicalParentUuid": "a4",
     "compactMetadata": {"trigger": "manual", "preTokens": 90000}},
    aline(T0 + 4020, "Resuming after compaction.", "a5", "cb1"),
]

# The pinned derivation, recorded under PLACEMENTS_V = 7 (2026-08-01; the derivation itself is unchanged
# since v6 — v7 seals for a GROWN atom set, the replay-guard scoping. The LAST id — a text-less segment
# — moved off the shared sha1('') hash da39a3ee onto its anchor atom's uuid, so text-less seams no longer
# alias each other; the four text-bearing ids above are unchanged, they still key on content).
EXPECTED_SEG_IDS = [
    SID + ":1780000000:ca8d36fd",
    SID + ":1780000120:f03c5f4f",
    SID + ":1780000240:686c9d66",
    SID + ":1780000330:f3320ed1",
    SID + ":1780004000:d780b71b",
]


class PlacementIdentityCanary(unittest.TestCase):
    def test_seg_id_derivation_is_pinned_to_placements_v(self):
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td) / (SID + ".jsonl")
            tp.write_text("\n".join(json.dumps(r) for r in RECORDS) + "\n")
            sess = jd.parsed_session(SID, [str(tp)], T0 + 4000)
        ids = [seg["id"] for turn in sess["turns"] for seg in em.segments(turn)]
        self.assertEqual(
            ids, EXPECTED_SEG_IDS,
            "\n\nSegment-id derivation CHANGED. Stored placements no longer match, so dormant sessions"
            "\nwill replay their history as junk cards (the 2026-07-09 cleared-cards-reappear bug)."
            "\nIn THIS commit: bump jd.PLACEMENTS_V (seals v(n-1) stores' ready-unplaced units at the"
            "\nnext pass) and re-pin EXPECTED_SEG_IDS to the new derivation. Current PLACEMENTS_V=%d."
            % jd.PLACEMENTS_V)

    def test_placements_v_is_current(self):
        # The pins above were recorded under this version; a bump without re-pinning (or re-pinning
        # without a bump) should both fail loudly.
        # v6 (2026-07-22, the uuid-anchored text-less seg id) shifted the LAST pinned id off da39a3ee;
        # text-bearing ids are unchanged. Pins and version re-recorded together.
        # v8 (2026-08-13, the shape-B twin drop): this fixture carries no command wrappers, so every
        # pinned id is UNCHANGED — the bump seals transcripts that DO carry shape-B commands, whose
        # phantom twin atom drops out of the set.
        self.assertEqual(jd.PLACEMENTS_V, 8, "EXPECTED_SEG_IDS was pinned under PLACEMENTS_V=8 — "
                         "re-pin the ids and this version together, in the same commit")


if __name__ == "__main__":
    unittest.main(verbosity=2)
