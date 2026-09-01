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




class R57PostalDurability(unittest.TestCase):
    """the v1.3.29 audit's postal half: _tl_append returned True with zero fsync calls
    (P1.6), a non-UTF-8 seen ledger loaded as EMPTY and re-delivered recorded mids (P1.8),
    relay effects preceded the durable dedupe — a crash replay double-delivered (P1.7), boot
    filed `unpublished` over a half-durable publication and deleted its only payload (P1.9),
    forwarded acks/bounces lived only in memory (P1.10), bounce settlement was process-local
    (P1.11), receipts confirmed the RELAY id so /handoff-done requeued forever (P2.17), junk
    exchange rows consumed the whole read budget (P2.19), isolation failed open (P1.4)."""

    HOST = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"

    def setUp(self):
        _reset()
        pm._seen_ids = None
        try:
            pm.PEER_SEEN.unlink()
        except OSError:
            pass
        pm._bounced_done.clear()
        pm._bounced_done_loaded[0] = False
        pm._postal_off_cache[0] = None
        for p in (pm._bounced_done_path(), pm._backflow_path(), pm.RECEIPTS_DONE,
                  pm.SESSION_FLAGS):
            try:
                p.unlink()
            except OSError:
                pass
        with pm._peer_lock:
            pm._peer_pending.clear()
        shutil.rmtree(pm.OUTBOX / self.HOST, ignore_errors=True)
        shutil.rmtree(pm.RECEIPTBOX / self.HOST, ignore_errors=True)

    def tearDown(self):
        self.setUp()

    def test_a_terminal_log_append_is_fsynced(self):
        seen = []
        real = os.fsync

        def spy(fd):
            seen.append(os.fstat(fd).st_ino)
            real(fd)

        pm.os.fsync = spy
        try:
            self.assertTrue(pm._tl_append("messages.jsonl", {"t": 1, "ev": "sent", "id": "x"}))
        finally:
            pm.os.fsync = real
        self.assertIn((pm.TLDIR / "messages.jsonl").stat().st_ino, seen,
                      "True means DURABLE: fsync ran on the log's own fd (r57 P1.6)")

    def test_b_fsync_failure_refuses_append_and_delivery(self):
        real = os.fsync

        def boom(fd):
            raise OSError(5, "EIO")

        mb = pm._mailbox(SID)
        pm.os.fsync = boom
        try:
            self.assertFalse(pm._tl_append("messages.jsonl", {"t": 1}),
                             "an unsynced row must never report durable")
            with self.assertRaises((RuntimeError, OSError)):
                # r58 P1.7: the EFFECT write fsyncs too now, so the injected fault can
                # surface there (OSError) or at the accounting row (RuntimeError) — either
                # way nothing publishes
                pm.deliver(SID, "peer", SID2, "hello")
        finally:
            pm.os.fsync = real
        self.assertEqual(list((mb / "new").iterdir()), [],
                         "no mail publishes over a lost accounting record")

    def test_c_non_utf8_seen_ledger_holds_the_verdict(self):
        pm.PEER_SEEN.parent.mkdir(parents=True, exist_ok=True)
        pm.PEER_SEEN.write_bytes(b"\xff\xfe not utf8\n")
        pm._seen_ids = None
        self.assertIsNone(pm.peer_seen_check("a" * 32),
                          "corrupt bytes are NO INFORMATION — never a fresh ledger (r57 "
                          "P1.8); the relay path drops and the peer re-sends")
        self.assertEqual(pm.PEER_SEEN.read_bytes(), b"\xff\xfe not utf8\n",
                         "…and the records are preserved for recovery, not clobbered")

    def test_d_relayed_delivery_is_idempotent_by_relay_mid(self):
        mid = "ab" * 16
        via = "cd" * 16
        pm.deliver(SID, "peer", SID2, "hello", relay_mid=mid, relay_via=via)
        pm.deliver(SID, "peer", SID2, "hello", relay_mid=mid, relay_via=via)  # crash replay
        files = [f.name for f in (pm._mailbox(SID) / "new").iterdir()]
        self.assertEqual(files, [pm._relay_name("", mid)],
                         "the replay lands on the SAME unread file — one delivery (r57 "
                         "P1.7; the name is the origin-scoped digest since r58 P1.8/P2.16)")

    def test_e_phase_marker_republishes_the_lost_final(self):
        # r57 P1.9, executed there: crash after link+failed-fsync lost the final's dir
        # entry; boot filed `unpublished` and deleted the stage — the ONLY payload copy
        d = pm.OUTBOX / self.HOST
        d.mkdir(parents=True, exist_ok=True)
        mid = "cd" * 16
        st = d / (".stage-" + mid)
        st.write_text(json.dumps({"body": "payload"}))
        (d / (".pubphase-" + mid)).write_text("1")
        with contextlib.redirect_stderr(io.StringIO()):
            pm._reconcile_relay_stages()
        self.assertEqual((d / (mid + ".json")).read_text(), json.dumps({"body": "payload"}),
                         "a standing phase marker means outcome UNKNOWN — boot RE-PUBLISHES")
        self.assertFalse(st.exists(), "…retiring the stage in the same step")
        self.assertFalse((d / (".pubphase-" + mid)).exists(),
                         "…and the settled marker is cleared")

    def test_f_backflow_survives_the_restart(self):
        mid, bmid = "11" * 16, "22" * 16
        pm._backflow_load()              # latch (ENOENT = provably empty): the save
        #                                  refuses while unfolded since r59
        with pm._peer_lock:
            p = pm._peer_pending.setdefault(self.HOST, {"acks": [], "bounces": [],
                                                        "readAcks": [], "handoffDoneAcks": []})
            p["acks"].append(mid)
            p["bounces"].append({"mid": bmid, "reason": "gone"})
            pm._backflow_save_locked()
            pm._peer_pending.clear()                 # the process died mid-flight
        pm._backflow_load()
        with pm._peer_lock:
            q = pm._peer_pending.get(self.HOST)
        self.assertEqual(q["acks"], [mid],
                         "a forwarded ack survives the dropped response (r57 P1.10)")
        self.assertEqual(q["bounces"], [{"mid": bmid, "reason": "gone"}])

    def test_g_bounce_settlement_survives_the_restart(self):
        mid = "33" * 16
        self.assertTrue(pm._bounced_done_add(self.HOST, mid))
        pm._bounced_done.clear()
        pm._bounced_done_loaded[0] = False           # a fresh process
        self.assertTrue(pm._bounced_done_has(self.HOST, mid),
                        "settlement is durable — the return note never re-sends (r57 P1.11)")
        self.assertFalse(pm._bounced_done_has(self.HOST, "44" * 16))

    def test_h_receipt_confirmation_keys_on_the_delivery_id(self):
        rmid, dmid = "55" * 16, "66" * 16
        pm.receiptbox_put(self.HOST, rmid, dmid=dmid)
        row = json.loads((pm.RECEIPTBOX / self.HOST / (rmid + ".json")).read_text())
        self.assertEqual(row.get("dmid"), dmid, "the park records the DELIVERY id")
        pm.receiptbox_del(self.HOST, rmid)
        done = json.loads(pm.RECEIPTS_DONE.read_text())
        self.assertIn(dmid, done,
                      "confirmation marks the id /handoff-done re-posts (r57 P2.17: marking "
                      "the relay id re-parked the receipt forever)")
        self.assertNotIn(rmid, done)

    def test_i_junk_rows_do_not_consume_the_read_budget(self):
        cap = pm.PEER_LIST_LIMITS["reads"]
        rows = [123] * (cap + 50) + [{"mid": "77" * 16, "t": 5}]
        out = pm._peer_read_rows(rows)
        self.assertEqual([r["mid"] for r in out], ["77" * 16],
                         "junk is discarded WITHOUT spending the cap (r57 P2.19: numeric "
                         "rows hid every valid receipt behind them)")

    def test_j_isolation_fails_closed_with_last_known_good(self):
        # r57 P1.4, executed there: one EIO read isolation as OFF and a peer message was
        # delivered and ACKed into a durably-isolated session
        pm.SESSION_FLAGS.parent.mkdir(parents=True, exist_ok=True)
        pm.SESSION_FLAGS.write_text(json.dumps({SID: {"postalServiceOff": True}}))
        self.assertTrue(pm._postal_off(SID))         # a good read primes the copy
        pm.SESSION_FLAGS.write_text("{not valid json")
        self.assertTrue(pm._postal_off(SID), "the fault serves the copy — still isolated")
        pm.SESSION_FLAGS.write_text(json.dumps({SID: {"postalServiceOff": False}}))
        self.assertFalse(pm._postal_off(SID))
        pm.SESSION_FLAGS.write_text("{not valid json")
        self.assertTrue(pm._postal_off(SID),
                        "r58 P1.4: the corrupt overwrite is a NEW generation — the stale "
                        "permissive copy is never proof across it; fail closed")
        pm._postal_off_cache[0] = None
        self.assertTrue(pm._postal_off(SID),
                        "no history + unreadable: CLOSED — isolation is safety state")
        pm.SESSION_FLAGS.unlink()
        self.assertFalse(pm._postal_off(SID), "provably no flags: not isolated")

    def test_k_the_quiet_bus_drains_the_retry_queues(self):
        # r57 P2.20: the pumps were event-paced only (ack arrivals, dials) — a recovered
        # queued row on a QUIET bus starved forever. The maintenance loop drains too.
        src = open(os.path.join(BIN, "romp-postal-service")).read()
        fn = src.index("def _monitor(")
        body = src[fn:fn + 2400]
        self.assertIn("_drain_postal_retries()", body,
                      "the periodic monitor pumps queued terminal rows and parked bounces")




