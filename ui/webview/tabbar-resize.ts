// Drag-to-resize state for the chat tab strip (the user 2026-08-18): #tabbar wraps its session tabs
// into rows and scrolls past a max-height cap, which clipped the fifth row of a many-session strip
// with no way to see more. The #tabbar-resize grip drags that cap; the strip stays a scroll pane at
// every size. Pure logic split out of render.ts so it can be unit-tested without a DOM (the
// time-marker.ts pattern).

// localStorage key: the dragged cap is per-viewer ARRANGEMENT, like the tab order (romp:vieworder) —
// a property of how you are looking at your sessions, not of the sessions.
export const TABBAR_H_KEY = "romp:tabbarH";
export const TABBAR_H_DEFAULT = 150;   // the CSS max-height when never dragged (~four rows)
export const TABBAR_H_MIN = 40;        // at least one tab row stays visible

// The dragged cap, clamped: at least one row, at most 60% of the window (the composer's convention,
// composerMaxH) — but never below the CSS default's reach, so a stored height survives a tiny window.
export function clampTabbarH(h: number, winH: number): number {
  const max = Math.max(TABBAR_H_DEFAULT, Math.round(winH * 0.6));
  return Math.max(TABBAR_H_MIN, Math.min(max, Math.round(h)));
}

// A stored height: a finite positive number, or null (never dragged / reset / garbage).
export function parseTabbarH(raw: string | null): number | null {
  if (!raw) return null;
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : null;
}
