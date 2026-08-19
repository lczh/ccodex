#!/usr/bin/env bats

# ./bootstrap.sh — the one-line installer:
#   curl -fsSL .../bootstrap.sh | bash
# Hermetic: HOME points at a temp dir and the "origin" is a local fixture repo whose install.sh is
# a stub, so the real install.sh never runs (it would symlink this machine's ~/.claude at the temp
# clone and break Claude Code when the temp dir is removed).

REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

setup() {
    TEST_DIR="$(mktemp -d)"
    export HOME="$TEST_DIR/home"
    mkdir -p "$HOME"
    export SHELL=/bin/zsh
    export ROMP_REPO="$TEST_DIR/origin"
    # Every git WRITE in this suite (fixture commits, the local-only signed tag crafted inside the
    # bootstrapped clone) needs a committer identity. The scratch HOME has no gitconfig, and CI
    # runners can't auto-detect one — "Committer identity unknown", exit 128 — while dev boxes
    # usually can, which is exactly how this suite ran green locally and red on CI for three
    # releases (2026-08-16). Env vars, so no fixture repo needs per-repo config.
    export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.invalid
    export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.invalid

    # Use a per-test SSH signing key and trust root. SSH-signed tags are understood by GitHub,
    # need no ambient GPG agent/keyring, and keep this suite completely hermetic.
    ssh-keygen -q -t ed25519 -N '' -f "$TEST_DIR/release-key"
    printf 'release@example.invalid %s\n' "$(cat "$TEST_DIR/release-key.pub")" \
        > "$TEST_DIR/allowed-signers"
    export ROMP_RELEASE_ALLOWED_SIGNERS="$TEST_DIR/allowed-signers"

    # A fake romp origin: enough structure for bootstrap's clone check, plus a
    # non-release tag alongside two releases so tag selection is exercised.
    mkdir -p "$ROMP_REPO/kernel"
    printf '#!/usr/bin/env bash\necho STUB_INSTALL_RAN\n' > "$ROMP_REPO/install.sh"
    chmod +x "$ROMP_REPO/install.sh"
    touch "$ROMP_REPO/kernel/.keep"
    git -C "$ROMP_REPO" init -q -b main .
    # The fixture takes several commits+tags per test; git's background auto-gc can then REPACK the
    # fixture while bootstrap's clone is copying its packs — the pack file vanishes mid-copy and the
    # clone dies with ENOENT on a .tmp-*-pack-*.rev (a 2026-08-17 CI runner, green on rerun). The
    # fixture never needs gc; forbid it outright so the clone can't race it.
    git -C "$ROMP_REPO" config gc.auto 0
    git -C "$ROMP_REPO" config gc.autoDetach false
    git -C "$ROMP_REPO" config maintenance.auto false
    git -C "$ROMP_REPO" config gpg.format ssh
    git -C "$ROMP_REPO" config user.signingKey "$TEST_DIR/release-key"
    git -C "$ROMP_REPO" config user.email release@example.invalid
    git -C "$ROMP_REPO" config user.name 'Release Fixture'
    git -C "$ROMP_REPO" config gpg.ssh.allowedSignersFile "$TEST_DIR/allowed-signers"
    git -C "$ROMP_REPO" add -A
    git -C "$ROMP_REPO" -c user.email=t@t -c user.name=t commit -qm init
    git -C "$ROMP_REPO" tag not-a-release
    git -C "$ROMP_REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m r1
    git -C "$ROMP_REPO" tag -s v0.1.0 -m v0.1.0
    git -C "$ROMP_REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m r2
    git -C "$ROMP_REPO" tag -s v0.2.0 -m v0.2.0
    git -C "$ROMP_REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m post-release
}

teardown() { rm -rf "$TEST_DIR"; }

@test "bootstrap.sh: clones, checks out the newest RELEASE tag, installs, sets PATH" {
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *STUB_INSTALL_RAN* ]]
    # v0.2.0, not the newer untagged commit and not the non-release tag.
    [ "$(git -C "$HOME/romp" describe --tags)" = "v0.2.0" ]
    grep -qF "$HOME/romp/bin" "$HOME/.zshrc"
}

