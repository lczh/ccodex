#!/usr/bin/env bash
# scripts/release.sh [major|minor|patch|X.Y.Z] [flags] — cut a romp release, end to end.
#
# ONE SOURCE OF TRUTH: the VERSION file. The tag is DERIVED from it, always as
# "v$(cat VERSION)", and this script does not accept a tag argument at all. That is
# deliberate: the old interface took the tag and then checked it against VERSION, so the
# two could disagree and the only thing standing between you and a mismatched release was
# a comparison you had to remember to trust. Removing the second input removes the whole
# class of error — there is now exactly one place a human types the number, and everything
# downstream is computed (the user 2026-07-29, who wanted one script that does everything
# and keeps the version in sync with the tags).
#
# It runs the whole sequence, because the steps between "bump the number" and "users can
# install it" were a chain of by-hand commands that were easy to half-finish. Leaving
# VERSION merged but untagged is the worst of those states: main claims a version that
# bootstrap.sh will not install, since bootstrap picks the newest v* TAG.
#
#   1. resolve the target version (a bump level, an explicit number, or whatever VERSION
#      already says) and refuse it if that tag already exists
#   2. if VERSION needs to change: branch, commit, push, open a PR, auto-merge it, and wait
#      for it to land on main  (skipped entirely when VERSION is already correct)
#   3. run the test suites
#   4. the macOS gate (see below)
#   5. create and locally verify a cryptographically signed annotated tag, push it, and
#      publish the GitHub release
#
# Two rules it has always existed to enforce, both easy to get wrong by hand and expensive
# to get wrong in public:
#
#   * The tag MUST be v-prefixed. bootstrap.sh picks the release with
#     `git tag -l 'v*' --sort=-v:refname | head -n1`. A tag like "0.1.0" matches NOTHING, so
#     the strict one-line installer reports that no release exists. Deriving the tag guarantees
#     the prefix.
#   * macOS CI does not run on pushes (it is billed even on public repos, ~10x, so it is
#     workflow_dispatch-only). A macOS-only breakage can therefore sit undetected until a
#     user hits it. Releasing is exactly when that matters, so this triggers the macOS run
#     and REFUSES to tag unless it goes green.
#
# --skip-macos exists for the case where you must ship anyway; it is deliberately an
# explicit flag (never an env default) and it says so loudly.
set -euo pipefail

GH="${ROMP_GH:-gh}"                       # overridable so tests can stub the GitHub CLI
POLL="${ROMP_RELEASE_POLL:-5}"            # seconds between checks while the run starts
REF="${ROMP_RELEASE_REF:-main}"
RELEASE_REMOTE="${ROMP_RELEASE_REMOTE:-origin}"
# Normally derived from RELEASE_REMOTE. The override is useful for local/non-GitHub remotes
# (including the hermetic test fixture), but a GitHub remote and an explicit override must agree.
RELEASE_REPO="${ROMP_RELEASE_REPO:-${ROMP_RELEASE_UPSTREAM:-}}"
skip_macos=0
skip_tests=0
dry_run=0
bump=""

usage() {
    cat >&2 <<'USAGE'
usage: scripts/release.sh [major|minor|patch|X.Y.Z] [flags]

  (no argument)       release the version VERSION already carries
  major|minor|patch   bump VERSION by that much first, via a PR
  X.Y.Z               set VERSION to exactly this, via a PR

flags:
  --skip-macos    tag without waiting for the dispatch-only macOS run (loud, discouraged)
  --skip-tests    do not run the local suites before releasing
  --dry-run       print what would happen; change nothing

The release tag is signed with Git's configured signing key and verified locally before push.
USAGE
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --skip-macos) skip_macos=1 ;;
        --skip-tests) skip_tests=1 ;;
        --dry-run)    dry_run=1 ;;
        -h|--help)    usage ;;
        -*)           echo "release: unknown flag $1" >&2; usage ;;
        v[0-9]*)      echo "release: pass the version WITHOUT the leading v ('${1#v}', not '$1')." >&2
                      echo "  The tag is derived from VERSION, so the two cannot disagree." >&2
                      exit 2 ;;
        *)            [ -z "$bump" ] || usage; bump="$1" ;;
    esac
    shift
