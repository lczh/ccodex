// The FEED follows the active session view (the user 2026-08-24, the expectation surfaced when a
// just-tagged session's cards stayed on an "untagged"-filtered board): the board's cards gate on
// the SAME union-aware decider the tabs and timeline lanes read — session-views.ts's viewVisible,
// the kernel _view_visible's client mirror — never a fourth fork of the rule; the mirrors are
// pinned against each other for exactly this reason. THE BREAKTHROUGH GUARD (the interrupt rule):
// a needs-you card always shows regardless of view — a view-hidden session that needs the human is
// this board's whole job — and wears the outside-the-view cue so the exception explains itself.
// Pure, split out for node --test (the feed-search.ts pattern): the tag-home fixtures the untagged
// union fix established compose straight onto these.
import { SessionViews, viewVisible } from "./session-views";

/** Is this card's session outside the active view? (The cue's question — a breakthrough card is
 *  shown AND outside.) No blob (an older kernel) → nothing is outside. */
export function outsideView(views: SessionViews | null | undefined, sid: string): boolean {
  return !!views && !viewVisible(views, sid);
}

/** Does this card show on the board? The view's visibility rule, with the breakthrough guard. */
export function cardInView(views: SessionViews | null | undefined, sid: string, needsYou: boolean): boolean {
  return !outsideView(views, sid) || needsYou;
}

/** The active view's display label for the outside-view line: a tag view is its NAME (the union is
 *  name-keyed), untagged is its plain-words self, All is All (only the manual hidden set excludes). */
export function viewLabel(views: SessionViews | null | undefined): string {
  const act = views && views.active;
  if (!act || act === "all") return "All";
  if (act === "untagged") return "no tags";
  const tags = (views!.tags || views!.groups || []).concat(views!.remoteTags || []);
  const t = tags.find((x) => x.id === act);
  return (t && t.name) || "this";
}

/** The dim "N cards outside this view" count: view-hidden and NOT broken through — exactly what
 *  the board is not showing (a breakthrough already shows; counting it would double-speak). */
export function outsideViewCount(views: SessionViews | null | undefined,
                                 cards: { sid: string; needsYou: boolean }[]): number {
  if (!views) return 0;
  return cards.filter((c) => outsideView(views, c.sid) && !c.needsYou).length;
}