@test "bootstrap.sh: verifies with Git's configured trust when no allowed-signers env is set" {
    git config --global gpg.ssh.allowedSignersFile "$TEST_DIR/allowed-signers"
    unset ROMP_RELEASE_ALLOWED_SIGNERS
    GIT_TRACE2_EVENT="$TEST_DIR/git-trace" ROMP_DIR="$HOME/romp" run /bin/bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *STUB_INSTALL_RAN* ]]
    [ "$(git -C "$HOME/romp" describe --tags)" = "v0.2.0" ]
    grep -q '"gpg.minTrustLevel=fully","verify-tag","v0.2.0"' "$TEST_DIR/git-trace"
}

@test "bootstrap.sh: re-running updates in place and does not duplicate the PATH line" {
    ROMP_DIR="$HOME/romp" bash "$REPO_ROOT/bootstrap.sh"
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Updating the romp clone"* ]]
    [ "$(grep -cF "$HOME/romp/bin" "$HOME/.zshrc")" = "1" ]
}

@test "bootstrap.sh: persists one-shot SSH trust in the clone for later updater verification" {
    ROMP_DIR="$HOME/romp" bash "$REPO_ROOT/bootstrap.sh"
    expected_signers="$(cd "$TEST_DIR" && pwd -P)/allowed-signers"
    [ "$(git -C "$HOME/romp" config --local --get gpg.ssh.allowedSignersFile)" = "$expected_signers" ]

    unset ROMP_RELEASE_ALLOWED_SIGNERS
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *STUB_INSTALL_RAN* ]]
}

@test "bootstrap.sh: ROMP_REF pins a branch instead of the newest release" {
    ROMP_DIR="$HOME/romp" ROMP_REF=main run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [ "$(git -C "$HOME/romp" rev-parse --abbrev-ref HEAD)" = "main" ]
}

@test "bootstrap.sh: refuses to hide a non-fast-forward branch update" {
    ROMP_DIR="$HOME/romp" ROMP_REF=main bash "$REPO_ROOT/bootstrap.sh"
    git -C "$HOME/romp" config user.email t@t
    git -C "$HOME/romp" config user.name t
    git -C "$HOME/romp" commit -q --allow-empty -m local
    git -C "$ROMP_REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m remote

    ROMP_DIR="$HOME/romp" ROMP_REF=main run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"cannot fast-forward"* ]]
    [[ "$output" != *STUB_INSTALL_RAN* ]]
}

@test "bootstrap.sh: refuses to reuse a checkout from a different origin" {
    ROMP_DIR="$HOME/romp" bash "$REPO_ROOT/bootstrap.sh"
    git -C "$HOME/romp" remote set-url origin "$TEST_DIR/not-the-requested-repo"

    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"different origin"* ]]
    [[ "$output" == *"expected: $ROMP_REPO"* ]]
    [[ "$output" != *STUB_INSTALL_RAN* ]]
}

@test "bootstrap.sh: refuses to fall back to unsigned main when no release is tagged" {
    git -C "$ROMP_REPO" tag -d v0.1.0 v0.2.0
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"no release tag is published"* ]]
    [[ "$output" == *"ROMP_REF=main"* ]]
    [[ "$output" != *STUB_INSTALL_RAN* ]]
}

@test "bootstrap.sh: rejects the newest unsigned release instead of installing an older signed tag" {
    git -C "$ROMP_REPO" tag -a v0.3.0 -m unsigned
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"does not have a valid signature"* ]]
    [[ "$output" != *STUB_INSTALL_RAN* ]]
    [ "$(git -C "$HOME/romp" rev-parse HEAD)" != "$(git -C "$ROMP_REPO" rev-parse v0.2.0^{})" ]
}

