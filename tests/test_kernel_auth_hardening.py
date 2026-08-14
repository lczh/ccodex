#!/usr/bin/env python3
"""Regression guards for the romp-kernel serve-layer auth gate (_authorize):

  L2 — the serve token is compared in constant time (hmac.compare_digest), not
       with ==, so a network (tailnet) client gets no timing oracle on the token.
  Token-everywhere — the serve token is REQUIRED on every gated route, loopback
       included (Jupyter's model: loopback is reachable by every local user on
       the machine, so the 0600 token file — not the socket — is the same-user
       trust boundary). The old loopback bypass (and with it the whole notion of
       "locality") is gone: a token-less loopback request is denied, and the Host
       header carries no authorization weight in any direction. Accepted forms:
       ?token= (browser bootstrap, seeds the cookie), the romp_token cookie, and
       the X-Romp-Token header (CLI/hooks/daemons).

Synthetic only — no real session data; the gate decision touches no session state.
Mirrors tests/test_kernel_ws_auth.py's module load order.
"""
import os
import io
import unittest
from email.message import Message
from importlib.machinery import SourceFileLoader
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()

TOK = km.TOKEN


def _inst(peer="127.0.0.1", headers=None):
    """A Handler with just enough state to call _authorize (no socket). `peer` is
    the TCP client IP (self.client_address[0]); `headers` are the request headers
    (Host / Origin / Cookie / X-Romp-Token)."""
    h = km.Handler.__new__(km.Handler)
    h.client_address = None if peer is None else (peer, 0)
    h.headers = headers if hasattr(headers, "get_all") else dict(headers or {})
    return h


def _auth(peer="127.0.0.1", headers=None, token=None):
    q = {"token": [token]} if token is not None else {}
    return _inst(peer, headers)._authorize(q)


class TokenCompare(unittest.TestCase):
    def test_ct_eq_matches_and_differs(self):
        self.assertTrue(km._ct_eq("abc", "abc"))
        self.assertFalse(km._ct_eq("abc", "abd"))
        self.assertFalse(km._ct_eq("abc", "abcd"))   # length differs

    def test_ct_eq_never_raises_on_odd_input(self):
        self.assertFalse(km._ct_eq(None, "x"))
        self.assertFalse(km._ct_eq("x", None))


