// The file viewer — a big modal over the CHAT pane (the user 2026-08-15: the first cut filled the
// FEED pane, and reading a file cost the cards; the click came out of the chat, so the file presents
// over the chat, ~95% of the pane behind a dimmed backdrop, ✕ top right — and the feed is never touched).
//
// Clicking a file path in the chat used to post `openFile`, which the kernel served by running an
// opener on ITS machine (the user 2026-08-08). Read the dashboard from another device — a laptop
// across the internet, a phone — and that is the wrong screen entirely; on a kernel with no desktop it
// did nothing at all, silently, which is how the user found it. The only place a file can actually be
// shown is the browser you are looking at, so the bytes come over the same `/file` route the image
// previews already use (federation-aware via fileUrl, so a remote session's file is relayed from the
// host that owns it).
//
// Living in the CHAT page also removes a whole relay: the click and the viewer are the same document
// now, so there is no shell forwarding, no feed-pane bring-forward/put-back, and the standalone /chat
// page views files exactly like the framed one.
import hljs from "highlight.js/lib/core";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { fileUrl } from "./preview";

// hljs is registered per-bundle. Same language set (and grammar registrations) the chat's fence
// highlighting uses, dup-guarded, so importing this module alongside render.ts costs nothing.
import bash from "highlight.js/lib/languages/bash";
import python from "highlight.js/lib/languages/python";
import javascript from "highlight.js/lib/languages/javascript";
import typescript from "highlight.js/lib/languages/typescript";
import json from "highlight.js/lib/languages/json";
import xml from "highlight.js/lib/languages/xml";
import cssLang from "highlight.js/lib/languages/css";
import markdown from "highlight.js/lib/languages/markdown";
import diff from "highlight.js/lib/languages/diff";
import yaml from "highlight.js/lib/languages/yaml";

for (const [name, lang] of Object.entries({
  bash, sh: bash, shell: bash, python, py: python, javascript, js: javascript,
  typescript, ts: typescript, json, xml, html: xml, css: cssLang, markdown, md: markdown,
  diff, yaml, yml: yaml,
})) {
  try { hljs.registerLanguage(name, lang as any); } catch { /* dup alias */ }
}

// Extension → the hljs language to force. Anything absent is shown unhighlighted rather than guessed:
// highlightAuto on a config file or a log picks a language at random and paints it misleadingly, and a
// wrong highlight reads as information the file does not contain.
const LANG: Record<string, string> = {
  py: "python", pyi: "python", js: "javascript", jsx: "javascript", mjs: "javascript",
  cjs: "javascript", ts: "typescript", tsx: "typescript", json: "json", jsonc: "json",
  yaml: "yaml", yml: "yaml", sh: "bash", bash: "bash", zsh: "bash", bats: "bash",
  html: "xml", htm: "xml", xml: "xml", svg: "xml", vue: "xml", css: "css", scss: "css",
  md: "markdown", markdown: "markdown", diff: "diff", patch: "diff",
};

function langFor(path: string): string | null {
  const ext = path.slice(path.lastIndexOf(".") + 1).toLowerCase();
  return LANG[ext] || null;
}

// marked is a per-bundle singleton. render.ts makes the SAME calls with the SAME choices — GFM without
// hard breaks, strikethrough only on DOUBLE tildes (marked's stock GFM `del` tokenizer fires on a
// single ~, so prose between two "approximately" tildes renders struck through; GitHub itself only
// strikes ~~double~~) — so configuring here too is an idempotent no-op in the chat bundle, and keeps
// this module correct anywhere it's bundled without render.ts.
marked.setOptions({ gfm: true, breaks: false });
marked.use({
  tokenizer: {
    del(src: string) {
      const m = /^~~(?=\S)([\s\S]*?\S)~~/.exec(src);
      if (!m) return undefined;
      return { type: "del", raw: m[0], text: m[1], tokens: (this as { lexer: { inlineTokens(s: string): unknown[] } }).lexer.inlineTokens(m[1]) };
    },
  },
} as Parameters<typeof marked.use>[0]);

