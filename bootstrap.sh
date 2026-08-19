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

# ── the MOVE + INSTALL are one transaction (the user's audit, 2026-08-18) ────────────────────
# A bootstrap re-run used to check out new code and run install.sh with no update lock and no
# recovery latch: a crash mid-install, or a concurrent in-app updater, left an unrecorded partial
# install — the exact hole every other update path closes. The runner below takes the checkout's
# own romp-update.lock (bounded wait), arms the latch with the TARGET commit before anything
# moves, executes the move, runs install.sh with the lock fd riding along, and spends the latch
# only on success. A failed install leaves the latch armed: romp-serve's gate and the kernel's
# boot heal refuse to run the build until install passes.

# Resolve the channel BEFORE the move (it needs only $ref/$is_tag); its WRITE lands after the
# locked transaction succeeds — decisive state must not change outside it (the user's audit,
# 2026-08-19), and a failed install keeps the OLD install's channel.
channel="stable"
# dev means the MAIN BRANCH opt-in in any spelling (main / refs/heads/main / origin/main) — and
# never a TAG that happens to be named main: a tag install is a pinned, verified artifact
# whatever its name, and a branch install under another spelling is still main-tracking
# (the adversarial review, 2026-08-17).
if [ "$is_tag" -eq 0 ]; then
    case "$ref" in main|refs/heads/main|origin/main) channel="dev" ;; esac
fi

precheck="-"
if [ "$is_tag" -eq 1 ]; then
    # Address the tag by its full ref and detach. If a remote branch has the same short name,
    # installing that branch after verifying the tag would install different, unsigned code.
    target="$(git -C "$DIR" rev-parse "refs/tags/$ref^{commit}")" || {
        echo "romp: verified tag '$ref' could not be resolved to a commit." >&2; exit 1; }
    set -- checkout --quiet --detach "refs/tags/$ref"
elif git -C "$DIR" show-ref --verify --quiet "refs/remotes/origin/$ref"; then
    # Branch installs follow exactly the fetched remote branch. Never suppress a non-fast-forward:
    # doing so used to reinstall stale local code while announcing a successful update.
    target="$(git -C "$DIR" rev-parse "refs/remotes/origin/$ref")" || {
        echo "romp: origin/$ref could not be resolved to a commit." >&2; exit 1; }
    # LATCH-COHERENT move order (the adversarial review, 2026-08-18: checkout-then-ff-merge moved
    # HEAD to the stale local tip and a failed merge then erased the latch as "nothing moved").
    # The ancestry check runs FIRST (read-only; a diverged branch refuses before anything moves),
    # then HEAD detaches onto the exact target — so from the first move onward the armed latch
    # matches HEAD — and only then does the local branch label catch up.
    precheck="-"
    if git -C "$DIR" show-ref --verify --quiet "refs/heads/$ref"; then
        precheck="refs/heads/$ref"
        if ! git -C "$DIR" merge-base --is-ancestor "$precheck" "$target"; then
            echo "romp: local branch $ref cannot fast-forward to origin/$ref; refusing to install stale code." >&2
            echo "  Resolve or preserve the local commits, then rerun bootstrap.sh." >&2
            exit 1
        fi
    fi
    set -- checkout --quiet --detach "$target" -- branch --quiet -f "$ref" "$target" -- checkout --quiet "$ref"
else
    # Tags and explicit commit IDs are immutable checkouts; pulling them is neither useful nor valid.
    target="$(git -C "$DIR" rev-parse "$ref^{commit}" 2>/dev/null)" || {
        echo "romp: ref '$ref' was not found after fetching origin." >&2; exit 1; }
    set -- checkout --quiet "$ref"
fi

gd="$(git -C "$DIR" rev-parse --absolute-git-dir)" || {
    echo "romp: could not resolve the clone's git dir for the update transaction." >&2; exit 1; }
echo "==> Checking out $ref + installing (one locked transaction)"
txn_rc=0
python3 - "$DIR" "$gd" "$target" "$precheck" "$channel" "$@" <<'TXNPY' || txn_rc=$?
import fcntl, os, subprocess, sys, time

root, gdir, target, precheck, channel = (sys.argv[1], sys.argv[2], sys.argv[3],
                                          sys.argv[4], sys.argv[5])
moves, cur = [], []
for tok in sys.argv[6:]:
    if tok == "--":
        moves.append(cur); cur = []
    else:
        cur.append(tok)
if cur:
    moves.append(cur)
fd = os.open(os.path.join(gdir, "romp-update.lock"), os.O_RDWR | os.O_CREAT, 0o644)
deadline = time.time() + float(os.environ.get("ROMP_TXN_LOCK_WAIT", "120"))
while True:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        break
    except OSError:
        if time.time() >= deadline:
            sys.exit(3)
        time.sleep(0.5)
