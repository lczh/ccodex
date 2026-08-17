#!/usr/bin/env bash
# One-line install (ccodex — this fork; see README.md / docs/codex.md):
#   curl -fsSL https://raw.githubusercontent.com/lczh/ccodex/main/bootstrap.sh | bash
#
# Clones the repo, cryptographically verifies and checks out the requested release tag
# (newest v* release by default), runs install.sh, and puts bin/ on your PATH. The clone IS
# the installation
# (install.sh symlinks the hooks, MCP config and skills out of it, and bin/
# links back into it), so this keeps the clone at a stable location rather
# than a temp dir.
#
# Knobs:
#   ROMP_REPO=<git url> which repo to clone (default lczh/ccodex)
#   ROMP_DIR=~/ccodex   where to clone (default ~/ccodex)
#   ROMP_REF=main       install a specific tag/branch instead of the newest release
#   ROMP_RELEASE_ALLOWED_SIGNERS=/absolute/path  SSH allowed-signers file used by git
#   ROMP_NO_PATH=1      don't touch your shell rc
# install.sh's own switches (ROMP_NO_EXT, ROMP_NO_SERVICE, ROMP_NO_SDK) pass through.
set -euo pipefail

REPO="${ROMP_REPO:-https://github.com/lczh/ccodex.git}"
DIR="${ROMP_DIR:-$HOME/ccodex}"

command -v git >/dev/null 2>&1 || {
    echo "romp: git not found. Install git and re-run." >&2; exit 1; }

repo_identity() {
    case "$1" in
        git@github.com:*) printf 'github.com/%s\n' "${1#git@github.com:}" | sed 's/\.git$//; s:/$::' ;;
        http://github.com/*|https://github.com/*|ssh://git@github.com/*)
            printf 'github.com/%s\n' "${1#*github.com/}" | sed 's/\.git$//; s:/$::' ;;
        *) printf '%s\n' "$1" | sed 's/\.git$//; s:/$::' ;;
    esac
}

# Clone, or reuse an existing clone. Refuse to touch a directory that is not a
# romp checkout: this script writes into it and checks out refs, which would be
# destructive to somebody else's files.
if [ -e "$DIR" ]; then
    if [ -d "$DIR/.git" ] && [ -f "$DIR/install.sh" ] && [ -d "$DIR/kernel" ]; then
        actual_repo="$(git -C "$DIR" remote get-url origin 2>/dev/null || true)"
        if [ -z "$actual_repo" ] || [ "$(repo_identity "$actual_repo")" != "$(repo_identity "$REPO")" ]; then
            echo "romp: $DIR points at a different origin; refusing to update the wrong checkout." >&2
            echo "  expected: $REPO" >&2
            echo "  actual:   ${actual_repo:-<missing origin>}" >&2
            echo "  Choose its repository with ROMP_REPO, or choose another ROMP_DIR." >&2
            exit 1
        fi
        echo "==> Updating the romp clone at $DIR"
        git -C "$DIR" fetch --quiet --tags origin || {
            echo "romp: could not fetch updates from origin." >&2; exit 1; }
    else
        echo "romp: $DIR exists and is not a romp clone. Refusing to write into it." >&2
        echo "  Move it aside, or choose another location:" >&2
        echo "    curl -fsSL <url> | ROMP_DIR=\"\$HOME/elsewhere\" bash" >&2
        exit 1
    fi
else
    # Name the destination and the knob in the same breath. The clone lands in $HOME
    # regardless of where you run the one-liner from — reasonable for a `curl | bash`
    # (your cwd could be /, /tmp, or somebody else's repo), but surprising if unstated
    # (the user 2026-07-27 asked whether it installs into the current directory).
    echo "==> Cloning romp into $DIR   (override with ROMP_DIR=/path)"
    git clone --quiet "$REPO" "$DIR"
fi

# Pick the ref. Releases are `v`-prefixed, so match on that rather than taking
# the newest tag of any kind: the repo also carries non-release tags, and
# installing one of those would silently pin somebody to an old baseline.
ref="${ROMP_REF:-}"
if [ -z "$ref" ]; then
    # Select only a tag the fetched origin actually advertises, and require the local tag object
    # to be that exact remote object. `git tag -l` alone also sees local-only tags, so a signed
    # v999 tag planted in a reused checkout could otherwise outrank every published release.
    if ! remote_release_refs="$(git -C "$DIR" ls-remote --tags --refs origin 'refs/tags/v*')"; then
        echo "romp: could not enumerate release tags from origin." >&2
        exit 1
    fi
    while IFS= read -r candidate; do
        [ -n "$candidate" ] || continue
        # Stable releases ONLY — the exact vX.Y.Z shape the kernel updater's _semver accepts. The
        # version sort otherwise ranks a prerelease-suffixed tag (v9.9.9-rc.1) above every stable,
        # so a fresh install would take a prerelease the UPDATER then refuses to move past — the
        # two pickers must agree on what a release is (the user 2026-08-16).
        case "$candidate" in
            v[0-9]*.[0-9]*.[0-9]*) printf '%s' "${candidate#v}" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || continue ;;
            *) continue ;;
        esac
        remote_tag_oid="$(printf '%s\n' "$remote_release_refs" | \
            awk -v wanted="refs/tags/$candidate" '$2 == wanted { print $1; exit }')"
        [ -n "$remote_tag_oid" ] || continue
        local_tag_oid="$(git -C "$DIR" rev-parse --verify "refs/tags/$candidate" 2>/dev/null || true)"
        if [ "$local_tag_oid" != "$remote_tag_oid" ]; then
            echo "romp: fetched tag '$candidate' does not match the tag object advertised by origin." >&2
            exit 1
        fi
        ref="$candidate"
        break
    done < <(git -C "$DIR" tag -l 'v*' --sort=-v:refname)
    if [ -z "$ref" ]; then
        echo "romp: no release tag is published; refusing to install an unverified branch." >&2
        echo "  Wait for a signed release, or explicitly choose development code with ROMP_REF=main." >&2
        exit 1
    fi
