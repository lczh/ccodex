#!/usr/bin/env bats

# romp-serve maps the manager's spawn contract (--port / ROMP_SERVE_PORT) onto the
# Python kernel's env and execs it. The kernel binds loopback only; tailnet reach
# is `tailscale serve` proxying to loopback, so there is no persisted host opt-in
# (`romp --serve` removed 2026-07-19).

BIN="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)"
ROMP_SERVE="$BIN/romp-serve"
ROMP_SCRIPT="$BIN/romp"

setup() {
    # Running this suite INSIDE a romp session inherits the live kernel's port env, which would
    # turn every "unset" case below into an override (same trap tests/romp-service.bats documents).
    unset ROMP_SERVE_PORT ROMP_KERNEL_PORT
    TEST_DIR="$(mktemp -d)"
    export HOME="$TEST_DIR/home"
    export XDG_STATE_HOME="$HOME/.local/state"
    mkdir -p "$XDG_STATE_HOME/romp"
    # Stub kernel: print the env romp-serve hands it, then exit (no real server).
    export ROMP_KERNEL_BIN="$TEST_DIR/stub-kernel"
    cat > "$ROMP_KERNEL_BIN" << 'STUB'
#!/usr/bin/env bash
echo "PORT=${ROMP_KERNEL_PORT:-}"
echo "SERVEPORT=${ROMP_SERVE_PORT:-}"
echo "HOST=${ROMP_SERVE_HOST:-}"
echo "NOOPEN=${ROMP_KERNEL_NO_OPEN:-}"
echo "MGRPID=${ROMP_MANAGER_PID:-}"
STUB
    chmod +x "$ROMP_KERNEL_BIN"
    # romp-serve now execs the kernel VIA a picked python (`exec "$PY" "$KERNEL"`); the stub kernel is
    # bash, so hand it a "python" that just runs its argument as a shell script.
    export ROMP_PYTHON="$TEST_DIR/fake-python"
    cat > "$ROMP_PYTHON" << 'SHIM'
#!/usr/bin/env bash
exec bash "$@"
SHIM
    chmod +x "$ROMP_PYTHON"
}

teardown() { rm -rf "$TEST_DIR"; }

@test "romp-serve: maps --port to ROMP_KERNEL_PORT, sets no-open, execs the kernel" {
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 0 ]
    [[ "$output" == *"PORT=9999"* ]]
    [[ "$output" == *"NOOPEN=1"* ]]
}

@test "romp-serve: host defaults to 127.0.0.1 with no opt-in" {
    run "$ROMP_SERVE" --port 9999
    [[ "$output" == *"HOST=127.0.0.1"* ]]
}

@test "romp-serve: a stale serve-host file can NOT rebind the kernel off loopback" {
    # The `romp --serve` opt-in is removed; a leftover state file must be ignored,
    # never silently expose the kernel on 0.0.0.0.
    printf '0.0.0.0\n' > "$XDG_STATE_HOME/romp/serve-host"
    run "$ROMP_SERVE" --port 9999
    [[ "$output" == *"HOST=127.0.0.1"* ]]
}

@test "romp-serve: explicit --host still wins (the manager's spawn seam)" {
    run "$ROMP_SERVE" --port 9999 --host 0.0.0.0
    [[ "$output" == *"HOST=0.0.0.0"* ]]
}

@test "romp-serve: ROMP_SERVE_PORT fallback + forwards ROMP_MANAGER_PID" {
    ROMP_MANAGER_PID=4242 ROMP_SERVE_PORT=29855 run "$ROMP_SERVE"
    [[ "$output" == *"PORT=29855"* ]]
    [[ "$output" == *"MGRPID=4242"* ]]
}

# ─── the two spellings of the listen port ───────────────────────────────────────────────────
# ROMP_SERVE_PORT (service-facing) and ROMP_KERNEL_PORT (process-facing) name ONE value, and
# this script is the seam where they meet. It used to read only the first and stamp it over the
# second, so renumbering a second kernel with the documented knob alone put the kernel on the
# primary's port while the CLI kept printing the configured one.

@test "romp-serve: ROMP_KERNEL_PORT alone (the documented knob) reaches the kernel" {
    ROMP_KERNEL_PORT=29856 run "$ROMP_SERVE"
    [ "$status" -eq 0 ]
    [[ "$output" == *"PORT=29856"* ]]
}

@test "romp-serve: exports BOTH spellings from the one resolved port" {
    # Nothing downstream (the postal bus, the wake hook, a `romp` verb in a session) may read a
    # stale copy of the other name.
    ROMP_KERNEL_PORT=29856 run "$ROMP_SERVE"
    [[ "$output" == *"PORT=29856"* ]]
    [[ "$output" == *"SERVEPORT=29856"* ]]
    ROMP_SERVE_PORT=29857 run "$ROMP_SERVE"
    [[ "$output" == *"PORT=29857"* ]]
    [[ "$output" == *"SERVEPORT=29857"* ]]
}

