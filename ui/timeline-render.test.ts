// Headless draw() smoke test (2026-06-12 regression): the timeline view's draw() runs only in a
// browser, so the unit tests for its pure helpers never executed the render path — and a `const hit`
// name collision (a membership helper shadowing the bar loop's local `const hit` rect) TDZ-crashed
// draw() on the first in-window bar, blanking the whole timeline while every test stayed green. This
// test stands up a minimal DOM shim, feeds the view a synthetic payload with IN-WINDOW turns (the
// exact path that crashed), and asserts draw() completes and emits lanes. It is the guard that any
// future draw()-level crash trips.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

// ---- minimal DOM shim (only what the view touches: SVG/HTML nodes, canvas measureText, localStorage) ----
function makeNode(tag: string): any {
  const n: any = {
    tag, _attrs: {}, children: [] as any[], style: {}, dataset: {}, textContent: "", parentNode: null,
    classList: { _s: new Set<string>(), add(...a: string[]) { a.forEach((c) => this._s.add(c)); },
      remove(...a: string[]) { a.forEach((c) => this._s.delete(c)); },
      toggle(c: string, f?: boolean) { f ? this._s.add(c) : this._s.delete(c); }, contains(c: string) { return this._s.has(c); } },
    setAttribute(k: string, v: any) { this._attrs[k] = v; }, getAttribute(k: string) { return this._attrs[k]; },
    setAttributeNS(_n: any, k: string, v: any) { this._attrs[k] = v; }, removeAttribute(k: string) { delete this._attrs[k]; },
    appendChild(c: any) { c.parentNode = n; this.children.push(c); return c; },
    insertBefore(c: any, ref: any) { c.parentNode = n; const i = this.children.indexOf(ref); i < 0 ? this.children.push(c) : this.children.splice(i, 0, c); return c; },
    removeChild(c: any) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; },
    get firstChild() { return this.children[0] || null; },
    addEventListener() {}, removeEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { width: 1400, height: 420, left: 0, top: 0, right: 1400, bottom: 420 }; },
    closest() { return null; }, focus() {},
    createEl(t: string, o: any) { const e = makeNode(t); if (o && o.cls) e.classList.add(o.cls); if (o && o.text) e.textContent = o.text; this.appendChild(e); return e; },
    createDiv(o: any) { return this.createEl("div", o); }, createSpan(o: any) { return this.createEl("span", o); },
  };
  return n;
}
const g: any = global;
g.document = {
  createElement(t: string) { return t === "canvas" ? { getContext() { return { font: "", measureText(s: string) { return { width: (s ? s.length : 0) * 6 }; } }; } } : makeNode(t); },
  createElementNS(_n: any, t: string) { return makeNode(t); },
  body: makeNode("body"), documentElement: makeNode("html"), head: makeNode("head"),
  getElementById() { return null; },   // the loader overlay injects its <style> once via this
  addEventListener() {}, removeEventListener() {},
};
g.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
g.getComputedStyle = () => ({ backgroundColor: "rgb(30,30,30)" });
g.requestAnimationFrame = () => 0;
g.addEventListener = () => {}; g.removeEventListener = () => {};
g.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
g.window = g;   // the view reads window.* (event listeners / globals) in its constructor
g.innerWidth = 1400; g.innerHeight = 800;   // moveTip() clamps the tooltip to the viewport

const viewPath = path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js");
const { TimelinePanel, fmtSpan } = createRequire(__filename)(viewPath);

const DAY = 86400, WEEK = 7 * DAY, MONTH = 30 * DAY;
test("fmtSpan: concise day/week/month label for long collapsed gaps", () => {
  assert.equal(fmtSpan(DAY), "1 day");
  assert.equal(fmtSpan(2 * DAY), "2 days");
  assert.equal(fmtSpan(2.4 * DAY), "2 days");           // rounds to nearest day
  assert.equal(fmtSpan(6 * DAY), "6 days");
  assert.equal(fmtSpan(WEEK), "1 week");
  assert.equal(fmtSpan(3 * WEEK), "3 weeks");
  assert.equal(fmtSpan(MONTH), "1 month");
  assert.equal(fmtSpan(2 * MONTH), "2 months");
});
test("fmtSpan: never reads '7 days' or '5 weeks' — each unit clamps below the next threshold", () => {
  assert.equal(fmtSpan(WEEK - 1), "6 days");            // just under a week stays "6 days", not "7 days"
  assert.equal(fmtSpan(MONTH - 1), "4 weeks");          // just under a month stays "4 weeks", not "5 weeks"
});

// A synthetic payload with TWO live lanes, each with an IN-WINDOW turn carrying the atom ids — the
// precise shape that exercises barLit/dotLit (where the TDZ crash lived).
function synthData() {
  const now = 1_781_000_000;
  const turn = (id: string, dt0: number, dt1: number) => ({
    id, promptId: id + "#p", workId: id + "#w",
    start: now - dt0, end: now - dt1, prompt: "do the thing", src: "typed", mids: [],
    pending: false, summary: "did the thing", reply: "did it", tid: "fork-" + id, uuid: "u-" + id,
    workUuid: "w-" + id, replyUuid: "r-" + id,
  });
  const sess = (id: string, name: string) => ({
    id, name, color: "#7aa2f7", state: "working", live: true, model: "Opus 4.8", effort: "xhigh",
    context: 40, since: now - 60, awaiting: [], compacting: [], pendingMail: 0, compactions: [], faded: false, stale: false,
  });
  return {
    now,
    sessions: [sess("S1", "alpha"), sess("S2", "beta")],
    turns: { S1: [turn("S1:1:aa", 300, 60), turn("S1:2:bb", 50, 5)], S2: [turn("S2:1:cc", 200, 30)] },
    messages: [], activeChat: null, focus: null, hover: null, usage: null,
  };
}

test("draw() renders multiple lanes without throwing (no const-hit TDZ crash)", () => {
  const host = makeNode("div");
  const panel = new TimelinePanel(host);
  panel.data = synthData();
  assert.doesNotThrow(() => panel.draw(), "draw() must not throw on in-window bars");
  assert.ok(panel.svg.children.length > 10, "draw() should emit a populated SVG (lanes/bars/dots)");
  assert.equal(panel._vis.length, 2, "both live lanes must survive the render");
});

