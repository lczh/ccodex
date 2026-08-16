#!/usr/bin/env bats

# The announcer (live tmux phrase) is DEPRECATED and DEFAULT OFF (the user 2026-07-24): the SDK
# backend is the normal way to run and the kernel's index judges write the durable captions, so no
# tmux path may spend tokens unless explicitly re-enabled. The switch flipped from opt-OUT
# (~/.claude/romp-summarize-off) to opt-IN (~/.claude/romp-summarize-on): absence of the -on file IS
# off. The gate sits before any tmux call or model spawn, so default-off costs nothing at all.

setup() {
    TEST_DIR="$(mktemp -d)"
    HOOK="$(cd "$(dirname "$BATS_TEST_FILENAME")/../hooks" && pwd)/romp-summarize.sh"
    export HOME="$TEST_DIR/home"; mkdir -p "$HOME/.claude"
    export XDG_STATE_HOME="$HOME/.local/state"   # keep the state root inside the sandbox
    unset ROMP_SUMMARIZING ROMP_STATE_DIR || true
    export TMUX="fake-socket,1,0"       # LOOK like a tmux session so only the opt-in gate can bail first
    # a tripwire tmux on PATH: if the hook gets past the gate it will call this and we fail the test
    mkdir -p "$TEST_DIR/bin"
    printf '#!/bin/sh\necho TRIPPED > "%s/tripped"\nexit 1\n' "$TEST_DIR" > "$TEST_DIR/bin/tmux"
    chmod +x "$TEST_DIR/bin/tmux"
    export PATH="$TEST_DIR/bin:$PATH"
}

teardown() { rm -rf "$TEST_DIR"; }

@test "default (no switch file) exits before touching tmux or any model" {
    run bash -c 'printf "{\"hook_event_name\":\"Stop\"}" | "$0"' "$HOOK"
    [ "$status" -eq 0 ]
    [ ! -f "$TEST_DIR/tripped" ]
}

@test "the retired opt-OUT file does not turn it on" {
    touch "$HOME/.claude/romp-summarize-off"
    run bash -c 'printf "{\"hook_event_name\":\"Stop\"}" | "$0"' "$HOOK"
    [ "$status" -eq 0 ]
    [ ! -f "$TEST_DIR/tripped" ]
}

@test "the opt-in file arms it (reaches the tmux lookup past the gate)" {
    touch "$HOME/.claude/romp-summarize-on"
    run bash -c 'printf "{\"hook_event_name\":\"Stop\"}" | "$0"' "$HOOK"
    [ "$status" -eq 0 ]                  # the tripwire tmux fails silently (errors swallowed by design)
    [ -f "$TEST_DIR/tripped" ]           # but its invocation PROVES the gate opened
}

# --- the announcer's isolation ---------------------------------------------
# Once the gate opens, the summarizer spawns `claude -p` on every prompt and
# every stop. Two things make that spawn safe, and both are invisible from the
# outside, so they are asserted on the argv and cwd the spawn actually gets:
#   --safe-mode  — --tools ""/--mcp-config gate tools and MCP but NOT hooks, so
#                  without it a discovered settings.json still runs commands.
#   a private cwd — it ran from /tmp, which is world-writable: any other local
#                  user can plant /tmp/.claude/settings.json there.

# Arm the hook with a tmux that answers instead of tripping, and a `claude`
# stand-in that records the argv + cwd it was handed. The hook detaches its
# expensive work, so the caller returns before the spawn happens.
arm_summarizer() {
    touch "$HOME/.claude/romp-summarize-on"
    export CLAUDE_ARGV="$TEST_DIR/claude-argv"
    cat > "$TEST_DIR/bin/tmux" <<'SH'
#!/bin/sh
case "$1" in
  display-message) echo web ;;                 # session name
  show) case "$*" in *@romp*) echo 1 ;; esac ;;  # a romp-managed session
esac
exit 0
SH
    cat > "$TEST_DIR/bin/claude" <<'SH'
#!/bin/sh
{ echo "CWD=$(pwd)"; for a in "$@"; do echo "ARG=$a"; done; } > "$CLAUDE_ARGV"
echo "summarized the thing"
SH
    chmod +x "$TEST_DIR/bin/tmux" "$TEST_DIR/bin/claude"
}

# The spawn lands a moment after the hook returns; wait for the record, don't race it.
wait_for_argv() {
    for _ in $(seq 1 100); do
        [ -s "$CLAUDE_ARGV" ] && return 0
        sleep 0.1
    done
    return 1
}

run_summarizer() {
    printf '{"hook_event_name":"UserPromptSubmit","prompt":"add a retry to the notes-api client"}' \
        | "$HOOK"
    wait_for_argv
}

@test "the summarizer model call passes --safe-mode (tools/MCP off does not cover hooks)" {
    arm_summarizer
    run_summarizer
    grep -qx 'ARG=--safe-mode' "$CLAUDE_ARGV"
}

@test "the summarizer runs from a private dir it owns, never world-writable /tmp" {
    arm_summarizer
    run_summarizer
    cwd="$(sed -n 's/^CWD=//p' "$CLAUDE_ARGV")"
    [ -n "$cwd" ]
    [ "$cwd" != "/tmp" ]
    [ "$cwd" = "$XDG_STATE_HOME/romp/summarize" ]
    # Created by the hook itself (it did not exist before this run) and 0700, so
    # no other local user can drop a settings.json into the summarizer's cwd.
    [ "$(stat -c '%a' "$cwd" 2>/dev/null || stat -f '%Lp' "$cwd")" = "700" ]
}
