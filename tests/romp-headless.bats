#!/usr/bin/env bats

# `romp send|interrupt|end <session> [text]` — headless session control through the kernel
# HTTP API (2026-07-05: interrupt/end existed only as browser WS ops, so a runaway session had no
# headless stop). Bare words since round 3 (2026-07-25); the dashed spellings stay as SILENT
# aliases because agent-facing text delivered before then names them. A tiny one-shot python
# server stands in for the kernel; failures must be LOUD (non-zero exit + a message), never a
# silent curl swallow.

ROMP_SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp"

setup() {
    TEST_DIR="$(mktemp -d)"
}

teardown() {
    [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null
    rm -rf "$TEST_DIR"
}

# Start a one-shot fake kernel; writes its port to $TEST_DIR/port and its request to $TEST_DIR/req.
start_fake_kernel() {   # $1 = response body
    python3 - "$1" "$TEST_DIR" <<'PY' &
import http.server, json, sys
body, tdir = sys.argv[1].encode(), sys.argv[2]
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        with open(tdir + "/req", "w") as f:
            f.write(self.path + "\n" + self.rfile.read(n).decode())
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
s = http.server.HTTPServer(("127.0.0.1", 0), H)
with open(tdir + "/port", "w") as f:
    f.write(str(s.server_address[1]))
s.handle_request()
PY
    SERVER_PID=$!
    until [ -s "$TEST_DIR/port" ]; do sleep 0.05; done
    export ROMP_KERNEL_PORT="$(cat "$TEST_DIR/port")"
}

@test "romp interrupt <name> POSTs /interrupt and exits 0 on ok" {
    start_fake_kernel '{"ok": true}'
    run "$ROMP_SCRIPT" interrupt runaway
    [ "$status" -eq 0 ]
    [[ "$output" == *"ok (runaway)"* ]]
    grep -q "^/interrupt$" <(head -1 "$TEST_DIR/req")
    grep -q '"name": "runaway"' "$TEST_DIR/req"
}

@test "romp end stops a session through /end" {
    start_fake_kernel '{"ok": true}'
    run "$ROMP_SCRIPT" end done-with-it
    [ "$status" -eq 0 ]
    grep -q "^/end$" <(head -1 "$TEST_DIR/req")
}

@test "romp end self resolves through ROMP_SID and defers to idle by default" {
    # a session closing ITSELF after its work (the user 2026-08-15): self = the spawn-frozen sid,
    # and the kernel kills at the turn's settle so the goodbye lands first
    start_fake_kernel '{"ok": true}'
    ROMP_SID="11111111-2222-3333-4444-555555555555" run "$ROMP_SCRIPT" end self
    [ "$status" -eq 0 ]
    grep -q "^/end$" <(head -1 "$TEST_DIR/req")
    grep -q '"id": "11111111-2222-3333-4444-555555555555"' "$TEST_DIR/req"
    grep -q '"when": "idle"' "$TEST_DIR/req"
}

@test "romp end self --now skips the deferral; self outside a session fails loudly" {
    start_fake_kernel '{"ok": true}'
    ROMP_SID="11111111-2222-3333-4444-555555555555" run "$ROMP_SCRIPT" end self --now
    [ "$status" -eq 0 ]
    ! grep -q '"when"' "$TEST_DIR/req"
    ROMP_SID="" run "$ROMP_SCRIPT" end self
    [ "$status" -eq 2 ]
    [[ "$output" == *"only works from inside a romp SDK session"* ]]
}

@test "the dashed spellings are silent aliases: --send works and says nothing about it" {
    # Agent-facing text delivered before 2026-07-25 (postal reply footers, skill
    # docs in old transcripts) names the dashed forms; they must keep working
    # with no retirement noise.
    start_fake_kernel '{"ok": true}'
    run "$ROMP_SCRIPT" --send helper "hello"
    [ "$status" -eq 0 ]
    [[ "$output" != *"retired"* ]]
    grep -q "^/send$" <(head -1 "$TEST_DIR/req")
    grep -q '"name": "helper"' "$TEST_DIR/req"
}

@test "romp send ships JSON-safe text" {
    start_fake_kernel '{"ok": true}'
    run "$ROMP_SCRIPT" send helper 'fix the "thing" \ and this'
    [ "$status" -eq 0 ]
    grep -q "^/send$" <(head -1 "$TEST_DIR/req")
    python3 - "$TEST_DIR/req" <<'PY'
import json, sys
body = open(sys.argv[1]).read().split("\n", 1)[1]
assert json.loads(body) == {"name": "helper", "text": 'fix the "thing" \\ and this'}, body
PY
}

@test "romp send --tag appends the render-hint marker; bad labels and missing text exit 2" {
    start_fake_kernel '{"ok": true}'
    run "$ROMP_SCRIPT" send helper --tag kickoff 'boot brief for the run'
    [ "$status" -eq 0 ]
    python3 - "$TEST_DIR/req" <<'PY'
import json, sys
body = open(sys.argv[1]).read().split("\n", 1)[1]
d = json.loads(body)
assert d["name"] == "helper", d
assert d["text"] == "boot brief for the run\n\n<!-- romp-tag: kickoff -->", d
PY
    run "$ROMP_SCRIPT" send helper --tag 'two words' 'text'
    [ "$status" -eq 2 ]
    [[ "$output" == *"--tag must be one word"* ]]
    run "$ROMP_SCRIPT" send helper --tag kickoff
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp send"* ]]
}

@test "a kernel refusal is loud: non-zero exit + the kernel's answer" {
    start_fake_kernel '{"ok": false, "error": "id or name required"}'
    run "$ROMP_SCRIPT" interrupt ghost
    [ "$status" -eq 1 ]
    [[ "$output" == *"kernel refused"* ]]
}

@test "an unreachable kernel is loud, not a silent curl swallow" {
    ROMP_KERNEL_PORT=1 run "$ROMP_SCRIPT" interrupt anyone
    [ "$status" -eq 1 ]
    [[ "$output" == *"kernel not reachable"* ]]
}

@test "usage errors exit 2: missing session name, send without text" {
    run "$ROMP_SCRIPT" interrupt
    [ "$status" -eq 2 ]
    run "$ROMP_SCRIPT" send lonely
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp send"* ]]
}