// Dead lanes strike their name (the user 2026-06-13: a dead agent's name should be struck through
// wherever it appears — timeline + feed). Keys on the data-model `live` field, NOT a render heuristic.
function findText(node: any, txt: string): any {
  if (node.tag === "text" && node.textContent === txt) return node;
  for (const c of node.children || []) { const r = findText(c, txt); if (r) return r; }
  return null;
}
test("a dead lane's name is struck through; a live lane's is not", () => {
  const panel = new TimelinePanel(makeNode("div"));
  const data: any = synthData();
  // turn lane "beta" into an ended (dead) session — its in-window turn keeps it on screen
  Object.assign(data.sessions[1], { live: false, faded: true, state: "idle" });
  panel.data = data;
  assert.doesNotThrow(() => panel.draw());
  const live = findText(panel.svg, "alpha");
  const dead = findText(panel.svg, "beta");
  assert.ok(live && dead, "both lane names must render");
  assert.equal(dead.getAttribute("text-decoration"), "line-through", "the dead lane's name is struck through");
  assert.ok(!live.getAttribute("text-decoration"), "the live lane's name is NOT struck");
});

test("a compacting lane's badge says Compacting — never a percentage (the user 2026-07-02)", () => {
  // The scraped tmux pct was laggy/inaccurate, and the SDK offers NO progress number (compact_progress
  // events are lifecycle-only — investigated 2026-07-02). The scan-bar animation is the live cue; the
  // badge is just the state word, even when a stale compactPct still rides the payload.
  const panel = new TimelinePanel(makeNode("div"));
  const data: any = synthData();
  Object.assign(data.sessions[0], { state: "compacting", compactPct: 74 });
  panel.data = data;
  assert.doesNotThrow(() => panel.draw());
  assert.ok(findText(panel.svg, "Compacting"), "the Compacting badge shows");
  assert.ok(!findText(panel.svg, "Compacting 74%"), "…with no percentage riding it");
});

test("effort words left-justify to ONE fixed column regardless of model-name length (the user 2026-07-03)", () => {
  const panel: any = new TimelinePanel(makeNode("div"));
  const data: any = synthData();
  // two lanes, very different model-name widths, distinct effort words so findText can locate each
  Object.assign(data.sessions[0], { model: "Haiku", effort: "high" });         // short model
  Object.assign(data.sessions[1], { model: "Claude Opus 4.8", effort: "xhigh" }); // long model
  panel.data = data;
  assert.doesNotThrow(() => panel.draw());
  const e0 = findText(panel.svg, "high");
  const e1 = findText(panel.svg, "xhigh");
  assert.ok(e0 && e1, "both effort words render");
  // their LEFT x is identical — the effort column, not dangling after each model
  assert.equal(Number(e0.getAttribute("x")), Number(e1.getAttribute("x")), "efforts share one left x");
  // and it sits to the RIGHT of the (shared) model column start, so it's a real second column
  const m0 = findText(panel.svg, "Haiku");
  assert.ok(Number(e0.getAttribute("x")) > Number(m0.getAttribute("x")), "effort column is right of the model column");
});

test("a lane resolving a /model switch shows the pulsing dots overlay, not just a dimmed name (the user 2026-07-03)", () => {
  const panel: any = new TimelinePanel(makeNode("div"));
  const data: any = synthData();
  data.sessions[0].modelPending = true;    // server-driven: the kernel flags the switch until the new name lands
  panel.data = data;
  assert.doesNotThrow(() => panel.draw());
  // the switching lane gets a persistent overlay div (3 pulsing accent dots), keyed by sid
  const dots = panel._metaDots.get(data.sessions[0].id);
  assert.ok(dots, "the switching lane has a meta-dots overlay div");
  assert.equal(dots.children.length, 3, "three pulsing dots, the romp loader motif");
  assert.equal(dots.className, "romp-tl-meta-dots", "styled by the shared dots class");
  // the settled lane (no pending) gets NO dots overlay
  assert.ok(!panel._metaDots.get(data.sessions[1].id), "a settled lane shows its name, no dots");
});

test("the switching-dots overlay is reaped once the /model pick lands (no orphan pulse)", () => {
  const panel: any = new TimelinePanel(makeNode("div"));
  const pending: any = synthData();
  pending.sessions[0].modelPending = true;
  panel.data = pending; panel.draw();
  assert.ok(panel._metaDots.get(pending.sessions[0].id), "dots up while pending");
  // the new model lands → modelPending clears; the next draw drops the overlay
  const landed: any = synthData();
  landed.sessions[0].modelPending = false;
  panel.data = landed; panel.draw();
  assert.ok(!panel._metaDots.get(landed.sessions[0].id), "overlay reaped once resolved");
});

test("an optimistic eye-toggle (hideFromFeed) survives a STALE push and clears once confirmed (the user 2026-06-22)", () => {
  const panel: any = new TimelinePanel(makeNode("div"));
  panel.update(synthData());
  const sid = panel.data.sessions[0].id;
  // user clicked the eye: optimistic flip + a sticky pending entry (what the click handler does)
  panel.data.sessions[0].hideFromFeed = true;
  panel._pendingFlags[sid] = { hideFromFeed: true };
  // a routine push lands carrying the OLD value (kernel hasn't processed the flag yet) → must NOT revert
  panel.update(synthData());
  assert.equal(panel.data.sessions[0].hideFromFeed, true, "the hidden state holds through a stale push (no flicker-back)");
  assert.ok(panel._pendingFlags[sid], "still pending — the kernel hasn't confirmed yet");
  // the kernel's confirming rebuild arrives with the new value → the override is dropped
  const confirmed: any = synthData();
  confirmed.sessions[0].hideFromFeed = true;
  panel.update(confirmed);
  assert.equal(panel.data.sessions[0].hideFromFeed, true, "confirmed value holds");
  assert.ok(!panel._pendingFlags[sid], "pending cleared once the kernel agrees (so a later external change isn't blocked)");
});

test("draw() also survives with an active hover set (atom-id highlight path)", () => {
  const host = makeNode("div");
  const panel = new TimelinePanel(host);
  const data: any = synthData();
  panel.data = data;
  // a work-atom hover + a prompt-atom hover → exercises barLit/dotLit membership inside the loops
  panel._hover = { ids: ["S1:1:aa#w", "S2:1:cc#p"] };
  assert.doesNotThrow(() => panel.draw(), "draw() must not throw while a hover highlight is active");
  assert.equal(panel._vis.length, 2);
});

