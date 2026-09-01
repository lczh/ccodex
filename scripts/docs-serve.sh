#!/usr/bin/env bash
# Serve the docs site so a browser refresh ALWAYS shows the current tree.
#
# `mkdocs serve` watches the filesystem, and that watcher reliably catches your
# own saves but misses changes that arrive through git: a merge, a checkout, a
# rebase replaces files wholesale and the rebuild never fires, so the page you
# refresh is whatever was true when the server started. That has fooled us into
# re-reporting fixed text as broken more than once.
#
# So: run mkdocs serve, and watch the one thing its watcher misses. Whenever
# HEAD or the working tree moves, restart it. Event-based on git state, not a
# rebuild timer (CLAUDE.md's design rule).
#
#   scripts/docs-serve.sh [port]        # default 8000
set -euo pipefail

PORT="${1:-8000}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# HEAD plus the hash of every tracked+untracked doc input: catches merges,
# checkouts, and stashes, which are exactly what the mkdocs watcher sleeps through.
tree_id() {
  {
    git rev-parse HEAD 2>/dev/null || true
    # a READ-ONLY poll must never take index.lock: plain `git status` opportunistically
    # rewrites the index, and on a slow runner that write collided with the test's own
    # `git add` — "Unable to create .git/index.lock: File exists", twice in a row on
    # the v1.3.35 macOS gate (r62). --no-optional-locks (git >= 2.15) makes it a pure read.
    git --no-optional-locks status --porcelain -- docs mkdocs.yml overrides 2>/dev/null || true
  } | shasum | cut -d' ' -f1
}

MKDOCS="${ROMP_MKDOCS:-mkdocs}"     # stubbable, so the test never runs a real server
POLL="${ROMP_DOCS_POLL:-2}"

SERVER=""
start() {
  "$MKDOCS" serve -a "127.0.0.1:$PORT" &
  SERVER=$!
}
stop() { [ -n "$SERVER" ] && kill "$SERVER" 2>/dev/null || true; }
trap 'stop; exit 0' INT TERM

start
LAST="$(tree_id)"
echo "docs on http://127.0.0.1:$PORT/romp/ — restarting on any git change"

while true; do
  sleep "$POLL"
  NOW="$(tree_id)"
  if [ "$NOW" != "$LAST" ]; then
    echo "[docs-serve] tree moved; restarting mkdocs"
    LAST="$NOW"
    stop
    wait "$SERVER" 2>/dev/null || true
    start
  fi
  # A crashed mkdocs (a bad config, a port grab) must not leave a dead port
  # answering nothing: bring it back rather than looping over a corpse.
  if ! kill -0 "$SERVER" 2>/dev/null; then
    echo "[docs-serve] mkdocs exited; restarting"
    start
  fi
done
