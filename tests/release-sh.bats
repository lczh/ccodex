#!/usr/bin/env bats

# scripts/release.sh — the release gate. What it exists to enforce:
#   * VERSION is the ONE source of truth and the tag is DERIVED from it, so the two can
#     never disagree — the script takes no tag argument at all (2026-07-29);
#   * the tag is therefore always v-prefixed (bootstrap.sh's `git tag -l 'v*'` selector
#     matches nothing otherwise, and the installer fails closed);
#   * every published tag is a cryptographically signed annotated tag and verifies locally;
#   * the macOS CI run — dispatch-only, since macOS is billed even on public repos — must
#     be GREEN before a version is tagged.
# The GitHub CLI is stubbed via ROMP_GH so none of this touches real CI, and most tests run
# with --skip-tests: the fixture repo has no suites of its own.

ROMP_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

setup() {
    TEST_DIR="$(mktemp -d)"
    REPO="$TEST_DIR/repo"
    mkdir -p "$REPO/scripts"
    cp "$ROMP_DIR/scripts/release.sh" "$REPO/scripts/"
    git init -q "$REPO"
    # `git init -b main` needs git 2.28+; setting HEAD before the first commit works on every
    # version, which matters because this suite also runs on the CI's older shells.
    git -C "$REPO" symbolic-ref HEAD refs/heads/main
    git -C "$REPO" config user.email t@e.invalid
    git -C "$REPO" config user.name t
    ssh-keygen -q -t ed25519 -N '' -f "$TEST_DIR/release-key"
    printf 'release@example.invalid %s\n' "$(cat "$TEST_DIR/release-key.pub")" \
        > "$TEST_DIR/allowed-signers"
    git -C "$REPO" config gpg.format ssh
    git -C "$REPO" config user.signingKey "$TEST_DIR/release-key"
    git -C "$REPO" config user.email release@example.invalid
    git -C "$REPO" config gpg.ssh.allowedSignersFile "$TEST_DIR/allowed-signers"
    export ROMP_RELEASE_ALLOWED_SIGNERS="$TEST_DIR/allowed-signers"
    echo "0.1.0" > "$REPO/VERSION"
    git -C "$REPO" add -A
    git -C "$REPO" commit -qm init
    # A real origin, because the script now pushes the tag and (on a bump) the branch. A
    # bare repo is enough and keeps every test on the same footing as a real clone.
    git init -q --bare "$TEST_DIR/origin.git"
    git -C "$REPO" remote add origin "$TEST_DIR/origin.git"
    git -C "$REPO" push -q origin main
    export REPO_FOR_STUB="$REPO"
    export ROMP_RELEASE_POLL=0          # no sleeping in tests
    export ROMP_RELEASE_REPO=fixture/ccodex
    export GH_LOG="$TEST_DIR/gh.log"
}
teardown() { rm -rf "$TEST_DIR"; }

# STUB_CONCLUSION = what the stubbed `gh run view` reports (default success).
# STUB_FLAKY_VIEWS = report nothing for the first N `run view` calls, as a transient API
# error looks to the poll loop, then the real conclusion.
# STUB_PR_STATE = what `gh pr view` reports (default MERGED).
_stub_gh() {
    cat > "$TEST_DIR/gh" <<STUB
#!/usr/bin/env bash
TEST_DIR="$TEST_DIR"
echo "\$@" >> "$GH_LOG"
case "\$1 \$2" in
  # a NEW run id appears only after a dispatch, as the real API behaves
  "run list")   if [ -f "$TEST_DIR/dispatched" ]; then echo 1000; else echo 999; fi ;;
  "workflow run") touch "$TEST_DIR/dispatched"; exit 0 ;;
  "run view")
      n=\$(( \$(cat "$TEST_DIR/views" 2>/dev/null || echo 0) + 1 )); echo "\$n" > "$TEST_DIR/views"
      if [ "\$n" -le "\${STUB_FLAKY_VIEWS:-0}" ]; then exit 1; fi
      echo "\${STUB_CONCLUSION:-success}" ;;
  # Auto-merge really lands the branch on origin/main, so the script's post-merge
  # fast-forward has something to pull and VERSION genuinely changes on main. Simulating
  # the merge as a no-op would let the bump path "pass" while proving nothing.
  # The gh pr create command prints the PR URL; the script reads the NUMBER off its tail and addresses
  # every later call by that number (a fork-headed branch is unresolvable by name — see below).
  "pr create")  echo "https://github.com/$ROMP_RELEASE_REPO/pull/4242" ;;
  "pr merge")   if [ "\${STUB_PR_STATE:-MERGED}" = "MERGED" ]; then
                    git -C "$REPO" push -q "\${ROMP_RELEASE_REMOTE:-origin}" HEAD:main
                fi ;;
  "pr view")    echo "\${STUB_PR_STATE:-MERGED}" ;;