// ── view-format preferences ────────────────────────────────────────────────────────────────────────
// The Raw ⇄ Rendered choice for markdown and the word-wrap toggle persist in localStorage, NOT a kernel
// file — per-browser view state, the same call feed-view-state.ts makes for the feed's open sections (it
// must survive a kernel restart without a round-trip to the thing that just restarted). RENDERED is the
// default for markdown (the user 2026-08-09); Raw stays one click away.
const FMT_KEY = "romp:fileviewFmt";
type FileViewFmt = { md: "rendered" | "raw"; wrap: boolean };

// Any malformed/foreign value reads as the defaults rather than throwing — a corrupt entry may cost the
// stored preference, never the viewer (feed-view-state's parseViewState contract).
function parseFmt(raw: string | null | undefined): FileViewFmt {
  const def: FileViewFmt = { md: "rendered", wrap: false };
  if (!raw) return def;
  try {
    const o = JSON.parse(raw) as { md?: unknown; wrap?: unknown };
    if (!o || typeof o !== "object") return def;
    return { md: o.md === "raw" ? "raw" : "rendered", wrap: o.wrap === true };
  } catch { return def; }
}

function loadFmt(): FileViewFmt {
  try { return parseFmt(localStorage.getItem(FMT_KEY)); } catch { return parseFmt(null); }
}

function saveFmt(f: FileViewFmt): void {
  try { localStorage.setItem(FMT_KEY, JSON.stringify(f)); } catch { /* storage full */ }
}

function el(tag: string, cls?: string): HTMLElement {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  return e;
}

export function closeFileView(): void {
  const wrap = document.getElementById("romp-fileview");
  if (!wrap) return;
  wrap.remove();
  document.body.classList.remove("fileview-open");
}

