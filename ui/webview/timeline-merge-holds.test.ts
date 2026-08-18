// The federated timeline's merge holds and prefix coverage (the user 2026-08-17, whose dashboard —
// freshly reloaded with snape newly attached — showed most local sessions bar-less, connectors from
// a session that wasn't running, and idle remote lanes). Three exact defects, pinned here:
// 1. BOOT BARS RACE: the merged BARS emission was gated only on the local LANES snapshot, so a
//    remote's bars landing first emitted a merged-without-local payload — and the panel's applyBars
//    REPLACES turns wholesale, blanking every local lane until the next local push.
// 2. A lane's fork parent (sessions[].branch.fromId) was never host-prefixed, so remote branch
//    connectors silently missed their vidx lookup and never drew.
// 3. The kernel stripped bar `mids` from the wire (a 2026-07-07 payload audit) that the 2026-08-06
//    merged-view dmid join READS — the join's key set was always empty on live payloads. Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the merged bars emission waits for the LOCAL bars snapshot, like the lanes hold", () => {
  assert.match(FED, /if \(!\(LOCAL in this\.perHostTl\)\) return;/);
  assert.match(FED, /if \(bars && !\(LOCAL in this\.perHostTlBars\)\) return;/,
    "a remote winning the connect race can no longer blank every local lane");
});

test("a remote lane's fork parent is prefixed so its branch connector draws", () => {
  assert.match(FED, /if \(out\.branch && typeof out\.branch === "object" && typeof out\.branch\.fromId === "string"\)/);
  assert.match(FED, /out\.branch = \{ \.\.\.out\.branch, fromId: prefixId\(host, out\.branch\.fromId\) \};/);
});

test("bar mids ride the wire — the merged-view dmid join reads them", () => {
  assert.doesNotMatch(KERNEL, /_b\.pop\("mids", None\)/, "the payload-audit pop predated the dmid join and starved it");
  assert.match(KERNEL, /mids STAY on the wire \(2026-08-17\)/);
  assert.match(KERNEL, /_m\.pop\("fromOrig", None\)/, "fromOrig stays binder-only — nothing client-side reads it");
});