esac
exit 0
STUB
    chmod +x "$TEST_DIR/gh"
    export ROMP_GH="$TEST_DIR/gh"
}

# ── the source-of-truth contract ──────────────────────────────────────

@test "release: with no argument it releases whatever VERSION says" {
    _stub_gh
    run "$REPO/scripts/release.sh" --skip-tests
    [ "$status" -eq 0 ]
    run git -C "$REPO" tag -l
    [ "$output" = "v0.1.0" ]
    [ "$(git -C "$REPO" cat-file -t v0.1.0)" = "tag" ]
    run git -C "$REPO" -c gpg.ssh.allowedSignersFile="$TEST_DIR/allowed-signers" verify-tag v0.1.0
    [ "$status" -eq 0 ]
}

@test "release: verifies with Git's configured trust when no allowed-signers env is set" {
    _stub_gh
    unset ROMP_RELEASE_ALLOWED_SIGNERS
    GIT_TRACE2_EVENT="$TEST_DIR/git-trace" run /bin/bash "$REPO/scripts/release.sh" --skip-tests
    [ "$status" -eq 0 ]
    [ "$(git -C "$REPO" cat-file -t v0.1.0)" = "tag" ]
    run git -C "$REPO" verify-tag v0.1.0
    [ "$status" -eq 0 ]
    grep -q '"gpg.minTrustLevel=fully","verify-tag","v0.1.0"' "$TEST_DIR/git-trace"
}

@test "release: refuses to publish when the configured signing program fails" {
    _stub_gh
    printf '#!/usr/bin/env bash\nexit 9\n' > "$TEST_DIR/fail-signer"
    chmod +x "$TEST_DIR/fail-signer"
    git -C "$REPO" config gpg.ssh.program "$TEST_DIR/fail-signer"
    run "$REPO/scripts/release.sh" --skip-tests
    [ "$status" -ne 0 ]
    [[ "$output" == *"could not create signed tag"* || "$output" == *"signature did not verify locally"* ]]
    run git -C "$REPO" tag -l
    [ -z "$output" ]
    ! grep -q "release create" "$GH_LOG"
}

@test "release: deletes a new local tag when its signature is not trusted" {
    _stub_gh
    ssh-keygen -q -t ed25519 -N '' -f "$TEST_DIR/other-key"
    printf 'other@example.invalid %s\n' "$(cat "$TEST_DIR/other-key.pub")" \
        > "$TEST_DIR/other-allowed-signers"
    ROMP_RELEASE_ALLOWED_SIGNERS="$TEST_DIR/other-allowed-signers" \
        run "$REPO/scripts/release.sh" --skip-tests
    [ "$status" -ne 0 ]
    [[ "$output" == *"signature did not verify locally"* ]]
    run git -C "$REPO" tag -l
    [ -z "$output" ]
    ! grep -q "release create" "$GH_LOG"
}

