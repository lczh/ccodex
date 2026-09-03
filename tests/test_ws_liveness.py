#!/usr/bin/env python3
"""The kernel notices a dead dashboard pane (2026-09-03).

The keepalive was one-way: the kernel sent a small frame every KEEPALIVE_S and expected nothing back. A
peer whose forwarder kept the devbox-side socket open after the browser pane was gone — VS Code's port
forwarder does exactly that — therefore stayed a "live dashboard" forever, and the pusher kept building
and streaming every full payload to it. Measured live: 84 client connections on one kernel, 73 silent for
over five minutes, all phantoms sharing one multiplexed channel with three real panes.

Now every beat is also a WebSocket PING (RFC 6455), which every conforming peer answers with a PONG
without application code; a peer that has not answered the oldest outstanding ping within WS_DEAD_S is
dropped — the missing pong is the event. Any inbound message counts as life too. Real TCP loopback
pairs, matching the kernel's transport. Synthetic only.
"""
import json
import os
import socket
import struct
import tempfile
import threading
import time
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = SourceFileLoader("romp_kernel_wslive", os.path.join(BIN, "romp-kernel")).load_module()


def _mask(payload):
    """A client-side (masked) frame body for `payload` with a fixed mask — what a browser's pong looks like."""
    mask = b"\x11\x22\x33\x44"
    return mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def _client_frame(opcode, payload):
    n = len(payload)
    hdr = bytes([0x80 | opcode, 0x80 | n]) if n < 126 else bytes([0x80 | opcode, 0x80 | 126]) + struct.pack(">H", n)
    return hdr + _mask(payload)


class _Peer(threading.Thread):
    """The far end of a pane's socket. `pongs=True` behaves like a browser (answers every ping with a pong
    echoing its payload); `pongs=False` is the phantom: it reads what arrives and never answers."""

    def __init__(self, sock, pongs):
        super().__init__(daemon=True)
        self.sock, self.pongs, self.pings, self.texts, self.stop = sock, pongs, [], [], False

    def run(self):
        rf = self.sock.makefile("rb")
        while not self.stop:
            op, payload, fin = km._ws_recv(rf)      # server frames are unmasked; the reader tolerates that
            if op is None:
                return
            if op == 0x9:
                self.pings.append(payload)
                if self.pongs:
                    try:
                        self.sock.sendall(_client_frame(0xA, payload))
                    except OSError:
                        return
            elif op == 0x1:
                self.texts.append(payload)


class _Handler(threading.Thread):
    """The kernel side's read loop, as the connection handler runs it: pongs and messages stamp liveness."""

    def __init__(self, client, sock):
        super().__init__(daemon=True)
        self.client, self.sock, self.msgs = client, sock, []

    def run(self):
        rf = self.sock.makefile("rb")
        while self.client["alive"]:
            op, payload = km._ws_recv_message(rf, lambda p: None, on_pong=lambda p: km._note_ws_inbound(self.client))
            if op is None:
                break
            km._note_ws_inbound(self.client)
            self.msgs.append(payload)
        self.client["alive"] = False


