// The webview pages' ONE views writer (the 2026-08-26 audit's Finding A). The chat strip
// (render.ts postViews) and the Outline's tag lens (fleet.ts) each cloned the pushed views blob
// and posted {type:"setTimelineViews"} raw — the clone still carried the payload's rev N, so two
// quick gestures both declared base N to the kernel's compare-and-set and the SECOND was always
// refused: a rapid hide-then-reveal (or two lens picks) silently lost the later edit. The timeline
// panel already solved exactly this with an optimistic counter (_nextViewsRev/viewsAck in
// ui/romp-timeline-view.js); this module is that discipline as the webview's shared TS twin.
//
// The counter is TWO numbers, not one (the r47 verification of Finding A): the last rev the
// kernel actually REPORTED (a pushed payload's views.rev, or a viewsAck's rev), plus how many of
// our writes are still in flight toward it. A single monotone counter forged past its own refused
// writes — after a foreign client committed, the forged base could COINCIDE with the kernel's
// real rev and a stale blob was ACCEPTED, silently erasing the foreign edit — and re-anchoring it
// to every payload rewound it below writes still in flight, resurrecting the second-gesture 409.
// So:
//  - postViewsWrite declares baseRev = confirmed + in-flight — exactly one optimistic slot per
//    write actually outstanding, never headroom left over from writes the kernel already answered;
//  - anchorViewsRev raises the confirmed rev to a pushed payload's views.rev and NEVER lowers it
//    (federation re-emits the cached blob with its old rev on reorder/remote-push events — a stale
//    payload must not rewind the base under an in-flight write);
//  - consumeViewsAck retires one in-flight slot per kernel answer. ok:true raises the confirmed
//    rev (never lowers — an ack can arrive after a newer payload). ok:false re-anchors to the
//    SERVED rev even downward (the refusal reports the CAS truth), clears the remaining
//    optimistic headroom — every slot minted after the refused write assumed it would land, and
//    reusing that guess is how a stale base coincides with a foreign commit — and hands the
//    refusal to the surface so its known-refused overlay drops now instead of aging out.
// Pure and stateful-per-bundle (each pane has its own kernel socket and views stream), split out
// for tests like session-views.ts.
import { SessionViews } from "./session-views";

let confirmedRev = 0;   // the last rev the KERNEL reported (payload push or ack) — never a guess
let inFlight = 0;       // our writes posted since then and not yet answered by a viewsAck

/** Raise the confirmed rev to a pushed payload's views.rev — called on every views arrival
 *  (tabOrder push in the chat, feed push in the Outline). Monotonic: a re-emitted stale payload
 *  never rewinds the base below a write already in flight (the r47 verification). */
export function anchorViewsRev(v: SessionViews | { rev?: unknown } | null | undefined): void {
  const rev = v && (v as { rev?: unknown }).rev;
  if (typeof rev === "number") confirmedRev = Math.max(confirmedRev, rev);
}

/** The kernel's per-write acknowledgement for setTimelineViews. Returns whether the frame was
 *  consumed (so routers can skip their other cases). Every ack retires one in-flight slot;
 *  ok:false re-anchors to the served rev, clears the forged headroom, and invokes the surface's
 *  rollback — the write is KNOWN-refused, not merely unconfirmed. A malformed rev leaves the
 *  confirmed rev standing (anchoring it to 0 was itself a rewind). */
export function consumeViewsAck(m: unknown, onRefused?: () => void): boolean {
  const a = m as { type?: unknown; ok?: unknown; rev?: unknown } | null;
  if (!a || a.type !== "viewsAck") return false;
  inFlight = Math.max(0, inFlight - 1);
  const rev = typeof a.rev === "number" ? a.rev : null;
  if (a.ok === false) {
    if (rev !== null) confirmedRev = rev;   // the served CAS truth — downward is legitimate HERE
    inFlight = 0;                           // slots minted after the refused write are forged — never reused
    if (onRefused) onRefused();
  } else if (rev !== null) confirmedRev = Math.max(confirmedRev, rev);
  return true;
}

/** Stamp and post one whole-blob write. The blob is a clone of a pushed payload, so it may carry
 *  that payload's rev — server truth too, folded into the confirmed rev; the declared base is
 *  confirmed + writes actually in flight, one optimistic slot per outstanding write and no more. */
export function postViewsWrite(post: (m: Record<string, unknown>) => void, views: SessionViews): void {
  const v: SessionViews & { rev?: number; baseRev?: number } = { ...views };
  if (typeof v.rev === "number") confirmedRev = Math.max(confirmedRev, v.rev);
  v.baseRev = confirmedRev + inFlight;
  inFlight++;
  delete v.rev;                      // the stale payload rev never rides — baseRev is the declared base
  post({ type: "setTimelineViews", views: v });
}

/** Tests only: each test starts from a fresh counter. */
export function resetViewsWriterForTest(): void { confirmedRev = 0; inFlight = 0; }
