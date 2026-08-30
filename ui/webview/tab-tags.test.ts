// The chat tab menu's TAGS section (the user 2026-08-24, overruling the earlier skip: tag editing
// belongs everywhere a session is in front of you). Same semantics as the timeline dialog — the
// name-keyed union rules bind (kernels are plumbing, never a host prefix in presentation), edits
// reuse the wire (local = the whole blob via postViews; remote-homed = the editTag op family) —
// never a forked implementation. Executable union coverage + source pins (no jsdom for render.ts).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { viewTagUnion } from "./session-views";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("executable: the union joins local and remote tags BY NAME — one group, local id/colour winning", () => {
  const u = viewTagUnion({
    active: "all", hidden: [],
    tags: [{ id: "g1", name: "pool", color: "#1EA1EB", members: ["S1"] }],
    remoteTags: [
      { id: "TESTHOST:g9", name: "pool", color: "#999999", members: ["TESTHOST:S7"], host: "TESTHOST" },
      { id: "TESTHOST:g8", name: "ops", color: "#54B204", members: ["TESTHOST:S7"], host: "TESTHOST" },
    ],
  });
  assert.equal(u.length, 2, "pool unions across kernels; ops stands alone");
  const pool = u.find((g) => g.name === "pool")!;
  assert.equal(pool.localId, "g1", "the local store is the union's write-home for adds");
  assert.equal(pool.color, "#1EA1EB", "the local colour wins");
  assert.deepEqual(pool.members.sort(), ["S1", "TESTHOST:S7"], "membership is the union");
  assert.equal(pool.remotes.length, 1);
  const ops = u.find((g) => g.name === "ops")!;
  assert.equal(ops.localId, null, "a remote-only tag has no local write-home");
  assert.equal(ops.remotes[0].host, "TESTHOST");
});

test("the Tags row sits with the session controls ABOVE the divider; Browse stays last", () => {
  const at = RENDER.indexOf("function showTabMenu");
  const body = RENDER.slice(at, RENDER.indexOf("document.body.appendChild(menu);", at));
  const tagsAt = body.indexOf('l.textContent = "Tags"');
  const browseAt = body.indexOf('l.textContent = "Browse files"');
  assert.ok(tagsAt > 0 && browseAt > 0 && tagsAt < browseAt, "Tags above, Browse last");
  assert.match(body.slice(tagsAt - 500, tagsAt), /ctxIcon\("tag", false\)/, "the tag icon");
  assert.match(body, /return names\.length \? names\.join\(" · "\) : "none yet — tag it to organize and dispatch";/,
    "the compact one-line row: current names, or the honest empty state");
});

test("edits reuse the wire — never a fork: local adds post the whole blob, remote edits ride editTag", () => {
  const at = RENDER.indexOf("const editUnion = (g: TagUnion");
  const body = RENDER.slice(at, at + 9800);   // widened for the r52/r53 journal-first gesture
  assert.ok(body.includes("t.members = Array.from(new Set((t.members || []).concat(edit.add)));"),
    "local add edits the optimistic copy (pendingSessionViews echoes instantly)");
  assert.ok(body.includes("localOps.push({ tag: g.localId, add: edit.add.slice() });"),
    "…and records the TARGETED op the kernel composes (the v1.3.20 audit — no whole-blob CAS)");
  assert.ok(body.includes('vscodeApi?.postMessage({ type: "editTag", edit: { opId: "web" + (++webEditSeq),  host: g.remotes[0].host || "", name: g.name, add: edit.add.slice() } });'),
    "an add with no local home routes to the tag's single home over the editTag wire — WITH a "
    + "web-minted opId (r48: an opId-less refusal swept the timeline's newest gesture)");
  assert.ok(body.includes("const candidates = g.remotes.filter((rt) =>"),
    "a REMOVE walks every remote store holding the pair — remove-everywhere, never half");
  assert.ok(body.includes("if (localOps.length) postViews(nv, localOps);"),
    "the LOCAL half posts its collected targeted ops (the v1.3.20 audit; blob-less for "
    + "remote-only gestures since r52)");
  assert.ok(body.includes("const nvRemote = (rt: SessionTag)"),
    "the remote entries mirror optimistically too — echoed remoteTags are derived, kernel-dropped, presentation-only");
  assert.match(RENDER, /x\.title = "remove this tag from the session — everywhere it holds it";/);
});

