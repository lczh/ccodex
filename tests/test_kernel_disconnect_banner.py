"""Connection-status reporting (the user 2026-06-27): a real network drop used to blind-reload each pane
iframe into a dead page, so the dashboard silently froze (the timeline "stopped moving") with no explanation.
Now each pane reports its WebSocket state to the shell, and the panes RECONNECT (retry) instead of
blind-reloading — reloading to resync only once the socket is actually back.

The shell surface changed on 2026-07-27: the fixed top "Disconnected — reconnecting…" banner is gone —
connection drops now log entries in the shell's NOTIFICATION CENTER (the bell in the bottom bar, red while
anything is unread or a visible pane is down), whose behavioral tests live in test_error_center.py. The
source pins here cover the pane shim (unchanged) and the shell's wiring of the center."""
import inspect
import os
import unittest
from importlib.machinery import SourceFileLoader
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()


class DisconnectBanner(unittest.TestCase):
    def test_pane_shim_reports_state_and_reconnects(self):
        js = km._shim("chat")
        # reports up/down to the shell
        self.assertIn('postMessage({romp:"wsState",app:APP,state:s}', js)
        self.assertIn('ws.onopen=function(){lastRecv=Date.now();netState("up");', js)   # lastRecv stamp → heartbeat watchdog
        self.assertIn('ws.onclose=function(){netState("down");', js)   # reconnects on close (wsdown loader + retry follow)
        self.assertIn("setTimeout(connect,1500);", js)
        self.assertIn("ws.onerror=function(){try{ws.close();}catch(e){}};", js)
        # a RECONNECT no longer silently reloads (the user 2026-07-05): it PROMPTS via raiseStale, and the fresh
        # socket resyncs live. The old auto-reload-on-reopen is gone.
        # the reconnect ALSO fires romp:wsup, which is what takes the pane loader back down
        # (test_pane_loader_reconnect.py owns that half)
        # …and ARMS the retire (freshPending) so the prompt clears itself when the resync frame lands
        # (the user 2026-08-01) — see test_the_connection_prompt_retires_when_the_resync_lands
        self.assertIn('if(wasReconn){armStale("reconnect");freshPending=true;'
                      'try{window.dispatchEvent(new Event("romp:wsup"));}catch(e){}}', js)
        self.assertNotIn("if(everConnected){location.reload();return;}", js,
                         "the silent auto-reload-on-reconnect is replaced by a reload PROMPT")
        self.assertNotIn("ws.onclose=function(){setTimeout(function(){location.reload();},1500);};", js,
                         "the old blind-reload-on-close is gone")

    def test_shim_prompts_reload_on_reconnect_and_stale_foreground(self):
        js = km._shim("feed")
        # raiseStale routes to the shell's #rstale banner when embedded (a pane iframe), else self-injects.
        self.assertIn('window.parent.postMessage({romp:"wsStale"}', js, "embedded pane hands off to the shell banner")
        self.assertIn("function selfStale()", js, "standalone page (no shell) gets its own reload bar")
        self.assertIn("romp-stale-self", js)
        # visibility fast-path: a foregrounded tab whose socket is dead/quiet ARMS the prompt at once (not
        # after the throttled 5s watchdog), and forces a reconnect to resync — which disarms it again if the
        # resync lands inside the arming window (the user 2026-08-01).
        self.assertIn('document.addEventListener("visibilitychange"', js)
        self.assertIn('Date.now()-lastRecv>STALE_MS){armStale("foreground");freshPending=true;', js)

    def test_a_hidden_pane_never_raises_the_stale_banner(self):
        # the user 2026-08-15, on the phone: the mobile shell shows ONE pane, hiding the rest with
        # display:none; iOS throttles the hidden iframes' JS, so each hidden pane's watchdog kept
        # force-closing its own healthy socket and re-raising the banner every ~45s over a dashboard
        # that was visibly working. A display:none iframe has a ZERO viewport — raiseStale checks that
        # at raise time (no event exists for a CSS display flip) and stays silent while hidden; a pane
        # shown while genuinely stale re-raises within one watchdog tick, now visible.
        js = km._shim("feed")
        self.assertIn("function paneHidden(){try{return window.parent!==window"
                      "&&(window.innerWidth===0||window.innerHeight===0);}", js)
        self.assertIn('function raiseStale(why){if(paneHidden()){staleDiag("stale-suppressed-hidden",why);return;}', js,
                      "the visibility gate is at RAISE time, so hidden panes reconnect silently")

    def test_every_stale_raise_leaves_a_breadcrumb_naming_pane_and_path(self):
        # the user 2026-08-15: the flapping-banner repro was Chrome-on-Android after an iOS-shaped
        # diagnosis — the next report must carry recorded evidence. Every raise (and every hidden-pane
        # suppression, and every watchdog force-close) posts a clientDiag with the pane, the arming
        # path (reconnect/foreground), the socket state and the quiet gap; send() queues while the
        # socket is down, so the breadcrumb survives the drop it describes.
        js = km._shim("feed")
        self.assertIn('send({type:"clientDiag",surface:"pane-shim",what:what,', js)
        self.assertIn('staleDiag("stale-raise",why);', js)
        self.assertIn('staleDiag("stale-suppressed-hidden",why);', js)
        self.assertIn('staleDiag("watchdog-close","quiet");', js)
        self.assertIn('armStale("reconnect");', js)
        self.assertIn('armStale("foreground");', js)

    def test_shim_reconnect_loop_cannot_die(self):
        # The retry chain used to hang entirely off onclose, and the watchdog only ever closed OPEN
        # sockets — so an attempt the browser held in CONNECTING (Firefox delays re-admitting a
        # recently-failed endpoint after a kernel restart) had NOTHING driving it forward, and the
        # "Disconnected — reconnecting…" banner sat until a manual refresh (the user 2026-07-21).
        js = km._shim("chat")
        # connect() is idempotent (one live attempt at a time) and stamps its start time
        self.assertIn("function connect(){if(ws&&(ws.readyState===0||ws.readyState===1))return;", js)
        self.assertIn("connT=Date.now();", js)
        # the watchdog handles EVERY socket state: half-open OPEN, stuck CONNECTING, and lost-timer CLOSED
        self.assertIn('if(ws.readyState===1){if(everConnected&&Date.now()-lastRecv>STALE_MS){staleDiag("watchdog-close","quiet");try{ws.close();}catch(e){}}return;}', js)
        self.assertIn("if(ws.readyState===0&&Date.now()-connT>15000){try{ws.close();}catch(e){}return;}", js)
        self.assertIn("if(ws.readyState===3&&Date.now()-connT>8000){connect();}", js)
        # the foregrounding fast-path tears down a stuck CONNECTING socket too, and re-dials a closed one
        self.assertIn("if(ws&&ws.readyState<=1)ws.close();", js)
        self.assertIn("if(!ws||ws.readyState===3)connect();", js)

    def test_error_popover_has_no_reload_button(self):
        # the popover briefly inherited the old offline banner's Reload button; the user called it
        # redundant next to the rail's own restart control and a plain browser refresh (2026-07-27),
        # so it is gone — and nothing in the popover reloads the page on its own.
        land = inspect.getsource(km._landing)
        self.assertNotIn("rerr-reload", land)
        self.assertNotIn("location.reload", km._LANDING_ERRS_JS)

    def test_shell_rstale_banner_shows_on_ws_stale_message(self):
        # the #rstale reload banner (formerly build-drift only) now ALSO shows on a pane's wsStale post, with a
        # connection-specific message, latched so the /version poll can't clear it out from under the user.
        self.assertIn("m.romp==='wsStale'", km._STALE_JS)
        self.assertIn("connStale=true", km._STALE_JS)
        self.assertIn("served<=loaded&&!connStale", km._STALE_JS, "the version poll must not hide a live-conn prompt")
        self.assertIn("connStale=false", km._STALE_JS, "Dismiss clears the latch")

    def test_shell_banner_words_a_build_raise_as_a_build(self):
        # a pane's build-drift raise rides the same wsStale channel tagged build:1 → the shell shows
        # BUILDMSG (not the connection wording), and latches it so its own /version poll — whose token
        # may be current — can't clear the prompt out from under the stale pane (the user 2026-07-13)
        self.assertIn("if(m.build){buildStale=true;show(BUILDMSG);}else{connStale=true;show(CONNMSG);}", km._STALE_JS)
        self.assertIn("!connStale&&!buildStale", km._STALE_JS, "the poll's clear respects both latches")
        self.assertIn("connStale=false;buildStale=false;", km._STALE_JS, "Dismiss clears both")

    def test_the_connection_prompt_retires_when_the_resync_lands(self):
        """The prompt must go away on its own once what it warns about is over (the user 2026-08-01).

        It popped on nearly every dashboard open — a pane whose socket dropped-and-reconnected raises it —
        and then sat there offering a reload for staleness the kernel's connect-time push had already
        healed in the background. So the retire keys on that push: the FIRST non-keepalive frame after a
        reconnect IS the resync, and it clears the prompt. An event, not a timer, and not a silent
        auto-reload — the reload prompt still stands whenever the resync genuinely never arrives."""
        js = km._shim("feed", 7777)
        self.assertIn("freshPending=true", js, "a reconnect arms the retire")
        # …and the prompt is ARMED, not shown (the user 2026-08-01): raising it at once made it flash up
        # and straight back down on nearly every dashboard open, since the resync lands within a beat.
        self.assertIn("staleTimer=setTimeout(function(){staleTimer=0;raiseStale(why);},1000)", js,
                      "a one-second arming window, not an immediate raise")
        self.assertIn('if(wasReconn){armStale("reconnect");', js, "the reconnect ARMS it")
        self.assertNotIn("if(wasReconn){raiseStale();", js, "…and never raises it outright")
        self.assertIn("if(staleTimer){clearTimeout(staleTimer);staleTimer=0;}", js,
                      "a resync inside the window disarms it, so it never appears at all")
        self.assertIn("if(freshPending){freshPending=false;clearStale();}", js,
                      "the first real frame after it fires the retire")
        # keepalives must NOT count as a resync — the ka branch returns before the retire line
        self.assertLess(js.index('msg.type==="ka"'), js.index("if(freshPending)"),
                        "the keepalive early-return sits ahead of the retire")
        self.assertIn('window.parent.postMessage({romp:"wsFresh"}', js, "embedded pane tells the shell")
        # …and the shell drops the connection prompt on it, while a BUILD prompt survives (a resync
        # delivers state, never new code — only a reload answers that one)
        self.assertIn("m.romp==='wsFresh'", km._STALE_JS)
        self.assertIn("connStale=false;if(buildStale)show(BUILDMSG);else box.classList.remove('show');",
                      km._STALE_JS)

    def test_the_foregrounded_tab_path_arms_the_same_way(self):
        # a tab foregrounded onto a dead socket forces a reconnect and used to prompt immediately; that
        # reconnect resyncs like any other, so it arms the same window and disarms on the same frame
        js = km._shim("chat", 7777)
        self.assertIn('Date.now()-lastRecv>STALE_MS){armStale("foreground");freshPending=true;', js)
        self.assertNotIn("STALE_MS){raiseStale();", js)

    def test_a_standalone_page_retires_only_its_connection_bar(self):
        # the shell-less case (feed/timeline opened directly) self-injects the same prompt; the retire must
        # remove ONLY the connection one, so a build-drift bar is never cleared by a mere resync
        js = km._shim("timeline", 7777)
        self.assertIn('b.dataset.kind=kind||"conn"', js, "the self-injected bar records which prompt it is")
        self.assertIn('selfBar("romp lost the live connection, so what you see may be stale.","conn")', js)
        self.assertIn('selfBar("A newer romp build is available.","build")', js)
        self.assertIn('if(b&&b.dataset.kind==="conn")b.remove();', js, "only the conn bar retires")

    def test_timeline_page_uses_the_shared_shim(self):
        # The timeline used to hand-roll its own WebSocket in _TIMELINE_BOOT (a copy of _shim's
        # connect/queue/watchdog) — which bypassed the federation manager, so remote hosts' lanes never
        # reached the pane. Now the page carries the SAME shim as every other pane (state reporting,
        # reconnect, reload-on-reopen all come from it) + the federation bundle; the boot only adapts
        # window "message" events to panel.update/applyBars and posts actions via acquireVsCodeApi.
        self.assertNotIn("new WebSocket", km._TIMELINE_BOOT, "the boot owns no socket of its own")
        page = km._timeline_page()
        self.assertIn('postMessage({romp:"wsState",app:APP,state:s}', page, "the shared shim reports state")
        self.assertIn('/ws?app=timeline', page, "the shim is instantiated for the timeline app")
        self.assertIn("/dist/federation.js", page, "the multi-kernel manager rides the page")
        self.assertIn('window.addEventListener("message"', km._TIMELINE_BOOT)
        self.assertIn("panel.update(m.data)", km._TIMELINE_BOOT)

    def test_shell_mounts_the_notification_center(self):
        # the shell listens for pane wsState posts and routes drops into the notification center — the
        # old fixed top banner is GONE (it got in the way, the user 2026-07-27)
        self.assertIn("var s=(m.state==='up')?'up':'down',prev=st[m.app];st[m.app]=s;", km._LANDING_ERRS_JS)
        land = inspect.getsource(km._landing)
        self.assertIn("id=rail-errs", land, "the bell sits in the bottom bar's action cluster")
        self.assertIn("id=rerr-back", land, "the popover backdrop is in the shell body")
        self.assertIn("_LANDING_ERRS_JS", land, "the center's script is injected into the shell")
        self.assertNotIn("id=romp-offline", land, "the top banner element is gone")
        self.assertNotIn("Disconnected — reconnecting…", land)

    def test_only_visible_panes_count(self):
        # a pane toggled OFF still holds a live socket (the Fleet pane is hidden by default, its iframe always
        # loaded), so a blip on a pane you can't even see must NOT log an error / redden the bell while the
        # chat pane you interact through is up (the user 2026-07-06). Gate on the pane-enabled body class
        # the toggle sets (po-chat/po-feed/po-timeline/po-fleet), and re-check when panes toggle.
        js = km._LANDING_ERRS_JS
        self.assertIn("function shown(k){return document.body.classList.contains('po-'+k);}", js)
        self.assertIn("if(st[k]==='down'&&shown(k))return true;", js, "a down pane counts only while visible")
        self.assertIn("shown(m.app)", js, "and only a visible pane's drop logs an entry")
        self.assertIn("window.addEventListener('romp-panes',paint)", js, "re-check the cue on pane toggle")
        # the pane toggle actually fires the romp-panes event this listens for, and both scripts ride the shell
        self.assertIn("new Event('romp-panes')", km._LANDING_COLLAPSE_JS)
        land = inspect.getsource(km._landing)
        self.assertIn("_LANDING_COLLAPSE_JS", land)
        self.assertIn("_LANDING_ERRS_JS", land)


if __name__ == "__main__":
    unittest.main()