// "Show active sessions only" (the user 2026-08-12, default ON): a lane draws only when the session
// has activity intersecting the VISIBLE window — a live-but-idle session thins out (its chat tab is
// untouched) and returns when zoom/pan reaches a range where it worked. Toggled off (the gear), the
// old rule stands: live lanes always show. Recomputed per draw off the window — pure view state.
test("an idle live lane thins out under active-only, and returns when the toggle is off", () => {
  const panel: any = new TimelinePanel(makeNode("div"));
  const data: any = synthData();
  // beta's only activity is ~25h before the window — with gap-collapse off it is genuinely off-screen
  const old = data.turns.S2[0];
  data.turns.S2 = [Object.assign({}, old, { start: data.now - 90000, end: data.now - 89000 })];
  panel._collapseGaps = false;   // a collapsed axis SHOWS that old period on screen — defeat it for determinism
  panel.data = data;
  panel.draw();
  assert.deepEqual(panel._vis.map((s: any) => s.id), ["S1"], "the idle live lane is hidden by default");
  panel._activeOnly = false;     // the gear toggle writes romp:settings.activeOnly; the storage event re-reads it
  panel.draw();
  assert.equal(panel._vis.length, 2, "toggled off, a live-but-idle session keeps its lane");
  // …and zooming out to cover the old work brings the lane back WITH the toggle on (the window rule,
  // not a latch): widen the window past the 25h-old turn.
  panel._activeOnly = true;
  panel._winSec = 200000; panel._offDirty = true; panel._pinned = true;
  panel.draw();
  assert.equal(panel._vis.length, 2, "zoomed out over its past work, the lane returns");
});

test("a live lane with NO bars evidence yet is presumed active — never dropped cold (the user 2026-08-15)", () => {
  // after a kernel restart, the active-only filter judged a WORKING lane by bars that had not arrived
  // yet (its own kernel still warming, or a remote host's bars unmerged) and dropped the lane entirely;
  // it then reappeared bar-less as data trickled in. A live lane without a turns key is unjudged, not
  // inactive; its key landing (every with_bars build writes one per covered lane) hands judgment back.
  const panel: any = new TimelinePanel(makeNode("div"));
  const data: any = synthData();
  delete data.turns.S2;             // S2's evidence has not arrived (cold connect / unmerged host)
  panel._collapseGaps = false;
  panel.data = data;
  panel.draw();
  assert.deepEqual(panel._vis.map((s: any) => s.id).sort(), ["S1", "S2"],
    "the evidence-less live lane stands beside the working one");
  panel._barsSeen.add("S2");        // its bars payload lands (even empty) → judgment passes to hasWork
  panel.draw();
  assert.deepEqual(panel._vis.map((s: any) => s.id), ["S1"],
    "once its bars arrive empty, the same lane is genuinely quiet and thins out");
});

test("an ALL-quiet window falls back to the live lanes — never a blank, un-grabbable band", () => {
  // Panning into a stretch where nothing happened must not empty the timeline: a blank band reads as
  // broken, and the per-lane row space is the only mouse-drag pan surface, so an empty _vis would
  // strand the pan gesture there (caught by the drag-pan test when active-only first landed).
  const panel: any = new TimelinePanel(makeNode("div"));
  const data: any = synthData();
  panel._collapseGaps = false;
  panel.data = data;
  panel._winSec = 600; panel._offSec = 50000; panel._pinned = false; panel._offDirty = true;
  panel.draw();
  assert.equal(panel._vis.length, 2, "both LIVE lanes stand in an all-quiet window");
  const src = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");
  assert.match(src, /if \(this\._activeOnly && !vis\.length\) vis = data\.sessions\.filter\(inView\)\.filter\(\(s\) => s\.live \|\| hasWork\(s\)\)/);
});

test("the active-only flag rides romp:settings like collapseGaps (source pins)", () => {
  const src = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");
  assert.match(src, /this\._activeOnly = true;/, "default ON");
  assert.match(src, /if \(raw\) this\._activeOnly = JSON\.parse\(raw\)\.activeOnly !== false/, "constructor read");
  assert.match(src, /this\._activeOnly = s2\.activeOnly !== false/, "storage live-sync");
  assert.match(src, /const active = \(s\) => \(s\.live && !this\._activeOnly\) \|\| hasWork\(s\)/, "the gate");
  assert.match(src, /overlaps\(t\.start, barEndT\(t, nowS, data\.now\)\)/,
    "an OPEN turn counts to the live edge, exactly as its bar is drawn — a working session never hides");
});

// A romp NUDGE prompt marks its dot as a ROMP MESSAGE: a BLACK-filled dot with the romp favicon swirl
// INSIDE it (the user 2026-06-23, replacing the old white ⚡ bolt). Differential: marking one prompt adds
// exactly one swirl <image>, and the old white bolt path is gone.
function swirlImages(panel: any): number {
  let n = 0;
  const walk = (node: any) => {
    if (node.tag === "image" && String(node.getAttribute("href") || "").indexOf("romp-swirl-glyph") >= 0) n++;
    for (const c of node.children || []) walk(c);
  };
  walk(panel.svg);
  return n;
}
function whiteBoltPaths(panel: any): number {
  let n = 0;
  const walk = (node: any) => {
    if (node.tag === "path" && node.getAttribute("fill") === "#ffffff") n++;
    for (const c of node.children || []) walk(c);
  };
  walk(panel.svg);
  return n;
}
// Any romp-AUTHORED prompt draws the romp swirl in its dot. Originally auto-nudges only (the user
// 2026-06-23); widened to every romp message (the user 2026-07-16, who reported an auto-retry rendering as a user prompt
// instead of a romp logo thing), mirroring the chat's 2026-07-05 supersession of the same rule.
test("an AUTO-nudge prompt draws the romp swirl in its dot; a normal user prompt does not", () => {
  const base = new TimelinePanel(makeNode("div"));
  base.data = synthData();
  base.draw();
  const before = swirlImages(base);
  // an AUTO-nudge (t.nudgeAuto) → the swirl
  const panel = new TimelinePanel(makeNode("div"));
  const data: any = synthData();
  data.turns.S1[0].nudgeAuto = true;
  panel.data = data;
  assert.doesNotThrow(() => panel.draw());
  assert.equal(swirlImages(panel), before + 1, "exactly one romp swirl is added for the single AUTO-nudge");
  assert.equal(whiteBoltPaths(panel), 0, "the old white ⚡ bolt path is gone");
  // a plain human prompt (neither flag) → NO swirl, a normal session-coloured dot
  const plain = new TimelinePanel(makeNode("div"));
  plain.data = synthData();
  assert.doesNotThrow(() => plain.draw());
  assert.equal(swirlImages(plain), before, "a typed human prompt draws NO swirl");
});

