#!/usr/bin/env python3
"""Restart means the FLEET (the user 2026-07-29).

Restart used to restart this machine's kernel and nothing else, which is a half-truth once remotes are
attached: they kept running their old processes on their old code, and nothing said so. Restart now
covers every reachable kernel, syncs on the way wherever a clean fast-forward can be PROVEN in either
direction, and reports per host what it did and what it refused.

The refusals are the point. A diverged remote, a dirty tree on either side, a relationship this repo
cannot evaluate: skipped, with the reason named, never guessed at. The report is written to disk before
the local restart, because that restart takes the reporting process down with it.

Synthetic only — placeholder hosts/shas, no ssh, no restarts.
"""
import inspect
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = SourceFileLoader("romp_kernel_fleet", os.path.join(BIN, "romp-kernel")).load_module()

LOCAL_SHA = "a" * 40
REMOTE_SHA = "b" * 40


def row(**kw):
    r = {"host": "TESTHOST", "status": "up", "kernel_sha": "", "trust": "directed"}
    r.update(kw)
    return r


class Plan(unittest.TestCase):
    """_fleet_restart_plan is the whole decision, pure, so the report and these tests read the same rule."""

    def setUp(self):
        self._saved = {n: getattr(km, n) for n in
                       ("_remote_out_of_date", "_is_fast_forward", "_is_fast_pull", "_is_ask_pull",
                        "_behind_info", "_local_branch")}
        km._local_branch = lambda: "main"

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(km, n, v)

    def _stub(self, out_of_date=False, ff=False, pull=False, ask=False, behind=None, ahead=None):
        km._remote_out_of_date = lambda r: out_of_date
        km._is_fast_forward = lambda r: ff
        km._is_fast_pull = lambda r: pull
        km._is_ask_pull = lambda r: ask
        km._behind_info = lambda sha: {"behind": behind, "ahead": ahead}

    def test_a_disconnected_host_is_skipped_and_says_romp_is_still_dialing(self):
        self._stub()
        action, why = km._fleet_restart_plan(row(status="down"))
        self.assertEqual(action, "skip")
        self.assertIn("still dialing", why)

    def test_a_host_on_this_build_is_simply_restarted(self):
        self._stub(out_of_date=False)
        action, why = km._fleet_restart_plan(row())
        self.assertEqual(action, "restart")
        self.assertIn("already on this build", why)

    def test_a_host_strictly_behind_is_pushed_and_restarted(self):
        self._stub(out_of_date=True, ff=True)
        action, why = km._fleet_restart_plan(row(kernel_sha=REMOTE_SHA))
        self.assertEqual(action, "sync-push")
        self.assertIn("behind", why)

    def test_a_host_strictly_ahead_fast_forwards_THIS_machine_onto_it(self):
        # "make sure that both kernels have the latest code via fast forwarding from each other"
        self._stub(out_of_date=True, pull=True)
        action, why = km._fleet_restart_plan(row(kernel_sha=REMOTE_SHA, trust="trusted"))
        self.assertEqual(action, "sync-pull")
        self.assertIn("ahead", why)

    def test_a_pull_is_refused_when_this_checkout_is_not_on_main(self):
        self._stub(out_of_date=True, pull=True)
        km._local_branch = lambda: "some-feature"
        action, why = km._fleet_restart_plan(row(kernel_sha=REMOTE_SHA, trust="trusted"))
        self.assertEqual(action, "skip")
        self.assertIn("isn't on main", why)

    def test_a_nontrusted_ahead_host_is_never_pulled(self):
        self._stub(out_of_date=True, pull=True, behind=0, ahead=4)
        action, why = km._fleet_restart_plan(row(kernel_sha=REMOTE_SHA, trust="directed"))
        self.assertEqual(action, "skip")
        self.assertIn("not trusted", why)

    def test_a_diverged_host_is_skipped_and_the_reason_counts_its_commits(self):
        self._stub(out_of_date=True, behind=3, ahead=4)
        action, why = km._fleet_restart_plan(row(kernel_sha=REMOTE_SHA))
        self.assertEqual(action, "skip")
        self.assertIn("diverged", why)
        self.assertIn("4 commit", why, "it names what would be clobbered")
        self.assertIn("not clobbering", why)

    def test_an_unknown_relationship_is_skipped_rather_than_guessed(self):
        # the remote's sha isn't in this repo at all (updated from a third machine): None is not zero
        self._stub(out_of_date=True, behind=None, ahead=None)
        action, why = km._fleet_restart_plan(row(kernel_sha="c" * 40))
        self.assertEqual(action, "skip")
        self.assertIn("nothing can be proven safe", why)

    def test_a_checked_in_peer_is_ASKED_because_there_is_no_ssh_route_here(self):
        self._stub(out_of_date=True, ask=True)
        action, _ = km._fleet_restart_plan(row(checkin_peer=True, kernel_sha=REMOTE_SHA,
                                                trust="trusted"))
        self.assertEqual(action, "ask")
        self._stub(out_of_date=True, ask=False)
        action, why = km._fleet_restart_plan(row(checkin_peer=True, kernel_sha=REMOTE_SHA,
                                                  trust="trusted"))
        self.assertEqual(action, "skip")
        self.assertIn("its own dashboard", why)


