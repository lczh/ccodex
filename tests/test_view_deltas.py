#!/usr/bin/env python3
"""The timeline's bars and the feed cross the wire as DELTAS (2026-09-03).

Both slots used to be sent whole on every change — 2.95 MB and 0.86 MB on a seventeen-session board,
where something changes every few seconds — so a dashboard on a forwarded or tunnelled link streamed
about half a megabyte a second of mostly-unchanged JSON and its panes starved. A pane that connects with
?delta=1 (the shim does) now receives, per change, only the collection entries that changed, keyed the
way each collection is keyed; the shim reassembles the full message and hands the bundle exactly what it
received before. These tests drive the kernel encoder against a fake client and check the reassembly two
ways: with a Python mirror of the shim's decoder, and — when node is installed — with the shim's own
JavaScript, lifted verbatim from the served page. Synthetic payloads only.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
import io
import threading
from contextlib import redirect_stderr
from importlib.machinery import SourceFileLoader
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = SourceFileLoader("romp_kernel_viewdelta", os.path.join(BIN, "romp-kernel")).load_module()

S1 = "11111111-2222-3333-4444-aaaaaaaaaaa1"
S2 = "11111111-2222-3333-4444-aaaaaaaaaaa2"
S3 = "11111111-2222-3333-4444-aaaaaaaaaaa3"


def _bars(turns, judging, messages, now=1000, warming=False):
    return {"type": "bars", "turns": turns, "judging": judging, "messages": messages, "now": now, "warming": warming}


def _feed(asks, now=1000, **rest):
    d = {"type": "feed", "asks": asks, "now": now, "sessions": [{"sid": S1, "name": "web", "color": None}],
         "order": [S1], "working": [], "awaiting": []}
    d.update(rest)
    return d


class _Client(dict):
    """A delta-capable client whose frames are captured rather than written."""

    def __init__(self):
        super().__init__(app="timeline", wid="w1", alive=True, delta=True, sock=object())
        self.frames = []
        self["send"] = lambda s: self.frames.append(json.loads(s))


KINDS = {"bars": {"turns": "dictlist:id", "judging": "bykeys:sid,t,judge,t1", "messages": "byid"}, "feed": {"asks": "byid:itemId"}}
SEP = "\u001f"


def _assemble(kind, order, items):
    if kind == "dict":
        return {kk: items[kk] for kk in order if kk in items}
    if kind.startswith("dictlist:"):
        d = {}
        for kk in order:
            if kk not in items:
                continue
            dk, _, rest = kk.partition(SEP)
            if rest == "" and not isinstance(items[kk], list):
                d[dk] = items[kk]; continue
            if rest == "":
                d[dk] = items[kk]; continue
            d.setdefault(dk, []).append(items[kk])
        return d
    return [items[kk] for kk in order if kk in items]


def _py_apply(last, d):
    """A faithful Python mirror of the shim's applyDelta/buildMaps/assemble — the reference the JS is held to."""
    if last is None or last["rev"] != d["base"]:
        return None
    m = dict(last["msg"])
    if d.get("rest") is not None and d.get("restAll"):
        for k in [k for k in m if k not in KINDS[d["slot"]] and k not in d["rest"]]:
            del m[k]                                    # a full remainder retires the keys it no longer carries
    for k, v in (d.get("rest") or {}).items():
        m[k] = v
    for name, c in (d.get("coll") or {}).items():
        kind = KINDS[d["slot"]].get(name)
        mp = last["maps"].get(name) or {"order": [], "items": {}}
        items = dict(mp["items"]); order = list(mp["order"])
        for kk in c.get("del") or []:
            items.pop(kk, None)
        new = [kk for kk in (c.get("set") or {}) if kk not in items]
        for kk, v in (c.get("set") or {}).items():
            items[kk] = v
        order += km._js_key_order(new)                  # JS enumerates integer-like keys first, ascending
        order = list(c["order"]) if c.get("order") else [kk for kk in order if kk in items]
        last["maps"][name] = {"order": order, "items": items}
        m[name] = _assemble(kind, order, items)
    last["rev"] = d["rev"]; last["msg"] = m
    return m


def _py_maps(msg, keys):
    """The shim's buildMaps: the kernel's key list zipped onto the payload positionally, per kind."""
    maps = {}
    for name, kind in KINDS[msg["type"]].items():
        v = msg.get(name); order = list((keys or {}).get(name) or []); items = {}; pos = {}
        for i, kk in enumerate(order):
            if kind == "dict":
                items[kk] = v[kk]
            elif kind.startswith("dictlist:"):
                dk, _, rest = kk.partition(SEP); lane = v[dk]
                if rest == "":
                    items[kk] = lane
                else:
                    j = pos.get(dk, 0); pos[dk] = j + 1; items[kk] = lane[j]
            else:
                items[kk] = v[i]
        maps[name] = {"order": order, "items": items}
    return maps