@test "romp-serve: --port settles a disagreement and wins over both env spellings" {
    # The manager always passes --port; that is the kernel's own name for itself, so a stale
    # inherited env copy must never override it.
    ROMP_SERVE_PORT=29855 ROMP_KERNEL_PORT=29999 run "$ROMP_SERVE" --port 30001
    [ "$status" -eq 0 ]
    [[ "$output" == *"PORT=30001"* ]]
    [[ "$output" == *"SERVEPORT=30001"* ]]
}

@test "romp-serve: conflicting spellings with no --port REFUSE to start" {
    # A silent pick here is the collision that reports success. Fail loudly instead.
    ROMP_SERVE_PORT=29855 ROMP_KERNEL_PORT=29856 run "$ROMP_SERVE"
    [ "$status" -ne 0 ]
    [[ "$output" == *"29855"* ]]
    [[ "$output" == *"29856"* ]]
    ! printf '%s\n' "$output" | grep -q '^PORT='     # the stub kernel never ran
}

@test "romp-serve: matching spellings are not a conflict" {
    ROMP_SERVE_PORT=29856 ROMP_KERNEL_PORT=29856 run "$ROMP_SERVE"
    [ "$status" -eq 0 ]
    [[ "$output" == *"PORT=29856"* ]]
}

@test "romp-serve: neither set and no --port leaves both unset (the kernel's own default)" {
    run "$ROMP_SERVE"
    [ "$status" -eq 0 ]
    [ "$(printf '%s\n' "$output" | grep '^PORT=')" = "PORT=" ]
    [ "$(printf '%s\n' "$output" | grep '^SERVEPORT=')" = "SERVEPORT=" ]
}

@test "romp --serve: removed — rejected as unknown, writes no state" {
    run "$ROMP_SCRIPT" --serve on
    [ "$status" -ne 0 ]
    [[ "$output" == *"unknown option"* ]]
    [ ! -f "$XDG_STATE_HOME/romp/serve-host" ]
}

# ─── pick_python: the kernel runs on the best python available (Agent SDK needs >= 3.10) ────────
# Unit tests over the extracted function (it uses only `command -v` + $HOME probing, so a bare
# fake PATH is enough). The e2e wiring (exec "$PY" "$KERNEL") is covered by every test above via
# the ROMP_PYTHON shim in setup().

extract_pick() { sed -n '/^pick_python()/,/^}/p' "$1"; }

@test "pick_python: ROMP_PYTHON override wins verbatim" {
    eval "$(extract_pick "$ROMP_SERVE")"
    ROMP_PYTHON=/opt/custom/python run pick_python
    [ "$output" = "/opt/custom/python" ]
}

@test "pick_python: newest python3.1x on PATH beats plain python3" {
    fakebin="$TEST_DIR/fakebin"; mkdir -p "$fakebin"
    printf '#!/bin/sh\n' > "$fakebin/python3.12"; chmod +x "$fakebin/python3.12"
    printf '#!/bin/sh\n' > "$fakebin/python3";    chmod +x "$fakebin/python3"
    eval "$(extract_pick "$ROMP_SERVE")"
    ROMP_PYTHON= PATH="$fakebin" run pick_python
    [ "$output" = "$fakebin/python3.12" ]
}

@test "pick_python: probes ~/.local/bin explicitly (non-login ssh shells lack it on PATH)" {
    mkdir -p "$HOME/.local/bin"
    printf '#!/bin/sh\n' > "$HOME/.local/bin/python3.11"; chmod +x "$HOME/.local/bin/python3.11"
    fakebin="$TEST_DIR/fakebin2"; mkdir -p "$fakebin"
    printf '#!/bin/sh\n' > "$fakebin/python3"; chmod +x "$fakebin/python3"
    eval "$(extract_pick "$ROMP_SERVE")"
    ROMP_PYTHON= PATH="$fakebin" run pick_python
    [ "$output" = "$HOME/.local/bin/python3.11" ]
}

@test "pick_python: falls back to plain python3 when no 3.1x exists anywhere" {
    fakebin="$TEST_DIR/fakebin3"; mkdir -p "$fakebin"
    printf '#!/bin/sh\n' > "$fakebin/python3"; chmod +x "$fakebin/python3"
    eval "$(extract_pick "$ROMP_SERVE")"
    ROMP_PYTHON= PATH="$fakebin" run pick_python
    [ "$output" = "$fakebin/python3" ]
}

@test "pick_python: romp-serve and romp-sdk-setup carry the SAME picker (venv must match the kernel)" {
    diff <(sed -n '/^pick_python()/,/^}/p' "$ROMP_SERVE") \
         <(sed -n '/^pick_python()/,/^}/p' "$BIN/romp-sdk-setup")
}

