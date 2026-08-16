#!/usr/bin/env bats

# `romp-manager ensure` is the no-`romp on` auto-start: the SessionStart hook
# (romp-manager-ensure.sh) calls it so romp usage brings up the supervisor.
# It must be idempotent (no second manager) and non-blocking (spawns detached).

setup() {
    TEST_DIR="$(mktemp -d)"
    MGR="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-manager"
    # Fake kernel launcher: stay alive without binding a real port (we assert on the
    # manager's control endpoint, not a live kernel).
    FAKE="$TEST_DIR/fake-serve"
    printf '#!/usr/bin/env bash\nexec sleep 30\n' > "$FAKE"
    chmod +x "$FAKE"
    CPORT=7561 MPORT=7562
    TOKEN=manager_test_token_0123456789abcdef
    export ROMP_MANAGER_TOKEN="$TOKEN"
    export XDG_STATE_HOME="$TEST_DIR/state"
}

mcurl() {
    curl -H "X-Romp-Manager-Token: $TOKEN" "$@"
}

teardown() {
    # Graceful stop, then reap the detached manager (it is orphaned, not our child).
    mcurl -fsS -X POST "http://127.0.0.1:${CPORT:-0}/stop" >/dev/null 2>&1 || true
    [[ -n "${MGR_PID:-}" ]] && kill "$MGR_PID" 2>/dev/null || true
    rm -rf "$TEST_DIR"
}

@test "ensure: idempotent, non-blocking auto-start of the supervisor" {
    command -v node >/dev/null 2>&1 || skip "node not available"
    command -v curl >/dev/null 2>&1 || skip "curl not available"

    # Nothing running yet → status fails.
    run env ROMP_MANAGER_PORT=$CPORT node "$MGR" status
    [ "$status" -eq 1 ]

    # ensure returns 0 immediately (non-blocking) and spawns a DETACHED manager.
    run env ROMP_MANAGER_PORT=$CPORT ROMP_SERVE_PORT=$MPORT ROMP_SERVE_BIN="$FAKE" node "$MGR" ensure
    [ "$status" -eq 0 ]

    # The detached manager comes up on the control port.
    local i
    for i in $(seq 1 40); do
        mcurl -fsS "http://127.0.0.1:$CPORT/status" >/dev/null 2>&1 && break
        sleep 0.1
    done
    run mcurl -fsS "http://127.0.0.1:$CPORT/status"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"id":"main"'* ]]
    MGR_PID="$(printf '%s' "$output" | grep -oE '"pid":[ ]*[0-9]+' | head -1 | grep -oE '[0-9]+')"

    # A second ensure is a harmless no-op; the manager stays up (no double-start).
    run env ROMP_MANAGER_PORT=$CPORT ROMP_SERVE_PORT=$MPORT ROMP_SERVE_BIN="$FAKE" node "$MGR" ensure
    [ "$status" -eq 0 ]
    run mcurl -fsS "http://127.0.0.1:$CPORT/status"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"id":"main"'* ]]
}

@test "manager bootstraps a tmux server (launchd-rooted) with exit-empty off" {
    command -v node >/dev/null 2>&1 || skip "node not available"

    # Fake tmux on PATH that records its args — so we assert WHAT the manager asks of tmux at startup,
    # without touching the real tmux server. (The fix: a launchd-rooted server so new sessions don't
    # inherit a terminal's TCC identity → the "VS Code wants to access" prompt.)
    BIN="$TEST_DIR/bin"; mkdir -p "$BIN"
    CALLS="$TEST_DIR/tmux-calls"
    printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$*" >> "%s"\nexit 0\n' "$CALLS" > "$BIN/tmux"
    chmod +x "$BIN/tmux"

    # Run `up` directly on a UNIQUE port (so it doesn't no-op against the other test's manager); the
    # manager calls startTmuxServer() at startup, before it ever binds the control port.
    env PATH="$BIN:$PATH" ROMP_MANAGER_PORT=7573 ROMP_SERVE_PORT=7574 ROMP_SERVE_BIN="$FAKE" node "$MGR" up >/dev/null 2>&1 &
    MGR_PID=$!
    local i
    for i in $(seq 1 50); do [ -f "$CALLS" ] && break; sleep 0.1; done
    kill "$MGR_PID" 2>/dev/null || true

    # startManager() → startTmuxServer() ran our fake tmux with start-server + exit-empty off.
    [ -f "$CALLS" ]
    grep -q "start-server" "$CALLS"
    grep -q "exit-empty off" "$CALLS"
}