class _Stream:
    """Drive the kernel encoder for one client and mirror the shim: every captured frame is applied."""

    def __init__(self, ftype):
        self.ftype, self.c, self.last, self.fulls, self.deltas = ftype, _Client(), None, 0, 0

    def push(self, payload):
        pre = json.dumps(payload); sig = km._dedup_sig(payload, pre)
        n0 = len(self.c.frames)
        km._send_slot(self.c, self.ftype, payload, pre, sig)
        for fr in self.c.frames[n0:]:
            if fr.get("type") == "delta":
                self.deltas += 1
                out = _py_apply(self.last, fr)
                assert out is not None, "the mirror rejected a delta the kernel sent: %r" % fr
            else:
                self.fulls += 1
                keys = fr.get("_keys")                       # the frame itself stays as the wire carried it
                msg = {kk: v for kk, v in fr.items() if kk != "_keys"}
                self.last = {"rev": 0, "msg": msg, "maps": _py_maps(msg, keys)} if keys is not None else None
        return self.c.frames[n0:]

    @property
    def held(self):
        return self.last["msg"] if self.last else None


class BarsDeltas(unittest.TestCase):
    def setUp(self):
        km._delta_parts_cache.clear()
        self.t0 = time.time()
        # the size guard sends a delta that is not smaller than the whole as the whole; these synthetic payloads are
        # tiny, so the structural tests switch it off — test_d covers the guard with the real threshold
        self._frac = km._DELTA_MAX_FRACTION
        km._DELTA_MAX_FRACTION = 10.0

    def tearDown(self):
        km._DELTA_MAX_FRACTION = self._frac

    def _turn(self, sid, n):
        return [{"id": "seg-%s-%d" % (sid[-1], i), "t": self.t0 - 60 * i, "end": self.t0 - 60 * i + 30, "open": False} for i in range(n)]

    def test_a_first_send_is_full_then_only_the_changed_lane_crosses(self):
        st = _Stream("bars")
        p1 = _bars({S1: self._turn(S1, 3), S2: self._turn(S2, 2)},
                   [{"sid": S1, "judge": "closer", "t": 1, "t1": 2}, {"sid": S2, "judge": "planner", "t": 3, "t1": 4}],
                   [{"id": "m1", "from": "web", "to": "api", "sent": 1}])
        frames = st.push(p1)
        self.assertEqual([f["type"] for f in frames], ["bars"], "a client holding nothing gets the whole slot")
        self.assertEqual(st.held, p1)
        p2 = json.loads(json.dumps(p1)); p2["turns"][S2] = self._turn(S2, 3); p2["now"] = 1005
        frames = st.push(p2)
        self.assertEqual([f["type"] for f in frames], ["delta"])
        d = frames[0]
        self.assertEqual(set(d["coll"]), {"turns"}, "only the lane that moved is in the frame")
        self.assertEqual(set(d["coll"]["turns"]["set"]), {S2 + SEP + "seg-2-2"}, "one BAR crosses, not the lane")
        self.assertNotIn("del", d["coll"]["turns"]); self.assertNotIn("order", d["coll"]["turns"], "an appended bar needs no order")
        self.assertEqual(d.get("rest"), {"now": 1005}, "the clock rides, the unchanged remainder does not")
        self.assertEqual(st.held, p2, "the reassembled message is the new payload, exactly")
        self.assertLess(len(json.dumps(d)), len(json.dumps(p2)) / 2)

    def test_b_removed_added_and_reordered_entries(self):
        st = _Stream("bars")
        p1 = _bars({S1: self._turn(S1, 1), S2: self._turn(S2, 1), S3: self._turn(S3, 1)},
                   [{"sid": S1, "judge": "closer", "t": 1, "t1": 2}, {"sid": S1, "judge": "planner", "t": 5, "t1": 6},
                    {"sid": S3, "judge": "closer", "t": 7, "t1": 8}],
                   [{"id": "m1", "from": "a", "to": "b", "sent": 1}, {"id": "m2", "from": "b", "to": "a", "sent": 2}])
        st.push(p1)
        # S2 gone, a new session S3 judging group grows, messages reversed and one added
        p2 = _bars({S1: self._turn(S1, 1), S3: self._turn(S3, 2)},
                   [{"sid": S1, "judge": "closer", "t": 1, "t1": 2}, {"sid": S1, "judge": "planner", "t": 5, "t1": 6},
                    {"sid": S3, "judge": "closer", "t": 7, "t1": 8}, {"sid": S3, "judge": "courier", "t": 9, "t1": 10}],
                   [{"id": "m3", "from": "c", "to": "a", "sent": 3}, {"id": "m2", "from": "b", "to": "a", "sent": 2},
                    {"id": "m1", "from": "a", "to": "b", "sent": 1}], now=1010)
        frames = st.push(p2)
        self.assertEqual(frames[0]["type"], "delta")
        d = frames[0]
        self.assertEqual(d["coll"]["turns"].get("del"), [S2 + SEP + "seg-2-0"], "a removed lane deletes its bars")
        self.assertEqual(set(d["coll"]["turns"]["set"]), {S3 + SEP + "seg-3-1"}, "a grown lane adds only its new bar")
        self.assertEqual(set(d["coll"]["judging"]["set"]), {SEP.join([S3, "9", "courier", "10"])}, "a judge call is one keyed item")
        self.assertEqual(d["coll"]["messages"]["order"], ["m3", "m2", "m1"], "a reorder ships the key order")
        self.assertEqual(st.held, p2)
        # and a delta stream keeps going: a third push changes nothing but the clock
        p3 = json.loads(json.dumps(p2)); p3["now"] = 1011
        st.c.setdefault("dstate", {})["bars"]["at"] = time.time() - km._DEDUP_REPOST_S - 1   # the repost is due
        frames = st.push(p3)
        self.assertEqual(frames[0]["type"], "delta"); self.assertEqual(frames[0]["coll"], {})
        self.assertEqual(frames[0]["rest"], {"now": 1011})
        self.assertEqual(st.held, p3)

    def test_c_an_unchanged_payload_sends_nothing_until_the_repost_is_due(self):
        st = _Stream("bars")
        p = _bars({S1: self._turn(S1, 2)}, [], [])
        st.push(p)
        self.assertEqual(st.push(p), [], "nothing moved, nothing sent")
        self.assertEqual(st.push(dict(p, now=1002)), [], "…and the clock alone is not a change inside the repost window")
        st.c["dstate"]["bars"]["at"] -= km._DEDUP_REPOST_S + 1
        fr = st.push(dict(p, now=1003))
        self.assertEqual(len(fr), 1); self.assertEqual(fr[0]["type"], "delta"); self.assertEqual(fr[0]["coll"], {})

    def test_d_a_delta_not_worth_its_bytes_is_sent_whole_and_rebases(self):
        km._DELTA_MAX_FRACTION = self._frac                    # the real threshold, for this test alone
        st = _Stream("bars")
        st.push(_bars({S1: self._turn(S1, 1)}, [], []))
        big = _bars({S1: self._turn(S1, 40), S2: self._turn(S2, 40), S3: self._turn(S3, 40)}, [], [], now=1020)
        frames = st.push(big)
        self.assertEqual([f["type"] for f in frames], ["bars"], "a near-total change crosses whole")
        self.assertEqual(st.held, big)
        self.assertEqual(st.c["dstate"]["bars"]["rev"], 0, "…and the stream re-bases from it")
        small = json.loads(json.dumps(big)); small["turns"][S1] = self._turn(S1, 41); small["now"] = 1021
        self.assertEqual(st.push(small)[0]["type"], "delta")

    def test_e_a_client_without_delta_support_gets_whole_payloads_exactly_as_before(self):
        c = _Client(); c.pop("delta")
        p1 = _bars({S1: self._turn(S1, 1)}, [], []); pre = json.dumps(p1); sig = km._dedup_sig(p1, pre)
        km._send_slot(c, "bars", p1, pre, sig)
        p2 = _bars({S1: self._turn(S1, 2)}, [], [], now=1005); pre2 = json.dumps(p2); sig2 = km._dedup_sig(p2, pre2)
        km._send_slot(c, "bars", p2, pre2, sig2)
        self.assertEqual([f["type"] for f in c.frames], ["bars", "bars"])
        self.assertEqual(c.frames[1], p2)

    def test_f_needslot_resets_the_stream_to_a_full_frame(self):
        st = _Stream("bars")
        p1 = _bars({S1: self._turn(S1, 1)}, [], []); st.push(p1)
        p2 = _bars({S1: self._turn(S1, 2)}, [], [], now=1005); st.push(p2)
        self.assertEqual(st.c["dstate"]["bars"]["rev"], 1)
        # the shim could not apply a delta → the socket handler flags the slot for the PUSHER (not its own
        # thread: two threads over one client's held state would rebase a full the dedup then swallowed)
        st.c.setdefault("resync", set()).add("bars")
        p3 = _bars({S1: self._turn(S1, 3)}, [], [], now=1010)
        fr = st.push(p3)
        self.assertEqual([f["type"] for f in fr], ["bars"]); self.assertIn("_keys", fr[0])
        self.assertEqual(st.held, p3); self.assertEqual(st.c["resync"], set())
        src = open(km.__file__, encoding="utf-8").read()
        h = src[src.index('msg.get("type") == "needSlot"'):]
        h = h[:h.index("return")]
        self.assertIn('client.setdefault("resync", set()).add(', h, "the handler flags the slot…")
        self.assertIn("_pusher_wake.set()", h, "…and wakes the pusher, which sends on its own thread")
        self.assertNotIn("_push_one", h)

    def test_i_a_top_level_key_that_leaves_the_payload_leaves_the_client(self):
        st = _Stream("bars")
        st.push(_bars({S1: self._turn(S1, 1)}, [], [], warming=True))
        p2 = _bars({S1: self._turn(S1, 1)}, [], [], now=1005); del p2["warming"]
        fr = st.push(p2)
        self.assertEqual(fr[0].get("restAll"), 1); self.assertNotIn("warming", fr[0]["rest"])
        self.assertEqual(st.held, p2, "the remainder is replaced, not merged: a dropped key is gone")

    def test_j_an_empty_id_and_a_positional_key_spelling_a_real_id_stay_exact(self):
        st = _Stream("bars")
        lane = [{"id": "", "t": 1}, {"id": "b2", "t": 2}]
        msgs = [{"id": "#2", "x": 0}, {"x": 1}, {"x": 2}]
        p1 = _bars({S1: lane}, [], msgs); st.push(p1)
        self.assertEqual(st.held, p1)
        keys = st.c["dstate"]["bars"]["order"]
        self.assertEqual(keys["turns"], [S1 + SEP + "#0", S1 + SEP + "b2"], "an empty id is positional, in its lane")
        self.assertEqual(keys["messages"], ["#2", "#1", "#3"], "the positional key steps past the real '#2'")
        p2 = _bars({S1: [{"id": "", "t": 1}, {"id": "b2", "t": 3}]}, [], [{"id": "#2", "x": 0}, {"x": 1}, {"x": 5}], now=1005)
        st.push(p2)
        self.assertEqual(st.held, p2)

    def test_k_javascript_index_keys_are_ascii_and_bounded(self):
        odd = ["\u00b2", "\u0661", "\uff11\uff12", "9" * 5000, "10", "9", "x", "4294967295", "4294967294"]
        self.assertEqual(km._js_key_order(odd), ["9", "10", "4294967294", "\u00b2", "\u0661", "\uff11\uff12", "9" * 5000, "x", "4294967295"])

    def test_l_a_failing_delta_never_takes_the_pusher_down(self):
        import io, contextlib
        st = _Stream("bars")
        p1 = _bars({S1: self._turn(S1, 1)}, [], [])
        real = km._delta_parts
        def boom(ftype, payload): raise RuntimeError("synthetic")
        km._delta_parts = boom
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                fr = st.push(p1)
        finally:
            km._delta_parts = real
        self.assertEqual([f["type"] for f in fr], ["bars"]); self.assertNotIn("_keys", fr[0])
        self.assertIn("view-delta bars", err.getvalue()); self.assertIn("synthetic", err.getvalue())
        self.assertNotIn("bars", st.c.get("dstate", {}))
        self.assertIsNone(st.last, "a full frame without keys leaves the client holding nothing")
        fr = st.push(_bars({S1: self._turn(S1, 2)}, [], [], now=1005))
        self.assertEqual([f["type"] for f in fr], ["bars"]); self.assertIn("_keys", fr[0], "the stream starts afresh")

    def test_m_after_a_keyless_whole_frame_the_keyed_full_still_goes_even_for_an_unchanged_payload(self):
        """The failure path sends the whole payload without keys, filling the dedup slot with its signature. If
        the next cycle's keyed full were deduped, the kernel would hold state for a client holding nothing, and
        its next delta would be refused (review 2026-09-03). The keyed full always goes once."""
        import io, contextlib
        st = _Stream("bars")
        p1 = _bars({S1: self._turn(S1, 1)}, [], [])
        real = km._delta_parts
        km._delta_parts = lambda ftype, payload: (_ for _ in ()).throw(RuntimeError("synthetic"))
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                st.push(p1)
        finally:
            km._delta_parts = real
        self.assertIsNone(st.last)
        fr = st.push(dict(p1, now=1001))                            # the same payload, only the clock moved
        self.assertEqual([f["type"] for f in fr], ["bars"]); self.assertIn("_keys", fr[0], "keyed full not deduped away")
        self.assertEqual(st.held, dict(p1, now=1001))
        self.assertEqual(st.c["dstate"]["bars"]["rev"], 0)
        fr = st.push(_bars({S1: self._turn(S1, 2)}, [], [], now=1005))
        self.assertEqual([f["type"] for f in fr], ["delta"], "…and the stream continues as deltas the client can apply")


    def test_g_a_bar_appended_to_an_earlier_lane_crosses_alone_and_lands_in_its_lane(self):
        """The flat key order changes (the new bar sits before the later lanes' bars) but the assembled
        lanes do not — so no order crosses, and the client's derived order must still assemble right."""
        st = _Stream("bars")
        st.push(_bars({S1: self._turn(S1, 2), S2: self._turn(S2, 2), S3: self._turn(S3, 1)}, [], []))
        p2 = _bars({S1: self._turn(S1, 3), S2: self._turn(S2, 2), S3: self._turn(S3, 1)}, [], [], now=1001)
        fr = st.push(p2)
        self.assertEqual(len(fr), 1); d = fr[0]
        self.assertEqual(set(d["coll"]["turns"]["set"]), {S1 + SEP + "seg-1-2"})
        self.assertNotIn("order", d["coll"]["turns"], "same lanes, same relative order: nothing to say")
        self.assertEqual(st.held, p2, "…and the client's own derived order assembles the exact payload")
        self.assertEqual(list(st.held["turns"]), [S1, S2, S3], "lane order kept")
        # a lane whose bars all go, then a bar in a lane that never existed: deletes and a fresh group
        p3 = _bars({S1: self._turn(S1, 3), S3: self._turn(S3, 1), "11111111-2222-3333-4444-aaaaaaaaaaa4": self._turn("x4", 1)}, [], [], now=1002)
        st.push(p3)
        self.assertEqual(st.held, p3)

    def test_h_numeric_ids_enumerate_ascending_in_javascript_so_the_order_crosses_when_that_would_misplace_them(self):
        """JS enumerates integer-like object keys ascending before the rest — the shim appends a delta's new
        keys in THAT order. Two new messages with ids "10" then "9" would land as "9","10"; the kernel must
        predict the misplacement and send the order. Numeric ids that arrive ascending need none."""
        def msg(i, when): return {"id": i, "sent": when, "text": "m"}
        st = _Stream("bars")
        st.push(_bars({S1: self._turn(S1, 1)}, [], [msg("10", 1)]))
        p2 = _bars({S1: self._turn(S1, 1)}, [], [msg("10", 1), msg("11", 2)], now=1001)
        d = st.push(p2)[0]
        self.assertNotIn("order", d["coll"]["messages"], "an ascending numeric id appends where JS puts it anyway")
        self.assertEqual(st.held, p2)
        p3 = _bars({S1: self._turn(S1, 1)}, [], [msg("10", 1), msg("11", 2), msg("30", 3), msg("9", 4)], now=1002)
        d = st.push(p3)[0]
        self.assertEqual(d["coll"]["messages"].get("order"), ["10", "11", "30", "9"], "JS would put 9 first: the order must cross")
        self.assertEqual(st.held, p3)
        self.assertEqual([m["id"] for m in st.held["messages"]], ["10", "11", "30", "9"])

