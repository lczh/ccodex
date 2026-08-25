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


class BusBodyValidation(unittest.TestCase):
    """Non-object JSON bodies (arrays, null, numbers) crashed .get() and dropped the connection
    on /send, /recall, /wake, /heartbeat, and /quarantine/act (the v1.3.10 audit) — the shared
    body parser now maps them to the same JSON 400 every malformed envelope gets."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), ps.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_non_object_bodies_are_a_json_400(self):
        for path in ("/send", "/recall", "/wake", "/heartbeat"):
            for junk in (b"[]", b"null", b"7"):
                req = urllib.request.Request(
                    "http://127.0.0.1:%d%s" % (self.port, path), method="POST", data=junk,
                    headers={"X-Romp-Token": TOK, "Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=5) as r:
                        code, body = r.status, r.read().decode()
                except urllib.error.HTTPError as e:
                    code, body = e.code, e.read().decode()
                self.assertEqual(code, 400, "%s with %r must be a clean 400" % (path, junk))
                self.assertIn("error", body)


class PostalTokenFlock(unittest.TestCase):
    """The bus's loader shares the kernel's serve-token.lock — whichever daemon starts first mints,
    the other blocks and adopts. Same deterministic schedule as the kernel-side test: the test
    holds the lock as the kernel-minter mid-mint, the bus announces its lock attempt, the winner
    publishes and releases, and the bus must come back with the winner's token."""

    _CHILD = r"""
import os, sys
os.environ["ROMP_SERVE_TOKEN"] = "import-shield"   # module import loads SERVE_TOKEN; shield it
os.environ["ROMP_STATE_DIR"] = sys.argv[2]         # pin to the PARENT test's state root (sibling
#                                                    test modules overwrite XDG_STATE_HOME at import)
from importlib.machinery import SourceFileLoader
BIN = sys.argv[1]
ps = SourceFileLoader("romp_postal_child", os.path.join(BIN, "romp-postal-service")).load_module()
os.environ.pop("ROMP_SERVE_TOKEN", None)
import fcntl
_real = fcntl.flock
def _spy(fd, op):
    print("FLOCK", flush=True)
    return _real(fd, op)
fcntl.flock = _spy
print(ps._load_serve_token(), flush=True)
"""

    def test_the_bus_blocks_on_the_kernels_mint_and_adopts_its_token(self):
        import fcntl
        import select
        import subprocess
        import sys
        f = ps.STATE.parent / "serve-token"
        f.parent.mkdir(parents=True, exist_ok=True)
        saved = f.read_text() if f.exists() else None
        f.write_text("")                             # the audit's remnant: exists, empty
        env_tok = os.environ.pop("ROMP_SERVE_TOKEN", None)
        script = f.with_name("flock-child.py")
        script.write_text(self._CHILD)
        lfd = os.open(str(f) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(lfd, fcntl.LOCK_EX)
        p = subprocess.Popen([sys.executable, str(script), BIN, str(ps.STATE.parent)],
                             stdout=subprocess.PIPE, text=True)

        def _cleanup():
            try:
                os.close(lfd)
            except OSError:
                pass
            if p.poll() is None:
                p.kill()
            p.stdout.close()
            if env_tok is not None:
                os.environ["ROMP_SERVE_TOKEN"] = env_tok
            if saved is not None:
                f.write_text(saved)                  # later gate tests compare ps.SERVE_TOKEN
            for leftover in (f.with_name(f.name + ".lock"), script):
                try:
                    leftover.unlink()
                except OSError:
                    pass
        self.addCleanup(_cleanup)

        def _line(why):
            r, _, _ = select.select([p.stdout], [], [], 120)
            self.assertTrue(r, "no output within 120s — " + why)
            return p.stdout.readline().strip()

        self.assertEqual(_line("the loader never spoke"), "FLOCK",
                         "the bus must try the lock BEFORE reading — reading first is the "
                         "split-brain schedule (the user's audit, 2026-08-19)")
        tmp = f.with_name("winner.tmp")
        tmp.write_text("winner-token")
        os.replace(tmp, f)
        os.close(lfd)
        self.assertEqual(_line("the loader stayed blocked after the lock was freed"),
                         "winner-token",
                         "the bus must adopt the token the lock-holder published")
        self.assertEqual(p.wait(timeout=60), 0)
        self.assertEqual(f.read_text().strip(), "winner-token")

    def test_a_stale_mint_temp_never_wedges_the_loader(self):
        # same wedge as the kernel loader: a dead mint's temp made every later O_EXCL fail
        # silently, so the bus ran on a never-persisted token (the adversarial review, 2026-08-19)
        f = ps.STATE.parent / "serve-token"
        f.parent.mkdir(parents=True, exist_ok=True)
        saved = f.read_text() if f.exists() else None
        env_tok = os.environ.pop("ROMP_SERVE_TOKEN", None)
        tmp = f.with_name("%s.%d.tmp" % (f.name, os.getpid()))
        tmp.write_text("stale")

        def _restore():
            if env_tok is not None:
                os.environ["ROMP_SERVE_TOKEN"] = env_tok
            if saved is not None:
                f.write_text(saved)
            try:
                tmp.unlink()
            except OSError:
                pass
        self.addCleanup(_restore)
        f.unlink(missing_ok=True)                # force the mint path
        tok = ps._load_serve_token()
        self.assertEqual(f.read_text().strip(), tok,
                         "the minted token must be PERSISTED despite the stale temp")

    def test_a_failing_flock_REFUSES_instead_of_minting_locklessly(self):
        # same fail-closed contract as the kernel loader (the v1.3.8 audit)
        f = ps.STATE.parent / "serve-token"
        f.parent.mkdir(parents=True, exist_ok=True)
        saved = f.read_text() if f.exists() else None
        env_tok = os.environ.pop("ROMP_SERVE_TOKEN", None)

        def _restore():
            if env_tok is not None:
                os.environ["ROMP_SERVE_TOKEN"] = env_tok
            if saved is not None:
                f.write_text(saved)
        self.addCleanup(_restore)
        f.unlink(missing_ok=True)                # force the MINT path: the module import already
        #                                          minted a healthy 0600 token, and the read-only
        #                                          grace then served it without ever calling
        #                                          flock — this test failed standalone and was
        #                                          masked in the full suite (the v1.3.10 audit)
        real = ps.fcntl.flock

        def no_locks(fd, op):
            raise OSError(37, "No locks available")
        ps.fcntl.flock = no_locks
        try:
            with self.assertRaises(RuntimeError):
                ps._load_serve_token()
        finally:
            ps.fcntl.flock = real

    def test_an_unreadable_existing_token_is_never_rotated(self):
        if os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        f = ps.STATE.parent / "serve-token"
        f.parent.mkdir(parents=True, exist_ok=True)
        saved = f.read_text() if f.exists() else None
        env_tok = os.environ.pop("ROMP_SERVE_TOKEN", None)

        def _restore():
            try:
                os.chmod(f, 0o600)
            except OSError:
                pass
            if env_tok is not None:
                os.environ["ROMP_SERVE_TOKEN"] = env_tok
            if saved is not None:
                f.write_text(saved)
        self.addCleanup(_restore)
        f.write_text("live-token")
        os.chmod(f, 0)
        with self.assertRaises(RuntimeError):
            ps._load_serve_token()
        os.chmod(f, 0o600)
        self.assertEqual(f.read_text(), "live-token")

    def test_a_nonempty_loose_file_is_tightened_on_read(self):
        # the read path keeps the inode, so it must chmod what it keeps (the user's audit,
        # 2026-08-19: a 0644 nonempty token was returned as-is)
        import stat
        f = ps.STATE.parent / "serve-token"
        f.parent.mkdir(parents=True, exist_ok=True)
        saved = f.read_text() if f.exists() else None
        env_tok = os.environ.pop("ROMP_SERVE_TOKEN", None)

        def _restore():
            if env_tok is not None:
                os.environ["ROMP_SERVE_TOKEN"] = env_tok
            if saved is not None:
                f.write_text(saved)
        self.addCleanup(_restore)
        f.write_text("keep-me-token\n")
        os.chmod(f, 0o644)
        self.assertEqual(ps._load_serve_token(), "keep-me-token")
        self.assertEqual(stat.S_IMODE(os.stat(f).st_mode), 0o600,
                         "a kept token file must not stay at the loose mode it arrived with")



class WireCarriesTheStableId(unittest.TestCase):
    """the v1.3.17 audit's P1.6: /send resolved the uuid but serialized the current NAME into
    the relay envelope — a rename after enqueue bounced the delivery. The envelope now carries
    the validated stable id."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), ps.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_send_stamps_to_id_into_the_outbox_envelope(self):
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        ps.PEER_STATE["hostx"] = {"presence": [{"name": "web", "id": "sid-web1"}],
                                  "epoch": 1, "seenAt": 1}
        ps.PEERS["hostx"] = {"port": 1, "up": False}
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:%d/send" % self.port,
                data=json.dumps({"to": "hostx:web", "from": "api", "from_id": "sid-a",
                                 "body": "hello", "kind": "question"}).encode(),
                headers={"Content-Type": "application/json", "X-Romp-Token": TOK},
                method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                out = json.loads(r.read().decode())
            self.assertTrue(out.get("ok"))
            msg = ps.outbox_get("hostx", out["id"])
            self.assertIsNotNone(msg)
            self.assertEqual(msg.get("to_id"), "sid-web1",
                             "the stable id rides the wire; the name is display/legacy only")
        finally:
            ps.PEER_STATE.pop("hostx", None)
            ps.PEERS.pop("hostx", None)
            os.environ.pop("ROMP_POSTAL_PEERS", None)


class McpTrackedIsARealBoolean(unittest.TestCase):
    """the v1.3.17 audit's P2.13, MCP surface: bool("false") armed tracking the sender declined,
    and `is True` alone would silently DROP a tracking the sender believed armed — refuse."""

    def test_a_string_tracked_refuses_and_sends_nothing(self):
        with mock.patch.object(ps, "my_name", lambda: "web"), \
             mock.patch.object(ps, "my_id", lambda: "sid-w"), \
             mock.patch.object(ps, "_heartbeat", lambda *a, **k: None), \
             mock.patch.object(ps, "_http", side_effect=AssertionError("must not send")):
            out, is_err = ps._mcp_call("send_message",
                                       {"to": "api", "body": "x", "kind": "delegate",
                                        "tracked": "false"})
        self.assertTrue(is_err)
        self.assertIn("boolean", out)

    def test_a_real_boolean_still_arms(self):
        seen = {}

        def fake_http(method, path, payload):
            seen.update(payload)
            return {"ok": True, "id": "m1"}

        with mock.patch.object(ps, "my_name", lambda: "web"), \
             mock.patch.object(ps, "my_id", lambda: "sid-w"), \
             mock.patch.object(ps, "_heartbeat", lambda *a, **k: None), \
             mock.patch.object(ps, "_http", side_effect=fake_http):
            ps._mcp_call("send_message", {"to": "api", "body": "x", "kind": "delegate",
                                          "tracked": True})
        self.assertIs(seen.get("tracked"), True)


class TrackedIsARealBoolean(unittest.TestCase):
    """the v1.3.16 audit: bool("false") is True — the string spelling ARMED the report-back the
    sender declined. A behavioral flag takes a real JSON boolean only."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), ps.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _send(self, payload):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/send" % self.port, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-Romp-Token": TOK}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_a_string_false_is_a_400_never_an_armed_track(self):
        st, body = self._send({"to": "api", "from": "web", "from_id": "sid-w",
                               "body": "x", "kind": "delegate", "tracked": "false"})
        self.assertEqual(st, 400, body)
        self.assertIn("boolean", body)

    def test_a_real_boolean_passes_this_gate(self):
        st, body = self._send({"to": "no-such-recipient-zzz", "from": "web", "from_id": "sid-w",
                               "body": "x", "kind": "delegate", "tracked": True})
        self.assertNotEqual(st, 400, "a real boolean reaches recipient resolution: %s" % body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