@test "a leaked \$TMUX never reaches the manager or its kernels" {
    command -v node >/dev/null 2>&1 || skip "node not available"

    # The 2026-07-20 anchor-clobber chain: a manual `romp-manager up` from inside tmux leaked
    # $TMUX to kernels + SDK sessions, whose tmux-status hooks then hijacked the ATTACHED
    # session's @romp-session-id (live session flapping "dead" -> bogus revive). The manager
    # must scrub TMUX/TMUX_PANE from its own env before any kernel spawns.
    local envdump="$TEST_DIR/kernel-env"
    printf '#!/usr/bin/env bash\nenv > "%s"\nexec sleep 30\n' "$envdump" > "$FAKE"
    chmod +x "$FAKE"
    env TMUX="/tmp/tmux-000/default,99999,7" TMUX_PANE="%7" \
        ROMP_MANAGER_PORT=7581 ROMP_SERVE_PORT=7582 ROMP_SERVE_BIN="$FAKE" \
        node "$MGR" up >/dev/null 2>&1 &
    MGR_PID=$!
    local i
    for i in $(seq 1 50); do [ -s "$envdump" ] && break; sleep 0.1; done
    mcurl -fsS -X POST "http://127.0.0.1:7581/stop" >/dev/null 2>&1 || true
    [ -s "$envdump" ]
    # `run` + status, NOT a bare `! grep`: `!` is exempt from set -e, so mid-test it asserts nothing.
    run grep -q '^TMUX=' "$envdump"
    [ "$status" -ne 0 ]
    run grep -q '^TMUX_PANE=' "$envdump"
    [ "$status" -ne 0 ]
    grep -q '^ROMP_SERVE_BIN=' "$envdump"   # the dump is real: other env DID flow through
}

@test "quiet-mode refresh defers while turns are in flight, coalesces, applies on the quiet event" {
    command -v node >/dev/null 2>&1 || skip "node not available"
    command -v python3 >/dev/null 2>&1 || skip "python3 not available"

    # Fake kernel: binds the serve port, answers /busy from a file the test flips, and logs each
    # spawn — so the bounce (SIGTERM + respawn) is observable as a second spawn line.
    local BUSY="$TEST_DIR/busy" SPAWNS="$TEST_DIR/spawns" FAKEK="$TEST_DIR/fake-kernel"
    echo 2 > "$BUSY"
    cat > "$FAKEK" <<'PYEOF'
#!/usr/bin/env python3
import http.server, json, os
with open(os.environ["SPAWN_LOG"], "a") as f:
    f.write("spawn\n")
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            n = int(open(os.environ["BUSY_FILE"]).read().strip())
        except Exception:
            n = 0
        b = json.dumps({"busy": n}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers()
        self.wfile.write(b)
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", int(os.environ["ROMP_SERVE_PORT"])), H).serve_forever()
PYEOF
    chmod +x "$FAKEK"

    env BUSY_FILE="$BUSY" SPAWN_LOG="$SPAWNS" ROMP_QUIET_POLL_MS=200 \
        ROMP_MANAGER_PORT=7591 ROMP_SERVE_PORT=7592 ROMP_SERVE_BIN="$FAKEK" \
        node "$MGR" up >/dev/null 2>&1 &
    MGR_PID=$!
    local i
    for i in $(seq 1 50); do
        mcurl -fsS "http://127.0.0.1:7591/status" >/dev/null 2>&1 && [ -s "$SPAWNS" ] && break
        sleep 0.1
    done
    [ "$(grep -c spawn "$SPAWNS")" -eq 1 ]

    # Two quiet-mode refreshes while turns are in flight: both defer, the second coalesces.
    run mcurl -fsS -X POST "http://127.0.0.1:7591/restart-all?when=quiet"
    [[ "$output" == *'"deferred":true'* ]]
    run mcurl -fsS -X POST "http://127.0.0.1:7591/restart-all?when=quiet"
    [[ "$output" == *'"coalesced":2'* ]]

    # Still busy after several poll cycles -> no bounce happened.
    sleep 1
    [ "$(grep -c spawn "$SPAWNS")" -eq 1 ]

    # The fleet quiets -> exactly ONE bounce delivers both queued refreshes.
    echo 0 > "$BUSY"
    for i in $(seq 1 60); do [ "$(grep -c spawn "$SPAWNS")" -ge 2 ] && break; sleep 0.1; done
    [ "$(grep -c spawn "$SPAWNS")" -eq 2 ]
    mcurl -fsS -X POST "http://127.0.0.1:7591/stop" >/dev/null 2>&1 || true
}
