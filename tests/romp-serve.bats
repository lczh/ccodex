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
    # the COMMITTED install consults an untracked flag (+ echoes): the gate heal runs the
    # SNAPSHOT's bytes now (v1.3.16 P1.1), so working-tree swaps of install.sh are inert
    printf '#!/bin/sh\necho INSTALL_RAN\n[ -e "${ROMP_INSTALL_TARGET:-.}/.install-broken" ] && exit 1\nexit 0\n' \
        > "$FIX/install.sh"
    chmod +x "$FIX/install.sh"
    # the kernel stub is COMMITTED: the gate heal's gen_build archives the commit and
    # content-checks the unpacked generation for an executable bin/romp-kernel (r47) — an
    # install.sh-only commit would fail the heal it used to pass
    printf '#!/usr/bin/env bash\necho KERNEL_RAN\n' > "$FIX/bin/romp-kernel"
    chmod +x "$FIX/bin/romp-kernel"
    # the serve copy is committed too: the validated content check requires kernel AND serve
    # in the archived tree (the v1.3.20 audit's P2)
    git -C "$FIX" add install.sh bin/romp-kernel bin/romp-serve
    git -C "$FIX" -c user.email=t@t -c user.name=t commit -qm x
    GD="$(git -C "$FIX" rev-parse --absolute-git-dir)"
    CUR="$(git -C "$FIX" rev-parse --short=8 HEAD | head -c 8)"
    export ROMP_KERNEL_BIN="$FIX/bin/romp-kernel"   # outrank the suite-wide stub from setup()
}

@test "romp-serve: an armed latch with a FAILING install refuses to start the kernel (exit 70)" {
    _latch_fixture
    touch "$FIX/.install-broken"
    printf '%s' "$CUR" > "$GD/romp-install-failed"
    run "$FIX/bin/romp-serve"
    [ "$status" -eq 70 ]
    [[ "$output" != *KERNEL_RAN* ]]
    [[ "$output" == *"half-installed"* ]]
    [ -s "$GD/romp-install-failed" ]
}

@test "romp-serve: an armed latch heals (install passes), spends the latch, and starts the kernel" {
    _latch_fixture
    printf '%s' "$CUR" > "$GD/romp-install-failed"
    run "$FIX/bin/romp-serve"
    [ "$status" -eq 0 ]
    [[ "$output" == *KERNEL_RAN* ]]
    [ ! -e "$GD/romp-install-failed" ]
    # the heal ARMS the update marker before spending the latch (the r48 verification: this
    # gate was the one latch-spending leg that didn't — the healed release then exec'd
    # mutated live bytes on a dirty tree)
    [ -s "$GD/romp-restart-needed" ]
    FULL="$(git -C "$FIX" rev-parse HEAD)"
    [ "$(cat "$GD/romp-restart-needed")" = "$FULL" ]
}

@test "romp-serve: the boot that HEALS an update runs the VERIFIED generation, not dirty live bytes (r48)" {
    # the r48 verification's P1, end-to-end: latch armed at HEAD, NO marker (the update died
    # before arming), a tracked edit in the tree. The gate heals, arms, builds — and the exec
    # must re-resolve onto the generation instead of the pre-resolved live kernel.
    _gen_fixture
    rm -rf "$GENGD/romp-run-"*
    printf '#!/bin/sh\necho INSTALL_RAN\nexit 0\n' > "$GENFIX/install.sh"
    git -C "$GENFIX" add install.sh
    git -C "$GENFIX" -c user.email=t@t -c user.name=t commit -qm heal-me
    GENH8="$(git -C "$GENFIX" rev-parse --short=8 HEAD)"
    printf '%s' "$GENH8" > "$GENGD/romp-install-failed"
    echo change >> "$GENFIX/tracked.txt"               # the dirty tree that used to win
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 0 ]
    [ ! -e "$GENGD/romp-install-failed" ]
    [ -s "$GENGD/romp-restart-needed" ]
    # the generation launch exports ROMP_CHECKOUT — the tell that verified bytes ran
    [[ "$output" == *"LIVE_CHECKOUT=$GENFIX"* ]]
}