@test "bootstrap.sh: an unsigned release with NO trust root installs, with a loud note" {
    # Enforcement requires a configured trust root (env / ROMP_VERIFY_RELEASES / persisted config).
    # Mandatory-with-no-published-key bricked every friend install: releases were not signed and no
    # key was distributed anywhere, so there was nothing to trust (2026-08-14 review).
    git -C "$ROMP_REPO" tag -a v0.3.0 -m unsigned
    unset ROMP_RELEASE_ALLOWED_SIGNERS
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *"not signature-verified"* ]]
    [[ "$output" == *STUB_INSTALL_RAN* ]]
    [ "$(git -C "$HOME/romp" describe --tags)" = "v0.3.0" ]
}

@test "bootstrap.sh: rejects a release signed by a key outside the allowed-signers trust root" {
    ssh-keygen -q -t ed25519 -N '' -f "$TEST_DIR/untrusted-key"
    git -C "$ROMP_REPO" -c user.signingKey="$TEST_DIR/untrusted-key" \
        tag -s v0.3.0 -m untrusted
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"does not have a valid signature"* ]]
    [[ "$output" != *STUB_INSTALL_RAN* ]]
}

@test "bootstrap.sh: fails closed when the configured SSH trust file is missing" {
    ROMP_RELEASE_ALLOWED_SIGNERS="$TEST_DIR/missing" ROMP_DIR="$HOME/romp" \
        run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"not a readable regular file"* ]]
    [[ "$output" != *STUB_INSTALL_RAN* ]]
}

@test "bootstrap.sh: a verified tag wins over an unsigned remote branch with the same name" {
    git -C "$ROMP_REPO" branch v0.2.0 main
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [ "$(git -C "$HOME/romp" rev-parse HEAD)" = "$(git -C "$ROMP_REPO" rev-parse v0.2.0^{})" ]
    [ "$(git -C "$HOME/romp" rev-parse --abbrev-ref HEAD)" = "HEAD" ]
}

@test "bootstrap.sh: ignores a higher local-only signed tag when choosing the origin release" {
    ROMP_DIR="$HOME/romp" bash "$REPO_ROOT/bootstrap.sh"
    git -C "$HOME/romp" -c gpg.format=ssh -c user.signingKey="$TEST_DIR/release-key" \
        tag -s v999.0.0 -m local-only main

    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [ "$(git -C "$HOME/romp" rev-parse HEAD)" = "$(git -C "$ROMP_REPO" rev-parse v0.2.0^{})" ]
    [[ "$output" == *"Verifying release signature for v0.2.0"* ]]
    [[ "$output" != *"v999.0.0"* ]]
}

@test "bootstrap.sh: refuses a target directory that is not a romp clone, leaving it untouched" {
    mkdir -p "$HOME/mine" && echo keep > "$HOME/mine/important.txt"
    ROMP_DIR="$HOME/mine" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 1 ]
    [[ "$output" == *"not a romp clone"* ]]
    [ "$(cat "$HOME/mine/important.txt")" = "keep" ]
    [ ! -e "$HOME/mine/.git" ]
}

@test "bootstrap.sh: ROMP_NO_PATH leaves the shell rc alone" {
    ROMP_DIR="$HOME/romp" ROMP_NO_PATH=1 run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [ ! -f "$HOME/.zshrc" ]
}

@test "install.sh: refuses to run piped from curl instead of dangling every hook symlink" {
    # Piped, \$0 is "bash", so install.sh's ROMP_DIR would resolve to the caller's
    # cwd and `ln -s` would happily point ~/.claude at paths that do not exist.
    cd "$TEST_DIR"
    run bash -c "cat '$REPO_ROOT/install.sh' | bash"
    [ "$status" -eq 1 ]
    [[ "$output" == *"cannot be piped from curl"* ]]
    [[ "$output" == *bootstrap.sh* ]]
    [ ! -e "$HOME/.claude/hooks" ]
}

# ── the publishing remote ────────────────────────────────────────────────────
# CLAUDE.md's worktree rule says publish with `git push -u fork <branch>` and never to
# origin (upstream rulesets reject a direct push) — but a plain clone has only `origin`,
# so a fresh install could not follow the workflow the repo documents.

