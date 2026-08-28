// The union journal's transport resilience (the v1.3.23 audit's P1.3) and the last ms-only
// tag mint (its P2.6): a sync accepted by a dying socket was never replayed (dirty cleared at
// send time, the ack never came, and reconnect had no union hook), a refused ack waited for an
// UNRELATED future payload to re-send, and the join-menu's Date.now()-only id collided across
// panels. Executed against the SHARED TimelinePanel prototype, headless (the bare-prototype
// pattern _syncUnionOps documents), plus source pins for the boot wiring.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ui = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", ...p), "utf8");
const VIEW = ui("romp-timeline-view.js");
const RENDER = ui("webview", "render.ts");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

function panelProto(): any {
  const mod: any = { exports: {} };
  new Function("module", "exports", VIEW)(mod, mod.exports);
  return mod.exports.TimelinePanel.prototype;
}

function bare(): any {
  const p = Object.create(panelProto());
  p._unionOps = [{ host: "TESTHOST-A", name: "pool", inverse: {}, edit: { color: "#123" },
                   rt: { id: "TESTHOST-A:r1" }, gid: 7, oldName: "pool", oldColor: "",
                   post: {} }];
  return p;
}

test("executed: a reconnect replays the union sync whose ack died with the old socket", () => {
  const sends: any[] = [];
  (globalThis as any).window = {
    __rompTimelineSetUnionOps: (e: any, r: any, id: any) => sends.push({ e, r, id }),
  };
  try {
    const p = bare();
    p._syncUnionOps();                       // accepted by the dying socket, never delivered
    assert.equal(sends.length, 1);
    assert.equal(p._unionSyncDirty, false, "dirty clears at send time — the audited gap");
    p.unionTransportReset();                 // the shim's romp:wsup
    assert.equal(sends.length, 2, "the fresh socket carries the replay");
    assert.deepEqual(sends[1].e.map((o: any) => o.gid), [7],
      "the full-replace upsert makes the replay idempotent");
    p.unionOpsAck({ ok: true, opId: sends[0].id });   // the OLD socket's late twin
    assert.equal(p._unionSyncDirty, false, "a voided sync's ack settles nothing");
  } finally {
    delete (globalThis as any).window;
  }
});

test("executed: a refused union ack retries DIRECTLY once, then paces on payloads", () => {
  const sends: any[] = [];
  (globalThis as any).window = {
    __rompTimelineSetUnionOps: (e: any, r: any, id: any) => sends.push({ e, r, id }),
  };
  try {
    const p = bare();
    p._syncUnionOps();
    assert.equal(sends.length, 1);
    p.unionOpsAck({ ok: false, opId: sends[0].id });   // the kernel's save failed
    assert.equal(sends.length, 2,
      "the nack triggers ONE direct retry — no unrelated payload needed");
    p.unionOpsAck({ ok: false, opId: sends[1].id });   // still failing
    assert.equal(sends.length, 2, "a second consecutive nack never hammers a failing store");
    assert.equal(p._unionSyncDirty, true, "…the dirty flag holds the payload-paced re-send");
    if (p._unionSyncDirty) p._syncUnionOps();          // update()'s payload-arrival line
    assert.equal(sends.length, 3);
    p.unionOpsAck({ ok: true, opId: sends[2].id });    // the store healed
    assert.equal(p._unionRetryPending, false, "success re-arms the direct retry");
  } finally {
    delete (globalThis as any).window;
  }
});

test("the kernel's browser boot replays union syncs on the shim's reconnect (P1.3)", () => {
  // the VS Code host needs no twin — its extension reloads the webview on reconnect, and the
  // reload re-seeds from the kernel's journal echo; the browser shim reconnects IN PLACE
  assert.match(KERNEL,
    /window\.addEventListener\("romp:wsup",function\(\)\{if\(panel&&panel\.unionTransportReset\)panel\.unionTransportReset\(\);\}\);/);
});

test("no tag-creation surface mints ms-only ids (the v1.3.23 audit's P2.6)", () => {
  // two panels' same-ms mints collided; the server reads a same-id create as a replay, so
  // simultaneous 'alpha'/'beta' creates both acked ok while only one tag existed
  const mints = VIEW.match(/id: 'g' \+ Date\.now\(\)\.toString\(36\)/g) || [];
  const entropic = VIEW.match(
    /id: 'g' \+ Date\.now\(\)\.toString\(36\) \+ '-' \+ Math\.random\(\)\.toString\(36\)\.slice\(2, 8\)/g) || [];
  assert.ok(mints.length >= 2, "both timeline mints are present");
  assert.equal(mints.length, entropic.length, "no bare ms-only mint remains in the timeline view");
  assert.match(RENDER,
    /id: "g" \+ Date\.now\(\)\.toString\(36\) \+ "-" \+ Math\.random\(\)\.toString\(36\)\.slice\(2, 8\)/,
    "the chat strip's mint keeps its entropy too");
});