@test "romp-serve: an EMPTY update marker refuses to start — unknown is never absent (r49)" {
    # the v1.3.21 audit's P1.1: a torn arm left an empty marker, the gate read it as
    # no-marker, and the boot took the dirty-live leg under the very intent that forbids it
    _gen_fixture
    : > "$GENGD/romp-restart-needed"
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 70 ]
    [[ "$output" != *KERNEL_RAN* ]]
    [[ "$output" == *"EMPTY"* ]]
}

@test "romp-serve: the launch pick is made UNDER the gate lock and applied at exec (r49)" {
    # the v1.3.21 audit's P1.2, executed: bash used to pick a generation BEFORE the gate's
    # (up to 90s) lock wait — an update landing in that window launched the OLD pick. The
    # pick now happens inside the gate: a generation built/armed by the gate's own heal is
    # what boots, which only the under-lock pick can see (the pre-gate resolution never could).
    _gen_fixture
    rm -rf "$GENGD/romp-run-"*
    printf '#!/bin/sh\necho INSTALL_RAN\nexit 0\n' > "$GENFIX/install.sh"
    git -C "$GENFIX" add install.sh
    git -C "$GENFIX" -c user.email=t@t -c user.name=t commit -qm heal-pick
    GENH8="$(git -C "$GENFIX" rev-parse --short=8 HEAD)"
    printf '%s' "$GENH8" > "$GENGD/romp-install-failed"
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 0 ]
    [[ "$output" == *"LIVE_CHECKOUT=$GENFIX"* ]]      # the gen the GATE just built is what ran
    [ ! -e "$GENGD/romp-install-failed" ]
}

@test "romp-serve: an armed marker with an UNREADABLE HEAD refuses to start (fail closed, r48)" {
    _gen_fixture
    printf '%s\n' "$GENFULL" > "$GENGD/romp-restart-needed"
    # a symbolic ref to a branch that does not exist: --absolute-git-dir still resolves (the
    # gate reaches its marker leg) but rev-parse HEAD fails — the unreadable-HEAD shape
    printf 'ref: refs/heads/nonexistent\n' > "$GENGD/HEAD"
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 70 ]
    [[ "$output" != *KERNEL_RAN* ]]
    [[ "$output" == *"HEAD is unreadable"* ]]
}

@test "romp-serve: the gate heal BUILDS the runtime generation before spending the latch" {
    # the r47 verification: every heal leg spent the latch on install alone — one failed
    # gen_build permanently downgraded a signed release to live-byte serving (serve resolves
    # romp-run-<sha8> per spawn and nothing would ever rebuild it)
    _latch_fixture
    printf '%s' "$CUR" > "$GD/romp-install-failed"
    run "$FIX/bin/romp-serve"
    [ "$status" -eq 0 ]
    [ -x "$GD/romp-run-$CUR/bin/romp-kernel" ]
    [ ! -e "$GD/romp-install-failed" ]
}

@test "romp-serve: a heal whose generation cannot be built keeps the latch armed (exit 70)" {
    # the commit lacks an executable bin/romp-kernel, so gen_build's content check refuses —
    # install PASSES yet the latch must survive for the retry, and no generation publishes
    _latch_fixture
    git -C "$FIX" rm -q --cached bin/romp-kernel
    git -C "$FIX" -c user.email=t@t -c user.name=t commit -qm no-kernel
    CUR="$(git -C "$FIX" rev-parse --short=8 HEAD | head -c 8)"
    printf '%s' "$CUR" > "$GD/romp-install-failed"
    run "$FIX/bin/romp-serve"
    [ "$status" -eq 70 ]
    [[ "$output" == *INSTALL_RAN* ]]
    [[ "$output" == *"runtime generation"* ]]
    [ -s "$GD/romp-install-failed" ]
    [ ! -d "$GD/romp-run-$CUR" ]
}