/** Show `path` in a modal over this pane. Re-opening replaces whatever is up — never stacks. */
export function openFileView(path: string, sid?: string | null): void {
  document.getElementById("romp-fileview")?.remove();
  // backdrop (the whole overlay carries the id every open/closed check targets) + the ~95% card.
  // The backdrop treatment matches the lightbox: dimmed, click outside the card closes, content
  // clicks don't (the user 2026-08-15: it must be obvious the chat is still right behind it).
  const wrap = el("div");
  wrap.id = "romp-fileview";
  wrap.onclick = (ev) => { if (ev.target === wrap) closeFileView(); };
  const box = el("div", "fileview");
  document.body.classList.add("fileview-open");

  const bar = el("div", "fileview-bar");
  // Directory then basename as TWO elements, because only the directory may be truncated: the filename
  // is what identifies the file, so it never shrinks however deep the path is. (A single text node with
  // the rtl-ellipsis trick would truncate the right end — exactly the wrong half.)
  const name = el("div", "fileview-name");
  name.title = path;                                   // the full path, one hover away
  const cut = path.lastIndexOf("/");
  const dir = el("span", "fileview-dir");
  dir.textContent = cut >= 0 ? path.slice(0, cut + 1) : "";
  const base = el("span", "fileview-base");
  base.textContent = path.slice(cut + 1);
  name.appendChild(dir); name.appendChild(base);
  const acts = el("div", "fileview-acts");

  // ── format toggles (the user 2026-08-09) ── A markdown file opens RENDERED, its Raw form one click
  // away; everything else keeps the code view, whose long lines the Wrap toggle can soft-wrap. Both
  // choices persist per browser (FMT_KEY above). These buttons are built once per open and never
  // re-rendered by kernel pushes — the viewer is a static overlay — so direct listeners are click-safe
  // here, same as Copy path below.
  const fmt = loadFmt();
  let text: string | null = null;             // set once the fetch lands; earlier clicks just save the pref
  const isMd = langFor(path) === "markdown";  // .md/.markdown — the only kind with a Rendered form
  const segBtns: Array<["rendered" | "raw", HTMLButtonElement]> = [];
  if (isMd) {
    for (const mode of ["rendered", "raw"] as const) {
      const b = el("button", "fileview-btn") as HTMLButtonElement;
      b.type = "button";
      b.textContent = mode === "rendered" ? "Rendered" : "Raw";
      b.title = mode === "rendered" ? "The prose the markdown means" : "The file's actual bytes";
      b.addEventListener("click", () => { fmt.md = mode; saveFmt(fmt); renderBody(); });
      segBtns.push([mode, b]);
      acts.appendChild(b);
    }
  }
  const wrapBtn = el("button", "fileview-btn") as HTMLButtonElement;
  wrapBtn.type = "button"; wrapBtn.textContent = "Wrap"; wrapBtn.title = "Soft-wrap long lines";
  wrapBtn.addEventListener("click", () => { fmt.wrap = !fmt.wrap; saveFmt(fmt); renderBody(); });
  acts.appendChild(wrapBtn);

  // ── download (the user 2026-08-09) ── Any linked file can be SAVED, including everything the pane
  // cannot show: the kernel's ?download=1 serves anything on disk (the rationale lives with
  // _file_download in kernel.py). Same-origin and cookie-authed like the view fetch, and
  // federation-aware for free — fileUrl already routes a remote session's file through the relay.
  const dlUrl = fileUrl(path, sid) + "&download=1";
  const dl = el("button", "fileview-btn") as HTMLButtonElement;
  dl.type = "button"; dl.textContent = "Download"; dl.title = "Save this file to your device";
  dl.addEventListener("click", () => startDownload(dlUrl, dl));
  acts.appendChild(dl);

  const copy = el("button", "fileview-btn") as HTMLButtonElement;
  copy.type = "button"; copy.textContent = "Copy path"; copy.title = path;
  copy.addEventListener("click", () => {
    navigator.clipboard?.writeText(path).then(
      () => { copy.textContent = "Copied"; setTimeout(() => { copy.textContent = "Copy path"; }, 1200); },
      () => { copy.textContent = "Copy failed"; });
  });
  const close = el("button", "fileview-btn fileview-close") as HTMLButtonElement;
  close.type = "button"; close.textContent = "✕"; close.title = "Close (Esc)";
  close.setAttribute("aria-label", "Close the file viewer");
  close.addEventListener("click", closeFileView);
  acts.appendChild(copy); acts.appendChild(close);
  bar.appendChild(name); bar.appendChild(acts);

  const body = el("div", "fileview-body");
  // Per the loading-state rule the first thing up is the romp loader, not a blank pane — a file coming
  // over an ssh tunnel to a phone is a real wait.
  const load = el("div", "fileview-load");
  load.innerHTML = '<img src="/media/romp-swirl-glyph.svg" alt=""><span>romp</span>'
    + '<i class="fileview-dot"></i><i class="fileview-dot"></i><i class="fileview-dot"></i>';
  body.appendChild(load);

  box.appendChild(bar); box.appendChild(body);
  wrap.appendChild(box);
  document.body.appendChild(wrap);

  // Chooses the body for the current prefs and syncs the buttons. The pressed state flips SYNCHRONOUSLY
  // in the click handler — the immediate acknowledgement ui/CLAUDE.md requires — and so does the content
  // swap, since the text is already in memory.
  const renderBody = () => {
    const rendered = isMd && fmt.md === "rendered";
    for (const [mode, b] of segBtns) {
      const on = fmt.md === mode;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", String(on));
    }
    // wrap governs the pre view only — rendered prose always wraps — so the button leaves with it
    wrapBtn.hidden = rendered;
    wrapBtn.classList.toggle("on", fmt.wrap);
    wrapBtn.setAttribute("aria-pressed", String(fmt.wrap));
    if (text === null) return;   // still loading; the saved pref is honored when the bytes land
    body.replaceChildren(rendered ? mdBlock(text) : codeBlock(text, path, fmt.wrap));
  };
  renderBody();   // buttons take their initial state now; the loader stays up until the fetch lands

  const onKey = (e: KeyboardEvent) => {
    if (e.key !== "Escape" || !document.getElementById("romp-fileview")) return;
    e.preventDefault();
    closeFileView();
    document.removeEventListener("keydown", onKey);
  };
  document.addEventListener("keydown", onKey);

  fetch(fileUrl(path, sid), { cache: "no-store" }).then((r) => {
    // Every failure says WHY, in the pane, rather than leaving a blank one: the kernel distinguishes
    // "not a type I serve" from "too big" from "not text after all", and that is exactly what the
    // person who clicked needs to know (a 413 names the size and the cap). The status rides along so
    // the catch below can tell "the file is there but I can't show it" from "there is no file".
    if (!r.ok) return r.text().then((t) => {
      throw Object.assign(new Error(t || ("HTTP " + r.status)), { status: r.status });
    });
    return r.text();
  }).then((t) => {
    if (!document.getElementById("romp-fileview")) return;    // closed while it was in flight
    text = t;
    renderBody();
  }).catch((err) => {
    if (!document.getElementById("romp-fileview")) return;
    const why = el("div", "fileview-err");
    const msg = String(err && err.message || err);
    why.textContent = msg;
    if (!msg.includes(path)) {
      // The kernel's 404/413/415 bodies name the RESOLVED path themselves now — the hint exists for
      // errors that don't (a network failure, an old kernel), not to say the same path twice.
      const hint = el("div", "fileview-err-hint");
      hint.textContent = path;
      why.appendChild(hint);
    }
    // A refusal-to-RENDER is not a dead end (ui/CLAUDE.md): when the file exists, the kernel's own
    // words are followed by the way out — the download the view could not be. A 404 stays offerless,
    // because offering to download a file that is not there would be a lie.
    if (offersDownload((err as { status?: number }).status)) {
      const offer = el("button", "fileview-btn fileview-err-dl") as HTMLButtonElement;
      offer.type = "button"; offer.textContent = "Download";
      offer.title = "Save this file to your device";
      offer.addEventListener("click", () => startDownload(dlUrl, offer));
      why.appendChild(offer);
    }
    body.replaceChildren(why);
  });
}

