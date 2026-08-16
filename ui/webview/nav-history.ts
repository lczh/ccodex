// Chat navigation history — back/forward through visited transcript spots, the way Obsidian walks
// its history (the user 2026-08-14, whose own vault binds nav-back/-forward; verified from the
// vault's hotkeys.json as Ctrl+M / Ctrl+, — they remembered Option, the config says Ctrl, and the
// config wins). The chat records a spot on EVERY navigation through setActive — a tab switch, a
// feed/outline/timeline jump landing on an anchor — and the two keys walk that trail across tabs
// and positions. PURE (deps injected) so nav-history.test.ts EXECUTES the rules — dedup of
// same-spot re-records, forward-trail truncation on a new move, dead-tab skip, the cap — instead
// of regexing render.ts. Chat-only for now, by the user's scoping.

export interface NavSpot { sid: string; top: number }

export interface NavDeps {
  now(): NavSpot | null;             // where the chat is right this moment (null: no active tab)
  alive(sid: string): boolean;       // the tab still exists (a closed session's spots are skipped)
  apply(spot: NavSpot): void;        // land there (switch the tab + restore the scroll position)
}

const CAP = 100;                     // plenty of trail, bounded memory
const SAME_TOP = 40;                 // px: re-recording the same reading spot is one entry, not many

export class NavHistory {
  private back: NavSpot[] = [];
  private fwd: NavSpot[] = [];
  private applying = false;

  constructor(private deps: NavDeps) {}

  /** Record the spot being LEFT — called before a navigation applies (setActive's first act). A
   *  history jump must never record itself: `applying` latches while apply() runs, so the
   *  setActive it triggers records nothing. */
  record(): void {
    if (this.applying) return;
    const s = this.deps.now();
    if (!s) return;
    const last = this.back[this.back.length - 1];
    if (last && last.sid === s.sid && Math.abs(last.top - s.top) < SAME_TOP) return;
    this.back.push(s);
    if (this.back.length > CAP) this.back.shift();
    this.fwd.length = 0;             // a NEW move truncates the forward trail (the browser rule)
  }

  /** Walk the trail: dir -1 = back, 1 = forward. Spots on closed tabs are skipped, not shown.
   *  Returns whether anywhere was left to go. */
  go(dir: -1 | 1): boolean {
    const from = dir < 0 ? this.back : this.fwd;
    const to = dir < 0 ? this.fwd : this.back;
    let spot = from.pop();
    while (spot && !this.deps.alive(spot.sid)) spot = from.pop();
    if (!spot) return false;
    const cur = this.deps.now();
    if (cur) to.push(cur);
    this.applying = true;
    try { this.deps.apply(spot); } finally { this.applying = false; }
    return true;
  }
}