fi

echo "==> Checking out $ref"
is_tag=0
if git -C "$DIR" show-ref --verify --quiet "refs/tags/$ref"; then
    is_tag=1
    # `git verify-tag` rejects lightweight, unsigned, malformed, unknown-key, and bad-signature
    # tags. Never search for an older tag or fall back to main after this fails: doing so would
    # turn a forged newest release into an unsigned install. For SSH signatures Git needs an
    # allowed-signers file; callers may point at one without mutating their global git config.
    allowed_signers=""
    if [ -n "${ROMP_RELEASE_ALLOWED_SIGNERS:-}" ]; then
        if [ ! -f "$ROMP_RELEASE_ALLOWED_SIGNERS" ] || [ ! -r "$ROMP_RELEASE_ALLOWED_SIGNERS" ]; then
            echo "romp: ROMP_RELEASE_ALLOWED_SIGNERS is not a readable regular file: $ROMP_RELEASE_ALLOWED_SIGNERS" >&2
            exit 1
        fi
        allowed_signers_dir="$(cd "$(dirname "$ROMP_RELEASE_ALLOWED_SIGNERS")" && pwd -P)" || {
            echo "romp: could not resolve ROMP_RELEASE_ALLOWED_SIGNERS: $ROMP_RELEASE_ALLOWED_SIGNERS" >&2
            exit 1
        }
        allowed_signers="$allowed_signers_dir/$(basename "$ROMP_RELEASE_ALLOWED_SIGNERS")"
    fi
    # A trust root CONFIGURED IN GIT also enforces — a signers file persisted in this clone by an
    # earlier bootstrap, or the user's global config. Without this, a re-run WITHOUT the one-shot
    # env var silently downgraded an already-hardened install back to warn-and-proceed
    # (the user's audit, 2026-08-16). The probe fails CLOSED like the kernel's: only git's clean
    # "key absent" answer (rc 1) reads as no trust root — a query error can't prove absence, and a
    # configured-but-EMPTY value is a misconfiguration for verification to fail loudly against,
    # never a downgrade to warning-only (the user's audit, 2026-08-17).
    configured_signers_rc=0
    probe_err="$(git -C "$DIR" config --get gpg.ssh.allowedSignersFile 2>&1 >/dev/null)" || configured_signers_rc=$?
    trust_root_in_git=1
    [ "$configured_signers_rc" -eq 1 ] && [ -z "$probe_err" ] && trust_root_in_git=0
    echo "==> Verifying release signature for $ref"
    signature_ok=1
    if [ -n "$allowed_signers" ]; then
        git -C "$DIR" -c gpg.minTrustLevel=fully \
            -c "gpg.ssh.allowedSignersFile=$allowed_signers" \
            verify-tag "$ref" || signature_ok=0
    else
        git -C "$DIR" -c gpg.minTrustLevel=fully verify-tag "$ref" 2>/dev/null || signature_ok=0
    fi
    if [ "$signature_ok" -ne 1 ]; then
        # Verification ENFORCES only when a trust root was configured (ROMP_RELEASE_ALLOWED_SIGNERS,
        # or ROMP_VERIFY_RELEASES=1 for GPG-trust users). Mandatory-with-no-published-key bricked
        # every install: the repo's releases are not signed yet and no key is distributed anywhere,
        # so there was nothing any installer could trust (2026-08-14 review). Configured deployments
        # keep the full hard-fail; everyone else gets a loud, honest warning instead of a dead end.
        if [ -n "$allowed_signers" ] || [ -n "${ROMP_VERIFY_RELEASES:-}" ] || [ "$trust_root_in_git" -eq 1 ]; then
            echo "romp: release tag '$ref' does not have a valid signature trusted by git; refusing to install it." >&2
            echo "  Import the maintainer's GPG key or configure Git's SSH allowed-signers file, then rerun." >&2
            exit 1
        fi
        echo "==> Note: release tag '$ref' is not signature-verified (no trust root configured)." >&2
        echo "    To enforce verification, set ROMP_RELEASE_ALLOWED_SIGNERS to an allowed-signers file." >&2
    fi
    if [ -n "$allowed_signers" ]; then
        # The kernel updater runs long after this bootstrap process and cannot inherit a one-shot
        # environment variable. Persist the already-validated absolute path in this clone only so
        # future `git verify-tag` calls enforce the identical SSH trust root.
        git -C "$DIR" config --local gpg.ssh.allowedSignersFile "$allowed_signers" || {
            echo "romp: verified $ref but could not persist its SSH trust configuration; refusing to install." >&2
            exit 1
        }
    fi