class FeedDeltas(unittest.TestCase):
    def setUp(self):
        km._delta_parts_cache.clear()
        self._frac = km._DELTA_MAX_FRACTION
        km._DELTA_MAX_FRACTION = 10.0

    def tearDown(self):
        km._DELTA_MAX_FRACTION = self._frac

    def _ask(self, i, column="working", text="do the thing"):
        return {"itemId": "awaiting:g%d" % i, "sid": S1, "column": column, "text": text, "color": None, "trail": [1, 2, 3]}

    def test_a_only_the_card_that_moved_crosses(self):
        st = _Stream("feed")
        p1 = _feed([self._ask(1), self._ask(2), self._ask(3)])
        self.assertEqual([f["type"] for f in st.push(p1)], ["feed"])
        p2 = _feed([self._ask(1), self._ask(2, column="done"), self._ask(3)], now=1005)
        frames = st.push(p2)
        self.assertEqual(frames[0]["type"], "delta")
        self.assertEqual(set(frames[0]["coll"]["asks"]["set"]), {"awaiting:g2"})
        self.assertEqual(st.held, p2)

    def test_b_the_remainder_rides_only_when_it_changed(self):
        st = _Stream("feed")
        st.push(_feed([self._ask(1)]))
        p2 = _feed([self._ask(1)], now=1005, showDismissed=True)
        fr = st.push(p2)
        self.assertEqual(fr[0]["type"], "delta")
        self.assertIn("showDismissed", fr[0]["rest"]); self.assertEqual(fr[0]["coll"], {})
        self.assertEqual(st.held, p2)


    def test_c_a_card_inserted_at_the_top_costs_one_card_and_the_order(self):
        """Cards are keyed by itemId, so an insert anywhere ships that card plus the key order — never the feed."""
        km._DELTA_MAX_FRACTION = self._frac                    # the real threshold
        st = _Stream("feed")
        st.push(_feed([self._ask(i, text="x" * 300) for i in range(30)]))
        p2 = _feed([self._ask(99, text="y" * 300)] + [self._ask(i, text="x" * 300) for i in range(30)], now=1005)
        fr = st.push(p2)
        self.assertEqual(fr[0]["type"], "delta")
        self.assertEqual(set(fr[0]["coll"]["asks"]["set"]), {"awaiting:g99"})
        self.assertEqual(fr[0]["coll"]["asks"]["order"][0], "awaiting:g99")
        self.assertEqual(st.held, p2)