@test "romp-serve: a latch naming some OTHER commit is moot — cleared, kernel starts, no install" {
    _latch_fixture
    touch "$FIX/.install-broken"                        # would fail IF it ran — it must not
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

@test "romp-serve: an EXISTING latch that cannot be read refuses — unknown is not absent" {
    _latch_fixture
    printf '%s' "$CUR" > "$GD/romp-install-failed"
    chmod 000 "$GD/romp-install-failed"
    run "$FIX/bin/romp-serve"
    chmod 644 "$GD/romp-install-failed"
    [ "$status" -eq 70 ]
    [[ "$output" != *KERNEL_RAN* ]]
}

# ── runtime-generation resolution (the v1.3.18 audit's P1, per-spawn per the r46 verification) ──
# When a durable per-commit generation (<gitdir>/romp-run-<sha8>, built by the update
# transactions) matches the checkout's CURRENT HEAD, serve execs ITS kernel — the verified
# commit's exact bytes — with ROMP_CHECKOUT pointing state/git/dist work at the real checkout.
# A dirty checkout, or no generation for HEAD, runs the checkout's own kernel exactly as before.
# Provenance for every test in this section: the r46 re-verify — reverting the resolution kept
# every suite green, so these EXECUTE it. NOTE: setup() exports ROMP_KERNEL_BIN (the classic
# stub seam), which bypasses this whole block, so each test here unsets or overrides it.

_gen_fixture() {
    unset ROMP_KERNEL_BIN               # setup()'s seam would win over the resolution
    unset ROMP_CHECKOUT                 # never inherit one from the invoking environment
    GENFIX="$TEST_DIR/genfix"
    mkdir -p "$GENFIX/bin"
    # the checkout's OWN kernel (committed, so the checkout is clean per `git status --porcelain`)
    cat > "$GENFIX/bin/romp-kernel" << 'STUB'
#!/usr/bin/env bash
echo "LIVE_KERNEL_RAN"
echo "LIVE_CHECKOUT=${ROMP_CHECKOUT:-<unset>}"
STUB
    chmod +x "$GENFIX/bin/romp-kernel"
    # the committed serve stub: gen_build's validated content check requires kernel AND serve
    # in the archived tree (the v1.3.20 audit's P2)
    printf '#!/usr/bin/env bash\nexit 0\n' > "$GENFIX/bin/romp-serve"
    chmod +x "$GENFIX/bin/romp-serve"
    printf '#!/bin/sh\nexit 0\n' > "$GENFIX/install.sh"
    chmod +x "$GENFIX/install.sh"
    echo tracked > "$GENFIX/tracked.txt"    # the file the dirty cases edit
    git -C "$GENFIX" init -q -b main
    git -C "$GENFIX" add -A
    git -C "$GENFIX" -c user.email=t@t -c user.name=t commit -qm x
    GENGD="$(git -C "$GENFIX" rev-parse --absolute-git-dir)"
    GENH8="$(git -C "$GENFIX" rev-parse --short=8 HEAD)"
    GENFULL="$(git -C "$GENFIX" rev-parse HEAD)"
    # the generation for HEAD, inside the git dir (invisible to `git status`) — carrying the
    # .romp-gen full-sha manifest and executable serve the validated resolution requires (the
    # v1.3.20 audit's P2: a manifest-less or empty directory must never be selected)
    mkdir -p "$GENGD/romp-run-$GENH8/bin"
    cat > "$GENGD/romp-run-$GENH8/bin/romp-kernel" << 'STUB'
#!/usr/bin/env bash
echo "GEN_KERNEL_RAN"
echo "GEN_CHECKOUT=${ROMP_CHECKOUT:-<unset>}"
STUB
    chmod +x "$GENGD/romp-run-$GENH8/bin/romp-kernel"
    printf '#!/usr/bin/env bash\nexit 0\n' > "$GENGD/romp-run-$GENH8/bin/romp-serve"
    chmod +x "$GENGD/romp-run-$GENH8/bin/romp-serve"
    printf '%s\n' "$GENFULL" > "$GENGD/romp-run-$GENH8/.romp-gen"
    # point the REAL bin/romp-serve at the fixture checkout (the updater's snapshot seam)
    export ROMP_SERVE_ROOT="$GENFIX"
}

@test "romp-serve: a manifest-less generation is never selected — the live kernel runs (validated bless)" {
    # the v1.3.20 audit's P2, executed at the spawn leg: an EMPTY or torn generation directory
    # used to be selected by bare existence and exec'd forever
    _gen_fixture
    rm -f "$GENGD/romp-run-$GENH8/.romp-gen"
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 0 ]
    [[ "$output" == *"LIVE_KERNEL_RAN"* ]]
    [[ "$output" != *"GEN_KERNEL_RAN"* ]]
}