class TokenRequiredEverywhere(unittest.TestCase):
    def test_tokenless_loopback_denied(self):
        # THE hardening this file pins: a loopback peer with no credential is DENIED —
        # loopback is shared by every local user, so it can't be a trust boundary. This
        # is what keeps a same-host co-tenant out of every authed route reached through
        # _authorize, incl. POST /send (prompt injection into live agents).
        for peer in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            ok, _, why = _auth(peer=peer)
            self.assertFalse(ok, "token-less loopback (%s) must be denied" % peer)
            self.assertIn("token", why)

    def test_forged_local_host_headers_do_not_authorize(self):
        # The old M3 bypass, now moot by construction: Host carries no auth weight at all.
        for host in ("localhost", "127.0.0.1", "localhost:29855", "::1"):
            ok, _, _ = _auth(peer="203.0.113.9", headers={"Host": host})
            self.assertFalse(ok, "Host: %s must not authorize" % host)
        ok, _, _ = _auth(peer="127.0.0.1", headers={"Host": "localhost"})
        self.assertFalse(ok, "a local Host on a loopback peer still needs the token")

    def test_query_token_authorizes_and_seeds_cookie(self):
        ok, cookie, _ = _auth(token=TOK)
        self.assertTrue(ok)
        self.assertEqual(cookie, TOK)     # ?token= sets the cookie so the browser never re-prompts

    def test_cookie_authorizes(self):
        ok, cookie, _ = _auth(headers={"Cookie": "romp_token=" + TOK,
                                        "Host": "127.0.0.1:29855",
                                        "Origin": "http://127.0.0.1:29855"})
        self.assertTrue(ok)
        self.assertIsNone(cookie)         # already has it — no re-set

    def test_cookie_requires_browser_provenance_and_rejects_cross_port_localhost(self):
        ok, _, why = _auth(headers={"Cookie": "romp_token=" + TOK})
        self.assertFalse(ok)
        self.assertIn("browser provenance", why)
        ok, _, why = _auth(headers={"Cookie": "romp_token=" + TOK,
                                     "Host": "127.0.0.1:29855",
                                     "Origin": "http://127.0.0.1:39999"})
        self.assertFalse(ok, "a page on another localhost port receives host cookies but is not same-origin")
        self.assertIn("browser provenance", why)

    def test_cookie_navigation_and_reload_use_fetch_metadata(self):
        for fetch_site in ("none", "same-origin"):
            ok, _, why = _auth(headers={"Cookie": "romp_token=" + TOK,
                                         "Sec-Fetch-Site": fetch_site})
            self.assertTrue(ok, "%s is valid browser provenance: %s" % (fetch_site, why))
        for fetch_site in ("same-site", "cross-site", ""):
            ok, _, _ = _auth(headers={"Cookie": "romp_token=" + TOK,
                                       "Sec-Fetch-Site": fetch_site})
            self.assertFalse(ok, "%s must not authorize a host-wide cookie" % (fetch_site or "missing"))

    def test_header_authorizes(self):
        # X-Romp-Token: the CLI/hook/daemon form (read from the 0600 file). Safe to accept
        # regardless of Origin: a cross-site page's custom header forces a CORS preflight,
        # which runs this same gate and fails without the token.
        ok, cookie, _ = _auth(headers={"X-Romp-Token": TOK})
        self.assertTrue(ok)
        self.assertIsNone(cookie)

    def test_wrong_credentials_denied(self):
        self.assertFalse(_auth(token="wrong")[0])
        self.assertFalse(_auth(headers={"Cookie": "romp_token=wrong"})[0])
        self.assertFalse(_auth(headers={"X-Romp-Token": "wrong"})[0])

    def test_valid_token_bypasses_origin_gate_wrong_token_does_not(self):
        # Federation: a foreign-Origin browser (served by ANOTHER kernel) carrying this
        # kernel's token through the tunnel must authorize; without it the Origin gate holds.
        ok, _, _ = _auth(headers={"Origin": "http://evil.example"}, token=TOK)
        self.assertTrue(ok)
        ok, _, why = _auth(headers={"Origin": "http://evil.example"}, token="wrong")
        self.assertFalse(ok)
        self.assertEqual(why, "cross-site origin")

    def test_remote_peer_denied_without_token(self):
        ok, _, why = _auth(peer="100.92.170.123")
        self.assertFalse(ok)
        self.assertIn("token", why)


class PostBodyGate(unittest.TestCase):
    class NoRead(io.BytesIO):
        def __init__(self):
            super().__init__(b"")
            self.called = False

        def read(self, *args, **kwargs):
            self.called = True
            raise AssertionError("body must not be read")

    def _post(self, headers):
        h = _inst(headers=headers)
        h.path = "/send"
        h.rfile = self.NoRead()
        h.close_connection = False
        h._send = lambda code, body, ctype, **kwargs: code
        return h, h.do_POST()

    def test_unauthenticated_post_is_rejected_before_body_read(self):
        h, code = self._post({"Content-Length": str(km._POST_MAX_BYTES + 1)})
        self.assertEqual(code, 403)
        self.assertFalse(h.rfile.called)
        self.assertTrue(h.close_connection)

    def test_authenticated_oversize_post_is_rejected_before_body_read(self):
        h, code = self._post({"X-Romp-Token": TOK,
                              "Content-Length": str(km._POST_MAX_BYTES + 1)})
        self.assertEqual(code, 413)
        self.assertFalse(h.rfile.called)
        self.assertTrue(h.close_connection)

    def test_authenticated_transfer_encoding_is_rejected_without_read(self):
        h, code = self._post({"X-Romp-Token": TOK, "Transfer-Encoding": "chunked"})
        self.assertEqual(code, 413)
        self.assertFalse(h.rfile.called)
        self.assertTrue(h.close_connection)

    def test_duplicate_content_length_is_rejected_without_read(self):
        headers = Message()
        headers.add_header("X-Romp-Token", TOK)
        headers.add_header("Content-Length", "0")
        headers.add_header("Content-Length", "1")
        h, code = self._post(headers)
        self.assertEqual(code, 400)
        self.assertFalse(h.rfile.called)
        self.assertTrue(h.close_connection)

    def test_non_decimal_content_length_is_rejected_without_read(self):
        h, code = self._post({"X-Romp-Token": TOK, "Content-Length": "+1"})
        self.assertEqual(code, 400)
        self.assertFalse(h.rfile.called)
        self.assertTrue(h.close_connection)


if __name__ == "__main__":
    unittest.main(verbosity=2)
