#!/usr/bin/env python3
"""The postal bus is token-gated (Jupyter's model, shared with the kernel): every route
except the /ping liveness probe requires the machine's serve token — loopback included,
since loopback is reachable by every local user and the bus can wake sessions and inject
mail into their prompts. Accepted forms: X-Romp-Token (same-machine clients, read from
the 0600 file) and ?token= (a peer bus dialing through the ssh forward with the DIALED
machine's token). Also pins the peer-token plumbing: /peer notifies carry the peer's
token, a token-less down notify keeps the last known one, and the dialer sends ?token=.

Synthetic only — hermetic temp state dir, placeholder names, no real session data.
"""
import json
import http.client
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state dir; the sessions-file seam signals "no live kernel" to the bus.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
_SESS = os.path.join(os.environ["XDG_STATE_HOME"], "sessions.json")
Path(_SESS).write_text("[]")
os.environ["ROMP_SESSIONS_FILE"] = _SESS
ps = SourceFileLoader("romp_postal_token", os.path.join(BIN, "romp-postal-service")).load_module()

TOK = ps.SERVE_TOKEN


def _code(port, path, headers=None, method="GET", data=None):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 headers=dict(headers or {}), method=method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


class BusTokenGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), ps.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_ping_is_exempt(self):
        self.assertEqual(_code(self.port, "/ping"), 200)

    def test_tokenless_requests_denied(self):
        self.assertEqual(_code(self.port, "/agents"), 403)
        self.assertEqual(_code(self.port, "/peers"), 403)
        self.assertEqual(_code(self.port, "/inbox?id=x"), 403)
        self.assertEqual(_code(self.port, "/send", method="POST", data=b"{}"), 403)
        self.assertEqual(_code(self.port, "/peer", method="POST", data=b"{}"), 403)

    def test_header_token_authorizes(self):
        self.assertEqual(_code(self.port, "/peers", headers={"X-Romp-Token": TOK}), 200)

    def test_query_token_authorizes_the_peer_dial_form(self):
        # A peer bus dials /peer-exchange through the ssh forward with ?token= — same
        # acceptance on any route (here /peers, which needs no exchange payload).
        self.assertEqual(_code(self.port, "/peers?token=" + TOK), 200)

    def test_wrong_token_denied(self):
        self.assertEqual(_code(self.port, "/peers", headers={"X-Romp-Token": "wrong"}), 403)
        self.assertEqual(_code(self.port, "/peers?token=wrong"), 403)

    def test_peer_exchange_rejects_an_oversize_declared_body_before_reading_it(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.putrequest("POST", "/peer-exchange?token=" + TOK)
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(ps.PEER_EXCHANGE_MAX_BYTES + 1))
            conn.endheaders()
            response = conn.getresponse()
            self.assertEqual(response.status, 413)
            self.assertLessEqual(len(response.read()), 256)
        finally:
            conn.close()

    def test_peer_exchange_rejects_non_object_and_over_cardinality_envelopes(self):
        headers = {"Content-Type": "application/json"}
        self.assertEqual(_code(self.port, "/peer-exchange?token=" + TOK, headers=headers,
                               method="POST", data=b"[]"), 400)
        body = json.dumps({"host": "TESTHOST", "proto": ps.PEER_PROTO,
                           "relays": [None] * (ps.PEER_LIST_LIMITS["relays"] + 1)}).encode()
        self.assertEqual(_code(self.port, "/peer-exchange?token=" + TOK, headers=headers,
                               method="POST", data=body), 413)