# ── where the clone lands ────────────────────────────────────────────────────

@test "bootstrap.sh: names the install directory and the knob, since it ignores cwd" {
    # It clones into $HOME regardless of where the one-liner is run from. Reasonable for a
    # `curl | bash`, surprising if unstated (the user asked whether it uses the cwd).
    cd "$TEST_DIR"
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Cloning romp into $HOME/romp"* ]]
    [[ "$output" == *"ROMP_DIR"* ]]
    [ ! -e "$TEST_DIR/romp" ]           # never the cwd
}

# ── the installer's blast radius ─────────────────────────────────────────────
# Installing romp is not contributing to it. bootstrap once derived <gh-user>/romp from
# whoever `gh` was logged in as and wired it as a git remote with remote.pushDefault — so an
# ordinary installer got a remote pointing at a repo they had never created, a `git push`
# aimed at it, and an unexplained line mid-install (the user 2026-07-27: don't assume the gh
# CLI, and that is beyond what an installer should configure anyway). These pin the scope so
# it cannot creep back.

@test "bootstrap.sh: never assumes or invokes the gh CLI" {
    # A `gh` that fails loudly if called at all. The installer must never reach for it: it is
    # not a romp dependency, and plenty of machines that run romp will never have it.
    cat > "$TEST_DIR/gh" <<'EOF'
#!/usr/bin/env bash
echo "bootstrap invoked gh" >> "$TEST_DIR/gh-was-called"
exit 1
EOF
    chmod +x "$TEST_DIR/gh"

    PATH="$TEST_DIR:$PATH" ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [ ! -f "$TEST_DIR/gh-was-called" ]
    ! grep -q 'gh ' "$REPO_ROOT/bootstrap.sh"
}

@test "bootstrap.sh: configures no git remotes or push defaults beyond the clone's own origin" {
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    # origin, and nothing else — no `fork`, no invented publishing target.
    [ "$(git -C "$HOME/romp" remote)" = "origin" ]
    [ -z "$(git -C "$HOME/romp" config --get remote.pushDefault || true)" ]
    [[ "$output" != *"Publishing remote"* ]]
}

@test "bootstrap.sh: a prerelease-suffixed tag never outranks the newest stable release" {
    # version sort ranks v9.9.9-rc.1 above every stable, and the kernel updater's _semver refuses
    # prerelease shapes — installing one would strand the machine on a release the updater can't
    # move past (the user 2026-08-16). Both pickers speak exactly vX.Y.Z.
    git -C "$ROMP_REPO" -c gpg.format=ssh -c user.signingKey="$TEST_DIR/release-key" \
        tag -s v9.9.9-rc.1 -m prerelease
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [ "$(git -C "$HOME/romp" describe --tags)" = "v0.2.0" ]
    [[ "$output" == *"Checking out v0.2.0"* ]]
}

@test "bootstrap.sh: a persisted trust root keeps enforcing when the env var is gone" {
    # the first run persists the signers path into the clone's git config; a later run without
    # the one-shot env var must NOT silently downgrade to warn-and-proceed (the user 2026-08-16)
    ROMP_DIR="$HOME/romp" bash "$REPO_ROOT/bootstrap.sh"
    [ -n "$(git -C "$HOME/romp" config --local --get gpg.ssh.allowedSignersFile)" ]
    git -C "$ROMP_REPO" tag -a v0.3.0 -m unsigned
    unset ROMP_RELEASE_ALLOWED_SIGNERS
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"does not have a valid signature"* ]]
    [[ "$output" != *STUB_INSTALL_RAN* ]]
}

