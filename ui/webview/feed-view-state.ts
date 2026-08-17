// Feed DISCLOSURE state that survives a reload (the user 2026-07-24, who lost every open section whenever
// the kernel restarted). Which card sections you opened, which sub-goal triangles you expanded, which modal
// nodes you collapsed — all of it lived only in module-level Sets/Maps, so the reload a kernel restart
// triggers wiped it and every card snapped back to its default.
//
// It persists to localStorage, NOT to a kernel file. This is per-view UI state: it belongs to the browser
// that holds it, it must survive a kernel restart WITHOUT a round-trip to the thing that just restarted, and
// it needs no server-side store to garbage-collect. The feed already keeps `romp:settings` this way.
//
// What is deliberately NOT persisted: the DOM element caches (askEls, groupEls, sessHeadEls) and every
// in-flight optimistic record (pendingCleared, pendingMoveAck, pendingDone, …). Those describe requests in
// flight against a kernel that no longer exists after a restart; restoring them would resurrect predictions
// for work nobody is doing. Only what the USER chose to open is worth carrying across.
//
// SELF-CLEANING is event-based, not a timer: every kernel payload names the full live card set, so any entry
// whose card is no longer in that set (cleared, archived) is dropped on the spot. Nothing ages out on a
// clock, and nothing accumulates for cards that no longer exist. CAP is a pure backstop against a bug in
// that rule, never the primary mechanism.

export interface FeedViewState {
  v: 1;
  sec: Record<string, string>;   // itemId -> open section ("bg" | "summary" | "subgoals" | "tasks" | "stall" | "none")
  tree: string[];                // "itemId:nodeId" — expanded triangles in a card's inline sub-goal tree
  nodes: string[];               // "askId:nodeId" — COLLAPSED modal tree nodes (inverted sense, see feed.ts)
  logs: string[];                // "askId:nodeId" — expanded per-node log stories
  asks: string[];                // expanded ask itemIds
  // COLLAPSED session sids (inverted sense, like `nodes`): in grouped mode the thread shows its header
  // alone. Keyed by SESSION, not by card, because the point is that cards which do not exist yet inherit
  // it — see the prune exemption below.
  threads: string[];
  // Stacked-layout COLUMN state (the user 2026-08-16): collapsed column keys ("asks"/"needsInput"/
  // "completed") and the user's dragged column order. Both describe the LAYOUT, not any card, so both
  // are prune-EXEMPT like `threads` and bounded by their three known keys instead.
  cols: string[];
  order: string[];
}

export const VIEW_STATE_KEY = "romp:feedview";
// Backstop only. The live-set prune below bounds the real size to what is on screen; this exists so a bug in
// that rule can never grow the entry unboundedly and wedge localStorage.
export const VIEW_STATE_CAP = 4000;

export function emptyViewState(): FeedViewState {
  return { v: 1, sec: {}, tree: [], nodes: [], logs: [], asks: [], threads: [], cols: [], order: [] };
}

/** Parse a stored blob. ANY malformed/foreign/old-version value reads as empty rather than throwing — a
 *  corrupt entry must cost you your open sections, never the whole feed. */
export function parseViewState(raw: string | null | undefined): FeedViewState {
  if (!raw) return emptyViewState();
  try {
    const o = JSON.parse(raw);
    if (!o || typeof o !== "object" || o.v !== 1) return emptyViewState();
    const arr = (x: unknown): string[] => (Array.isArray(x) ? x.filter((s) => typeof s === "string") : []);
    const sec: Record<string, string> = {};
    if (o.sec && typeof o.sec === "object") {
      for (const [k, v] of Object.entries(o.sec as Record<string, unknown>)) if (typeof v === "string") sec[k] = v;
    }
    // `threads` post-dates v1 blobs, so a stored entry without it reads as "nothing collapsed" rather
    // than as corrupt — no version bump, and yesterday's saved sections survive the upgrade.
    const col = (x: unknown): string[] =>
      arr(x).filter((k) => k === "asks" || k === "needsInput" || k === "completed");
    return { v: 1, sec, tree: arr(o.tree), nodes: arr(o.nodes), logs: arr(o.logs), asks: arr(o.asks),
             threads: arr(o.threads), cols: col(o.cols), order: col(o.order) };
  } catch {
    return emptyViewState();
  }
}