# UNDER the lock: ancestry decided here (the read-only pre-check raced a concurrent updater
# moving refs — the user's audit, 2026-08-18), and a PRIOR latch is SETTLED, never overwritten
# (heal a matching one by running install; clear a moot one).
if precheck != "-":
    if subprocess.run(["git", "-C", root, "merge-base", "--is-ancestor", precheck, target]).returncode:
        sys.exit(7)
latch = os.path.join(gdir, "romp-install-failed")


def head8():
    r = subprocess.run(["git", "-C", root, "rev-parse", "--short=8", "HEAD"],
                       capture_output=True, text=True)
    return (r.stdout or "").strip()[:8] if r.returncode == 0 else ""


def write_latch(sha8):
    tmp = latch + ".tmp"
    with open(tmp, "w") as f:
        f.write(sha8)
    os.replace(tmp, latch)


try:
    lines = [ln.strip()[:8] for ln in open(latch).read().splitlines() if ln.strip()]
except FileNotFoundError:
    lines = []
except OSError:
    sys.exit(3)                              # an EXISTING record we cannot read is UNKNOWN, never absent
pre_head = head8()
carry = ""
if lines:
    if not pre_head:
        sys.exit(3)                          # can't settle a prior record against an unreadable HEAD
    if pre_head in lines:
        # heal-first, but a heal that still fails CARRIES the record into the new arm instead of
        # blocking the very update that may fix install.sh (the adversarial review, 2026-08-19)
        if subprocess.run(["bash", os.path.join(root, "install.sh")], cwd=root,
                          pass_fds=(fd,)).returncode:
            carry = pre_head
        else:
            os.remove(latch)
    elif len(lines) > 1:
        sys.exit(10)                         # died mid-move from a broken state: heal by hand
    else:
        os.remove(latch)                     # intent-only mismatch: the move never landed
# the channel marker is STAGED here — content written to the temp before anything moves, so
# the only marker step left after the moves is one atomic rename. A failure here costs nothing:
# HEAD is unmoved, no latch is armed, the old marker still matches the old HEAD (rc 11).
ctmp = os.path.join(gdir, "romp-update-channel.tmp")
try:
    with open(ctmp, "w") as cf:
        cf.write(channel + "\n")
except OSError:
    sys.exit(11)


def stuck_latch():
    """Quarantine: two NON-HEX lines no checkout sha can ever match, so every reader (boot heal,
    the gate, settle — this script's own included) takes its fail-closed heal-by-hand branch: a
    single-line latch matching HEAD is an AUTO-RUN trigger, and the healers know nothing about
    the stale channel marker they would be reviving the build under (the adversarial review,
    2026-08-19, reproduced against the gate and the boot heal; hex sentinels were MINABLE by a
    peer that controls its commit — same review). Returns False only when even the in-place
    rewrite failed: the atomic tmp+rename needs new blocks, but rewriting the EXISTING armed
    latch in place survives ENOSPC, the common dynamic failure — and we hold the update flock,
    which every reader takes before reading, so the non-atomic write races nobody. A silent
    best-effort here left the armed auto-run latch behind an exit that claimed quarantine."""
    try:
        write_latch("quarantined\nquarantined")
        return True
    except OSError:
        try:
            # UNBUFFERED pwrite, never open(.., "w"): O_TRUNC erased the armed record at open,
            # and a failed buffered flush then left the latch EMPTY — which every reader
            # moot-removes, serving the moved build with install.sh never run (the adversarial
            # review, 2026-08-19, reproduced). pwrite either lands whole or raises with the
            # armed bytes still on disk; 23 bytes always covers the <=17-byte armed record.
            qfd = os.open(latch, os.O_RDWR)
            try:
                qb = b"quarantined\nquarantined"
                if os.pwrite(qfd, qb, 0) != len(qb):
                    raise OSError("short quarantine write")
            finally:
                os.close(qfd)
            return True
        except OSError:
            return False                         # the armed target latch stays (pwrite cannot
            #                                      truncate): the caller says so instead of
            #                                      claiming quarantine


try:
    write_latch(target[:8] + ("\n" + carry if carry and carry != target[:8] else ""))
except OSError:
    sys.exit(5)