test("a romp-AUTHORED prompt (an auto-RETRY, not an auto-nudge) also draws the swirl (the user 2026-07-16)", () => {
  const base = new TimelinePanel(makeNode("div"));
  base.data = synthData();
  base.draw();
  const before = swirlImages(base);
  // the exact case from the screenshot: romp's auto-retry — author 'romp' (romp-injected), but NOT romp-auto,
  // so the old auto-only rule left it looking like something the human typed.
  const retry = new TimelinePanel(makeNode("div"));
  const d2: any = synthData();
  d2.turns.S1[0].romp = true;           // romp-injected (the kernel's new author flag)…
  d2.turns.S1[0].nudgeAuto = false;     // …but not an auto-nudge
  d2.turns.S1[0].prompt = "retry";
  retry.data = d2;
  assert.doesNotThrow(() => retry.draw());
  assert.equal(swirlImages(retry), before + 1, "a romp auto-retry now wears the romp logo, not a user-prompt dot");
});

// A segment that straddled a host sleep renders as several bars (kernel _awake_spans); only the FIRST
// carries the prompt — the post-sleep continuation pieces are flagged `cont` and must draw NO second
// prompt dot (the user 2026-06-22). Prompt dots are the only #e8eef5-stroked circles in this
// message-less fixture, so counting them is an exact differential.
function promptDots(panel: any): number {
  let n = 0;
  const walk = (node: any) => {
    if (node.tag === "circle" && node.getAttribute("stroke") === "#e8eef5") n++;
    for (const c of node.children || []) walk(c);
  };
  walk(panel.svg);
  return n;
}
test("a `cont` continuation bar (post-sleep piece) draws NO prompt dot; a non-cont one does", () => {
  const base = new TimelinePanel(makeNode("div"));
  base.data = synthData();
  base.draw();
  const before = promptDots(base);
  const now = (synthData() as any).now;
  const piece = (cont: boolean) => ({
    id: "S1:1:aa", promptId: "S1:1:aa#p", workId: "S1:1:aa#w", start: now - 100, end: now - 80,
    prompt: "do the thing", src: "typed", mids: [], pending: false, summary: "more work", reply: "",
    tid: "fork-S1:1:aa", uuid: "u-S1:1:aa", workUuid: "w-S1:1:aa", replyUuid: "r-S1:1:aa", cont,
  });
  // a CONTINUATION piece adds a bar but no dot
  const pCont = new TimelinePanel(makeNode("div"));
  const dCont: any = synthData();
  dCont.turns.S1.push(piece(true));
  pCont.data = dCont;
  assert.doesNotThrow(() => pCont.draw());
  assert.equal(promptDots(pCont), before, "a post-sleep continuation piece draws no extra prompt dot");
  // the SAME piece without the flag DOES add a dot — proves the test is meaningful, not vacuous
  const pReal = new TimelinePanel(makeNode("div"));
  const dReal: any = synthData();
  dReal.turns.S1.push(piece(false));
  pReal.data = dReal;
  assert.doesNotThrow(() => pReal.draw());
  assert.equal(promptDots(pReal), before + 1, "a non-cont bar (a real prompt) adds exactly one prompt dot");
});

// Direct hover push (setHover) + nonce gate — the fast path that skips the file→watch→rebuild.
test("setHover applies by atom ids and gates on nonce (stale push ignored, clear works)", () => {
  const panel = new TimelinePanel(makeNode("div"));
  panel.data = synthData();
  panel.setHover({ ids: ["S1:1:aa#w"], nonce: 5 });
  assert.deepEqual(panel._hover && panel._hover.ids, ["S1:1:aa#w"]);
  assert.equal(panel._hoverNonce, 5);
  panel.setHover({ ids: ["S2:1:cc#p"], nonce: 4 });          // older → ignored
  assert.deepEqual(panel._hover.ids, ["S1:1:aa#w"]);
  panel.setHover({ ids: ["S2:1:cc#p"], nonce: 6 });          // newer → applied
  assert.deepEqual(panel._hover.ids, ["S2:1:cc#p"]);
  panel.setHover({ ids: null, nonce: 7 });                   // clear
  assert.equal(panel._hover, null);
  assert.equal(panel._hoverNonce, 7);
});

test("a feed-card hover PERSISTS across periodic pushes — hover:null/absent doesn't clear it (the user 2026-06-27)", () => {
  const panel: any = new TimelinePanel(makeNode("div"));
  panel.update(synthData());
  panel.setHover({ ids: ["S1:1:aa#w"], nonce: 5 });          // the direct feed-card hover push lights segments
  assert.ok(panel._hover && panel._hover.ids.length, "hover set");
  panel.update({ ...synthData(), hover: null });             // a periodic timeline push carries hover:null …
  assert.ok(panel._hover && panel._hover.ids.length, "… and must NOT clear the highlight (the half-second fade)");
  panel.update(synthData());                                 // hover absent entirely → still intact
  assert.ok(panel._hover && panel._hover.ids.length, "an absent hover leaves it intact too");
  panel.update({ ...synthData(), hover: { ids: [], nonce: 6 } });   // an explicit newer empty file-hover clears it
  assert.equal(panel._hover, null, "an explicit newer empty hover does clear it");
});

test("a file-poll hover cannot revert a fresher direct push (nonce gate in update)", () => {
  const panel = new TimelinePanel(makeNode("div"));
  const data: any = synthData();
  panel.update(data);                                        // no hover yet
  panel.setHover({ ids: ["S1:1:aa#w"], nonce: 9 });          // fresh push
  panel.update({ ...data, hover: { ids: ["S2:1:cc#p"], nonce: 8 } });   // OLDER poll → ignored
  assert.deepEqual(panel._hover.ids, ["S1:1:aa#w"]);
  panel.update({ ...data, hover: { ids: ["S2:1:cc#p"], nonce: 10 } });  // NEWER poll → wins
  assert.deepEqual(panel._hover.ids, ["S2:1:cc#p"]);
});

// Freeze-on-hover: a tooltip pauses live-follow only when we were pinned (following now).
const fakeEv = () => ({ clientX: 100, clientY: 100, currentTarget: makeNode("rect") });

test("freeze-on-hover: showTip pauses live-follow when pinned; hideTip resumes (deferred)", async () => {
  const panel = new TimelinePanel(makeNode("div"));
  panel.update(synthData());
  assert.equal(panel._pinned, true, "starts pinned (following now)");
  panel.showTip("<div>tip</div>", fakeEv());
  assert.equal(panel._frozeFromPin, true);
  assert.equal(panel._pinned, false, "live-follow paused while hovering");
  assert.equal(panel._holdReal, panel.data.now, "held at the now-edge captured at hover-start");
  panel.hideTip();
  assert.equal(panel._frozeFromPin, true, "resume is DEFERRED — still frozen right after hideTip");
  await new Promise((r) => setTimeout(r, 60));   // let the grace-window timer fire
  assert.equal(panel._frozeFromPin, false);
  assert.equal(panel._pinned, true, "live-follow resumed after the grace window");
});