export function serializeViewState(s: FeedViewState): string {
  return JSON.stringify(s);
}

/** Does `key` belong to a card in `liveIds`? Keys are either a bare itemId or "<itemId>:<nodeId>".
 *
 *  Matched by whole-id prefix at EVERY colon rather than splitting on the first one, because real itemIds
 *  contain colons themselves ("provisional:<fsid>", "awaiting:<fsid>", "blocked:<fsid>", or a bare node id).
 *  Splitting on the first colon would read those as a card called "provisional" and prune state that is very
 *  much live.
 *
 *  This is ambiguous in principle: if some id were a strict colon-prefix of another, the shorter one would
 *  also claim the longer one's keys and a cleared card's state could linger. That needs an itemId equal to
 *  exactly "provisional"/"awaiting"/"blocked", which the kernel never emits — those tokens always carry a
 *  ":<fsid>". Where it is undecidable the bias is deliberately toward KEEPING state: a stale entry costs a
 *  few bytes and is bounded by the cap, whereas a wrong drop silently loses sections the user opened, which
 *  is the exact complaint this whole file exists to fix. */
export function keyIsLive(key: string, liveIds: Set<string>): boolean {
  if (liveIds.has(key)) return true;
  const c = key.indexOf(":");
  for (let i = c; i > 0; i = key.indexOf(":", i + 1)) {
    if (liveIds.has(key.slice(0, i))) return true;
  }
  return false;
}

/** Drop every entry whose card is no longer in the kernel's live set — the self-clean. Pass the FULL payload
 *  ids, never a view-filtered subset (`#only=` hides cards without ending them; pruning against the filtered
 *  list would silently discard the hidden cards' state). Call only once a payload has actually arrived: an
 *  empty live set legitimately means "no cards", and pruning against it before the first push would wipe
 *  everything the user just restored.
 *
 *  `threads` is EXEMPT, deliberately. Every other entry describes a card, so a card that is gone makes its
 *  entry meaningless; a collapsed thread describes a SESSION, and its whole purpose is to hold while that
 *  session has no cards on the board so the next one arrives collapsed too. Pruning it against the live set
 *  would silently re-expand a thread the moment its last card cleared — exactly the "collapse it and it
 *  stays collapsed" the feature is for. It is bounded by the cap instead. */
export function pruneViewState(s: FeedViewState, liveIds: Set<string>): FeedViewState {
  const sec: Record<string, string> = {};
  for (const [k, v] of Object.entries(s.sec)) if (keyIsLive(k, liveIds)) sec[k] = v;
  const keep = (xs: string[]) => xs.filter((k) => keyIsLive(k, liveIds));
  return capViewState({ v: 1, sec, tree: keep(s.tree), nodes: keep(s.nodes), logs: keep(s.logs),
                        asks: keep(s.asks), threads: s.threads, cols: s.cols, order: s.order });
}

export function viewStateSize(s: FeedViewState): number {
  return Object.keys(s.sec).length + s.tree.length + s.nodes.length + s.logs.length + s.asks.length
    + s.threads.length;
}

/** Backstop trim. Order is deliberate: the per-card SECTION choice is the state the user notices losing, so
 *  it is trimmed last; the per-node tree/log expansions are cheap to re-open and go first. Collapsed
 *  THREADS sit just above sec — they are few (one per session, not per card) and the most durable choice
 *  here, so they are the last thing to give before the sections do. */
export function capViewState(s: FeedViewState, cap: number = VIEW_STATE_CAP): FeedViewState {
  let over = viewStateSize(s) - cap;
  if (over <= 0) return s;
  const out: FeedViewState = { ...s, tree: [...s.tree], nodes: [...s.nodes], logs: [...s.logs],
                               asks: [...s.asks], threads: [...s.threads] };
  for (const f of ["logs", "nodes", "tree", "asks", "threads"] as const) {
    if (over <= 0) break;
    const drop = Math.min(over, out[f].length);
    out[f] = out[f].slice(drop);
    over -= drop;
  }
  if (over > 0) {
    const sec: Record<string, string> = {};
    const ks = Object.keys(out.sec).slice(over);
    for (const k of ks) sec[k] = out.sec[k];
    out.sec = sec;
  }
  return out;
}