@test "release: refuses a v-prefixed argument and names the version to pass instead" {
    # the tag is derived, so accepting one would re-open the mismatch this design closed
    _stub_gh
    run "$REPO/scripts/release.sh" v0.1.0
    [ "$status" -ne 0 ]
    [[ "$output" == *"WITHOUT the leading v"* ]]
    [[ "$output" == *"'0.1.0'"* ]]
    [ ! -s "$GH_LOG" ]
}

@test "release: the derived tag is always v-prefixed" {
    _stub_gh
    echo "1.2.3" > "$REPO/VERSION"
    git -C "$REPO" commit -qam ver
    run "$REPO/scripts/release.sh" --skip-tests
    [ "$status" -eq 0 ]
    run git -C "$REPO" tag -l
    [ "$output" = "v1.2.3" ]
}

@test "release: a bump level computes the next version and PRs it" {
    _stub_gh
    run "$REPO/scripts/release.sh" minor --skip-tests
    [ "$status" -eq 0 ]
    [[ "$output" == *"0.1.0 → 0.2.0"* ]]
    grep -q "pr create" "$GH_LOG"
    grep -q "pr merge" "$GH_LOG"
    # BY NUMBER, never by branch name (the user 2026-08-01): every PR here is fork-headed, because
    # rulesets block branch pushes upstream — and `gh pr merge <branch> --repo <upstream>` cannot
    # resolve a branch that lives on the fork. It failed with "no pull requests found for branch
    # release-0.3.0" one step after opening the PR, leaving VERSION merged but UNTAGGED: the exact
    # half-finished release this script exists to prevent.
    grep -q "pr merge 4242 " "$GH_LOG"
    grep -q "pr view 4242 " "$GH_LOG"
    ! grep -qE "pr (merge|view) release-" "$GH_LOG"
    run git -C "$REPO" tag -l
    [ "$output" = "v0.2.0" ]
}

@test "release: patch and major bump the right component" {
    _stub_gh
    echo "1.4.7" > "$REPO/VERSION"
    git -C "$REPO" commit -qam ver
    run "$REPO/scripts/release.sh" patch --skip-tests --dry-run
    [[ "$output" == *"1.4.7 → 1.4.8"* ]]
    run "$REPO/scripts/release.sh" major --skip-tests --dry-run
    [[ "$output" == *"1.4.7 → 2.0.0"* ]]
}

@test "release: an explicit number is taken as the target" {
    _stub_gh
    run "$REPO/scripts/release.sh" 3.0.0 --skip-tests --dry-run
    [ "$status" -eq 0 ]
    [[ "$output" == *"releasing v3.0.0"* ]]
}

@test "release: refuses a target that is not semver" {
    _stub_gh
    run "$REPO/scripts/release.sh" not-a-version --skip-tests
    [ "$status" -ne 0 ]
    [[ "$output" == *"is not semver"* ]]
}

@test "release: VERSION already at the target needs no bump PR" {
    # the resumable case: a bump PR landed earlier, so only the tagging half remains
    _stub_gh
    run "$REPO/scripts/release.sh" 0.1.0 --skip-tests
    [ "$status" -eq 0 ]
    [[ "$output" == *"no bump PR needed"* ]]
    ! grep -q "pr create" "$GH_LOG"
    run git -C "$REPO" tag -l
    [ "$output" = "v0.1.0" ]
}

@test "release: a version PR that never merges does NOT tag" {
    _stub_gh
    STUB_PR_STATE=OPEN run "$REPO/scripts/release.sh" minor --skip-tests
    [ "$status" -ne 0 ]
    [[ "$output" == *"did not merge"* ]]
    run git -C "$REPO" tag -l
    [ -z "$output" ]
}

@test "release: a version PR closed unmerged does NOT tag" {
    _stub_gh
    STUB_PR_STATE=CLOSED run "$REPO/scripts/release.sh" minor --skip-tests
    [ "$status" -ne 0 ]
    [[ "$output" == *"closed without merging"* ]]
    run git -C "$REPO" tag -l
    [ -z "$output" ]
}

