// Sending while an attachment is still UPLOADING must never silently drop it (the user 2026-08-16:
// the composer said "uploading", allowed the send, and the message went without the image — the send
// reads only the acked composerFiles list, and pendingShips was never consulted). The gate: a send
// with ships in flight opens the same pane-local confirm the /clear guard uses — send WITHOUT the
// upload explicitly, or hold the send and let the LAST droppedPath ack fire it (event-based). A save
// nack cancels the hold loudly; any successful send supersedes it. The ack also attaches the file to
// the composer that SHIPPED it (retirePendingShip now returns the owning sid) instead of whatever tab
// is active at ack time — the same report's second face. Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("a send with ships in flight is gated by the confirm: send-without is explicit, wait is the default", () => {
  assert.match(RENDER, /const shipping = \(pendingShips\.get\(activeId\) \|\| \[\]\)\.length;/,
    "the send path finally consults the in-flight list");
  assert.match(RENDER, /if \(shipping && !opts\?\.pastShipGate\) \{/);
  assert.match(RENDER, /\{ label: "Wait for the upload", value: "wait" \}/);
  assert.match(RENDER, /\{ label: "Send without " \+ them, value: "now", danger: true \}/,
    "sending without the file is the marked-dangerous, explicit choice");
  assert.match(RENDER, /if \(v === "now"\) sendComposer\(\{ pastShipGate: true \}\);/);
  assert.match(RENDER, /else if \(v === "wait"\) \{ sendOnShip\.add\(sid\); renderComposerFiles\(sid\); \}/);
});

test("the held send fires on the LAST ack — event-based — and a nack cancels it loudly", () => {
  assert.match(RENDER, /if \(owner && sendOnShip\.has\(owner\) && !\(pendingShips\.get\(owner\) \|\| \[\]\)\.length\) \{/,
    "the deciding event is the last pending ship retiring");
  assert.match(RENDER, /if \(owner === activeId\) fireHeldSend\(\);/);
  assert.match(RENDER, /the held message was not sent; review it there/,
    "a mid-hold tab switch surfaces instead of sending a background composer");
  assert.match(RENDER, /const held = !!owner && sendOnShip\.delete\(owner\);/,
    "a failed save cancels the hold — it must not fire without the file it waited for");
  assert.match(RENDER, /\+ \(held \? " Your message was NOT sent\." : ""\)/);
});

test("any successful send supersedes a hold, so a spent hold can never double-send", () => {
  const hits = RENDER.match(/sendOnShip\.delete\(sid\);\s+\/\/ a send happened — any held one is superseded/g) || [];
  assert.equal(hits.length, 2, "both delivery paths (provisional queue and live route) clear it");
});

test("the ack attaches to the composer that SHIPPED the file, not whatever tab is active", () => {
  assert.match(RENDER, /function retirePendingShip\(key: string\): string \| null \{/);
  assert.match(RENDER, /const owner = retirePendingShip\(m\.path\) \|\| activeId;/);
  assert.match(RENDER, /addComposerFile\(owner, m\.path\);/);
});

test("a held send is visible on the button and always inspectable", () => {
  assert.match(RENDER, /sendBtn\.classList\.toggle\("send-held", held\);/);
  assert.match(RENDER, /"sends when the upload finishes"/);
  assert.match(CSS, /#composer-send\.send-held \{ opacity: 0\.45; \}/);
});
