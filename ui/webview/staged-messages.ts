// STAGED messages (the user 2026-08-15): compose against a highlight, hold it, keep reading — then
// release the whole run together. ⌘/Ctrl+⏎ stages the composer's text WITH its citation chips; a
// plain send flushes the stack in stage order with the typed message last; the strip's Send now
// releases the stack alone. Deliberately NOT "queued": queued is romp's injection-side wait (sent,
// pending injection into the session); staged is user-side — not sent at all, just held where it
// was written so each message keeps the context it was written against.
//
// This is the PURE stack — per-tab isolation, order, flush-clears, discard, persistence round-trip —
// so staged-messages.test.ts EXECUTES the rules instead of regexing render.ts (the repo's
// extract-for-execution idiom). The DOM strip and the send routing live in render.ts.

export interface StagedMsg { text: string; cites: unknown[] }

export class StagedStack {
  private m = new Map<string, StagedMsg[]>();

  list(sid: string): readonly StagedMsg[] { return this.m.get(sid) || []; }
  count(sid: string): number { return (this.m.get(sid) || []).length; }

  push(sid: string, msg: StagedMsg): void {
    const l = this.m.get(sid) || [];
    l.push(msg);
    this.m.set(sid, l);
  }

  removeAt(sid: string, i: number): void {
    const l = this.m.get(sid);
    if (!l) return;
    l.splice(i, 1);
    if (!l.length) this.m.delete(sid);
  }

  /** The flush: every staged message for this tab, in stage order, and the stack is now empty —
   *  release is one-shot, never a re-send. */
  takeAll(sid: string): StagedMsg[] {
    const l = this.m.get(sid) || [];
    this.m.delete(sid);
    return l;
  }

  /** Persistence shape (rides the drafts store): sid → messages. */
  entries(): Record<string, StagedMsg[]> {
    return Object.fromEntries(this.m);
  }

  /** Hydrate from a persisted shape; junk entries are dropped, never a crash. */
  restore(saved: unknown): void {
    if (!saved || typeof saved !== "object") return;
    for (const [sid, v] of Object.entries(saved as Record<string, unknown>)) {
      const list: StagedMsg[] = [];
      for (const m of Array.isArray(v) ? v : []) {
        if (m && typeof (m as any).text === "string" && (m as any).text)
          list.push({ text: (m as any).text, cites: Array.isArray((m as any).cites) ? (m as any).cites : [] });
      }
      if (list.length) this.m.set(sid, list);
    }
  }
}
