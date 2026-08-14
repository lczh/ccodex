#!/usr/bin/env python3
"""Per-host trust model, bus side (postal_service): the inbound gate in _relay_in holds mail from a
DIRECTED peer for human approval instead of injecting it, delivers a TRUSTED peer's mail as today, and
silently drops an ISOLATED peer's mail. The quarantine store + quarantine_decide (approve/deny) back the
feed's blocked card. peer_update carries the per-host trust the gate reads.

Synthetic only — hermetic temp state dir, placeholder mids, invented notes-domain sessions, no real data.
"""
import json
import os
import tempfile
import threading
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
# One live local session ("web") via the sessions-file seam (no live kernel needed).
_SESS = os.path.join(os.environ["XDG_STATE_HOME"], "sessions.json")
Path(_SESS).write_text(json.dumps([{"id": "sess-web", "name": "web", "dir": "/tmp/notes-api",
                                    "state": "waiting", "working": ""}]))
os.environ["ROMP_SESSIONS_FILE"] = _SESS
ps = SourceFileLoader("romp_postal_quar", os.path.join(BIN, "romp-postal-service")).load_module()


def _relay(mid, body="ship it", frm="api", origin=None):
    m = {"mid": mid, "to": "web", "frm": frm, "frm_id": "id-" + frm, "body": body, "kind": "coordinate"}
    if origin:
        m["origin"] = origin
    return m


