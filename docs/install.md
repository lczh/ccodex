# Install

## Requirements

- **Python 3.10 or newer, Node.js 22 or newer, and git.**
- At least one agent backend: an OpenAI login for Codex and/or
  **[Claude Code](https://claude.com/claude-code)**, signed in.

    ```bash
    brew install python node git           # macOS (Homebrew)
    # Linux: install Python/git from the distro and Node.js 22+ from nodejs.org
    ```

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/lczh/ccodex/main/bootstrap.sh | bash
```

Open a new terminal afterwards, so `~/ccodex/bin` is on your `PATH`, and type
`ccodex` to launch the user interface in a browser.

The same command updates ccodex later. To remove it, run `ccodex uninstall` (add
`--purge` to delete recorded sessions too).

This clones ccodex to `~/ccodex`, cryptographically verifies the newest release tag,
and installs it. If the newest tag is unsigned, invalid, or signed by a key Git does not
trust, installation stops; it never silently falls back to `main` or an older tag.
[What it installs, in detail](architecture.md#what-the-installer-sets-up).

### Release signature trust

The installer and in-app updater use `git verify-tag`. Git therefore needs the release
signer's public key before installation:

- For an OpenPGP signature, import the maintainer's published public key into the GPG
  keyring Git uses, confirm its fingerprint through a trusted channel, and certify that key
  at **full** trust. ccodex invokes Git with `gpg.minTrustLevel=fully`; merely importing an
  otherwise-unknown key is intentionally insufficient. Git accepts any key that the configured
  GPG trust database evaluates at that level; use a narrowly scoped keyring or SSH allowed-signers
  when the verifier must be pinned to exactly one release key.
- For an SSH signature, put the maintainer's published SSH signing key in an
  [allowed-signers file](https://git-scm.com/docs/git-config#Documentation/git-config.txt-gpgsshallowedSignersFile).
  Configure it globally with `git config --global gpg.ssh.allowedSignersFile /absolute/path`,
  or pass the same absolute path as `ROMP_RELEASE_ALLOWED_SIGNERS` to bootstrap. After a
  successful verification, bootstrap saves that absolute path in the clone's local Git
  configuration so the long-lived in-app updater uses the identical trust root.

The signer key or fingerprint must be distributed independently of the release tag being
checked; trusting a key supplied only by an unverified tag would defeat the check. Custom
`ROMP_REPO` installations work the same way, using that repository maintainer's trusted key.

`v1.0.0` was created before signed releases were required and is unsigned. A new signed
release is required after the signature-gate change merges. This is an intentional migration
break: do not disable verification or select an older tag to make the default installer pass.

### Manual and custom installs

Install this way to keep ccodex somewhere other than `~/ccodex`, or to run the
latest commit rather than the newest release:

```bash
git clone https://github.com/lczh/ccodex.git ~/ccodex
cd ~/ccodex
tag="$(git tag -l 'v*' --sort=-v:refname | head -n1)"
git verify-tag "$tag"                                           # must succeed
git checkout --detach "refs/tags/$tag"                          # newest verified release
# or:   git checkout main                                        # the latest commit
./install.sh
```

Then add `bin/` to your `PATH` in your shell rc; `install.sh` prints the exact
line for your clone.

```bash
export PATH="$PATH:$HOME/ccodex/bin"
```

## First run

The installer leaves ccodex's back end running, so there is nothing to start. For a
Codex backend, run `ccodex-setup` once and then `codex login`; on a machine without
Claude Code, also run `ccodex engine codex`. Open the dashboard with `ccodex`.

### In VS Code or Cursor

The installer adds the extension automatically. Reload your editor window and
open ccodex from the sidebar using **ccodex: Open** (the extension id remains
`romp.romp-chat-view` for upgrade compatibility).

### Start a session

<video src="../assets/guide/first-session.mp4" controls loop muted playsinline preload="none" data-romp-autoplay width="100%"></video>
