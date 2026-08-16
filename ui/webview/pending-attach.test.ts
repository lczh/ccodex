// While a picked/dropped/pasted file's bytes ship to the kernel (shipFileToHost →
// dropFile → droppedPath), the attachment strip shows a PENDING chip — name +
// pulsing dots — from the instant the file is chosen. On a phone that round trip
// is seconds long (base64 + a fragmented WS send + the kernel write), and with
// nothing on screen it read as a dead click (the user 2026-08-11: "seemed like it
// didn't work for a second and then the thumbnail appeared").
//
// Lifecycle is EVENT-based end to end: the chip goes up on pick, and comes down
// only on the ack (droppedPath), the kernel's loud nack (dropSaveFailed), a
// FileReader error, or the user's own ✕ — never a timer.
//
// The chat renderer has no jsdom harness, so — like the other webview tests —
// pin the wiring at the source level.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the pending chip goes up BEFORE the encode starts — pick-to-feedback is immediate", () => {
  // registry keyed by session, like composerFiles beside it
  assert.match(RENDER, /const pendingShips = new Map<string, string\[\]>\(\);/);
  // registered at the TOP of shipFileToHost (before new FileReader), with the sid captured once —
  // at call time via the sidAt default (a pasted-path caller passes the sid it verified against)
  assert.match(RENDER, /const name = f\.name \|\| "pasted\.png";\s*\n\s*const sid = sidAt;.*\n\s*addPendingShip\(sid, name\);.*\n\s*const reader = new FileReader\(\);/);
});

test("the strip renders pending chips (name + pulsing dots) and shows even with no real attachments", () => {
  // the strip's empty-check counts pending ships too
  assert.match(RENDER, /if \(!paths\.length && !pending\.length\) \{ strip\.style\.display = "none"; return; \}/);
  // each pending entry renders as a composer-file-pending chip with the ship-dots
  assert.match(RENDER, /composer-file composer-file-pending/);
  assert.match(RENDER, /composer-ship-dots/);
  // the ✕ removes just the CHIP — the escape hatch for an ack lost to a mid-ship disconnect
  assert.match(RENDER, /aria-label", "Dismiss pending attachment"/);
});

test("the droppedPath ack retires the chip it answers, then attaches the thumbnail", () => {
  assert.match(RENDER, /const owner = retirePendingShip\(m\.path\) \|\| activeId;/);
  assert.match(RENDER, /retirePendingShip\(m\.path\)[\s\S]{0,300}addComposerFile\(owner, m\.path\)/,
    "the ack attaches to the composer that SHIPPED the file (the 2026-08-16 wrong-tab attach)");
});

test("ack↔chip matching mirrors the kernel's saved-name sanitizer, FIFO as the fallback", () => {
  // drops/<ms>-<safe name>: the JS sanitizer mirrors _save_dropped_file's regex …
  assert.match(RENDER, /name\.replace\(\/\[\^\\w.-\]\+\/g, "_"\)\.slice\(-80\)/);
  assert.match(KERNEL, /re\.sub\(r"\[\^\\w.-\]\+", "_", name\)\[-80:\]/);
  // … and an unmatched ack still retires the OLDEST entry (the kernel answers in order)
  assert.match(RENDER, /list\.splice\(i >= 0 \? i : 0, 1\);/);
});

test("a failed kernel save is NACKED and surfaces loudly — never a silent stuck chip", () => {
  // kernel: the no-path branch replies dropSaveFailed instead of nothing
  assert.match(KERNEL, /_reply\(client, \{"type": "dropSaveFailed", "name": str\(msg\["name"\]\)\}\)/);
  // client: the nack retires the chip and says so in a toast
  assert.match(RENDER, /m\.type === "dropSaveFailed" && typeof m\.name === "string"/);
  assert.match(RENDER, /retirePendingShip\(m\.name\) \|\| activeId;[\s\S]{0,300}warnToast\(m\.name \+ " couldn't be saved on the kernel/);
  // a FileReader failure retires it too — an unreadable file must not pulse forever
  assert.match(RENDER, /reader\.onerror = \(\) => retirePendingShip\(name\);/);
});

test("pending chips are in-memory only — never persisted with drafts", () => {
  // a reload kills the page whose socket the ack would ride; persisting the chip would
  // revive dots that nothing can ever retire
  assert.doesNotMatch(RENDER, /pendingShips[\s\S]{0,80}persistDrafts|persistDrafts[\s\S]{0,300}pendingShips/);
});

test("the chip wears the accent loader-dots motif from styles.css", () => {
  assert.match(CSS, /\.composer-file-pending \{[^}]*dashed/);
  assert.match(CSS, /\.composer-ship-dots i \{[^}]*var\(--accent\)/);
  assert.match(CSS, /@keyframes ship-bnc/);
  // staggered like the romp loader's dots
  assert.match(CSS, /\.composer-ship-dots i:nth-child\(3\) \{ animation-delay: 0\.32s; \}/);
});