@test "bootstrap.sh: a configured-but-EMPTY signers value still enforces (never warn-only)" {
    # rc-0-with-empty-value is a MISCONFIGURATION for verification to fail loudly against, not an
    # absent trust root: collapsing it into "no trust root" downgraded a hardened install to
    # warning-only (the user's audit, 2026-08-17). Only git's clean rc-1 "key absent" downgrades.
    ROMP_DIR="$HOME/romp" bash "$REPO_ROOT/bootstrap.sh"
    git -C "$HOME/romp" config --local gpg.ssh.allowedSignersFile ""
    git -C "$ROMP_REPO" tag -a v0.3.0 -m unsigned
    unset ROMP_RELEASE_ALLOWED_SIGNERS
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"does not have a valid signature"* ]]
    [[ "$output" != *STUB_INSTALL_RAN* ]]
}

@test "bootstrap.sh: a release install lands on the STABLE update channel" {
    # the channel is persisted separately from signature policy: the kernel's main-convergence
    # follows origin/main only on `dev`, and keying that off the trust root left default installs
    # silently tracking unsigned main (the user's audit, 2026-08-17)
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Update channel: stable"* ]]
    [ "$(cat "$(git -C "$HOME/romp" rev-parse --absolute-git-dir)/romp-update-channel")" = "stable" ]
}

@test "bootstrap.sh: ROMP_REF=main is the explicit dev opt-in, and a tag re-run flips back" {
    ROMP_DIR="$HOME/romp" ROMP_REF=main run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [ "$(cat "$(git -C "$HOME/romp" rev-parse --absolute-git-dir)/romp-update-channel")" = "dev" ]
    # re-bootstrapping onto a release moves the install back to stable — the channel follows
    # the last explicit choice, never a sticky accident
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [ "$(cat "$(git -C "$HOME/romp" rev-parse --absolute-git-dir)/romp-update-channel")" = "stable" ]
}

@test "bootstrap.sh: a feature branch or pinned ref is NOT the dev channel" {
    # dev means exactly the ROMP_REF=main opt-in: a branch or pinned-commit install is a
    # deliberate NON-main checkout, and recording it as dev would authorize converging it onto a
    # main it never asked to follow (the user's audit, 2026-08-17)
    git -C "$ROMP_REPO" branch feature-x
    ROMP_DIR="$HOME/romp" ROMP_REF=feature-x run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [ "$(cat "$(git -C "$HOME/romp" rev-parse --absolute-git-dir)/romp-update-channel")" = "stable" ]
}

@test "bootstrap.sh: a NOISY rc-1 trust probe enforces — only git's quiet 'absent' downgrades" {
    # rc 1 with stderr noise proves nothing about the trust root; reading it as 'absent'
    # downgraded verification to warn-only (the adversarial review, 2026-08-17). A git wrapper
    # injects noise into exactly the probe; everything else delegates to the real git.
    ROMP_DIR="$HOME/romp" bash "$REPO_ROOT/bootstrap.sh"
    git -C "$HOME/romp" config --local --unset gpg.ssh.allowedSignersFile
    git -C "$ROMP_REPO" tag -a v0.3.0 -m unsigned
    unset ROMP_RELEASE_ALLOWED_SIGNERS
    REAL_GIT="$(command -v git)"
    mkdir -p "$TEST_DIR/bin"
    cat > "$TEST_DIR/bin/git" <<WRAP
#!/bin/sh
case " \$* " in
  *' config --get gpg.ssh.allowedSignersFile '*|*' config --get gpg.ssh.allowedSignersFile') echo 'warning: config oddity' >&2; exit 1;;
esac
exec "$REAL_GIT" "\$@"
WRAP
    chmod +x "$TEST_DIR/bin/git"
    PATH="$TEST_DIR/bin:$PATH" ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"does not have a valid signature"* ]]
    [[ "$output" != *STUB_INSTALL_RAN* ]]
}

