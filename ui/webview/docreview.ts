// Doc review (the user 2026-08-14, who found coordinating a markdown review painful): reading a doc an
// agent wrote meant opening it in an editor and hand-copying every line you wanted changed back into the
// chat. This module is the pure half of the fix — the chat pane renders the doc, you highlight a span and
// comment on it, and ONE submit turns every comment into a single message drafted into that session's
// composer. No DOM here so it is unit-testable; render.ts wires the reader and the composer insert.

export interface DocComment {
  id: string;
  quote: string;         // the span the user highlighted, whitespace-normalized
  line: number | null;   // 1-based source line, or null when we could not honestly find one
  body: string;          // what the user wants changed
  ts: number;
}

// Collapse runs of whitespace (a rendered selection carries the layout's newlines and indentation, the
// source carries its own) so both sides compare on words alone.
function norm(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}

// Strip the inline markdown the RENDERER consumed, so a span selected out of rendered text can still be
// found in the source. Deliberately does NOT touch `_`: snake_case identifiers are far commoner in these
// docs than underscore emphasis, and stripping them would break more matches than it fixes.
function stripInline(line: string): string {
  return line
    .replace(/^\s*(?:[#]{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)/, "")   // block markers: heading, quote, list
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")                       // image → its alt text
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")                        // link → its label
    .replace(/\*\*|~~|`|\*/g, "");                                  // bold / strike / code / emphasis
}

// Which source line does this selection come from? Tries progressively looser matches and returns null
// rather than a guess — a wrong line number sends the agent to the wrong place, which is worse than no
// line number at all (the message still carries the quoted text).
export function anchorFor(source: string, selected: string): { quote: string; line: number | null } {
  const quote = norm(selected);
  if (!quote) return { quote: "", line: null };
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  // A multi-line selection anchors on its FIRST non-empty line — that is where the agent should look.
  // Split the RAW selection: `quote` has already had its newlines collapsed away.
  const head = norm(selected.replace(/\r\n?/g, "\n").split("\n").find((l) => l.trim()) || quote);
  const needles = [head];
  const stripped = norm(stripInline(head));
  if (stripped && stripped !== head) needles.push(stripped);
  // Last resort: the first six words, so a selection whose tail crossed formatting we do not strip
  // (an underscore, a footnote marker) still lands on its line.
  const words = (stripped || head).split(" ").filter(Boolean);
  if (words.length > 6) needles.push(words.slice(0, 6).join(" "));

  for (const needle of needles) {
    if (!needle) continue;
    for (let i = 0; i < lines.length; i++) {
      const raw = norm(lines[i]);
      if (raw.includes(needle) || norm(stripInline(lines[i])).includes(needle)) {
        return { quote, line: i + 1 };
      }
    }
  }
  return { quote, line: null };
}

// Comments are held per session AND per file: reviewing two docs in one session keeps two batches, and
// each submits on its own. render.ts owns the Map and persists it beside the drafts; the key lives here
// so the shape is pinned by a test.
export function docKey(sid: string, path: string): string {
  return sid + "\0" + path;
}

const QUOTE_MAX = 140;   // long enough to identify the span, short enough that the message stays readable

function shortQuote(q: string): string {
  return q.length <= QUOTE_MAX ? q : q.slice(0, QUOTE_MAX - 1).trimEnd() + "…";
}

// The message the composer receives. It is read by an agent that has never heard of romp (CLAUDE.md), so
// it names no machinery — it reads as the person it works for listing what they want changed, with the
// anchors attached so nothing has to be copy-pasted back.
export function buildReviewMessage(path: string, comments: DocComment[]): string {
  const live = comments.filter((c) => c.body.trim());
  if (!live.length) return "";
  const parts = live.map((c, i) => {
    const where = c.line ? `line ${c.line} — ` : "";
    const head = `${i + 1}. ${where}"${shortQuote(c.quote)}"`;
    const body = c.body.trim().split("\n").map((l) => "   " + l.trimEnd()).join("\n");
    return head + "\n" + body;
  });
  return `Comments on ${path} — all of them, one pass:\n\n` + parts.join("\n\n") + "\n";
}
