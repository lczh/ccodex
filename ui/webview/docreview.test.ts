// Doc review (the user 2026-08-14): highlight spans in a markdown doc, comment on them, submit the batch
// as ONE message into the session's composer. These pin the pure half — where an anchor lands, when it
// honestly refuses to guess, and the exact shape of the outgoing message.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { anchorFor, buildReviewMessage, docKey, DocComment } from "./docreview";

const DOC = [
  "# Rollout plan",                                            // 1
  "",                                                          // 2
  "The judge files a verdict per build, then the card moves.", // 3
  "",                                                          // 4
  "- **Stage one** ships behind `access-read-facts-from-ddb`", // 5
  "- See [the tracker](https://example.invalid/t) for dates",  // 6
  "",                                                          // 7
  "Values live in cache_key_prefix and never expire.",         // 8
].join("\n");

test("a plain selection anchors on its source line", () => {
  const a = anchorFor(DOC, "files a verdict per build");
  assert.equal(a.line, 3);
  assert.equal(a.quote, "files a verdict per build");
});

test("a span selected out of RENDERED text still finds its marked-up source line", () => {
  // The reader shows "Stage one ships behind access-read-facts-from-ddb" — no asterisks, no backticks.
  const a = anchorFor(DOC, "Stage one ships behind access-read-facts-from-ddb");
  assert.equal(a.line, 5);
});

test("a link's label anchors on the line carrying the link", () => {
  const a = anchorFor(DOC, "See the tracker for dates");
  assert.equal(a.line, 6);
});

test("a heading anchors despite its # marker", () => {
  assert.equal(anchorFor(DOC, "Rollout plan").line, 1);
});

test("snake_case survives: underscores are not stripped from the source", () => {
  assert.equal(anchorFor(DOC, "cache_key_prefix").line, 8);
});

test("the selection's newlines and indentation are collapsed before matching", () => {
  const a = anchorFor(DOC, "  The judge\n   files a verdict  ");
  assert.equal(a.line, 3);
  assert.equal(a.quote, "The judge files a verdict");   // normalized, single-spaced
});

test("no match returns a text-only anchor, never a guessed line", () => {
  const a = anchorFor(DOC, "a sentence that is nowhere in this document at all");
  assert.equal(a.line, null);
  assert.equal(a.quote, "a sentence that is nowhere in this document at all");
});

test("an empty selection anchors nothing", () => {
  assert.deepEqual(anchorFor(DOC, "   \n  "), { quote: "", line: null });
});

test("a repeated span takes the first occurrence", () => {
  const src = "alpha beta\ngamma\nalpha beta";
  assert.equal(anchorFor(src, "alpha beta").line, 1);
});

test("a long selection falls back to its first words", () => {
  const src = "The judge files a verdict per build and then the card_id moves along.";
  // tail differs from the source (the user selected across a rendered footnote), head does not
  const a = anchorFor(src, "The judge files a verdict per build and then something else entirely happens");
  assert.equal(a.line, 1);
});

function c(over: Partial<DocComment>): DocComment {
  return { id: "x", quote: "q", line: 1, body: "b", ts: 0, ...over };
}

test("the message numbers every comment and carries line + quote", () => {
  const out = buildReviewMessage("docs/plan.md", [
    c({ quote: "files a verdict per build", line: 3, body: "Should be per turn, not per build." }),
    c({ quote: "Rollout", line: 1, body: "Drop this section." }),
  ]);
  assert.equal(out,
    'Comments on docs/plan.md — all of them, one pass:\n\n' +
    '1. line 3 — "files a verdict per build"\n' +
    '   Should be per turn, not per build.\n\n' +
    '2. line 1 — "Rollout"\n' +
    '   Drop this section.\n');
});

test("a line-less anchor drops the line prefix rather than inventing one", () => {
  const out = buildReviewMessage("plan.md", [c({ quote: "somewhere", line: null, body: "fix" })]);
  assert.match(out, /^1\. "somewhere"$/m);
  assert.doesNotMatch(out, /line null|line undefined|line 0/);
});

test("a multi-line comment body keeps its lines, indented under the anchor", () => {
  const out = buildReviewMessage("plan.md", [c({ body: "first\nsecond" })]);
  assert.match(out, /\n {3}first\n {3}second\n/);
});

test("an over-long quote is truncated, not dumped whole", () => {
  const out = buildReviewMessage("plan.md", [c({ quote: "z".repeat(400) })]);
  const quoted = out.match(/"(z+…?)"/)![1];
  assert.ok(quoted.length <= 140, "quote should be capped, got " + quoted.length);
  assert.ok(quoted.endsWith("…"));
});

test("comments with an empty body are dropped, and an all-empty batch builds nothing", () => {
  assert.equal(buildReviewMessage("plan.md", [c({ body: "  " })]), "");
  const out = buildReviewMessage("plan.md", [c({ body: "  " }), c({ quote: "real", body: "change it" })]);
  assert.match(out, /^1\. line 1 — "real"$/m);   // the survivor renumbers from 1
});

test("no romp vocabulary reaches the agent — it has never heard of romp (CLAUDE.md)", () => {
  const out = buildReviewMessage("docs/plan.md", [c({ body: "reword this" })]);
  for (const word of [/\bromp\b/i, /\bcard\b/i, /\bboard\b/i, /\bgoal\b/i, /\bcolumn\b/i,
                      /\bnudge\b/i, /\bbatch\b/i, /\bsubmit(ted)?\b/i, /\breview(er)?\b/i]) {
    assert.doesNotMatch(out, word, "outgoing message must not say " + word);
  }
});

test("comments are keyed per session AND per file, so two open docs keep separate batches", () => {
  assert.notEqual(docKey("s1", "a.md"), docKey("s1", "b.md"));
  assert.notEqual(docKey("s1", "a.md"), docKey("s2", "a.md"));
});
