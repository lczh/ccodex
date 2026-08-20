// Behavior tests for the TeX math extension (math.ts). The delimiter rules are the
// load-bearing part: in chat text a bare `$` means shell variables and prices far more often
// than math, so the stay-literal cases matter as much as the rendered ones. marked and katex
// both run for real here (plain JS, no DOM needed); DOMPurify compatibility is pinned at the
// source level instead, same as render-sanitize.test.ts (no jsdom harness for the webview).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { Marked } from "marked";
import { mathBlock, mathInline } from "./math";

const m = new Marked({ gfm: true, extensions: [mathBlock, mathInline] });
const html = (src: string) => m.parse(src) as string;
const hasMath = (s: string) => s.includes('class="katex"');

// --- renders as math ---

test("inline $..$ renders KaTeX", () => {
  const out = html("Euler: $e^{i\\pi}+1=0$ holds.");
  assert.ok(hasMath(out));
  assert.ok(!out.includes("$e^"), "raw TeX must not leak through");
});

test("inline \\(..\\) renders KaTeX", () => {
  assert.ok(hasMath(html("Ratio \\(\\tfrac{a}{b}\\) here.")));
});

test("$$..$$ and \\[..\\] render display math", () => {
  assert.ok(html("Total: $$\\sum_i x_i$$ done.").includes("katex-display"));
  assert.ok(html("\\[x^2\\]").includes("katex-display"));
});

test("a multi-line $$ paragraph beats markdown's block rules", () => {
  // The "- x" line would become a <li> if block tokenization carved the formula up first.
  const out = html("$$\n- x\n$$");
  assert.ok(out.includes("katex-display"));
  assert.ok(!out.includes("<li>"), "list rule must not fire inside display math");
});

test("closing $ may touch trailing punctuation and emphasis", () => {
  assert.ok(hasMath(html("the $x$-axis")));
  const strong = html("**$O(n)$** cost");
  assert.ok(strong.includes("<strong>") && hasMath(strong));
});

test("invalid TeX degrades to flagged output, never a throw", () => {
  const out = html("bad: $\\frac{1}{$ end");
  assert.equal(typeof out, "string");
  assert.ok(out.includes("katex"), "katex error rendering expected");
});

// --- stays literal ---

const LITERAL = [
  ["prices: closer followed by a digit", "costs $5 and $10 today"],
  ["shell vars joined by a slash", "paths $HOME/$USER here"],
  ["shell vars joined by a comma (closer followed by a letter)", "set $FOO,$BAR now"],
  ["shell vars with space-preceded closers", "echo $FOO and $BAR"],
  ["opener must touch its content", "not math: $ x$ spaced"],
  ["content may not span lines", "a $x\ny$ b"],
] as const;

for (const [why, src] of LITERAL) {
  test(`literal: ${why}`, () => {
    const out = html(src);
    assert.ok(!hasMath(out), `must stay literal: ${src}\ngot: ${out}`);
    assert.ok(out.includes("$"), "the $ itself must survive as text");
  });
}

test("a code span is a wall: $ cannot pair across it", () => {
  const out = html("price $5 `a $b` end");
  assert.ok(!hasMath(out));
  assert.ok(out.includes("<code>a $b</code>"), "code span must render intact");
});

test("$..$ inside a code span or fence stays code", () => {
  const span = html("`$x$`");
  assert.ok(!hasMath(span) && span.includes("<code>$x$</code>"));
  const fence = html("```sh\necho $X and $Y$\n```");
  assert.ok(!hasMath(fence) && fence.includes("echo $X"));
});

test("escaped \\$ never opens math", () => {
  const out = html("\\$5 vs \\$10");
  assert.ok(!hasMath(out) && out.includes("$5"));
});

// --- source pins (the wiring that behavior tests can't reach) ---

const UI = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");

test("render.ts wires the math extensions into marked", () => {
  const src = UI("render.ts");
  assert.match(src, /import \{ mathBlock, mathInline \} from "\.\/math";/);
  assert.match(src, /marked\.use\(\{ extensions: \[mathBlock, mathInline\] \}\)/);
});

test("math.ts renders html-only output so md()'s DOMPurify profile passes it", () => {
  // output: "html" means no MathML twin — but stretchy glyphs (\sqrt radicals, wide accents,
  // extensible arrows) are still inline <svg> even in html mode, so the sanitizer allows
  // DOMPurify's svg profile alongside html (the user 2026-08-19: $\sqrt{d}$ rendered as a bare
  // serif "d" — the radical was sanitized away while its radicand survived).
  assert.match(UI("math.ts"), /output: "html"/);
  assert.match(UI("render.ts"), /USE_PROFILES: \{ html: true, svg: true \}/);
});

test("executed: \\sqrt really does emit inline svg — the glyph the sanitizer must keep", () => {
  const out = html("norm grows like $\\sqrt{d}$ here.");
  assert.ok(hasMath(out));
  assert.ok(out.includes("<svg"), "the radical is an inline svg even with output:html");
  assert.ok(out.includes("sqrt"), "KaTeX marks the construct");
});

test("styles.css imports the KaTeX layout css", () => {
  assert.match(UI("styles.css"), /@import "katex\/dist\/katex\.min\.css";/);
});

test("esbuild emits KaTeX woff2 fonts under dist/fonts/", () => {
  const build = fs.readFileSync(path.resolve(process.cwd(), "esbuild.js"), "utf8");
  assert.match(build, /"\.woff2": "file"/);
  assert.match(build, /assetNames: "fonts\/\[name\]-\[hash\]"/);
});

test("katex is a declared dependency", () => {
  const pkg = JSON.parse(fs.readFileSync(path.resolve(process.cwd(), "package.json"), "utf8"));
  assert.ok(pkg.dependencies && pkg.dependencies.katex, "katex must be in dependencies");
});