# ── the macOS gate ────────────────────────────────────────────────────

@test "release: refuses when the macOS run fails, and does NOT tag" {
    _stub_gh
    STUB_CONCLUSION=failure run "$REPO/scripts/release.sh" --skip-tests
    [ "$status" -ne 0 ]
    [[ "$output" == *"macOS run did not pass"* ]]
    run git -C "$REPO" tag -l
    [ -z "$output" ]
}

@test "release: a transient API error while watching does not fail the gate" {
    # `gh run watch` treated a dropped connection as a failed RUN and refused a green
    # release twice (2026-07-27); the poll must ride out empty answers.
    _stub_gh
    ROMP_RELEASE_POLL=0.01 STUB_FLAKY_VIEWS=3 run "$REPO/scripts/release.sh" --skip-tests
    [ "$status" -eq 0 ]
    [[ "$output" == *"macOS run green"* ]]
    run git -C "$REPO" tag -l
    [ "$output" = "v0.1.0" ]
}

@test "release: tags when the macOS run is green" {
    _stub_gh
    run "$REPO/scripts/release.sh" --skip-tests
    [ "$status" -eq 0 ]
    [[ "$output" == *"macOS run green"* ]]
    run git -C "$REPO" tag -l
    [ "$output" = "v0.1.0" ]
    grep -q "workflow run CI" "$GH_LOG"      # it really did dispatch
}

@test "release: --skip-macos tags without CI, but says so loudly" {
    _stub_gh
    run "$REPO/scripts/release.sh" --skip-macos --skip-tests
    [ "$status" -eq 0 ]
    [[ "$output" == *"SKIPPING the macOS check"* ]]
    ! grep -q "workflow run CI" "$GH_LOG"     # no CI was dispatched
    run git -C "$REPO" tag -l
    [ "$output" = "v0.1.0" ]
}

# ── publishing ────────────────────────────────────────────────────────

@test "release: pushes the tag and publishes the release" {
    _stub_gh
    run "$REPO/scripts/release.sh" --skip-tests
    [ "$status" -eq 0 ]
    grep -q "release create v0.1.0" "$GH_LOG"
    # the tag really reached the remote — a local-only tag installs for nobody
    run git -C "$TEST_DIR/origin.git" tag -l
    [ "$output" = "v0.1.0" ]
}

@test "release: derives the GitHub repository from the tag-push remote" {
    _stub_gh
    unset ROMP_RELEASE_REPO
    git -C "$REPO" remote set-url origin https://github.com/lczh/ccodex.git
    git -C "$REPO" remote set-url --add --push origin "$TEST_DIR/origin.git"
    run "$REPO/scripts/release.sh" --skip-tests
    [ "$status" -eq 0 ]
    grep -q "release create v0.1.0 --repo lczh/ccodex" "$GH_LOG"
    run git -C "$TEST_DIR/origin.git" tag -l
    [ "$output" = "v0.1.0" ]
}

@test "release: refuses to publish a tag and release to different GitHub repositories" {
    _stub_gh
    git -C "$REPO" remote set-url origin https://github.com/lczh/ccodex.git
    ROMP_RELEASE_REPO=romp-on/romp run "$REPO/scripts/release.sh" --skip-tests
    [ "$status" -ne 0 ]
    [[ "$output" == *"tag and release must share a repository"* ]]
    [ ! -s "$GH_LOG" ]
    run git -C "$REPO" tag -l
    [ -z "$output" ]
}

@test "release: refuses a GitHub push URL that targets a different repository" {
    _stub_gh
    unset ROMP_RELEASE_REPO
    git -C "$REPO" remote set-url origin https://github.com/lczh/ccodex.git
    git -C "$REPO" remote set-url --push origin https://github.com/romp-on/romp.git
    run "$REPO/scripts/release.sh" --skip-tests
    [ "$status" -ne 0 ]
    [[ "$output" == *"fetches 'lczh/ccodex' but pushes tags to 'romp-on/romp'"* ]]
    [ ! -s "$GH_LOG" ]
}

