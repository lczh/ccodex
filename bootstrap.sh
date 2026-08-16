#!/usr/bin/env bash
# One-line install (ccodex — this fork; see README.md / docs/codex.md):
#   curl -fsSL https://raw.githubusercontent.com/lczh/ccodex/main/bootstrap.sh | bash
#
# Clones the repo, cryptographically verifies and checks out the requested release tag
# (newest stable vX.Y.Z release by default), runs install.sh, and puts bin/ on your PATH. The clone IS
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

# Pick the ref. Stable releases are exactly `vX.Y.Z`, so match that shape rather
# than taking the newest tag of any kind: the repo also carries non-release and
# prerelease tags, and the in-app updater intentionally ignores those too.
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
        [[ "$candidate" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
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
        echo "romp: no stable vX.Y.Z release tag is published; refusing to install an unverified branch." >&2
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
    echo "==> Verifying release signature for $ref"
    signature_ok=1
    if [ -n "$allowed_signers" ]; then
        git -C "$DIR" -c gpg.minTrustLevel=fully \
            -c "gpg.ssh.allowedSignersFile=$allowed_signers" \
            verify-tag "$ref" || signature_ok=0
    else
        git -C "$DIR" -c gpg.minTrustLevel=fully verify-tag "$ref" || signature_ok=0
    fi
    if [ "$signature_ok" -ne 1 ]; then
        echo "romp: release tag '$ref' does not have a valid signature trusted by git; refusing to install it." >&2
        echo "  Import the maintainer's GPG key or configure Git's SSH allowed-signers file, then rerun." >&2
        exit 1
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

if [ "$is_tag" -ne 1 ]; then
    echo "romp: UNSAFE DEVELOPMENT OVERRIDE: ROMP_REF='$ref' is not a release tag; its code is not signature-verified." >&2
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
