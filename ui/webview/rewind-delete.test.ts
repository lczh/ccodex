// Conversation rollback — delete a past message (SDK sessions): the bubble's delete affordance
// posts rewindDelete, which rolls the conversation back to just before that message with NOTHING
// sent (kernel _rewind_rollback → backend rollback(), the edit rewind's bare arm). The clicking
// client overlays the outcome — the deleted bubble AND the tail dim — until the kernel's cut
// payload arrives; unlike the edit flow no turn ever lands, so the kernel parse itself renders
// the cut (backend pending_cut → parse leaf_override) rather than waiting on the transcript.
// render.ts has import-time DOM side effects → source pins + an executed replica of the bare
// overlay decision (rewind-edit.test.ts precedent).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(
  path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(
  path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the delete affordance rides the edit gate and arms in two clicks", () => {
  // one shared eligibility gate: the delete button is built inside the SAME block as edit,
  // so it inherits every rule (SDK backend, post-compaction, real uuid, not optimistic)
  const gate = RENDER.indexOf("&& (sessions.get(editSid) as any)?._editable?.has(ev.uuid))");
  const del = RENDER.indexOf('el("button", "msg-del")');
  const acts = RENDER.indexOf('el("div", "msg-acts")');
  assert.ok(gate > 0 && del > gate && acts > del, "delete + acts row built inside the edit gate block");
  // two-click arm: the first click only arms; blur / pointerleave / a re-render disarm —
  // every miss fails toward "not deleted"
  assert.match(RENDER, /if \(!del\.classList\.contains\("armed"\)\) \{ del\.classList\.add\("armed"\); del\.textContent = "roll back\?"; return; \}/);
  assert.match(RENDER, /del\.addEventListener\("blur", disarm\);/);
  assert.match(RENDER, /del\.addEventListener\("pointerleave", disarm\);/);
});

test("the armed second click fires rewindDelete and paints the bare overlay", () => {
  assert.match(RENDER, /vscodeApi\?\.postMessage\(\{ type: "rewindDelete", id: sid, uuid \}\);/);
  assert.match(RENDER, /pendingRewind\.set\(sid, \{ uuid, text: "", ts: Date\.now\(\), bare: true \}\);/);
  // deleting the message currently loaded in the composer's edit mode cancels that edit
  assert.match(RENDER, /if \(ce && ce\.uuid === uuid\) cancelComposerEdit\(sid\);/);
  // overlay painted NOW, same as the edit flow (stale → window re-render)
  assert.match(RENDER, /function fireRewindDelete\(sid: string, uuid: string\): void \{/);
});

test("the bare overlay dims the deleted bubble itself, not just the tail", () => {
  assert.match(RENDER, /if \(pr\.bare\) \{/);
  assert.match(RENDER, /for \(let j = idx; j < s\.events\.length; j\+\+\) \(s\.events\[j\] as any\)\.rewound = true;/);
});

test("the delete button dresses like edit, with a destructive armed state", () => {
  assert.match(CSS, /\.msg-acts \{ display: flex; gap: 4px; align-self: flex-end; \}/);
  // restore-files shares delete's dress (same reveal, same destructive armed red — the user 2026-08-04)
  assert.match(CSS, /\.turn-user:hover \.msg-del, \.msg-del:focus-visible,\s*\n\.turn-user:hover \.msg-restorefiles, \.msg-restorefiles:focus-visible \{ opacity: 0\.9; \}/);
  assert.match(CSS, /\.msg-del\.armed, \.msg-restorefiles\.armed \{ color: #ff6a6a; border-color: #ff6a6a;/);
});

// Executed replica of the BARE keep/retire decision: dim from the deleted bubble (idx, not idx+1),
// no text replacement, no queued-chip suppression; retire when the kernel's cut payload drops the
// uuid, or on the TTL backstop (a refused rollback warn-toasts and un-cuts — the tail must return).
test("the bare overlay retires when the cut payload lands, or on TTL", () => {
  type Ev = { kind: string; uuid?: string; md?: string; rewound?: boolean };
  const REWIND_TTL_MS = 30_000;
  const apply = (events: Ev[], pr: { uuid: string; ts: number; bare: boolean }, now: number) => {
    const idx = events.findIndex((e) => e.kind === "user" && e.uuid === pr.uuid);
    if (idx < 0 || now - pr.ts > REWIND_TTL_MS) return { kept: false, events };
    for (let j = idx; j < events.length; j++) events[j].rewound = true;
    return { kept: true, events };
  };
  const T0 = 1_000_000;
  const pr = { uuid: "u2", ts: T0, bare: true };

  // the gap: the deleted bubble and everything after dim; nothing before it does
  const gap = apply([
    { kind: "user", uuid: "u1", md: "first" }, { kind: "assistant", uuid: "a1", md: "r1" },
    { kind: "user", uuid: "u2", md: "second ask" }, { kind: "assistant", uuid: "a2", md: "r2" },
  ], pr, T0 + 1000);
  assert.equal(gap.kept, true);
  assert.equal(gap.events[2].rewound, true);                // the deleted bubble itself dims
  assert.equal(gap.events[3].rewound, true);
  assert.equal(gap.events[1].rewound, undefined);           // the surviving prefix stays bright
  assert.equal(gap.events[2].md, "second ask");             // and keeps its text (nothing replaces it)

  // the kernel's cut payload landed: the deleted uuid is gone from the events → retire
  const landed = apply([
    { kind: "user", uuid: "u1", md: "first" }, { kind: "assistant", uuid: "a1", md: "r1" },
  ], pr, T0 + 3000);
  assert.equal(landed.kept, false);

  // TTL backstop: the rollback failed (kernel warn-toasted, payload un-cut) — stop dimming
  const expired = apply([{ kind: "user", uuid: "u2", md: "second ask" }], pr, T0 + REWIND_TTL_MS + 1);
  assert.equal(expired.kept, false);
});

// the kernel side of the contract this client codes against (node-tests-pin-kernel-source precedent)
test("kernel: rewindDelete sends nothing and the parse renders the pending cut", () => {
  const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  const BACKEND = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "sdk_backend.py"), "utf8");
  const EM = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "event_model.py"), "utf8");
  assert.match(KERNEL, /elif t == "rewindDelete" and msg\.get\("uuid"\):/);
  assert.match(KERNEL, /def _rewind_rollback\(sid, user_uuid, now=None\):/);
  assert.match(KERNEL, /leaf_override=cut or None/);
  assert.match(BACKEND, /def rollback\(self, sid: str, target_uuid: str\)/);
  assert.match(BACKEND, /def pending_cut\(self, sid: str\) -> str:/);
  assert.match(EM, /if leaf_override and leaf_override in self\.by_uuid:/);
});
