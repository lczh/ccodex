// The card-age provenance popover (the user 2026-07-27): the header's "Nm ago" stamps the card's
// NEWEST event — a completed card's age is when it was marked done — so hovering it must tell where
// the thread CAME from: started when, each sub-item with the time it landed, and what the visible
// stamp itself marks. Emits structured {when, what} rows (the native-title first cut was dense and
// unaligned — same day) that the feed renders as an aligned popover. EXECUTES ./provenance directly;
// the feed wiring + CSS vocabulary are source-pinned below.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { provenanceRows, provenanceGroupRows, rootStart, type ProvItem, type ProvFmt } from "./provenance";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

// deterministic formatters: the real relAge/clockHM/logPhrase live in feed.ts (pinned there); this
// module owns only the story's structure
const F: ProvFmt = {
  rel: (s) => Math.round(s / 60) + "m ago",
  clock: (t) => "@" + t,
  phrase: (r) => "[" + r.kind + "]",
};
const NOW = 10_000;

function item(over: Partial<ProvItem> = {}): ProvItem {
  return {
    itemId: "root", t: 9_400, column: "completed",
    // NEWEST-subtree-activity FIRST, like the kernel's flatten actually ships it (_fsubmax, reverse) —
    // the old ascending fixture was why the shuffled popover was never caught (the user 2026-08-13)
    tree: [
      { id: "root", text: "ship the notes-api", status: "done", t: 1_000, last: 9_400 },
      { id: "s3", text: "docs pass", status: "open", t: 8_800, last: 8_800 },
      { id: "s2", text: "ask about the schema", status: "question", t: 2_200, last: 7_000, mt: 7_000 },
      { id: "s1", text: "write the parser", status: "done", t: 1_600, last: 5_000, mt: 5_200 },
    ],
    ...over,
  };
}

test("the story: started at the root's mint, each sub at ITS time, ONE clock throughout, the stamp last", () => {
  const rows = provenanceRows(item(), NOW, F);
  assert.deepEqual(rows[0], { when: "150m ago · @1000", what: "started", t: 1_000, kind: "start" });
  assert.deepEqual(rows[1], { when: "80m ago · @5200", what: "✓ write the parser", t: 5_200, kind: "sub" },
    "a resolved sub stamps where it RESOLVED (mt) — the newest-first wire order re-sorts to the clock");
  assert.deepEqual(rows[2], { when: "50m ago · @7000", what: "⏸ ask about the schema", t: 7_000, kind: "sub" });
  assert.deepEqual(rows[3], { when: "20m ago · @8800", what: "· docs pass", t: 8_800, kind: "sub" },
    "an open sub stamps its mint (nothing resolved yet)");
  assert.deepEqual(rows[4], { when: "10m ago · @9400", what: "marked done", t: 9_400, kind: "stamp" },
    "the visible age is named for what it marks");
});

test("events and subs INTERLEAVE on the clock — never section-by-section (the user 2026-08-13)", () => {
  // the observed shuffle: root events ran up to now while the ✓ block jumped back a day — a sub whose
  // resolution PRECEDES a root event must print before it
  const it = item();
  it.tree[0].log = [{ kind: "block", src: "romp", at: 6_000 }, { kind: "unblock", src: "romp", at: 8_000 }];
  const rows = provenanceRows(it, NOW, F);
  assert.deepEqual(rows.map((r) => [r.kind, r.t]),
    [["start", 1_000], ["sub", 5_200], ["event", 6_000], ["sub", 7_000], ["event", 8_000],
     ["sub", 8_800], ["stamp", 9_400]],
    "one story, one clock: strict chronological, the stamp pinned last");
});

test("the final row matches the column: blocked / last update", () => {
  assert.equal(provenanceRows(item({ column: "needs_input" }), NOW, F).at(-1)!.what, "blocked");
  assert.equal(provenanceRows(item({ column: "working" }), NOW, F).at(-1)!.what, "last update");
});

test("the root's verdict rows land at their own times, in the feed's outcome words", () => {
  const it = item();
  it.tree[0].log = [{ kind: "block", src: "romp", at: 3_000 }, { kind: "unblock", src: "romp", evT: 4_000 }];
  const rows = provenanceRows(it, NOW, F);
  assert.deepEqual(rows[1], { when: "117m ago · @3000", what: "[block]", t: 3_000, kind: "event" });
  assert.deepEqual(rows[2], { when: "100m ago · @4000", what: "[unblock]", t: 4_000, kind: "event" },
    "evT is the time-nav fallback when `at` is absent");
  assert.equal(rows[3].what, "✓ write the parser", "the first sub follows — its mt (5200) postdates both events");
});

