#!/usr/bin/env python3
"""Migration fingerprints for unreadable shared files (the v1.3.20 audit's P2). _mig_shared_fps
mapped EVERY OSError to None — the same value that means "absent" — so a certificate issued
while a keyed file was missing also accepted an unreadable or non-regular replacement (a FIFO,
a symlink, an EACCES file) as settled, and the migration never re-ran. The fix: only
FileNotFoundError means absent; any other failure fingerprints as a never-match sentinel
("unreadable:" + fresh randomness), which voids the settle compare in both directions, and the
certificate writer refuses to store a sentinel. Synthetic data throughout per CLAUDE.md — a
PRIVATE synthetic sid (goal-store fixture rule, 2026-08-24), never the shared placeholder.

Run:    python3 tests/test_mig_fps_unreadable.py
"""
import json
import os
import tempfile
import unittest
from unittest import mock
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
jd = SourceFileLoader("romp_judge_migfps", os.path.join(ROOT, "bin", "romp-judge")).load_module()

# a PRIVATE synthetic sid/tid pair: goal-minting tests must never share the 11111111-2222-…
# placeholder sid (other modules' journaled overrides replay onto colliding node ids)
SID = "aaaa1111-bbbb-4ccc-8ddd-eeee22223333"
TID = "0191aaaa-bbbb-7ccc-8ddd-eeee22223333"


class UnreadableFingerprint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(self.tmp)
        for d in (jd.GOALDIR, jd.GOALARCHDIR, jd.CAPDIR, jd.ARCHDIR, jd.EPIDIR, jd.CODEXDIR):
            d.mkdir(parents=True, exist_ok=True)
        (jd.CODEXDIR / "registry.json").write_text(json.dumps(
            {SID: {"tid": TID, "name": "cx", "cwd": "/TESTDIR", "dead": False}}))
        (jd.GOALDIR / (TID + ".json")).write_text(json.dumps(
            {"rompUuid": TID, "nodes": {TID + ":g1": {"t": 1}}, "status": {},
             "placements": {}}))
        self.marker = jd.CODEXDIR / "migrated" / "shared.state"

    def tearDown(self):
        ov = jd._overrides_dir()
        for sid in (SID, TID):
            try:
                (ov / (sid + ".jsonl")).unlink()
            except OSError:
                pass

    def _counting_migrate(self):
        calls = []
        real = jd._migrate_shared_identity_files

        def counting(*a, **kw):
            calls.append(1)
            return real(*a, **kw)

        with mock.patch.object(jd, "_migrate_shared_identity_files", side_effect=counting):
            jd.migrate_codex_identity()
        return len(calls)

    def test_unreadable_fingerprint_never_matches_anything(self):
        os.mkfifo(jd.STATE / "session-flags.json")
        a = jd._mig_shared_fps()["session-flags.json"]
        b = jd._mig_shared_fps()["session-flags.json"]
        self.assertIsNotNone(a, "unreadable is NOT absence")
        self.assertTrue(a.startswith(jd._MIG_FP_UNREADABLE))
        self.assertNotEqual(a, b, "fresh randomness per draw — even a stored sentinel "
                                  "can never be matched by a later one")
        self.assertIsNone(jd._mig_shared_fps()["session-order.json"],
                          "a genuinely missing file still fingerprints as None")

    def test_fifo_replacement_of_a_missing_file_voids_the_certificate(self):
        # the audited hole, exactly: certify while the keyed file is ABSENT (fingerprint
        # None), then replace it with a non-regular file — OSError-as-None matched the
        # certified absence, settled, and migration never re-ran over the replacement
        jd.migrate_codex_identity()
        self.assertTrue(self.marker.exists())
        rec = json.loads(self.marker.read_text())
        self.assertIsNone(rec["files"]["session-flags.json"], "certified as absent")
        os.mkfifo(jd.STATE / "session-flags.json")
        self.assertEqual(self._counting_migrate(), 1,
                         "an unreadable replacement re-runs the shared pass")
        self.assertFalse(self.marker.exists(),
                         "…and the failed pass retracts the certificate")

    @unittest.skipIf(os.geteuid() == 0, "chmod 000 does not block reads for root")
    def test_unreadable_permissions_replacement_voids_the_certificate(self):
        # same hole, EACCES flavor: the replacement is a regular file that cannot be read
        jd.migrate_codex_identity()
        self.assertTrue(self.marker.exists())
        p = jd.STATE / "session-flags.json"
        p.write_text(json.dumps({TID: {"hideFromFeed": True}}))
        os.chmod(p, 0)
        self.addCleanup(os.chmod, p, 0o600)
        self.assertEqual(self._counting_migrate(), 1,
                         "an EACCES replacement re-runs the shared pass")
        self.assertFalse(self.marker.exists())

    def test_certified_content_replaced_by_fifo_reruns_and_retracts(self):
        # the other direction: the certificate holds a REAL hash and the file turns
        # non-regular — the sentinel mismatches that too, so the pass re-runs and fails
        # loudly instead of certifying
        (jd.STATE / "session-flags.json").write_text(json.dumps({TID: {"hideFromFeed": True}}))
        jd.migrate_codex_identity()
        self.assertTrue(self.marker.exists())
        flags = json.loads((jd.STATE / "session-flags.json").read_text())
        self.assertIn(SID, flags, "control: the pass canonicalized the readable file")
        os.unlink(jd.STATE / "session-flags.json")
        os.mkfifo(jd.STATE / "session-flags.json")
        self.assertEqual(self._counting_migrate(), 1)
        self.assertFalse(self.marker.exists())

    def test_a_sentinel_is_never_written_into_a_certificate(self):
        # the write-moment backstop: even if a pass reports clean while a keyed file is
        # unreadable (a file turning unreadable between the fps snapshot and the write),
        # the certificate must not land carrying a sentinel
        jd.migrate_codex_identity()
        self.assertTrue(self.marker.exists())
        os.mkfifo(jd.STATE / "session-order.json")
        with mock.patch.object(jd, "_migrate_shared_identity_files", return_value=(True, {})):
            jd.migrate_codex_identity()
        if self.marker.exists():
            rec = json.loads(self.marker.read_text())
            bad = [n for n, v in (rec.get("files") or {}).items()
                   if isinstance(v, str) and v.startswith(jd._MIG_FP_UNREADABLE)]
            self.assertEqual(bad, [], "a certificate never certifies an unreadable file")

    def test_recovered_file_recertifies(self):
        # the sentinel forces re-runs only while the file is broken: once readable again,
        # the pass canonicalizes it and the certificate lands — no permanent re-run loop
        jd.migrate_codex_identity()
        os.mkfifo(jd.STATE / "session-flags.json")
        jd.migrate_codex_identity()
        self.assertFalse(self.marker.exists())
        os.unlink(jd.STATE / "session-flags.json")
        (jd.STATE / "session-flags.json").write_text(json.dumps({TID: {"hideFromFeed": True}}))
        jd.migrate_codex_identity()
        self.assertTrue(self.marker.exists(), "the repaired file certifies")
        flags = json.loads((jd.STATE / "session-flags.json").read_text())
        self.assertIn(SID, flags)
        self.assertNotIn(TID, flags, "…and its TID rows were migrated, nothing skipped as settled")


if __name__ == "__main__":
    unittest.main()