test("freeze-on-hover: a quick glyph→glyph handoff keeps the freeze (no mid-handoff jump)", async () => {
  const panel = new TimelinePanel(makeNode("div"));
  panel.update(synthData());
  panel.showTip("<div>bar</div>", fakeEv());
  assert.equal(panel._frozeFromPin, true);
  panel.hideTip();                              // leaving the bar — resume is SCHEDULED
  panel.showTip("<div>dot</div>", fakeEv());    // …but the dot grabs it before the grace elapses
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(panel._frozeFromPin, true, "still frozen — the handoff cancelled the resume");
  assert.equal(panel._pinned, false, "timeline did not resume/jump mid-handoff");
});

test("freeze-on-hover does NOT fire (or snap to now) when the user has panned into history", () => {
  const panel = new TimelinePanel(makeNode("div"));
  panel.update(synthData());
  panel._pinned = false;                                     // user panned back
  panel.showTip("<div>tip</div>", fakeEv());
  assert.equal(panel._frozeFromPin, false, "no freeze when already unpinned");
  panel.hideTip();
  assert.equal(panel._pinned, false, "un-hover must NOT yank a history-browsing user to now");
});

// The restart ↻ button MOVED to the feed's top-right next to the ⛭ gear (the user 2026-06-17) — off the
// timeline's bottom-left. So the timeline controls no longer embed it (the feed gear holds it now).
test("the timeline controls no longer embed a kernel-restart button (it moved to the feed gear)", () => {
  const panel = new TimelinePanel(makeNode("div"));
  const kids = panel.controls.children;
  const btn = kids.find((c: any) => c.tag === "button" && c.getAttribute("title") === "Restart the romp kernel");
  assert.equal(btn, undefined, "the restart button moved to the feed's top-right, beside the ⛭ gear");
});

// The settings gear was MERGED into the feed's top-right ⛭ (the user 2026-06-16): one gear now holds the
// compact toggle + version info. So the timeline controls NO LONGER embed a settings gear — only the
// restart button + usage bars remain at the bottom-left.
test("the timeline controls no longer embed a settings gear (merged into the feed's ⛭)", () => {
  const panel = new TimelinePanel(makeNode("div"));
  const kids = panel.controls.children;
  const gear = kids.find((c: any) => c.tag === "button" && c.getAttribute("title") === "Settings");
  assert.equal(gear, undefined, "the timeline's settings gear moved to the feed's top-right ⛭");
});

// "collapse idle gaps" moved from the timeline toolbar into the Settings dialog's Timeline section (the user
// 2026-06-25). So the toolbar no longer hosts that checkbox, and the view reads it from the shared setting.
test("the timeline controls no longer embed a 'collapse gaps' checkbox (moved to the Settings dialog)", () => {
  const panel = new TimelinePanel(makeNode("div"));
  const texts: string[] = [];
  const walk = (n: any) => { if (n && n.textContent) texts.push(n.textContent); (n && n.children || []).forEach(walk); };
  panel.controls.children.forEach(walk);
  assert.ok(!texts.some((t) => t.includes("collapse gaps")), "the collapse-gaps checkbox moved to Settings → Timeline");
});

test("the timeline reads collapse-gaps from the shared romp:settings (live-synced via the storage event)", () => {
  const src = require("node:fs").readFileSync(viewPath, "utf8");
  assert.match(src, /this\._collapseGaps = JSON\.parse\(raw\)\.collapseGaps !== false/);   // constructor read
  assert.match(src, /e\.key !== 'romp:settings'\) return;[\s\S]*?collapseGaps !== false/);   // storage live-sync
});

// Freeze-on-hover must actually STOP the edge (the user 2026-06-13, who reported the timeline not stopping when they hover).
test("freeze-on-hover also fires under 🔒 lock-to-now, and never marks offDirty", () => {
  const panel = new TimelinePanel(makeNode("div"));
  panel.update(synthData());
  panel._lockNow = true; panel._pinned = true;
  panel.showTip("<div>tip</div>", fakeEv());
  assert.equal(panel._frozeFromPin, true, "lock must NOT block the hover-freeze");
  assert.equal(panel._offDirty, false, "freeze must not mark offDirty, or the next poll jumps the edge to the new now");
});

test("freeze-on-hover: _liveNow holds at the hover instant so open bars + pending stop advancing", () => {
  const panel = new TimelinePanel(makeNode("div"));
  panel.data = { now: 5000 } as any;
  panel._frozeFromPin = true; panel._holdReal = 4000;
  assert.equal(panel._liveNow(), 4000, "frozen → _holdReal (the hover instant), not the advancing data.now");
});

// Round 2: holding the EDGE wasn't enough — update() re-laid-out the SVG every poll (new events +
// recompressed gaps shift x), which read as an intermittent jump. While a tooltip is up, update() must
// buffer the data but skip the redraw; hideTip paints the catch-up. (the user 2026-06-13)
test("update() keeps a still snapshot while a tooltip is up; hideTip paints the catch-up (deferred)", async () => {
  const panel = new TimelinePanel(makeNode("div"));
  panel.update(synthData());                       // initial real layout
  let draws = 0; panel.draw = () => { draws++; };  // count further redraws
  panel.tip.classList.add("show");                 // a tooltip is showing
  const d2 = synthData();
  panel.update(d2);
  assert.equal(draws, 0, "a poll while a tooltip is up must NOT redraw (the re-layout was the jump)");
  assert.equal(panel.data, d2, "the fresh data is still buffered for the catch-up");
  assert.equal(panel._dirtyWhileTip, true);
  panel.hideTip();
  await new Promise((r) => setTimeout(r, 60));      // the catch-up repaint is deferred with the unfreeze
  assert.ok(draws >= 1, "hideTip paints the buffered data (one catch-up)");
  assert.equal(panel._dirtyWhileTip, false);
});

// Mouse model (the user 2026-06-22): a plain VERTICAL wheel scrolls the panel natively (NOT zoom), PINCH
// (ctrl+wheel) zooms (honors 🔒lock), a HORIZONTAL wheel pans (honors lock), click-drag=pan (breaks 🔒lock),
// vertical drag=reorder. These drive the real handlers through the DOM shim and assert the state moves.
function wheelEv(over: any) { return { deltaX: 0, deltaY: 0, ctrlKey: false, clientX: 700, clientY: 200, preventDefault() {}, ...over }; }
function mouseEv(over: any) { return { button: 0, clientX: 500, clientY: 200, preventDefault() {}, ...over }; }