test("cleared subs stay out; a huge tree caps at 8 with an honest remainder", () => {
  const it = item();
  it.tree.find((n) => n.id === "s1")!.cleared = true;   // by id — the fixture ships newest-first now
  assert.ok(!provenanceRows(it, NOW, F).some((r) => r.what.includes("write the parser")),
    "a cleared sub is not provenance");
  const big = item({
    tree: [{ id: "root", text: "r", status: "done", t: 1_000, last: 9_000 } as any].concat(
      Array.from({ length: 11 }, (_, i) => ({ id: "n" + i, text: "sub " + i, status: "open", t: 2_000 + i, last: 2_000 + i } as any))),
  });
  const rows = provenanceRows(big, NOW, F);
  assert.equal(rows.length, 1 + 8 + 1 + 1, "start + 8 subs + remainder + stamp row");
  assert.deepEqual(rows[9], { when: "", what: "…and 3 more", t: 0, kind: "more" }, "no silent truncation");
});

test("rootStart falls back: earliest tree mint without a root row, the card's t on an empty tree", () => {
  assert.equal(rootStart(item()), 1_000);
  assert.equal(rootStart(item({ itemId: "elsewhere" })), 1_000, "no root row → earliest mint");
  assert.equal(rootStart(item({ tree: [] })), 9_400, "provisional cards have no tree at all");
});

test("a group's story is the fold: earliest member start, the member count, the group stamp", () => {
  const rows = provenanceGroupRows([5_000, 3_000, 7_000], 9_000, NOW, F);
  assert.deepEqual(rows[0], { when: "117m ago · @3000", what: "started", t: 3_000, kind: "start" });
  assert.deepEqual(rows[1], { when: "", what: "3 cards from one prompt", t: 0, kind: "sub" });
  assert.deepEqual(rows[2], { when: "17m ago · @9000", what: "last update", t: 9_000, kind: "stamp" });
});

test("the feed wires the popover beside every age write — card, group card, both modal headers", () => {
  assert.match(FEED, /wireAgeTip\(a\._time, \(\) => provenanceRows\(it, hostNow, PROV_FMT\)\);/);
  assert.match(FEED, /wireAgeTip\(a\._time, \(\) => provenanceGroupRows\(g\.members\.map\(rootStart\), g\.t, hostNow, PROV_FMT\)\);/);
  assert.match(FEED, /wireAgeTip\(ageEl, \(\) => provenanceRows\(it, hostNow, PROV_FMT\)\);/);
  assert.match(FEED, /wireAgeTip\(ageEl, \(\) => provenanceGroupRows\(grp\.members\.map\(rootStart\), grp\.t, hostNow, PROV_FMT\)\);/);
  // …with the same vocabulary the card itself renders in
  assert.match(FEED, /const PROV_FMT: ProvFmt = \{ rel: relAge, clock: clockHM, phrase: logPhrase \};/);
});

test("every timed line wears its own recency colour — time AND text, the chat tab-tip treatment", () => {
  // the user 2026-07-27: colour the timestamps and their items the way the chat tab hover does. The
  // feed tints the whole row (and the when cell explicitly, beating its dim class) from the SHARED
  // age-color ramp; the un-timed remainder row keeps the panel's dim default (t: 0 guards it).
  assert.match(FEED, /import \{ ageColorReadable \} from "\.\/age-color";/);
  assert.match(FEED, /if \(r\.t > 0\) \{ const c = ageColorReadable\(hostNow - r\.t\); row\.style\.color = c; w\.style\.color = c; \}/);
});

test("the popover is aligned, styled in the feed's own vocabulary, and can never eat a click", () => {
  assert.match(CSS, /\.age-tip-when \{ flex: 0 0 auto; min-width: 118px; text-align: right; color: var\(--dim\);/,
    "one right-aligned dim time column — the alignment the title tooltip couldn't give");
  assert.match(CSS, /font-variant-numeric: tabular-nums/, "digits line up down the column");
  assert.match(CSS, /#age-tip \{[^}]*pointer-events: none/s, "hover chrome, never a click target");
  assert.match(CSS, /\.age-tip-row\.stamp \{ margin-top: 4px; padding-top: 4px; border-top:/,
    "the closing stamp line is its own section under a hairline");
  // the tip survives ordinary re-renders (cards update in place; unconditional hiding made it vanish
  // ~a second into every hover — the feed re-renders on every kernel push, the user 2026-07-27) and
  // hides only when the render actually tore its hovered stamp out of the DOM
  assert.match(FEED, /function pruneAgeTip\(\): void \{ if \(ageTipAnchor && !ageTipAnchor\.isConnected\) hideAgeTip\(\); \}/);
  assert.match(FEED, /pruneAgeTip\(\);   \/\/ drop the tip only if the render tore its hovered stamp out/);
  assert.doesNotMatch(FEED, /\n\s*hideAgeTip\(\);\s*\/\/ a re-render/, "the unconditional hide is gone");
});