@test "romp-serve: an armed update marker forces the VERIFIED generation over a dirty tree" {
    # the v1.3.20 audit's P1.2, executed: one tracked edit landing between an update and its
    # restart switched the freshly signed release to mutable live bytes — while the marker
    # names HEAD, the generation runs, edits or not
    _gen_fixture
    echo change >> "$GENFIX/tracked.txt"
    printf '%s\n' "$GENFULL" > "$GENGD/romp-restart-needed"
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 0 ]
    [[ "$output" == *"GEN_KERNEL_RAN"* ]]
    [[ "$output" != *"LIVE_KERNEL_RAN"* ]]
    [[ "$output" == *"tracked edits in the checkout are ignored"* ]]
}

@test "romp-serve: an armed update marker BUILDS the missing generation before starting (N−1 backfill)" {
    # the v1.3.20 audit's P1.1 belt: an old updater that spent the latch without building a
    # generation leaves only the marker — the first spawn builds it from the committed tree
    # and starts the verified bytes, never live files
    _gen_fixture
    rm -rf "$GENGD/romp-run-$GENH8"
    printf '%s\n' "$GENFULL" > "$GENGD/romp-restart-needed"
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 0 ]
    [ -x "$GENGD/romp-run-$GENH8/bin/romp-kernel" ]
    [ "$(cat "$GENGD/romp-run-$GENH8/.romp-gen")" = "$GENFULL" ]
    # the committed kernel stub prints LIVE_* text, but a generation launch exports
    # ROMP_CHECKOUT — that is the tell that the built generation's bytes ran
    [[ "$output" == *"LIVE_CHECKOUT=$GENFIX"* ]]
}

@test "romp-serve: an armed update marker with an unbuildable generation refuses to start (exit 70)" {
    # fail closed: under the marker, live bytes are never an acceptable fallback
    _gen_fixture
    git -C "$GENFIX" rm -q --cached bin/romp-serve
    git -C "$GENFIX" -c user.email=t@t -c user.name=t commit -qm no-serve
    GENFULL="$(git -C "$GENFIX" rev-parse HEAD)"
    GENH8="$(git -C "$GENFIX" rev-parse --short=8 HEAD)"
    rm -rf "$GENGD/romp-run-"*
    printf '%s\n' "$GENFULL" > "$GENGD/romp-restart-needed"
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 70 ]
    [[ "$output" != *"KERNEL_RAN"* ]]
    [[ "$output" == *"refusing to start live bytes under the update marker"* ]]
}

@test "romp-serve: an armed update marker REBUILDS an empty generation directory (validated bless)" {
    # the v1.3.20 audit's P2 executed end-to-end: the full transaction used to report success
    # and spend the latch on an EMPTY romp-run directory
    _gen_fixture
    rm -rf "$GENGD/romp-run-$GENH8"
    mkdir -p "$GENGD/romp-run-$GENH8"                  # empty: no manifest, no binaries
    printf '%s\n' "$GENFULL" > "$GENGD/romp-restart-needed"
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 0 ]
    [ -x "$GENGD/romp-run-$GENH8/bin/romp-kernel" ]
    [ "$(cat "$GENGD/romp-run-$GENH8/.romp-gen")" = "$GENFULL" ]
    [[ "$output" == *"LIVE_CHECKOUT=$GENFIX"* ]]
}

@test "romp-serve: a CLEAN checkout with a generation for HEAD runs the GENERATION kernel, ROMP_CHECKOUT exported" {
    # the r46 re-verify: reverting the resolution kept every suite green — this executes it
    _gen_fixture
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 0 ]
    [[ "$output" == *"GEN_KERNEL_RAN"* ]]
    [[ "$output" == *"GEN_CHECKOUT=$GENFIX"* ]]      # state/git/dist stay on the real checkout
    [[ "$output" != *"LIVE_KERNEL_RAN"* ]]
}

@test "romp-serve: a DIRTY checkout skips the generation — the live kernel runs, no ROMP_CHECKOUT" {
    # the r46 re-verify (reverting kept every suite green): the generation silently shadowed a
    # developer's uncommitted kernel edits — the generation is the verified COMMIT, and edits
    # mean the commit is not what the user is running
    _gen_fixture
    echo change >> "$GENFIX/tracked.txt"
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 0 ]
    [[ "$output" == *"LIVE_KERNEL_RAN"* ]]
    [[ "$output" == *"LIVE_CHECKOUT=<unset>"* ]]
    [[ "$output" != *"GEN_KERNEL_RAN"* ]]
}