class R57Wave2Postal(unittest.TestCase):
    """the r57 wave-2 verification round, postal half — every finding below was REPRODUCED
    by an adversarial skeptic against the wave-1 commit: the r- replay resurrected READ
    mail and blind-replaced standing different bytes, the r- names broke the drain's
    oldest-first contract and outgrew the 128-char id cap, the ack path deleted its source
    before the backward ack was durable, durable settlement made the bounce note
    at-most-once, "any next request" retired backflow a redial never applied, orphaned
    phase markers leaked forever, and a no-information isolation verdict minted TERMINAL
    bounces."""

    HOST = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    ORIGIN = "abababababababababababababababab"

    def setUp(self):
        _reset()
        pm._postal_off_cache[0] = None
        pm._postal_off_warned[0] = False
        pm._bounced_done.clear()
        pm._bounced_done_loaded[0] = False
        for p in (pm._bounced_done_path(), pm._backflow_path(), pm.SESSION_FLAGS):
            try:
                p.unlink()
            except OSError:
                pass
        with pm._peer_lock:
            pm._peer_pending.clear()
        for h in (self.HOST, self.ORIGIN):
            shutil.rmtree(pm.OUTBOX / h, ignore_errors=True)
        shutil.rmtree(pm.MAILROOT / SID, ignore_errors=True)

    def tearDown(self):
        self.setUp()

    def test_a_replay_never_resurrects_read_mail(self):
        mid = "cc" * 16
        pm.deliver(SID, "peer", SID2, "hello", relay_mid=mid, relay_via="dd" * 16)
        got = pm.read_box(SID, consume=True)
        self.assertEqual([m["body"] for m in got], ["hello"])
        pm.deliver(SID, "peer", SID2, "hello", relay_mid=mid, relay_via="dd" * 16)  # replay
        self.assertEqual(list((pm._mailbox(SID) / "new").iterdir()), [],
                         "wave 2, reproduced: the crash replay minted a fresh UNREAD copy "
                         "of mail the agent had already read — delivered twice")
        self.assertTrue((pm._mailbox(SID) / "cur" / pm._relay_name("", mid)).exists())

    def test_b_replay_never_replaces_standing_different_bytes(self):
        mid = "ee" * 16
        pm.deliver(SID, "peer", SID2, "original", relay_mid=mid, relay_via="dd" * 16)
        pm.deliver(SID, "peer", SID2, "EDITED", relay_mid=mid, relay_via="dd" * 16)
        files = list((pm._mailbox(SID) / "new").iterdir())
        self.assertEqual(len(files), 1)
        self.assertIn("original", files[0].read_text(),
                      "wave 2, reproduced: the blind rename replaced standing unread mail "
                      "with whatever bytes the replay carried")

    def test_c_overlong_relay_mid_falls_back_to_a_receipt_safe_name(self):
        mid = "a" * 127                    # passes _safe_id alone; "r-"+mid is 129 chars
        name = pm.deliver(SID, "peer", SID2, "hello", relay_mid=mid, relay_via="dd" * 16)
        self.assertTrue(pm._safe_id(name),
                        "wave 2, reproduced: the 129-char delivery id failed every "
                        "/handoff-done validation — the completion receipt could never "
                        "file and the kernel re-posted forever")

    def test_d_drain_order_is_arrival_not_filename(self):
        mb = pm._mailbox(SID)
        (mb / "new").mkdir(parents=True, exist_ok=True)
        a = mb / "new" / ("r-" + "ff" * 16)
        a.write_text("From: peer\nFrom-Id: x\nDate: now\n\nfirst\n")
        os.utime(a, (100, 100))
        b = mb / "new" / "1756600000-xyz"
        b.write_text("From: peer\nFrom-Id: x\nDate: now\n\nsecond\n")
        os.utime(b, (200, 200))
        got = pm.read_box(SID, consume=False)
        self.assertEqual([m["body"] for m in got], ["first", "second"],
                         "wave 2, reproduced: 'r-' sorted after every epoch-named file, so "
                         "the relayed message that arrived FIRST was injected LAST")

    def test_e_forwarded_ack_is_durable_before_the_source_dies(self):
        mid = "aa" * 16
        d = pm.OUTBOX / self.HOST
        d.mkdir(parents=True, exist_ok=True)
        pm._atomic_json_put(d / (mid + ".json"),
                            {"mid": mid, "to": "web", "frm": "peer", "frm_id": "x" * 32,
                             "body": "hi", "origin": self.ORIGIN})
        seen = []
        real_del = pm.outbox_del

        def spy(h, m):
            try:
                snap = json.loads(pm._backflow_path().read_text())
            except Exception:
                snap = {}
            seen.append(mid in ((snap.get(self.ORIGIN) or {}).get("acks") or []))
            return real_del(h, m)

        pm.outbox_del = spy
        try:
            self.assertTrue(pm._ack_arrived(self.HOST, mid))
        finally:
            pm.outbox_del = real_del
        self.assertEqual(seen, [True],
                         "wave 2, reproduced: the delete ran FIRST — a crash before the "
                         "save left the backward ack in NO durable store (P1.10, ack path)")
        _po = pm._pending(self.ORIGIN)               # takes _peer_lock itself — never nest
        with pm._peer_lock:
            self.assertIn(mid, _po["acks"])

    def test_f_bounce_note_precedes_durable_settlement(self):
        mid = "bb" * 16
        d = pm.OUTBOX / self.HOST
        d.mkdir(parents=True, exist_ok=True)
        pm._atomic_json_put(d / (mid + ".json"),
                            {"mid": mid, "to": "web", "frm": "peer", "frm_id": SID,
                             "body": "hi"})
        real = pm.deliver

        def boom(*a, **kw):
            raise OSError("note delivery died")

        pm.deliver = boom
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                settled = pm._bounce_apply(self.HOST,
                                           {"mid": mid, "code": "recipient-unavailable"})
        finally:
            pm.deliver = real
        self.assertFalse(settled, "an undelivered note is NOT settled — the park retries")
        self.assertFalse(pm._bounced_done_has(self.HOST, mid),
                         "wave 2, reproduced: settling FIRST made the sender's note "
                         "at-most-once across restarts — a crash suppressed it forever")
        self.assertTrue((d / (mid + ".json")).exists(), "the retry source survives")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(pm._bounce_apply(self.HOST,
                                             {"mid": mid, "code": "recipient-unavailable"}))
        self.assertTrue(pm._bounced_done_has(self.HOST, mid))
        got = pm.read_box(SID, consume=True)
        self.assertTrue(any("undeliverable" in m["body"] for m in got),
                        "the retry delivered the note (r53 rule: a duplicate note over a "
                        "silently lost one, every time)")

    def test_g_backflow_retires_only_on_explicit_confirmation(self):
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        try:
            mid = "88" * 16
            p = pm._pending(self.HOST)
            with pm._peer_lock:
                p["acks"].append(mid)
                pm._backflow_save_locked()
            req = {"host": self.HOST, "epoch": 1, "proto": pm.PEER_PROTO, "presence": [],
                   "holds": [], "relays": [], "acks": [], "bounces": [], "wait": False,
                   "backflowAcks": {"acks": [], "bounces": []}}   # a MODERN dialer always
            #                        sends the key — absence means a legacy peer, for which
            #                        the old any-next-request retirement applies (r58)
            resp1, st1 = pm.peer_exchange_handle(dict(req))
            self.assertEqual(st1, 200)
            self.assertIn(mid, resp1.get("acks") or [])
            # a REDIAL after a dropped response confirms nothing — the slice re-rides
            resp2, _ = pm.peer_exchange_handle(dict(req))
            self.assertIn(mid, resp2.get("acks") or [],
                          "wave 2, reproduced: 'any next request' treated a redial after a "
                          "DROPPED response as proof of progress and deleted the unapplied "
                          "ack — the P1.10 loss shifted one request later")
            _ph = pm._pending(self.HOST)             # takes _peer_lock itself — never nest
            with pm._peer_lock:
                self.assertIn(mid, _ph["acks"])
            # the dialer's explicit confirmation retires exactly the named entries
            pm.peer_exchange_handle(dict(req, backflowAcks={"acks": [mid], "bounces": []}))
            with pm._peer_lock:
                self.assertNotIn(mid, _ph["acks"])
        finally:
            os.environ.pop("ROMP_POSTAL_PEERS", None)

    def test_h_dialer_confirms_applied_response_backflow(self):
        mid = "99" * 16
        resp = {"host": self.HOST, "epoch": 1, "proto": pm.PEER_PROTO, "busId": "e" * 32,
                "presence": [], "holds": [], "relays": [], "acks": [mid], "bounces": [],
                "reads": [], "handoffDone": []}
        req = pm.build_exchange_request(self.HOST, wait=False)
        self.assertTrue(pm.peer_exchange_apply(self.HOST, req, resp))
        p = pm._pending(self.HOST)
        with pm._peer_lock:
            self.assertIn(mid, (p.get("backflowAcks") or {}).get("acks") or [],
                          "the applied response-carried ack is recorded for confirmation")
        req2 = pm.build_exchange_request(self.HOST, wait=False)
        self.assertIn(mid, (req2.get("backflowAcks") or {}).get("acks") or [],
                      "…and rides the next request")
        resp2 = dict(resp, acks=[])
        self.assertTrue(pm.peer_exchange_apply(self.HOST, req2, resp2))
        with pm._peer_lock:
            self.assertNotIn(mid, (p.get("backflowAcks") or {}).get("acks") or [],
                             "an answered request retires its own confirmations")

    def test_i_orphaned_phase_markers_and_payload_temps_sweep(self):
        d = pm.OUTBOX / self.HOST
        d.mkdir(parents=True, exist_ok=True)
        (d / (".pubphase-" + "11" * 16)).write_text("1")   # no stage: nothing to protect
        (d / ".pub-x.deadbeef.tmp").write_text("payload")  # a crash-leaked payload copy
        with contextlib.redirect_stderr(io.StringIO()):
            pm._reconcile_relay_stages()
        self.assertEqual([f.name for f in d.iterdir()], [],
                         "wave 2, reproduced: one orphaned marker per incident leaked "
                         "forever — no sweeper matched either dotfile")

    def test_j_unproved_isolation_drops_the_relay_never_bounces(self):
        # BYO seam: another postal module pops ROMP_SESSIONS_FILE mid-suite (full-run only),
        # so the test plants its own live-agent file and restores whatever was there
        old_env = os.environ.get("ROMP_SESSIONS_FILE")
        sessfile = os.path.join(tempfile.mkdtemp(), "sessions.json")
        with open(sessfile, "w") as fh:
            fh.write(json.dumps([{"id": SID, "name": "web", "dir": "",
                                  "state": "waiting", "working": ""}]))
        os.environ["ROMP_SESSIONS_FILE"] = sessfile
        pm.SESSION_FLAGS.parent.mkdir(parents=True, exist_ok=True)
        pm.SESSION_FLAGS.write_text("{not valid json")
        pm._postal_off_cache[0] = None
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                verdict, bounce = pm._relay_in(
                    self.HOST, {"mid": "22" * 16, "to": "web", "frm": "peer",
                                "frm_id": "77" * 16, "body": "hi"}, token_proven=True)
        finally:
            if old_env is None:
                os.environ.pop("ROMP_SESSIONS_FILE", None)
            else:
                os.environ["ROMP_SESSIONS_FILE"] = old_env
            pm.SESSION_FLAGS.unlink()
        self.assertEqual((verdict, bounce), ("drop", None),
                         "wave 2, reproduced: a no-information isolation verdict minted a "
                         "TERMINAL recipient-isolated bounce — the origin deleted its "
                         "source and blamed the recipient, over one transient read fault")