class PeerTokenPlumbing(unittest.TestCase):
    def test_peer_update_stores_token_and_down_notify_keeps_it(self):
        ps.peer_update({"host": "TESTHOST", "port": 45001, "up": True, "token": "peer-tok"})
        self.assertEqual(ps.PEERS["TESTHOST"]["token"], "peer-tok")
        ps.peer_update({"host": "TESTHOST", "port": 45001, "up": False})   # down carries no token
        self.assertEqual(ps.PEERS["TESTHOST"]["token"], "peer-tok",
                         "a token-less transition must keep the last known peer token")
        ps.PEERS.pop("TESTHOST", None)

    def test_peer_http_sends_the_peer_token_as_query(self):
        seen = {}

        class Capture(BaseHTTPRequestHandler):
            def do_POST(self):
                seen["path"] = self.path
                body = json.dumps({}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Capture)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            ps._peer_http(srv.server_address[1], {"host": "TESTHOST"}, token="peer-tok")
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(seen.get("path"), "/peer-exchange?token=peer-tok")

    def test_peer_http_rejects_oversize_response_before_reading(self):
        class DeclaredOversize:
            headers = {"Content-Length": str(ps.PEER_EXCHANGE_MAX_BYTES + 1)}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, *args):
                raise AssertionError("oversize response body must not be read")

        with mock.patch.object(ps.urllib.request, "urlopen", return_value=DeclaredOversize()):
            with self.assertRaises(ps.PeerExchangeError):
                ps._peer_http(45001, {"host": "TESTHOST"})

    def test_peer_http_rejects_non_object_and_over_cardinality_responses(self):
        class Response:
            def __init__(self, body):
                self.body = body
                self.headers = {"Content-Length": str(len(body))}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, *args):
                return self.body

        bodies = [b"[]", json.dumps({"relays": [None] *
                                      (ps.PEER_LIST_LIMITS["relays"] + 1)}).encode()]
        for body in bodies:
            with self.subTest(body=body[:20]):
                with mock.patch.object(ps.urllib.request, "urlopen", return_value=Response(body)):
                    with self.assertRaises(ps.PeerExchangeError):
                        ps._peer_http(45001, {"host": "TESTHOST"})

    def test_peer_http_rejects_bad_outbound_envelopes_before_connecting(self):
        payloads = [[], {"relays": [None] * (ps.PEER_LIST_LIMITS["relays"] + 1)}]
        for payload in payloads:
            with self.subTest(payload_type=type(payload).__name__):
                with mock.patch.object(ps.urllib.request, "urlopen") as open_peer:
                    with self.assertRaises(ps.PeerExchangeError):
                        ps._peer_http(45001, payload)
                    open_peer.assert_not_called()

    def test_peer_http_error_diagnostic_uses_a_bounded_read(self):
        class ErrorBody:
            requested = None

            def read(self, size):
                self.requested = size
                return b"synthetic peer error " * 200

        error = ErrorBody()
        text = ps._peer_http_error_text(error)
        self.assertEqual(error.requested, 2049)
        self.assertLessEqual(len(text), 200)




class TokenBirth(unittest.TestCase):
    def test_the_token_file_is_born_0600_and_a_loser_rereads_the_winner(self):
        # write-then-chmod left a world-readable window, and two concurrent starts could mint
        # different tokens (the user's audit, 2026-08-18): O_EXCL + mode closes both
        import stat
        f = ps.STATE.parent / "serve-token"
        f.unlink(missing_ok=True)
        old = dict(os.environ)
        os.environ.pop("ROMP_SERVE_TOKEN", None)
        try:
            chmods = []
            real_chmod = os.chmod
            with_unittest = __import__("unittest").mock
            with with_unittest.patch.object(ps.os, "chmod",
                                            side_effect=lambda *a, **k: chmods.append(a) or real_chmod(*a, **k)):
                v = ps._load_serve_token()
            self.assertTrue(v)
            mode = stat.S_IMODE(os.stat(f).st_mode)
            self.assertEqual(mode, 0o600, "born 0600")
            self.assertEqual(chmods, [], "born 0600 by OPEN MODE — a chmod call IS the audited window")
            self.assertEqual(ps._load_serve_token(), v, "a second minter re-reads the winner's token")
            # a loser must never read an incomplete token: the name appears only WITH its content
            # (link-claim), so a pre-existing complete file always wins
            f.unlink()
            f.write_text("winner-token-value")
            self.assertEqual(ps._load_serve_token(), "winner-token-value")
        finally:
            os.environ.clear(); os.environ.update(old)
            f.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
