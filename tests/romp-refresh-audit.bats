#!/usr/bin/env bats
# `romp refresh --quiet` writes an audit row the kernel's drift check can read (T240d): when=quiet and
# the checkout sha. Without them the check saw the checkout ahead of the kernel, read a row that
# named no parked deploy, and posted an IMMEDIATE restart that pre-empted the quiet window this very
# flag asked for. The immediate refresh keeps its row as it was. Fake manager + postal stand-ins.
#
# Also the KEY-SOURCE PREFLIGHT (the user 2026-09-07): before the audit row and the restart, `romp
# refresh` runs this tree's `romp-keysource --check`; exit 1 (API-key billing selected, no key source)
# refuses the restart unless --force, exit 0 proceeds, anything else warns and proceeds. The audit
# tests run against the REAL preflight over a hermetic (absent) env file and an empty state dir — the
# "nothing selects API-key billing" pass — so they never read this machine's own service.env, and with
# ROMP_KERNEL_PORT pinned to a closed port they never ask this machine's kernel (the check asks a running
# kernel before the file); the refusal cases drive it through the ROMP_KEYSOURCE_BIN seam (the pattern
# the manager/postal fakes use).

setup() {
    TEST_DIR="$(mktemp -d)"
    export ROMP_STATE_DIR="$TEST_DIR/state"
    export ROMP_SERVICE_ENV_FILE="$TEST_DIR/service.env"     # hermetic: absent, no billing selected
    export CLAUDE_CONFIG_DIR="$TEST_DIR/claude"               # no apiKeyHelper to find either
    export ROMP_KERNEL_PORT=1                                 # the preflight asks the kernel first: a closed
    #                                                           port, so it never dials this machine's own
    unset ROMP_KEYSOURCE_BIN
    mkdir -p "$TEST_DIR/bin"
    printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$*" >> "%s/manager-calls"\n' "$TEST_DIR" > "$TEST_DIR/bin/romp-manager"
    printf '#!/usr/bin/env bash\nexit 0\n' > "$TEST_DIR/bin/romp-postal-service"
    chmod +x "$TEST_DIR/bin/romp-manager" "$TEST_DIR/bin/romp-postal-service"
    export ROMP_MANAGER_BIN="$TEST_DIR/bin/romp-manager"
    export ROMP_POSTAL_BIN="$TEST_DIR/bin/romp-postal-service"
    ROMP="$BATS_TEST_DIRNAME/../bin/romp"
}

teardown() { rm -rf "$TEST_DIR"; }

# A fake preflight: $1 = exit code, $2 = the line it prints (the remedy stands in for the real one).
_fake_keysource() {
    printf '#!/usr/bin/env bash\necho "$*" >> "%s/keysource-calls"\necho "%s"\nexit %s\n' \
        "$TEST_DIR" "${2:-key source  none}" "$1" > "$TEST_DIR/bin/romp-keysource"
    chmod +x "$TEST_DIR/bin/romp-keysource"
    export ROMP_KEYSOURCE_BIN="$TEST_DIR/bin/romp-keysource"
}

@test "romp refresh --quiet: the audit row says when=quiet and names the checkout sha" {
    run "$ROMP" refresh --quiet
    [ "$status" -eq 0 ]
    grep -qx 'restart-all --quiet' "$TEST_DIR/manager-calls"
    python3 - "$ROMP_STATE_DIR/restart-audit.jsonl" <<'EOF'
import json, re, sys
row = [json.loads(l) for l in open(sys.argv[1]) if l.strip()][-1]
assert row.get("when") == "quiet", row
assert re.fullmatch(r"[0-9a-f]{8}", row.get("sha") or ""), row
assert "action" not in row, row          # still the caller-attribution row, not a kernel action row
EOF
}

@test "romp refresh (immediate): the audit row carries neither when nor sha" {
    run "$ROMP" refresh
    [ "$status" -eq 0 ]
    grep -q '^restart-all' "$TEST_DIR/manager-calls"
    python3 - "$ROMP_STATE_DIR/restart-audit.jsonl" <<'EOF'
import json, sys
row = [json.loads(l) for l in open(sys.argv[1]) if l.strip()][-1]
assert "when" not in row and "sha" not in row, row
EOF
}

