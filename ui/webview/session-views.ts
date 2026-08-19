// Session views (the user 2026-08-18): the kernel's timeline-views blob, echoed on every tabOrder
// push — which sessions the chat TAB STRIP (and the timeline lanes) show. "all" shows everything
// except the hidden set (a hidden session is a BACKGROUND session: still running, judged and carded;
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

// the reveal gesture: explicitly opening a hidden session always shows it — drop its hidden bit,
// and when the active group excludes it, fall back to All. One predictable rule.
export function revealIn(views: SessionViews | null | undefined, id: string): SessionViews {
  const v: SessionViews = JSON.parse(JSON.stringify(views || {}));
  v.hidden = (v.hidden || []).filter((x) => x !== id);
  if (v.active && v.active !== "all") {
    const g = (v.groups || []).find((x) => x.id === v.active);
    if (!g || !(g.members || []).includes(id)) v.active = "all";
  }
  return v;
}