class R58PostalAudit(unittest.TestCase):
    """the v1.3.30 audit, postal half: the ACK outlived an unsynced delivery effect (P1.7),
    long mids fell back to random names and lost crash idempotency (P1.8), the publication
    phase record was best-effort and follow-happy (P1.9 + P2.20), a failed backflow save
    still spent the downstream source (P1.10), _tl_append returned True past a dir-sync
    fault (P1.11), bare-mid identity aliased two peers (P2.16), read faults read as absence
    (P2.17), and junk consumed scanner budgets (P2.18)."""

    HOST = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    HOST2 = "abababababababababababababababab"

    def setUp(self):
        _reset()
        pm._postal_off_cache[0] = None
        pm._bounced_done.clear()
        pm._bounced_done_loaded[0] = False
        pm._seen_ids = None
        for p in (pm._bounced_done_path(), pm._backflow_path(), pm.SESSION_FLAGS,
                  pm.PEER_SEEN, pm.RECEIPTS_DONE):
            try:
                p.unlink()
            except OSError:
                pass
        with pm._peer_lock:
            pm._peer_pending.clear()
        for h in (self.HOST, self.HOST2):
            shutil.rmtree(pm.OUTBOX / h, ignore_errors=True)
            shutil.rmtree(pm.RECEIPTBOX / h, ignore_errors=True)
        shutil.rmtree(pm.MAILROOT / SID, ignore_errors=True)

    def tearDown(self):
        self.setUp()

    def test_a_two_origins_one_mid_both_deliver(self):
        # r58 P2.16, reproduced there: identity was the bare mid — the second peer's
        # message was ACKed while only the first body delivered
        mid = "aa" * 16
        n1 = pm.deliver(SID, "peer", "77" * 16, "from A", from_host=self.HOST,
                        relay_mid=mid, relay_via=self.HOST)
        n2 = pm.deliver(SID, "peer", "77" * 16, "from B", from_host=self.HOST2,
                        relay_mid=mid, relay_via=self.HOST2)
        self.assertNotEqual(n1, n2, "origin-scoped names: no aliasing")
        got = sorted(m["body"] for m in pm.read_box(SID, consume=True))
        self.assertEqual(got, ["from A", "from B"], "BOTH bodies delivered")

    def test_b_long_mids_keep_crash_idempotency(self):
        # r58 P1.8, reproduced there: a 127-byte mid's name fell back to RANDOM — a crash
        # replay delivered twice
        mid = "a" * 127
        n1 = pm.deliver(SID, "peer", "77" * 16, "hello", from_host=self.HOST,
                        relay_mid=mid, relay_via=self.HOST)
        n2 = pm.deliver(SID, "peer", "77" * 16, "hello", from_host=self.HOST,
                        relay_mid=mid, relay_via=self.HOST)   # the crash replay
        self.assertEqual(n1, n2, "deterministic for ANY valid mid length")
        self.assertEqual(len(list((pm._mailbox(SID) / "new").iterdir())), 1,
                         "one delivery, not two")
        self.assertTrue(pm._safe_id(n1), "…and the id passes every /handoff-done check")

    def test_c_delivery_effect_is_durable_before_return(self):
        # r58 P1.7, reproduced there: the effect vanished across a modeled crash while the
        # fsync'd seen-append survived — the replay ACKed without restoring the mail
        seen = []
        real = os.fsync

        def spy(fd):
            seen.append(os.fstat(fd).st_ino)
            real(fd)

        pm.os.fsync = spy
        try:
            name = pm.deliver(SID, "peer", "77" * 16, "hello", from_host=self.HOST,
                              relay_mid="bb" * 16, relay_via=self.HOST)
        finally:
            pm.os.fsync = real
        f = pm._mailbox(SID) / "new" / name
        self.assertIn(f.stat().st_ino, seen, "the mail file was fsync'd before return")
        self.assertIn((pm._mailbox(SID) / "new").stat().st_ino, seen,
                      "…and its directory entry too")

    def test_d_tl_append_reports_a_failed_dir_sync(self):
        # r58 P1.11: a swallowed EIO on the created log's dir sync returned True — callers
        # then deleted their only retry source over an undurable record
        real_fsync_dir = pm._fsync_dir

        def boom(p):
            raise OSError(5, "EIO")

        pm._fsync_dir = boom
        try:
            self.assertFalse(pm._tl_append("fresh-log.jsonl", {"t": 1}),
                             "a created log whose dir entry may not survive is NOT durable")
        finally:
            pm._fsync_dir = real_fsync_dir
        try:
            (pm.TLDIR / "fresh-log.jsonl").unlink()
        except OSError:
            pass

    def test_e_phase_marker_failure_refuses_the_publish(self):
        # r58 P1.9: the marker was best-effort and unsynced — combined with an uncertain
        # publication, boot deleted the durable stage and recorded `unpublished`
        d = pm.OUTBOX / self.HOST
        d.mkdir(parents=True, exist_ok=True)
        mid = "cc" * 16
        st = d / (".stage-" + mid)
        st.write_text(json.dumps({"mid": mid, "body": "payload"}))
        real = os.fsync

        def boom(fd):
            raise OSError(5, "EIO")

        pm.os.fsync = boom
        try:
            with self.assertRaises(pm._PublishNeverStarted):
                pm.outbox_publish_stage(self.HOST, mid, st)
        finally:
            pm.os.fsync = real
        self.assertTrue(st.exists(), "nothing started: the stage stands for retry")
        self.assertFalse((d / (mid + ".json")).exists())

    def test_f_phase_marker_never_follows_a_planted_symlink(self):
        # r58 P2.20, reproduced there: the predictable path followed a symlink and its
        # target was overwritten with "1"
        d = pm.OUTBOX / self.HOST
        d.mkdir(parents=True, exist_ok=True)
        mid = "dd" * 16
        st = d / (".stage-" + mid)
        st.write_text(json.dumps({"mid": mid, "body": "payload"}))
        victim = pm.STATE / "victim-r58.txt"
        victim.write_text("precious")
        os.symlink(victim, d / (".pubphase-" + mid))
        try:
            with self.assertRaises(pm._PublishNeverStarted):
                pm.outbox_publish_stage(self.HOST, mid, st)
            self.assertEqual(victim.read_text(), "precious",
                             "O_NOFOLLOW: the planted link's target is untouched")
        finally:
            victim.unlink(missing_ok=True)
            (d / (".pubphase-" + mid)).unlink(missing_ok=True)

    def test_g_failed_backflow_save_spends_nothing(self):
        # r58 P1.10, reproduced there: the save logged its failure and returned nothing —
        # both callers deleted their source; after a restart the ack was in NO store
        mid = "ee" * 16
        d = pm.OUTBOX / self.HOST
        d.mkdir(parents=True, exist_ok=True)
        pm._atomic_json_put(d / (mid + ".json"),
                            {"mid": mid, "to": "web", "frm": "peer", "frm_id": "x" * 32,
                             "body": "hi", "origin": self.HOST2})
        real = pm._atomic_json_put

        def boom(path, obj):
            if str(path) == str(pm._backflow_path()):
                raise OSError(5, "EIO")
            return real(path, obj)

        pm._atomic_json_put = boom
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertFalse(pm._ack_arrived(self.HOST, mid),
                                 "not durably applied — nothing confirms")
        finally:
            pm._atomic_json_put = real
        self.assertTrue((d / (mid + ".json")).exists(),
                        "the downstream source SURVIVES the failed save")
        with pm._peer_lock:
            self.assertIn(mid, (pm._peer_pending.get(self.HOST2) or {}).get("acks") or [],
                          "the memory entry stays UNSAVED (wave 2: withdrawing it raced a "
                          "concurrent duplicate that had skipped its own append)")
        self.assertTrue(pm._ack_arrived(self.HOST, mid), "the healed save applies")
        self.assertFalse((d / (mid + ".json")).exists())

    @unittest.skipIf(os.geteuid() == 0, "chmod 0 does not block reads for root")
    def test_h_receipt_park_read_fault_is_not_absence(self):
        # r58 P2.17: a fault reading the park settled under the RELAY id — the delivery id
        # was lost and /handoff-done re-posted forever
        rmid, dmid = "f1" * 16, "f2" * 16
        pm.receiptbox_put(self.HOST, rmid, dmid=dmid)
        park = pm.RECEIPTBOX / self.HOST / (rmid + ".json")
        os.chmod(park, 0)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                pm.receiptbox_del(self.HOST, rmid)
            self.assertTrue(park.exists(), "the unreadable park is KEPT for retry")
            self.assertEqual(pm._receipts_done(), {}, "…and nothing settled")
        finally:
            os.chmod(park, 0o644)
        pm.receiptbox_del(self.HOST, rmid)
        self.assertIn(dmid, pm._receipts_done(), "the healed read settles the DELIVERY id")

    @unittest.skipIf(os.geteuid() == 0, "chmod 0 does not block reads for root")
    def test_i_bounced_done_fault_never_latches_empty(self):
        # r58 P2.17: one EIO at load latched a fabricated-empty ledger — an already-settled
        # bounce re-noted the sender, and the belief survived the fault clearing
        self.assertTrue(pm._bounced_done_add(self.HOST, "a1" * 16))
        pm._bounced_done.clear()
        pm._bounced_done_loaded[0] = False           # a fresh process…
        os.chmod(pm._bounced_done_path(), 0)         # …booting under a fault
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertFalse(pm._bounced_done_has(self.HOST, "a1" * 16))
            self.assertFalse(pm._bounced_done_loaded[0],
                             "the fault never latches — the next call retries the load")
        finally:
            os.chmod(pm._bounced_done_path(), 0o644)
        self.assertTrue(pm._bounced_done_has(self.HOST, "a1" * 16),
                        "…and the healed load restores the settlement")

    def test_j_junk_never_consumes_the_ack_budget(self):
        cap = pm.PEER_LIST_LIMITS["acks"]
        rows = [123] * (cap + 50) + ["99" * 16]
        self.assertEqual(pm._peer_ack_ids(rows), ["99" * 16],
                         "r58 P2.18: junk is discarded WITHOUT spending the cap")

    def test_k_legacy_dialer_still_retires_its_slice(self):
        # the disclosed mixed-version issue: a pre-r57-wave-2 peer never sends
        # backflowAcks, so its slice re-rode forever — absence of the KEY restores the old
        # any-next-request retirement for that peer alone
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        try:
            mid = "b2" * 16
            p = pm._pending(self.HOST)
            with pm._peer_lock:
                p["acks"].append(mid)
                pm._backflow_save_locked()
            legacy_req = {"host": self.HOST, "epoch": 1, "proto": pm.PEER_PROTO,
                          "presence": [], "holds": [], "relays": [], "acks": [],
                          "bounces": [], "wait": False}   # NO backflowAcks key
            resp1, _ = pm.peer_exchange_handle(dict(legacy_req))
            self.assertIn(mid, resp1.get("acks") or [])
            pm.peer_exchange_handle(dict(legacy_req))    # the legacy peer's next request
            with pm._peer_lock:
                self.assertNotIn(mid, (pm._peer_pending.get(self.HOST) or {}).get("acks")
                                 or [], "the old contract holds for the old peer")
        finally:
            os.environ.pop("ROMP_POSTAL_PEERS", None)

    def test_l_unsafe_outbox_ids_are_judged_aside(self):
        # r58 P2.18: an id the ack path must refuse was listed forever, settled never
        d = pm.OUTBOX / self.HOST
        d.mkdir(parents=True, exist_ok=True)
        (d / "bad id.json").write_text(json.dumps({"mid": "bad id", "to": "web"}))
        with contextlib.redirect_stderr(io.StringIO()):
            out = pm.outbox_list(self.HOST)
        self.assertEqual(out, [], "never listed")
        self.assertFalse((d / "bad id.json").exists(), "…and moved aside, not stranded")

    def test_m_receipt_parks_scope_by_origin(self):
        # r58 wave 2, reproduced: two origins' same-mid mail through one hub parked on one
        # bare-mid file — the second receipt replaced the first and one sender never
        # learned its mail landed
        mid, dA, dB = "a3" * 16, "a4" * 16, "a5" * 16
        pm.receiptbox_put(self.HOST, mid, origin=self.HOST2, dmid=dA)
        pm.receiptbox_put(self.HOST, mid, origin="cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd",
                          dmid=dB)
        rows = pm.receiptbox_list(self.HOST)
        self.assertEqual(len(rows), 2, "both origins' receipts park independently")
        pm.receiptbox_del(self.HOST, mid, origin=self.HOST2)
        self.assertIn(dA, pm._receipts_done(), "A's delivery id settled")
        self.assertNotIn(dB, pm._receipts_done(), "…without touching B's")
        self.assertEqual(len(pm.receiptbox_list(self.HOST)), 1, "B's park stands")

    def test_n_legacy_named_deliveries_stay_idempotent(self):
        # r58 wave 2: pre-r58 files wear r-<mid> — invisible to the digest skip, a crash
        # replay re-delivered them, read mail included
        mid = "a6" * 16
        mb = pm._mailbox(SID)
        (mb / "cur").mkdir(parents=True, exist_ok=True)
        (mb / "cur" / ("r-" + mid)).write_text(
            "From: peer\nFrom-Id: x\nDate: now\n\nold\n")   # an already-READ v1.3.30 file
        pm.deliver(SID, "peer", "77" * 16, "old", from_host=self.HOST,
                   relay_mid=mid, relay_via=self.HOST)          # the crash replay
        self.assertEqual(list((mb / "new").iterdir()), [],
                         "the legacy spelling satisfies the skip — nothing re-delivers")