@test "bootstrap.sh: a failed install leaves the latch ARMED — the build is gated until it passes" {
    # the move+install is one locked transaction now: a re-run whose install.sh fails must leave
    # the durable intent for romp-serve's gate and the kernel's boot heal (the user's audit, 2026-08-18)
    ROMP_DIR="$HOME/romp" bash "$REPO_ROOT/bootstrap.sh"
    printf '#!/usr/bin/env bash\nexit 1\n' > "$ROMP_REPO/install.sh"
    git -C "$ROMP_REPO" -c user.email=t@t -c user.name=t commit -qam broken-install
    git -C "$ROMP_REPO" tag -s v0.3.0 -m v0.3.0
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"latch is armed"* ]]
    gd="$(git -C "$HOME/romp" rev-parse --absolute-git-dir)"
    [ "$(cat "$gd/romp-install-failed")" = "$(git -C "$HOME/romp" rev-parse --short=8 HEAD | head -c 8)" ]
}

@test "bootstrap.sh: a held update lock refuses the re-run instead of racing the updater" {
    ROMP_DIR="$HOME/romp" bash "$REPO_ROOT/bootstrap.sh"
    gd="$(git -C "$HOME/romp" rev-parse --absolute-git-dir)"
    python3 - "$gd/romp-update.lock" <<'HOLDPY' &
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX)
time.sleep(60)
HOLDPY
    HOLDER=$!
    sleep 1
    ROMP_TXN_LOCK_WAIT=1 ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null || true
    [ "$status" -ne 0 ]
    [[ "$output" == *"another update holds"* ]]
    [[ "$output" != *STUB_INSTALL_RAN* ]]
}

@test "bootstrap.sh: an unpersistable install intent refuses BEFORE anything moves (exit-5 leg)" {
    ROMP_DIR="$HOME/romp" bash "$REPO_ROOT/bootstrap.sh"
    gd="$(git -C "$HOME/romp" rev-parse --absolute-git-dir)"
    git -C "$ROMP_REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m r3
    git -C "$ROMP_REPO" tag -s v0.3.0 -m v0.3.0
    head_before="$(git -C "$HOME/romp" rev-parse HEAD)"
    mkdir "$gd/romp-install-failed.tmp"   # the latch's tmp path is a DIRECTORY: only the arm fails
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    rmdir "$gd/romp-install-failed.tmp"
    [ "$status" -ne 0 ]
    [[ "$output" == *"could not record the install intent"* ]]
    [ "$(git -C "$HOME/romp" rev-parse HEAD)" = "$head_before" ]
    [[ "$output" != *STUB_INSTALL_RAN* ]]
}

@test "bootstrap.sh: a successful transaction SPENDS the latch" {
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    gd="$(git -C "$HOME/romp" rev-parse --absolute-git-dir)"
    [ ! -e "$gd/romp-install-failed" ]
}

@test "bootstrap.sh: a failed move never erases a PRE-EXISTING armed latch" {
    # the unconditional remove destroyed the only record protecting a half-installed build when a
    # later re-run's move failed (the adversarial review, 2026-08-18, reproduced live)
    ROMP_DIR="$HOME/romp" bash "$REPO_ROOT/bootstrap.sh"
    gd="$(git -C "$HOME/romp" rev-parse --absolute-git-dir)"
    cur8="$(git -C "$HOME/romp" rev-parse --short=8 HEAD | head -c 8)"
    printf '%s' "$cur8" > "$gd/romp-install-failed"          # a half-installed HEAD's record
    git -C "$HOME/romp" config user.email t@t
    git -C "$HOME/romp" config user.name t
    git -C "$HOME/romp" checkout -q -b main 2>/dev/null || git -C "$HOME/romp" checkout -q main 2>/dev/null || true
    git -C "$HOME/romp" commit -q --allow-empty -m local
    git -C "$ROMP_REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m remote
    ROMP_DIR="$HOME/romp" ROMP_REF=main run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -ne 0 ]
    [ -s "$gd/romp-install-failed" ]   # SOME honest latch survives the refusal
}