test("New tag… is an inline input (menu vocabulary, no native prompt) that creates locally with a palette colour", () => {
  assert.match(RENDER, /inp\.placeholder = "New tag…"; inp\.maxLength = 40;/);
  assert.doesNotMatch(RENDER.slice(RENDER.indexOf("const editUnion")), /window\.prompt/);
  assert.match(RENDER, /const color = paletteColors\.find\(\(c\) => !used\.has\(c\)\) \|\| paletteColors\[0\] \|\| "#1EA1EB";/);
  assert.match(RENDER, /id: "g" \+ Date\.now\(\)\.toString\(36\) \+ "-" \+ Math\.random\(\)\.toString\(36\)\.slice\(2, 8\)/,
    "the id carries randomness — ms-only ids silently discarded a simultaneous create (v1.3.21)");
  assert.match(RENDER, /postViews\(nv, \[\{ create: tg \}\]\);/,
    "the create rides the targeted op — it composes, no CAS base (the v1.3.20 audit)");
  // an existing name typed into the box ADDS to that union instead of minting a duplicate tag
  assert.match(RENDER, /const existing = unionFor\(\)\.find\(\(g\) => g\.name === name\);/);
});

test("presentation: one chip per NAME, identity dot, ✕ — and never a host prefix in the flyout", () => {
  assert.match(RENDER, /lb\.textContent = g\.name; bodyE\.appendChild\(lb\);/);
  assert.match(RENDER, /lb\.textContent = "\+ " \+ g\.name; bodyE\.appendChild\(lb\);/);
  assert.match(CSS, /\.ctx-tag-dot \{ flex: 0 0 auto; width: 9px; height: 9px; border-radius: 50%; \}/);
  const fly = RENDER.slice(RENDER.indexOf("const sub = el(\"div\", \"ctx-menu ctx-sub ctx-sub-tags\");"));
  assert.doesNotMatch(fly.slice(0, 2500), /host-prefix|hostNameNodes/, "kernels are plumbing — no host chrome in the flyout");
});

test("the menu groups BY KIND: [Rename+colors] / [toggles+billing+Tags] / [Browse] (the user 2026-08-24, final ruling)", () => {
  // supersedes 644's single top section: aesthetic controls together at the top, the
  // behavior/membership controls as the middle section, Browse alone at the bottom
  const at = RENDER.indexOf("function showTabMenu");
  const body = RENDER.slice(at, RENDER.indexOf("document.body.appendChild(menu);", at));
  const renameAt = body.indexOf('l.textContent = "Rename"');
  assert.ok(renameAt > 0, "Rename wears the label span like its siblings");
  assert.match(body.slice(renameAt - 400, renameAt), /ctxIcon\("pencil", false\)/, "…and the pencil icon");
  assert.match(body, /sb\.textContent = "the name is a label — mail, goals and history follow the session";/,
    "…and a sub-line saying what a rename preserves (uuid-keyed truth)");
  const colorsAt = body.indexOf('el("div", "ctx-colors")');
  const firstToggleAt = body.indexOf('toggle("feed"');
  const tagsAt = body.indexOf('l.textContent = "Tags"');
  const browseAt = body.indexOf('l.textContent = "Browse files"');
  assert.ok(renameAt < colorsAt && colorsAt < firstToggleAt && firstToggleAt < tagsAt && tagsAt < browseAt,
    "order: Rename, colors, toggles, Tags, Browse");
  // one divider between colors and the toggles; NONE inside section 1 or section 2
  assert.ok(!body.slice(renameAt, colorsAt).includes('el("div", "ctx-sep")'), "Rename+colors are one section");
  assert.ok(body.slice(colorsAt, firstToggleAt).includes('menu.appendChild(el("div", "ctx-sep"));'), "a divider splits sections 1/2");
  assert.ok(!body.slice(firstToggleAt, tagsAt).includes('el("div", "ctx-sep")'),
    "toggles, billing and Tags are ONE behavior section — no inner dividers");
  assert.ok(body.slice(tagsAt, browseAt).includes('menu.appendChild(el("div", "ctx-sep"));'), "a divider splits sections 2/3 — Browse alone at the bottom");
});