@test "romp-serve: the live leg UNSETS an inherited ROMP_CHECKOUT (never attribute live bytes to a generation)" {
    # the r46 re-verify (reverting kept every suite green): a leaked value made _kernel_sha
    # attribute live bytes to a generation name
    _gen_fixture
    echo change >> "$GENFIX/tracked.txt"             # dirty → the live leg
    ROMP_CHECKOUT=/stale/inherited run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 0 ]
    [[ "$output" == *"LIVE_CHECKOUT=<unset>"* ]]
}

@test "romp-serve: a STALE generation pin self-heals to per-spawn resolution instead of exit 1" {
    # the r46 re-verify (reverting kept every suite green): managers spawned by the v1.3.18
    # env-pin wrapper carry ROMP_KERNEL_BIN for life; once the pinned generation was pruned
    # that seam made every respawn exit 1 forever — a permanent crash loop
    _gen_fixture
    ROMP_KERNEL_BIN="$GENGD/romp-run-deadbeef/bin/romp-kernel" run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 0 ]
    [[ "$output" == *"GEN_KERNEL_RAN"* ]]            # healed into HEAD's own generation
    [[ "$output" == *"GEN_CHECKOUT=$GENFIX"* ]]
}

@test "romp-serve: an explicit NON-generation ROMP_KERNEL_BIN still wins over a live generation" {
    # the r46 re-verify (reverting kept every suite green): only pins INTO a romp-run-* dir are
    # second-guessed; the explicit test/dev seam to any other path stays unconditional
    _gen_fixture
    cat > "$TEST_DIR/explicit-kernel" << 'STUB'
#!/usr/bin/env bash
echo "EXPLICIT_KERNEL_RAN"
STUB
    chmod +x "$TEST_DIR/explicit-kernel"
    ROMP_KERNEL_BIN="$TEST_DIR/explicit-kernel" run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 0 ]
    [[ "$output" == *"EXPLICIT_KERNEL_RAN"* ]]
    [[ "$output" != *"GEN_KERNEL_RAN"* ]]
}

@test "romp-serve: a MISSING non-generation ROMP_KERNEL_BIN fails loudly — never silently self-healed" {
    # the r46 re-verify (reverting kept every suite green): the stale-pin second-guess is scoped
    # to romp-run-* paths; a broken explicit seam must surface, not fall through to other bytes
    _gen_fixture
    ROMP_KERNEL_BIN="$TEST_DIR/no-such-kernel" run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 1 ]
    [[ "$output" == *"kernel not found"* ]]
    [[ "$output" != *"GEN_KERNEL_RAN"* ]]
    [[ "$output" != *"LIVE_KERNEL_RAN"* ]]
}

@test "romp-serve: INSIDE the install transaction (live marker) the gate stands down" {
    # gating against our own transaction deadlocked the fresh install's dashboard-link poll
    # (the adversarial review, 2026-08-18); the marker counts only while its pid is alive
    _latch_fixture
    python3 - "$GD/romp-update.lock" <<'HOLDPY' &
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX)
time.sleep(60)
HOLDPY
    HOLDER=$!
    sleep 1
    ROMP_INSIDE_UPDATE_TXN="$HOLDER" run "$FIX/bin/romp-serve"
    st_live=$status out_live=$output
    ROMP_GATE_LOCK_WAIT=1 ROMP_INSIDE_UPDATE_TXN="999999" run "$FIX/bin/romp-serve"
    st_stale=$status
    kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null || true
    [ "$st_live" -eq 0 ]
    [[ "$out_live" == *KERNEL_RAN* ]]
    [ "$st_stale" -eq 70 ]      # a DEAD holder's marker is stale: the gate is back
}

@test "romp-serve: a STALE generation pin over a generation-less HEAD heals to the LIVE kernel, never exit 1" {
    # the r46 coverage pass: KERNEL was computed from the pin BEFORE the stale-pin unset, so a
    # pre-r46 manager's lifetime pin crash-looped serve on a pruned/generation-less checkout.
    # The heal must land on the LIVE kernel when no generation matches HEAD.
    _gen_fixture
    GD="$(git -C "$GENFIX" rev-parse --absolute-git-dir)"
    rm -rf "$GD/romp-run-"*                     # no generation for HEAD at all
    export ROMP_KERNEL_BIN="$GD/romp-run-deadbeef/bin/romp-kernel"
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 0 ]
    [[ "$output" == *LIVE_KERNEL_RAN* ]]
    [[ "$output" == *"LIVE_CHECKOUT=<unset>"* ]]
}