@test "bootstrap.sh: a failing heal CARRIES the old record forward — no wedge, nothing orphaned" {
    # heal-ONLY wedged every path when the old install failed deterministically; arming by
    # overwrite orphaned the old record (the audits, 2026-08-18/19). The synthesis: the update
    # PROCEEDS, and the new arm carries BOTH shas — a crash before the move still protects the
    # old build, and a fixed install in the new commit is reachable.
    ROMP_DIR="$HOME/romp" bash "$REPO_ROOT/bootstrap.sh"
    gd="$(git -C "$HOME/romp" rev-parse --absolute-git-dir)"
    cur8="$(git -C "$HOME/romp" rev-parse --short=8 HEAD | head -c 8)"
    printf '%s' "$cur8" > "$gd/romp-install-failed"
    printf '#!/usr/bin/env bash\nexit 1\n' > "$HOME/romp/install.sh"   # broken at BOTH commits
    git -C "$ROMP_REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m r3
    git -C "$ROMP_REPO" tag -s v0.3.0 -m v0.3.0
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -ne 0 ]
    new8="$(git -C "$HOME/romp" rev-parse --short=8 HEAD | head -c 8)"
    [ "$new8" != "$cur8" ]                              # forward progress: HEAD reached the new tag
    [ "$(sed -n 1p "$gd/romp-install-failed")" = "$new8" ]   # the intent line
    [ "$(sed -n 2p "$gd/romp-install-failed")" = "$cur8" ]   # the CARRIED prior — never orphaned
    # and when install is FIXED, a re-run heals everything and spends the latch entirely
    printf '#!/usr/bin/env bash\necho STUB_INSTALL_RAN\n' > "$HOME/romp/install.sh"
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [ ! -e "$gd/romp-install-failed" ]
}

@test "bootstrap.sh: ancestry is re-decided UNDER the lock — a divergence landing during the wait refuses" {
    # the read-only pre-check raced a concurrent updater (the adversarial review, 2026-08-18);
    # choreography: hold the lock, diverge the local branch while bootstrap waits, release —
    # TXNPY's under-lock re-check must catch what the pre-check could not see
    ROMP_DIR="$HOME/romp" ROMP_REF=main bash "$REPO_ROOT/bootstrap.sh"
    gd="$(git -C "$HOME/romp" rev-parse --absolute-git-dir)"
    git -C "$ROMP_REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m remote
    ( python3 - "$gd/romp-update.lock" <<'HOLDPY'
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX)
time.sleep(4)
HOLDPY
    ) &
    HOLDER=$!
    sleep 1
    ( sleep 2
      git -C "$HOME/romp" config user.email t@t
      git -C "$HOME/romp" config user.name t
      git -C "$HOME/romp" commit -q --allow-empty -m diverge-during-wait ) &
    DIVERGER=$!
    ROMP_TXN_LOCK_WAIT=20 ROMP_DIR="$HOME/romp" ROMP_REF=main run bash "$REPO_ROOT/bootstrap.sh"
    wait "$HOLDER" 2>/dev/null || true
    wait "$DIVERGER" 2>/dev/null || true
    [ "$status" -ne 0 ]
    [[ "$output" == *"moved while waiting for the lock"* ]]
    [[ "$output" != *STUB_INSTALL_RAN* ]]
}

@test "bootstrap.sh: the transaction itself publishes the channel marker" {
    # the marker write lives in TXNPY, under the update lock (the user's audit, 2026-08-19: the
    # shell wrote it after the lock released, so two serial bootstraps could publish in reverse
    # order). Executed directly: the extracted transaction, run with a channel and no moves, must
    # leave the marker — no shell tail involved.
    sed -n "/<<'TXNPY'/,/^TXNPY\$/p" "$REPO_ROOT/bootstrap.sh" | sed '1d;$d' > "$TEST_DIR/txn.py"
    root="$TEST_DIR/clone"; mkdir -p "$root"
    git -C "$root" init -q -b main .
    printf '#!/usr/bin/env bash\nexit 0\n' > "$root/install.sh"
    git -C "$root" add -A
    git -C "$root" -c user.email=t@t -c user.name=t commit -qm init
    gd="$(git -C "$root" rev-parse --absolute-git-dir)"
    target="$(git -C "$root" rev-parse HEAD)"
    run python3 "$TEST_DIR/txn.py" "$root" "$gd" "$target" "-" "stable"
    [ "$status" -eq 0 ]
    [ "$(cat "$gd/romp-update-channel")" = "stable" ]
    [ ! -e "$gd/romp-install-failed" ]
}

