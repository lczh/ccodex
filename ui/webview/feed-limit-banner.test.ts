// A judge layer down on a USAGE LIMIT says so loudly (the user 2026-08-18, whose judges failed
// quietly into ~22,400 doomed retries over two days while the Fable window sat at 100%): the
// kernel ships the judge-limit latch on the feed payload, and the feed renders a compact banner
// above the columns — for a Fable-window exhaustion it offers switching analysis to Opus (cheaper
// per token); for a general exhaustion it states the account is full and when it resumes. The
// banner is built ONCE and updated in place (the click-safety rule), and the button acknowledges
// before the round-trip. Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const FEED = fs.readFileSync(path.join(UI, "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.join(UI, "feed.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const JUDGE = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "judge.py"), "utf8");

test("the kernel ships the latch and the judge gate writes it model-aware", () => {
  assert.match(KERNEL, /"judgeLimit": jd\._limit_down\(\),/);
  assert.match(JUDGE, /_buckets = \["five_hour", "seven_day"\] \+ \(\["fable"\] if "fable" in str\(model\)\.lower\(\) else \[\]\)/,
    "the gated buckets follow the CALL'S model — a fable pin gates on the fable window");
  assert.match(JUDGE, /_limit_mark\(_b, _lim\.get\("pct"\), _lim\.get\("resets_at"\), model\)/);
  assert.match(JUDGE, /_limit_clear\(\) {8,}# \.\.\.and the usage-limit latch|_limit_clear\(\)/, "a success clears it");
  assert.match(JUDGE, /jd\._USAGE_REFRESH_FN|_USAGE_REFRESH_FN = None/, "the idle-stale poke hook exists");
  assert.match(KERNEL, /jd\._USAGE_REFRESH_FN = getattr\(_sdk_backend, "refresh_usage", None\)/,
    "…and the kernel wires it (getattr: the hook is best-effort, so a stub backend can't break the build)");
});

test("the banner is build-once, acknowledges, and offers Opus only for the Fable window", () => {
  assert.match(FEED, /function ensureJudgeLimit\(\): HTMLElement/);
  assert.match(FEED, /let b = document\.getElementById\("judge-limit-banner"\);\s*\n\s*if \(b\) return b;/,
    "built once — the button survives re-renders (click-safety)");
  assert.match(FEED, /btn\.textContent = "Switching…";\s*\n\s*\/\/ ?.*|btn\.textContent = "Switching…";/,
    "acknowledges before the kernel round-trip");
  assert.match(FEED, /vscodeApi\?\.postMessage\(\{ type: "setJudgeModel", model: "opus" \}\)/);
  assert.match(FEED, /btn\.style\.display = fable \? "" : "none";/, "the switch offer is Fable-specific");
  assert.match(FEED, /the account's usage window is full/, "general exhaustion states it plainly");
  assert.match(FEED, /paintJudgeLimit\(\);   \/\/ the usage-limit banner above the columns/,
    "painted on every feed render");
  assert.match(CSS, /\.judge-limit-banner \{/);
});
