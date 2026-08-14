import { test } from "node:test";
import * as assert from "node:assert/strict";
import { citeText, normalizeDir, sessionMatchesFolders, sessionsForWorkspace } from "./workspace-sessions";
import { parsePorcelain } from "./session-diff";

test("normalizeDir strips trailing slashes", () => {
  assert.equal(normalizeDir("/a/b/"), "/a/b");
  assert.equal(normalizeDir("/"), "/");
  assert.equal(normalizeDir(""), "/");
});

test("session matches the window that has its dir (or a parent/child of it) open", () => {
  assert.ok(sessionMatchesFolders("/repos/romp", ["/repos/romp"]));               // exact
  assert.ok(sessionMatchesFolders("/repos/romp-vscode", ["/repos/romp-vscode"])); // a worktree opened directly
  assert.ok(sessionMatchesFolders("/repos/romp/ui", ["/repos/romp"]));            // session below the root
  assert.ok(sessionMatchesFolders("/repos/romp", ["/repos/romp/vscode-extension"]));     // window on a subfolder
  assert.ok(!sessionMatchesFolders("/repos/romp", ["/repos/romp-vscode"]));       // sibling is NOT a prefix match
  assert.ok(!sessionMatchesFolders("/repos/other", ["/repos/romp"]));
  assert.ok(!sessionMatchesFolders("", ["/repos/romp"]));
});

test("sessionsForWorkspace filters by dir", () => {
  const sessions = [
    { id: "1", name: "here", dir: "/repos/romp" },
    { id: "2", name: "away", dir: "/repos/other" },
  ];
  assert.deepEqual(sessionsForWorkspace(sessions, ["/repos/romp"]).map((s) => s.name), ["here"]);
});

test("citeText: bare file, single line, and range", () => {
  assert.equal(citeText("/a/f.ts"), "/a/f.ts");
  assert.equal(citeText("/a/f.ts", 5, 5, false), "/a/f.ts");        // cursor only, no selection
  assert.equal(citeText("/a/f.ts", 5, 5, true), "/a/f.ts:5");
  assert.equal(citeText("/a/f.ts", 5, 9, true), "/a/f.ts:5-9");
});

test("parsePorcelain: modified, added, untracked, and renamed in -z form", () => {
  const out = [
    " M ui/webview/render.ts",
    "A  vscode-extension/src/new.ts",
    "?? notes.txt",
    "R  new name.ts", "old name.ts",
    "",
  ].join("\0");
  const files = parsePorcelain(out);
  assert.deepEqual(files.map((f) => f.path),
    ["ui/webview/render.ts", "vscode-extension/src/new.ts", "notes.txt", "new name.ts"]);
  assert.equal(files[0].status, "M");
  assert.equal(files[2].untracked, true);
  assert.equal(files[3].renamedFrom, "old name.ts");
});

test("parsePorcelain preserves special names and both staged and unstaged renames", () => {
  const odd = "notes/雪 \\ line\nwith -> arrow.txt";
  const out = [
    ` M ${odd}`,
    "R  staged-new.ts", "staged-old.ts",
    " R unstaged-new.ts", "unstaged-old.ts",
    "",
  ].join("\0");
  assert.deepEqual(parsePorcelain(out), [
    { path: odd, status: "M", untracked: false, renamedFrom: undefined },
    { path: "staged-new.ts", status: "R", untracked: false, renamedFrom: "staged-old.ts" },
    { path: "unstaged-new.ts", status: "R", untracked: false, renamedFrom: "unstaged-old.ts" },
  ]);
});

test("parsePorcelain tolerates junk", () => {
  assert.deepEqual(parsePorcelain(""), []);
  assert.deepEqual(parsePorcelain("\0xx\0"), []);
});