class R59PostalAudit(unittest.TestCase):
    """the v1.3.31 audit, postal half: an unreadable backflow ledger was overwritten
    (P1.6), a first delivery's mailbox ancestors were unsynced (P1.7), origin scoping
    stopped at the seen ledger (P1.8), a failed withdraw recorded live mail unpublished
    (P1.9), an unreadable settlement re-noted the sender (P2.7), receipts-done folded
    faults to {} and one add erased every confirmation (P2.12), and a planted FIFO at the
    phase-marker path hung publication forever (P2.13)."""

    HOST = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    HOST2 = "abababababababababababababababab"

    def setUp(self):
        _reset()
        pm._postal_off_cache[0] = None
        pm._bounced_done.clear()
        pm._bounced_done_loaded[0] = False
        pm._backflow_loaded[0] = False
        for p in (pm._bounced_done_path(), pm._backflow_path(), pm.SESSION_FLAGS,
                  pm.RECEIPTS_DONE):
            try:
                p.unlink()
            except OSError:
                pass
        with pm._peer_lock:
            pm._peer_pending.clear()
        for h in (self.HOST, self.HOST2):
            shutil.rmtree(pm.OUTBOX / h, ignore_errors=True)
            shutil.rmtree(pm.READBOX / h, ignore_errors=True)
        shutil.rmtree(pm.MAILROOT / SID, ignore_errors=True)

    def tearDown(self):
        self.setUp()

    def test_a_readbox_parks_scope_by_origin(self):
        # r59 P1.8: two origins' same-mid reads through one hub overwrote one bare-mid
        # park — a read receipt was permanently lost
        mid = "c1" * 16
        pm.readbox_put(self.HOST, {"mid": mid, "origin": self.HOST2, "unread": False})
        pm.readbox_put(self.HOST, {"mid": mid,
                                   "origin": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd",
                                   "unread": False})
        rows = pm.readbox_list(self.HOST)
        self.assertEqual(len(rows), 2, "both origins' receipts park independently")
        pm.readbox_del(self.HOST, {"mid": mid, "origin": self.HOST2, "unread": False})
        self.assertEqual(len(pm.readbox_list(self.HOST)), 1, "the other stands")

    @unittest.skipIf(os.geteuid() == 0, "chmod 0 does not block reads for root")
    def test_b_unreadable_backflow_is_never_overwritten(self):
        # r59 P1.6, reproduced there: an injected read EIO over a durable old ACK, then
        # one new ACK — the rewrite kept only the new host
        pm._backflow_load()              # latch (ENOENT = provably empty)
        p = pm._pending(self.HOST)
        with pm._peer_lock:
            p["acks"].append("d1" * 16)
            self.assertTrue(pm._backflow_save_locked())
        with pm._peer_lock:
            pm._peer_pending.clear()                 # the process died
        pm._backflow_loaded[0] = False               # …and reboots under a read fault
        os.chmod(pm._backflow_path(), 0)
        p2 = pm._pending(self.HOST2)
        try:
            with pm._peer_lock:
                p2["acks"].append("d2" * 16)
                self.assertFalse(pm._backflow_save_locked(),
                                 "the save is HELD while the ledger is unfolded")
        finally:
            os.chmod(pm._backflow_path(), 0o644)
        pm._backflow_load()              # the heal folds BEFORE any save (r59 wave 2: the
        #                                  save's own retry-fold resurrected retired acks)
        with pm._peer_lock:
            self.assertTrue(pm._backflow_save_locked(), "the healed save lands")
        d = json.loads(pm._backflow_path().read_text())
        self.assertIn(self.HOST, d, "the durable old ack SURVIVED the fault window")
        self.assertIn(self.HOST2, d)

    def test_c_first_delivery_syncs_the_mailbox_ancestors(self):
        # r59 P1.7, reproduced there: the message and new/ were synced but the freshly
        # created MAILROOT/<sid> entry was not — the modeled crash lost the whole mailbox
        # while the durable seen receipt survived
        seen = []
        real = os.fsync

        def spy(fd):
            seen.append(os.fstat(fd).st_ino)
            real(fd)

        pm.os.fsync = spy
        try:
            pm.deliver(SID, "peer", "77" * 16, "hello")
        finally:
            pm.os.fsync = real
        self.assertIn(pm._mailbox(SID).stat().st_ino, seen, "the sid dir entry is durable")
        self.assertIn(pm.MAILROOT.stat().st_ino, seen, "…and the mailroot's")

    def test_d_failed_withdraw_is_live_not_unpublished(self):
        # r59 P1.9, reproduced there: dir-sync fails AND the withdraw fails — the mail is
        # visible, yet an `unpublished` event was appended; replay then ACKed the final,
        # leaving contradictory permanent accounting
        real_fsync_dir = pm._fsync_dir
        real_unlink = pm.Path.unlink
        mb = pm._mailbox(SID)

        def boom_dir(p):
            if str(p) == str(mb / "new"):
                raise OSError(5, "EIO")
            return real_fsync_dir(p)

        def boom_unlink(self, *a, **kw):
            if str(self).startswith(str(mb / "new")):
                raise OSError(5, "EIO")
            return real_unlink(self, *a, **kw)

        pm._fsync_dir = boom_dir
        pm.Path.unlink = boom_unlink
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(pm._DeliverUndurable) as _ctx:
                    pm.deliver(SID, "peer", "77" * 16, "hello")
            name = str(_ctx.exception)
        finally:
            pm._fsync_dir = real_fsync_dir
            pm.Path.unlink = real_unlink
        self.assertTrue((mb / "new" / name).exists(),
                        "the mail is LIVE — and the DISTINCT raise means no ack ever "
                        "correlates with it (r59 wave 2)")
        rows = [json.loads(l) for l in
                (pm.TLDIR / "messages.jsonl").read_text().splitlines() if l.strip()]
        self.assertFalse(any(r.get("ev") == "unpublished" and r.get("id") == name
                             for r in rows),
                         "…and NO contradictory unpublished row was filed")

    @unittest.skipIf(os.geteuid() == 0, "chmod 0 does not block reads for root")
    def test_e_unreadable_settlement_holds_the_bounce(self):
        # r59 P2.7: "not settled" was a GUESS under an unreadable ledger — the sender was
        # re-noted and a duplicate terminal row filed
        self.assertTrue(pm._bounced_done_add(self.HOST, "e1" * 16))
        d = pm.OUTBOX / self.HOST
        d.mkdir(parents=True, exist_ok=True)
        pm._atomic_json_put(d / (("e1" * 16) + ".json"),
                            {"mid": "e1" * 16, "to": "web", "frm_id": SID, "body": "x"})
        pm._bounced_done.clear()
        pm._bounced_done_loaded[0] = False
        os.chmod(pm._bounced_done_path(), 0)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertFalse(pm._bounce_apply(
                    self.HOST, {"mid": "e1" * 16, "code": "recipient-unavailable"}),
                    "held, not re-noted")
        finally:
            os.chmod(pm._bounced_done_path(), 0o644)
        self.assertEqual(pm.read_box(SID, consume=True), [], "no duplicate note landed")

    @unittest.skipIf(os.geteuid() == 0, "chmod 0 does not block reads for root")
    def test_f_receipts_done_never_rewrites_through_a_fault(self):
        # r59 P2.12: read faults folded to {} — one new confirmation overwrote them all
        pm._atomic_json_put(pm.RECEIPTS_DONE, {"old-one": 1})
        os.chmod(pm.RECEIPTS_DONE, 0)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                pm._receipts_done_add("f1" * 16)
        finally:
            os.chmod(pm.RECEIPTS_DONE, 0o644)
        self.assertEqual(json.loads(pm.RECEIPTS_DONE.read_text()), {"old-one": 1},
                         "the fault persisted NOTHING over the standing confirmations")
        pm._receipts_done_add("f1" * 16)
        self.assertIn("old-one", json.loads(pm.RECEIPTS_DONE.read_text()))
        self.assertIn("f1" * 16, json.loads(pm.RECEIPTS_DONE.read_text()))

    def test_g_planted_fifo_never_blocks_publication(self):
        # r59 P2.13, reproduced there: O_NOFOLLOW does not reject FIFOs and the blocking
        # write-only open hung the bus thread forever
        d = pm.OUTBOX / self.HOST
        d.mkdir(parents=True, exist_ok=True)
        mid = "f2" * 16
        st = d / (".stage-" + mid)
        st.write_text(json.dumps({"mid": mid, "body": "x"}))
        os.mkfifo(d / (".pubphase-" + mid))
        try:
            with self.assertRaises(pm._PublishNeverStarted):
                pm.outbox_publish_stage(self.HOST, mid, st)   # returns, never hangs
            self.assertTrue(st.exists(), "the stage stands for retry")
        finally:
            (d / (".pubphase-" + mid)).unlink(missing_ok=True)

    def test_h_preserved_bounce_rows_survive_rewrites(self):
        # r59 P2.11: an invalid standing row was skipped at load, and the next park's
        # rewrite erased it
        pm._atomic_json_put(pm._pending_bounces_path(),
                            {"weird|row": "NOT-A-DICT", "ok|row": {
                                "host": self.HOST, "mid": "a1" * 16,
                                "code": "recipient-unavailable"}})
        pm._pending_bounces.clear()
        pm._pending_bounces_loaded[0] = False
        pm._pending_bounce_park(self.HOST, "a2" * 16, "recipient-unavailable")
        d = json.loads(pm._pending_bounces_path().read_text())
        self.assertIn("weird|row", d, "the unreadable row RIDES every rewrite untouched")


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
        # a crash AFTER the publish, before the stage unlink: the mail is real — no row.
        # CONTENT decides since r57 P1.9: identical bytes = this stage's message published
        body = json.dumps({"mid": "px-202.1_888.TESTHOST"})
        (self.hd / ".stage-px-202.1_888.TESTHOST").write_text(body)
        (self.hd / "px-202.1_888.TESTHOST.json").write_text(body)
        self.assertEqual(pm._reconcile_relay_stages(), 0)
        self.assertEqual([r for r in _rows() if r.get("ev") == "unpublished"], [])
        self.assertFalse((self.hd / ".stage-px-202.1_888.TESTHOST").exists())
        # …while a DIFFERENT same-mid final keeps BOTH copies (the r57 audit's P1.9: boot
        # used to spend the staged payload's only copy over someone else's final)
        (self.hd / ".stage-px-203.1_999.TESTHOST").write_text("the staged message")
        (self.hd / "px-203.1_999.TESTHOST.json").write_text("a different message")
        with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
            pm._reconcile_relay_stages()
        self.assertTrue((self.hd / ".stage-px-203.1_999.TESTHOST").exists())
        self.assertTrue((self.hd / "px-203.1_999.TESTHOST.json").exists())

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
        pm._pending_bounces_loaded[0] = False
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
            self.assertIn("TESTHOST2|px-405.1_ee.TESTHOST", pm._pending_bounces,
                          "…the bounce parked durably instead (host-keyed since r56 P2.19)")
            self.assertTrue(pm._pending_bounces_path().exists())
        finally:
            os.chmod(f, 0o644)
        pm._flush_pending_bounces()                  # the read proves on the next pass
        bounced = [r for r in _rows() if r.get("ev") == "bounced"]
        self.assertEqual([r["id"] for r in bounced], ["px-405.1_ee.TESTHOST"],
                         "the recovered record takes the FULL path — receipt and return note")
        self.assertFalse(f.exists(), "…and retires with it")
        self.assertNotIn("TESTHOST2|px-405.1_ee.TESTHOST", pm._pending_bounces,
                         "the parked entry clears")
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
        _calls = [0]
        def _fail_after_marker(p):
            # the r58 phase marker records intent durably BEFORE the link — its own dir
            # fsync (call 1) must succeed for this test to reach the post-link arm
            _calls[0] += 1
            if _calls[0] > 1:
                raise RuntimeError("forced")
            return real(p)
        pm._fsync_dir = _fail_after_marker
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
        pm._pending_bounces_loaded[0] = False
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
        _n = [0]
        def _boom_after_marker(fd):
            # the r58 phase marker fsyncs its file + dir first (calls 1-2, must land or the
            # publish honestly refuses pre-link); the POST-link syncs are the ones failing
            _n[0] += 1
            if _n[0] > 2:
                raise OSError(5, "EIO")
            return real(fd)
        pm.os.fsync = _boom_after_marker
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