@test "release: rejects repository identifiers with more than owner/repo" {
    _stub_gh
    ROMP_RELEASE_REPO=owner/repo/extra run "$REPO/scripts/release.sh" --skip-tests
    [ "$status" -ne 0 ]
    [[ "$output" == *"must be exactly owner/repo"* ]]
    [ ! -s "$GH_LOG" ]
}

@test "release: uses the validated release remote after a version PR merges" {
    _stub_gh
    # Keep origin deliberately stale. The PR stub lands the bump only on `release`, so a
    # post-merge fetch accidentally hard-coded to origin leaves VERSION at 0.1.0 and fails.
    git init -q --bare "$TEST_DIR/release.git"
    git -C "$REPO" remote add release "$TEST_DIR/release.git"
    git -C "$REPO" push -q release main
    git -C "$REPO" config remote.pushDefault release
    ROMP_RELEASE_REMOTE=release run "$REPO/scripts/release.sh" minor --skip-tests
    [ "$status" -eq 0 ]
    run git -C "$REPO" tag -l
    [ "$output" = "v0.2.0" ]
    run git -C "$TEST_DIR/release.git" tag -l
    [ "$output" = "v0.2.0" ]
}

@test "release: notes start at the PREVIOUS tag, never at the one being cut" {
    _stub_gh
    git -C "$REPO" tag v0.0.9
    run "$REPO/scripts/release.sh" --skip-tests
    [ "$status" -eq 0 ]
    grep -q -- "--notes-start-tag v0.0.9" "$GH_LOG"
}

# ── the ordinary guards ───────────────────────────────────────────────

@test "release: refuses a dirty tree" {
    _stub_gh
    echo dirty > "$REPO/junk.txt"
    git -C "$REPO" add junk.txt
    run "$REPO/scripts/release.sh" --skip-tests
    [ "$status" -ne 0 ]
    [[ "$output" == *"dirty"* ]]
}

@test "release: refuses a tag that already exists" {
    _stub_gh
    git -C "$REPO" tag v0.1.0
    run "$REPO/scripts/release.sh" --skip-tests
    [ "$status" -ne 0 ]
    [[ "$output" == *"already exists"* ]]
    [ ! -s "$GH_LOG" ]                        # bailed before spending any CI
}

@test "release: refuses when VERSION is missing" {
    _stub_gh
    rm "$REPO/VERSION"
    git -C "$REPO" commit -qam rmver
    run "$REPO/scripts/release.sh" --skip-tests
    [ "$status" -ne 0 ]
    [[ "$output" == *"source of truth"* ]]
}

@test "release: refuses a VERSION that is not X.Y.Z" {
    _stub_gh
    echo "nightly" > "$REPO/VERSION"
    git -C "$REPO" commit -qam ver
    run "$REPO/scripts/release.sh" --skip-tests
    [ "$status" -ne 0 ]
    [[ "$output" == *"is not X.Y.Z"* ]]
}

@test "release: a prerelease version tags as-is" {
    _stub_gh
    echo "0.2.0-rc.1" > "$REPO/VERSION"
    git -C "$REPO" commit -qam ver
    run "$REPO/scripts/release.sh" --skip-tests
    [ "$status" -eq 0 ]
    run git -C "$REPO" tag -l
    [ "$output" = "v0.2.0-rc.1" ]
}

@test "release: a prerelease bumps from its release number" {
    _stub_gh
    echo "0.2.0-rc.1" > "$REPO/VERSION"
    git -C "$REPO" commit -qam ver
    run "$REPO/scripts/release.sh" minor --skip-tests --dry-run
    [[ "$output" == *"0.2.0-rc.1 → 0.3.0"* ]]
}

