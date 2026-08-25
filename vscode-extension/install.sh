#!/usr/bin/env bash
# Build + permanently install this extension into every VS Code-family editor
# present (VS Code, Cursor, Codium, Insiders, and any `code`/`cursor` on PATH).
# After this runs once, the extension is always loaded in normal editor windows
# — no F5 / dev host. Re-run it to pick up source changes.
#
# WHY a script (and why the version bump): editors load the INSTALLED copy under
# ~/.vscode/extensions/<publisher>.<name>-<ver>/, NOT this repo's dist/. They
# also cache extension code by version, so reinstalling the SAME version — even
# with --force — usually won't refresh on a window reload. So every install must
# be a strictly NEWER version. Rebuilding dist/ here alone changes nothing in the
# running editor; you must repackage + reinstall, which is what this does.
#
# The bump is NOT committed. package.json holds a stable base version; the
# strictly-newer number exists only in the packaged .vsix, and package.json is
# restored (even on failure) before this script returns. Committing the bump
# meant a version-churn commit per install and a package.json version that
# looked like romp's release version without being it. This number is a BUILD
# ID for editor cache-busting, not a romp version: `romp --version` is the one
# that answers "what romp am I running".
#
# Mirrors ../vscode-trackchanges/install.sh, minus the shared-engine wiring
# (romp-chat-view is standalone; esbuild bundles its deps in).
set -euo pipefail
# ROMP_INSTALL_TARGET (the v1.3.16 audit's P1.1, completed by the v1.3.17 audit's P1.1): under
# an updater this script's BYTES come from an immutable snapshot — and the BUILD runs there too.
# The first cut cd'd into the target and ran the LIVE npm ci + esbuild.js, so a racing writer
# could still swap the build's inputs after verification. Now every input is the snapshot's:
# npm ci reproduces the snapshot's lockfile (integrity-hashed), esbuild runs the snapshot's
# config over the snapshot's sources, and only GENERATED ARTIFACTS (one dist generation, the
# .vsix) are published into the target — atomically, via a symlink swap.
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
PUBLISH_DIR=""
if [[ -n "${ROMP_INSTALL_TARGET:-}" && -d "$ROMP_INSTALL_TARGET/vscode-extension" \
      && "$SELF_DIR" != "$(cd "$ROMP_INSTALL_TARGET/vscode-extension" && pwd)" ]]; then
  PUBLISH_DIR="$(cd "$ROMP_INSTALL_TARGET/vscode-extension" && pwd)"
fi
cd "$SELF_DIR"

# Atomically publish the built dist/ into "$1" as a fresh GENERATION: copy to dist.gen.<pid>,
# then swap the `dist` symlink with a rename(2) (python3 os.rename replaces the link in one
# step; mv -T is GNU-only). A pre-generations REAL dist dir is moved aside first — that one
# swap has a microsecond gap, once, on upgrade; every later publish is atomic. Old generations
# are pruned after the swap (installs serialize under the updater's flock).
publish_dist() {
  local pub="$1" gen="dist.gen.$$"
  rm -rf "$pub/$gen"
  cp -R dist "$pub/$gen"
  ln -s "$gen" "$pub/.dist.lnk.$$"
  if [ -d "$pub/dist" ] && [ ! -L "$pub/dist" ]; then
    rm -rf "$pub/.dist.old.$$"
    mv "$pub/dist" "$pub/.dist.old.$$"
  fi
  python3 -c 'import os, sys; os.rename(sys.argv[1], sys.argv[2])' "$pub/.dist.lnk.$$" "$pub/dist"
  rm -rf "$pub/.dist.old.$$" 2>/dev/null || true
  local g
  for g in "$pub"/dist.gen.*; do
    [ -e "$g" ] || continue
    [ "$g" = "$pub/$gen" ] || rm -rf "$g"
  done
}

if ! command -v node >/dev/null 2>&1; then
  echo "!! node not found — skipping romp-chat-view install."
  exit 0
fi

# ROMP_EXT_PACKAGE_ONLY=1 → build + package the .vsix, install it into nothing.
# Lets CI (and tests/install-sh.bats) exercise the stamp/restore and the packaging
# without touching the editors on the machine running it.
PACKAGE_ONLY="${ROMP_EXT_PACKAGE_ONLY:-}"

# ── deps + bundles: ALWAYS, before any editor check ───────────────────
# dist/ is NOT just the extension's — the kernel serves these same bundles to the
# BROWSER dashboard (kernel.py's DIST/_ensure_bundles point at vscode-extension/dist).
# So the build must never be gated on an editor being present. It used to be: the
# editor-CLI check below sat here, above dependency installation, and exited 0 on a machine with
# no VS Code family installed — leaving node_modules AND dist absent, so every dashboard
# pane fetched /dist/*.js and got a 404 and the UI came up blank (a browser-only Linux
# box, the user 2026-07-27). The old skip message even claimed "built dist/ is ready",
# which was never true on that path. Editor presence gates only the PACKAGE + INSTALL
# steps at the bottom, which is all it ever meant.
echo "==> npm ci"
# Reproduce the reviewed lock exactly. `npm install` can rewrite/resynthesize the tree and defeats
# pinning the local vsce binary that `npx --no-install` must use below.
npm ci --silent