# ── the exec-revalidated launch pick (the v1.3.22 audit's P1.2 + P3.9) ─────────────────────────
# The gate picks gen-vs-live under the update lock, but bash APPLIES the pick after Python exits
# — the lock was gone, and an update landing in the window launched the OLD pick (an executed
# A→B schedule booted A while HEAD and the marker named B). The applier now hard-requires a
# well-formed pick, re-takes the lock, and revalidates HEAD/marker immediately before exec; the
# pick tempfile dies with the process on every path (the EXIT trap). These tests INJECT picks by
# shadowing python3 (the gate's interpreter), so the applier's behavior is what executes.

_pick_shim() {   # $1 = the pick line the fake gate writes ("" = write nothing)
    # Shadows the GATE's interpreter only: the exec-time revalidation also runs `python3 -c`
    # (the fcntl lock on the inherited fd — the r50 verification round replaced the flock(1)
    # binary, absent on macOS), and THAT invocation must stay real or the held-lock tests
    # would fake-acquire. -c delegates to the true python3; the bare-stdin gate call fakes.
    mkdir -p "$TEST_DIR/pathbin"
    _REALPY="$(command -v python3)"
    {
        printf '#!/usr/bin/env bash\n'
        printf 'if [ "$1" = "-c" ]; then exec %q "$@"; fi\n' "$_REALPY"
        if [ -n "$1" ]; then
            printf 'printf "%%s\\n" %q > "$ROMP_GATE_PICK"\n' "$1"
        fi
        printf 'exit 0\n'
    } > "$TEST_DIR/pathbin/python3"
    chmod +x "$TEST_DIR/pathbin/python3"
    export PATH="$TEST_DIR/pathbin:$PATH"
    export ROMP_REVAL_LOCK_WAIT=1     # the bounded sibling wait, shrunk so refusal tests
    #                                   don't idle 15s each
}

@test "romp-serve: a MISSING pick after a successful gate refuses — never the live default" {
    _gen_fixture
    _pick_shim ""                       # the gate 'succeeded' but persisted no pick
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 70 ]
    [[ "$output" == *"launch pick is missing or malformed"* ]]
    [[ "$output" != *KERNEL_RAN* ]]
}

@test "romp-serve: a MALFORMED pick refuses — tampering or failure, not a fallback" {
    _gen_fixture
    _pick_shim "bogus nonsense"
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 70 ]
    [[ "$output" == *"launch pick is missing or malformed"* ]]
    [[ "$output" != *KERNEL_RAN* ]]
}

@test "romp-serve: a WELL-FORMED gen pick still applies — the injection seam proves the applier" {
    _gen_fixture
    _pick_shim "gen $GENGD/romp-run-$GENH8"
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 0 ]
    [[ "$output" == *"GEN_KERNEL_RAN"* ]]
    [[ "$output" == *"GEN_CHECKOUT=$GENFIX"* ]]
}

@test "romp-serve: HEAD moving past the gate's gen pick refuses at exec time (the A->B schedule)" {
    # the v1.3.22 audit's P1.2, executed: the pick named generation A; before exec the checkout
    # moved to B — launching A would boot the superseded build as if it were the update
    _gen_fixture
    _pick_shim "gen $GENGD/romp-run-deadbeef"    # a pick for a HEAD this checkout no longer has
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 70 ]
    [[ "$output" == *"HEAD moved past the gate's pick"* ]]
    [[ "$output" != *KERNEL_RAN* ]]
}

@test "romp-serve: a live pick under a NOW-ARMED update marker refuses (respawn re-picks)" {
    # the marker armed between the gate's pick and the exec: live bytes would boot while the
    # marker names HEAD — exactly the forced-generation rule the gate enforces, at exec time
    _gen_fixture
    _pick_shim "live"
    printf '%s\n' "$GENFULL" > "$GENGD/romp-restart-needed"
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 70 ]
    [[ "$output" == *"update marker armed after the gate's live pick"* ]]
    [[ "$output" != *KERNEL_RAN* ]]
}

