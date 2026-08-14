// Every attached host's row expands into THAT machine's own attached-host list with WORKING
// controls (the user 2026-08-11: manage what's connected to what from one dashboard — the concrete
// case, managing a far box's attach of a third machine from the laptop). The list comes from
// /tunnels/of — the machine's own /tunnels read over your tunnel + its serve token — fetched on
// EXPAND and after a forwarded action, never in the 3s poll (the /tunnels/pairs rule). Actions
// post the normal routes with {via}; the kernel relays them to the machine that owns the tunnel
// (_via_forward) and its answer comes back tagged. Keyed expand state and a via|host
// pending-trust latch survive re-renders (progressive disclosure + pending-confirm rules).
//
// Pinned in BOTH copies (web _LANDING_REMOTES_JS in kernel.py, VS Code strip.ts) — the
// net-trust-pending discipline: the two popovers must stay in step.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");
const STRIP = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.ts"), "utf8");
const STRIPCSS = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.css"), "utf8");

test("kernel: /tunnels/of reads ONE host's own list; every action route forwards {via}", () => {
  assert.match(KERNEL, /def tunnels_of\(host\):/);
  assert.match(KERNEL, /def _via_forward\(body, path\):/);
  assert.ok(KERNEL.includes('if p == "/tunnels/of":'), "the GET route serves the expand");
  for (const route of ["/tunnels/detach", "/tunnels/checkin", "/tunnels/autoupdate", "/tunnels/forget",
                       "/tunnels/trust", "/tunnels/update", "/tunnels/pull", "/tunnels/askpull",
                       "/tunnels/start"]) {
    assert.ok(KERNEL.includes(`_via_forward(body, "${route}")`), `${route} must consult _via_forward`);
  }
  // slow forwarded actions (push/boot over the via machine's ssh) get the long leash
  assert.match(KERNEL, /_VIA_TIMEOUT = \{"\/tunnels\/update": 180/);
});

test("web popover: rows expand into that machine's connections, fetched on demand", () => {
  assert.match(KERNEL, /var _openSub=\{\},_subInfo=\{\},_subBusy=\{\},_pendSub=\{\};/,
    "keyed expand + sub-list state outlive render()");
  assert.match(KERNEL, /'\/tunnels\/of\?host='\+encodeURIComponent\(h\),\{cache:'no-store'\}/,
    "the expand fetches /tunnels/of — never folded into the 3s poll");
  assert.match(KERNEL, /function subBlock\(via\)/);
  assert.match(KERNEL, /if\(t\.status==='up'&&_openSub\[t\.host\]\)item\.appendChild\(subBlock\(t\.host\)\);/,
    "the block renders under the host's own row");
  // loading and a failed read SAY so, with Retry — never a silent blank (the inline JS lives in a
  // Python string, so its \uXXXX escapes are literally TWO backslashes in the file — hence \\\\)
  assert.match(KERNEL, /Reading '\+via\+'\\\\u2019s connections/);
  assert.match(KERNEL, /Couldn\\\\u2019t read '\+via\+'\\\\u2019s connections/);
  assert.match(KERNEL, /setAttribute\('data-xr',via\)/,
    "Retry is built as a DOM node; the far host's error never becomes innerHTML");
});

test("web popover: sub-row actions ride the normal routes with {via}, refusals alert loudly", () => {
  assert.match(KERNEL, /body:JSON\.stringify\(\{host:h,via:via\}\)/, "one vact shape for all five actions");
  for (const attr of ["data-vu", "data-va", "data-vp", "data-vs", "data-vh"]) {
    assert.ok(KERNEL.includes(`button[${attr}]`), `${attr} action is wired`);
  }
  assert.match(KERNEL, /host:h,via:via,trust:s\.value/, "sub trust writes on the via machine");
  assert.match(KERNEL, /failed to reach the kernel\.'\);\}\)\;\}/,
    "a dead kernel is named, not swallowed");
  // the sub trust select wears the same class the defer-while-engaged latch keys on
  assert.match(KERNEL, /class=\\"rnet-trust'\+\(spd\?' rnet-applying':''\)/);
});

test("VS Code popover: the same expand, same on-demand read, same via actions", () => {
  assert.match(STRIP, /const openSub = new Set<string>\(\);/);
  assert.match(STRIP, /const pendingSub = new Map<string, string>\(\);/);
  assert.match(STRIP, /fetch\(kernelUrl\("\/tunnels\/of\?host=" \+ encodeURIComponent\(host\)\), \{ cache: "no-store" \}\)/);
  assert.match(STRIP, /function renderSub\(via: string\)/);
  assert.match(STRIP, /function subRow\(via: string, s: any\)/);
  assert.match(STRIP, /if \(t\.status === "up" && openSub\.has\(t\.host\)\) renderSub\(t\.host\);/);
  // act() grew the optional via and posts it; a forwarded refusal is named on the button itself
  assert.match(STRIP, /function act\(path: string, host: string, b: HTMLButtonElement, busyText: string, via\?: string\)/);
  assert.match(STRIP, /JSON\.stringify\(via \? \{ host, via \} : \{ host \}\)/);
  assert.match(STRIP, /b\.classList\.add\("sn-actfail"\);/);
  // the five forwarded actions, riding the same gating fields the via machine computed
  assert.match(STRIP, /act\("\/tunnels\/update", s\.host, u, "Pushing…", via\)/);
  assert.match(STRIP, /act\("\/tunnels\/askpull", s\.host, a, "Asking…", via\)/);
  assert.match(STRIP, /act\("\/tunnels\/pull", s\.host, pl, "Pulling…", via\)/);
  assert.match(STRIP, /act\("\/tunnels\/start", s\.host, st, "Starting…", via\)/);
  assert.match(STRIP, /act\("\/tunnels\/detach", s\.host, dt, "…", via\)/);
  assert.match(STRIP, /body: JSON\.stringify\(\{ via, host: s\.host, trust: sel\.value \}\)/);
});

test("both copies indent the sub-rows and keep the toggle compact by default", () => {
  assert.match(KERNEL, /\.rnet-row\.rnet-subrow\{margin-left:20px\}/);
  assert.match(STRIPCSS, /\.sn-row\.sn-sub \{ margin-left: 20px; \}/);
  assert.match(STRIPCSS, /\.sn-actfail \{ border-color: #E5534B; color: #E5534B; \}/);
  // the toggle glyphs: closed ▸, open ▾ — one keyed expand per host
  assert.match(KERNEL, /_openSub\[t\.host\]\?'\\\\u25be':'\\\\u25b8'/);
  assert.match(STRIP, /\(openSub\.has\(t\.host\) \? "▾" : "▸"\) \+ " connections"/);
});

test("the web sub-rows never wear the panel's pre-existing .rnet-sub explainer class", () => {
  // .rnet-sub is the panel's DESCRIPTION block, styled margin:-6px 0 11px — a sub-ROW wearing it
  // is pulled up over the element above it (the user 2026-08-12, whose expanded connections
  // painted over the parent row's settings line). The rows and notes carry their own names.
  assert.match(KERNEL, /sr\.className='rnet-row rnet-subrow';/);
  assert.match(KERNEL, /rnet-empty rnet-subnote/);
  assert.doesNotMatch(KERNEL, /className='rnet-row rnet-sub'/, "the colliding class name must not return");
  assert.doesNotMatch(KERNEL, /rnet-empty rnet-sub\\"/, "sub notes must not wear .rnet-sub either");
});
