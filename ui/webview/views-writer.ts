// The webview pages' ONE views writer (the 2026-08-26 audit's Finding A). The chat strip
// (render.ts postViews) and the Outline's tag lens (fleet.ts) each cloned the pushed views blob
// and posted {type:"setTimelineViews"} raw — the clone still carried the payload's rev N, so two
// quick gestures both declared base N to the kernel's compare-and-set and the SECOND was always
// refused: a rapid hide-then-reveal (or two lens picks) silently lost the later edit. The timeline
// panel already solved exactly this with an optimistic counter (_nextViewsRev/viewsAck in
// ui/romp-timeline-view.js); this module is that discipline as the webview's shared TS twin:
//  - postViewsWrite stamps views.baseRev = max(payload rev, last anchor) — advancing once per
//    write — and strips the stale rev, so same-client sequences never self-409;
//  - anchorViewsRev re-anchors the counter to every pushed payload's views.rev (the panel's
//    update() rule), so a cross-client conflict costs at most one refused gesture;
//  - consumeViewsAck re-anchors on the kernel's per-write {type:"viewsAck", ok, rev} answer —
//    accepted or refused — and hands a refusal to the surface so its known-refused optimistic
//    overlay can drop now instead of aging out over three silent pushes.
// Pure and stateful-per-bundle (each pane has its own kernel socket and views stream), split out
// for tests like session-views.ts.
import { SessionViews } from "./session-views";

let optRev = 0;   // the CAS base for the NEXT write: max(payload rev, last ack), +1 per write

/** Re-anchor to a pushed payload's views.rev — called on every views arrival (tabOrder push in
 *  the chat, feed push in the Outline), unconditionally: the kernel's counter is the truth. */
export function anchorViewsRev(v: SessionViews | { rev?: unknown } | null | undefined): void {
  const rev = v && (v as { rev?: unknown }).rev;
  if (typeof rev === "number") optRev = rev;
}

/** The kernel's per-write acknowledgement for setTimelineViews. Returns whether the frame was
 *  consumed (so routers can skip their other cases). rev re-anchors either way; ok:false also
 *  invokes the surface's rollback — the write is KNOWN-refused, not merely unconfirmed. */
export function consumeViewsAck(m: unknown, onRefused?: () => void): boolean {
  const a = m as { type?: unknown; ok?: unknown; rev?: unknown } | null;
  if (!a || a.type !== "viewsAck") return false;
  optRev = typeof a.rev === "number" ? a.rev : 0;
  if (a.ok === false && onRefused) onRefused();
  return true;
}

/** Stamp and post one whole-blob write. The blob is a clone of a pushed payload, so it may carry
 *  that payload's rev — the counter outranks it when local writes are already in flight. */
export function postViewsWrite(post: (m: Record<string, unknown>) => void, views: SessionViews): void {
  const v: SessionViews & { rev?: number; baseRev?: number } = { ...views };
  const base = typeof v.rev === "number" ? v.rev : 0;
  optRev = Math.max(optRev, base) + 1;
  v.baseRev = optRev - 1;
  delete v.rev;                      // the stale payload rev never rides — baseRev is the declared base
  post({ type: "setTimelineViews", views: v });
}

/** Tests only: each test starts from a fresh counter. */
export function resetViewsWriterForTest(): void { optRev = 0; }
