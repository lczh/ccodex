// Composer drafts must survive a full RELOAD, not just a tab switch (the user 2026-06-25: a half-typed message
// "doesn't pop up" after a refresh). The `drafts` Map is in-memory, so it's mirrored into the webview's
// persisted state (the same store that remembers the active tab) and reloaded at startup; an in-progress draft
// is captured on every keystroke and restored ONCE into the box after load, without clobbering live typing.
// No jsdom for this renderer, so pin the wiring at source (the repo convention — see tab-switch-defer.test.ts).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("drafts are persisted to and reloaded from the webview's saved state", () => {
  // persist: mirror the Map into setState, alongside (not replacing) whatever else is saved — plus
  // citations, files, and the staged stack (2026-08-15)
  assert.match(RENDER, /function persistDrafts\(\): void \{[\s\S]*setState\?\.\(\{ \.\.\.\(vscodeApi\.getState\?\.\(\) \|\| \{\}\), drafts: Object\.fromEntries\(drafts\),[\s\S]*citations: Object\.fromEntries\(composerCitations\),[\s\S]*files: Object\.fromEntries\(composerFiles\),[\s\S]*staged: stagedMsgs\.entries\(\) \}\)/);
  // reload: hydrate the Map from saved state at startup (string values only)
  assert.match(RENDER, /const saved = \(\(vscodeApi\?\.getState\?\.\(\) \|\| \{\}\) as any\)\.drafts;/);
  assert.match(RENDER, /for \(const \[k, v\] of Object\.entries\(saved\)\) if \(typeof v === "string"\) drafts\.set\(k, v\);/);
});

test("typing captures the draft (and persists it) every keystroke — so a reload can restore it", () => {
  // the same set/delete-then-persist, now also stamping when THIS draft began (ask-draft-predates.test.ts)
  assert.match(RENDER, /ta\.addEventListener\("input", \(\) => \{[\s\S]*if \(ta\.value\) \{ if \(!had\) draftStartedAt\.set\(activeId, Date\.now\(\)\); drafts\.set\(activeId, ta\.value\); \}\s*\n\s*else \{ draftStartedAt\.delete\(activeId\); drafts\.delete\(activeId\); \}\s*\n\s*persistDrafts\(\);[\s\S]*\}\);/);
});

test("the post-reload restore is one-shot and never clobbers live typing", () => {
  assert.match(RENDER, /function restoreActiveDraftOnce\(\): void \{/);
  assert.match(RENDER, /if \(draftsRestored\) return;/);                 // one-shot
  assert.match(RENDER, /if \(!ta \|\| !activeId\) return;/);              // wait until the active tab exists post-load
  assert.match(RENDER, /if \(!ta\.value\) \{ const d = drafts\.get\(activeId\); if \(d\) \{ ta\.value = d;/);  // only when the box is empty
  // invoked from showActive once the active view is shown (so activeId is established post-reload)
  assert.match(RENDER, /if \(empty\) empty\.style\.display = "none";\s*\n\s*restoreActiveDraftOnce\(\);/);
});

test("every draft mutation keeps the persisted copy in sync (switch / send / close)", () => {
  // tab switch stashes the leaving tab's draft (and renders the entering tab's citation chip) → persist
  assert.match(RENDER, /ta\.value = drafts\.get\(id\) \?\? "";\s*\n\s*growComposer\(ta\);\s*\n\s*renderComposerChips\(id\);[\s\S]*?persistDrafts\(\);/);
  // sending clears the draft (and ends its start-stamp) → persist
  assert.match(RENDER, /drafts\.delete\(activeId\); draftStartedAt\.delete\(activeId\); persistDrafts\(\);\s*\/\/ sent/);
  // closing a tab drops its draft AND its citation AND its edit pill → persist (the user 2026-08-04)
  assert.match(RENDER, /drafts\.delete\(id\); composerCitations\.delete\(id\); composerEdits\.delete\(id\); composerFiles\.delete\(id\); persistDrafts\(\);/);
});
