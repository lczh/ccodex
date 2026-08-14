// The remote-kernel row says HOW a build differs and offers the action that can actually succeed
// (the user 2026-07-27): "behind N" offers Push, "ahead N" offers Pull (fetched over THIS machine's
// ssh — the remote has no outbound ssh route, although a trusted check-in has an authenticated Romp
// administration route), and an auto-sync in
// flight ('pulling'/'asking' included) suppresses the manual buttons. A checked-in host, which no ssh
// of ours can reach, offers Update when it is behind — the peer fast-forwards ITSELF over the link it
// holds (the user 2026-07-28) — and nothing but an explanation otherwise. Pinned in BOTH copies —
// web _LANDING_REMOTES_JS (kernel.py) and the VS Code strip — which must stay in step.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");
const STRIP = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.ts"), "utf8");

test("web popover: Pull rides the attach tunnel, gated to a provable fast-forward", () => {
  assert.match(KERNEL, /data-p=/, "a Pull control keyed by host");
  assert.match(KERNEL, /\/tunnels\/pull/, "wired to the kernel's pull route");
  assert.match(KERNEL, /t\.fastPull&&!apx&&!t\.checkinPeer/, "offered only when ff-provable, idle, ssh-reachable");
  // Push is offered ONLY where it can succeed: our own ssh route AND a provable fast-forward. Anything
  // else (ahead, diverged, a build this repo has never seen) is refused by the remote's ancestor check
  // every time, so it gets Pull, Update, or an explanation — never a button that can only error.
  assert.match(KERNEL, /t\.status==='up'&&t\.fastForward&&!apx&&!t\.checkinPeer\)\?'<button class=rnet-upd data-u=/);
  // the checked-in case explains itself instead of dead-ending
  assert.match(KERNEL, /No ssh path from this machine \(it checked in over its own tunnel\)/);
  // an auto-pull/ask in flight counts as busy everywhere a push does
  assert.match(KERNEL, /t\.autoPush\.phase==='pulling'/);
  assert.match(KERNEL, /t\.autoPush\.phase==='asking'/);
});

test("web popover: a checked-in peer that is behind is asked to update itself", () => {
  assert.match(KERNEL, /t\.status==='up'&&t\.askPull&&!apx/, "offered only on a provable fast-forward");
  assert.match(KERNEL, /data-a=/, "an Update control keyed by host");
  assert.match(KERNEL, /\/tunnels\/askpull/, "wired to the kernel's ask route");
  assert.match(KERNEL, /asks it to fast-forward itself over the link it holds/, "the tooltip says who does the work");
});

test("drift banner: it prompts only for a host romp can actually move", () => {
  // the every-4s dead end (the user 2026-07-28): a laptop with no ssh path from here was offered a push
  // that /tunnels/update refused on sight, over and over
  assert.match(KERNEL, /\(t\.fastForward&&!t\.checkinPeer\)\|\|t\.askPull/);
  assert.match(KERNEL, /route\[t\.host\]=t\.askPull\?'\/tunnels\/askpull':'\/tunnels\/update'/,
    "each host goes down the route that works for it");
});

test("VS Code strip: same row treatment", () => {
  assert.match(STRIP, /ab > 0 \? ` · ahead \$\{ab\} commit/, "ahead/behind wording matches the web copy");
  assert.match(STRIP, /bb > 0 \? ` · behind \$\{bb\} commit/);
  assert.match(STRIP, /" · diverged"/);
  assert.match(STRIP, /act\("\/tunnels\/pull", t\.host, pl, "Pulling…"\)/, "Pull posts the kernel route");
  assert.match(STRIP, /t\.status === "up" && t\.fastPull && !apx && !t\.checkinPeer/);
  assert.match(STRIP, /t\.status === "up" && t\.fastForward && !apx && !t\.checkinPeer/,
    "Push only where it can succeed — our ssh route and a provable fast-forward");
  assert.match(STRIP, /t\.status === "up" && t\.askPull && !apx/, "Update for a checked-in peer that is behind");
  assert.match(STRIP, /act\("\/tunnels\/askpull", t\.host, a, "Asking…"\)/);
  assert.match(STRIP, /No outbound ssh path from this machine \(it checked in over its own reverse tunnel\)/);
  assert.match(STRIP, /trusted check-in (?:still )?grants authenticated Romp administration/,
    "the copy does not mistake missing outbound ssh for a one-way security boundary");
  assert.match(STRIP, /t\.autoPush\.phase === "pulling"/);
  assert.match(STRIP, /t\.autoPush\.phase === "asking"/);
});