class ReportSurvivesTheRestart(unittest.TestCase):
    def test_the_run_writes_every_host_then_restarts_this_machine_last(self):
        src = inspect.getsource(km._fleet_restart_run)
        self.assertIn("_atomic_write(FLEET_REPORT", src)
        self.assertLess(src.index("_atomic_write(FLEET_REPORT"), src.index("_restart_this_kernel("),
                        "the report must be on disk BEFORE this process is taken down")
        self.assertIn("except Exception as e:", src)   # one bad host never strands the rest of the fleet

    def test_a_failing_host_lands_in_the_report_and_the_sweep_continues(self):
        saved = {n: getattr(km, n) for n in ("_fleet_restart_plan", "_restart_remote_kernel",
                                            "_restart_this_kernel", "_local_head", "_local_branch")}
        km._fleet_restart_plan = lambda r: ("restart", "already on this build")
        km._restart_remote_kernel = lambda h: (_ for _ in ()).throw(RuntimeError("ssh exploded"))
        km._restart_this_kernel = lambda reason="": None   # audits its reason since 2026-07-31
        km._local_head = lambda short=False: "abc1234"
        km._local_branch = lambda: "main"
        km._remotes.clear()
        km._remotes["web"] = row(host="web")
        km._remotes["api"] = row(host="api")
        try:
            km._fleet_restart_run()
            report = json.loads(km.FLEET_REPORT.read_text())
        finally:
            for n, v in saved.items():
                setattr(km, n, v)
            km._remotes.clear()
        hosts = sorted(x["host"] for x in report["rows"])
        self.assertEqual(hosts, ["api", "web"], "both hosts reported, neither stranded by the other")
        for x in report["rows"]:
            self.assertFalse(x["ok"])
            self.assertIn("ssh exploded", x["detail"])
        self.assertEqual(report["local"]["head"], "abc1234")

    def test_the_route_hands_the_report_back_and_the_page_shows_it_once(self):
        src = inspect.getsource(km.Handler)
        self.assertIn('if u.path == "/fleet-restart":', src)
        self.assertIn("FLEET_REPORT.read_text()", src)
        self.assertIn("threading.Thread(target=_fleet_restart_run, daemon=True).start()", src)
        # the page reads it back AFTER reloading and shows it exactly once (keyed on the report's stamp).
        # It rides the block that owns the Restart button itself, so the two can never drift apart.
        js = km._LANDING_SETTINGS_JS
        self.assertIn("window.__rompRestart", js, "the report lives with the button that causes it")
        self.assertIn("body:'{\"fleet\":false}'", js,
                      "the routine Restart button never opts into remote code import")
        self.assertIn('.get("fleet", False)', src,
                      "a bodyless/API restart is local-only")
        self.assertIn("fetch('/fleet-restart'", js)
        self.assertIn("romp:fleetSeen", js)
        self.assertIn("_fleetReport();", js, "and runs on load, after the restart brought the page back")

    def test_a_kernel_with_no_remotes_restarts_exactly_as_before(self):
        src = inspect.getsource(km.Handler)
        self.assertIn("if _fleet and _remotes:", src)
        self.assertIn("else:\n                    _restart_this_kernel(\"http /restart (local-only)\")", src)


class GlyphSaysTheFleetState(unittest.TestCase):
    """The rail's network glyph carries the whole verdict, so drift/disconnection reads without opening
    the panel (the user 2026-07-29): top node = this machine, the two below = the remotes."""

    def setUp(self):
        self.js = km._LANDING_REMOTES_JS

    def test_severity_is_ranked_worst_first_so_a_sick_host_cannot_hide(self):
        self.assertIn("var SEVRANK={warn:0,wait:1,ok:2};", self.js)
        self.assertIn("return [sev[0],sev.length>1?sev[1]:sev[0]];", self.js,
                      "one attached host colours BOTH nodes — its state is the fleet's")

    def test_connected_and_in_sync_is_accent_drift_is_red(self):
        self.assertIn("if(t.status==='up')return t.outOfDate?'warn':'ok';", self.js)

    def test_a_fresh_drop_is_grey_but_a_persistent_one_needs_you(self):
        # romp is retrying either way; the difference is whether it is still plausibly a blip
        self.assertIn("if((t.fails||0)>=12)return 'warn';", self.js)
        self.assertIn("return 'wait';", self.js)

    def test_nothing_attached_leaves_the_glyph_exactly_as_it_was(self):
        self.assertIn("if(!ts.length)return null;", self.js)

    def test_both_glyphs_carry_paintable_nodes_and_the_colours_are_defined(self):
        html = km._landing()
        self.assertEqual(html.count("class=rn-me"), 2, "the rail glyph and the mobile one")
        self.assertEqual(html.count("class=rn-a"), 2)
        self.assertEqual(html.count("class=rn-b"), 2)
        self.assertIn(".rn-ok{fill:var(--accent)}", html)
        self.assertIn(".rn-wait{fill:#8a8a8a}", html)
        self.assertIn(".rn-warn{fill:#e5484d}", html)
        self.assertIn("paintNodes(icon,nodes)", html)

    def test_the_tooltip_says_the_verdict_in_words(self):
        # the colour is the glance; the words behind it are one hover away (progressive disclosure)
        self.assertIn("' attention'", self.js)
        self.assertIn("disconnected (romp is retrying)", self.js)
        self.assertIn("all hosts connected and in sync", self.js)


if __name__ == "__main__":
    unittest.main()