done

die() { echo "release: $*" >&2; exit 1; }
say() { echo "release: $*"; }
step() { if [ "$dry_run" -eq 1 ]; then echo "release: [dry-run] $*"; else "$@"; fi; }

verify_release_tag() {
    local release_tag="$1"
    if [ -n "${ROMP_RELEASE_ALLOWED_SIGNERS:-}" ]; then
        if [ ! -f "$ROMP_RELEASE_ALLOWED_SIGNERS" ] || [ ! -r "$ROMP_RELEASE_ALLOWED_SIGNERS" ]; then
            echo "release: ROMP_RELEASE_ALLOWED_SIGNERS is not a readable regular file: $ROMP_RELEASE_ALLOWED_SIGNERS" >&2
            return 1
        fi
        git -c gpg.minTrustLevel=fully \
            -c "gpg.ssh.allowedSignersFile=$ROMP_RELEASE_ALLOWED_SIGNERS" \
            verify-tag "$release_tag"
    else
        git -c gpg.minTrustLevel=fully verify-tag "$release_tag"
    fi
}

# Resolve the repo from THIS SCRIPT's location, never the caller's cwd. With
# `git rev-parse --show-toplevel` the script would inspect — and tag — whatever repo you
# happened to be standing in, reading that tree's cleanliness and VERSION instead of the one
# being released.
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

github_repo_from_url() {
    local url="$1" slug=""
    case "$url" in
        git@github.com:*) slug="${url#git@github.com:}" ;;
        http://github.com/*|https://github.com/*|ssh://git@github.com/*)
            slug="${url#*github.com/}" ;;
    esac
    slug="${slug%.git}"
    case "$slug" in
        */*/*|/*|*/|"") return 0 ;;
        */*) printf '%s\n' "$slug" ;;
    esac
}

# A release must be created in the same GitHub repository that receives its tag. Previously
# this was hard-coded to romp-on/romp, so a ccodex run pushed a tag to lczh/ccodex and then
# failed after the irreversible step while trying to publish the release in another repo.
release_url="$(git remote get-url "$RELEASE_REMOTE" 2>/dev/null || true)"
[ -n "$release_url" ] || die "release remote '$RELEASE_REMOTE' does not exist."
release_push_url="$(git remote get-url --push "$RELEASE_REMOTE" 2>/dev/null || true)"
fetch_repo="$(github_repo_from_url "$release_url")"
push_repo="$(github_repo_from_url "$release_push_url")"
if [ -n "$fetch_repo" ] && [ -n "$push_repo" ] && [ "$fetch_repo" != "$push_repo" ]; then
    die "$RELEASE_REMOTE fetches '$fetch_repo' but pushes tags to '$push_repo'; the tag and release must share a repository."
fi
# A GitHub push URL is authoritative. A non-GitHub push URL occurs in hermetic/local release
# fixtures; there an explicit repository override may be used, or the GitHub fetch URL supplies it.
remote_repo="${push_repo:-$fetch_repo}"
if [ -z "$RELEASE_REPO" ]; then
    [ -n "$remote_repo" ] || die "cannot derive a GitHub repository from '$release_url'; set ROMP_RELEASE_REPO=owner/repo."
    RELEASE_REPO="$remote_repo"
elif [ -n "$remote_repo" ] && [ "$RELEASE_REPO" != "$remote_repo" ]; then
    die "ROMP_RELEASE_REPO '$RELEASE_REPO' does not match $RELEASE_REMOTE '$remote_repo'; the tag and release must share a repository."
fi
case "$RELEASE_REPO" in
    */*/*|/*|*/|"") die "release repository '$RELEASE_REPO' must be exactly owner/repo." ;;
    */*) ;;
    *) die "release repository '$RELEASE_REPO' must be owner/repo." ;;
esac
PROJECT="${RELEASE_REPO##*/}"

