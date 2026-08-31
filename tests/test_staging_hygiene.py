"""Every fixed-name staging writer unlinks a possible plant before opening (the r34
verification: a FIFO at a fixed .tmp/.pub name blocks open() forever — in the judge it wedged
the whole triage pass, in bootstrap it held the update flock against boot and every update)."""
import os
import re
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = SourceFileLoader("romp_judge_staging", os.path.join(ROOT, "bin", "romp-judge")).load_module()


class JudgeStagingWriters(unittest.TestCase):
    def test_a_fifo_at_the_death_marker_staging_name_never_hangs_the_triage(self):
        # the r34 verification: kernel._record_death got the unlink-first; this SAME gone/
        # directory's other writer — the judge's — did not, and a plant wedged run_close's
        # worker pool permanently (signals never reach non-main threads)
        import json
        import signal
        with tempfile.TemporaryDirectory() as td:
            gone = Path(td) / "gone"
            gone.mkdir()
            sid = "11111111-2222-3333-4444-555555555555"
            os.mkfifo(str(gone / (sid + ".json.tmp")))
            prev = signal.signal(signal.SIGALRM,
                                 lambda *a: (_ for _ in ()).throw(TimeoutError("hung")))
            signal.alarm(10)
            try:
                with mock.patch.object(jd, "GONEDIR", gone):
                    jd._write_death_marker(sid, {"t": 1, "by": "test"})
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, prev)
            self.assertIn('"by": "test"', (gone / (sid + ".json")).read_text())

    def test_every_judge_fixed_name_staging_writer_unlinks_first(self):
        src = open(os.path.join(ROOT, "kernel", "judge.py")).read()
        for anchor in ('tmp = USAGE.with_name(USAGE.name + ".tmp")',
                       'tmp = JUDGE_AUTH.with_suffix(".tmp")',
                       'tmp = JUDGE_LIMIT.with_suffix(".tmp")',
                       'tmp = path.with_name(path.name + ".mig")',
                       'tmp = GONEDIR / (fsid + ".json.tmp")'):
            i = src.index(anchor)
            window = src[i:i + 420]
            self.assertIn("tmp.unlink(missing_ok=True)", window,
                          "%s stages at a fixed name without unlinking a plant first" % anchor)
            self.assertLess(window.index("tmp.unlink(missing_ok=True)"),
                            window.index("tmp.write_text"), anchor)


class OtherStagingWriters(unittest.TestCase):
    def test_the_postal_quarantine_stager_unlinks_first(self):
        # the staging name derives from the SENDER's message id — a planted FIFO hung the
        # bus thread (the r34 verification)
        src = open(os.path.join(ROOT, "bin", "romp-postal-service")).read()
        i = src.index('tmp = QUARANTINE / (mid + ".tmp")')
        window = src[i:i + 600]   # widened for the r58 fsync'd fd write (durable effect
        #                           before the ack correlates — the O_NOFOLLOW open is the
        #                           symlink half; the unlink-first stays the FIFO half)
        self.assertIn("tmp.unlink(missing_ok=True)", window)
        self.assertLess(window.index("tmp.unlink(missing_ok=True)"),
                        window.index("os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW"))

    def test_the_node_copy_stagers_remove_the_plant_first(self):
        # BOTH node copiers: the login-time one (node-launch) and the install-time twin in
        # romp-service, which r35 missed (the r35 verification — a plant hung `romp refresh`)
        for fn, anchor in (("romp-node-launch", 'cp "$sys_node" "$ROMP_NODE.tmp"'),
                           ("romp-service", 'cp "$src" "$ROMP_NODE.tmp"')):
            src = open(os.path.join(ROOT, "bin", fn)).read()
            i = src.index(anchor)
            self.assertIn('rm -f "$ROMP_NODE.tmp"', src[max(0, i - 400):i],
                          "%s: the copy target is a fixed staging name — rm the plant before "
                          "cp" % fn)


class ElectronStagingWriters(unittest.TestCase):
    def test_the_direct_state_writers_are_gone_entirely(self):
        # the v1.3.13 audit's P2 pinned staging hygiene (unlink-the-plant-first) on the
        # timeline's direct state writers. The v1.3.17 audit's P1.5 removed those writers
        # outright — kernel-down gestures append to the pending-ui-ops spool for the kernel's
        # locked replay, and the Obsidian order went viewer-local — so the STRONGER property
        # holds now: no direct state-file write exists to need hygiene at all.
        src = open(os.path.join(ROOT, "ui", "romp-timeline-view.js")).read()
        for gone in ("'session-order.json'", "'timeline-views.json'", "'session-flags.json'"):
            self.assertNotIn(gone, src,
                             "%s: no direct state-file path may reappear in the timeline — "
                             "gestures ride the kernel routes or the replay spool" % gone)
        self.assertIn("'pending-ui-ops'", src, "the spool dir is the ONLY fallback surface")
        i = src.index("'pending-ui-ops'")
        win = src[max(0, i - 300):i + 900]
        self.assertIn("'pending-ui-ops.stage'", win,
                      "staged in the SIBLING dir (the r46 re-verify: an in-dir tmp raced the "
                      "kernel's sweep and the gesture was silently lost)")
        self.assertIn("renameSync", win,
                      "one atomically-published FILE per op (the v1.3.18 audit's P1: the shared "
                      "append file's rename-aside handoff lost a racing writer's gesture)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