test("onWheel: plain vertical does NOT zoom (scrolls natively); pinch zooms; horizontal pans (the user 2026-06-22)", () => {
  const panel = new TimelinePanel(makeNode("div"));
  panel.update(synthData());
  const w0 = panel.winSec();
  panel.onWheel(wheelEv({ deltaY: 20 }));                 // plain vertical → NOT hijacked → no zoom (panel scrolls natively)
  assert.equal(panel.winSec(), w0, "plain vertical wheel leaves the zoom window alone");
  panel.onWheel(wheelEv({ deltaY: 20, ctrlKey: true }));  // pinch (ctrl+wheel) → zoom (window widens)
  assert.notEqual(panel.winSec(), w0, "pinch zooms the window");
  const off0 = panel.offSec();
  panel.onWheel(wheelEv({ deltaX: -40, deltaY: 0 }));     // horizontal → pan into history (offset grows)
  assert.ok(panel.offSec() > off0, "horizontal wheel pans (offset moves off now)");
});

test("onWheel: pinch-zoom HONORS 🔒 lock (right edge stays at now)", () => {
  const panel = new TimelinePanel(makeNode("div"));
  panel.update(synthData());
  panel._setLock(true);
  panel.onWheel(wheelEv({ deltaY: 20, ctrlKey: true }));   // pinch → zoom; the lock keeps the offset at 0
  assert.equal(panel.offSec(), 0, "locked zoom keeps offset 0 — edge pinned at now");
});

test("onWheel: a HORIZONTAL wheel ZOOMS when 🔒locked to now (nowhere to pan) (the user 2026-06-22)", () => {
  const panel = new TimelinePanel(makeNode("div"));
  panel.update(synthData());
  panel._setLock(true);
  const w0 = panel.winSec();
  // leftward (toward the past) → zoom OUT (window widens), right edge stays pinned at now
  panel.onWheel(wheelEv({ deltaX: -40, deltaY: 0 }));
  assert.ok(panel.winSec() > w0, "locked horizontal wheel (toward past) widens the window — it zoomed, not panned");
  assert.equal(panel.offSec(), 0, "the lock keeps the right edge at now — no pan");
  // rightward (toward now) → zoom IN (window narrows again)
  const w1 = panel.winSec();
  panel.onWheel(wheelEv({ deltaX: 40, deltaY: 0 }));
  assert.ok(panel.winSec() < w1, "locked horizontal wheel (toward now) narrows the window — zoom in");
  assert.equal(panel.offSec(), 0, "still pinned at now");
});

test("onWheel pans/zooms only over the PLOT, not the gutter controls (the user 2026-06-27)", () => {
  const panel: any = new TimelinePanel(makeNode("div"));
  panel.update(synthData());
  const off0 = panel.offSec();
  // over the gutter (small clientX → svgX < the plot's left edge g.ml): a horizontal wheel must NOT pan,
  // and must NOT preventDefault (so it falls through to the lane control / native)
  let prevented = false;
  panel.onWheel(wheelEv({ deltaX: -40, clientX: 12, preventDefault() { prevented = true; } }));
  assert.equal(panel.offSec(), off0, "a horizontal wheel over the gutter does not pan");
  assert.equal(prevented, false, "and it is left to the control/native (no preventDefault)");
  // over the plot: the same wheel DOES pan (leftward → into the past, so the offset grows off the now-edge)
  panel.onWheel(wheelEv({ deltaX: -40, clientX: 820 }));
  assert.notEqual(panel.offSec(), off0, "the same wheel over the plot pans");
});

test("click-drag pan BREAKS 🔒 lock and unpins", () => {
  const panel = new TimelinePanel(makeNode("div"));
  panel.update(synthData());
  panel._setLock(true); panel._pinned = true;
  const sid = panel._vis[0].id;
  panel._beginDrag(sid, mouseEv({ clientX: 500, clientY: 200 }));
  panel._dragMove(mouseEv({ clientX: 440, clientY: 202 }));   // horizontal-dominant → pan
  assert.equal(panel._lockNow, false, "a pan-drag turns OFF the lock");
  assert.equal(panel._pinned, false, "a pan-drag unpins (edge leaves now)");
  panel._dragUp(mouseEv({ clientX: 440, clientY: 202 }));
});

test("click-drag pans grab-the-content style: drag right → into the past, drag left → toward now", () => {
  const panel: any = new TimelinePanel(makeNode("div"));
  panel.update(synthData());
  const sid = panel._vis[0].id;
  panel._offSec = 1000; panel._pinned = false;                    // start panned back so we can move either way
  panel._beginDrag(sid, mouseEv({ clientX: 400, clientY: 200 }));
  panel._dragMove(mouseEv({ clientX: 600, clientY: 201 }));       // drag RIGHT
  const afterRight = panel.offSec();
  panel._dragUp(mouseEv({ clientX: 600, clientY: 201 }));
  assert.ok(afterRight > 1000, "drag right grows the offset (reveals earlier time)");
  panel._offSec = 1000; panel._pinned = false;
  panel._beginDrag(sid, mouseEv({ clientX: 600, clientY: 200 }));
  panel._dragMove(mouseEv({ clientX: 400, clientY: 201 }));       // drag LEFT
  const afterLeft = panel.offSec();
  panel._dragUp(mouseEv({ clientX: 400, clientY: 201 }));
  assert.ok(afterLeft < 1000, "drag left shrinks the offset (toward now)");
});

test("a committed drag shows the closed-fist (grabbing) cursor over the whole plot, cleared on drop", () => {
  const panel: any = new TimelinePanel(makeNode("div"));
  panel.update(synthData());
  const sid = panel._vis[0].id;
  panel._beginDrag(sid, mouseEv({ clientX: 500, clientY: 200 }));
  assert.equal(panel.wrap.classList.contains("tl-grabbing"), false, "not grabbing until movement commits");
  panel._dragMove(mouseEv({ clientX: 440, clientY: 202 }));       // commit (pan)
  assert.equal(panel.wrap.classList.contains("tl-grabbing"), true, ".tl-grabbing forces grabbing over every descendant");
  panel._dragUp(mouseEv({ clientX: 440, clientY: 202 }));
  assert.equal(panel.wrap.classList.contains("tl-grabbing"), false, "cleared on drop");
});

