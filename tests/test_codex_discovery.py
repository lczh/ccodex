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
        p = self._mint()
        rows = jd._discover_impl(NOW)
        cx = [r for r in rows if r[0] == TID]
        self.assertEqual(len(cx), 1)
        fsid, path, anchor, name = cx[0]
        self.assertEqual((fsid, str(path), anchor, name), (TID, str(p), SID, "cx"))

    def test_window_ages_out(self):
        self._mint(mtime=NOW - 10 * 24 * 3600)
        rows = jd._discover_impl(NOW)          # default window is far shorter than 10 days
        self.assertEqual([r for r in rows if r[0] == TID], [])

    def test_registry_change_invalidates_discover_cache(self):
        self._mint()
        first = jd.discover(NOW)
        self.assertEqual(len([r for r in first if r[0] == TID]), 1)
        # a second session lands: only the registry + a new file change — the fingerprint must move
        time.sleep(0.02)
        tid2 = TID.replace("01911111", "01922222")
        self._mint(name="cx2", sid=SID.replace("1111", "2222", 1), tid=tid2)
        # bump the registry mtime past same-second granularity for the stat-based signature
        os.utime(jd.CODEXDIR / "registry.json", (NOW + 5, NOW + 5))
        second = jd.discover(NOW)
        self.assertEqual(len([r for r in second if r[0] == tid2]), 1,
                         "new codex session invisible — fingerprint missed the registry change")

    def test_missing_or_broken_registry_is_harmless(self):
        self.assertEqual(jd._codex_rows(0, set()), [])
        jd.CODEXDIR.mkdir(parents=True, exist_ok=True)
        (jd.CODEXDIR / "registry.json").write_text("{not json")
        self.assertEqual(jd._codex_rows(0, set()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