// Which fetch failures still deserve a Download offer? Exactly the ones that mean the file EXISTS:
// 413 (too large to render) and 415 (on disk but not viewable — a .zip, a binary named like text).
// A 404 is genuinely missing, and gets nothing.
function offersDownload(status: number | undefined): boolean {
  return status === 413 || status === 415;
}

// Kick the browser's downloader at `url` without touching the pane: a clicked <a download> starts a
// same-origin, cookie-authed request the BROWSER owns (its progress UI, its save location), and since
// the kernel answers with Content-Disposition: attachment the page never navigates — the viewer, the
// feed behind it, and the scroll position all stay put. The button acknowledges the click itself
// (ui/CLAUDE.md), because the browser's download UI can take a beat to appear over a slow tunnel.
function startDownload(url: string, btn: HTMLButtonElement): void {
  const a = document.createElement("a");
  a.href = url;
  a.download = "";               // a hint; the kernel's attachment disposition is what actually decides
  document.body.appendChild(a);
  a.click();
  a.remove();
  const was = btn.textContent;
  btn.textContent = "Downloading…";
  setTimeout(() => { btn.textContent = was; }, 1500);
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Wrap mode's numbering. The flat sibling gutter cannot survive soft-wrapping — one logical line becomes
// several visual lines and every number below it drifts — so wrap mode RESTRUCTURES instead of shipping a
// misaligned column: each logical line is its own row (.fv-cl) whose number is a CSS counter in ::before
// (the chat's .cl/.ct treatment, styles.css), so the numbers stay glued to their lines however tall a
// wrapped line grows, and being ::before content they still never copy with the code. hljs spans can
// cross newlines, so each row re-opens the spans the previous row left unclosed and closes its own —
// render.ts's wrapCodeLines balance walk.
function wrapNumberedHtml(html: string): string {
  const lines = html.split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();   // a trailing newline is not a line
  let open: string[] = [];
  return lines.map((ln) => {
    const prefix = open.join("");
    const re = /<span[^>]*>|<\/span>/g; let m; const stack = open.slice();
    while ((m = re.exec(ln))) { if (m[0] === "</span>") stack.pop(); else stack.push(m[0]); }
    const suffix = "</span>".repeat(Math.max(0, stack.length));
    open = stack;
    return `<span class="fv-cl"><span class="fv-ct">${prefix}${ln}${suffix}</span></span>`;
  }).join("");
}

// Line-numbered <pre>. In the default (no-wrap) view the gutter is a sibling column rather than text in
// the same <pre>, so selecting the code and copying it does NOT drag the line numbers along with it; the
// wrap view keeps that copy-safety a different way (see wrapNumberedHtml above).
function codeBlock(text: string, path: string, wrapLines: boolean): HTMLElement {
  const wrap = el("div", "fileview-code");
  const lines = text.split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();   // a trailing newline is not a line
  const lang = langFor(path);
  let hl: string | null = null;
  if (lang) {
    try { hl = hljs.highlight(text, { language: lang }).value; }
    catch { hl = null; }                               // a broken grammar must never cost the content
  }
  const pre = el("pre", "fileview-pre");
  const code = el("code", "hljs");
  if (wrapLines) {
    pre.classList.add("fileview-wrap");
    code.innerHTML = wrapNumberedHtml(hl !== null ? hl : escapeHtml(text));
    pre.appendChild(code);
    wrap.appendChild(pre);
    return wrap;
  }
  const gutter = el("div", "fileview-gutter");
  gutter.textContent = lines.map((_, i) => String(i + 1)).join("\n");
  gutter.setAttribute("aria-hidden", "true");
  if (hl !== null) code.innerHTML = hl; else code.textContent = text;
  pre.appendChild(code);
  wrap.appendChild(gutter); wrap.appendChild(pre);
  return wrap;
}

// Markdown rendered as the prose it means (the user 2026-08-09: Rendered is the default, Raw one click
// away). The file is arbitrary bytes off a disk and marked emits raw HTML verbatim, so — exactly like the
// chat's md() in render.ts — the output goes through DOMPurify before it ever reaches .innerHTML: an
// <img onerror> or a javascript: href in a README must never run in the dashboard.
function mdBlock(text: string): HTMLElement {
  const box = el("div", "fileview-md");
  try {
    const dirty = marked.parse(text) as string;
    box.innerHTML = DOMPurify.sanitize(dirty, { USE_PROFILES: { html: true }, ADD_DATA_URI_TAGS: ["img"] });
  } catch {
    box.textContent = text;                            // a marked bug must never cost the content
  }
  // Links open a NEW tab: the viewer lives inside the chat pane's document, and letting a README link
  // navigate it away would silently eat the chat until a reload.
  box.querySelectorAll("a[href]").forEach((a) => {
    (a as HTMLAnchorElement).target = "_blank";
    (a as HTMLAnchorElement).rel = "noopener";
  });
  // Fenced blocks: highlight only a language the fence NAMES and this bundle registers — the same
  // no-guessing rule as langFor; an unnamed block stays plain rather than being painted at random.
  box.querySelectorAll("pre code").forEach((node) => {
    const codeEl = node as HTMLElement;
    const lang = (codeEl.className.match(/language-([\w-]+)/) || [])[1];
    if (!lang || !hljs.getLanguage(lang)) return;
    try {
      codeEl.innerHTML = hljs.highlight(codeEl.textContent || "", { language: lang }).value;
      codeEl.classList.add("hljs");
    } catch { /* leave plain */ }
  });
  return box;
}
