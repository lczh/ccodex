// Fork a session (the user 2026-08-13): a NEW parallel session branches from just before a chosen user
// message (the bubble's hover "fork") or from the tip (the palette's "Fork this session…"); the parent
// is untouched and both continue as separate threads. The modal asks the new name — default
// "<session>-fork", editable — and the provisional tab is the instant acknowledgement, joined by NAME
// exactly like a picker create. Source-level pins (no jsdom for the chat renderer), plus the kernel
// side of the contract (node-tests-pin-kernel-source precedent).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const PALETTE = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "palette-main.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const BACKEND = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "sdk_backend.py"), "utf8");

test("a fork button rides each editable bubble — non-destructive, accent hover, single click to the modal", () => {
  assert.match(RENDER, /const fk = el\("button", "msg-fork"\) as HTMLButtonElement;/);
  assert.match(RENDER, /fk\.addEventListener\("click", \(e\) => \{ e\.stopPropagation\(\); showForkPrompt\(editSid, uuid\); \}\);/);
  assert.match(RENDER, /acts\.appendChild\(fk\);/);
  // fork is not a rewind: no two-click arm (the modal is the confirmation), and never the destructive red
  assert.match(CSS, /\.msg-fork:hover \{ color: var\(--fg\); border-color: var\(--accent\); \}/);
  assert.doesNotMatch(CSS, /\.msg-fork\.armed/);
});

test("the modal defaults to <session>-fork and posts forkSession {id, uuid, name}", () => {
  assert.match(RENDER, /function showForkPrompt\(sid: string, uuid: string\): void \{/);
  assert.match(RENDER, /input\.value = base \+ "-fork";/);
  assert.match(RENDER, /if \(!\/\^\[A-Za-z0-9._-\]\+\$\/\.test\(name\)\) \{ input\.classList\.add\("bad"\); input\.focus\(\); return; \}/);
  assert.match(RENDER, /vscodeApi\?\.postMessage\(\{ type: "forkSession", id: sid, uuid, name \}\);/);
  // the instant acknowledgement is the provisional tab, name-joined like a picker create
  assert.match(RENDER, /openProvisional\(\{ name, backend: "sdk", dir: "", host: hostOf\(sid\) \}\);/);
  // both cut semantics are said in the dialog itself
  assert.match(RENDER, /continues from just before this message/);
  assert.match(RENDER, /continues this whole conversation/);
  assert.match(CSS, /\.fork-name \{ display: block; width: 100%;/);
});

test("the palette forks the ACTIVE session from the tip, via the chat pane", () => {
  assert.match(PALETTE, /id: "session\.fork", title: "Fork this session…"/);
  assert.match(PALETTE, /pane\("f-chat"\)!\.contentWindow!\.postMessage\(\{ romp: "forkSession" \}, "\*"\)/);
  assert.match(RENDER, /if \(m\.romp === "forkSession"\) \{/);
  assert.match(RENDER, /if \(activeId && !isProvisionalId\(activeId\) && sessions\.get\(activeId\)\) showForkPrompt\(activeId, ""\);/);
});

test("kernel: forkSession is a session op; seeding precedes discoverability; the fsid is pinned to the sid", () => {
  assert.match(KERNEL, /"mcpAction", "forkSession",/);   // in ID_OPS — routed by session id like every session op
  assert.match(KERNEL, /elif t == "forkSession" and msg\.get\("name"\):/);
  assert.match(KERNEL, /def _fork_session\(parent_sid, cut_msg_uuid, new_name, now=None\):/);
  // the cut means the same thing the edit/delete rewind means: just before the clicked user message
  assert.match(KERNEL, /cut_uuid, err = _rewind_target\(sess\["path"\], parent_sid, str\(cut_msg_uuid\)\)/);
  // the judge stores are seeded BEFORE be.fork writes the names/ entry (discoverability)
  assert.match(KERNEL, /err = _seed_fork_stores\(parent_sid, sid, sess\["path"\], cut_uuid\)[\s\S]{0,200}be\.fork\(nm, parent_sid, cut_uuid, bg, fg, sid=sid\)/);
  // the backend rides the SDK's designed fork contract, with the new fsid PINNED to the romp sid
  assert.match(BACKEND, /kw\["fork_session"\] = True/);
  assert.match(BACKEND, /"forkOf": parent_sid, "forkAt": cut_uuid or ""/);
  // one-shot: the init's lastSid flip spends the flags, so a reconnect resumes the fork's own transcript
  assert.match(BACKEND, /if self\._fork_of and fsid == self\.sid:/);
  assert.match(BACKEND, /self\.backend\._update_reg\(self\.sid, forkOf="", forkAt=""\)/);
  // …and the names/ entry is written LAST (it is the discoverability trigger)
  assert.match(BACKEND, /write_reg\(self\.state_dir, sid, reg\)[\s\S]{0,400}write_name\(self\.state_dir, sid, name, cwd, bg, fg\)[\s\S]{0,200}append_state\(self\.state_dir, sid, "waiting"\)/);
});

// ── branch lineage (the user 2026-08-13: branching must SHOW) ───────────────────────────────────

test("a forked session renders its branch divider, deep-linked to the parent", () => {
  assert.match(RENDER, /\| \{ kind: "branch"; fromSid\?: string; fromName\?: string; cut\?: string/);
  assert.match(RENDER, /if \(ev\.kind === "branch"\) \{/);
  assert.match(RENDER, /label\.dataset\.act = "branchjump"/);
  assert.match(RENDER, /"Branched from " \+ \(ev\.fromName \|\| "another session"\)/);
});

test("the parent wears a chip where each branch departed, jumping to the child's divider", () => {
  assert.match(RENDER, /function applyBranchChips\(sid: string, v: View\)/);
  assert.match(RENDER, /chip\.dataset\.cut = "branch:" \+ k\.cut/);
  assert.match(RENDER, /applyBranchChips\(sid, v\);\s+\/\/ same driver, same hooks/);
  assert.match(RENDER, /branchjump: \(elx\) =>/);
});

test("kernel persists lineage durably and serves it on the session payload", () => {
  // forkOf/forkAt are one-shot launch flags — forkedFrom is the durable record
  assert.match(BACKEND, /reg\["forkedFrom"\] = \{"sid": parent_sid, "name": parent\.get\("name", ""\)/);
  assert.match(BACKEND, /lineage_cut = cut_uuid or last_record_uuid\(/);
  assert.match(BACKEND, /def fork_children\(self\)/);
  assert.match(KERNEL, /"branch": branch, "branches": _kids,/);
  assert.match(KERNEL, /"kind": "branch", "uuid": "branch:" \+ branch\["cut"\]/);
});

test("branch chrome wears the accent, like every highlight", () => {
  const css = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
  assert.match(css, /\.branch-divider::before, \.branch-divider::after \{[^}]*var\(--accent\)/s);
  assert.match(css, /\.branch-chip \{[^}]*color: var\(--accent\)/s);
});