test("vertical click-drag reorders the lane (not pan), leaving the lock alone", () => {
  // Persistence MUST route through the host hook here: without it, _persistOrder's Obsidian-desktop
  // fallback wrote the REAL ~/.local/state/romp/session-order.json from this very test — every
  // `npm test` wiped the user's tab order to ["S2","S1"], the long-mysterious "tabs keep reordering
  // themselves" bug (caught by the order-audit log, 2026-07-02). The hook captures the write, and the
  // view additionally refuses the direct write outside Electron.
  const persisted: string[][] = [];
  (g as any).__rompTimelineWriteOrder = (o: string[]) => persisted.push(o);
  try {
    const panel = new TimelinePanel(makeNode("div"));
    panel.update(synthData());
    const lockBefore = panel._lockNow;
    const sid = panel._vis[0].id;
    panel._beginDrag(sid, mouseEv({ clientX: 500, clientY: 200 }));
    panel._dragMove(mouseEv({ clientX: 502, clientY: 270 }));   // vertical-dominant → reorder
    assert.equal(panel._drag.mode, "row", "vertical drag → reorder mode");
    assert.equal(panel._lockNow, lockBefore, "reorder must not touch the lock");
    panel._dragUp(mouseEv({ clientX: 502, clientY: 270 }));
    for (const o of persisted) assert.ok(Array.isArray(o), "order writes are captured by the hook, never disk");
  } finally {
    delete (g as any).__rompTimelineWriteOrder;
  }
});

test("_persistOrder without a host hook refuses the direct file write outside Electron", () => {
  const src = require("node:fs").readFileSync(viewPath, "utf8");
  assert.match(src, /if \(typeof process === 'undefined' \|\| !process\.versions \|\| !process\.versions\.electron\) return;/,
    "the direct session-order.json write is Electron-only (Obsidian desktop) — plain node (the test runner) must never touch the real state file");
});

// ── judging band (2026-06-17): a compact second timeline under the lanes, one row per summarizer
// judge, each mark coloured by the SESSION it acted on. Fed by data.judging = [{judge,sid,t,kind,text}].
function findAll(node: any, pred: (n: any) => boolean, acc: any[] = []): any[] {
  if (pred(node)) acc.push(node);
  for (const c of node.children || []) findAll(c, pred, acc);
  return acc;
}
// The band is gated on the GLOBAL Debug setting (romp:settings.debug, toggled in the feed gear). Drive
// it through the same localStorage key the view reads.
function setDebug(on: boolean) {
  g.localStorage.getItem = (k: string) => (k === "romp:settings" && on ? JSON.stringify({ debug: true }) : null);
}
test("judging band: with Debug mode on, data.judging renders a compact, labelled row per judge", () => {
  setDebug(true);
  const panel = new TimelinePanel(makeNode("div"));
  const base: any = synthData();
  const now = base.now;
  panel.data = { ...base, judging: [
    { judge: "captioner", sid: "S1", t: now - 200, kind: "segment", text: "did a thing" },
    { judge: "captioner", sid: "S1", t: now - 150, kind: "turn", text: "wrapped the turn" },  // merges with the above (same session, <gap)
    { judge: "planner", sid: "S2", t: now - 120, kind: "mint", text: "new goal" },
    { judge: "courier", sid: "S2", t: now - 80, kind: "plant", text: "handoff in" },
    { judge: "closer", sid: "S1", t: now - 30, kind: "close", text: "shipped it" },
  ] };
  assert.doesNotThrow(() => panel.draw(), "draw() must not throw with a judging band");
  for (const j of ["captioner", "archiver", "planner", "grouper", "closer", "distiller", "courier"])
    assert.ok(findText(panel.svg, j), `judge row '${j}' must be labelled in the gutter`);
  assert.ok(findText(panel.svg, "judges"), "the band carries a gutter heading");
  const cap = findAll(panel.svg, (n) => n.getAttribute && n.getAttribute("data-judge") === "captioner");
  assert.equal(cap.length, 1, "two adjacent same-session captions merge into ONE attention mark");
  assert.equal(cap[0].getAttribute("fill"), "#7aa2f7", "a mark is FILLED with the session it judged");
  // no per-bar outline (the user 2026-06-18) — the judge's colour lives on the row rail, not a redundant stroke
  assert.equal(cap[0].getAttribute("stroke"), undefined, "a mark has NO stroke (solid session-colour fill only)");
  assert.equal(findAll(panel.svg, (n) => n.getAttribute && n.getAttribute("data-judge") === "courier").length, 1);
});
test("judging band is gated on Debug mode: OFF by default hides it; Debug on draws it and grows the SVG", () => {
  setDebug(false);
  const panel = new TimelinePanel(makeNode("div"));
  const base: any = synthData();
  panel.data = { ...base, judging: [{ judge: "planner", sid: "S1", t: base.now - 50, kind: "mint", text: "g" }] };
  panel.draw();                                               // Debug off (default)
  assert.ok(!findText(panel.svg, "judges"), "no band heading while Debug is off");
  assert.equal(findAll(panel.svg, (n) => n.getAttribute && n.getAttribute("data-judge")).length, 0, "no judge marks drawn while off");
  const hOff = Number(panel.svg.getAttribute("height"));
  setDebug(true);
  panel.draw();                                               // a storage event from the gear would trigger this in the browser
  assert.ok(findText(panel.svg, "judges"), "Debug on reveals the band");
  assert.ok(Number(panel.svg.getAttribute("height")) > hOff, "the band adds height below the lanes");
  g.localStorage.getItem = () => null;                        // reset the shared mock
});

// (The per-window token grid was removed from the timeline controls at the user's request 2026-06-18;
// only the /usage rate-limit bars remain. Its render tests went with it.)

// Hover bodies: the prompt DOT shows the REQUEST (prompt); the activity BAR shows the WORK (the
// segment's own caption). They must differ — the bar used to show its own work caption mislabeled
// "request:", reading as a duplicate of the dot (the user 2026-06-18).
test("work-bar hover shows the work caption (summary), not mislabeled 'request:'", () => {
  const panel: any = new TimelinePanel(makeNode("div"));
  const done = panel.barBody({ summary: "Fixed the off-by-one", prompt: "fix the bug", reply: "" }, false);
  assert.match(done, /Fixed the off-by-one/, "the bar shows its own work caption");
  assert.doesNotMatch(done, /request:/, "a work period WITH a caption is not labeled 'request:'");
  const noCap = panel.barBody({ summary: "", prompt: "fix the bug", reply: "" }, false);
  assert.match(noCap, /request: /);                           // no caption yet, finished → the request, muted
  assert.match(noCap, /fix the bug/);
  const live = panel.barBody({ summary: "", prompt: "fix the bug", reply: "" }, true);
  assert.match(live, /working on: /, "ongoing with no caption → 'working on: <prompt>'");
});

