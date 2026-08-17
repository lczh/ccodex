#!/usr/bin/env bats

# `romp engine [claude|codex|status]` — one switch for the machine's vendor posture
# (docs/codex.md): writes STATE/judge-engine (judges read it live per call) and
# STATE/default-backend (`romp new` + POST /new default) together. Running sessions
# are never touched; the notes about missing binaries/logins are warnings, not gates.
# `status` must survive ABSENT files (set -euo pipefail killed it mid-substitution once).

BIN="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)"
ROMP_SCRIPT="$BIN/romp"

setup() {
    TEST_DIR="$(mktemp -d)"
    export HOME="$TEST_DIR/home"
    export XDG_STATE_HOME="$HOME/.local/state"
    export PATH="$TEST_DIR/bin:/usr/bin:/bin"   # a controlled PATH so binary probes are deterministic
    mkdir -p "$XDG_STATE_HOME/romp" "$TEST_DIR/bin"
}

teardown() {
    rm -rf "$TEST_DIR"
}

@test "romp engine: status defaults on a fresh state, codex writes both knobs, claude flips back" {
    run "$ROMP_SCRIPT" engine status
    [ "$status" -eq 0 ]
    [[ "$output" == *"judges:               claude"* ]]
    [[ "$output" == *"new-session default:  sdk"* ]]

    run "$ROMP_SCRIPT" engine codex
    [ "$status" -eq 0 ]
    [ "$(cat "$XDG_STATE_HOME/romp/judge-engine")" = "codex" ]
    [ "$(cat "$XDG_STATE_HOME/romp/default-backend")" = "codex" ]
    [[ "$output" == *"engine CODEX"* ]]

    run "$ROMP_SCRIPT" engine status
    [[ "$output" == *"judges:               codex"* ]]
    [[ "$output" == *"new-session default:  codex"* ]]

    run "$ROMP_SCRIPT" engine claude
    [ "$status" -eq 0 ]
    [ "$(cat "$XDG_STATE_HOME/romp/judge-engine")" = "claude" ]
    [ "$(cat "$XDG_STATE_HOME/romp/default-backend")" = "sdk" ]
}

@test "romp engine codex: warns (never refuses) on a missing binary, and on a logged-out codex" {
    run "$ROMP_SCRIPT" engine codex
    [ "$status" -eq 0 ]
    [[ "$output" == *"no codex binary found"* ]]

    # a fake codex that reports Not logged in (real codex prints status on stderr; the check
    # merges streams and anchors on ^logged so "Not logged in" can't false-match)
    cat > "$TEST_DIR/bin/codex" <<'EOF'
#!/usr/bin/env bash
echo "Not logged in" >&2
EOF
    chmod +x "$TEST_DIR/bin/codex"
    run "$ROMP_SCRIPT" engine codex
    [ "$status" -eq 0 ]
    [[ "$output" == *"codex isn't logged in"* ]]

    # logged in → no note at all
    cat > "$TEST_DIR/bin/codex" <<'EOF'
#!/usr/bin/env bash
echo "Logged in using ChatGPT" >&2
EOF
    chmod +x "$TEST_DIR/bin/codex"
    run "$ROMP_SCRIPT" engine codex
    [ "$status" -eq 0 ]
    [[ "$output" != *"note:"* ]]
}

@test "romp engine: an unknown arg is a usage error" {
    run "$ROMP_SCRIPT" engine gemini
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp engine"* ]]
}

@test "romp new: --model/--effort refuse UP FRONT when the engine default makes the spawn codex" {
    # validating against the --codex flag alone let these sail through on an engine-codex
    # machine: the codex session got created and the preference silently dropped, warned only
    # afterward (the user's audit, 2026-08-17). The refusal must come at parse time — exit 2,
    # BEFORE any kernel/token probing (exit 1 would mean it got past validation).
    run "$ROMP_SCRIPT" engine codex
    [ "$status" -eq 0 ]
    run "$ROMP_SCRIPT" new --model some-model mysess
    [ "$status" -eq 2 ]
    [[ "$output" == *"romp engine codex"* ]]
    run "$ROMP_SCRIPT" new --effort high mysess
    [ "$status" -eq 2 ]

    # the explicit flag still refuses, naming the flag
    run "$ROMP_SCRIPT" engine claude
    run "$ROMP_SCRIPT" new --codex --model some-model mysess
    [ "$status" -eq 2 ]
    [[ "$output" == *"--codex"* ]]

    # and an SDK spawn keeps taking them past validation (fails later only on the dead kernel)
    run "$ROMP_SCRIPT" new --model some-model mysess
    [ "$status" -eq 1 ]
    [[ "$output" == *"kernel"* ]]
}
