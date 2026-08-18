import { test } from "node:test";
import assert from "node:assert";
import { markerLabel, dayContext } from "./time-marker";

// All epochs below are built from local-time components so the test is timezone-agnostic
// (markerLabel reads getHours()/getMinutes()/getDate() in local time, matching the browser).
const at = (y: number, mo: number, d: number, h: number, mi: number, s = 0) =>
  Math.floor(new Date(y, mo, d, h, mi, s).getTime() / 1000);

// "now" anchored so that 2026-06-12 is today.
const NOW = new Date(2026, 5, 12, 12, 0, 0).getTime();

test("markerLabel: first timed turn (no previous) shows HH:MM", () => {
  const r = markerLabel(at(2026, 5, 12, 11, 3), null, NOW);
  assert.deepEqual(r, { text: "11:03", day: false, hm: "11:03", date: "" });
});

test("markerLabel: a run of same-minute turns shows the stamp only on the first", () => {
  const first = at(2026, 5, 12, 11, 3, 5);
  const second = at(2026, 5, 12, 11, 3, 40); // same minute, later seconds
  assert.equal(markerLabel(first, null, NOW).text, "11:03");
  assert.equal(markerLabel(second, first, NOW).text, ""); // suppressed
});

test("markerLabel: a suppressed turn still carries its HH:MM in hm (for the sticky rail stamp)", () => {
  const first = at(2026, 5, 12, 11, 3, 5);
  const second = at(2026, 5, 12, 11, 3, 40);
  const r = markerLabel(second, first, NOW);
  assert.equal(r.text, "");      // not shown by the minute rule
  assert.equal(r.hm, "11:03");   // but available to reveal
});

test("markerLabel: the stamp reappears when the minute changes", () => {
  const prev = at(2026, 5, 12, 11, 3, 50);
  const next = at(2026, 5, 12, 11, 4, 1);
  assert.deepEqual(markerLabel(next, prev, NOW), { text: "11:04", day: false, hm: "11:04", date: "" });
});

test("markerLabel: same HH:MM on a different day is NOT deduped", () => {
  const prev = at(2026, 5, 11, 11, 3); // yesterday 11:03
  const today = at(2026, 5, 12, 11, 3); // today 11:03
  assert.equal(markerLabel(today, prev, NOW).text, "11:03");
});

test("markerLabel: first turn of a past day shows the date, emphasised", () => {
  const prev = at(2026, 5, 10, 9, 0);
  const r = markerLabel(at(2026, 5, 11, 9, 0), prev, NOW); // 2026-06-11 = yesterday
  assert.deepEqual(r, { text: "Yesterday · 09:00", day: true, hm: "09:00", date: "Yesterday" });
});

test("markerLabel: a past day within a week shows the weekday", () => {
  const r = markerLabel(at(2026, 5, 8, 14, 30), null, NOW); // 2026-06-08 is a Monday
  assert.equal(r.day, true);
  assert.equal(r.text, "Mon · 14:30");
});

test("markerLabel: a past day older than a week shows month + day", () => {
  const r = markerLabel(at(2026, 4, 20, 8, 5), null, NOW); // 2026-05-20
  assert.deepEqual(r, { text: "May 20 · 08:05", day: true, hm: "08:05", date: "May 20" });
});

test("markerLabel: a new day still shows the date even when same minute as prev day", () => {
  const prev = at(2026, 5, 10, 11, 3); // older day, same HH:MM
  const r = markerLabel(at(2026, 5, 11, 11, 3), prev, NOW);
  assert.equal(r.day, true);
  assert.equal(r.text, "Yesterday · 11:03");
});

// --- a stamp marks a time CHANGE and nothing else (the user 2026-07-23) ---
// The chooseStamps() spacing pass that used to re-reveal suppressed same-minute stamps every ~6 rows is
// gone: the sticky rail stamp always shows the time at the top of the view, so repeating a time already
// shown was noise. These pin that the suppression itself still holds across a long same-minute run.

test("a long same-minute run stamps only the first turn — no repeats down the rail", () => {
  const base = at(2026, 5, 12, 11, 3);
  const labels = [0, 5, 12, 20, 31, 44, 58].map((s, i, arr) =>
    markerLabel(base + s, i === 0 ? null : base + arr[i - 1], NOW));
  assert.equal(labels[0].text, "11:03", "the first turn of the minute carries the stamp");
  assert.deepEqual(labels.slice(1).map((l) => l.text), ["", "", "", "", "", ""],
    "every later turn in the same minute stays suppressed, however many there are");
  assert.ok(labels.every((l) => l.hm === "11:03"), "but each still carries its time for the sticky to read");
});

test("the stamp returns exactly when the minute changes", () => {
  const t1103 = at(2026, 5, 12, 11, 3), t1104 = at(2026, 5, 12, 11, 4);
  assert.equal(markerLabel(t1104, t1103, NOW).text, "11:04", "a new minute is a real change → stamp it");
  assert.equal(markerLabel(t1104 + 30, t1104, NOW).text, "", "still 11:04 → suppressed again");
});

// ── dayContext: the top-of-view day label (the user 2026-08-17) ──
// Real behavior, not source pins: the vocabulary the label speaks, midnight-relative (calendar days,
// never 24h buckets — 23:50 yesterday is "Yesterday" ten minutes later).
test("dayContext speaks the relative-day vocabulary, midnight-relative", () => {
  const now = new Date(2026, 7, 17, 10, 0, 0).getTime();          // Mon Aug 17 2026, 10:00 local
  const at = (y: number, mo: number, d: number, h = 12) => new Date(y, mo, d, h).getTime() / 1000;
  assert.equal(dayContext(at(2026, 7, 17, 1), now), "", "today → no label, even 9h ago");
  assert.equal(dayContext(at(2026, 7, 16, 23), now), "Yesterday", "23:00 yesterday, 11h ago — calendar, not 24h");
  assert.equal(dayContext(at(2026, 7, 15), now), "2 days ago");
  assert.equal(dayContext(at(2026, 7, 11), now), "6 days ago");
  assert.equal(dayContext(at(2026, 7, 10), now), "Last week", "7 days");
  assert.equal(dayContext(at(2026, 7, 4), now), "Last week", "13 days");
  assert.equal(dayContext(at(2026, 7, 3), now), "2 weeks ago", "14 days");
  assert.equal(dayContext(at(2026, 6, 22), now), "3 weeks ago", "26 days");
  assert.equal(dayContext(at(2026, 6, 15), now), "Jul 15", "past a month → the divider's own date form");
  assert.equal(dayContext(at(2025, 11, 30), now), "Dec 30 2025", "a different year says so");
});
