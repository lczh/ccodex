// The webview pages' ONE views writer (the 2026-08-26 audit's Finding A). The chat strip
// (render.ts postViews) and the Outline's tag lens (fleet.ts) each cloned the pushed views blob
// and posted {type:"setTimelineViews"} raw — the clone still carried the payload's rev N, so two
// quick gestures both declared base N to the kernel's compare-and-set and the SECOND was always
// refused: a rapid hide-then-reveal (or two lens picks) silently lost the later edit.
//
// SERIALIZED since the v1.3.20 audit: the r47 two-number counter (confirmed + in-flight) still
// PIPELINED whole-blob writes on guessed revisions — with W1 in flight and a foreign client
// committing, W1 was refused but stale W2's guessed base could COINCIDE with the foreign
// commit's rev and the kernel accepted a blob that never saw the foreign edit, silently erasing
// it. No guess survives here:
//  - at most ONE write is outstanding; later gestures queue;
//  - a whole-blob write declares baseRev = the last KERNEL-REPORTED rev, exactly — stamped at
//    POST time (dequeue), never at gesture time, so it is the freshest served truth;
//  - an ok:false ack drops every QUEUED blob (each was rendered on state the kernel just
//    refuted — re-posting one is exactly the coincide-erase) and hands the refusal to the
//    surface, which re-derives from the fresh payload;
//  - TARGETED ops (postViewsOps — the lens picks and their kin, the same audit's grammar
//    extension) ride the queue for ordering but carry no base at all: they compose server-side
//    under the kernel's lock, so they survive a blob refusal instead of being dropped.
//  - anchorViewsRev stays MONOTONIC (the r47 verification): federation re-emits the cached blob
//    with its old rev — a stale payload must not rewind the base.
// Pure and stateful-per-bundle (each pane has its own kernel socket and views stream), split out
// for tests like session-views.ts.
import { SessionViews } from "./session-views";

type QueuedWrite = { kind: "blob"; views: SessionViews; wireId?: string }
                 | { kind: "ops"; ops: Record<string, unknown>[]; wireId?: string };
let wireSeq = 0;   // per-page write ids — the ack correlation (the r49 verification)

let confirmedRev = 0;   // the last rev the KERNEL reported (payload push or ack) — never a guess
let outstanding = 0;    // 0 or 1: the one write whose ack we await
let outstandingKind: "blob" | "ops" | null = null;   // what kind it was (the r48 release rule)
let queue: QueuedWrite[] = [];
let poster: ((m: Record<string, unknown>) => void) | null = null;

/** Raise the confirmed rev to a pushed payload's views.rev — called on every views arrival
 *  (tabOrder push in the chat, feed push in the Outline). Monotonic: a re-emitted stale payload
 *  never rewinds the base below a write already outstanding (the r47 verification). */
export function anchorViewsRev(v: SessionViews | { rev?: unknown } | null | undefined): void {
  const rev = v && (v as { rev?: unknown }).rev;
  if (typeof rev === "number" && rev > confirmedRev) {
    confirmedRev = rev;
    if (outstanding > 0 && outstandingKind === "ops" && !queue.some((w) => w.kind === "blob")) {
      // the kernel demonstrably moved PAST our outstanding write and its ack never arrived (a
      // dropped frame — the r48 verification: the queue wedged forever on that missing ack).
      // The RAISED payload releases the slot ONLY when the outstanding write AND everything
      // queued are targeted ops: releasing around a BLOB anywhere lets a stale-rendered blob
      // post at the foreign rev, and the CAS would accept the very erase this module exists to
      // end (the raise cannot distinguish our own lost-ack success from a foreign commit).
      // Ops compose safely, so the common gestures — the lens picks — never wedge; a blob
      // waits for its ack (the kernel now acks failures too) or the reconnect reload that
      // resets this writer.
      outstanding = 0;
      outstandingKind = null;
      pump();
    }
  }
  if (retryOnNextAnchor && outstanding === 0 && queue.length) {
    retryOnNextAnchor = false;       // AFTER the raise-release above, so the two arms never
    pump();                          // double-pump one payload; the retryable-refused head
  }                                  // re-posts on the next payload (r53 P2.6)
}

function pump(): void {
  if (!poster || outstanding > 0 || !queue.length) return;
  const w = queue[0];               // the head STAYS queued until its ack retires it (the
  outstanding = 1;                  // v1.3.21 audit's P2.8: a send lost on an OPEN socket that
  outstandingKind = w.kind;         // then died was gone — nothing could ever replay it)
  w.wireId = "w" + (++wireSeq);     // fresh per POST: a replay's late twin ack must not match
  if (w.kind === "ops") {
    poster({ type: "setTimelineViewsOps", ops: w.ops, opId: w.wireId });
    return;
  }
  const v: SessionViews & { rev?: number; baseRev?: number } = { ...w.views };
  v.baseRev = confirmedRev;          // the served truth at POST time — never a gesture-time guess
  delete v.rev;                      // the stale payload rev never rides — baseRev is the declared base
  poster({ type: "setTimelineViews", views: v, opId: w.wireId });
}

