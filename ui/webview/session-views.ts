// Session views (the user 2026-08-18): the kernel's timeline-views blob, echoed on every tabOrder
// push — which sessions the chat TAB STRIP (and the timeline lanes) show. "all" shows everything
// except the hidden set — which reads as "removed from the DEFAULT GROUP": every session is in the
// default group at birth, and leaving it is what hiding means (the user 2026-08-19). A session out
// of the default group is a BACKGROUND session: still running, judged and carded;
// the feed and the + picker keep surfacing it, so nothing runs in secret — the 2026-08-11 rule); a
// named group shows exactly its members, membership beating the hidden bit. The kernel's
// _view_visible is the decision of record; this is its client mirror (the timeline carries its own
// copy — it cannot import TS). Pure, split out of render.ts for tests (the time-marker.ts pattern).
export interface SessionGroup { id: string; name?: string; color?: string; members?: string[] }
export interface SessionViews { active?: string; hidden?: string[]; groups?: SessionGroup[] }

export function viewVisible(views: SessionViews | null | undefined, id: string): boolean {
  if (!views || !views.active || views.active === "all")
    return !(views && Array.isArray(views.hidden) && views.hidden.includes(id));
  const g = (views.groups || []).find((x) => x.id === views.active);
  return g ? (g.members || []).includes(id) : true;
}

// one canonical serialization for echo comparison — the kernel normalizer re-sorts lists and may
// clamp names, so optimistic edits compare by shape, never by identity
export function viewsKey(v: SessionViews | null | undefined): string {
  if (!v) return "";
  return JSON.stringify({ active: v.active || "all",
    hidden: (v.hidden || []).slice().sort(),
    groups: (v.groups || []).map((g) => ({ id: g.id, name: g.name, color: g.color,
                                           members: (g.members || []).slice().sort() })) });
}

// hide: add the session to the hidden set — and when the ACTIVE view is a group that contains it,
// drop it from that group too, or the gesture is a silent no-op (membership beats hidden, so a
// member stays visible however hidden it is). Other groups keep it: hiding from what you are
// looking at must not quietly rewrite views you are not.
export function hideIn(views: SessionViews | null | undefined, id: string): SessionViews {
  const v: SessionViews = JSON.parse(JSON.stringify(views || {}));
  if (!(v.hidden || []).includes(id)) v.hidden = (v.hidden || []).concat([id]);
  if (v.active && v.active !== "all") {
    const g = (v.groups || []).find((x) => x.id === v.active);
    if (g && (g.members || []).includes(id)) g.members = (g.members || []).filter((x) => x !== id);
  }
  return v;
}

// the reveal gesture (reshaped 2026-08-19 with the DEFAULT-GROUP model): explicitly opening a
// session SWITCHES the active view to one that shows it and never mutates membership — peeking at
// a pool worker must not drag it back into the default group. Prefer the default group ("all" =
// everything not removed from it), else the first named group holding it; a session in NO view at
// all is re-added to the default group, the one case where visibility requires a membership edit.
export function revealIn(views: SessionViews | null | undefined, id: string): SessionViews {
  const v: SessionViews = JSON.parse(JSON.stringify(views || {}));
  if (viewVisible(v, id)) return v;
  if (!(v.hidden || []).includes(id)) { v.active = "all"; return v; }
  const holder = (v.groups || []).find((g) => (g.members || []).includes(id));
  if (holder) { v.active = holder.id; return v; }
  v.hidden = (v.hidden || []).filter((x) => x !== id);
  v.active = "all";
  return v;
}
