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
class CookieDoesNotBypassOrigin(unittest.TestCase):
    """The cookie is the one credential the browser attaches for you, so it is the one that must
    NOT bypass the Origin gate. Cookies are host- not port-scoped (RFC 6265 §8.5), so every
    http://127.0.0.1:<port> page is same-site with the dashboard and rides this cookie — SameSite
    included. Without the Origin check, any page served by anything else on loopback (a dev server
    in a repo an agent cloned) reached /ws, which streams every session and accepts sendMessage."""

    def test_cookie_denied_from_a_foreign_loopback_origin(self):
        # the drive-by case: a page on another loopback PORT is same-site, so the browser attaches
        # the cookie, but its Origin is not ours → the cookie must not authorize
        ok, _, why = _auth(headers={"Cookie": "romp_token=" + TOK,
                                    "Origin": "http://127.0.0.1:59999",
                                    "Host": "127.0.0.1:%d" % km.PORT})
        self.assertFalse(ok, "a cookie from another loopback port must not authorize")
        self.assertEqual(why, "cross-site origin")

    def test_cookie_denied_from_an_offsite_origin(self):
        ok, _, why = _auth(headers={"Cookie": "romp_token=" + TOK, "Origin": "http://evil.example"})
        self.assertFalse(ok)
        self.assertEqual(why, "cross-site origin")

    def test_cookie_still_authorizes_absent_origin(self):
        # a same-origin GET omits Origin; that path (and non-browser clients) is unchanged
        ok, _, _ = _auth(headers={"Cookie": "romp_token=" + TOK})
        self.assertTrue(ok, "a cookie with no Origin (same-origin nav / curl) still authorizes")

    def test_cookie_still_authorizes_the_dashboards_own_origin(self):
        ok, _, _ = _auth(headers={"Cookie": "romp_token=" + TOK,
                                  "Origin": "http://127.0.0.1:%d" % km.PORT,
                                  "Host": "127.0.0.1:%d" % km.PORT})
        self.assertTrue(ok)

    def test_cookie_still_authorizes_the_vscode_webview(self):
        ok, _, _ = _auth(headers={"Cookie": "romp_token=" + TOK,
                                  "Origin": "vscode-webview://0p9m1abc"})
        self.assertTrue(ok, "the VS Code webview origin is allowed by _origin_ok")

    def test_explicit_token_still_bypasses_origin_for_federation(self):
        # the escape hatch a cross-site page cannot use: only an EXPLICIT token bypasses origin,
        # and a drive-by page can't obtain one (it rides only the cookie)
        ok, _, _ = _auth(headers={"Origin": "http://evil.example"}, token=TOK)
        self.assertTrue(ok)


class ResponseHardeningHeaders(unittest.TestCase):
    """Every response declares its type as final (nosniff) and refuses cross-origin framing
    (clickjacking). Source-pinned: the header set lives in _send, exercised on every route."""

    def test_send_sets_nosniff_and_frame_guards(self):
        import inspect
        src = inspect.getsource(km.Handler._send)
        self.assertIn('"X-Content-Type-Options", "nosniff"', src)
        self.assertIn('"X-Frame-Options", "SAMEORIGIN"', src)
        self.assertIn("frame-ancestors 'self'", src)

    def test_remote_relay_derives_its_own_mime_and_discards_the_remotes(self):
        # the /remote/<host>/file relay must decide the Content-Type from the requested extension
        # (_PREVIEW_MIME) and never mirror the remote's — a remote answering text/html for a .pdf
        # the lightbox opens in a same-origin iframe would be script on the dashboard's origin
        import inspect
        src = inspect.getsource(km.Handler._remote_file)
        self.assertIn("_PREVIEW_MIME.get(os.path.splitext", src)
        self.assertIn("status, ctype = resp.status, mime", src)
        # the type must not be READ from the remote (a comment may still name it as "never this")
        self.assertNotIn("ctype = resp.getheader", src)
        self.assertNotIn('resp.status, resp.getheader("Content-Type")', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
