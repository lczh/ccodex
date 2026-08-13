// Card-age provenance (the user 2026-07-27): the card header's "Nm ago" stamps the card's NEWEST
// event — a completed card's age is when it was marked done — which hides where the thread CAME from.
// Hovering the stamp tells the story: when the goal was started, the root's own verdict events, each
// sub-item with the time it landed, and what the visible stamp itself marks.
//
// Emits STRUCTURED rows ({when, what}) rather than a text blob (the user, same day: the native title
// tooltip was dense and unaligned) — the feed renders them as a styled popover whose time column
// aligns, reusing the modal history-row vocabulary. Pure assembly, executed by provenance.test.ts;
// the wording/format helpers stay in feed.ts (relAge / clockHM / logPhrase are pinned there and used
// by a dozen other surfaces) and are injected, so this module owns only the story's structure.

export interface LogRow { kind: string; src: string; why?: string | null; at?: number | null; evT?: number | null; }
export interface ProvNode {
  id: string; text: string; status: string; t: number; last: number; mt?: number;
  cleared?: boolean; log?: LogRow[] | null;
}
export interface ProvItem { itemId: string; t: number; column: string; tree: ProvNode[]; }
export interface ProvFmt {
  rel: (sec: number) => string;          // feed relAge
  clock: (t: number) => string;          // feed clockHM
  phrase: (r: LogRow) => string;         // feed logPhrase
}
// one popover line: `when` is the aligned time cell ("Nm ago · HH:MM"), `what` the story cell, `t`
// the row's own epoch so the renderer can tint the whole line by recency (the tab-tip treatment —
// the user 2026-07-27; 0 on the un-timed remainder row). kind lets the renderer treat the closing
// stamp line as its own section (separator above).
export interface ProvRow { when: string; what: string; t: number; kind: "start" | "event" | "sub" | "more" | "stamp"; }

const SUB_CAP = 8;                       // a huge tree stays a glanceable popover, not a scroll

function stamp(t: number, now: number, f: ProvFmt): string {
  return f.rel(now - t) + " · " + f.clock(t);
}

// the moment the card's thread began: its root node's mint time (earliest tree mint as a fallback for
// payloads without a root row; the card's own t as the last resort)
export function rootStart(it: ProvItem): number {
  const root = it.tree.find((n) => n.id === it.itemId);
  if (root?.t) return root.t;
  return it.tree.length ? Math.min(...it.tree.map((n) => n.t)) : it.t;
}

// what the visible "Nm ago" itself marks, so the stamp is self-explaining
function stampWhat(column: string): string {
  return column === "completed" ? "marked done" : column === "needs_input" ? "blocked" : "last update";
}

export function provenanceRows(it: ProvItem, now: number, f: ProvFmt): ProvRow[] {
  const rows: ProvRow[] = [{ when: stamp(rootStart(it), now, f), what: "started", t: rootStart(it), kind: "start" }];
  // the root's own verdict rows (asked you / you answered / …) — only shipped for non-done nodes
  const root = it.tree.find((n) => n.id === it.itemId);
  for (const r of root?.log ?? []) {
    const rt = r.at || r.evT || 0;
    if (rt) rows.push({ when: stamp(rt, now, f), what: f.phrase(r), t: rt, kind: "event" });
  }
  // sub-items: a resolved sub is stamped when it RESOLVED (mt — where it landed), an open one when it
  // was minted (its resolution hasn't happened yet). The kernel ships the tree newest-subtree-activity
  // FIRST (flatten's _fsubmax sort, for the ledger views) — the slice keeps that "8 most recent"
  // selection; the sort below puts what's KEPT back on the clock.
  const subs = it.tree.filter((n) => n.id !== it.itemId && !n.cleared);
  for (const n of subs.slice(0, SUB_CAP)) {
    const mark = n.status === "done" ? "✓" : n.status === "question" ? "⏸" : "·";
    const at = n.status === "open" ? n.t : (n.mt || n.last || n.t);
    const txt = n.text.length > 48 ? n.text.slice(0, 47) + "…" : n.text;
    rows.push({ when: stamp(at, now, f), what: mark + " " + txt, t: at, kind: "sub" });
  }
  // ONE story, ONE clock (the user 2026-08-13, who read the popover as shuffled): the root log runs
  // ascending while the tree ships newest-first, so the sections read in opposite directions — strict
  // chronological interleave, before the t:0 remainder and the pinned stamp join. The sort is stable,
  // so equal-t rows keep their event-before-sub order.
  rows.sort((a, b) => a.t - b.t);
  if (subs.length > SUB_CAP) rows.push({ when: "", what: "…and " + (subs.length - SUB_CAP) + " more", t: 0, kind: "more" });
  rows.push({ when: stamp(it.t, now, f), what: stampWhat(it.column), t: it.t, kind: "stamp" });
  return rows;
}

// a GROUP card folds N sibling asks from one typed prompt — its stamp's story is the fold itself
export function provenanceGroupRows(memberStarts: number[], t: number, now: number, f: ProvFmt): ProvRow[] {
  const rows: ProvRow[] = [];
  if (memberStarts.length) rows.push({ when: stamp(Math.min(...memberStarts), now, f), what: "started", t: Math.min(...memberStarts), kind: "start" });
  rows.push({ when: "", what: memberStarts.length + " cards from one prompt", t: 0, kind: "sub" });
  rows.push({ when: stamp(t, now, f), what: "last update", t: t, kind: "stamp" });
  return rows;
}
