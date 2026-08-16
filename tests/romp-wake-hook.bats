#!/usr/bin/env bats

# romp-wake.sh is the event-driven judge trigger: on the Stop / UserPromptSubmit
# hook events it pokes the kernel's POST /tick so the producer runs a pass NOW
# instead of on the 20s backstop. It must (a) hit /tick on the configured port,
# and (b) NEVER fail the turn — even with no kernel/curl reachable.

setup() {
    TEST_DIR="$(mktemp -d)"
    MOCK="$TEST_DIR/mock"; mkdir -p "$MOCK"
    export CURL_LOG="$TEST_DIR/curl.log"
    # Mock curl: log args (the hook detaches it into a subshell, so the test polls
    # for the log below). Exported CURL_LOG so the detached mock can still find it.
    export CURL_STDIN="$TEST_DIR/curl.stdin"
    # ...and whatever arrived on stdin — the token travels on stdin now (curl --config -), so a
    # test that only watched argv could not tell a working auth header from none at all. Read
    # stdin BEFORE anything else, or the config never lands.
    cat > "$MOCK/curl" <<'MOCK'
#!/usr/bin/env bash
echo "curl $*" >> "$CURL_LOG"
cat >> "$CURL_STDIN" 2>/dev/null
MOCK
    chmod +x "$MOCK/curl"
    export PATH="$MOCK:$PATH"
    # Clear the port env so the default-port test asserts the hook's OWN default.
    # Run the suite from inside a romp session and it inherits that kernel's
    # ROMP_SERVE_PORT, which silently turns the default case into an override
    # case — a green CI and a red local run (2026-07-24).
    unset ROMP_SERVE_PORT ROMP_KERNEL_PORT
    HOOK="$(cd "$(dirname "$BATS_TEST_FILENAME")/../hooks" && pwd)/romp-wake.sh"
}

teardown() { rm -rf "$TEST_DIR"; }

@test "romp-wake pokes POST /tick on the configured kernel port" {
    ROMP_SERVE_PORT=7777 run bash -c 'echo "{}" | "'"$HOOK"'"'
    [ "$status" -eq 0 ]
    # curl is detached (( curl & )), so poll briefly for the log to land
    for _ in $(seq 1 40); do [ -s "$CURL_LOG" ] && break; sleep 0.05; done
    grep -q -- '-X POST' "$CURL_LOG"
    grep -q 'http://127.0.0.1:7777/tick' "$CURL_LOG"
}

@test "romp-wake defaults to port 29855 when ROMP_SERVE_PORT is unset" {
    run bash -c 'echo "{}" | "'"$HOOK"'"'
    [ "$status" -eq 0 ]
    for _ in $(seq 1 40); do [ -s "$CURL_LOG" ] && break; sleep 0.05; done
    grep -q 'http://127.0.0.1:29855/tick' "$CURL_LOG"
}

@test "romp-wake accepts ROMP_KERNEL_PORT, the other spelling of the same port" {
    # bin/romp-serve exports both, but a hook can also run under a shell that set only the
    # documented one — poking the default kernel from an aux session is a cross-instance poke.
    ROMP_KERNEL_PORT=7778 run bash -c 'echo "{}" | "'"$HOOK"'"'
    [ "$status" -eq 0 ]
    for _ in $(seq 1 40); do [ -s "$CURL_LOG" ] && break; sleep 0.05; done
    grep -q 'http://127.0.0.1:7778/tick' "$CURL_LOG"
}

@test "romp-wake sends the serve token header (env override form)" {
    # The kernel gates every request on the serve token, loopback included — the poke
    # must carry X-Romp-Token or it 403s silently and the judges fall back to the backstop.
    ROMP_SERVE_TOKEN=tok-from-env run bash -c 'echo "{}" | "'"$HOOK"'"'
    [ "$status" -eq 0 ]
    for _ in $(seq 1 40); do [ -s "$CURL_STDIN" ] && break; sleep 0.05; done
    grep -q 'X-Romp-Token: tok-from-env' "$CURL_STDIN"   # on stdin now, never argv
}

@test "romp-wake reads the serve token from the state file" {
    export XDG_STATE_HOME="$TEST_DIR/state"
    mkdir -p "$XDG_STATE_HOME/romp"
    printf 'tok-from-file\n' > "$XDG_STATE_HOME/romp/serve-token"
    run bash -c 'echo "{}" | "'"$HOOK"'"'
    [ "$status" -eq 0 ]
    for _ in $(seq 1 40); do [ -s "$CURL_STDIN" ] && break; sleep 0.05; done
    grep -q 'X-Romp-Token: tok-from-file' "$CURL_STDIN"  # on stdin now, never argv
}

@test "romp-wake never fails the turn when the kernel is unreachable" {
    rm "$MOCK/curl"                       # use the real curl against a dead port
    ROMP_SERVE_PORT=1 run bash -c 'printf "" | "'"$HOOK"'"'
    [ "$status" -eq 0 ]
}

@test "romp-wake never puts the serve token in curl's argv" {
    # /proc/<pid>/cmdline is world-readable, so a token in argv is full control of every session
    # handed to any other account on the machine. It goes in on stdin as a curl config instead.
    export ROMP_SERVE_TOKEN="TESTTOKENDONOTUSE"
    ROMP_SERVE_PORT=7777 run bash -c 'echo "{}" | "'"$HOOK"'"'
    [ "$status" -eq 0 ]
    for _ in $(seq 1 40); do [ -s "$CURL_LOG" ] && break; sleep 0.05; done
    for _ in $(seq 1 40); do [ -s "$CURL_STDIN" ] && break; sleep 0.05; done

    # `run` + an explicit status check, NOT `! grep …`: bats runs each test under errexit, but a
    # `!`-prefixed pipeline is exempt from it, so a bare `! grep` anywhere except the test's LAST
    # line reports nothing when it fails — the token could be in argv and this test would still
    # pass. This is the one assertion in the file that has to be armed, so arm it explicitly.
    run grep -q "TESTTOKENDONOTUSE" "$CURL_LOG"
    [ "$status" -ne 0 ]
    grep -q -- "--config" "$CURL_LOG"
    # ...and the header really is being sent, so this is not "secure by not authenticating"
    grep -q "X-Romp-Token: TESTTOKENDONOTUSE" "$CURL_STDIN"
}

@test "romp-wake escapes a token that would otherwise break curl's config syntax" {
    # Only reachable via ROMP_SERVE_TOKEN (the minted token is base64url), but a quote in the
    # value would truncate the header and silently unauthenticate the poke.
    export ROMP_SERVE_TOKEN='ab"c\d'
    ROMP_SERVE_PORT=7777 run bash -c 'echo "{}" | "'"$HOOK"'"'
    [ "$status" -eq 0 ]
    for _ in $(seq 1 40); do [ -s "$CURL_STDIN" ] && break; sleep 0.05; done
    # curl's config syntax is quoted, so the " and the \ must arrive escaped; curl parses them
    # back to the original token. Compared as a fixed string — the escaping is the point here.
    run cat "$CURL_STDIN"
    [ "$output" = 'header = "X-Romp-Token: ab\"c\\d"' ]
}