class R55Wave2Retries(unittest.TestCase):
    """the r55 wave-2 verification's postal cluster: the consume drain raised through a
    failed read receipt AFTER the claim, corrupt handoff provenance 404-looped past intact
    maildir headers, the parked-bounce flush skipped the forwarded-vs-ours arbitration and
    cleared durable parks over memory-only queued rows, and two exchange threads
    double-drained the retry pumps."""

    def setUp(self):
        _reset()
        shutil.rmtree(pm.OUTBOX, ignore_errors=True)
        pm._pending_terminal_rows[:] = []
        pm._pending_bounces.clear()
        pm._pending_bounces_loaded[0] = False
        try:
            pm._pending_bounces_path().unlink()
        except OSError:
            pass
        self.now = int(time.time())
        self.hd = pm.OUTBOX / "TESTHOST2"
        self.hd.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.setUp()

    def test_a_failed_read_receipt_never_loses_claimed_mail(self):
        # r55 wave 2: _fsync_dir's honest EIO propagated through readbox_put AFTER the
        # rename to cur/ — the drain errored with the message already consumed, and the
        # agent never received mail it could never reclaim
        sid = "77777777-8888-9999-aaaa-bbbbbbbbbbbb"
        mid = pm.deliver(sid, "peer", "88888888-9999-aaaa-bbbb-cccccccccccc", "hello there")
        self.assertTrue(mid)
        real = pm._queue_read_receipt
        pm._queue_read_receipt = lambda *a, **kw: (_ for _ in ()).throw(OSError(5, "EIO"))
        try:
            out = pm.read_box(sid, consume=True)
        finally:
            pm._queue_read_receipt = real
        self.assertEqual([r["body"] for r in out], ["hello there"],
                         "the claimed mail DELIVERS — the receipt is best-effort behind it")

    def test_corrupt_provenance_consults_the_maildir_before_any_404(self):
        # r55 wave 2: the shape-guard 404 looped forever while the delivered mail's own
        # intact headers sat in the maildir — the promised second source was unreachable
        sid = "77777777-8888-9999-aaaa-bbbbbbbbbbbb"
        mid = pm.deliver(sid, "peer", "", "relayed thing",
                         relay_mid="px-701.1_aa.TESTHOST", relay_via="TESTHOST2")
        with open(pm.TLDIR / "messages.jsonl", "a") as fh:   # the CORRUPT historic twin
            fh.write(json.dumps({"t": self.now, "ev": "sent", "id": mid,
                                 "relay_mid": [], "relay_via": "TESTHOST2"}) + "\n")
        ans, code = pm.handoff_done_apply({"mid": mid})
        self.assertEqual(code, 200, "the maildir headers decide — never a loop past them")
        self.assertEqual(ans.get("queued"), "TESTHOST2",
                         "…and the completion receipt relays to the via-host")

    def test_the_parked_bounce_flush_is_origin_aware(self):
        # r55 wave 2: the flush called _bounce_apply directly — a HUB's parked bounce
        # delivered its return note as LOCAL mail instead of relaying backward
        mid = "px-702.1_bb.TESTHOST"
        (self.hd / (mid + ".json")).write_text(json.dumps(
            {"mid": mid, "body": "hi", "origin": "TESTHOST3"}))
        pm._pending_bounce_park("TESTHOST2", mid, "recipient-unavailable")
        pm._flush_pending_bounces()
        with pm._peer_lock:
            back = list(pm._peer_pending.get("TESTHOST3", {}).get("bounces") or [])
        self.assertEqual([b["mid"] for b in back], [mid],
                         "the bounce relays BACKWARD to the origin — never a local note")
        self.assertNotIn("TESTHOST2|" + mid, pm._pending_bounces,
                         "…and the park clears on settlement")

    def test_a_failed_terminal_append_keeps_the_durable_park(self):
        # r55 wave 2: the malformed arm returned True after queuing a MEMORY-only row —
        # the durable park cleared, and a process death lost both
        mid = "px-703.1_cc.TESTHOST"
        (self.hd / (mid + ".json")).write_text("{garbage")
        pm._pending_bounce_park("TESTHOST2", mid, "recipient-unavailable")
        real = pm._tl_append
        pm._tl_append = lambda name, row: False
        try:
            pm._flush_pending_bounces()
        finally:
            pm._tl_append = real
        self.assertIn("TESTHOST2|" + mid, pm._pending_bounces,
                      "the park SURVIVES a failed terminal append — never cleared over a "
                      "memory-only queued row")
        pm._flush_pending_bounces()                  # the store recovers
        self.assertNotIn("TESTHOST2|" + mid, pm._pending_bounces)
        self.assertIn(mid, [r["id"] for r in _rows() if r.get("ev") == "bounced"])

    def test_the_drain_is_single_flight(self):
        src = open(os.path.join(BIN, "romp-postal-service"), encoding="utf-8").read()
        i = src.index("def _drain_postal_retries():")
        window = src[i:i + 900]
        self.assertIn("_drain_lock.acquire(blocking=False)", window,
                      "two exchange threads double-drained: duplicate return notes and "
                      "terminal rows (r55 wave 2)")