@test "release: --dry-run changes nothing at all" {
    _stub_gh
    run "$REPO/scripts/release.sh" minor --skip-tests --dry-run
    [ "$status" -eq 0 ]
    run git -C "$REPO" tag -l
    [ -z "$output" ]
    run cat "$REPO/VERSION"
    [ "$output" = "0.1.0" ]
}

@test "release: a failing suite stops the release before any tag" {
    _stub_gh
    # a fixture 'suite' that fails collection, so the gate is exercised for real
    mkdir -p "$REPO/tests"
    echo "raise SystemExit(1)" > "$REPO/tests/conftest.py"
    git -C "$REPO" add -A && git -C "$REPO" commit -qm suite
    run "$REPO/scripts/release.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"Python suite failed"* ]]
    run git -C "$REPO" tag -l
    [ -z "$output" ]
}

@test "release: a clean checkout installs the lock and gates typecheck, tests, and VSIX packaging" {
    _stub_gh
    mkdir -p "$REPO/tests" "$REPO/vscode-extension" "$TEST_DIR/gate-bin"
    : > "$REPO/vscode-extension/package.json"
    : > "$REPO/vscode-extension/package-lock.json"
    cat > "$TEST_DIR/gate-bin/python3" <<'EOF'
#!/usr/bin/env bash
echo "python3 $*" >> "$RELEASE_GATE_LOG"
exit 0
EOF
    cat > "$TEST_DIR/gate-bin/npm" <<'EOF'
#!/usr/bin/env bash
echo "npm $*" >> "$RELEASE_GATE_LOG"
exit 0
EOF
    cat > "$TEST_DIR/gate-bin/npx" <<'EOF'
#!/usr/bin/env bash
echo "npx $*" >> "$RELEASE_GATE_LOG"
out=""
while [ "$#" -gt 0 ]; do
    if [ "$1" = "-o" ]; then shift; out="${1:-}"; break; fi
    shift
done
[ -n "$out" ] && printf 'vsix\n' > "$out"
exit 0
EOF
    chmod +x "$TEST_DIR/gate-bin/python3" "$TEST_DIR/gate-bin/npm" "$TEST_DIR/gate-bin/npx"
    git -C "$REPO" add -A
    git -C "$REPO" commit -qm gate-fixture
    export RELEASE_GATE_LOG="$TEST_DIR/release-gate.log"

    PATH="$TEST_DIR/gate-bin:$PATH" run "$REPO/scripts/release.sh" --skip-macos
    [ "$status" -eq 0 ]
    grep -q '^npm ci --silent$' "$RELEASE_GATE_LOG"
    grep -q '^npm run typecheck$' "$RELEASE_GATE_LOG"
    grep -q '^npm test$' "$RELEASE_GATE_LOG"
    grep -q '^npx --no-install vsce package --no-dependencies --allow-missing-repository -o ' "$RELEASE_GATE_LOG"
    [[ "$output" != *"skipping the webview suite"* ]]
}

@test "release: a locked dependency install failure blocks the tag" {
    _stub_gh
    mkdir -p "$REPO/tests" "$REPO/vscode-extension" "$TEST_DIR/gate-bin"
    : > "$REPO/vscode-extension/package.json"
    : > "$REPO/vscode-extension/package-lock.json"
    cat > "$TEST_DIR/gate-bin/python3" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
    cat > "$TEST_DIR/gate-bin/npm" <<'EOF'
#!/usr/bin/env bash
[ "${1:-}" = ci ] && exit 7
exit 0
EOF
    chmod +x "$TEST_DIR/gate-bin/python3" "$TEST_DIR/gate-bin/npm"
    git -C "$REPO" add -A
    git -C "$REPO" commit -qm gate-fixture

    PATH="$TEST_DIR/gate-bin:$PATH" run "$REPO/scripts/release.sh" --skip-macos
    [ "$status" -ne 0 ]
    [[ "$output" == *"npm ci failed"* ]]
    run git -C "$REPO" tag -l
    [ -z "$output" ]
}
