#!/usr/bin/env python3
"""Postal isolation (the user 2026-06-23): a session with the timeline lane's mailbox toggled off
(postalServiceOff — legacy postalOff — in the kernel's session-flags.json) is invisible to list_agents, can't send, and can't receive —
for working privately. These pin the flag reader + the read_box RECEIVE gate at the unit level; the
end-to-end /send + /agents enforcement is in tests/romp-postal.bats.

Synthetic only — placeholder UUIDs, hermetic temp state dir, no real session data.
"""
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()      # hermetic; constants resolve under here at import
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = SourceFileLoader("romp_postal", os.path.join(BIN, "romp-postal-service")).load_module()

SID = "11111111-2222-3333-4444-555555555555"


def _set_flag(sid, postal_off):
    pm.SESSION_FLAGS.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(pm.SESSION_FLAGS.read_text()) if pm.SESSION_FLAGS.exists() else {}
    if postal_off:
        data[sid] = {"postalOff": True}     # legacy key on purpose: pins back-compat (reader honours old + new)
    else:
        data.pop(sid, None)
    pm.SESSION_FLAGS.write_text(json.dumps(data))


class PostalOff(unittest.TestCase):
    def setUp(self):
        # the r57 last-known-good copy is module-global: without this reset, the alphabetical
        # neighbors primed it and the fault tests measured cross-test leakage, not the reader
        # (the r57 wave-2 verification caught the OLD fail-open pin below passing exactly
        # that way — green in a full run, red standalone)
        pm._postal_off_cache[0] = None
        pm._postal_off_key[0] = None

    def tearDown(self):
        try:
            pm.SESSION_FLAGS.unlink()
        except OSError:
            pass

    def test_default_not_isolated(self):
        self.assertFalse(pm._postal_off(SID), "no flags file → on the Romp Postal Service")
        self.assertFalse(pm._postal_off(""), "empty sid → not isolated")

    def test_flag_toggles_isolation(self):
        _set_flag(SID, True)
        self.assertTrue(pm._postal_off(SID))
        _set_flag(SID, False)
        self.assertFalse(pm._postal_off(SID), "clearing the flag rejoins the Romp Postal Service")

    def test_other_flags_do_not_isolate(self):
        pm.SESSION_FLAGS.parent.mkdir(parents=True, exist_ok=True)
        pm.SESSION_FLAGS.write_text(json.dumps({SID: {"hideFromFeed": True}}))   # muted from feed, NOT postal
        self.assertFalse(pm._postal_off(SID), "hideFromFeed alone must not isolate from postal")

    def test_malformed_flags_file_fails_closed_then_serves_history(self):
        # r57 P1.4 REVERSED the old fail-open pin here: one unreadable window used to
        # deliver into a durably-isolated session. With no good read yet, isolation is
        # the safe answer; with history, the copy answers ONLY for the same stat
        # generation (r58 P1.4: a stale permissive copy crossed a NEWER isolation).
        pm.SESSION_FLAGS.parent.mkdir(parents=True, exist_ok=True)
        pm.SESSION_FLAGS.write_text("{not valid json")
        self.assertTrue(pm._postal_off(SID),
                        "corrupt flags with NO history: closed, never fail-open delivery")
        pm.SESSION_FLAGS.unlink()                    # the helper reads the file it writes
        _set_flag(SID, False)
        self.assertFalse(pm._postal_off(SID))        # a good read primes the copy
        if os.geteuid() != 0:
            os.chmod(pm.SESSION_FLAGS, 0)            # SAME generation, unreadable window
            try:
                self.assertFalse(pm._postal_off(SID),
                                 "…and a same-generation fault serves the copy")
            finally:
                os.chmod(pm.SESSION_FLAGS, 0o644)
        pm.SESSION_FLAGS.write_text("{not valid json")
        self.assertTrue(pm._postal_off(SID),
                        "r58 P1.4: a NEW generation the reader cannot read is never "
                        "answered by the stale permissive copy — fail closed")

    def test_wrong_shape_flags_are_a_fault_not_an_empty_store(self):
        # the r57 wave-2 verification, reproduced: []-shaped VALID bytes took the success
        # path and un-isolated every session past a primed last-known-good
        _set_flag(SID, True)
        self.assertTrue(pm._postal_off(SID))         # primes the copy
        for junk in ('[]', 'null', '0', '"oops"'):
            pm.SESSION_FLAGS.write_text(junk)
            self.assertTrue(pm._postal_off(SID),
                            "%s must answer isolated (a fault/closed verdict), never "
                            "un-isolate through the success path" % junk)
        pm._postal_off_cache[0] = None
        pm._postal_off_key[0] = None               # a fresh process with no history
        pm.SESSION_FLAGS.write_text("[]")
        self.assertTrue(pm._postal_off(SID), "wrong shape with no history: closed")

    def test_read_box_holds_mail_while_isolated(self):
        box = pm.MAILROOT / SID / "new"
        box.mkdir(parents=True, exist_ok=True)
        (box / "msg1").write_text("From: peer\nFrom-Id: x\nDate: now\n\nhello\n")
        _set_flag(SID, True)
        self.assertEqual(pm.read_box(SID, consume=True), [],
                         "isolated → a drain delivers nothing")
        self.assertTrue((box / "msg1").exists(),
                        "the message stays in new/ (not consumed) until the session reconnects")
        _set_flag(SID, False)
        got = pm.read_box(SID, consume=True)
        self.assertEqual([m["body"] for m in got], ["hello"],
                         "reconnecting delivers the held mail")


class WiringAcrossSurfaces(unittest.TestCase):
    """The postalServiceOff flag spans three files (kernel boot exposure → timeline render/toggle → postal
    enforcement). Pin the cross-surface wiring by name so a rename can't silently disconnect a surface."""

    def test_kernel_boot_exposes_postaloff(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('"postalServiceOff": _session_flag(sid, "postalServiceOff")', src,
                      "the kernel must publish postalServiceOff in the session boot so the timeline can render it")

    def test_timeline_view_draws_and_toggles_the_mailbox(self):
        # Since 2026-07-28 the mailbox lives in the lane GEAR's drop-down (LANE_TOGGLES) rather than as
        # its own lane icon — the row still draws the mailboxIcon and toggles postalServiceOff through
        # the same _setSessionFlag persistence.
        src = open(os.path.join(os.path.dirname(BIN), "ui", "romp-timeline-view.js")).read()
        self.assertIn("mailboxIcon", src, "the gear menu draws the (monochrome) mailbox icon")
        self.assertIn("flag: 'postalServiceOff', label: 'Postal service', icon: mailboxIcon", src,
                      "the gear menu row toggles the postalServiceOff flag")
        self.assertIn("this._setSessionFlag(s, t.flag, next);", src, "menu rows persist via _setSessionFlag")


if __name__ == "__main__":
    unittest.main(verbosity=2)