class R56DurableBeforeAck(unittest.TestCase):
    """the v1.3.28 audit's postal cluster: publication lacked an honest uncertain state
    (P1.7), bounce parking acked before durability (P1.8), the dedupe ledger failed open
    (P1.9), receipts acked over failed appends (P1.10), and the bounded scanners accepted
    schema poison (P2.14-P2.18)."""

    def setUp(self):
        _reset()
        shutil.rmtree(pm.OUTBOX, ignore_errors=True)
        shutil.rmtree(pm.RECEIPTBOX, ignore_errors=True)
        shutil.rmtree(pm.READBOX, ignore_errors=True)
        pm._pending_terminal_rows[:] = []
        pm._pending_bounces.clear()
        pm._pending_bounces_loaded[0] = False
        pm._bounced_done.clear()
        pm._bounced_done_loaded[0] = False
        try:
            pm._bounced_done_path().unlink()
        except OSError:
            pass
        pm._seen_ids = None
        pm._seen_pending_durable.clear()
        for p in (pm._pending_bounces_path(), pm.PEER_SEEN):
            try:
                p.unlink()
            except OSError:
                pass
        self.now = int(time.time())
        self.hd = pm.OUTBOX / "TESTHOST2"
        self.hd.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.setUp()

    def test_a_link_that_created_then_failed_is_uncertain(self):
        # r56 P1.7a, executed there: a link() that created the final and then reported EIO
        # was classified never-started — `unpublished` was filed over LIVE mail
        mid = "px-801.1_aa.TESTHOST"
        stage = self.hd / (".stage-" + mid)
        body = json.dumps({"mid": mid, "body": "hi"})
        stage.write_text(body)
        real = os.link
        def _link_then_fail(src, dst):
            real(src, dst)
            raise OSError(5, "EIO")
        pm.os.link = _link_then_fail
        try:
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                pm.outbox_publish_stage("TESTHOST2", mid, stage)   # NO raise: uncertain
        finally:
            pm.os.link = real
        self.assertTrue((self.hd / (mid + ".json")).exists(), "the mail is LIVE")
        self.assertTrue(stage.exists(), "…and the stage stays for boot arbitration")

    def test_boot_arbitration_keeps_a_stage_over_an_unreadable_final(self):
        # r56 P1.7c, executed there: Path.exists() folded an unreadable final to "absent",
        # boot filed `unpublished` over relayable mail and deleted its recovery stage
        mid = "px-802.1_bb.TESTHOST"
        final = self.hd / (mid + ".json")
        final.write_text(json.dumps({"mid": mid, "body": "hi"}))
        stage = self.hd / (".stage-" + mid)
        stage.write_text("1")
        os.chmod(final, 0)
        # os.stat still SUCCEEDS on a chmod-0 file (read fails; stat needs only the dir) —
        # simulate a stat-level fault instead
        real = pm.os.stat
        def _stat(p, *a, **kw):
            if str(p).endswith(mid + ".json"):
                raise OSError(5, "EIO")
            return real(p, *a, **kw)
        pm.os.stat = _stat
        try:
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                pm._reconcile_relay_stages()
        finally:
            pm.os.stat = real
            os.chmod(final, 0o644)
        self.assertTrue(stage.exists(), "the stage SURVIVES an unknowable final")
        self.assertEqual([r for r in _rows() if r.get("ev") == "unpublished"], [],
                         "…and no false unpublished row files over relayable mail")

    def test_a_non_durable_park_fails_the_exchange(self):
        # r56 P1.8, executed there: a failed park write still answered 200 — after a process
        # death the bounce was gone and the peer never re-sent
        mid = "px-803.1_cc.TESTHOST"
        f = self.hd / (mid + ".json")
        f.write_text(json.dumps({"mid": mid, "body": "hi"}))
        os.chmod(f, 0)
        real = pm._atomic_json_put
        pm._atomic_json_put = lambda *a, **kw: (_ for _ in ()).throw(OSError(28, "ENOSPC"))
        try:
            out = pm._bounce_arrived("TESTHOST2", {"mid": mid,
                                                   "code": "recipient-unavailable"})
        finally:
            pm._atomic_json_put = real
            os.chmod(f, 0o644)
        self.assertIs(out, False, "not durable → the exchange must FAIL (503) so the peer "
                                  "re-sends; every arm is idempotent")
        src = open(os.path.join(BIN, "romp-postal-service"), encoding="utf-8").read()
        self.assertIn("_ExchangeNotDurable", src)
        self.assertIn('return self._send({"error": "not durable', src)

    def test_the_dedupe_ledger_fails_closed(self):
        # r56 P1.9, executed there: an EIO loaded the ledger as EMPTY and the same message
        # delivered twice with an ack
        pm.PEER_SEEN.parent.mkdir(parents=True, exist_ok=True)
        pm.PEER_SEEN.write_text("px-804.1_dd.TESTHOST\n")
        os.chmod(pm.PEER_SEEN, 0)
        try:
            pm._seen_ids = None
            self.assertIsNone(pm.peer_seen_check("px-804.1_dd.TESTHOST"),
                              "unreadable = NO VERDICT, never fresh")
        finally:
            os.chmod(pm.PEER_SEEN, 0o644)
        pm._seen_ids = None
        self.assertTrue(pm.peer_seen_check("px-804.1_dd.TESTHOST"))

    def test_the_ack_waits_for_the_durable_seen_append(self):
        # r56 P1.9's second leg: the swallowed append failure acked an effect whose dedupe
        # record died with the process
        pm._seen_ids = set()
        pm._seen_order = []
        real = pm.os.fsync
        pm.os.fsync = lambda fd: (_ for _ in ()).throw(OSError(5, "EIO"))
        try:
            with pm._seen_lock:
                ok = pm._peer_seen_add_locked("px-805.1_ee.TESTHOST")
        finally:
            pm.os.fsync = real
        self.assertFalse(ok, "no durable record, no ack")
        self.assertIn("px-805.1_ee.TESTHOST", pm._seen_pending_durable)
        with pm._seen_lock:
            self.assertTrue(pm._peer_seen_append_durable("px-805.1_ee.TESTHOST"))
        self.assertNotIn("px-805.1_ee.TESTHOST", pm._seen_pending_durable)

    def test_receipt_acks_wait_for_durable_application(self):
        # r56 P1.10, executed there: apply_ok:true with an empty timeline — the remote then
        # deleted the only durable receipt
        real = pm._tl_append
        pm._tl_append = lambda name, row: False
        try:
            out = pm._read_arrived("TESTHOST2", {"mid": "px-806.1_ff.TESTHOST"})
        finally:
            pm._tl_append = real
        self.assertIs(out, False, "no durable exec row, no readAck")
        self.assertIn("px-806.1_ff.TESTHOST",
                      [r["id"] for r in pm._pending_terminal_rows],
                      "…and the row itself queues for the retry pumps")
        pm._drain_postal_retries()
        self.assertIn("px-806.1_ff.TESTHOST",
                      [r["id"] for r in _rows() if r.get("ev") == "exec"])

    def test_settled_bounces_never_renote(self):
        # r56 P2.14, executed there: a failed source deletion re-ran the whole path — two
        # return notes and two terminal rows while the source kept relaying
        mid = "px-807.1_gg.TESTHOST"
        f = self.hd / (mid + ".json")
        f.write_text(json.dumps({"mid": mid, "body": "hi",
                                 "frm_id": "77777777-8888-9999-aaaa-bbbbbbbbbbbb"}))
        real = pm.outbox_del
        pm.outbox_del = lambda h, m: False           # the deletion fails
        try:
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
                self.assertIs(pm._bounce_apply("TESTHOST2",
                                               {"mid": mid,
                                                "code": "recipient-unavailable"}), True)
        finally:
            pm.outbox_del = real
        self.assertTrue(f.exists())
        with __import__("contextlib").redirect_stderr(__import__("io").StringIO()):
            self.assertIs(pm._bounce_apply("TESTHOST2",
                                           {"mid": mid,
                                            "code": "recipient-unavailable"}), True)
        bounced = [r for r in _rows() if r.get("ev") == "bounced"]
        self.assertEqual(len(bounced), 1, "ONE terminal row — the retry deletes, never re-notes")
        self.assertFalse(f.exists(), "…and the deletion retried")

    def test_scanners_bind_schema_and_filenames(self):
        # r56 P2.15/P2.16/P2.17/P2.18
        rb = pm.RECEIPTBOX / "TESTHOST2"
        rb.mkdir(parents=True, exist_ok=True)
        (rb / "px-808.1_hh.TESTHOST.json").write_text(json.dumps({"mid": "px-999.9_zz.OTHER"}))
        self.assertEqual(pm.receiptbox_list("TESTHOST2"), [],
                         "a payload naming ANOTHER mid never marks it done (P2.16)")
        self.assertFalse((rb / "px-808.1_hh.TESTHOST.json").exists(), "…and quarantines")
        db = pm.READBOX / "TESTHOST2"
        db.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (db / ("px-809.%d_ii.TESTHOST.json" % i)).write_text("[]")
        (db / "px-810.1_jj.TESTHOST.json").write_text(
            json.dumps({"mid": "px-810.1_jj.TESTHOST"}))
        rows = pm.readbox_list("TESTHOST2", limit=2)
        self.assertEqual([r["mid"] for r in rows], ["px-810.1_jj.TESTHOST"],
                         "garbage never consumes the bounded budget (P2.15)")
        f = self.hd / "px-811.1_kk.TESTHOST.json"
        f.write_text(json.dumps({"mid": 123, "body": "x"}))
        with self.assertRaises(pm._OutboxMalformed):
            pm.outbox_get("TESTHOST2", "px-811.1_kk.TESTHOST")   # numeric mid (P2.17)
        f.write_text("[]")
        out = pm._recall("77777777-8888-9999-aaaa-bbbbbbbbbbbb", "", "")
        self.assertIsInstance(out, list,
                              "recall survives schema poison — a [] record used to raise "
                              "AttributeError and abort the WHOLE recall (P2.18)")


