#!/usr/bin/env bats

# scripts/docs-serve.sh — serve the docs so a browser refresh always shows the
# current tree. `mkdocs serve`'s own watcher catches your saves but sleeps
# through changes that arrive via git (a merge replaces files wholesale), which
# is the whole reason this wrapper exists: it watches git state and restarts.
# The real mkdocs is stubbed via ROMP_MKDOCS, so no test starts a web server.

ROMP_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

setup() {
    TEST_DIR="$(mktemp -d)"
    REPO="$TEST_DIR/repo"
    mkdir -p "$REPO/scripts" "$REPO/docs"
    cp "$ROMP_DIR/scripts/docs-serve.sh" "$REPO/scripts/"
    chmod +x "$REPO/scripts/docs-serve.sh"
    git init -q "$REPO"
    git -C "$REPO" config user.email t@e.invalid
    git -C "$REPO" config user.name t
    echo "start" > "$REPO/docs/guide.md"
    printf 'site_name: t\n' > "$REPO/mkdocs.yml"
    git -C "$REPO" add -A
    git -C "$REPO" commit -qm init

    # A stub that logs one line per launch and then blocks, so a restart is
    # visible as a second line rather than inferred from a pid.
    STUB="$TEST_DIR/mkdocs"
    RUNS="$TEST_DIR/runs.log"
    cat > "$STUB" <<EOF
#!/usr/bin/env bash
echo "serve" >> "$RUNS"
while true; do sleep 0.2; done
EOF
    chmod +x "$STUB"
    export ROMP_MKDOCS="$STUB"
    export ROMP_DOCS_POLL=0.2
}

teardown() {
    [ -n "${LOOP_PID:-}" ] && kill "$LOOP_PID" 2>/dev/null
    pkill -f "$TEST_DIR/mkdocs" 2>/dev/null
    rm -rf "$TEST_DIR"
    return 0
}

runs() { [ -f "$RUNS" ] && wc -l < "$RUNS" | tr -d ' ' || echo 0; }

@test "it starts the server once and leaves it alone while the tree is still" {
    "$REPO/scripts/docs-serve.sh" 8099 >/dev/null 2>&1 &
    LOOP_PID=$!
    sleep 1.5
    [ "$(runs)" = "1" ]
}

@test "a commit restarts the server, which the mkdocs watcher would have missed" {
    "$REPO/scripts/docs-serve.sh" 8099 >/dev/null 2>&1 &
    LOOP_PID=$!
    sleep 1
    [ "$(runs)" = "1" ]

    # A merge/checkout looks like this to the tree: HEAD moves, files replaced.
    echo "edited" > "$REPO/docs/guide.md"
    git -C "$REPO" add -A
    git -C "$REPO" commit -qm second
    sleep 1.5

    [ "$(runs)" -ge 2 ]
}

@test "an uncommitted doc edit restarts it too" {
    "$REPO/scripts/docs-serve.sh" 8099 >/dev/null 2>&1 &
    LOOP_PID=$!
    sleep 1
    echo "dirty" > "$REPO/docs/new.md"
    sleep 1.5
    [ "$(runs)" -ge 2 ]
}

@test "a crashed mkdocs is brought back, never a dead port answering nothing" {
    "$REPO/scripts/docs-serve.sh" 8099 >/dev/null 2>&1 &
    LOOP_PID=$!
    sleep 1
    [ "$(runs)" = "1" ]
    pkill -f "$TEST_DIR/mkdocs"
    sleep 1.5
    [ "$(runs)" -ge 2 ]
}

@test "the watcher's status poll never takes index.lock (--no-optional-locks)" {
    # r62 release gate: plain `git status` rewrote the index during its poll and
    # collided with a concurrent `git add` — twice, deterministically, on macOS
    grep -q -- 'git --no-optional-locks status --porcelain' "$REPO/scripts/docs-serve.sh"
}
