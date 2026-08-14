// Remote dialing is gated on the KERNEL's tunnel state (the user 2026-07-21): federation used to
// blind-retry a remote's local tunnel port every 2s forever, even when the kernel — which
// health-checks its own ssh tunnels — reported the tunnel down. Endless failed connects feed the
// browser's per-host WebSocket failure backoff (Firefox delays re-admitting a recently-failed
// endpoint), which then held the LOCAL panes' reconnects hostage after a kernel restart: the
// "Disconnected — reconnecting…" banner sat until a manual page refresh. No jsdom for the manager
// class, so pin at the source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");

test("a conn carries the kernel-reported live flag, and connect() refuses to dial a down tunnel", () => {
  assert.match(FED, /live: boolean;/);
  assert.match(FED, /if \(conn\.closed \|\| !conn\.live\) return;/);
  // one attempt at a time — poll + the 2s onclose retry can both call in
  assert.match(FED, /if \(conn\.ws && \(conn\.ws\.readyState === 0 \|\| conn\.ws\.readyState === 1\)\) return;/);
});

test("the /tunnels poll refreshes live from t.status and re-dials a closed conn the kernel says is up", () => {
  assert.match(FED, /c\.live = t\.status === "up";/);
  assert.match(FED, /if \(c\.live && \(!c\.ws \|\| c\.ws\.readyState === 3\)\) this\.connect\(c\);/);
  // openRemote seeds the flag from the same status so the first dial is gated too
  assert.match(FED, /this\.openRemote\(host, t\.status === "up"\)/);
});

test("remote tokens remain server-side behind the authenticated relay", () => {
  assert.match(FED, /t\.hasToken && t\.localPort/);
  assert.doesNotMatch(FED, /t\.token/);
  assert.doesNotMatch(FED, /token=\$\{encodeURIComponent\(token\)\}/);
});

test("remotes are dialed through the kernel's /remote relay on the page origin, never a loopback port", () => {
  // The forwarded ssh -L port only exists on the kernel's own machine. A direct dial to
  // 127.0.0.1:<port> works from a browser ON that machine and silently reaches nothing from
  // anywhere else (the phone, over `tailscale serve`) — every remote host just vanished, with no
  // disconnected mark (2026-07-30). The kernel's /remote/<host>/ws route splices the connection
  // onto the tunnel server-side, so the same-origin dial works from every client.
  assert.match(FED, /\$\{proto\}\$\{location\.host\}\/remote\/\$\{encodeURIComponent\(host\)\}\/ws\?/);
  assert.ok(!/\$\{proto\}127\.0\.0\.1/.test(FED), "the direct loopback dial must not come back");
});

test("the down-tunnel 2s blind-retry loop is gone (the retry rides connect()'s own gate)", () => {
  // the onclose retry may stay, but it must route through connect(), whose live-gate stops a dead
  // tunnel from being redialed — there is no ungated `new WebSocket` path for remotes.
  const connectFn = FED.slice(FED.indexOf("private connect(conn: Conn)"));
  assert.ok(connectFn.indexOf("if (conn.closed || !conn.live) return;") >= 0
            && connectFn.indexOf("if (conn.closed || !conn.live) return;") < connectFn.indexOf("new WebSocket"),
            "the live-gate must run before any WebSocket is constructed");
});