# ── the install-latch gate (the user's audit, 2026-08-17) ────────────────────────────────────────
# An armed latch naming the current HEAD means the last update moved the code but install.sh never
# finished. The gate runs BEFORE the kernel execs (module imports otherwise happen on the
# half-installed checkout): heal under the checkout's lock, refuse to start unless install passes.
# The fixture is a copy of romp-serve inside its own tiny git repo, because the script resolves its
# repo from its own location.

_latch_fixture() {
    FIX="$TEST_DIR/fix"
    mkdir -p "$FIX/bin"
    cp "$ROMP_SERVE" "$FIX/bin/romp-serve"
    git -C "$FIX" init -q -b main
    git -C "$FIX" -c user.email=t@t -c user.name=t commit -q --allow-empty -m x
    GD="$(git -C "$FIX" rev-parse --absolute-git-dir)"
    CUR="$(git -C "$FIX" rev-parse --short=8 HEAD | head -c 8)"
    printf '#!/usr/bin/env bash\necho KERNEL_RAN\n' > "$FIX/bin/romp-kernel"
    chmod +x "$FIX/bin/romp-kernel"
    export ROMP_KERNEL_BIN="$FIX/bin/romp-kernel"   # outrank the suite-wide stub from setup()
}

@test "romp-serve: an armed latch with a FAILING install refuses to start the kernel (exit 70)" {
    _latch_fixture
    printf '#!/bin/sh\nexit 1\n' > "$FIX/install.sh"; chmod +x "$FIX/install.sh"
    printf '%s' "$CUR" > "$GD/romp-install-failed"
    run "$FIX/bin/romp-serve"
    [ "$status" -eq 70 ]
    [[ "$output" != *KERNEL_RAN* ]]
    [[ "$output" == *"half-installed"* ]]
    [ -s "$GD/romp-install-failed" ]
}

@test "romp-serve: an armed latch heals (install passes), spends the latch, and starts the kernel" {
    _latch_fixture
    printf '#!/bin/sh\nexit 0\n' > "$FIX/install.sh"; chmod +x "$FIX/install.sh"
    printf '%s' "$CUR" > "$GD/romp-install-failed"
    run "$FIX/bin/romp-serve"
    [ "$status" -eq 0 ]
    [[ "$output" == *KERNEL_RAN* ]]
    [ ! -e "$GD/romp-install-failed" ]
}

@test "romp-serve: a latch naming some OTHER commit is moot — cleared, kernel starts, no install" {
    _latch_fixture
    printf '#!/bin/sh\necho INSTALL_RAN; exit 1\n' > "$FIX/install.sh"; chmod +x "$FIX/install.sh"
    printf '%s' "00000000" > "$GD/romp-install-failed"
    run "$FIX/bin/romp-serve"
    [ "$status" -eq 0 ]
    [[ "$output" == *KERNEL_RAN* ]]
    [[ "$output" != *INSTALL_RAN* ]]
    [ ! -e "$GD/romp-install-failed" ]
}

@test "romp-serve: an UNREADABLE HEAD with an armed latch refuses — never reads as moot" {
    _latch_fixture
    printf '%s' "$CUR" > "$GD/romp-install-failed"
    printf 'ref: refs/heads/never-born\n' > "$GD/HEAD"   # repo still detected; HEAD unresolvable
    run "$FIX/bin/romp-serve"
    [ "$status" -eq 70 ]
    [[ "$output" != *KERNEL_RAN* ]]
    [ -s "$GD/romp-install-failed" ]                   # a git failure never erases the record
}

@test "romp-manager: exit 70 respawns on the long half-installed cadence, not the crash counter" {
    # a slow-failing install outlives the quick-crash window, so the counter reset back-to-back
    # install attempts forever (the adversarial review, 2026-08-17); code 70 gets a fixed delay
    grep -q "HALF_INSTALLED_BACKOFF_MS = 60000" "$BIN/romp-manager"
    grep -q "code === 70 ? HALF_INSTALLED_BACKOFF_MS" "$BIN/romp-manager"
}

@test "romp-serve: a HELD update lock refuses startup even with NO latch (the mid-update window)" {
    # the audit reproduced this directly: latch-before-lock let a kernel start in the window where
    # an updater held the lock but had not armed the latch yet — mid-update, HEAD about to move
    # under the booting kernel's imports (the user's audit, 2026-08-18). Lock comes FIRST now.
    _latch_fixture
    printf '#!/bin/sh\nexit 0\n' > "$FIX/install.sh"; chmod +x "$FIX/install.sh"
    python3 - "$GD/romp-update.lock" <<'HOLDPY' &
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX)
time.sleep(60)
HOLDPY
    HOLDER=$!
    sleep 1
    ROMP_GATE_LOCK_WAIT=1 run "$FIX/bin/romp-serve"
    kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null || true
    [ "$status" -eq 70 ]
    [[ "$output" != *KERNEL_RAN* ]]
}