@test "romp-serve: a HELD update lock at exec time refuses the stale pick" {
    # the lock holder is a background python (fcntl) — the flock(1) BINARY does not exist on
    # stock macOS, where this suite also runs (the r50 verification round's second P1)
    _gen_fixture
    _pick_shim "gen $GENGD/romp-run-$GENH8"
    # the holder runs the REAL interpreter — the shim on PATH would eat this invocation
    "$_REALPY" - "$GENGD/romp-update.lock" <<'HOLDPY' &
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX)
time.sleep(60)
HOLDPY
    HOLDER=$!
    sleep 1
    run "$ROMP_SERVE" --port 9999
    kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null || true
    [ "$status" -eq 70 ]
    [[ "$output" == *"an update is mid-flight"* ]]
    [[ "$output" != *KERNEL_RAN* ]]
}

@test "romp-serve: a refused boot leaves NO pick tempfile behind (the EXIT trap, P3.9)" {
    _gen_fixture
    export TMPDIR="$TEST_DIR/picktmp"
    mkdir -p "$TMPDIR"
    _pick_shim "bogus"
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 70 ]
    run bash -c "ls \"$TMPDIR\"/romp-serve-pick.* 2>/dev/null | wc -l"
    [ "$output" -eq 0 ]
}

@test "romp-serve: INSIDE the transaction the exec revalidation stands down on the PRODUCTION path" {
    # the r50 verification round's P1, reproduced there: the txn holds the update lock across
    # install -> manager -> serve; the gate bypasses (pid-alive ROMP_INSIDE_UPDATE_TXN) but the
    # new revalidation re-took the lock with no bypass and refused every in-transaction boot
    # (exit 70). This drives the REAL gate (no python shim) with NO ROMP_KERNEL_BIN — the
    # pinned txn test in the latch section rides the seam, which skips the whole applier.
    _gen_fixture
    python3 - "$GENGD/romp-update.lock" <<'HOLDPY' &
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX)
time.sleep(60)
HOLDPY
    HOLDER=$!
    sleep 1
    ROMP_INSIDE_UPDATE_TXN=$$ run "$ROMP_SERVE" --port 9999
    kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null || true
    [ "$status" -eq 0 ]
    [[ "$output" == *"GEN_KERNEL_RAN"* ]]
}

@test "romp-serve: a DEAD txn pid gets no bypass — the held lock still refuses at exec time" {
    _gen_fixture
    _pick_shim "gen $GENGD/romp-run-$GENH8"
    # the holder runs the REAL interpreter — the shim on PATH would eat this invocation
    "$_REALPY" - "$GENGD/romp-update.lock" <<'HOLDPY' &
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX)
time.sleep(60)
HOLDPY
    HOLDER=$!
    sleep 1
    ROMP_INSIDE_UPDATE_TXN=99999999 run "$ROMP_SERVE" --port 9999
    kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null || true
    [ "$status" -eq 70 ]
    [[ "$output" == *"an update is mid-flight"* ]]
}

@test "romp-serve: a NON-GIT checkout's gate writes an explicit live pick — boot proceeds" {
    # the r50 close of the pick grammar: the not-a-git exit used to leave the pick EMPTY, which
    # the hard-required applier would refuse — the explicit 'live' says what the gate decided
    unset ROMP_KERNEL_BIN ROMP_CHECKOUT
    NOGIT="$TEST_DIR/nogit"
    mkdir -p "$NOGIT/bin"
    printf '#!/usr/bin/env bash\necho "LIVE_KERNEL_RAN"\n' > "$NOGIT/bin/romp-kernel"
    chmod +x "$NOGIT/bin/romp-kernel"
    export ROMP_SERVE_ROOT="$NOGIT"
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 0 ]
    [[ "$output" == *"LIVE_KERNEL_RAN"* ]]
}

@test "romp-serve: the exec revalidation needs no flock(1) binary (stock macOS)" {
    # the r50 verification round's second P1 — and its wave-3 revert detector: the Linux CI
    # runners HAVE flock(1), so a revert to `flock -n 9` stayed green everywhere it runs.
    # A PATH shim that answers 127 (command not found) makes the reverted code red on Linux.
    _gen_fixture
    _pick_shim "gen $GENGD/romp-run-$GENH8"
    printf '#!/usr/bin/env bash\nexit 127\n' > "$TEST_DIR/pathbin/flock"
    chmod +x "$TEST_DIR/pathbin/flock"
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 0 ]
    [[ "$output" == *"GEN_KERNEL_RAN"* ]]
}

