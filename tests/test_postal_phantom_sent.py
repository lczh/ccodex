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


class R50RelayStages(unittest.TestCase):
    """the v1.3.22 audit's P2.5: the peer-relay leg wrote its sent row and then outbox_put —
    a crash (or a raise) in the window left a durable sent row for mail that never reached the
    outbox, and boot reconciliation SKIPS peer rows by design (consumed outbox mail leaves no
    file, so absence proves nothing there). The stage file is the arbiter now: staged before
    the publish, resolved after it; an orphaned stage at boot files the unpublished row."""

    def setUp(self):
        _reset()
        shutil.rmtree(pm.OUTBOX, ignore_errors=True)   # OUTBOX lives under STATE, outside
        #                                                MAILROOT — _reset() never touches it
        self.now = int(time.time())
        self.hd = pm.OUTBOX / "TESTHOST2"
        self.hd.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        _reset()
        shutil.rmtree(pm.OUTBOX, ignore_errors=True)

    def test_a_stage_is_invisible_to_outbox_consumers_whatever_the_hostname(self):
        # the r51 verification round (ported): pathlib globs match dotfiles — on a host
        # literally named "json" the full-payload stage matched *.json and could SHIP as
        # live mail before its sent row existed
        (self.hd / ".stage-px-9.1_1.json").write_text(json.dumps({"mid": "px-9.1_1.json",
                                                                  "body": "not mail yet"}))
        (self.hd / "px-8.1_0.json.json").write_text(json.dumps({"mid": "px-8.1_0.json"}))
        rows = pm.outbox_list("TESTHOST2")
        self.assertEqual([r["mid"] for r in rows], ["px-8.1_0.json"],
                         "the dot-prefixed stage is never exchanged")

    def test_an_orphaned_stage_files_the_unpublished_row(self):
        (self.hd / ".stage-px-201.1_777.TESTHOST").write_text("1")
        with contextlib.redirect_stderr(io.StringIO()):
            fixed = pm._reconcile_relay_stages()
        self.assertEqual(fixed, 1)
        rows = [r for r in _rows() if r.get("ev") == "unpublished"]
        self.assertEqual([r["id"] for r in rows], ["px-201.1_777.TESTHOST"],
                         "the never-published relay is terminal now — receipts and the "
                         "wait map stop accounting a phantom")
        self.assertFalse((self.hd / ".stage-px-201.1_777.TESTHOST").exists(),
                         "the resolved stage is spent")

    def test_a_stage_beside_its_outbox_file_just_drops(self):
        # a crash AFTER the publish, before the stage unlink: the mail is real — no row
        (self.hd / ".stage-px-202.1_888.TESTHOST").write_text("1")
        (self.hd / "px-202.1_888.TESTHOST.json").write_text(json.dumps({"mid": "x"}))
        self.assertEqual(pm._reconcile_relay_stages(), 0)
        self.assertEqual([r for r in _rows() if r.get("ev") == "unpublished"], [])
        self.assertFalse((self.hd / ".stage-px-202.1_888.TESTHOST").exists())

    def test_a_failed_terminal_append_retains_the_stage(self):
        # the row could not land -> the stage must survive to the NEXT bus start; consuming it
        # anyway would erase the only evidence the publish never happened
        (self.hd / ".stage-px-203.1_999.TESTHOST").write_text("1")
        real = pm._tl_append
        pm._tl_append = lambda name, row: False
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                fixed = pm._reconcile_relay_stages()
        finally:
            pm._tl_append = real
        self.assertEqual(fixed, 0)
        self.assertTrue((self.hd / ".stage-px-203.1_999.TESTHOST").exists(),
                        "no terminal row, no consumption — retried at the next start")

    def test_the_relay_leg_stages_first_and_boot_resolves(self):
        # the transaction's ordering, pinned at the source: STAGE -> sent row -> rename-publish
        # (the r50 verification round: staged after the row, a crash — or a plain OSError on
        # the stage write itself — in the one-line window still minted the permanent phantom;
        # the v1.3.23 audit's P2.4 folded the old stage unlink INTO the publish rename); and
        # serve() runs the stage reconciliation at boot
        src = open(os.path.join(BIN, "romp-postal-service"), encoding="utf-8").read()
        i_stage = src.index('".stage-" + mid')
        # the RELAY leg's copy of the refusal string — deliver()'s local leg wears the same
        # words much earlier in the file, so the search starts at the stage
        i_row = src.index('the delivery record could not be written', i_stage)
        i_put = src.index("outbox_publish_stage(phost, mid, _stagef)")
        self.assertLess(i_stage, i_row, "the arbiter lands before ANY durable claim")
        self.assertLess(i_row, i_put, "…and the sent row before the publish (receipts never lost)")
        body = src.split("def serve():", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_reconcile_relay_stages()", body)

    def test_a_delivered_mails_orphan_stage_files_nothing(self):
        # the r50 verification round: the success-path stage unlink can fail (OSError) while
        # the bus keeps serving — the mail ships, the far ack removes the outbox file and logs
        # ev:"relayed", and the old reconcile then read the orphan stage as never-published,
        # filing "unpublished" for mail the recipient RECEIVED and erasing its receipt
        (self.hd / ".stage-px-204.1_121.TESTHOST").write_text("1")
        pm._tl_append("messages.jsonl", {"t": self.now, "ev": "relayed",
                                         "id": "px-204.1_121.TESTHOST", "host": "TESTHOST2"})
        self.assertEqual(pm._reconcile_relay_stages(), 0)
        self.assertEqual([r for r in _rows() if r.get("ev") == "unpublished"], [],
                         "a relayed row proves the publish — no terminal is filed")
        self.assertFalse((self.hd / ".stage-px-204.1_121.TESTHOST").exists(),
                         "…and the orphan stage is spent")

    def test_the_publish_rename_leaves_no_stage_window(self):
        # the v1.3.23 audit's P2.4, executed: with put-then-unlink, a fast peer ack could
        # delete the published file BEFORE the stage unlink ran; a stop before the `relayed`
        # append then made boot read the orphan stage as never-published and mark DELIVERED
        # mail unpublished, erasing its receipt. The rename publish retires the stage in the
        # same atomic step: the delivered schedule leaves NO stage, and boot files nothing.
        mid = "px-205.1_131.TESTHOST"
        stage = self.hd / (".stage-" + mid)
        stage.write_text(json.dumps({"mid": mid, "to": "web", "body": "synthetic"}))
        pm._tl_append("messages.jsonl", {"t": self.now, "ev": "sent", "id": mid,
                                         "from": "api", "from_id": "1" * 8,
                                         "to_id": "peer:TESTHOST2"})
        pm.outbox_publish_stage("TESTHOST2", mid, stage)
        self.assertFalse(stage.exists(), "the publish IS the stage's retirement — one atomic step")
        self.assertEqual(json.loads((self.hd / (mid + ".json")).read_text())["mid"], mid,
                         "the staged payload is the published record")
        # the peer consumes and acks; the bus stops before the `relayed` append lands
        pm.outbox_del("TESTHOST2", mid)
        self.assertEqual(pm._reconcile_relay_stages(), 0,
                         "delivered mail is never re-marked unpublished")
        self.assertEqual([r for r in _rows() if r.get("ev") == "unpublished"], [])

    def test_the_relay_stage_carries_the_full_payload(self):
        # the rename publish can only be atomic if the stage IS the record — a marker-byte
        # stage ("1") would publish garbage; pin the leg writing relay_msg into the stage
        src = open(os.path.join(BIN, "romp-postal-service"), encoding="utf-8").read()
        i_stage = src.index('".stage-" + mid')
        leg = src[i_stage:i_stage + 3000]
        self.assertIn("json.dump(relay_msg, _sf)", leg, "the stage holds the payload")
        self.assertIn("os.fsync(_sf.fileno())", leg, "…durably, before any durable claim")

    def test_a_publish_raise_gets_a_compensating_row(self):
        # the executed in-process shape: outbox_put raises (not a crash) — the leg files the
        # compensating unpublished row itself and spends the stage
        src = open(os.path.join(BIN, "romp-postal-service"), encoding="utf-8").read()
        leg = src[src.index('".stage-" + mid'):src.index('".stage-" + mid') + 2500]
        self.assertIn('"ev": "unpublished"', leg,
                      "the raise path files the terminal row inline")
        self.assertIn("relay publication failed", leg, "…and answers the sender loudly")


class R52StageReconcileAndIds(unittest.TestCase):
    """the v1.3.24 audit's P2.8 (a legacy stage whose `relayed` proof scrolled past the 512KiB
    tail erased a delivered receipt) and P3.10 (100k mids/sec collided, and the colliding
    publish silently replaced the earlier message)."""

    def setUp(self):
        _reset()
        shutil.rmtree(pm.OUTBOX, ignore_errors=True)
        self.now = int(time.time())
        self.hd = pm.OUTBOX / "TESTHOST2"
        self.hd.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        _reset()
        shutil.rmtree(pm.OUTBOX, ignore_errors=True)

    def test_a_delivered_proof_beyond_the_tail_still_counts(self):
        # the executed r52 shape: legacy stage + sent + relayed, then >512KiB of traffic —
        # the tail scan missed the proof and appended `unpublished`, erasing the receipt
        mid = "px-300.1_aa.TESTHOST"
        (self.hd / (".stage-" + mid)).write_text("1")           # a legacy pre-rename stage
        pm._tl_append("messages.jsonl", {"t": self.now, "ev": "relayed", "id": mid,
                                         "host": "TESTHOST2"})
        filler = json.dumps({"t": self.now, "ev": "noise", "id": "n", "pad": "z" * 900})
        with open(pm.TLDIR / "messages.jsonl", "a") as fh:
            for _ in range(pm.PHANTOM_TAIL_BYTES // len(filler) + 50):
                fh.write(filler + "\n")
        self.assertEqual(pm._reconcile_relay_stages(), 0)
        self.assertEqual([r for r in _rows() if r.get("ev") == "unpublished"], [],
                         "the scan is keyed to the stage's mid over the WHOLE log — the "
                         "proof cannot scroll out of sight")
        self.assertFalse((self.hd / (".stage-" + mid)).exists(), "the settled stage drops")

    def test_an_unreadable_log_preserves_every_stage(self):
        mid = "px-301.1_bb.TESTHOST"
        (self.hd / (".stage-" + mid)).write_text("1")
        log = pm.TLDIR / "messages.jsonl"
        os.chmod(log, 0)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                fixed = pm._reconcile_relay_stages()
        finally:
            os.chmod(log, 0o644)
        self.assertEqual(fixed, 0)
        self.assertTrue((self.hd / (".stage-" + mid)).exists(),
                        "an unreadable log proves nothing — no terminal is filed over an "
                        "unproved read, and the stage waits for the next start")
        self.assertEqual([r for r in _rows() if r.get("ev") == "unpublished"], [])

    def test_mids_carry_128_random_bits(self):
        mid = pm._unique()
        rand = mid.split("_", 1)[1].rsplit(".", 1)[0]
        self.assertEqual(len(rand), 32, "32 hex chars = 128 bits — 100k/sec collided")
        int(rand, 16)

    def test_publication_never_replaces_standing_mail(self):
        mid = "px-302.1_cc.TESTHOST"
        (self.hd / (mid + ".json")).write_text(json.dumps({"mid": mid, "body": "FIRST"}))
        stage = self.hd / (".stage-" + mid)
        stage.write_text(json.dumps({"mid": mid, "body": "SECOND"}))
        with self.assertRaises(OSError):
            pm.outbox_publish_stage("TESTHOST2", mid, stage)
        self.assertEqual(json.loads((self.hd / (mid + ".json")).read_text())["body"], "FIRST",
                         "a colliding publish is LOUD and the standing message survives — "
                         "rename silently replaced it")
        self.assertTrue(stage.exists(), "…and the stage is retained for the compensation arm")


class R53ProvedRetirement(unittest.TestCase):
    """the v1.3.25 audit's P2.10-P2.12 + P3.13: the ack/bounce paths deleted the retry source
    over unproved reads and unproved terminal appends; boot reconciliation raced already-
    seeded dialers; an uncertain sent-append destroyed the relay's arbiter stage; a
    well-formed [] log row wedged the scanner every boot."""

    def setUp(self):
        _reset()
        shutil.rmtree(pm.OUTBOX, ignore_errors=True)
        self.now = int(time.time())
        self.hd = pm.OUTBOX / "TESTHOST2"
        self.hd.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        _reset()
        shutil.rmtree(pm.OUTBOX, ignore_errors=True)

    def test_an_unreadable_record_defers_the_ack(self):
        f = self.hd / "px-401.1_aa.TESTHOST.json"
        f.write_text(json.dumps({"mid": "px-401.1_aa.TESTHOST", "body": "hi"}))
        os.chmod(f, 0)
        try:
            pm._ack_arrived("TESTHOST2", "px-401.1_aa.TESTHOST")
        finally:
            os.chmod(f, 0o644)
        self.assertTrue(f.exists(),
                        "EIO used to read as 'missing' and the ack DELETED the only retry "
                        "source with no terminal row (the r53 audit's P2.10)")
        self.assertEqual([r for r in _rows() if r.get("ev") == "relayed"], [])

    def test_a_failed_relayed_append_keeps_the_file(self):
        f = self.hd / "px-402.1_bb.TESTHOST.json"
        f.write_text(json.dumps({"mid": "px-402.1_bb.TESTHOST", "body": "hi"}))
        real = pm._tl_append
        pm._tl_append = lambda name, row: False
        try:
            pm._ack_arrived("TESTHOST2", "px-402.1_bb.TESTHOST")
        finally:
            pm._tl_append = real
        self.assertTrue(f.exists(), "delete only after the terminal append PROVED — the next "
                                    "ack retries idempotently")

    def test_reconciliation_runs_before_any_peer_seeding(self):
        src = open(os.path.join(BIN, "romp-postal-service"), encoding="utf-8").read()
        body = src.split("def serve():", 1)[1].split("\ndef ", 1)[0]
        i_rec = body.index("_reconcile_relay_stages()")
        i_seed = body.index("_seed_peers_from_kernel()")
        self.assertLess(i_rec, i_seed,
                        "seeding starts dialer threads (the r53 audit's P2.11, reproduced "
                        "there: a dialer's ack deleted the outbox file between the "
                        "reconcile's log snapshot and its stage sweep, and delivered mail "
                        "was marked unpublished)")

    def test_an_uncertain_sent_append_keeps_the_stage(self):
        src = open(os.path.join(BIN, "romp-postal-service"), encoding="utf-8").read()
        i_stage = src.index('".stage-" + mid')
        i_refuse = src.index("the delivery record could not be written", i_stage)
        window = src[i_stage:i_refuse]
        self.assertNotIn("_stagef.unlink()", window,
                         "a torn append — row durably written, close reporting failure — "
                         "used to unlink the arbiter: sent row, no terminal, no stage, both "
                         "reconcilers skipping peer rows (the r53 audit's P2.12)")

    def test_a_non_dict_row_never_wedges_the_phantom_scanner(self):
        # the r53 verification round: the stage scanner got the isinstance guard, but the
        # PHANTOM-SENT tail scanner still called .get on whatever json.loads returned — one
        # historic [] row raised AttributeError through every boot reconcile forever
        with open(pm.TLDIR / "messages.jsonl", "a") as fh:
            fh.write("[]\n")
            fh.write(json.dumps({"t": self.now, "ev": "sent", "id": "px-404.1_dd.TESTHOST",
                                 "to_id": "77777777-8888-9999-aaaa-bbbbbbbbbbbb"}) + "\n")
        fixed = pm._reconcile_phantom_sent(now=self.now)
        self.assertEqual(fixed, 1, "the phantom past the [] row is still found and filed")

    def test_an_unreadable_bounce_parks_and_settles_on_proof(self):
        # r53 found the raise (killing the exchange batch); r54's "file the receipt now"
        # over-corrected (the r55 audit's P2.15, executed there: one TRANSIENT EIO deleted
        # recoverable mail and lost its return note). The bounce EVENT parks durably now —
        # the exchange 200s it, so the peer never re-sends — and retries until the read
        # PROVES; a recovered record then takes the full path, return note included.
        pm._pending_bounces.clear()
        f = self.hd / "px-405.1_ee.TESTHOST.json"
        f.write_text(json.dumps({"mid": "px-405.1_ee.TESTHOST", "body": "hi",
                                 "frm_id": "77777777-8888-9999-aaaa-bbbbbbbbbbbb"}))
        os.chmod(f, 0)
        try:
            pm._bounce_arrived("TESTHOST2", {"mid": "px-405.1_ee.TESTHOST",
                                             "code": "recipient-unavailable"})
            self.assertTrue(f.exists(), "the recoverable record SURVIVES the fault")
            self.assertEqual([r for r in _rows() if r.get("ev") == "bounced"], [],
                             "no terminal over an unproved read")
            self.assertIn("px-405.1_ee.TESTHOST", pm._pending_bounces,
                          "…the bounce parked durably instead")
            self.assertTrue(pm._pending_bounces_path().exists())
        finally:
            os.chmod(f, 0o644)
        pm._flush_pending_bounces()                  # the read proves on the next pass
        bounced = [r for r in _rows() if r.get("ev") == "bounced"]
        self.assertEqual([r["id"] for r in bounced], ["px-405.1_ee.TESTHOST"],
                         "the recovered record takes the FULL path — receipt and return note")
        self.assertFalse(f.exists(), "…and retires with it")
        self.assertNotIn("px-405.1_ee.TESTHOST", pm._pending_bounces, "the parked entry clears")
        # PARSED garbage is still definite: terminal now, never parked
        g2 = self.hd / "px-406.1_ff.TESTHOST.json"
        g2.write_text("{garbage")
        pm._bounce_arrived("TESTHOST2", {"mid": "px-406.1_ff.TESTHOST",
                                         "code": "recipient-unavailable"})
        self.assertIn("px-406.1_ff.TESTHOST",
                      [r["id"] for r in _rows() if r.get("ev") == "bounced"])
        self.assertFalse(g2.exists())

    def test_an_unreadable_record_still_reads_as_parked(self):
        # the r53 verification round: check_sent's parked probe raised through the whole
        # listing on one unreadable outbox record; unreadable is not GONE — the row stays
        # honestly parked and the listing survives
        src = open(os.path.join(BIN, "romp-postal-service"), encoding="utf-8").read()
        i = src.index("def _parked(i, e):")
        window = src[i:i + 900]
        self.assertIn("except (_OutboxUnreadable, _OutboxMalformed):", window)
        i2 = src.index("a resend while we hold it forwards nothing twice")
        window2 = src[i2 - 400:i2 + 700]
        self.assertIn("except (_OutboxUnreadable, _OutboxMalformed):", window2,
                      "the forward-dedupe probe treats unreadable/garbled as EXISTS — never "
                      "a double-forward, never a batch-killing raise")

    def test_a_non_dict_log_row_never_wedges_the_scanner(self):
        (self.hd / ".stage-px-403.1_cc.TESTHOST").write_text("1")
        with open(pm.TLDIR / "messages.jsonl", "a") as fh:
            fh.write("[]\n")             # well-formed, not an object — raised through boot (P3.13)
            fh.write(json.dumps({"t": self.now, "ev": "relayed",
                                 "id": "px-403.1_cc.TESTHOST"}) + "\n")
        self.assertEqual(pm._reconcile_relay_stages(), 0)
        self.assertFalse((self.hd / ".stage-px-403.1_cc.TESTHOST").exists(),
                         "the proof past the [] row still counts — the stage settles")

class R54PublishAndTypedReader(unittest.TestCase):
    """the v1.3.26 audit's P1.5 + P2.9 + P2.10: post-link failures compensated live mail as
    unpublished (double delivery); missing/malformed/transiently-unreadable outbox records
    were conflated (receipts and return notes vanished, valid files were quarantined); and
    wrongly-shaped historic log rows wedged /sent, the stage reconcile, and every handoff."""

    def setUp(self):
        _reset()
        shutil.rmtree(pm.OUTBOX, ignore_errors=True)
        self.now = int(time.time())
        self.hd = pm.OUTBOX / "TESTHOST2"
        self.hd.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        _reset()
        shutil.rmtree(pm.OUTBOX, ignore_errors=True)

    def test_publication_is_a_one_way_door(self):
        # r54 P1.5, executed there: the dir fsync raised after the link and the stage unlink;
        # the caller filed `unpublished` over LIVE mail and the sender's retry double-delivered
        mid = "px-501.1_aa.TESTHOST"
        stage = self.hd / (".stage-" + mid)
        stage.write_text(json.dumps({"mid": mid, "body": "hi"}))
        real = pm._fsync_dir
        pm._fsync_dir = lambda p: (_ for _ in ()).throw(RuntimeError("forced"))
        try:
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                pm.outbox_publish_stage("TESTHOST2", mid, stage)   # must NOT raise
        finally:
            pm._fsync_dir = real
        self.assertTrue((self.hd / (mid + ".json")).exists(),
                        "the message is LIVE — no post-link failure reads as unpublished")
        self.assertTrue(stage.exists(),
                        "…and the stage survives for boot arbitration (published file wins)")

    def test_a_crashed_retry_of_the_same_stage_is_publication(self):
        # the EEXIST arm: identical bytes = a link/unlink crash retry — published, silently;
        # different bytes stay the loud r52 collision (its own test above)
        mid = "px-502.1_bb.TESTHOST"
        body = json.dumps({"mid": mid, "body": "hi"})
        (self.hd / (mid + ".json")).write_text(body)
        stage = self.hd / (".stage-" + mid)
        stage.write_text(body)
        pm.outbox_publish_stage("TESTHOST2", mid, stage)           # no raise
        self.assertTrue((self.hd / (mid + ".json")).exists())
        self.assertFalse(stage.exists(), "the stage retires — the publication stands")

    def test_transient_faults_never_quarantine(self):
        # r54 P2.9, executed there: the exchange's scanner quarantined a VALID record the
        # same pass its ack path had correctly deferred — retirement of good mail
        f = self.hd / "px-503.1_cc.TESTHOST.json"
        f.write_text(json.dumps({"mid": "px-503.1_cc.TESTHOST", "body": "hi"}))
        os.chmod(f, 0)
        try:
            rows = pm.outbox_list("TESTHOST2")
        finally:
            os.chmod(f, 0o644)
        self.assertEqual(rows, [])
        self.assertTrue(f.exists(), "an EIO'd record is KEPT — only proven garbage moves")
        f.write_text("{garbage")
        rows = pm.outbox_list("TESTHOST2")
        self.assertEqual(rows, [])
        self.assertFalse(f.exists(), "…and parsed garbage still quarantines aside")

    def test_a_non_utf8_record_never_wedges_the_listing(self):
        # the r54 wave-2 verification: read_text's UnicodeDecodeError escaped BOTH arms to
        # the outer `except Exception: return []` — one torn record silenced the whole
        # host's relay listing forever, with nothing quarantined and nothing logged
        good = self.hd / "px-508.1_hh.TESTHOST.json"
        good.write_text(json.dumps({"mid": "px-508.1_hh.TESTHOST", "body": "hi"}))
        torn = self.hd / "px-509.1_ii.TESTHOST.json"
        torn.write_bytes(b"\xff\xfe\x00garbage")
        rows = pm.outbox_list("TESTHOST2")
        self.assertEqual([r["mid"] for r in rows], ["px-508.1_hh.TESTHOST"],
                         "the valid mail still relays")
        self.assertFalse(torn.exists(), "…and the torn record quarantines as PROVEN garbage")

    def test_a_malformed_record_still_files_the_ack_receipt(self):
        # r54 P2.9: malformed read as 'missing' — the ack deleted the record with no
        # terminal row, and the delivered message showed parked-forever after the tail aged
        f = self.hd / "px-504.1_dd.TESTHOST.json"
        f.write_text("{garbage")
        pm._ack_arrived("TESTHOST2", "px-504.1_dd.TESTHOST")
        relayed = [r for r in _rows() if r.get("ev") == "relayed"]
        self.assertEqual([r["id"] for r in relayed], ["px-504.1_dd.TESTHOST"],
                         "the ack is definite — the receipt files")
        self.assertFalse(f.exists(), "…and the garbage retires only after the append proved")

    def test_shaped_rows_never_wedge_sent_or_handoffs(self):
        # r54 P2.10: one historic [] row (or {"id": []}) raised through /sent's listing,
        # the handoff parser (the exchange never answered), and the stage reconcile's set probe
        with open(pm.TLDIR / "messages.jsonl", "a") as fh:
            fh.write("[]\n")
            fh.write(json.dumps({"t": self.now, "ev": "sent", "id": [],
                                 "from_id": "77777777-8888-9999-aaaa-bbbbbbbbbbbb"}) + "\n")
            fh.write(json.dumps({"t": self.now, "ev": "sent", "id": "px-505.1_ee.TESTHOST",
                                 "from_id": "77777777-8888-9999-aaaa-bbbbbbbbbbbb",
                                 "to_id": "88888888-9999-aaaa-bbbb-cccccccccccc"}) + "\n")
            fh.write(json.dumps({"t": self.now, "ev": "handoff-done", "id": []}) + "\n")
            fh.write(json.dumps({"t": self.now, "ev": "handoff-done",
                                 "id": "px-506.1_ff.TESTHOST"}) + "\n")
        rows = pm._sent_receipts("77777777-8888-9999-aaaa-bbbbbbbbbbbb")
        self.assertEqual([r["id"] for r in rows], ["px-505.1_ee.TESTHOST"],
                         "/sent lists the valid row and survives the shaped ones")
        pm._HANDOFF_DONE_MEMO["key"] = None
        self.assertEqual(pm._handoff_done_ids(), {"px-506.1_ff.TESTHOST"},
                         "the handoff parser survives too — the exchange answers")

    def test_an_unhashable_id_never_hides_later_stage_proof(self):
        # the stage reconcile's set probe raised on {"id": []} BEFORE reading the valid
        # settlement row behind it — the stage was never retired
        (self.hd / ".stage-px-507.1_gg.TESTHOST").write_text("1")
        with open(pm.TLDIR / "messages.jsonl", "a") as fh:
            fh.write(json.dumps({"t": self.now, "ev": "relayed", "id": []}) + "\n")
            fh.write(json.dumps({"t": self.now, "ev": "relayed",
                                 "id": "px-507.1_gg.TESTHOST"}) + "\n")
        self.assertEqual(pm._reconcile_relay_stages(), 0)
        self.assertFalse((self.hd / ".stage-px-507.1_gg.TESTHOST").exists(),
                         "the proof past the shaped row still counts — the stage settles")

class R55PublishOutcomesAndSchema(unittest.TestCase):
    """the v1.3.27 audit's P1.7/P1.8 + P2.13/P2.14/P2.16/P2.17: publication could still
    throw after the message was live (double delivery), REAL directory-fsync failures were
    swallowed (both copies lost on a hard shutdown), the outbox reader validated syntax but
    not schema, failed terminal appends raced the scanner's quarantine, wrong-shaped
    handoff provenance was marked complete with zero receipts, and the receipt/read boxes
    quarantined valid records on transient faults."""

    def setUp(self):
        _reset()
        shutil.rmtree(pm.OUTBOX, ignore_errors=True)
        shutil.rmtree(pm.RECEIPTBOX, ignore_errors=True)
        shutil.rmtree(pm.READBOX, ignore_errors=True)
        pm._pending_terminal_rows[:] = []
        pm._pending_bounces.clear()
        try:
            pm._pending_bounces_path().unlink()
        except OSError:
            pass
        self.now = int(time.time())
        self.hd = pm.OUTBOX / "TESTHOST2"
        self.hd.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.setUp()

    def test_real_fsync_failure_keeps_the_stage(self):
        # r55 P1.8, executed with a REAL os.fsync injection (the audit called out that the
        # old test mocked the wrapper): two swallowed EIOs let publication report success
        # and delete the recovery stage — a hard shutdown then had NO copy at all
        mid = "px-601.1_aa.TESTHOST"
        stage = self.hd / (".stage-" + mid)
        stage.write_text(json.dumps({"mid": mid, "body": "hi"}))
        real = os.fsync
        def _boom(fd):
            raise OSError(5, "EIO")
        pm.os.fsync = _boom
        try:
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                pm.outbox_publish_stage("TESTHOST2", mid, stage)   # never raises post-link
        finally:
            pm.os.fsync = real
        self.assertTrue((self.hd / (mid + ".json")).exists(), "the message is live")
        self.assertTrue(stage.exists(),
                        "…and the UNPERSISTED publication keeps its recovery stage")
        # unsupported directory fsync stays silent — portability intact
        def _einval(fd):
            raise OSError(errno.EINVAL, "EINVAL")
        import errno
        pm.os.fsync = lambda fd: (_ for _ in ()).throw(OSError(22, "EINVAL"))
        try:
            pm._fsync_dir(self.hd)                   # no raise
        finally:
            pm.os.fsync = real

    def test_an_unreadable_standing_file_is_uncertain_never_compensated(self):
        # r55 P1.7's EEXIST arm: an unreadable same-mid file means the mail is live either
        # way — raising here filed `unpublished` over delivered mail and the retry doubled
        mid = "px-602.1_bb.TESTHOST"
        final = self.hd / (mid + ".json")
        final.write_text(json.dumps({"mid": mid, "body": "hi"}))
        stage = self.hd / (".stage-" + mid)
        stage.write_text(json.dumps({"mid": mid, "body": "hi"}))
        os.chmod(final, 0)
        try:
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                pm.outbox_publish_stage("TESTHOST2", mid, stage)   # no raise
        finally:
            os.chmod(final, 0o644)
        self.assertTrue(stage.exists(), "the stage stays for boot arbitration")
        # …and the pre-publication failures are TYPED so only they reach compensation
        self.assertTrue(issubclass(pm._PublishNeverStarted, OSError))
        src = open(os.path.join(BIN, "romp-postal-service"), encoding="utf-8").read()
        i = src.index("outbox_publish_stage(phost, mid, _stagef)")
        window = src[i:i + 1800]
        self.assertIn("except _PublishNeverStarted:", window,
                      "ONLY definitely-never-started compensates as unpublished")
        self.assertIn("UNCERTAIN", window, "…anything else keeps the stage, files nothing")

    def test_schema_invalid_records_never_relay_or_ack(self):
        # r55 P2.13, executed there: 32 parsed-valid [] rows exhausted the relay byte
        # budget and hid a real message indefinitely; an ack for one deleted it as mail
        good = self.hd / "px-603.1_cc.TESTHOST.json"
        good.write_text(json.dumps({"mid": "px-603.1_cc.TESTHOST", "body": "hi"}))
        for i in range(3):
            (self.hd / ("px-604.%d_dd.TESTHOST.json" % i)).write_text("[]")
        rows = pm.outbox_list("TESTHOST2")
        self.assertEqual([r["mid"] for r in rows], ["px-603.1_cc.TESTHOST"],
                         "only schema-valid mail relays; the garbage quarantined aside")
        self.assertFalse(any(self.hd.glob("px-604*")), "…and left the outbox")
        # a mid-mismatched record is garbage too (never deletable as someone else's mail)
        bad = self.hd / "px-605.1_ee.TESTHOST.json"
        bad.write_text(json.dumps({"mid": "px-999.9_zz.OTHER", "body": "hi"}))
        with self.assertRaises(pm._OutboxMalformed):
            pm.outbox_get("TESTHOST2", "px-605.1_ee.TESTHOST")

    def test_a_failed_terminal_append_queues_the_row_itself(self):
        # r55 P2.14, executed there: "the file is kept" was hollow — the same exchange's
        # scanner quarantined the malformed record before returning 200, the peer never
        # re-sent, and no terminal ever landed
        f = self.hd / "px-606.1_ff.TESTHOST.json"
        f.write_text("{garbage")
        real = pm._tl_append
        pm._tl_append = lambda name, row: False
        try:
            pm._ack_arrived("TESTHOST2", "px-606.1_ff.TESTHOST")
        finally:
            pm._tl_append = real
        self.assertEqual([r["id"] for r in pm._pending_terminal_rows],
                         ["px-606.1_ff.TESTHOST"], "the row itself is the retry intent")
        pm._drain_postal_retries()
        relayed = [r for r in _rows() if r.get("ev") == "relayed"]
        self.assertEqual([r["id"] for r in relayed], ["px-606.1_ff.TESTHOST"],
                         "…and the drain lands it — the receipt survives the race")
        self.assertEqual(pm._pending_terminal_rows, [])

    def test_wrong_shaped_handoff_provenance_is_retryable_never_done(self):
        # r55 P2.16, executed there: a historic row with relay_mid:[] answered
        # {ok, local:true} — the delivery was marked done with ZERO completion receipts
        with open(pm.TLDIR / "messages.jsonl", "a") as fh:
            fh.write("[]\n")             # the scanner survives shaped rows too
            fh.write(json.dumps({"t": self.now, "ev": "sent", "id": "px-607.1_gg.TESTHOST",
                                 "relay_mid": [], "relay_via": "TESTHOST2"}) + "\n")
        ans, code = pm.handoff_done_apply({"mid": "px-607.1_gg.TESTHOST"})
        self.assertEqual(code, 404, "corrupt provenance falls to RETRYABLE recovery")
        self.assertTrue(ans.get("retry"))
        self.assertNotIn("px-607.1_gg.TESTHOST", pm._receipts_done(),
                         "…and is NEVER marked complete over it")

    def test_receipt_and_read_boxes_keep_valid_records_through_transient_faults(self):
        # r55 P2.17, executed there: one injected EIO moved each valid row into corrupt/
        # and its active copy was gone
        rb = pm.RECEIPTBOX / "TESTHOST2"
        rb.mkdir(parents=True, exist_ok=True)
        rf = rb / "px-608.1_hh.TESTHOST.json"
        rf.write_text(json.dumps({"mid": "px-608.1_hh.TESTHOST"}))
        os.chmod(rf, 0)
        try:
            self.assertEqual(pm.receiptbox_list("TESTHOST2"), [])
        finally:
            os.chmod(rf, 0o644)
        self.assertTrue(rf.exists(), "receiptbox: kept through the fault")
        self.assertEqual([r["mid"] for r in pm.receiptbox_list("TESTHOST2")],
                         ["px-608.1_hh.TESTHOST"], "…and lists once readable")
        db = pm.READBOX / "TESTHOST2"
        db.mkdir(parents=True, exist_ok=True)
        df = db / "px-609.1_ii.TESTHOST.json"
        df.write_text(json.dumps({"mid": "px-609.1_ii.TESTHOST"}))
        os.chmod(df, 0)
        try:
            self.assertEqual(pm.readbox_list("TESTHOST2"), [])
        finally:
            os.chmod(df, 0o644)
        self.assertTrue(df.exists(), "readbox: kept through the fault")
        df.write_text("{garbage")
        self.assertEqual(pm.readbox_list("TESTHOST2"), [])
        self.assertFalse(df.exists(), "…while proven garbage still quarantines")