/** The kernel's per-write acknowledgement (setTimelineViews CAS and setTimelineViewsOps alike —
 *  both wear the viewsAck dress). Returns whether the frame was consumed (so routers can skip
 *  their other cases). ok:false re-anchors to the served rev (the CAS truth, downward included),
 *  DROPS every queued blob, and invokes the surface's rollback — the write is KNOWN-refused, not
 *  merely unconfirmed. Queued targeted ops survive: they compose against whatever the store
 *  holds. A malformed rev leaves the confirmed rev standing (anchoring it to 0 was itself a
 *  rewind). The kernel's conflict strings ride INTO the callback (the v1.3.23 audit's P3.9):
 *  discarding them here left the chat/Outline dropping the optimistic tag with nothing saying
 *  why — the surface decides how to show them; this writer just hands them over. */
let retryOnNextAnchor = false;   // a RETRYABLE refusal holds the head for the next payload (r53)

export function consumeViewsAck(m: unknown,
                                onRefused?: (conflicts?: string[]) => void): boolean {
  const a = m as { type?: unknown; ok?: unknown; rev?: unknown; opId?: unknown;
                   conflicts?: unknown; retryable?: unknown } | null;
  if (!a || a.type !== "viewsAck") return false;
  const rev0 = typeof a.rev === "number" ? a.rev : null;
  if (typeof a.opId === "string" && (!queue.length || queue[0].wireId !== a.opId)) {
    // a SURPLUS or foreign-generation ack (the r49 verification: after a raise-release or a
    // reconnect re-post, the original send's late answer arrived alongside the replay's — and
    // an unconditional shift retired the WRONG head, cascading one write behind forever).
    // Its rev is still served truth; nothing else about it is ours to act on.
    if (rev0 !== null) confirmedRev = Math.max(confirmedRev, rev0);
    return true;
  }
  const conflicts = Array.isArray(a.conflicts)
    ? a.conflicts.filter((c): c is string => typeof c === "string" && !!c) : [];
  if (a.ok === false && a.retryable === true) {
    // the kernel is UP but its store was momentarily unreadable/unwritable (the r53 audit's
    // P2.6: the generic refusal dropped the queue head — a plain lens pick or tag edit
    // vanished with no retry and no useful warning). The head STAYS queued; the next payload
    // arrival (anchorViewsRev) re-pumps it — event-paced, never a timer.
    outstanding = 0;
    outstandingKind = null;
    retryOnNextAnchor = true;
    return true;
  }
  outstanding = 0;
  outstandingKind = null;
  queue.shift();                    // the acked head retires NOW (kept queued for replay until here)
  const rev = typeof a.rev === "number" ? a.rev : null;
  if (a.ok === false) {
    if (rev !== null) confirmedRev = rev;   // the served CAS truth — downward is legitimate HERE
    queue = queue.filter((w) => w.kind === "ops");   // queued blobs were rendered on refuted state
    if (onRefused) onRefused(conflicts.length ? conflicts : undefined);
  } else {
    if (rev !== null) confirmedRev = Math.max(confirmedRev, rev);
    if (conflicts.length && onRefused) {
      // a PARTIAL application (the r50 verification round): the kernel refused a duplicate-name
      // create/rename inside an otherwise-ok ops write and NAMES it here — the timeline twin
      // got the loud treatment, but this writer's callers (the chat strip's "New tag…", the
      // Outline) were still shown their optimistic tag over a store that never held it. The
      // refusal callback re-derives the surface from served truth; the queue stays — ops
      // compose against whatever the store holds, and nothing here refutes a queued write.
      onRefused(conflicts);
    }
  }
  pump();
  return true;
}

/** Transport reconnected (the v1.3.21 audit's P2.8): a send lost on the OLD socket — accepted
 *  by the browser, never delivered — left the one outstanding slot waiting forever, and the
 *  reconnect neither reloads this page nor replays it. The head is still queued (pump never
 *  drops it before its ack), and every write is safe to re-send: targeted ops are idempotent
 *  absolute gestures, and a blob re-post is CAS-protected. Reset the slot and re-post. */
export function notifyViewsTransportReset(): void {
  outstanding = 0;
  outstandingKind = null;
  pump();
}

/** Queue one whole-blob write. The blob is a clone of a pushed payload, so it may carry that
 *  payload's rev — server truth too, folded into the confirmed rev. The CAS base is stamped when
 *  the write actually POSTS (see pump), so a queued gesture declares the freshest served rev,
 *  never a guess made while an earlier write was still in flight. */
export function postViewsWrite(post: (m: Record<string, unknown>) => void, views: SessionViews): void {
  poster = post;
  const v: SessionViews & { rev?: number } = { ...views };
  if (typeof v.rev === "number") confirmedRev = Math.max(confirmedRev, v.rev);
  queue.push({ kind: "blob", views: v });
  pump();
}

/** Queue TARGETED ops (the v1.3.20 audit's grammar extension): the webview's gestures — the lens
 *  picks and the tag-membership edits — are expressible as ops that compose server-side under
 *  the kernel's lock, with no base to guess and nothing a foreign edit can be erased by. They
 *  ride the same one-outstanding queue for ordering. */
export function postViewsOps(post: (m: Record<string, unknown>) => void,
                             ops: Record<string, unknown>[]): void {
  poster = post;
  queue.push({ kind: "ops", ops });
  pump();
}

/** Tests only: each test starts from a fresh writer. */
export function resetViewsWriterForTest(): void {
  confirmedRev = 0; outstanding = 0; outstandingKind = null; queue = []; poster = null;
  retryOnNextAnchor = false;
}
