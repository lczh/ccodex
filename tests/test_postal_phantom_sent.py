#!/usr/bin/env python3
"""Phantom "sent" rows (the v1.3.20 audit's P2). deliver() logs ev:"sent" BEFORE the maildir
rename publishes (deliberate — receipts must never be lost) and compensates a failed publish
with ev:"unpublished"; but _tl_append is best-effort, so publish AND compensation could both
fail and leave a durable sent row for mail that never existed — receipts/awaiting state then
minted from a phantom. The fix: (a) the compensating append retries once and failure is said
out loud on stderr; (b) bus boot reconciles the log's TAIL (bounded) against the maildir — the
authoritative store — filing the missing "unpublished" row for any recent local sent row with
no terminal row and no file in tmp/new/cur; (c) _sent_receipts honors "unpublished" so the
sender never sees a phantom as pending. Synthetic data only per CLAUDE.md.

Run:    python3 tests/test_postal_phantom_sent.py
"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
# the sessions seam: no live kernel — local_agents reads this file, _kernel_post no-ops
_SESSIONS = os.path.join(tempfile.mkdtemp(), "sessions.json")
with open(_SESSIONS, "w") as _fh:
    _fh.write("[]")
os.environ["ROMP_SESSIONS_FILE"] = _SESSIONS
pm = SourceFileLoader("romp_postal_phantom", os.path.join(BIN, "romp-postal-service")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
SID2 = "66666666-7777-8888-9999-000000000000"


def _rows():
    log = pm.TLDIR / "messages.jsonl"
    if not log.exists():
        return []
    return [json.loads(l) for l in log.read_text().splitlines() if l.strip()]


def _reset():
    shutil.rmtree(pm.MAILROOT, ignore_errors=True)
    pm.TLDIR.mkdir(parents=True, exist_ok=True)
    (pm.TLDIR / "messages.jsonl").write_text("")


@unittest.skipIf(os.geteuid() == 0, "read-only dir does not block rename for root")
class FailedPublishCompensation(unittest.TestCase):
    """The immediate path: a failed publish still compensates, and a failed compensation is
    retried once and reported loudly instead of swallowed."""

    def setUp(self):
        _reset()
        self.saved_append = pm._tl_append
        mb = pm._mailbox(SID)
        os.chmod(mb / "new", 0o555)                     # the publish rename will fail (EACCES)
        self.addCleanup(os.chmod, mb / "new", 0o755)

    def tearDown(self):
        pm._tl_append = self.saved_append

    def test_failed_publish_logs_the_compensating_row(self):
        with self.assertRaises(Exception):
            pm.deliver(SID, "web", "uuid-a", "synthetic body")
        rows = _rows()
        self.assertEqual(rows[-2]["ev"], "sent")
        self.assertEqual(rows[-1]["ev"], "unpublished")
        self.assertEqual(rows[-1]["id"], rows[-2]["id"],
                         "the compensation names the phantom's own id")

    def test_failed_compensation_retries_once_and_says_so(self):
        attempts = []
        real = self.saved_append

        def flaky(fname, obj):
            if obj.get("ev") == "unpublished":
                attempts.append(dict(obj))
                return False                            # the compensating append fails too
            return real(fname, obj)

        pm._tl_append = flaky
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(Exception):
                pm.deliver(SID, "web", "uuid-a", "synthetic body")
        self.assertEqual(len(attempts), 2, "a failed compensating append is retried once")
        self.assertIn("unpublished", err.getvalue(),
                      "both appends failing is said OUT LOUD, never swallowed")
        self.assertIn(attempts[0]["id"], err.getvalue(), "…naming the phantom row's id")


class BootReconciliation(unittest.TestCase):
    """The durable backstop: bus boot files the missing "unpublished" row for a recent local
    sent row whose mail exists nowhere in the maildir — and touches nothing that exists."""

    def setUp(self):
        _reset()
        self.now = int(time.time())

    def _sent(self, mid, to_id=SID, t=None, **extra):
        row = {"t": self.now if t is None else t, "ev": "sent", "id": mid,
               "from": "web", "from_id": "uuid-a", "to_id": to_id, "body": "synthetic"}
        row.update(extra)
        self.assertTrue(pm._tl_append("messages.jsonl", row))
        return row

    def test_phantom_sent_row_gets_its_unpublished_row(self):
        self._sent("100.1_11111.TESTHOST")
        self.assertEqual(pm._reconcile_phantom_sent(), 1)
        last = _rows()[-1]
        self.assertEqual((last["ev"], last["id"]), ("unpublished", "100.1_11111.TESTHOST"))
        self.assertEqual(pm._reconcile_phantom_sent(), 0,
                         "idempotent: the filed row is itself terminal")

    def test_unread_and_parked_mail_are_never_touched(self):
        pm.deliver(SID, "web", "uuid-a", "unread synthetic mail")
        pm.deliver(SID2, "web", "uuid-a", "parked synthetic handoff", park=True)
        self.assertEqual(pm._reconcile_phantom_sent(), 0)
        self.assertEqual([r["ev"] for r in _rows()], ["sent", "sent"],
                         "no unpublished row for mail that sits in new/")

    def test_consumed_mail_is_recognized_by_its_cur_file_alone(self):
        mid = pm.deliver(SID, "web", "uuid-a", "consumed synthetic mail")
        pm.read_box(SID, consume=True)                  # new/ -> cur/, appends ev:"exec"
        # strip the log to JUST the sent row so only the maildir check can protect it
        (pm.TLDIR / "messages.jsonl").write_text(
            json.dumps({"t": self.now, "ev": "sent", "id": mid, "from": "web",
                        "from_id": "uuid-a", "to_id": SID, "body": "synthetic"}) + "\n")
        self.assertEqual(pm._reconcile_phantom_sent(), 0,
                         "a file in cur/ means the mail existed — not a phantom")

    def test_a_crashed_mid_write_tmp_file_IS_a_phantom(self):
        # REVERSED by the r48 verification: counting tmp/ as "mail exists" made every real
        # failed publish permanent — the exact case the reconcile exists for leaves its file
        # in tmp/. This runs at BOOT (no concurrent writer), so tmp-only is unpublished by
        # definition: a failed rename or a crash mid-write, phantom either way.
        mid = "101.1_22222.TESTHOST"
        mb = pm._mailbox(SID)
        (mb / "tmp" / mid).write_text("From: web\n\nsynthetic half-written mail\n")
        self._sent(mid)
        self.assertEqual(pm._reconcile_phantom_sent(), 1,
                         "tmp-only at boot files the phantom — published means new/ or cur/")

    def test_terminal_rows_settle_the_id(self):
        for ev in ("exec", "unexec", "unpublished", "recall", "bounced"):
            mid = "102.1_3%s.TESTHOST" % ev
            self._sent(mid)
            pm._tl_append("messages.jsonl", {"t": self.now, "ev": ev, "id": mid})
        self.assertEqual(pm._reconcile_phantom_sent(), 0)

    def test_peer_relay_rows_are_skipped(self):
        # cross-host mail lives in the OUTBOX, never a local maildir — its sent row is not
        # a phantom however empty the maildir is
        self._sent("px-103.1_44444.TESTHOST", to_id="peer:TESTHOST2",
                   toName="TESTHOST2:api", relay_mid="", relay_via="")
        self.assertEqual(pm._reconcile_phantom_sent(), 0)

    def test_the_scan_is_time_bounded(self):
        self._sent("104.1_55555.TESTHOST", t=self.now - pm.PHANTOM_WINDOW - 3600)
        self.assertEqual(pm._reconcile_phantom_sent(), 0,
                         "rows older than the window are history, not work")

    def test_the_scan_is_byte_bounded(self):
        # a recent phantom buried MORE than PHANTOM_TAIL_BYTES before EOF is outside the
        # bounded tail — never rescanned (the r45 verification forbids full-log scans)
        self._sent("105.1_66666.TESTHOST")
        filler = json.dumps({"t": self.now, "ev": "noise", "id": "n", "pad": "z" * 900})
        with open(pm.TLDIR / "messages.jsonl", "a") as fh:
            for _ in range(pm.PHANTOM_TAIL_BYTES // len(filler) + 50):
                fh.write(filler + "\n")
        self.assertEqual(pm._reconcile_phantom_sent(), 0)

    def test_serve_wires_reconciliation_before_serving(self):
        src = open(os.path.join(BIN, "romp-postal-service"), encoding="utf-8").read()
        body = src.split("def serve():", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_reconcile_phantom_sent()", body, "boot reconciliation runs at startup")
        self.assertLess(body.index("_reconcile_phantom_sent()"), body.index("serve_forever"),
                        "…before the bus serves requests")


class SentReceiptsHonorUnpublished(unittest.TestCase):
    """The sender-facing awaiting state: a phantom whose unpublished row has been filed must
    not show as a forever-pending receipt in `romp mail sent`."""

    def setUp(self):
        _reset()
        self.now = int(time.time())

    def test_unpublished_removes_the_awaiting_receipt(self):
        pm._tl_append("messages.jsonl", {"t": self.now, "ev": "sent", "id": "106.1_7.TESTHOST",
                                         "from": "web", "from_id": "uuid-me", "to_id": SID,
                                         "body": "synthetic"})
        self.assertEqual(len(pm._sent_receipts("uuid-me")), 1, "control: pending until terminal")
        pm._tl_append("messages.jsonl", {"t": self.now, "ev": "unpublished",
                                         "id": "106.1_7.TESTHOST"})
        self.assertEqual(pm._sent_receipts("uuid-me"), [],
                         "the mail never existed — nothing to await")



class TmpOnlyIsUnpublished(unittest.TestCase):
    """the r48 verification: a FAILED publish leaves its file in tmp/ — and the reconcile
    counted tmp/ as 'mail exists', so the exact case it exists for never filed. tmp-only at
    boot is unpublished by definition (failed rename or crash mid-write, phantom either way)."""

    def setUp(self):
        _reset()

    def test_a_tmp_only_file_still_files_the_phantom(self):
        mid = "100.1_1.TESTHOST"
        pm._mailbox(SID)
        (pm.MAILROOT / SID / "tmp" / mid).write_text("From: x\n\nbody\n")   # the failed publish
        pm._tl_append("messages.jsonl", {"t": int(time.time()), "ev": "sent", "id": mid,
                                         "from": "web", "from_id": "f" * 8, "to_id": SID,
                                         "body": "hello"})
        n = pm._reconcile_phantom_sent()
        self.assertEqual(n, 1, "a tmp-only file is UNPUBLISHED — the phantom files")
        self.assertEqual(_rows()[-1]["ev"], "unpublished")
        self.assertEqual(_rows()[-1]["id"], mid)

    def test_published_mail_still_never_files(self):
        mid = "101.1_1.TESTHOST"
        pm._mailbox(SID)
        (pm.MAILROOT / SID / "new" / mid).write_text("From: x\n\nbody\n")
        pm._tl_append("messages.jsonl", {"t": int(time.time()), "ev": "sent", "id": mid,
                                         "from": "web", "from_id": "f" * 8, "to_id": SID,
                                         "body": "hello"})
        self.assertEqual(pm._reconcile_phantom_sent(), 0, "new/ mail is real — no phantom")

    def test_a_failed_publish_leaves_no_tmp_litter(self):
        # deliver() now unlinks its tmp file when the publish rename fails (r48)
        if os.geteuid() == 0:
            self.skipTest("read-only dir does not block rename for root")
        mb = pm._mailbox(SID)
        os.chmod(mb / "new", 0o555)
        self.addCleanup(os.chmod, mb / "new", 0o755)
        with self.assertRaises(Exception):
            pm.deliver(SID, "web", "uuid-a", "synthetic body")
        self.assertEqual(list((mb / "tmp").iterdir()), [],
                         "the failed publish cleaned its tmp file")


if __name__ == "__main__":
    unittest.main()