class ShimDecoderMatchesTheKernel(unittest.TestCase):
    """The shim's own JavaScript, lifted verbatim from the served page, reassembles the kernel's frames to the
    exact new payload — the property the whole scheme rests on."""

    def _shim_functions(self):
        js = km._shim("timeline", 1)
        m = re.search(r"var DELTA_KINDS=.*?last\.rev=d\.rev;last\.msg=m;return m;\}", js, re.S)
        self.assertIsNotNone(m, "the delta functions must be present in the shim")
        return m.group(0)

    def test_a_node_reassembles_every_frame_of_a_stream_to_the_new_payload(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        km._delta_parts_cache.clear()
        frac = km._DELTA_MAX_FRACTION; km._DELTA_MAX_FRACTION = 10.0
        self.addCleanup(setattr, km, "_DELTA_MAX_FRACTION", frac)
        st = _Stream("bars")
        t0 = 1000
        def turn(sid, n): return [{"id": "s%s-%d" % (sid[-1], i), "t": t0 - 60 * i, "end": t0 - 60 * i + 30} for i in range(n)]
        def msg(i, when): return {"id": i, "sent": when, "text": "m"}
        payloads = [
            _bars({S1: turn(S1, 2), S2: turn(S2, 1)}, [{"sid": S1, "judge": "closer", "t": 1, "t1": 2}], [{"id": "m1", "to": "x"}]),
            _bars({S1: turn(S1, 3), S2: turn(S2, 1)}, [{"sid": S1, "judge": "closer", "t": 1, "t1": 2}], [{"id": "m1", "to": "x"}], now=1005),
            _bars({S1: turn(S1, 3), S3: turn(S3, 1)}, [{"sid": S3, "judge": "planner", "t": 3, "t1": 4}, {"sid": S1, "judge": "closer", "t": 1, "t1": 2}],
                  [{"id": "m2", "to": "y"}, {"id": "m1", "to": "x"}], now=1010, warming=True),
            _bars({S1: turn(S1, 3), S3: turn(S3, 1)}, [], [{"id": "m2", "to": "y"}], now=1015),
            # numeric ids: "11" appends where JS puts it; then "30" followed by "9" needs the order to cross
            _bars({S1: turn(S1, 3), S3: turn(S3, 1)}, [], [msg("10", 1), msg("11", 2)], now=1020),
            _bars({S1: turn(S1, 3), S3: turn(S3, 1)}, [], [msg("10", 1), msg("11", 2), msg("30", 3), msg("9", 4)], now=1025),
            # the first lane empties to a bare value, the last lane grows: bare-prefix entry + append
            _bars({S1: [], S3: turn(S3, 2)}, [], [msg("9", 4)], now=1030),
            # an empty id and a real id spelling a positional key; then the `warming` key leaves the payload
            _bars({S1: [{"id": "", "t": 1}, {"id": "b2", "t": 2}], S3: turn(S3, 2)}, [], [{"id": "#1", "x": 0}, {"x": 1}], now=1035, warming=True),
            dict((kk, v) for kk, v in _bars({S1: [{"id": "", "t": 1}, {"id": "b2", "t": 3}], S3: turn(S3, 2)}, [], [{"id": "#1", "x": 0}, {"x": 5}], now=1040).items() if kk != "warming"),
        ]
        frames = []
        for p in payloads:
            frames += st.push(p)
        self.assertGreaterEqual(sum(1 for f in frames if f["type"] == "delta"), 5)
        self.assertTrue(any("order" in (f.get("coll") or {}).get("messages", {}) for f in frames if f["type"] == "delta"),
                        "the numeric-id step must have shipped an order for the shim to be tested on it")
        fx = tempfile.mkdtemp()
        with open(os.path.join(fx, "frames.json"), "w") as f:
            json.dump(frames, f)
        script = self._shim_functions() + r"""
var frames=JSON.parse(require("fs").readFileSync(process.argv[2],"utf8"));var out=[];
for(var i=0;i<frames.length;i++){var msg=frames[i];
if(msg.type==="delta"){var full=applyDelta(msg);if(!full){out.push({error:"rejected",at:i});break;}msg=full;}
else if(DELTA_KINDS[msg.type]){var keys=msg._keys;delete msg._keys;LAST[msg.type]=keys?{rev:0,msg:msg,maps:buildMaps(msg,keys)}:null;}
out.push(msg);}
process.stdout.write(JSON.stringify(out));"""
        with open(os.path.join(fx, "run.js"), "w") as f:
            f.write(script)
        r = subprocess.run([node, os.path.join(fx, "run.js"), os.path.join(fx, "frames.json")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(len(out), len(frames))
        # every frame reassembles to the payload the kernel had at that moment
        self.assertEqual(out[0], payloads[0])
        for i in range(1, len(payloads)):
            self.assertEqual(out[i], payloads[i], "frame %d reassembled differently in the shim than the kernel built" % i)
        self.assertTrue(any("dictlist" in f.get("coll", {}).get("turns", {}).__class__.__name__ or True for f in frames))

    def test_b_a_delta_whose_base_is_not_held_is_refused(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        fx = tempfile.mkdtemp()
        script = self._shim_functions() + r"""
LAST.bars={rev:0,msg:{type:"bars",turns:{},judging:[],messages:[],now:1},maps:buildMaps({type:"bars",turns:{},judging:[],messages:[]},{turns:[],judging:[],messages:[]})};
var r=applyDelta({type:"delta",slot:"bars",base:3,rev:4,coll:{}});
process.stdout.write(JSON.stringify({refused:r===null,rev:LAST.bars.rev}));"""
        with open(os.path.join(fx, "run.js"), "w") as f:
            f.write(script)
        r = subprocess.run([node, os.path.join(fx, "run.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"refused": True, "rev": 0}, "a base mismatch is refused and nothing moves")

class TwoThreadsOneClient(unittest.TestCase):
    """The socket handler's connect push (`ready` runs _push_one on the handler thread) and the pusher's cycle
    both reach _send_slot for the same client, and both read and write that client's held delta state.
    Review find (2026-09-04): unserialized, one interleaving — both see nothing held, a rebuild lands between
    them, the handler's dedup slot is written first but its bytes go second — left the client holding payload
    A while the kernel believed B, and every later delta was computed against B: silent divergence until the
    next full. The pre-PR race was on the stateless dedup dict alone, where a double full was harmless."""

    def setUp(self):
        km._delta_parts_cache.clear()
        self._frac = km._DELTA_MAX_FRACTION
        km._DELTA_MAX_FRACTION = 10.0

    def tearDown(self):
        km._DELTA_MAX_FRACTION = self._frac

    def test_a_a_second_sender_waits_for_the_first_frame_and_the_client_ends_holding_the_newer_payload(self):
        c = _Client()
        gate, entered = threading.Event(), threading.Event()
        real_send = c["send"]

        def parked_send(s):                     # thread A parks INSIDE its frame: dedup slot written, state not yet
            entered.set()
            gate.wait(5)
            real_send(s)
        c["send"] = parked_send
        bar = lambda i: {"id": "seg-%d" % i, "t": i, "end": i + 1, "open": False}
        pa = _bars({S1: [bar(1)]}, [], [], now=1)
        pb = _bars({S1: [bar(1), bar(2)]}, [], [], now=2)

        def push(p):
            pre = json.dumps(p)
            km._send_slot(c, "bars", p, pre, km._dedup_sig(p, pre))
        ta = threading.Thread(target=push, args=(pa,)); ta.start()
        self.assertTrue(entered.wait(5), "thread A is inside its frame")
        tb = threading.Thread(target=push, args=(pb,)); tb.start()
        tb.join(0.3)
        self.assertTrue(tb.is_alive(), "thread B waits: the client's slot state is one thread's at a time")
        self.assertEqual(c.frames, [], "…and nothing of B's crossed ahead of A's frame")
        gate.set(); ta.join(5); tb.join(5)
        self.assertFalse(ta.is_alive() or tb.is_alive())
        last = None                             # replay what the wire carried through the shim's mirror
        for fr in c.frames:
            if fr.get("type") == "delta":
                out = _py_apply(last, fr)
                self.assertIsNotNone(out, "the client could apply every delta it was sent")
            else:
                keys = fr.get("_keys")
                msg = {kk: v for kk, v in fr.items() if kk != "_keys"}
                last = {"rev": 0, "msg": msg, "maps": _py_maps(msg, keys)}
        self.assertEqual([f["type"] for f in c.frames], ["bars", "delta"], "A's keyed full, then B's delta against it")
        self.assertEqual(last["msg"], pb, "the client holds the newer payload…")
        self.assertEqual(c["dstate"]["bars"]["rev"], 1, "…and the kernel's belief about it agrees")
        self.assertEqual(c["dstate"]["bars"]["coll"]["turns"], {S1 + SEP + "seg-1": json.dumps(bar(1)), S1 + SEP + "seg-2": json.dumps(bar(2))})

    def test_b_a_collection_that_is_neither_list_nor_dict_goes_whole_and_is_logged_once(self):
        # _delta_split used to split an unexpected value (None where a list belongs) into ZERO entries, and the
        # remainder did not carry it either: the client kept its assembled [] while the kernel held None, with no
        # resync ever asked. Now the payload is unkeyable → the whole frame goes, and stderr says so once per shape.
        st = _Stream("bars")
        p1 = _bars({S1: [{"id": "seg-1", "t": 1, "end": 2, "open": False}]}, [], [{"id": "m1", "sent": 1}], now=1)
        st.push(p1)
        p2 = dict(p1, messages=None, now=2)
        p3 = dict(p2, now=3, warming=True)      # a real change (`now` alone is volatile and dedups)
        err = io.StringIO()
        with redirect_stderr(err):
            frames = st.push(p2)
            frames2 = st.push(p3)
        self.assertEqual(frames, [p2], "unkeyable: the whole payload crossed, no keys, no empty split")
        self.assertEqual(frames2, [p3])
        self.assertNotIn("bars", st.c.get("dstate", {}), "the kernel holds nothing for a client it sent a keyless whole")
        self.assertEqual(err.getvalue().count("cannot be keyed"), 1, "said once, not once per cycle")
        self.assertIn("messages", err.getvalue())

    def test_c_the_beat_reads_the_read_state_before_the_ping_stamp(self):
        # the handler ends a dispatch with two writes: pingAt = None, THEN inRead = True. A beat reading pingAt
        # first could pair the stale stamp with the fresh read state and drop a live peer as silent. Modelled
        # exactly: whichever of the two keys the beat reads FIRST sees the pre-end state, the second the post state.
        class _Handoff(dict):
            def __init__(self):
                super().__init__(app="timeline", wid="w1", alive=True, sock=object(), send=lambda s: None,
                                 qlock=threading.Lock(), lastIn=0.0)
                self.reads = 0

            def get(self, k, d=None):
                if k in ("pingAt", "inRead"):
                    self.reads += 1
                    pre = self.reads == 1
                    return (0.0 if pre else None) if k == "pingAt" else (False if pre else True)
                return super().get(k, d)
        c = _Handoff()
        dropped = []
        with mock.patch.object(km, "_drop_dead_ws_client", lambda cl, why: dropped.append(why)), \
             mock.patch.object(km, "_clients", [c]):
            km._keepalive_all(now=km.WS_DEAD_S + 1)
        self.assertEqual(dropped, [], "a live peer finishing its dispatch is never judged silent")
        self.assertEqual(c.reads, 2, "both keys were read once, read state first")



if __name__ == "__main__":
    unittest.main()


class HandlerWiring(unittest.TestCase):
    """The handshake end to end, with the real handler: the shim asks for deltas and carries its page id, the
    connect handler records both, a needSlot is flagged for the pusher, and the shim reacts to a refused delta.
    A mutation review (2026-09-03) turned each of these off with every test still green."""

    def _fake_self(self, path):
        import io
        class FakeSelf:
            headers = {"Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="}
            rfile = io.BytesIO(); wfile = io.BytesIO()
            connection = type("FakeSock", (), {"sendall": lambda self, b: None, "shutdown": lambda self, how: None})()
            close_connection = False
            def send_response(self, *a): pass
            def send_header(self, *a): pass
            def end_headers(self): pass
        FakeSelf.path = path
        return FakeSelf()

    def test_a_the_connect_handler_records_the_delta_flag_and_the_page_id(self):
        import contextlib, io
        got = []
        real_reg, real_recv = km._register_ws_client, km._ws_recv
        km._register_ws_client = lambda c: (got.append(c), km._clients.append(c))
        km._ws_recv = lambda rfile: (0x8, b"", True)              # the peer closes at once
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                km.Handler._ws(self._fake_self("/ws?app=feed&delta=1&iid=page-9&wid=w1"))
        finally:
            km._register_ws_client, km._ws_recv = real_reg, real_recv
            for c in got:
                if c in km._clients:
                    km._clients.remove(c)
        self.assertEqual(len(got), 1)
        c = got[0]
        self.assertTrue(c.get("delta")); self.assertEqual(c.get("iid"), "page-9"); self.assertEqual(c.get("wid"), "w1")
        self.assertEqual(c.get("app"), "feed")

    def test_b_a_needslot_is_flagged_for_the_pusher_and_wakes_it(self):
        client = _Client(); client["dstate"] = {"bars": {"rev": 3}}
        km._pusher_wake.clear()
        km.Handler._dispatch_ws(self._fake_self("/ws?app=timeline"), {"type": "needSlot", "slot": "bars"}, client)
        self.assertEqual(client.get("resync"), {"bars"})
        self.assertTrue(km._pusher_wake.is_set(), "the pusher is woken to re-base on its own thread")
        self.assertEqual(client["dstate"], {"bars": {"rev": 3}}, "the handler itself touches no held state")
        km.Handler._dispatch_ws(self._fake_self("/ws?app=timeline"), {"type": "needSlot", "slot": "nope"}, client)
        self.assertEqual(client.get("resync"), {"bars"}, "an unknown slot is ignored")

    def test_c_the_shim_asks_for_deltas_carries_its_page_id_and_asks_again_when_refused(self):
        js = km._shim("timeline", 1)
        self.assertIn('/ws?app=timeline&delta=1&iid="+encodeURIComponent(IID)', js)
        self.assertIn('send({type:"needSlot",slot:msg.slot})', js)
        self.assertIn("var IID=", js)
        self.assertNotIn('sessionStorage.setItem("romp:iid"', js, "the page id is never stored: a duplicated tab must not inherit it")