for mv in moves:
    if subprocess.run(["git", "-C", root] + mv).returncode:
        # A failed move must leave an HONEST latch (the adversarial review, 2026-08-18: an
        # unconditional remove erased the record after a step that DID move HEAD, and erased a
        # pre-existing latch protecting a half-installed build). HEAD unmoved → restore what was
        # there before; HEAD moved → arm for wherever we actually landed.
        now = head8()
        if now == target[:8] and now != pre_head:
            # HEAD MOVED to the target before a LATER move failed (branch -f, symbolic-ref): the
            # marker must follow it — the latch below matches HEAD, so the ordinary healers will
            # revive this build, and reviving it under the OLD channel marker is the stale-dev-
            # marker-on-a-stable-checkout hole (the adversarial review, 2026-08-19, reproduced).
            # `now != pre_head` is load-bearing: a checkout ALREADY at the target whose later
            # move failed ends with no latch and nothing installed — publishing there flipped a
            # stable checkout's marker to dev on a FAILED bootstrap (same review, reproduced).
            try:
                os.replace(ctmp, os.path.join(gdir, "romp-update-channel"))
            except OSError:
                sys.exit(12 if stuck_latch() else 13)
        try:
            if now and now != pre_head:
                write_latch(now + ("\n" + carry if carry and carry != now else ""))
            elif carry:
                write_latch(carry)           # HEAD unmoved: the carried prior record returns
            else:
                os.remove(latch)
        except OSError:
            pass                             # the armed target latch stays — fail closed
        sys.exit(6)
# the CHANNEL marker is PUBLISHED here — INSIDE the lock (the user's audit, 2026-08-19: the
# shell wrote it after the lock released, so two serial bootstraps could publish their markers
# in reverse order), AFTER the moves and BEFORE install.sh: the marker describes what HEAD now
# IS, so a build that fails install and is later healed (the heal runs only install.sh) comes up
# wearing ITS OWN channel. The content was staged pre-move; all that remains is one atomic rename.
try:
    os.replace(ctmp, os.path.join(gdir, "romp-update-channel"))
except OSError:
    sys.exit(12 if stuck_latch() else 13)   # moved + unrecordable channel: quarantined — the
    #                                         armed single-line latch would be auto-healed into
    #                                         a build wearing the OLD marker (a stable checkout
    #                                         following unsigned main); 13 = even the quarantine
    #                                         could not be recorded, and the exit says so
env = dict(os.environ, ROMP_INSIDE_UPDATE_TXN=str(os.getpid()))
if subprocess.run(["bash", os.path.join(root, "install.sh")], cwd=root, pass_fds=(fd,),
                  env=env).returncode:
    sys.exit(4)                        # latch stays armed: nothing runs this build until install passes
os.remove(latch)
sys.exit(0)
TXNPY
case "$txn_rc" in
    0)
       # the marker itself was written INSIDE the locked transaction (see TXNPY); only the legacy
       # git-config unset and the say-so remain out here
       git -C "$DIR" config --unset romp.updateChannel 2>/dev/null || true
       echo "==> Update channel: $channel"
       ;;
    3) echo "romp: another update holds this checkout's lock (or HEAD is unreadable) — try again when it finishes." >&2; exit 1 ;;
    7) echo "romp: local branch $ref cannot fast-forward to origin/$ref (it moved while waiting for the lock)." >&2
       echo "  Resolve or preserve the local commits, then rerun bootstrap.sh." >&2; exit 1 ;;
    5) echo "romp: could not record the install intent in $gd — not moving the checkout." >&2; exit 1 ;;
    6) echo "romp: the checkout could not be moved to $ref — most often the local branch cannot fast-forward" >&2
       echo "  to origin/$ref (see git's message above). Resolve or preserve the local commits, then rerun." >&2
       echo "  Nothing was installed." >&2; exit 1 ;;
    10) echo "romp: this checkout's install latch names commits HEAD doesn't match — an update died" >&2
        echo "  mid-move from a broken state. Heal by hand: run install.sh, then remove the latch" >&2
        echo "  file in the clone's git dir, and rerun bootstrap." >&2; exit 1 ;;
    11) echo "romp: could not stage the update-channel record in $gd — nothing was changed." >&2
        echo "  Fix the git dir's permissions/space and rerun bootstrap." >&2
        exit 1 ;;
    12) echo "romp: the checkout moved but the update channel could not be recorded in $gd —" >&2
        echo "  the install is quarantined so nothing revives this build under the OLD channel." >&2
        echo "  Heal by hand: remove $gd/romp-install-failed, then rerun bootstrap.sh." >&2
        exit 1 ;;
    13) echo "romp: the checkout moved, the update channel could not be recorded in $gd, AND the" >&2
        echo "  quarantine record could not be written — assume this build can revive under the" >&2
        echo "  OLD channel. Fix the git dir (space/permissions) and rerun bootstrap.sh NOW." >&2
        exit 1 ;;
    4) echo "romp: install.sh failed AFTER the checkout moved — the install latch is armed, so" >&2
       echo "  romp will refuse to run this build until a re-run of bootstrap or its boot heal" >&2
       echo "  gets install.sh to pass." >&2; exit 1 ;;
    *) echo "romp: the install transaction failed (rc=$txn_rc)." >&2; exit 1 ;;
esac

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
