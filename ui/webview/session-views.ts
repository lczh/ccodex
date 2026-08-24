// Session views (the user 2026-08-18; TAG model 2026-08-23; ALL default 2026-08-24): the kernel's
// timeline-views blob, echoed on every tabOrder push — which sessions the chat TAB STRIP (and the
// timeline lanes) show. TWO built-in sentinels, not tags: "all" — the DEFAULT — shows every session
// minus the `hidden` set (the manual one-off hide, deliberate, so All respects it); "untagged"
// keeps the old default's meaning under its own honest name — a TAG marks a SPECIALIZED session,
// excluded from the untagged view and shown under its tag views. "all" used to MEAN untagged, so
// reinterpreting it as truly-all lands every legacy persisted blob on the new All default with no
// migration. A hidden session is a BACKGROUND session: still running, judged and carded; the
// feed, the + picker, and the "N more" cue keep surfacing it, so nothing runs in secret — the
// 2026-08-11 rule. A tag view shows exactly its members. The kernel's _view_visible is the decision
// of record; this is its client mirror (the timeline carries its own copy — it cannot import TS).
// Pure, split out of render.ts for tests (the time-marker.ts pattern). The kernel emits `tags`;
// `groups` survives in the type as the pre-rename key an un-updated kernel still pushes.
export interface SessionTag { id: string; name?: string; color?: string; members?: string[]; host?: string }
export interface SessionViews {
  active?: string; hidden?: string[]; tags?: SessionTag[]; groups?: SessionTag[];
  // tag federation v0 (the user 2026-08-24): each ATTACHED kernel's own tags, read-only, joined by
  // the kernel per host (id = "host:tagid", members already respelled viewer-relative). Derived —
  // never an edit target, excluded from the echo key, dropped by the kernel if echoed back.
  remoteTags?: SessionTag[];
}

// the one place the legacy key is honored, so every rule below reads through it
export function viewTags(views: SessionViews | null | undefined): SessionTag[] {
  return (views && (views.tags || views.groups)) || [];
}

export function viewVisible(views: SessionViews | null | undefined, id: string): boolean {
  if (!views || !views.active || views.active === "all") {
    return !(views && Array.isArray(views.hidden) && views.hidden.includes(id));
  }
  if (views.active === "untagged") {
    if (Array.isArray(views.hidden) && views.hidden.includes(id)) return false;
    return !viewTags(views).some((t) => (t.members || []).includes(id));
  }
  // a tag view shows the NAME-KEYED UNION (user ruling 2026-08-24: a tag is its NAME; kernels are
  // plumbing) — whichever store's id is active, membership joins every same-name tag's
  const act = viewTags(views).find((x) => x.id === views.active)
    || (views.remoteTags || []).find((x) => x.id === views.active);
  if (!act) return true;
  const same = viewTags(views).concat(views.remoteTags || []).filter((x) => x.name === act.name);
  return same.some((t) => (t.members || []).includes(id));
}

// one canonical serialization for echo comparison — the kernel normalizer re-sorts lists and may
// clamp names, so optimistic edits compare by shape, never by identity
export function viewsKey(v: SessionViews | null | undefined): string {
  if (!v) return "";
  return JSON.stringify({ active: v.active || "all",
    hidden: (v.hidden || []).slice().sort(),
    tags: viewTags(v).map((t) => ({ id: t.id, name: t.name, color: t.color,
                                    members: (t.members || []).slice().sort() })) });
}

// hide: add the session to the hidden set — and when the ACTIVE view is a tag that contains it,
// drop it from that tag too, or the gesture is a silent no-op (membership shows it there however
// hidden it is). Other tags keep it: hiding from what you are looking at must not quietly rewrite
// views you are not.
export function hideIn(views: SessionViews | null | undefined, id: string): SessionViews {
  const v: SessionViews = JSON.parse(JSON.stringify(views || {}));
  if (!(v.hidden || []).includes(id)) v.hidden = (v.hidden || []).concat([id]);
  if (v.active && v.active !== "all") {
    const t = viewTags(v).find((x) => x.id === v.active);
    if (t && (t.members || []).includes(id)) t.members = (t.members || []).filter((x) => x !== id);
  }
  return v;
}

// the reveal gesture (re-grounded 2026-08-23 on the TAG model; ALL default 2026-08-24): explicitly
// opening a session lands on a visible tab with the MINIMAL move and never mutates membership —
// peeking at a tagged worker must not strip its tag. Under All only the hidden bit can hide a
// session, so a focus unhides it and STAYS — it never kicks the user off the all-sessions view.
// Elsewhere: a tagged session's home is its first holder tag; a tagless one is unhidden if hidden,
// switching to All only if still invisible (an unhidden tagless session already shows in the
// untagged view — no gratuitous view change).
export function revealIn(views: SessionViews | null | undefined, id: string): SessionViews {
  const v: SessionViews = JSON.parse(JSON.stringify(views || {}));
  if (viewVisible(v, id)) return v;
  if (!v.active || v.active === "all") {
    v.hidden = (v.hidden || []).filter((x) => x !== id);
    return v;
  }
  const holder = viewTags(v).find((t) => (t.members || []).includes(id));
  if (holder) { v.active = holder.id; return v; }
  if ((v.hidden || []).includes(id)) v.hidden = (v.hidden || []).filter((x) => x !== id);
  if (!viewVisible(v, id)) v.active = "all";
  return v;
}