fi

if [ "$is_tag" -eq 1 ]; then
    # Address the tag by its full ref and detach. If a remote branch has the same short name,
    # installing that branch after verifying the tag would install different, unsigned code.
    git -C "$DIR" checkout --quiet --detach "refs/tags/$ref" || {
        echo "romp: verified tag '$ref' could not be checked out." >&2; exit 1; }
elif git -C "$DIR" show-ref --verify --quiet "refs/remotes/origin/$ref"; then
    # Branch installs follow exactly the fetched remote branch. Never suppress a non-fast-forward:
    # doing so used to reinstall stale local code while announcing a successful update.
    if git -C "$DIR" show-ref --verify --quiet "refs/heads/$ref"; then
        git -C "$DIR" checkout --quiet "$ref" || {
            echo "romp: could not check out branch $ref (is the worktree dirty?)." >&2; exit 1; }
    else
        git -C "$DIR" checkout --quiet -b "$ref" "origin/$ref" || {
            echo "romp: could not create local branch $ref from origin/$ref." >&2; exit 1; }
    fi
    git -C "$DIR" merge --quiet --ff-only "origin/$ref" || {
        echo "romp: local branch $ref cannot fast-forward to origin/$ref; refusing to install stale code." >&2
        echo "  Resolve or preserve the local commits, then rerun bootstrap.sh." >&2
        exit 1
    }
else
    # Tags and explicit commit IDs are immutable checkouts; pulling them is neither useful nor valid.
    git -C "$DIR" checkout --quiet "$ref" || {
        echo "romp: ref '$ref' was not found after fetching origin." >&2; exit 1; }
fi

# Persist the UPDATE CHANNEL this install chose, in the CHECKOUT's own git config — the channel
# describes the checkout, and a per-state-dir copy let kernels sharing one checkout disagree
# about it (the user's audits, 2026-08-17). The kernel's main-convergence updater follows
# origin/main ONLY on `dev`, and `dev` means exactly the documented ROMP_REF=main opt-in: a
# feature branch or a pinned commit is a deliberate NON-main install, and recording it as dev
# would authorize converging it onto a main it never asked to follow. Everything else — tags
# included — is stable; re-running bootstrap follows the last explicit choice.
channel="stable"
[ "$ref" = "main" ] && channel="dev"
# The marker lives in the WORKTREE's own git dir: `git config --local` is repository-scoped, so a
# dev worktree could flip a sibling release worktree's channel via the shared config (the
# user's audit, 2026-08-17). The legacy key is unset so it can never shadow the marker.
gd="$(git -C "$DIR" rev-parse --absolute-git-dir)" || {
    echo "romp: could not resolve the clone's git dir for the update channel." >&2; exit 1; }
printf '%s\n' "$channel" > "$gd/romp-update-channel" || {
    echo "romp: could not record the update channel in $gd." >&2; exit 1; }
git -C "$DIR" config --unset romp.updateChannel 2>/dev/null || true
echo "==> Update channel: $channel"

echo "==> Running install.sh"
"$DIR/install.sh"

# Put bin/ on PATH. Idempotent: keyed on the exact line, so re-running this
# script never stacks up duplicates.
if [ -z "${ROMP_NO_PATH:-}" ]; then
    case "$(basename "${SHELL:-}")" in
        zsh)  rc="$HOME/.zshrc";  line="export PATH=\"\$PATH:$DIR/bin\"" ;;
        bash) if [ -f "$HOME/.bashrc" ]; then rc="$HOME/.bashrc"
              else rc="$HOME/.bash_profile"; fi
              line="export PATH=\"\$PATH:$DIR/bin\"" ;;
        fish) rc="$HOME/.config/fish/config.fish"; line="fish_add_path $DIR/bin" ;;
        *)    rc=""; line="export PATH=\"\$PATH:$DIR/bin\"" ;;
    esac
    if [ -n "$rc" ]; then
        mkdir -p "$(dirname "$rc")"
        if [ -f "$rc" ] && grep -qF "$DIR/bin" "$rc"; then
            echo "    PATH already set in $rc"
        else
            printf '\n# romp\n%s\n' "$line" >> "$rc"
            echo "    Added romp to your PATH in $rc"
        fi
    else
        echo "    Unknown shell. Add this to your shell rc yourself:"
        echo "      $line"
    fi
fi

echo
echo "romp is installed at $DIR"
echo "Open a new terminal (or 'source' your shell rc), then run:  romp"