@test "romp refresh: the real preflight passes on a box where nothing selects API-key billing" {
    command -v python3 >/dev/null 2>&1 || skip "python3 not available"
    run "$ROMP" refresh
    [ "$status" -eq 0 ]
    grep -q '^restart-all' "$TEST_DIR/manager-calls"
}

@test "romp refresh refuses to restart when the key-source preflight fails, and says why" {
    _fake_keysource 1 "No API key source is configured for API-key billing (TEST remedy)"
    run "$ROMP" refresh
    [ "$status" -eq 1 ]
    [[ "$output" == *"TEST remedy"* ]]                       # the preflight's own text, verbatim
    [[ "$output" == *"refusing to restart"* ]]
    [[ "$output" == *"--force"* ]]
    grep -qx -- '--check' "$TEST_DIR/keysource-calls"        # asked with --check and nothing else
    [ ! -e "$TEST_DIR/manager-calls" ]                       # the manager was never told to restart
    [ ! -e "$ROMP_STATE_DIR/restart-audit.jsonl" ]           # and no audit row claims a restart happened
}

@test "romp refresh --force restarts anyway and keeps --force out of the manager's arguments" {
    _fake_keysource 1 "No API key source is configured for API-key billing (TEST remedy)"
    run "$ROMP" refresh --force
    [ "$status" -eq 0 ]
    [[ "$output" == *"TEST remedy"* ]]                       # still said, so the operator knows
    [[ "$output" == *"restarting anyway"* ]]
    grep -q '^restart-all' "$TEST_DIR/manager-calls"
    ! grep -q -- '--force' "$TEST_DIR/manager-calls"
}

@test "romp refresh --quiet --force: --quiet still reaches the manager and the audit row" {
    _fake_keysource 1
    run "$ROMP" refresh --quiet --force
    [ "$status" -eq 0 ]
    grep -qx 'restart-all --quiet' "$TEST_DIR/manager-calls"
    python3 - "$ROMP_STATE_DIR/restart-audit.jsonl" <<'EOF'
import json, sys
row = [json.loads(l) for l in open(sys.argv[1]) if l.strip()][-1]
assert row.get("when") == "quiet", row
EOF
    run "$ROMP" refresh --force --quiet                      # either order
    [ "$status" -eq 0 ]
    [ "$(grep -c 'restart-all --quiet' "$TEST_DIR/manager-calls")" -eq 2 ]
}

@test "romp refresh proceeds when the preflight passes" {
    _fake_keysource 0 "key source  1Password reference abcdef012345 in /tmp/TEST/service.env"
    run "$ROMP" refresh
    [ "$status" -eq 0 ]
    [[ "$output" != *"key source"* ]]                        # a pass is quiet
    grep -q '^restart-all' "$TEST_DIR/manager-calls"
}

@test "romp refresh proceeds with a warning when the preflight itself cannot run" {
    _fake_keysource 3 "romp keysource: unexpected error: TEST"
    run "$ROMP" refresh
    [ "$status" -eq 0 ]
    [[ "$output" == *"preflight could not run (exit 3); continuing"* ]]
    grep -q '^restart-all' "$TEST_DIR/manager-calls"
    export ROMP_KEYSOURCE_BIN="$TEST_DIR/bin/no-such-keysource"   # missing binary: same, not a block
    run "$ROMP" refresh
    [ "$status" -eq 0 ]
    [[ "$output" == *"preflight could not run"* ]]
}

@test "romp refresh: the flagless path re-sets its positionals with the empty-array-safe idiom (bash 3.2 under set -u)" {
    # An EMPTY array expanded as "${arr[@]}" is an unbound-variable abort under `set -u` on bash < 4.4
    # (macOS /bin/bash 3.2), so a flagless `romp refresh` died at the preflight there. This bash is
    # newer and cannot reproduce it, so the file's own `[@]+` idiom is pinned; the flagless run above
    # ("the real preflight passes") covers the behaviour on the bash at hand.
    grep -qF 'set -- refresh ${_rf_rest[@]+"${_rf_rest[@]}"}' "$ROMP"
    ! grep -qF 'set -- refresh "${_rf_rest[@]}"' "$ROMP"
    run bash -u "$ROMP" refresh                              # the no-flag path, -u explicit
    [ "$status" -eq 0 ]
    grep -q '^restart-all' "$TEST_DIR/manager-calls"
}