[ -f VERSION ] || die "no VERSION file at the repo root — it is the source of truth and must exist."
current="$(tr -d '[:space:]' < VERSION)"

# ── 1. resolve the target version ─────────────────────────────────────
# The bump arithmetic works on the X.Y.Z core, so a prerelease suffix ("0.2.0-rc.1") bumps
# from its release number rather than tripping the parser.
core="${current%%-*}"
if [[ ! "$core" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    die "VERSION ('$current') is not X.Y.Z — cannot compute a bump from it."
fi
IFS=. read -r cur_major cur_minor cur_patch <<EOF
$core
EOF

case "$bump" in
    "")      target="$current" ;;
    major)   target="$((cur_major + 1)).0.0" ;;
    minor)   target="$cur_major.$((cur_minor + 1)).0" ;;
    patch)   target="$cur_major.$cur_minor.$((cur_patch + 1))" ;;
    *)       target="$bump" ;;
esac

if [[ ! "$target" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z.-]+)?$ ]]; then
    die "'$target' is not semver (X.Y.Z, optionally with a prerelease suffix)."
fi
tag="v$target"

if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
    die "tag $tag already exists."
fi

say "releasing $tag (VERSION currently reads $current)."

# ── 2. the tree must be releasable ────────────────────────────────────
[ -z "$(git status --porcelain)" ] || die "working tree is dirty — commit or stash first."