class PhantomPanesAreDropped(unittest.TestCase):
    def setUp(self):
        self.closeables = []
        self._saved_clients = list(km._clients)
        km._clients[:] = []
        self.clock = [1_800_000_000.0]
        self._saved_clock = km._ws_clock
        km._ws_clock = lambda: self.clock[0]

    def tearDown(self):
        km._ws_clock = self._saved_clock
        km._clients[:] = self._saved_clients
        for s in self.closeables:
            try:
                s.close()
            except OSError:
                pass

    def _pair(self):
        srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0)); srv.listen(1)
        peer = socket.create_connection(srv.getsockname())
        kern, _ = srv.accept()
        srv.close()
        self.closeables += [peer, kern]
        return kern, peer

    def _connect(self, pongs, app="timeline"):
        kern, peer_sock = self._pair()
        client, q, lock = km._new_ws_client(app, "w1", kern)
        km._clients.append(client)
        peer = _Peer(peer_sock, pongs); peer.start()
        handler = _Handler(client, kern); handler.start()
        return client, peer, handler

    def _settle(self, pred, timeout=3.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if pred():
                return True
            time.sleep(0.02)
        return pred()

    def test_a_the_beat_carries_a_ping_and_a_browser_answers_it(self):
        client, peer, _ = self._connect(pongs=True)
        t0 = 1_800_000_000.0
        km._keepalive_all(now=t0)
        self.assertTrue(self._settle(lambda: len(peer.pings) == 1 and len(peer.texts) == 1), "one ka text + one ping per beat")
        self.assertEqual(json.loads(peer.texts[0])["type"], "ka")
        self.assertEqual(peer.pings[0], str(int(t0)).encode(), "the ping carries the beat's stamp")
        self.assertTrue(self._settle(lambda: client["pingAt"] is None), "the pong cleared the outstanding ping")
        self.assertTrue(client["alive"])

    def test_b_a_peer_that_never_answers_is_dropped_after_the_window_and_only_then(self):
        client, peer, handler = self._connect(pongs=False)
        t0 = self.clock[0]
        km._keepalive_all(now=t0)
        self.assertTrue(self._settle(lambda: len(peer.pings) == 1))
        self.assertTrue(self._settle(lambda: client["pingAt"] == t0), "the clock runs from the first unanswered ping, stamped as it left")
        self.clock[0] = t0 + km.KEEPALIVE_S
        km._keepalive_all(now=self.clock[0])                        # second beat: still inside the window
        self.assertTrue(self._settle(lambda: len(peer.pings) == 2))
        self.assertTrue(client["alive"], "one missed pong is not death")
        self.assertEqual(client["pingAt"], t0, "…and the oldest unanswered ping keeps the clock")
        self.clock[0] = t0 + km.WS_DEAD_S
        km._keepalive_all(now=self.clock[0])                        # the window has run out
        self.assertFalse(client["alive"], "no pong within WS_DEAD_S → dropped")
        handler.join(3.0)
        self.assertFalse(handler.is_alive(), "the shutdown ended the handler's blocking read")
        self.assertTrue(self._settle(lambda: not peer.is_alive()), "…and the peer saw the close")

    def test_c_any_inbound_message_is_life(self):
        client, peer, handler = self._connect(pongs=False)          # never pongs, but talks
        t0 = self.clock[0]
        km._keepalive_all(now=t0)
        self.assertTrue(self._settle(lambda: len(peer.pings) == 1 and client["pingAt"] == t0))
        peer.sock.sendall(_client_frame(0x1, json.dumps({"type": "activeTab", "id": "x"}).encode()))
        self.assertTrue(self._settle(lambda: client["pingAt"] is None), "a message cleared the outstanding ping")
        self.clock[0] = t0 + km.WS_DEAD_S + 1
        km._keepalive_all(now=self.clock[0])
        self.assertTrue(client["alive"], "a talking peer is never dropped for a missing pong")

    def test_c2_the_clock_starts_when_the_ping_leaves_not_when_the_beat_queued_it(self):
        """A slow-but-alive peer with a backlog ahead of the ping must not be judged on time the ping spent in
        the queue — the diagnosis this change answers found a 300 KB/s tunnel client rightly kept alive."""
        kern, peer_sock = self._pair()
        client, q, lock = km._new_ws_client("timeline", "w1", kern, start_sender=False)   # sender held back = backlog
        km._clients.append(client)
        t0 = self.clock[0]
        km._keepalive_all(now=t0)
        self.assertIsNone(client["pingAt"], "queued, not on the wire → no clock yet")
        self.clock[0] = t0 + 10 * km.WS_DEAD_S                       # a long backlog later…
        km._keepalive_all(now=self.clock[0])
        self.assertTrue(client["alive"], "…still not judged: no ping has reached the peer")
        threading.Thread(target=km._ws_sender, args=(q, kern, lock, client), daemon=True).start()   # backlog drains
        self.assertTrue(self._settle(lambda: client["pingAt"] == self.clock[0]), "stamped as the first ping left")
        q.put(None)

    def test_d_the_ping_frame_is_well_formed(self):
        f = km._ws_ping_frame(b"1700000000")
        self.assertEqual(f[0], 0x89, "FIN + ping opcode")
        self.assertEqual(f[1], len(b"1700000000"))
        self.assertEqual(f[2:], b"1700000000")
        self.assertEqual(len(km._ws_ping_frame(b"x" * 300)), 2 + 125, "control payloads are capped at 125 bytes")

    def test_e_a_legacy_client_without_a_socket_is_never_judged(self):
        sent = []
        legacy = {"app": "feed", "wid": "", "alive": True, "send": sent.append}
        km._clients.append(legacy)
        km._keepalive_all(now=1_800_000_000.0)
        km._keepalive_all(now=1_800_000_000.0 + 10 * km.WS_DEAD_S)
        self.assertTrue(legacy["alive"])
        self.assertEqual(len(sent), 2, "it still gets the ka text and never a ping")


if __name__ == "__main__":
    unittest.main()
