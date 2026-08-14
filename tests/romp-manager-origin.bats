#!/usr/bin/env bats

# Authentication, CSRF and pre-auth body guards for the manager control port.

setup() {
    TEST_DIR="$(mktemp -d)"
    MGR="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-manager"
    # Fake kernel launcher: stays alive without binding a real port.
    FAKE="$TEST_DIR/fake-serve"
    printf '#!/usr/bin/env bash\nexec sleep 30\n' > "$FAKE"
    chmod +x "$FAKE"
    CPORT=7571; MPORT=7572
    TOKEN=manager_test_token_0123456789abcdef
    export ROMP_MANAGER_TOKEN="$TOKEN"
    export XDG_STATE_HOME="$TEST_DIR/state"
}

teardown() {
    [[ -n "${MGR_PID:-}" ]] && kill "$MGR_PID" 2>/dev/null || true
    rm -rf "$TEST_DIR"
}

@test "manager requires its token, rejects cross-site Origin, and caps bodies before auth" {
    command -v node >/dev/null 2>&1 || skip "node not available"
    command -v curl >/dev/null 2>&1 || skip "curl not available"

    env ROMP_MANAGER_PORT=$CPORT ROMP_SERVE_PORT=$MPORT ROMP_SERVE_BIN="$FAKE" \
        node "$MGR" up >/dev/null 2>&1 &
    MGR_PID=$!

    local i
    for i in $(seq 1 40); do
        curl -fsS -H "X-Romp-Manager-Token: $TOKEN" \
            "http://127.0.0.1:$CPORT/status" >/dev/null 2>&1 && break
        sleep 0.1
    done

    # A loopback caller without the same-user credential is still unauthorized.
    run curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$CPORT/status"
    [ "$output" = "401" ]

    # No Origin + the token (server-side client) → 200.
    run curl -s -o /dev/null -w '%{http_code}' -H "X-Romp-Manager-Token: $TOKEN" \
        "http://127.0.0.1:$CPORT/status"
    [ "$output" = "200" ]

    # Cross-site Origin wins even when the caller knows the token.
    run curl -s -o /dev/null -w '%{http_code}' -H "X-Romp-Manager-Token: $TOKEN" \
        -H 'Origin: http://evil.example' \
        "http://127.0.0.1:$CPORT/status"
    [ "$output" = "403" ]

    # Cross-site state-changing POST (the real attack) → 403.
    run curl -s -o /dev/null -w '%{http_code}' -X POST -H "X-Romp-Manager-Token: $TOKEN" \
        -H 'Origin: http://evil.example' \
        "http://127.0.0.1:$CPORT/restart-all"
    [ "$output" = "403" ]

    # Framing is checked before provenance/auth: even a foreign request cannot make the manager
    # retain or parse an unauthenticated body on a keep-alive connection.
    run curl -s -o /dev/null -w '%{http_code}' -X POST -d x \
        -H 'Origin: http://evil.example' \
        "http://127.0.0.1:$CPORT/restart-all"
    [ "$output" = "413" ]

    # A loopback Origin (the local web UI, if it ever calls directly) → allowed.
    run curl -s -o /dev/null -w '%{http_code}' -H "X-Romp-Manager-Token: $TOKEN" \
        -H "Origin: http://127.0.0.1:$CPORT" \
        "http://127.0.0.1:$CPORT/status"
    [ "$output" = "200" ]

    # All operations are bodyless. Reject declared/chunked bodies before checking auth and
    # leave the manager healthy after closing the unread connection.
    run curl -s -o /dev/null -w '%{http_code}' -X POST -d x \
        "http://127.0.0.1:$CPORT/restart-all"
    [ "$output" = "413" ]
    run curl -s -o /dev/null -w '%{http_code}' -H "X-Romp-Manager-Token: $TOKEN" \
        "http://127.0.0.1:$CPORT/status"
    [ "$output" = "200" ]
}