test("prompt-dot hover shows the MESSAGE caption once ready, falling back to the raw prompt (the user 2026-06-19)", () => {
  const panel: any = new TimelinePanel(makeNode("div"));
  // message caption available → show it (a gist of the ask), NOT the verbose prompt and NOT the work summary
  assert.equal(panel.req({ prompt: "please fix the pagination bug across the whole table view", msgCaption: "the pagination bug", summary: "Fixed pagination" }),
               "the pagination bug", "the dot shows the MESSAGE caption, distinct from the bar's work summary");
  // intermediate (no message caption yet) → the raw prompt is the fallback; the work summary is the BAR's, not the dot's
  assert.equal(panel.req({ prompt: "fix the pagination bug", summary: "Fixed pagination" }), "fix the pagination bug",
               "until the message caption lands, fall back to the raw prompt — never the work summary");
  assert.equal(panel.req({}), "", "neither → empty");
});

// ── BARS-DEFER (the user 2026-06-25, who wanted everything else loaded and the bars loaded after): the kernel
// ships the timeline as TWO ws messages — {type:"data"} = the lanes SKELETON (sessions/status, no
// turns/judging/messages/nudges) which paints instantly, then {type:"bars"} = the heavy detail. update()
// renders the skeleton; applyBars() fills the bars; and a skeleton-only update must NOT blink the bars out.
function skeletonOf(full: any) {
  return { now: full.now, sessions: full.sessions, turns: {}, judging: [], messages: [], nudges: [],
           activeChat: null, focus: null, hover: null, usage: null };
}
test("applyBars fills the deferred bars onto a lanes-only skeleton, and draw() emits them", () => {
  const panel: any = new TimelinePanel(makeNode("div"));
  const full = synthData();
  panel.update(skeletonOf(full));                                    // the {type:"data"} lanes skeleton
  assert.equal(Object.keys(panel.data.turns).length, 0, "the skeleton paints lanes with no bars yet");
  panel.applyBars({ type: "bars", turns: full.turns, judging: [], messages: [], nudges: [], now: full.now });
  assert.deepEqual(panel.data.turns, full.turns, "applyBars merges the bars into the live data");
  assert.ok(panel.svg.children.length > 10, "the bars render after applyBars (a populated SVG)");
});
test("a skeleton-only update preserves the bars from the last applyBars (no per-push blink)", () => {
  const panel: any = new TimelinePanel(makeNode("div"));
  const full = synthData();
  panel.update(skeletonOf(full));
  panel.applyBars({ type: "bars", turns: full.turns, judging: [], messages: [], nudges: [], now: full.now });
  const next = skeletonOf(full); next.now = full.now + 1;           // a fresh push: lanes skeleton again
  panel.update(next);
  assert.deepEqual(panel.data.turns, full.turns, "the prior bars survive a lanes-only update (carried over)");
});

// A message SENT before the visible window used to clamp its start to the left edge and hug the
// sender's lane all the way to the crossing — reading as "sent at the window's start", a send time
// that never existed (the user 2026-08-06: a connector spanning the whole window, "a timing issue
// maybe?"). An off-window send now enters from the edge at the crossing track height: structurally a
// SINGLE-corner path (track horizontal → one turn → arrival), where an in-window send keeps the full
// elbow (two+ corners). The tooltip carries the true send time either way.
test("an off-window send enters at track height — never a sender-lane hug from the left edge", () => {
  const panel: any = new TimelinePanel(makeNode("div"));
  const d0: any = synthData();   // messages: [] infers never[] — the synthetic payload is untyped by design
  d0.sessions[0].color = "#f7768e";                                  // sender distinct, so its connector is findable
  d0.messages = [
    { id: "m-old", fromId: "S1", toId: "S2", from: "alpha", to: "beta",
      sent: d0.now - 500_000, exec: d0.now - 30, pending: false, summary: "sent long before the window" } as any,
    { id: "m-new", fromId: "S2", toId: "S1", from: "beta", to: "alpha",
      sent: d0.now - 40, exec: d0.now - 10, pending: false, summary: "sent inside the window" } as any,
  ];
  panel.update(d0);
  const paths: any[] = [];
  (function walk(n: any) { for (const c of n.children || []) { if (c.tag === "path") paths.push(c); walk(c); } })(panel.svg);
  const conns = paths.filter((p) => p._attrs && p._attrs.fill === "none" && p._attrs.opacity === 0.5);
  const oldConn = conns.find((p) => p._attrs.stroke === "#f7768e");
  const newConn = conns.find((p) => p._attrs.stroke === "#7aa2f7");
  assert.ok(oldConn, "the off-window message still draws its connector");
  assert.ok(newConn, "the in-window message draws too");
  const corners = (d: string) => (String(d).match(/Q /g) || []).length;
  assert.equal(corners(oldConn._attrs.d), 1, "off-window send: track entry + ONE corner up to the arrival");
  assert.ok(corners(newConn._attrs.d) >= 2, "in-window send keeps the full elbow from its true send point");
});

// RELAYED mail's landing binds to the recipient's true process turn in the MERGED view (the user
// 2026-08-06: a cross-host connector landed at the read-receipt time — the relay handoff — because the
// kernel-side binder never sees a remote lane's turns). The receipt now carries the remote's delivery
// mid; the view joins it against every lane's bar mids, including merged remote lanes.
test("a relayed message's exec re-binds to the recipient turn whose mids carry its dmid", () => {
  const panel: any = new TimelinePanel(makeNode("div"));
  const d0: any = synthData();   // messages: [] infers never[] — the synthetic payload is untyped by design
  d0.turns.S2[0].mids = ["dm-remote-1"];                             // the recipient turn knows its delivery mid
  d0.messages = [{ id: "px-1", dmid: "dm-remote-1", fromId: "S1", toId: "S2", from: "alpha", to: "beta",
                   sent: d0.now - 250, exec: d0.now - 20, pending: false, summary: "relayed" }];
  panel.update(d0);
  assert.equal(panel.data.messages[0].exec, d0.turns.S2[0].start,
    "the landing is the recipient turn's start, not the receipt time");
  const d1: any = synthData();
  d1.messages = [{ id: "m-local", fromId: "S1", toId: "S2", from: "alpha", to: "beta",
                   sent: d1.now - 250, exec: d1.now - 20, pending: false, summary: "no join" }];
  panel.update(d1);
  assert.equal(panel.data.messages[0].exec, d1.now - 20, "no matching mids → the receipt time stands");
});