class R60PostalAudit(unittest.TestCase):
    """the v1.3.32 audit, postal half: origin scoping stopped before verdicts and legacy
    cleanup (P1.5), first-generation dirs were not durably linked into their parents
    (P1.6), a successful-but-unsynced withdrawal was recorded as definite non-publication
    (P1.7), backflow part-validated a ledger and then authorized destructive rewrites
    (P1.8), pending-bounce cleanup erased a durable ledger it never read (P1.9),
    receipts-done was neither proved nor concurrency-safe (P2.11), and a FIFO at the
    deterministic relay staging name hung delivery under _seen_lock (P2.13)."""

    HOST = "fefefefefefefefefefefefefefefefe"

    def setUp(self):
        _reset()
        pm._backflow_loaded[0] = False
        pm._backflow_preserved.clear()
        pm._pending_bounces.clear()
        pm._pending_bounces_loaded[0] = False
        with pm._peer_lock:
            pm._peer_pending.clear()
        for p in (pm._backflow_path(), pm._pending_bounces_path(), pm.RECEIPTS_DONE):
            try:
                p.unlink()
            except OSError:
                pass
        for d in (pm.QUARANTINE, pm.RECEIPTBOX / self.HOST, pm.READBOX / self.HOST,
                  pm.CORRUPT, pm.MAILROOT / SID):
            shutil.rmtree(d, ignore_errors=True)

    def tearDown(self):
        self.setUp()

    # ── P1.5: origin scoping through verdicts and legacy cleanup ────────────────────

    def test_a_unscoped_verdict_over_two_origins_is_refused(self):
        # reproduced by the audit: one mid-only approval delivered BOTH origins' held
        # messages; one denial dropped both
        m = {"mid": "m1" * 8, "to": "web", "frm": "api", "frm_id": "", "body": "from A"}
        self.assertTrue(pm._quarantine_put("hostA", m, SID))
        self.assertTrue(pm._quarantine_put("hostB", dict(m, body="from B"), SID))
        ok, err = pm.quarantine_decide("m1" * 8, "deny")
        self.assertFalse(ok, "an unscoped verdict over two origins must refuse")
        self.assertIn("origins hold", err or "")
        ok, err = pm.quarantine_decide("m1" * 8, "deny", origin="hostA")
        self.assertTrue(ok, err)
        self.assertIsNone(pm.quarantine_get("m1" * 8, origin="hostA"),
                          "origin A's hold is decided")
        self.assertIsNotNone(pm.quarantine_get("m1" * 8, origin="hostB"),
                             "origin B's hold STANDS — the verdict never touched it")

    def test_b_bare_quarantine_hold_is_origin_verified(self):
        # the legacy bare-mid spelling carries no origin in its NAME: an origin-scoped
        # verdict must verify the record before acting on it
        pm.QUARANTINE.mkdir(parents=True, exist_ok=True)
        (pm.QUARANTINE / ("m2" * 8 + ".json")).write_text(json.dumps(
            {"mid": "m2" * 8, "to": "web", "toId": SID, "frm": "api",
             "origin": "hostB", "body": "B's held mail", "at": 1}))
        self.assertEqual(pm._quarantine_paths("m2" * 8, origin="hostA"), [],
                         "origin A's verdict never reaches origin B's bare hold")
        self.assertEqual(len(pm._quarantine_paths("m2" * 8, origin="hostB")), 1)

    def test_c_receiptbox_del_verifies_the_bare_parks_origin(self):
        # reproduced by the audit: an origin-A acknowledgment fell through both scoped
        # spellings to origin B's bare legacy file — deleted it and marked B's done
        d = pm.RECEIPTBOX / self.HOST
        d.mkdir(parents=True, exist_ok=True)
        bare = d / ("m3" * 8 + ".json")
        bare.write_text(json.dumps({"mid": "m3" * 8, "origin": "hostB"}))
        with contextlib.redirect_stderr(io.StringIO()):
            pm.receiptbox_del(self.HOST, "m3" * 8, origin="hostA")
        self.assertTrue(bare.exists(), "origin B's park survives A's ack")
        self.assertNotIn("m3" * 8, pm._receipts_done())
        pm.receiptbox_del(self.HOST, "m3" * 8, origin="hostB")
        self.assertFalse(bare.exists(), "the OWNING origin's ack still settles it")

    def test_d_readbox_del_verifies_the_bare_parks_origin(self):
        d = pm.READBOX / self.HOST
        d.mkdir(parents=True, exist_ok=True)
        bare = d / ("m4" * 8 + ".json")
        bare.write_text(json.dumps({"mid": "m4" * 8, "origin": "hostB", "unread": False}))
        pm.readbox_del(self.HOST, {"mid": "m4" * 8, "origin": "hostA", "unread": False})
        self.assertTrue(bare.exists(), "origin B's read receipt survives A's ack")
        pm.readbox_del(self.HOST, {"mid": "m4" * 8, "origin": "hostB", "unread": False})
        self.assertFalse(bare.exists())

    # ── P1.6: durable directory chains ──────────────────────────────────────────────

    def test_e_mkdirs_durable_syncs_every_created_level_into_its_parent(self):
        seen = []
        real = pm._fsync_dir
        def spy(p):
            seen.append(str(p))
            return real(p)
        base = pm.STATE / "mkd-test"
        shutil.rmtree(base, ignore_errors=True)
        pm._fsync_dir = spy
        try:
            pm._mkdirs_durable(base / "a" / "b")
        finally:
            pm._fsync_dir = real
        for want in (pm.STATE, base, base / "a", base / "a" / "b"):
            self.assertIn(str(want), seen,
                          "%s is fsync'd — the chain includes the PARENT of the topmost "
                          "created level (r60 P1.6)" % want)
        seen.clear()
        pm._fsync_dir = spy
        try:
            pm._mkdirs_durable(base / "a" / "b")
        finally:
            pm._fsync_dir = real
            shutil.rmtree(base, ignore_errors=True)
        self.assertEqual(seen, [], "an existing chain costs nothing")

    def test_f_first_ever_delivery_links_mailroot_into_state(self):
        # the audit's crash model: two ACKs while the first mailbox disappeared — the
        # parent that OWNS a newly created MAILROOT was never synced
        shutil.rmtree(pm.MAILROOT, ignore_errors=True)
        seen = []
        real = os.fsync
        def spy(fd):
            seen.append(os.fstat(fd).st_ino)
            real(fd)
        pm.os.fsync = spy
        try:
            pm.deliver(SID, "peer", "77" * 16, "hello")
        finally:
            pm.os.fsync = real
        self.assertIn(pm.MAILROOT.stat().st_ino, seen)
        self.assertIn(pm.STATE.stat().st_ino, seen,
                      "the directory that OWNS the new MAILROOT is synced too")

    # ── P1.7: the withdrawal itself must be durable before the compensation ─────────

    def test_g_unsynced_withdrawal_is_uncertain_not_unpublished(self):
        # reproduced by the audit: publish-fsync failed, the unlink succeeded, and
        # `unpublished` was appended — a crash that lost the buffered unlink restored
        # live mail while the log said it never went out; replay then ACKed it
        mb = pm._mailbox(SID)
        real = pm._fsync_dir
        def always_boom(p):
            if str(p) == str(mb / "new"):
                raise OSError(5, "EIO")
            return real(p)
        pm._fsync_dir = always_boom
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(pm._DeliverUndurable):
                    pm.deliver(SID, "peer", "88" * 16, "boom")
        finally:
            pm._fsync_dir = real
        self.assertNotIn("unpublished", {r.get("ev") for r in _rows()},
                         "an UNPROVEN withdrawal never records definite non-publication")

    def test_h_synced_withdrawal_still_compensates_truthfully(self):
        mb = pm._mailbox(SID)
        real = pm._fsync_dir
        calls = [0]
        def boom_once(p):
            if str(p) == str(mb / "new"):
                calls[0] += 1
                if calls[0] == 1:
                    raise OSError(5, "EIO")     # the publish fsync
            return real(p)                      # the withdrawal fsync SUCCEEDS
        pm._fsync_dir = boom_once
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(OSError):
                    pm.deliver(SID, "peer", "99" * 16, "boom")
        finally:
            pm._fsync_dir = real
        self.assertIn("unpublished", {r.get("ev") for r in _rows()},
                      "a DURABLE withdrawal earns the truthful compensation")

    # ── P1.8: backflow validates the WHOLE ledger; invalid rows are save-proof ──────

    def test_i_invalid_backflow_rows_are_preserved_never_erased(self):
        pm._backflow_path().write_text(json.dumps({
            "badhost": {"acks": "VICTIM"},
            "inthost": {"acks": 123},
            "goodhost": {"acks": ["aa" * 16], "bounces": []}}))
        with contextlib.redirect_stderr(io.StringIO()):
            pm._backflow_load()
        self.assertTrue(pm._backflow_loaded[0])
        with pm._peer_lock:
            self.assertEqual(pm._peer_pending.get("goodhost", {}).get("acks"),
                             ["aa" * 16], "the fully-valid row folds")
            self.assertNotIn("badhost", pm._peer_pending,
                             "acks:'VICTIM' never folds as six single-letter acks")
            self.assertTrue(pm._backflow_save_locked())
        d = json.loads(pm._backflow_path().read_text())
        self.assertEqual(d["badhost"], {"acks": "VICTIM"},
                         "the invalid row rides the save VERBATIM — repairable, not erased")
        self.assertEqual(d["inthost"], {"acks": 123},
                         "…including the integer shape that used to raise TypeError")
        self.assertIn("goodhost", d)

    def test_j_wrong_shaped_backflow_root_quarantines_and_restarts(self):
        # the r59 bare hold was a PERMANENT wedge with no repair path — judged garbage
        # now moves aside (forensics kept) and the ledger restarts empty, loudly
        pm._backflow_path().write_text("[]")
        with contextlib.redirect_stderr(io.StringIO()):
            pm._backflow_load()
        self.assertTrue(pm._backflow_loaded[0], "the judged reset folds")
        self.assertFalse(pm._backflow_path().exists())
        kept = list((pm.CORRUPT / "peer-backflow").glob("*.corrupt"))
        self.assertEqual(len(kept), 1, "the judged bytes are kept for repair")

    # ── P1.9: pending-bounce cleanup never touches an unfolded ledger ───────────────

    @unittest.skipIf(os.geteuid() == 0, "chmod 0 does not block reads for root")
    def test_k_unfolded_pending_bounces_are_never_flushed_or_unlinked(self):
        pm._pending_bounces_path().parent.mkdir(parents=True, exist_ok=True)
        pm._pending_bounces_path().write_text(json.dumps(
            {"OLDHOST|om1": {"host": "OLDHOST", "mid": "om1", "code": "x", "t": 1}}))
        os.chmod(pm._pending_bounces_path(), 0)
        settled = []
        real = pm._bounce_arrived
        pm._bounce_arrived = lambda h, b: settled.append((h, b)) or True
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertFalse(pm._pending_bounce_park(self.HOST, "nm1", "code"))
                pm._flush_pending_bounces()
            self.assertEqual(settled, [], "NOTHING settles from an unfolded ledger")
            pm._pending_bounce_clear(self.HOST, "nm1")
        finally:
            pm._bounce_arrived = real
            os.chmod(pm._pending_bounces_path(), 0o644)
        self.assertTrue(pm._pending_bounces_path().exists(),
                        "the durable ledger survives the unfolded clear (the audit's "
                        "repro ended file_exists=False over a row this process never read)")
        d = json.loads(pm._pending_bounces_path().read_text())
        self.assertIn("OLDHOST|om1", d, "the durable park is untouched")

    # ── P2.11: receipts-done is proved and locked ───────────────────────────────────

    def test_l_wrong_shaped_receipts_done_is_judged_not_overwritten(self):
        pm.RECEIPTS_DONE.write_text("[]")
        with contextlib.redirect_stderr(io.StringIO()):
            pm._receipts_done_add("rd" * 16)
        self.assertEqual(set(json.loads(pm.RECEIPTS_DONE.read_text())), {"rd" * 16})
        kept = list((pm.CORRUPT / "receipts-done").glob("*.corrupt"))
        self.assertEqual(len(kept), 1, "the judged [] bytes are kept for repair")

    def test_m_concurrent_receipts_done_adds_both_land(self):
        # reproduced by the audit: two barrier-synchronized additions produced one
        # surviving confirmation — the RMW was unlocked
        import threading
        bar = threading.Barrier(2)
        def add(mid):
            bar.wait()
            pm._receipts_done_add(mid)
        ts = [threading.Thread(target=add, args=("cc" * 16,)),
              threading.Thread(target=add, args=("dd" * 16,))]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        d = json.loads(pm.RECEIPTS_DONE.read_text())
        self.assertIn("cc" * 16, d)
        self.assertIn("dd" * 16, d)

    # ── P2.13: the deterministic relay staging name is plant-proof ──────────────────

    def test_n_a_fifo_at_the_relay_staging_name_never_hangs_delivery(self):
        mb = pm._mailbox(SID)
        name = pm._relay_name("hostA", "mm" * 16)
        os.mkfifo(mb / "tmp" / name)
        got = pm.deliver(SID, "peer", "55" * 16, "body",
                         from_host="hostA", relay_mid="mm" * 16, relay_via="hostA")
        self.assertEqual(got, name, "delivery completes — no blocking open, no hang")
        self.assertTrue((mb / "new" / name).exists())


