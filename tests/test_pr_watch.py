#!/usr/bin/env python3
"""First-class PR-landing watches (the user 2026-08-24, both teams' surveys): a session registers
interest in a PR, the KERNEL polls gh for the terminal state — MERGED, CLOSED, or a FAILED check,
both ends of the standing watcher rule — and delivers ONE [romp] mail, surviving the kernel
restarts that killed every shell loop this replaces. Registrations persist and re-arm on boot (the
reconnect-intent idiom); a gh failure retires the watch LOUDLY after three consecutive errors.
Synthetic only — gh fully stubbed."""
import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest import mock
from http.server import ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel_prw", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"


class Verdict(unittest.TestCase):
    """The pure gh-payload reading, executed."""

    def test_merged_and_closed_are_terminal(self):
        self.assertEqual(km._pr_watch_verdict({"state": "MERGED"}), ("merged", ""))
        self.assertEqual(km._pr_watch_verdict({"state": "CLOSED"}), ("closed", ""))

    def test_a_red_rollup_row_alone_is_never_terminal(self):
        # the v1.3.17 audit's P2.9: the rollup does not mark required-ness — an optional lint
        # failure was mailed "will not land" and the watch retired while the PR went on to merge.
        d = {"state": "OPEN", "statusCheckRollup": [
            {"name": "Python 3.12", "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"name": "optional lint", "status": "COMPLETED", "conclusion": "FAILURE"}]}
        self.assertEqual(km._pr_watch_verdict(d), (None, ""),
                         "without required-check rows the watch keeps watching")

    def test_a_failing_required_check_is_terminal_with_its_name(self):
        d = {"state": "OPEN", "statusCheckRollup": [
            {"name": "optional lint", "status": "COMPLETED", "conclusion": "FAILURE"}]}
        req = [{"name": "Shell (bats)", "bucket": "fail"}]
        self.assertEqual(km._pr_watch_verdict(d, required=req), ("failed", "Shell (bats)"))
        req_ok = [{"name": "build", "bucket": "pass"}]
        self.assertEqual(km._pr_watch_verdict(d, required=req_ok), (None, ""),
                         "all required green + optional red -> still in flight")
        req_pend = [{"name": "build", "bucket": "pending"}]
        self.assertEqual(km._pr_watch_verdict(d, required=req_pend), (None, "busy"))

    def test_in_flight_says_busy_for_the_cadence_hint(self):
        d = {"state": "OPEN", "statusCheckRollup": [
            {"name": "x", "status": "IN_PROGRESS", "conclusion": ""}]}
        self.assertEqual(km._pr_watch_verdict(d), (None, "busy"))
        self.assertEqual(km._pr_watch_verdict({"state": "OPEN", "statusCheckRollup": []}), (None, ""))


class Notice(unittest.TestCase):
    """The [romp] mechanics-notice voice, executed (the injected-voice rule: the notice is ABOUT
    romp — like the restart notice it names romp — and carries none of the board vocabulary)."""

    def test_each_terminal_reads_plainly_and_wears_the_machine_tag(self):
        for verdict, must in (("merged", "has MERGED"), ("closed", "CLOSED without merging"),
                              ("failed", "FAILED check"), ("error", "could not read")):
            n = km._pr_watch_notice(verdict, "TESTORG/testrepo", 7, "Shell (bats)")
            self.assertTrue(n.startswith("[romp] "), verdict)
            self.assertIn("TESTORG/testrepo#7", n)
            self.assertIn(must, n)
            self.assertIn("<!-- romp-tag: pr-watch -->", n, "machine-sent dress, never the user's words")
            for word in ("card", "board", "goal", "column", "nudge"):
                self.assertNotIn(word, n.lower(), "no board vocabulary in an injected body")


class Persistence(unittest.TestCase):
    """Registration is intent: survives a restart, re-arms fresh (the reconnect-intent idiom)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._saved = (km.PR_WATCH_FILE, list(km._pr_watches))
        km.PR_WATCH_FILE = Path(self.td.name) / "pr-watches.json"
        km._pr_watches[:] = []

    def tearDown(self):
        km.PR_WATCH_FILE = self._saved[0]
        km._pr_watches[:] = self._saved[1]
        self.td.cleanup()

    def test_a_watch_survives_the_restart_and_rearms_fresh(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=1000)
        km._pr_watches[0]["_fails"] = 2                      # runtime state must NOT persist
        km._pr_watches[0]["_next"] = 99999
        km._pr_watches[:] = []
        km._pr_watches_load()                                # the boot path
        self.assertEqual(len(km._pr_watches), 1)
        r = km._pr_watches[0]
        self.assertEqual((r["pr"], r["repo"], r["sid"], r["at"]), (7, "TESTORG/testrepo", SID, 1000))
        self.assertEqual((r["_next"], r["_fails"]), (0, 0), "re-armed fresh: polls immediately")

    def test_registration_is_idempotent(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID)
        km.add_pr_watch(7, "TESTORG/testrepo", SID)
        self.assertEqual(len(km._pr_watches), 1)


class SupervisorReachesTheTick(unittest.TestCase):
    """The sweep must run on a box with NO remotes (the 2026-08-25 audit's #664 specimen): `now` was
    bound only inside the supervisor's per-remote loop, so with remotes.json = [] every pass died on
    UnboundLocalError inside the catch-all before reaching _pr_watch_tick — the landing mail never
    sent, and a merged PR's awaiting stamp sat stale for hours with no retire path short of the
    dead-man. The binding is per-pass now; this pins the ORDER (bound before the loop)."""

    def test_now_is_bound_per_pass_before_the_tick(self):
        import inspect
        src = inspect.getsource(km._tunnel_supervisor)
        self.assertIn("now = time.time()               # bound per PASS", src,
                      "the unconditional per-pass binding exists (loop-local bindings do not count)")
        self.assertLess(src.index("now = time.time()               # bound per PASS"),
                        src.index("_pr_watch_tick(now)"),
                        "…and it precedes the tick, so zero remotes can never unbind it")


class Tick(unittest.TestCase):
    """The sweep: terminal delivers + retires; gh failure retires LOUDLY after three; in-flight
    backs off while checks run."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._saved = (km.PR_WATCH_FILE, list(km._pr_watches), km._pr_watch_read, km._pr_watch_deliver)
        km.PR_WATCH_FILE = Path(self.td.name) / "pr-watches.json"
        km._pr_watches[:] = []
        self.mail = []
        km._pr_watch_deliver = lambda sid, text: self.mail.append((sid, text)) or True

    def tearDown(self):
        km.PR_WATCH_FILE, km._pr_watches[:], km._pr_watch_read, km._pr_watch_deliver = \
            self._saved[0], self._saved[1], self._saved[2], self._saved[3]
        self.td.cleanup()

    def test_merged_delivers_once_and_retires(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)
        km._pr_watch_read = lambda pr, repo: ("merged", "")
        km._pr_watch_tick(100.0)
        self.assertEqual(len(self.mail), 1)
        self.assertIn("has MERGED", self.mail[0][1])
        self.assertEqual(self.mail[0][0], SID)
        self.assertEqual(km._pr_watches, [], "terminal → the watch retires")
        km._pr_watch_tick(200.0)
        self.assertEqual(len(self.mail), 1, "…and never mails twice")

    def test_a_failed_check_is_just_as_terminal(self):
        km.add_pr_watch(8, "TESTORG/testrepo", SID, now=0)
        km._pr_watch_read = lambda pr, repo: ("failed", "Shell (bats)")
        km._pr_watch_tick(100.0)
        self.assertEqual(len(self.mail), 1)
        self.assertIn("FAILED check (Shell (bats))", self.mail[0][1])
        self.assertEqual(km._pr_watches, [])

    def test_gh_failure_retires_loudly_after_three_never_silently(self):
        km.add_pr_watch(9, "TESTORG/testrepo", SID, now=0)
        km._pr_watch_read = lambda pr, repo: ("error", "auth required")
        km._pr_watch_tick(100.0)
        km._pr_watch_tick(100.0 + km.PR_WATCH_EVERY)
        self.assertEqual(self.mail, [], "two failures → still trying, still quiet")
        km._pr_watch_tick(100.0 + 2 * km.PR_WATCH_EVERY)
        self.assertEqual(len(self.mail), 1, "the third delivers the loud retire")
        self.assertIn("could not read", self.mail[0][1])
        self.assertIn("auth required", self.mail[0][1])
        self.assertEqual(km._pr_watches, [])

    def test_a_failed_delivery_keeps_the_watch_for_retry(self):
        # the v1.3.17 audit's P2.10 (half 1): the tick retired the watch even when delivery
        # returned False — the landing mail was simply lost
        km.add_pr_watch(11, "TESTORG/testrepo", SID, now=0)
        km._pr_watch_read = lambda pr, repo: ("merged", "")
        km._pr_watch_deliver = lambda sid, text: False
        km._pr_watch_tick(100.0)
        self.assertEqual(len(km._pr_watches), 1, "a known delivery failure keeps the watch")
        self.assertNotIn("sent", km._pr_watches[0], "the stamp is cleared on a KNOWN failure")
        km._pr_watch_deliver = lambda sid, text: self.mail.append((sid, text)) or True
        km._pr_watch_tick(100.0 + km.PR_WATCH_EVERY)
        self.assertEqual(len(self.mail), 1, "the retry delivers")
        self.assertEqual(km._pr_watches, [])

    def test_a_stamped_row_retires_without_a_second_mail(self):
        # the v1.3.17 audit's P2.10 (half 2): a crash between deliver and retire re-mailed the
        # landing notice on restart. The verdict is stamped durably BEFORE the injection, and a
        # stamped row retires silently.
        km.add_pr_watch(12, "TESTORG/testrepo", SID, now=0)
        km._pr_watches[0]["sent"] = "merged"          # the crash left the stamp
        km._pr_watch_read = lambda pr, repo: ("merged", "")
        km._pr_watch_tick(100.0)
        self.assertEqual(self.mail, [], "no duplicate mail after the crash window")
        self.assertEqual(km._pr_watches, [], "the stamped row still retires")

    def test_a_nondurable_registration_is_refused(self):
        # the v1.3.19 audit: add_pr_watch acked a watch a restart would forget (the save now
        # runs INSIDE the registration's lock — _pr_watches_save_locked — so the mock moved)
        with mock.patch.object(km, "_pr_watches_save_locked", return_value=False):
            row = km.add_pr_watch(15, "TESTORG/testrepo", SID, now=0)
        self.assertIsNone(row, "no durable save, no acknowledgement")
        self.assertEqual([r for r in km._pr_watches if r["pr"] == 15], [],
                         "…and the in-memory row is rolled back too")

    def test_registration_is_one_locked_transaction_disk_never_leads_memory(self):
        # the v1.3.20 audit, executed interleave: with the save OUTSIDE the lock, a competing
        # registration could persist the first caller's PENDING row while its save was failing —
        # memory ended [22], disk [21, 22], and the refused 21 resurrected at the next boot.
        # Under the one-lock transaction the competitor BLOCKS until 21 rolls back.
        import threading as th
        import time as _t
        real_locked = km._pr_watches_save_locked
        started = th.Event()

        def locked_failing_for_21():
            if any(r["pr"] == 21 for r in km._pr_watches):
                started.set()                          # wake the competitor mid-transaction
                _t.sleep(0.08)                         # give it time to try to interleave
                return False                           # 21's save fails -> rollback
            return real_locked()

        t2 = th.Thread(target=lambda: (started.wait(2),
                                       km.add_pr_watch(22, "TESTORG/testrepo", SID, now=0)))
        t2.start()
        try:
            with mock.patch.object(km, "_pr_watches_save_locked",
                                   side_effect=locked_failing_for_21):
                self.assertIsNone(km.add_pr_watch(21, "TESTORG/testrepo", SID, now=0))
        finally:
            t2.join(timeout=5)
        self.assertEqual([r["pr"] for r in km._pr_watches], [22], "memory holds only the survivor")
        rows = json.loads(km.PR_WATCH_FILE.read_text())
        self.assertEqual([r["pr"] for r in rows], [22],
                         "disk agrees — the refused row was never persisted by the competitor "
                         "(the v1.3.20 audit's resurrection)")

    def test_a_known_failure_whose_stamp_clear_also_fails_still_retries_after_restart(self):
        # the v1.3.20 audit: delivery FAILED and clearing the durable `sent` stamp failed too —
        # after a restart the stale stamp read as possible-success and the notification was
        # silently retired. The durable failure marker outlives the crash and forces the retry.
        km.add_pr_watch(23, "TESTORG/testrepo", SID, now=0)
        km._pr_watch_read = lambda pr, repo: ("merged", "")
        km._pr_watch_deliver = lambda sid, text: False
        calls = [0]
        real_save = km._pr_watches_save

        def stamp_saves_clear_fails():
            calls[0] += 1
            return real_save() if calls[0] == 1 else False   # the stamp lands; the CLEAR fails

        with mock.patch.object(km, "_pr_watches_save", side_effect=stamp_saves_clear_fails):
            km._pr_watch_tick(100.0)
        self.assertEqual(self.mail, [], "the delivery failed")
        km._pr_watches[:] = []                        # the crash
        km._pr_watches_load()
        self.assertEqual(km._pr_watches[0].get("sent"), "merged",
                         "the stale stamp survived the restart — exactly the audited state")
        km._pr_watch_deliver = lambda sid, text: self.mail.append((sid, text)) or True
        km._pr_watch_tick(200.0)
        self.assertEqual(len(self.mail), 1,
                         "the failure marker outranks the stale stamp: the notification is "
                         "retried, never silently retired (the v1.3.20 audit)")
        self.assertEqual(km._pr_watches, [])
        self.assertFalse(km._pr_watch_fail_marker(
            {"repo": "TESTORG/testrepo", "pr": 23, "sid": SID}, "merged").exists(),
            "the confirmed delivery spends the marker")

    def test_a_failed_attempted_mark_retains_the_watch(self):
        # the v1.3.22 audit's P2.6, executed: the pending->attempted transition failing used to
        # return True — the watch RETIRED with zero deliveries ever attempted (the marker write
        # precedes the injection, so there is no duplicate to fear; retrying the MARK is free)
        km.add_pr_watch(31, "TESTORG/testrepo", SID, now=0)
        km._pr_watch_read = lambda pr, repo: ("merged", "")
        real_fk = km._pr_watch_fail_marker(km._pr_watches[0], "merged")
        real_fk.parent.mkdir(parents=True, exist_ok=True)
        real_fk.write_text("pending\n")             # a prior known failure left the marker
        state = {"fail_writes": True}

        class FK:
            def exists(self):
                return real_fk.exists()

            def read_text(self):
                return real_fk.read_text()

            def write_text(self, s):
                if state["fail_writes"]:
                    raise OSError("disk full")
                return real_fk.write_text(s)

            def unlink(self, missing_ok=False):
                return real_fk.unlink(missing_ok=missing_ok)

            @property
            def parent(self):
                return real_fk.parent

        try:
            with mock.patch.object(km, "_pr_watch_fail_marker", return_value=FK()):
                km._pr_watch_tick(100.0)
                self.assertEqual(self.mail, [], "no injection was attempted")
                self.assertEqual(len(km._pr_watches), 1,
                                 "the watch SURVIVES — nothing was delivered, nothing may retire")
                state["fail_writes"] = False          # the disk heals
                km._pr_watch_tick(100.0 + km.PR_WATCH_EVERY)
        finally:
            try:
                real_fk.unlink()
            except OSError:
                pass
        self.assertEqual(len(self.mail), 1, "the retry delivers exactly once")
        self.assertEqual(km._pr_watches, [], "…and the watch retires on the confirmed delivery")

    def test_an_unsaved_stamp_never_delivers(self):
        # the v1.3.18 audit: a swallowed save failure left the stamp in memory only — a crash
        # after delivery re-mailed the notice on restart. No durable stamp, no injection.
        km.add_pr_watch(14, "TESTORG/testrepo", SID, now=0)
        km._pr_watch_read = lambda pr, repo: ("merged", "")
        with mock.patch.object(km, "_pr_watches_save", return_value=False):
            km._pr_watch_tick(100.0)
        self.assertEqual(self.mail, [], "no durable stamp -> no injection this pass")
        self.assertEqual(len(km._pr_watches), 1)
        self.assertNotIn("sent", km._pr_watches[0])
        km._pr_watch_tick(100.0 + km.PR_WATCH_EVERY)   # save works again -> delivers once
        self.assertEqual(len(self.mail), 1)
        self.assertEqual(km._pr_watches, [])

    def test_the_stamp_survives_the_watch_file_roundtrip(self):
        km.add_pr_watch(13, "TESTORG/testrepo", SID, now=0)
        km._pr_watches[0]["sent"] = "merged"
        km._pr_watches_save()
        km._pr_watches[:] = []
        km._pr_watches_load()
        self.assertEqual(km._pr_watches[0].get("sent"), "merged",
                         "the delivery stamp is durable across a kernel restart")

    def test_in_flight_backs_off_while_checks_run(self):
        km.add_pr_watch(10, "TESTORG/testrepo", SID, now=0)
        km._pr_watch_read = lambda pr, repo: (None, "busy")
        km._pr_watch_tick(100.0)
        self.assertEqual(km._pr_watches[0]["_next"], 100.0 + km.PR_WATCH_BUSY_EVERY)
        km._pr_watch_read = lambda pr, repo: (None, "")
        km._pr_watch_tick(100.0 + km.PR_WATCH_BUSY_EVERY)
        self.assertEqual(km._pr_watches[0]["_next"],
                         100.0 + km.PR_WATCH_BUSY_EVERY + km.PR_WATCH_EVERY)
        self.assertEqual(self.mail, [])


class Route(unittest.TestCase):
    """POST /watch-pr over the real Handler: registration + refusals; token-gated."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._saved = (km.PR_WATCH_FILE, list(km._pr_watches), km._sid_of)
        km.PR_WATCH_FILE = Path(self.td.name) / "pr-watches.json"
        km._pr_watches[:] = []
        km._sid_of = lambda who: SID if who in (SID, "web") else ""

    def tearDown(self):
        km.PR_WATCH_FILE, km._pr_watches[:], km._sid_of = self._saved
        self.td.cleanup()

    def _post(self, body, token=True):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/watch-pr" % self.port, data=json.dumps(body).encode(),
            headers=dict({"Content-Type": "application/json"},
                         **({"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]} if token else {})))
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, (e.read() or b"").decode()

    def test_registers_by_name_and_persists(self):
        st, r = self._post({"pr": 7, "repo": "TESTORG/testrepo", "name": "web"})
        self.assertEqual(st, 200)
        self.assertTrue(r["ok"])
        self.assertEqual(r["watch"]["sid"], SID)
        self.assertEqual(json.loads(km.PR_WATCH_FILE.read_text())[0]["pr"], 7)

    def test_refusals_are_loud_and_shaped(self):
        self.assertEqual(self._post({"repo": "TESTORG/testrepo", "name": "web"})[0], 400)
        self.assertEqual(self._post({"pr": 7, "repo": "not-a-repo", "name": "web"})[0], 400)
        st, r = self._post({"pr": 7, "repo": "TESTORG/testrepo", "name": "ghost"})
        self.assertFalse(r["ok"]); self.assertIn('no session answers to "ghost"', r["error"])
        self.assertEqual(self._post({"pr": 7, "repo": "TESTORG/testrepo", "name": "web"},
                                    token=False)[0], 403)


if __name__ == "__main__":
    unittest.main()


class V1323StampResolutionAndIsolation(unittest.TestCase):
    """the v1.3.23 audit's P2.8: (a) a crash between delivery and retirement left the stamped
    row in the durable file, and the next boot's poll could return a CHANGED verdict — the
    landed "error" notice was followed by a contradictory "merged" one; (b) a non-UTF-8
    failure marker raised UnicodeDecodeError past the OSError catch and the exception aborted
    the ENTIRE supervisor pass. Tick's fixture, standalone (inheriting would re-run its tests)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._saved = (km.PR_WATCH_FILE, list(km._pr_watches), km._pr_watch_read,
                       km._pr_watch_deliver)
        km.PR_WATCH_FILE = Path(self.td.name) / "pr-watches.json"
        km._pr_watches[:] = []
        self.mail = []
        km._pr_watch_deliver = lambda sid, text: self.mail.append((sid, text)) or True

    def tearDown(self):
        km.PR_WATCH_FILE, km._pr_watches[:], km._pr_watch_read, km._pr_watch_deliver = \
            self._saved[0], self._saved[1], self._saved[2], self._saved[3]
        self.td.cleanup()

    def test_a_stamped_row_resolves_before_any_fresh_poll(self):
        km.add_pr_watch(41, "TESTORG/testrepo", SID, now=0)
        km._pr_watches[0]["sent"] = "error"               # the restart-loaded stamp:
        km._pr_watches[0]["sentDetail"] = "auth required"  # delivered, not yet retired
        polled = []
        km._pr_watch_read = lambda pr, repo: polled.append(1) or ("merged", "")
        km._pr_watch_tick(100.0)
        self.assertEqual(self.mail, [], "the stamp is delivery evidence — nothing re-mails")
        self.assertEqual(polled, [], "…and the changed verdict is never even polled")
        self.assertEqual(km._pr_watches, [], "the stamped row retires")

    def test_a_stamped_known_failure_retries_the_stamped_verdict(self):
        km.add_pr_watch(42, "TESTORG/testrepo", SID, now=0)
        r = km._pr_watches[0]
        r["sent"] = "error"
        r["sentDetail"] = "auth required"
        fk = km._pr_watch_fail_marker(r, "error")
        fk.parent.mkdir(parents=True, exist_ok=True)
        fk.write_text("pending\n")                        # the stamp is NOT delivery evidence
        self.addCleanup(lambda: fk.unlink(missing_ok=True))
        km._pr_watch_read = lambda pr, repo: ("merged", "")
        km._pr_watch_tick(100.0)
        self.assertEqual(len(self.mail), 1)
        self.assertIn("could not read", self.mail[0][1],
                      "the STAMPED error notice retries — never the fresh, contradictory verdict")
        self.assertIn("auth required", self.mail[0][1], "…with its stamped detail")
        self.assertEqual(km._pr_watches, [])

    def test_a_non_utf8_marker_never_aborts_the_pass(self):
        km.add_pr_watch(43, "TESTORG/testrepo", SID, now=0)
        km.add_pr_watch(44, "TESTORG/testrepo", SID, now=0)
        r = km._pr_watches[0]
        r["sent"] = "merged"
        r["sentDetail"] = ""
        fk = km._pr_watch_fail_marker(r, "merged")
        fk.parent.mkdir(parents=True, exist_ok=True)
        fk.write_bytes(b"\xff\xfe garbage \xff")
        self.addCleanup(lambda: fk.unlink(missing_ok=True))
        km._pr_watch_read = lambda pr, repo: ("merged", "")
        km._pr_watch_tick(100.0)
        self.assertEqual(km._pr_watches, [], "both watches processed — nothing aborted")
        self.assertEqual(len(self.mail), 1,
                         "the corrupt-marked stamp retires at-most-once; the OTHER watch "
                         "still delivered")

    def test_one_watch_raise_never_aborts_the_others(self):
        km.add_pr_watch(45, "TESTORG/testrepo", SID, now=0)
        km.add_pr_watch(46, "TESTORG/testrepo", SID, now=0)

        def read(pr, repo):
            if pr == 45:
                raise RuntimeError("synthetic")
            return ("merged", "")

        km._pr_watch_read = read
        with contextlib.redirect_stderr(io.StringIO()):
            km._pr_watch_tick(100.0)
        self.assertEqual(len(self.mail), 1, "the healthy watch still delivered")
        self.assertEqual(len(km._pr_watches), 1, "the raising watch survives for retry")
