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
from importlib.machinery import SourceFileLoader

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


def _py_apply(last, d):
    """A faithful Python mirror of the shim's applyDelta/buildMaps/assemble — the reference the JS is held to."""
    kinds = {"bars": {"turns": "dict", "judging": "bysid", "messages": "byid"}, "feed": {"asks": "byid"}}
    if last is None or last["rev"] != d["base"]:
        return None
    m = dict(last["msg"])
    for k, v in (d.get("rest") or {}).items():
        m[k] = v
    for name, c in (d.get("coll") or {}).items():
        kind = kinds[d["slot"]].get(name)
        mp = last["maps"].get(name) or {"order": [], "items": {}}
        items = dict(mp["items"]); order = list(mp["order"])
        for kk in c.get("del") or []:
            items.pop(kk, None)
        for kk, v in (c.get("set") or {}).items():
            if kk not in items:
                order.append(kk)
            items[kk] = v
        order = list(c["order"]) if c.get("order") else [kk for kk in order if kk in items]
        last["maps"][name] = {"order": order, "items": items}
        if kind == "dict":
            m[name] = {kk: items[kk] for kk in order if kk in items}
        elif kind == "bysid":
            m[name] = [it for kk in order if kk in items for it in items[kk]]
        else:
            m[name] = [items[kk] for kk in order if kk in items]
    last["rev"] = d["rev"]; last["msg"] = m
    return m


def _py_maps(msg):
    kinds = {"bars": {"turns": "dict", "judging": "bysid", "messages": "byid"}, "feed": {"asks": "byid"}}
    maps = {}
    for name, kind in kinds[msg["type"]].items():
        v = msg.get(name); order, items = [], {}
        if kind == "dict":
            for kk, vv in (v or {}).items():
                order.append(kk); items[kk] = vv
        elif kind == "byid":
            for it in v or []:
                kk = str(it.get("id")) if isinstance(it, dict) and it.get("id") is not None else None
                if kk is None or kk in items:
                    kk = "#%d" % len(order)
                order.append(kk); items[kk] = it
        else:
            for it in v or []:
                kk = str(it.get("sid")) if isinstance(it, dict) and it.get("sid") is not None else "#"
                if kk not in items:
                    order.append(kk); items[kk] = []
                items[kk].append(it)
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
                self.last = {"rev": 0, "msg": fr, "maps": _py_maps(fr)}
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
        return {"segs": [[self.t0 - 60 * i, self.t0 - 60 * i + 30, "seg-%s-%d" % (sid[-1], i)] for i in range(n)]}

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
        self.assertEqual(set(d["coll"]["turns"]["set"]), {S2})
        self.assertNotIn("del", d["coll"]["turns"]); self.assertNotIn("order", d["coll"]["turns"])
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
        self.assertEqual(d["coll"]["turns"].get("del"), [S2])
        self.assertEqual(set(d["coll"]["turns"]["set"]), {S3})
        self.assertEqual(set(d["coll"]["judging"]["set"]), {S3}, "a grouped collection replaces the group that changed")
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
        # the shim could not apply a delta → the kernel forgets what the client holds
        st.c["dstate"].pop("bars"); st.c["sent"].pop(("timelinebars",), None)
        p3 = _bars({S1: self._turn(S1, 3)}, [], [], now=1010)
        self.assertEqual([f["type"] for f in st.push(p3)], ["bars"])
        self.assertEqual(st.held, p3)


class FeedDeltas(unittest.TestCase):
    def setUp(self):
        km._delta_parts_cache.clear()
        self._frac = km._DELTA_MAX_FRACTION
        km._DELTA_MAX_FRACTION = 10.0

    def tearDown(self):
        km._DELTA_MAX_FRACTION = self._frac

    def _ask(self, i, column="working", text="do the thing"):
        return {"id": "g%d" % i, "sid": S1, "column": column, "text": text, "color": None, "trail": [1, 2, 3]}

    def test_a_only_the_card_that_moved_crosses(self):
        st = _Stream("feed")
        p1 = _feed([self._ask(1), self._ask(2), self._ask(3)])
        self.assertEqual([f["type"] for f in st.push(p1)], ["feed"])
        p2 = _feed([self._ask(1), self._ask(2, column="done"), self._ask(3)], now=1005)
        frames = st.push(p2)
        self.assertEqual(frames[0]["type"], "delta")
        self.assertEqual(set(frames[0]["coll"]["asks"]["set"]), {"g2"})
        self.assertEqual(st.held, p2)

    def test_b_the_remainder_rides_only_when_it_changed(self):
        st = _Stream("feed")
        st.push(_feed([self._ask(1)]))
        p2 = _feed([self._ask(1)], now=1005, showDismissed=True)
        fr = st.push(p2)
        self.assertEqual(fr[0]["type"], "delta")
        self.assertIn("showDismissed", fr[0]["rest"]); self.assertEqual(fr[0]["coll"], {})
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
        def turn(sid, n): return {"segs": [[t0 - 60 * i, t0 - 60 * i + 30, "s%d" % i] for i in range(n)]}
        payloads = [
            _bars({S1: turn(S1, 2), S2: turn(S2, 1)}, [{"sid": S1, "judge": "closer", "t": 1, "t1": 2}], [{"id": "m1", "to": "x"}]),
            _bars({S1: turn(S1, 3), S2: turn(S2, 1)}, [{"sid": S1, "judge": "closer", "t": 1, "t1": 2}], [{"id": "m1", "to": "x"}], now=1005),
            _bars({S1: turn(S1, 3), S3: turn(S3, 1)}, [{"sid": S3, "judge": "planner", "t": 3, "t1": 4}, {"sid": S1, "judge": "closer", "t": 1, "t1": 2}],
                  [{"id": "m2", "to": "y"}, {"id": "m1", "to": "x"}], now=1010, warming=True),
            _bars({S1: turn(S1, 3), S3: turn(S3, 1)}, [], [{"id": "m2", "to": "y"}], now=1015),
        ]
        frames = []
        for p in payloads:
            frames += st.push(p)
        self.assertGreaterEqual(sum(1 for f in frames if f["type"] == "delta"), 3)
        fx = tempfile.mkdtemp()
        with open(os.path.join(fx, "frames.json"), "w") as f:
            json.dump(frames, f)
        script = self._shim_functions() + r"""
var frames=JSON.parse(require("fs").readFileSync(process.argv[2],"utf8"));var out=[];
for(var i=0;i<frames.length;i++){var msg=frames[i];
if(msg.type==="delta"){var full=applyDelta(msg);if(!full){out.push({error:"rejected",at:i});break;}msg=full;}
else if(DELTA_KINDS[msg.type]){LAST[msg.type]={rev:0,msg:msg,maps:buildMaps(msg)};}
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

    def test_b_a_delta_whose_base_is_not_held_is_refused(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        fx = tempfile.mkdtemp()
        script = self._shim_functions() + r"""
LAST.bars={rev:0,msg:{type:"bars",turns:{},judging:[],messages:[],now:1},maps:buildMaps({type:"bars",turns:{},judging:[],messages:[]})};
var r=applyDelta({type:"delta",slot:"bars",base:3,rev:4,coll:{}});
process.stdout.write(JSON.stringify({refused:r===null,rev:LAST.bars.rev}));"""
        with open(os.path.join(fx, "run.js"), "w") as f:
            f.write(script)
        r = subprocess.run([node, os.path.join(fx, "run.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"refused": True, "rev": 0}, "a base mismatch is refused and nothing moves")


if __name__ == "__main__":
    unittest.main()
