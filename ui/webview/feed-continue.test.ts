// The CONTINUE button (the user 2026-08-08): a needs-you card's one-click "nothing needed from me,
// keep going". It is a REPLY with a kernel-canned body — askFollowUp cont:true — never a bare column
// move (the messageless cardMove was removed 2026-07-25 for exactly that: a move with no message adds
// no information). The card history that earned it (2026-07-25..08-08): 58% of blocked episodes
// resolved with no user gesture on the card, and a fifth of Clears landed on sessions visibly
// mid-turn — a "stop" sent where the user meant "keep going".
//
// Layout (the user 2026-08-08): Clear sits as high and as far right as it can while the title and the
// time-ago keep first claim; Continue sits LEFT of Clear when they share a line; when only one button
// fits per line, Clear takes the upper line. No jsdom for the feed renderer, so pin at the source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("Continue posts askFollowUp cont:true — the kernel owns the canned body (one voice-tested home)", () => {
  // the client sends only the gesture; no text rides along, so the copy can't fork from CONTINUE_TEXT
  assert.match(FEED, /vscodeApi\?\.postMessage\(\{ type: "askFollowUp", itemId: it\.itemId, sid: it\.sid, cont: true \}\);/);
  // the kernel substitutes its constant and the arm otherwise IS the typed-reply path (optimistic
  // reopen included) — pinned here because the button's whole design is "a reply, not a new mechanism"
  // …with the romp-canned marker riding the button's send (the chat folds it to a gesture gist)
  assert.match(KERNEL, /text = \(CONTINUE_TEXT \+ "\\n\\n<!-- romp-canned: continue -->"\) if msg\.get\("cont"\) else str\(msg\["text"\]\)/);
  assert.match(KERNEL, /CONTINUE_TEXT = \("Nothing needed from me here\./);
});

test("Continue shows only on a LIVE needs-you card with no live ask (and never on a placeholder)", () => {
  // a live permission/picker/apiError/quarantine block (it.blocked) can't be answered by "keep going" —
  // text sent there would queue behind the ask; Working/Completed cards have nothing to continue
  assert.match(FEED, /contBtn\.style\.display = \(askColumn\(it\) === "needsInput" && it\.live && !it\.provisional && !it\.blocked\)/);
});

test("Continue acknowledges INSTANTLY (disable + relabel) and re-arms only after the judge has ruled", () => {
  // the click-safety rule: the ack precedes any kernel round-trip; same contract as the modal's
  // Check status ("Sent" survives re-renders while followupPending/recheck holds)
  assert.match(FEED, /cont\.disabled = true; cont\.textContent = "Sent";/);
  assert.match(FEED, /optimisticFollowMove\(it\.itemId\);\s*\n\s*render\(\);\s*\n\s*\};/);
  assert.match(FEED, /if \(contBtn\.disabled && !it\.followupPending && !it\.recheck && !it\.rejudging\) \{\s*\n\s*contBtn\.disabled = false; contBtn\.textContent = "Continue";\s*\n\s*\}/);
});

test("the action corner: Continue left of Clear on one line; Clear on the UPPER line when they stack", () => {
  // source order [cont, clr] + row flex = Continue left of Clear when they share a line; wrap-reverse
  // places the overflow line ABOVE the first, so Clear (second in source, the one that wraps) lands on
  // the upper line when only one button fits per line — Clear stays as high and right as it can
  assert.match(FEED, /btns\.append\(cont, clr\);/);
  assert.match(CSS, /\.fask-btns \{ float: right; margin-left: 8px; display: flex; flex-wrap: wrap-reverse;\s*\n\s*justify-content: flex-end; gap: 3px 8px; \}/);
  // the corner floats from the END of row1's inline flow, so the title + time-ago keep first claim
  assert.match(FEED, /row1\.append\(bellBtn\);/);
  assert.match(FEED, /row1\.append\(btns\);/);
});

test("no re-home dance: the corner lives in row1 in EVERY mode (strongest form of click-safety)", () => {
  assert.doesNotMatch(FEED, /clrHome/, "the grouped-mode Clear re-home is gone");
});

test("the modal footer carries the same Continue (single-ask branch), left of Clear", () => {
  assert.match(FEED, /const cont = el\("button", "fdismiss feed-modal-continue"\); cont\.id = "feed-modal-continue"; cont\.textContent = "Continue";/);
  assert.match(FEED, /footRow\.append\(age, fup, cs, cont, clr\)/);
  // same gating + ack contract as the card button
  assert.match(FEED, /if \(contEl && askColumn\(it\) === "needsInput" && it\.live && !it\.provisional && !it\.blocked\) \{/);
  assert.match(FEED, /contEl\.disabled = true; contEl\.textContent = "Sent";/);
  // default-hidden and reset each render; only the single-ask branch shows it
  assert.match(FEED, /if \(contEl\) \{ contEl\.style\.display = "none"; \}/);
});