# ── 3. land the version bump, if there is one ─────────────────────────
# Skipped entirely when VERSION already carries the target, which is the normal case when a
# bump PR landed earlier. That makes a half-finished release resumable: re-running picks up
# where it stopped instead of insisting on starting over.
if [ "$current" != "$target" ]; then
    # Branch pushes to the upstream are blocked by rulesets, so publishing is always
    # push-to-a-fork then PR. remote.pushDefault is the configured answer when there is one.
    publish="$(git config --get remote.pushDefault || true)"
    if [ -z "$publish" ]; then
        if git remote get-url fork >/dev/null 2>&1; then publish=fork; else publish=origin; fi
    fi
    branch="release-$target"
    say "VERSION $current → $target, via a PR on $branch (pushing to '$publish')."
    if [ "$dry_run" -eq 1 ]; then
        say "[dry-run] would branch, commit VERSION=$target, open a PR, and auto-merge it."
    else
        printf '%s\n' "$target" > VERSION
        git switch -c "$branch" >/dev/null 2>&1 || die "could not create branch $branch."
        git add VERSION
        git commit -qm "VERSION $target" || die "nothing to commit for the version bump."
        git push -q -u "$publish" "$branch" || die "could not push $branch to $publish."
        # Address the PR by NUMBER, taken from the URL `gh pr create` prints (the user 2026-08-01).
        # `gh pr merge <branch>` resolves the branch WITHIN --repo, and the branch lives on the FORK
        # (rulesets block branch pushes upstream, so every PR here is fork-headed) — so it answered
        # "no pull requests found for branch release-0.3.0" and the release died one step after
        # opening the PR, leaving VERSION merged-but-untagged, exactly the half-finished state this
        # script exists to prevent. A number is unambiguous in any repo.
        pr_url="$("$GH" pr create --repo "$RELEASE_REPO" --title "VERSION $target" \
            --body "Version bump for \`$tag\`, opened by scripts/release.sh.")" \
            || die "could not open the version PR."
        pr="${pr_url##*/}"
        case "$pr" in
            ''|*[!0-9]*) die "could not read the PR number from '$pr_url'." ;;
        esac
        say "opened PR #$pr."
        "$GH" pr merge "$pr" --repo "$RELEASE_REPO" --auto --merge \
            || die "could not arm auto-merge on PR #$pr."
        say "waiting for the version PR to land on $REF (its CI is the first gate)..."
        state=""
        for _ in $(seq 1 120); do
            state="$("$GH" pr view "$pr" --repo "$RELEASE_REPO" --json state -q .state 2>/dev/null || true)"
            if [ "$state" = "MERGED" ]; then break; fi
            if [ "$state" = "CLOSED" ]; then die "the version PR was closed without merging."; fi
            if [ "$POLL" = "0" ]; then break; fi
            sleep "$POLL"
        done
        [ "$state" = "MERGED" ] || die "the version PR did not merge — check $RELEASE_REPO."
        git switch "$REF" >/dev/null 2>&1 || die "could not switch back to $REF."
        git fetch -q "$RELEASE_REMOTE"
        git merge --ff-only "$RELEASE_REMOTE/$REF" >/dev/null \
            || die "could not fast-forward $REF from $RELEASE_REMOTE after the merge."
        say "version PR merged; $REF now carries $target."
    fi
else
    say "VERSION already reads $target — no bump PR needed."
fi

# Re-read rather than trust the arithmetic: after the merge, the file on disk is the truth.
if [ "$dry_run" -eq 0 ]; then
    have="$(tr -d '[:space:]' < VERSION)"
    [ "$have" = "$target" ] || die "VERSION says '$have' but we are releasing '$tag' — refusing to tag a mismatch."
fi

# ── 4. the tests ──────────────────────────────────────────────────────
if [ "$skip_tests" -eq 1 ]; then
    echo "release: !! skipping the local suites at your explicit request (--skip-tests)."
else
    say "running the Python suite..."
    step python3 -m pytest tests/ -q || die "the Python suite failed — NOT releasing."

    # A release cannot inherit whatever dependency tree happens to be on the maintainer's machine.
    # Install exactly the reviewed lock, then exercise the same type/build/package path as CI and
    # the installer. The old node_modules-exists check silently skipped this entire gate on a clean
    # checkout, which made the least-prepared release environment the least-tested one.
    run_in_extension() { (cd vscode-extension && "$@"); }
    say "installing the locked webview dependencies..."
    step run_in_extension npm ci --silent \
        || die "npm ci failed — the locked webview dependencies are not reproducible; NOT releasing."
    say "type-checking the webview and extension..."
    step run_in_extension npm run typecheck \
        || die "the webview typecheck failed — NOT releasing."
    say "running the webview suite..."
    step run_in_extension npm test || die "the webview suite failed — NOT releasing."

    if [ "$dry_run" -eq 1 ]; then
        step run_in_extension npx --no-install vsce package --no-dependencies \
            --allow-missing-repository -o '<temporary-vsix>'
    else
        vsix_tmp="$(mktemp -d "${TMPDIR:-/tmp}/ccodex-release-vsix.XXXXXX")" \
            || die "could not create a temporary directory for the VSIX smoke test."
        vsix_out="$vsix_tmp/romp-chat-view.vsix"
        if ! run_in_extension npx --no-install vsce package --no-dependencies \
                --allow-missing-repository -o "$vsix_out"; then
            rm -rf "$vsix_tmp"
            die "the pinned VSIX package smoke test failed — NOT releasing."
        fi
        if [ ! -s "$vsix_out" ]; then
            rm -rf "$vsix_tmp"
            die "the VSIX package smoke test produced no artifact — NOT releasing."
        fi
        rm -rf "$vsix_tmp"
        say "VSIX package smoke passed."
    fi
fi

# ── 5. the macOS gate ─────────────────────────────────────────────────
if [ "$skip_macos" -eq 1 ]; then
    echo "release: !! SKIPPING the macOS check at your explicit request (--skip-macos)."
    echo "release: !! a macOS-only breakage in $tag would reach users undetected."
elif [ "$dry_run" -eq 1 ]; then
    say "[dry-run] would dispatch the macOS CI run and wait for it."
else
    say "triggering the macOS CI run on $REF (it is dispatch-only, so this is the check)..."
    before="$("$GH" run list --workflow CI --event workflow_dispatch -L 1 --json databaseId -q '.[0].databaseId // ""' 2>/dev/null || true)"
    "$GH" workflow run CI --ref "$REF" || die "could not dispatch the CI workflow."

    # Identify OUR run by waiting for the newest dispatch run to differ from the one that was
    # newest before we dispatched — `gh workflow run` prints no run id, and taking the newest
    # unconditionally would happily watch a PREVIOUS run and pass the gate on a stale green.
    #
    # Each guard is a full `if`: under `set -e`, a bare `[ x ] && y` whose test is false makes
    # the whole list non-zero and kills the script.
    run_id=""
    for _ in $(seq 1 60); do
        if [ "$POLL" != "0" ]; then sleep "$POLL"; fi
        cur="$("$GH" run list --workflow CI --event workflow_dispatch -L 1 --json databaseId -q '.[0].databaseId // ""' 2>/dev/null || true)"
        if [ -n "$cur" ] && [ "$cur" != "$before" ]; then run_id="$cur"; break; fi
        if [ "$POLL" = "0" ]; then break; fi     # test mode: never spin
    done
    if [ -z "$run_id" ]; then
        die "the dispatched CI run never appeared — check the Actions tab."
    fi

    say "watching run $run_id (macOS bats is ~16 min; this is the wait you are paying for)..."
    # Poll `run view`, never `gh run watch`: watch holds one long connection and treats ANY
    # hiccup — a GitHub 502, a local socket error — as run failure. Twice (2026-07-27) it
    # declared a still-running, ultimately GREEN gate "did not pass". Here a transient API
    # error just yields an empty conclusion and we poll again; only the run's own verdict
    # ends the wait.
    conclusion=""
    while :; do
        conclusion="$("$GH" run view "$run_id" --json conclusion -q .conclusion 2>/dev/null || true)"
        if [ -n "$conclusion" ]; then break; fi
        if [ "$POLL" = "0" ]; then break; fi     # test mode: never spin
        sleep "$POLL"
    done
    if [ "$conclusion" != "success" ]; then
        die "the macOS run did not pass (conclusion: ${conclusion:-none}) — NOT tagging $tag.
  Fix it, or re-run with --skip-macos if you have decided to ship anyway."
    fi
    say "macOS run green."
fi

# ── 6. tag, push, publish ─────────────────────────────────────────────
# The previous release, for the notes range — computed BEFORE the new tag exists so it can
# never pick itself.
prev="$(git tag -l 'v*' --sort=-v:refname | head -n1 || true)"

if [ "$dry_run" -eq 1 ]; then
    step git tag -s "$tag" -m "$PROJECT $tag"
    step git -c gpg.minTrustLevel=fully verify-tag "$tag"
else
    git tag -s "$tag" -m "$PROJECT $tag" \
        || die "could not create signed tag $tag. Configure Git signing (user.signingKey and, if needed, gpg.format) and retry."
    # A configured private key is not enough: prove the resulting tag is parseable and verifies
    # under the same Git trust configuration installers use before it leaves this machine.
    if ! verify_release_tag "$tag"; then
        git tag -d "$tag" >/dev/null 2>&1 || true
        die "the new tag's signature did not verify locally; deleted $tag and did not push it."
    fi
fi
say "created and verified signed tag $tag."

# The tag goes to the validated release remote: a tag that exists only locally installs for
# nobody, and publishing it in a different GitHub repository would leave a half-release.
step git push -q "$RELEASE_REMOTE" "$tag" || die "could not push $tag to $RELEASE_REMOTE."
say "pushed $tag."

if [ -n "$prev" ]; then
    step "$GH" release create "$tag" --repo "$RELEASE_REPO" --title "$PROJECT $tag" \
        --generate-notes --notes-start-tag "$prev" \
        || die "$tag is pushed, but publishing the release failed — finish with:
  gh release create $tag --repo $RELEASE_REPO --generate-notes"
else
    step "$GH" release create "$tag" --repo "$RELEASE_REPO" --title "$PROJECT $tag" --generate-notes \
        || die "$tag is pushed, but publishing the release failed."
fi
say "published. $tag is live — bootstrap.sh will install it."