@test "romp-serve: a READ-ONLY git dir boots locklessly — the gate's rule, at the revalidation too" {
    # the r50 verification round, wave 3: the gate explicitly supports an unwritable git dir
    # (no lock openable => no update can be mid-flight) and proceeded locklessly — then the
    # wave-2 revalidation exit-70'd on the same open, refusing EVERY boot of that checkout
    _gen_fixture
    _pick_shim "gen $GENGD/romp-run-$GENH8"
    chmod 555 "$GENGD"
    run "$ROMP_SERVE" --port 9999
    chmod 755 "$GENGD"
    [ "$status" -eq 0 ]
    [[ "$output" == *"GEN_KERNEL_RAN"* ]]
}

# ── the v1.3.23 audit: the pick channel fails closed; the lock holds THROUGH exec ──────────────

@test "romp-serve: a GIT checkout that cannot mint the pick channel refuses (exit 70, P1.2)" {
    # the v1.3.23 audit's P1.2, executed: with TMPDIR unwritable the gate could refuse only
    # non-live picks — a live verdict exited 0 with nothing to deliver, bash skipped the strict
    # applier AND the exec revalidation, and an update landing after the gate had armed its
    # marker still saw unverified mutable live bytes execute
    _gen_fixture
    export TMPDIR="$TEST_DIR/no-such-tmp"        # does not exist: mktemp fails
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 70 ]
    [[ "$output" == *"cannot create the launch-pick channel"* ]]
    [[ "$output" != *KERNEL_RAN* ]]
}

@test "romp-serve: a NON-GIT checkout without the pick channel still boots (no updates to race)" {
    # the P1.2 refusal is scoped to git checkouts: a non-git install hosts no updates, so
    # there is no pick to protect — a broken TMPDIR must not brick it
    unset ROMP_KERNEL_BIN ROMP_CHECKOUT
    NOGIT="$TEST_DIR/nogit-pickless"
    mkdir -p "$NOGIT/bin"
    printf '#!/usr/bin/env bash\necho "LIVE_KERNEL_RAN"\n' > "$NOGIT/bin/romp-kernel"
    chmod +x "$NOGIT/bin/romp-kernel"
    export ROMP_SERVE_ROOT="$NOGIT"
    export TMPDIR="$TEST_DIR/no-such-tmp"
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 0 ]
    [[ "$output" == *"LIVE_KERNEL_RAN"* ]]
}

@test "romp-serve: the revalidation lock does NOT ride into the kernel (CLOEXEC at exec, P1.1)" {
    # the v1.3.23 audit's P1.1's counter-worry, pinned: the lock now holds THROUGH os.execv
    # and must release exactly there — a kernel that inherited a held update lock would block
    # every later update transaction forever
    _gen_fixture
    cat > "$TEST_DIR/lock-probe.py" << 'PROBE'
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    print("LOCK_FREE_IN_KERNEL")
except OSError:
    print("LOCK_STILL_HELD")
PROBE
    cat > "$GENGD/romp-run-$GENH8/bin/romp-kernel" << STUB
#!/usr/bin/env bash
echo "GEN_KERNEL_RAN"
exec python3 "$TEST_DIR/lock-probe.py" "$GENGD/romp-update.lock"
STUB
    chmod +x "$GENGD/romp-run-$GENH8/bin/romp-kernel"
    run "$ROMP_SERVE" --port 9999
    [ "$status" -eq 0 ]
    [[ "$output" == *"GEN_KERNEL_RAN"* ]]
    [[ "$output" == *"LOCK_FREE_IN_KERNEL"* ]]
}

@test "romp-serve: the revalidation and the exec live in ONE python — no early lock release (P1.1)" {
    # revert detector (the flock(1) test's pattern): the fd-9 shape closed the lock before
    # exec, and an update landing in that gap deterministically booted the superseded pick.
    # The checks and the exec must share one process so the flock is still held at os.execv.
    run grep -c '9>&-' "$ROMP_SERVE"
    [ "$output" = "0" ]
    python3 - "$ROMP_SERVE" << 'CHECK'
import re, sys
src = open(sys.argv[1]).read()
m = re.search(r"exec python3 -c '(.*?)' \"\$ROMP_DIR\"", src, re.S)
assert m, "the one-python launcher is gone"
body = m.group(1)
assert "fcntl.flock" in body and "os.execv" in body, "lock and exec are not in ONE process"
CHECK
}