class R60Wave2Postal(unittest.TestCase):
    """the r60 verify round's confirmed second-order defects, postal half: _mkdirs_durable
    latched a failed fsync as created-forever, the deterministic staging name raced a
    concurrent same-(origin,mid) delivery into publishing unfsynced bytes, invalid-UTF-8
    receipts-done bytes escaped the judged arm uncaught, a failed move-aside still
    authorized the overwrite of judged bytes, and the fingerprint-less quarantine could
    move a concurrent writer's fresh valid ledger aside."""

    def setUp(self):
        _reset()
        for p in (pm.RECEIPTS_DONE,):
            try:
                p.unlink()
            except OSError:
                pass
        shutil.rmtree(pm.CORRUPT, ignore_errors=True)
        shutil.rmtree(pm.MAILROOT / SID, ignore_errors=True)
        shutil.rmtree(pm.STATE / "mkd2-test", ignore_errors=True)

    def tearDown(self):
        self.setUp()

    def test_a_a_failed_fsync_never_latches_the_chain_as_proven(self):
        # r61 P1.7 (superseding the r60 rollback design, which met a concurrent occupant
        # and then saw "exists" as proved): EXISTS is not PROVED — a failed fsync leaves
        # the levels unproven and the retry re-fsyncs them whether or not they stand.
        base = pm.STATE / "mkd2-test"
        real = pm._fsync_dir
        boom = [True]
        seen = []
        def spy(p):
            if boom[0]:
                raise OSError(5, "EIO")
            seen.append(str(p))
            return real(p)
        pm._fsync_dir = spy
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(OSError):
                    pm._mkdirs_durable(base / "a")
            # the dirs may STAND (a concurrent occupant blocks any undo) — what matters
            # is that nothing was marked proved
            self.assertNotIn(str(base), pm._proved_dirs)
            self.assertNotIn(str(base / "a"), pm._proved_dirs)
            boom[0] = False
            pm._mkdirs_durable(base / "a")
        finally:
            pm._fsync_dir = real
            shutil.rmtree(base, ignore_errors=True)
            pm._proved_dirs.discard(str(base))
            pm._proved_dirs.discard(str(base / "a"))
        for want in (base, base / "a"):
            self.assertIn(str(want), seen,
                          "the retry proved %s even though it already existed" % want)

    def test_b_staging_never_shares_or_touches_the_deterministic_name(self):
        # the verify round: a quarantine approve retrying against a still-running first
        # attempt shared one deterministic staging name — the loser's unlink orphaned the
        # winner's fsynced inode and the winner PUBLISHED the loser's unfsynced bytes
        mb = pm._mailbox(SID)
        name = pm._relay_name("hostA", "aa" * 16)
        sentinel = mb / "tmp" / name
        sentinel.write_text("SENTINEL")              # another in-flight call's stage
        got = pm.deliver(SID, "peer", "44" * 16, "body",
                         from_host="hostA", relay_mid="aa" * 16, relay_via="hostA")
        self.assertEqual(got, name)
        self.assertTrue((mb / "new" / name).exists())
        self.assertEqual(sentinel.read_text(), "SENTINEL",
                         "the other call's stage was never unlinked or written through")

    def test_c_invalid_utf8_receipts_done_is_judged_not_a_crash(self):
        pm.RECEIPTS_DONE.write_bytes(b"\xff\xfe{}")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(pm._receipts_done(), {},
                             "undecodable bytes are JUDGED (wave 1 raised "
                             "UnicodeDecodeError past the OSError arm and every "
                             "/handoff-done errored forever)")
            pm._receipts_done_add("ee" * 16)
        self.assertIn("ee" * 16, json.loads(pm.RECEIPTS_DONE.read_text()))
        self.assertEqual(len(list((pm.CORRUPT / "receipts-done").glob("*.corrupt"))), 1)

    def test_d_a_failed_move_aside_never_authorizes_the_overwrite(self):
        pm.RECEIPTS_DONE.write_text("[]")
        pm.CORRUPT.mkdir(parents=True, exist_ok=True)
        (pm.CORRUPT / "receipts-done").write_text("blocking file")   # the move-aside fails
        with contextlib.redirect_stderr(io.StringIO()):
            pm._receipts_done_add("ff" * 16)
        self.assertEqual(pm.RECEIPTS_DONE.read_text(), "[]",
                         "the judged bytes STAND — wave 1 overwrote them in place when "
                         "the quarantine silently failed")

    def test_e_the_judged_verdict_is_fingerprinted(self):
        # the verify round: a stale fingerprint-less verdict from the unlocked
        # handoff_done_apply reader moved a concurrent writer's FRESH VALID ledger aside
        pm.RECEIPTS_DONE.write_text("[]")
        captured = []
        real = pm._quarantine_corrupt_json
        def spy(path, store, error, fingerprint=None):
            captured.append(fingerprint)
            return real(path, store, error, fingerprint)
        pm._quarantine_corrupt_json = spy
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                pm._receipts_done()
        finally:
            pm._quarantine_corrupt_json = real
        self.assertEqual(len(captured), 1)
        self.assertIsNotNone(captured[0],
                             "the verdict carries the stat generation it judged — a "
                             "concurrent writer's replacement is never moved aside")


class R61PostalAudit(unittest.TestCase):
    """the v1.3.33 audit, postal half: directory durability still had four holes (P1.7),
    pending-bounce rows were unbound from their durable key (P1.8), preserved backflow
    rows were erased by newer live state (P1.9), ack readers coerced mid/origin/unread
    (P1.10), and quarantine staging kept the fixed-name unlink-then-open window (P3.1)."""

    HOST = "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"

    def setUp(self):
        _reset()
        pm._backflow_loaded[0] = False
        pm._backflow_preserved.clear()
        pm._pending_bounces.clear()
        pm._pending_bounces_loaded[0] = False
        pm._pending_bounces_quarantined.clear()
        with pm._peer_lock:
            pm._peer_pending.clear()
        for p in (pm._backflow_path(), pm._pending_bounces_path(), pm.PEER_SEEN):
            try:
                p.unlink()
            except OSError:
                pass
        shutil.rmtree(pm.QUARANTINE, ignore_errors=True)
        shutil.rmtree(pm.CORRUPT, ignore_errors=True)

    def tearDown(self):
        self.setUp()

    def test_a_the_first_peer_seen_ledger_links_durably(self):
        # r61 P1.7: the first creation fsynced the FILE but never its directory entry —
        # the ack correlated with a ledger a crash could lose whole
        seen_dirs = []
        real = pm._fsync_dir
        def spy(p):
            seen_dirs.append(str(p))
            return real(p)
        pm._fsync_dir = spy
        try:
            self.assertTrue(pm._peer_seen_append_durable("ab" * 16))
        finally:
            pm._fsync_dir = real
        self.assertIn(str(pm.PEER_SEEN.parent), seen_dirs,
                      "the first ledger's own directory entry is synced")

    def test_b_a_pending_bounce_row_is_bound_to_its_durable_key(self):
        # r61 P1.8, executed: a row stored under HOSTA|MIDA but naming HOSTB|VICTIM
        # settled the VICTIM — deleted its outbox record, noted its sender, and filed a
        # bounced terminal the real message never earned
        pm._pending_bounces_path().parent.mkdir(parents=True, exist_ok=True)
        pm._pending_bounces_path().write_text(json.dumps(
            {"HOSTA|" + "aa" * 16: {"host": "HOSTB", "mid": "ee" * 16, "code": "x",
                                    "t": 1}}))
        settled = []
        real = pm._bounce_arrived
        pm._bounce_arrived = lambda h, b: settled.append((h, b)) or True
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                pm._flush_pending_bounces()
        finally:
            pm._bounce_arrived = real
        self.assertEqual(settled, [], "the unbound row NEVER settles")
        pm._pending_bounce_clear("nosuch", "nn" * 16)            # any rewrite path
        d = json.loads(pm._pending_bounces_path().read_text())
        self.assertIn("HOSTA|" + "aa" * 16, d,
                      "…and it rides every rewrite VERBATIM for repair")
        self.assertEqual(d["HOSTA|" + "aa" * 16]["mid"], "ee" * 16)

    def test_c_a_preserved_backflow_row_survives_newer_live_state(self):
        # r61 P1.9, executed: the r60 pop erased a preserved row — and the valid
        # unresolved bounce inside it — the moment a new ack landed for the same host
        bounce = {"mid": "bb" * 16, "code": "recipient-unavailable"}
        pm._backflow_path().write_text(json.dumps(
            {self.HOST: {"acks": "VICTIM", "bounces": [bounce]}}))
        with contextlib.redirect_stderr(io.StringIO()):
            pm._backflow_load()
        p = pm._pending(self.HOST)
        with pm._peer_lock:
            p["acks"].append("cc" * 16)              # the newer live state
            self.assertTrue(pm._backflow_save_locked())
        d = json.loads(pm._backflow_path().read_text())
        self.assertEqual(d[self.HOST]["acks"], ["cc" * 16], "the live ack persisted")
        self.assertEqual(d[self.HOST]["preserved"],
                         {"acks": "VICTIM", "bounces": [bounce]},
                         "the preserved row rides EMBEDDED — the unresolved bounce "
                         "inside it is never erased")
        # …and a fresh process folds both halves back
        pm._backflow_loaded[0] = False
        pm._backflow_preserved.clear()
        with pm._peer_lock:
            pm._peer_pending.clear()
        with contextlib.redirect_stderr(io.StringIO()):
            pm._backflow_load()
        with pm._peer_lock:
            self.assertEqual(pm._peer_pending[self.HOST]["acks"], ["cc" * 16])
            self.assertEqual(pm._backflow_preserved[self.HOST],
                             {"acks": "VICTIM", "bounces": [bounce]})

    def test_d_ack_readers_reject_malformed_fields_whole_row(self):
        # r61 P1.10, executed three ways: mid:123 stringified into a false completion;
        # origin:123 dropped to an originless ack that deleted origin B's legacy park;
        # unread:"false" truth-coerced to True and deleted the retraction park
        good = {"mid": "dd" * 16, "origin": self.HOST, "unread": False}
        self.assertEqual(len(pm._peer_read_ack_rows([good])), 1)
        self.assertEqual(pm._peer_read_ack_rows(
            [dict(good, origin=123)]), [], "a malformed origin rejects the ROW")
        self.assertEqual(pm._peer_read_ack_rows(
            [dict(good, unread="false")]), [], "unread is a LITERAL bool")
        self.assertEqual(pm._receipt_rows([{"mid": "dd" * 16, "origin": 123}]), [],
                         "the handoffDoneAck reader rejects malformed origins too")
        before = len(_rows())
        self.assertTrue(pm._handoff_done_arrived("peer1", {"mid": 123}))
        self.assertEqual(len(_rows()), before,
                         "a numeric mid records NOTHING — never a stringified "
                         "completion for mail nobody delegated")

    def test_e_quarantine_staging_is_unique_and_exclusive(self):
        # r61 P3.1: the fixed-name unlink-then-open window admitted a same-user FIFO
        # replant — the executed thread stayed blocked past 400ms
        pm.QUARANTINE.mkdir(parents=True, exist_ok=True)
        _qname = pm._receipt_park_name("qq" * 16, "hostA")
        sentinel = pm.QUARANTINE / (_qname + ".tmp")
        sentinel.write_text("SENTINEL")              # the old fixed staging name
        ok = pm._quarantine_put("hostA", {"mid": "qq" * 16, "to": "web", "frm": "api",
                                          "frm_id": "", "body": "b"}, SID)
        self.assertTrue(ok, "the hold lands")
        self.assertEqual(sentinel.read_text(), "SENTINEL",
                         "the fixed name is never touched — nothing to plant against")
        self.assertTrue((pm.QUARANTINE / _qname).exists())
