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
    [ "$(git -C "$HOME/romp" config --get romp.updateChannel)" = "stable" ]
}

@test "bootstrap.sh: ROMP_REF=main is the explicit dev opt-in, and a tag re-run flips back" {
    ROMP_DIR="$HOME/romp" ROMP_REF=main run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [ "$(git -C "$HOME/romp" config --get romp.updateChannel)" = "dev" ]
    # re-bootstrapping onto a release moves the install back to stable — the channel follows
    # the last explicit choice, never a sticky accident
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [ "$(git -C "$HOME/romp" config --get romp.updateChannel)" = "stable" ]
}

@test "bootstrap.sh: a feature branch or pinned ref is NOT the dev channel" {
    # dev means exactly the ROMP_REF=main opt-in: a branch or pinned-commit install is a
    # deliberate NON-main checkout, and recording it as dev would authorize converging it onto a
    # main it never asked to follow (the user's audit, 2026-08-17)
    git -C "$ROMP_REPO" branch feature-x
    ROMP_DIR="$HOME/romp" ROMP_REF=feature-x run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [ "$(git -C "$HOME/romp" config --get romp.updateChannel)" = "stable" ]
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
