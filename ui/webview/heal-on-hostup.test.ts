// Failed figure previews must heal when the TUNNEL comes back, not when the user next speaks
// (the user 2026-08-17: "a lot of the time these figure renders don't land until I send another
// message"). The heal is message-driven — render.ts re-runs retryFailedPreviews on any window
// message — which covers kernel restarts (the reconnect payload is a message) but never ticks on
// an idle session, where no traffic flows. So a relay-failed figure sat as a chip until the
// user's next send generated pushes. The fix: federation's /tunnels poll, already the authority
// on tunnel state, dispatches {type:"hostUp", hosts} through the same window-message path on a
// host's down→up transition. These pins hold the three load-bearing pieces of that contract.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const read = (f: string) => fs.readFileSync(path.join(UI, f), "utf8");
const RENDER = read("render.ts");
const FED = read("federation.ts");

test("the tunnel poll dispatches hostUp on a down→up transition, through the window-message path", () => {
  // recovery = was in downHosts last poll, still attached (want), not down now — a DETACHED host
  // leaving downHosts is not a recovery and must not fire the heal
  assert.match(FED, /const recovered = \[\.\.\.this\.downHosts\]\.filter\(\(h\) => want\.has\(h\) && !down\.has\(h\)\);/);
  assert.match(FED,
    /if \(recovered\.length\) window\.dispatchEvent\(new MessageEvent\("message", \{ data: \{ type: "hostUp", hosts: recovered \} \}\)\);/,
    "a MessageEvent, not a bare Event — it must ride the same listener whose top re-runs the preview heal");
});

test("the recovery set is computed BEFORE downHosts is overwritten with this poll's state", () => {
  const compute = FED.indexOf("const recovered = [...this.downHosts]");
  const overwrite = FED.indexOf("this.downHosts = down;");
  assert.ok(compute >= 0 && overwrite >= 0 && compute < overwrite,
    "reading this.downHosts after the overwrite would compare the new state to itself and never see a transition");
});

test("render.ts's message listener heals previews unconditionally at the top, so hostUp needs no case of its own", () => {
  const at = RENDER.indexOf('window.addEventListener("message", (e: MessageEvent) => {');
  assert.ok(at >= 0);
  const heal = RENDER.indexOf("retryFailedPreviews();", at);
  const firstCase = RENDER.indexOf('if (m.type === "session")', at);
  assert.ok(heal >= 0 && firstCase >= 0 && heal < firstCase,
    "the heal must run before the type dispatch — moving it under a type check would silently drop hostUp");
});