# --production => minified, no sourcemaps. An INSTALL is not a dev loop: without it the dashboard
# shipped a DEVELOPMENT bundle, and render.js — the chat pane's code — was 591 KB of unminified JS
# the browser had to parse before anything appeared (a slow chat load on a fresh install, the user
# 2026-07-27). Serving was never the issue: every asset returns in under 3 ms over loopback.
# Iterating on the UI? Run `node esbuild.js` (or --watch) directly for readable output and
# sourcemaps; only the installers force production. ROMP_EXT_DEV_BUILD=1 opts out here too.
build_flags="--production"
[ -n "${ROMP_EXT_DEV_BUILD:-}" ] && build_flags=""
echo "==> build${build_flags:+ (minified)}"
node esbuild.js $build_flags

if [[ -n "$PUBLISH_DIR" ]]; then
  echo "==> publish dist generation -> $PUBLISH_DIR"
  publish_dist "$PUBLISH_DIR"
fi

# ── collect editor CLIs (dedup by resolved path) ──────────────────────
declare -a CLIS=()
add_cli() {
  local p="$1"
  [ -x "$p" ] || return 0
  local real; real="$(realpath "$p" 2>/dev/null || echo "$p")"
  for e in "${CLIS[@]:-}"; do [ "$e" = "$real" ] && return 0; done
  CLIS+=("$real")
}
# macOS app bundles
# ROMP_EDITOR_APPS: test seam so a dev mac's real /Applications doesn't leak
# editors into a test that is about having none.
APPS_DIR="${ROMP_EDITOR_APPS:-/Applications}"
add_cli "$APPS_DIR/Visual Studio Code.app/Contents/Resources/app/bin/code"
add_cli "$APPS_DIR/Cursor.app/Contents/Resources/app/bin/code"
add_cli "$APPS_DIR/VSCodium.app/Contents/Resources/app/bin/codium"
# anything on PATH (covers Linux / remote / server installs)
for c in code code-insiders cursor codium; do
  p="$(command -v "$c" 2>/dev/null || true)"; [ -n "$p" ] && add_cli "$p"
done

if [ "${#CLIS[@]}" -eq 0 ] && [ -z "$PACKAGE_ONLY" ]; then
  echo "==> dist/ built — the browser dashboard is ready."
  echo "    No VS Code-family editor CLI found, so nothing to install into; skipping the .vsix."
  exit 0
fi

# Set the packaged version WITHOUT committing it (see the header note for why).
# Patch = epoch seconds: monotonic by construction, so every install is strictly
# newer than the last no matter how often you reinstall at the same commit (a
# commit count would NOT be: edit -> install -> reload repeats one commit).
# major.minor stays on the committed line so a reinstall can never look like a
# DOWNGRADE to an editor holding an older build.
# `|| true` + `return 0`: this is an EXIT trap, so a non-zero last command in it can
# leak into the script's exit status and turn a clean run into a failure.
cleanup_pkg() { [ -f package.json.orig ] && mv -f package.json.orig package.json || true; return 0; }
trap cleanup_pkg EXIT INT TERM
cp package.json package.json.orig

echo "==> stamp build version"
node -e 'const fs=require("fs"),f="package.json",p=JSON.parse(fs.readFileSync(f));const v=p.version.split(".");v[2]=String(Math.floor(Date.now()/1000));p.version=v.join(".");fs.writeFileSync(f,JSON.stringify(p,null,2)+"\n");console.log("    build version -> "+p.version+" (not committed)");'

# Fixed output name (overwritten each run) so .vsix artifacts don't pile up.
echo "==> package .vsix"
npx --no-install vsce package --no-dependencies --allow-missing-repository -o romp-chat-view.vsix >/dev/null
echo "    packaged romp-chat-view.vsix"
if [[ -n "$PUBLISH_DIR" ]]; then
  # the packaged artifact lands in the target too (atomic: staged copy + rename) — the fixed
  # name is the contract other tooling reads
  cp romp-chat-view.vsix "$PUBLISH_DIR/.romp-chat-view.vsix.$$"
  mv "$PUBLISH_DIR/.romp-chat-view.vsix.$$" "$PUBLISH_DIR/romp-chat-view.vsix"
fi

if [ -n "$PACKAGE_ONLY" ]; then
  echo "==> ROMP_EXT_PACKAGE_ONLY set — packaged only, installed into no editor."
  exit 0
fi

# ":-" guard: bash 3.2 (the macOS system bash) treats "${arr[@]}" on an EMPTY array
# as an unbound variable under `set -u`.
installed=0
failed=0
for cli in "${CLIS[@]:-}"; do
  echo "==> installing into: $cli"
  if "$cli" --install-extension romp-chat-view.vsix --force </dev/null; then
    installed=$((installed + 1))
    echo "    installed into: $cli"
  else
    failed=$((failed + 1))
    echo "    failed for $cli"
  fi
done

# Stable, machine-readable outcome for the extension host and automated installers. A package
# build is not an update unless at least one editor accepted it.
echo "ROMP_EXT_INSTALL_RESULT installed=$installed failed=$failed"
if [ "$installed" -eq 0 ]; then
  echo "!! romp-chat-view was packaged, but no editor installed it." >&2
  exit 1
fi

echo "==> done. Reload the editor (Cmd+Shift+P -> 'Developer: Reload Window')"
echo "    or quit + reopen; the extension is then permanently active."