test("the chat's remove-everywhere journals compensation BEFORE any effect (r52 P2.5 + P1.4)", () => {
  // the v1.3.24 audit: this gesture posted remote edits and committed local ops with no
  // setUnionOps entries at all — a later remote refusal could neither restore the local half
  // nor roll back successful siblings; and nothing durable preceded the dispatches
  const at = RENDER.indexOf("const editUnion = (g: TagUnion");
  const win = RENDER.slice(at, at + 6000);
  const iJournal = win.indexOf('type: "setUnionOps"');
  const iGate = win.indexOf("pendingUnionGestures.set(ackId");
  const iDispatch = win.indexOf("opId: String(gid)");
  assert.ok(iJournal > 0, "the compensation journal exists");
  assert.ok(iGate > iJournal, "the effects are HELD behind the journal's ack (the r52 "
    + "verification: a refused write still dispatched, fail-open on exactly the failure "
    + "the journal exists for)");
  assert.ok(iDispatch > iGate, "the remote dispatch — riding the gesture's gid as its opId — "
    + "lives inside the gated commit");
  assert.ok(win.indexOf("inverse: willLocal ? { tag: g.localId, add: had.slice() } : {}") > 0,
    "each entry carries the LOCAL-half inverse when one exists — and journals EVEN WITHOUT "
    + "one (the r53 audit's P1.3: remote-only two-owner removes had zero rows, so A applied "
    + "while B refused and A was never rolled back)");
  assert.ok(win.indexOf("candidates.length >= 2 || (willLocal && candidates.length)") > 0,
    "every multi-owner gesture journals");
});

test("the chat's gated gesture carries the r53 settlement fields; paint and flip ride the ack", () => {
  // the r53 verification round, chat leg: (a) entries journaled without lop/dispatched fell
  // out of the timeline's local-leg settlement and adoption-completion — a chat webview dying
  // between the ack and the dispatch stranded a half-run gesture no panel could finish;
  // (b) the gated remote-only remove painted nothing — the member sat in the flyout until the
  // owner's next push; (c) rows left saying dispatched:false after the effects DID run invite
  // the adoption pass to re-run them — a re-remove over a member the user just re-added
  const at = RENDER.indexOf("const editUnion = (g: TagUnion");
  const win = RENDER.slice(at, at + 9800);
  assert.ok(win.indexOf("lop: localOp, dispatched, lapplied: false") > 0,
    "entries wear the same settlement dress as the timeline twin's mirror");
  const iGate = win.indexOf("pendingUnionGestures.set(ackId");
  const iPaint = win.indexOf("const mine = (nv2.remoteTags || []).find((x) => x.id === rt.id);");
  assert.ok(iPaint > iGate, "the optimistic remote paint rides INSIDE the gated continuation");
  const iFlip = win.indexOf("mkEntries(true)");
  assert.ok(win.indexOf("mkEntries(false)") > 0 && iFlip > iGate,
    "the dispatched:true flip re-posts the rows the moment the effects run — never left "
    + "for an adopting panel to re-dispatch");
});

test("the chat's gated gesture honors the kernel's unclaimed verdict (r55 P1.3)", () => {
  // the audit's executed repro: an ack carrying unclaimed:[gid] still executed remote A,
  // remote B, and the local removal — the exact double dispatch the claim exists to end
  const at = RENDER.indexOf("pendingUnionGestures.set(ackId");
  const win = RENDER.slice(at - 200, at + 400);
  assert.ok(win.indexOf("pendingUnionGestures.set(ackId, { gid, run: () => {") > 0,
    "the pending gesture carries its gid — the ack's verdict needs it");
  const rt = RENDER.indexOf('m.type === "unionOpsAck"');
  const rw = RENDER.slice(rt, rt + 1200);
  assert.ok(rw.indexOf("m.unclaimed.includes(go.gid)") > 0,
    "an unclaimed answer runs NOTHING — the claim holder's effects are the only ones");
  assert.ok(rw.indexOf("go.run()") > rw.indexOf("m.unclaimed.includes(go.gid)"),
    "…and the run sits behind that check");
});

test("remote-only chat tag edits post NO local write (r52 P2.9)", () => {
  // the v1.3.24 audit: the remoteTags mutation is derived presentation the kernel discards —
  // the whole-blob post it used to ride bumped the rev over a byte-identical store and could
  // 409 another client's real CAS write
  const at = RENDER.indexOf("const editUnion = (g: TagUnion");
  const win = RENDER.slice(at, at + 9800);   // widened for the r53 gated continuation
  assert.match(win, /if \(localOps\.length\) postViews\(nv, localOps\);/,
    "a LOCAL half still writes through the shared writer");
  assert.match(win, /pendingSessionViews = nv; pendingViewsAge = 0;/,
    "…while a remote-only gesture keeps only the optimistic overlay — no wire write");
  assert.doesNotMatch(win, /postViews\(nv, localOps\.length \? localOps : undefined\)/,
    "the old unconditional post — blob fallback included — is gone");
});