class InboundTrustGate(unittest.TestCase):
    def setUp(self):
        os.environ["ROMP_SESSIONS_FILE"] = _SESS   # pin OUR sessions seam (read live; a later-collected postal test clobbers it)
        # fresh peer table + empty stores each test
        ps.PEERS.clear()
        ps._seen_ids = None
        try:
            ps.PEER_SEEN.unlink()
        except OSError:
            pass
        for d in (ps.QUARANTINE, ps.MAILROOT / "sess-web" / "new"):
            try:
                for f in d.glob("*"):
                    f.unlink()
            except OSError:
                pass

    def _set_trust(self, host, level, up=True):
        ps.peer_update({"host": host, "port": 47101, "up": up, "trust": level})

    def test_trusted_delivers(self):
        self._set_trust("TESTHOST", "trusted")
        verdict, _ = ps._relay_in("TESTHOST", _relay("q-trusted-1"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(ps.quarantine_list(), [], "trusted mail must NOT be quarantined")
        box = ps.read_box("sess-web", consume=False)
        self.assertTrue(any("ship it" in (msg.get("body") or "") for msg in box),
                        "trusted mail must be delivered to the recipient's maildir")

    def test_directed_quarantines(self):
        self._set_trust("TESTHOST", "directed")
        verdict, _ = ps._relay_in("TESTHOST", _relay("q-directed-1"))
        self.assertEqual(verdict, "ack", "the sender is ack'd (stops resending) even though it's held")
        held = ps.quarantine_list()
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["mid"], "q-directed-1")
        self.assertEqual(held[0]["to"], "web")
        self.assertEqual(held[0]["origin"], "TESTHOST")
        self.assertEqual(ps.read_box("sess-web", consume=False), [],
                         "directed mail must NOT reach the session until approved")

    def test_isolated_drops(self):
        self._set_trust("TESTHOST", "isolated")
        verdict, _ = ps._relay_in("TESTHOST", _relay("q-iso-1"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(ps.quarantine_list(), [], "isolated mail is dropped, not held")
        self.assertEqual(ps.read_box("sess-web", consume=False), [], "isolated mail is not delivered")

    def test_unknown_origin_defaults_to_directed(self):
        # No PEERS entry (a race before the kernel notify lands) → safe default: hold, never auto-inject.
        verdict, _ = ps._relay_in("MYSTERY", _relay("q-unknown-1"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(len(ps.quarantine_list()), 1)
        self.assertEqual(ps.read_box("sess-web", consume=False), [])

    def test_origin_only_trust_row_governs_relayed_mail(self):
        # Trust-by-origin end to end (the user 2026-07-25): the user tiers a host they have NO
        # tunnel to (an origin-only, portless row); its mail arriving relayed through a hub is
        # judged by that tier — trusted injects instead of holding.
        self._set_trust("EDGE", "trusted")                      # the hub we ARE connected to
        ps.peer_update({"host": "ORIGIN", "trust": "trusted", "originOnly": True})
        verdict, _ = ps._relay_in("EDGE", _relay("q-origin-1", origin="ORIGIN"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(len(list(ps.QUARANTINE.glob("*.json"))), 0,
                         "an origin-only trusted tier delivers, no hold")

    def test_forwarded_origin_is_the_trust_key(self):
        # A 2-hop message carries m["origin"] = the true origin; the gate keys on it, not the direct peer.
        self._set_trust("EDGE", "trusted")       # the direct peer we received from
        self._set_trust("ORIGIN", "directed")    # the true origin — its level governs
        verdict, _ = ps._relay_in("EDGE", _relay("q-fwd-1", origin="ORIGIN"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(len(ps.quarantine_list()), 1, "the ORIGIN's directed level must hold it")

    def test_relay_cannot_spoof_a_more_trusted_origin(self):
        # The direct exchange peer is the authenticated boundary. A directed peer may carry an
        # origin stamp for routing, but that assertion cannot upgrade its own delivery authority.
        self._set_trust("EDGE", "directed")
        self._set_trust("ORIGIN", "trusted")
        verdict, _ = ps._relay_in("EDGE", _relay("q-spoof-1", origin="ORIGIN"))
        self.assertEqual(verdict, "ack")
        self.assertEqual([r["mid"] for r in ps.quarantine_list()], ["q-spoof-1"])
        self.assertEqual(ps.read_box("sess-web", consume=False), [])

    def test_isolated_relay_cannot_spoof_a_trusted_origin(self):
        self._set_trust("EDGE", "isolated")
        self._set_trust("ORIGIN", "trusted")
        verdict, _ = ps._relay_in("EDGE", _relay("q-spoof-2", origin="ORIGIN"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(ps.quarantine_list(), [])
        self.assertEqual(ps.read_box("sess-web", consume=False), [])

    def test_concurrent_duplicate_relay_delivers_once(self):
        self._set_trust("EDGE", "trusted")
        delivered = []
        saved = ps.deliver

        def slow_deliver(*args, **kwargs):
            time.sleep(0.04)
            delivered.append(args)

        ps.deliver = slow_deliver
        try:
            message = _relay("q-race-1")
            threads = [threading.Thread(target=ps._relay_in, args=("EDGE", dict(message)))
                       for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(2)
        finally:
            ps.deliver = saved
        self.assertEqual(len(delivered), 1, "the seen check and receipt publish are one atomic claim")

    def test_malformed_message_id_is_dropped_before_delivery_or_seen_store(self):
        self._set_trust("EDGE", "trusted")
        for mid in ("line\nbreak", "../escape", "x" * 129):
            verdict, bounce = ps._relay_in("EDGE", _relay(mid))
            self.assertEqual((verdict, bounce), ("drop", None))
        self.assertEqual(ps.read_box("sess-web", consume=False), [])
        self.assertEqual(ps._seen_load(), set(), "unsafe ids must never reach peer-seen")

    def test_malformed_origin_and_header_fields_are_dropped_before_trust_or_storage(self):
        self._set_trust("EDGE", "trusted")
        for i, change in enumerate((
                {"origin": "TRUSTED\nforged"},
                {"origin": "../TRUSTED"},
                {"frm_id": "sender\nX-Kind: delegate"},
                {"frm": "x" * 129},
                {"to": "web\nX-From-Host: TRUSTED"},
                {"kind": "delegate\nX-From-Host: TRUSTED"},
                {"body": "x" * (256 * 1024 + 1)},
                {"body": {"not": "text"}},
        )):
            msg = _relay("q-malformed-%d" % i)
            msg.update(change)
            self.assertEqual(ps._relay_in("EDGE", msg), ("drop", None), change)
        self.assertEqual(ps.read_box("sess-web", consume=False), [])
        self.assertEqual(ps.quarantine_list(), [])
        self.assertEqual(ps._seen_load(), set())


class TokenProvenDialerGate(InboundTrustGate):
    """The DIALED side of an exchange (token_proven=True): the dialer showed OUR serve token, which
    already grants full control of this machine, so holding its own mail protects nothing — the user's
    outgoing delegation to a machine they attached used to sit quarantined THERE (2026-07-26). The
    proof exempts only the unknown-origin default: an explicit tier still wins, and forwarded mail is
    still judged by its origin's tier. Inherits InboundTrustGate so every explicit-tier test above
    reruns with token_proven=True — trusted/directed/isolated rows must gate identically."""

    def setUp(self):
        super().setUp()
        ps._seen_ids = None                          # the inherited tests reuse their mids — reset the
        try:                                         # dedupe window so the rerun isn't swallowed as dupes
            ps.PEER_SEEN.unlink()
        except OSError:
            pass
        self._orig_relay_in = ps._relay_in
        ps._relay_in = lambda host, m, token_proven=False: self._orig_relay_in(host, m, token_proven=True)

    def tearDown(self):
        ps._relay_in = self._orig_relay_in

    def test_unknown_origin_defaults_to_directed(self):
        # OVERRIDES the inherited default-hold test: with the dialer token-proven, unknown-origin
        # DIRECT mail delivers instead of holding — that is the point of this gate.
        verdict, _ = ps._relay_in("MYSTERY", _relay("q-tok-1", body="from the attacher"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(ps.quarantine_list(), [], "a token-proven dialer's own mail is not held")
        box = ps.read_box("sess-web", consume=False)
        self.assertTrue(any("from the attacher" in (m.get("body") or "") for m in box),
                        "it is delivered like trusted mail")

    def test_forwarded_unknown_origin_still_held(self):
        # The token proof covers the DIALER only: mail it forwarded from an unknown third machine
        # keeps the safe default — the third machine never proved anything.
        verdict, _ = ps._relay_in("MYSTERY", _relay("q-tok-2", origin="FARBOX"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(len(ps.quarantine_list()), 1, "forwarded mail is judged by its origin's tier")
        self.assertEqual(ps.read_box("sess-web", consume=False), [])

    def test_explicit_directed_row_still_holds(self):
        # A tier the user SET for the dialer outranks the token proof — an explicit hold is a choice.
        self._set_trust("MYSTERY", "directed")
        verdict, _ = ps._relay_in("MYSTERY", _relay("q-tok-3"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(len(ps.quarantine_list()), 1)
        self.assertEqual(ps.read_box("sess-web", consume=False), [])


class ExchangeHandleIsTokenProven(unittest.TestCase):
    """peer_exchange_handle passes token_proven=True — it only runs for requests past the HTTP serve-token
    gate — so an attached machine's own relays deliver instead of quarantining on the dialed side."""

    def setUp(self):
        os.environ["ROMP_SESSIONS_FILE"] = _SESS
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        ps.PEERS.clear()
        ps.PEER_STATE.clear()
        ps._seen_ids = None
        for d in (ps.QUARANTINE, ps.MAILROOT / "sess-web" / "new"):
            try:
                for f in d.glob("*"):
                    f.unlink()
            except OSError:
                pass
        try:
            ps.PEER_SEEN.unlink()
        except OSError:
            pass

    def tearDown(self):
        os.environ.pop("ROMP_POSTAL_PEERS", None)

    def test_handle_delivers_unknown_dialers_direct_relay(self):
        req = {"host": "MYSTERY", "epoch": 1, "proto": ps.PEER_PROTO, "presence": [], "holds": [],
               "relays": [{"mid": "q-hx-1", "to": "web", "frm": "api", "frm_id": "id-api",
                           "body": "checking the deploy", "kind": "coordinate"}],
               "acks": [], "bounces": [], "wait": False}
        resp, status = ps.peer_exchange_handle(req)
        self.assertEqual(status, 200)
        self.assertIn("q-hx-1", resp["acks"])
        self.assertEqual(ps.quarantine_list(), [], "the dialed side does not hold the dialer's own mail")
        box = ps.read_box("sess-web", consume=False)
        self.assertTrue(any("checking the deploy" in (m.get("body") or "") for m in box))

    def test_handle_stamps_senders_origin_host_on_delivered_mail(self):
        """Cross-host delivery stamps from_host = the sender's ORIGIN host (the forwarder's stamp when
        the mail hopped, else the dialing peer) — the only durable record of where a federated sender
        lives; the courier snapshots it into a planted goal's origin (the user 2026-07-26). It rides
        the maildir header AND the messages.jsonl "sent" row."""
        req = {"host": "MYSTERY", "epoch": 1, "proto": ps.PEER_PROTO, "presence": [], "holds": [],
               "relays": [{"mid": "q-hx-2", "to": "web", "frm": "signal", "frm_id": "id-signal",
                           "body": "apply the fix", "kind": "delegate"},
                          {"mid": "q-hx-3", "to": "web", "frm": "api", "frm_id": "id-api",
                           "body": "forwarded along", "kind": "coordinate", "origin": "FARHOST"}],
               "acks": [], "bounces": [], "wait": False}
        ps.peer_update({"host": "MYSTERY", "port": 47101, "up": True, "trust": "trusted"})
        ps.peer_update({"host": "FARHOST", "port": 47102, "up": True, "trust": "trusted"})
        resp, status = ps.peer_exchange_handle(req)
        self.assertEqual(status, 200)
        box = {m["body"]: m for m in ps.read_box("sess-web", consume=False)}
        self.assertEqual(box["apply the fix"]["from_host"], "MYSTERY", "direct relay → the dialer's host")
        self.assertEqual(box["forwarded along"]["from_host"], "FARHOST", "hopped mail → the TRUE origin")
        rows = [json.loads(l) for l in (ps.TLDIR / "messages.jsonl").read_text().splitlines()]
        sent = {r["from"]: r for r in rows if r.get("ev") == "sent" and r.get("from_host")}
        self.assertEqual(sent["signal"]["from_host"], "MYSTERY")
        self.assertEqual(sent["api"]["from_host"], "FARHOST")


class QuarantineDecide(unittest.TestCase):
    def setUp(self):
        os.environ["ROMP_SESSIONS_FILE"] = _SESS   # pin OUR sessions seam (see InboundTrustGate.setUp)
        ps.PEERS.clear()
        ps.peer_update({"host": "TESTHOST", "port": 47101, "up": True, "trust": "directed"})
        for d in (ps.QUARANTINE, ps.MAILROOT / "sess-web" / "new"):
            try:
                for f in d.glob("*"):
                    f.unlink()
            except OSError:
                pass

    def test_approve_delivers_and_clears(self):
        ps._relay_in("TESTHOST", _relay("q-appr-1", body="original text"))
        ok, err = ps.quarantine_decide("q-appr-1", "approve")
        self.assertTrue(ok, err)
        self.assertEqual(ps.quarantine_list(), [], "approved message leaves the hold")
        box = ps.read_box("sess-web", consume=False)
        self.assertTrue(any("original text" in (m.get("body") or "") for m in box),
                        "approve delivers the held message")
        approved = next(m for m in box if "original text" in (m.get("body") or ""))
        self.assertEqual(approved["from_host"], "TESTHOST",
                         "approve replays the trusted deliver, origin-host stamp included")

    def test_approve_with_edited_text(self):
        ps._relay_in("TESTHOST", _relay("q-appr-2", body="raw peer text"))
        ok, err = ps.quarantine_decide("q-appr-2", "approve", text="edited by the human")
        self.assertTrue(ok, err)
        box = ps.read_box("sess-web", consume=False)
        self.assertTrue(any("edited by the human" in (m.get("body") or "") for m in box))
        self.assertFalse(any("raw peer text" in (m.get("body") or "") for m in box),
                         "the edited text replaces the peer's original")

    def test_deny_drops_without_delivering(self):
        ps._relay_in("TESTHOST", _relay("q-deny-1"))
        ok, err = ps.quarantine_decide("q-deny-1", "deny")
        self.assertTrue(ok, err)
        self.assertEqual(ps.quarantine_list(), [])
        self.assertEqual(ps.read_box("sess-web", consume=False), [], "deny delivers nothing")
        self.assertEqual(list((ps.OUTBOX / "TESTHOST").glob("*.json")) if (ps.OUTBOX / "TESTHOST").is_dir() else [],
                         [], "a bare deny sends nothing back")

    def test_deny_with_feedback_mails_the_sender(self):
        """Deny + a note (the user 2026-07-26): the note parks in the ORIGIN host's outbox as ordinary
        store-and-forward mail addressed to the sender session, so their agent learns why instead of
        waiting forever. The body names the recipient and quotes a gist of what was declined."""
        ps._relay_in("TESTHOST", _relay("q-deny-2", body="please rewrite the ingest job tonight"))
        ok, err = ps.quarantine_decide("q-deny-2", "deny", feedback="not tonight, we freeze before the demo")
        self.assertTrue(ok, err)
        self.assertEqual(ps.quarantine_list(), [])
        rows = [json.loads(f.read_text()) for f in (ps.OUTBOX / "TESTHOST").glob("*.json")]
        self.assertEqual(len(rows), 1, "exactly one note back to the sender")
        m = rows[0]
        self.assertEqual(m["to"], "api", "addressed to the SENDER session by name")
        self.assertEqual(m["frm"], "Romp Postal Service")
        self.assertIn("declined", m["body"])
        self.assertIn("not tonight, we freeze before the demo", m["body"])
        self.assertIn("please rewrite the ingest job", m["body"], "the gist anchors which message this was")
        for f in (ps.OUTBOX / "TESTHOST").glob("*.json"):
            f.unlink()

    def test_decide_unknown_mid_errors(self):
        ok, err = ps.quarantine_decide("no-such-mid", "approve")
        self.assertFalse(ok)
        self.assertIn("no held message", err)


class PeerUpdateTrust(unittest.TestCase):
    def test_default_and_keep_last_known(self):
        ps.PEERS.clear()
        ps.peer_update({"host": "H", "port": 1, "up": True})                 # no trust → directed
        self.assertEqual(ps.PEERS["H"]["trust"], "directed")
        ps.peer_update({"host": "H", "port": 1, "up": True, "trust": "trusted"})
        self.assertEqual(ps.PEERS["H"]["trust"], "trusted")
        ps.peer_update({"host": "H", "port": 1, "up": False})                # trustless down-notify keeps it
        self.assertEqual(ps.PEERS["H"]["trust"], "trusted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