@test "bootstrap.sh: an unstageable channel marker refuses BEFORE anything moves (rc 11)" {
    # the fallible step (writing the marker CONTENT) happens before the latch is armed and before
    # any move, where failing costs nothing: nothing armed, nothing moved, old marker still true
    sed -n "/<<'TXNPY'/,/^TXNPY\$/p" "$REPO_ROOT/bootstrap.sh" | sed '1d;$d' > "$TEST_DIR/txn.py"
    root="$TEST_DIR/clone"; mkdir -p "$root"
    git -C "$root" init -q -b main .
    printf '#!/usr/bin/env bash\nexit 0\n' > "$root/install.sh"
    git -C "$root" add -A
    git -C "$root" -c user.email=t@t -c user.name=t commit -qm init
    gd="$(git -C "$root" rev-parse --absolute-git-dir)"
    target="$(git -C "$root" rev-parse HEAD)"
    mkdir "$gd/romp-update-channel.tmp"    # open() on a directory fails → the txn must refuse
    run python3 "$TEST_DIR/txn.py" "$root" "$gd" "$target" "-" "stable"
    [ "$status" -eq 11 ]
    [ ! -e "$gd/romp-install-failed" ]     # nothing armed — nothing moved
    [ ! -e "$gd/romp-update-channel" ]
}

@test "bootstrap.sh: an unpublishable channel marker QUARANTINES the moved checkout (rc 12)" {
    # after the moves, publishing is one atomic rename; if even that fails, a single-line latch
    # matching HEAD would be auto-healed by marker-unaware healers (romp-serve's gate, the
    # kernel's boot heal) into a build wearing the OLD channel — a stable checkout following
    # unsigned main (the adversarial review, 2026-08-19, reproduced against both healers). The
    # stuck two-line form is what every reader refuses without a human.
    sed -n "/<<'TXNPY'/,/^TXNPY\$/p" "$REPO_ROOT/bootstrap.sh" | sed '1d;$d' > "$TEST_DIR/txn.py"
    root="$TEST_DIR/clone"; mkdir -p "$root"
    git -C "$root" init -q -b main .
    printf '#!/usr/bin/env bash\nexit 0\n' > "$root/install.sh"
    git -C "$root" add -A
    git -C "$root" -c user.email=t@t -c user.name=t commit -qm init
    gd="$(git -C "$root" rev-parse --absolute-git-dir)"
    target="$(git -C "$root" rev-parse HEAD)"
    mkdir "$gd/romp-update-channel"        # the FINAL name blocked: staging succeeds, publish fails
    run python3 "$TEST_DIR/txn.py" "$root" "$gd" "$target" "-" "stable"
    [ "$status" -eq 12 ]
    [ "$(cat "$gd/romp-install-failed")" = "$(printf '00000000\nffffffff')" ]
}

@test "bootstrap.sh: the channel marker follows the MOVE, inside the transaction" {
    # the marker is written under the update lock, after the moves land and before install.sh
    # (the user's audit, 2026-08-19: the shell wrote it after the lock released). It describes
    # what HEAD now IS: a build that moved but failed install carries ITS OWN channel, so a later
    # boot heal (which runs only install.sh) brings it up on the right channel — a stale dev
    # marker on a stable checkout followed unsigned main.
    printf '#!/usr/bin/env bash\nexit 1\n' > "$ROMP_REPO/install.sh"
    git -C "$ROMP_REPO" -c user.email=t@t -c user.name=t commit -qam broken
    git -C "$ROMP_REPO" tag -s v0.3.0 -m v0.3.0
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -ne 0 ]
    gd="$(git -C "$HOME/romp" rev-parse --absolute-git-dir)"
    [ "$(cat "$gd/romp-update-channel")" = "stable" ]
    [ -e "$gd/romp-install-failed" ]   # armed: the moved-but-uninstalled build stays gated
}
