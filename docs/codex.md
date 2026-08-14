# Codex sessions

Romp can run **OpenAI Codex** agents alongside Claude Code sessions — same board,
same cards, chat, timeline, and postal bus. A Codex session is just another kind
of session: pick **Codex** in the new-session dialog's Backend toggle (or set it
as the gear's Default backend).

Under the hood, romp drives Codex through the official `codex app-server`
protocol and materializes each thread as a transcript romp's read side already
understands — design in `plans/codex-backend.md`.

## Setup

Romp itself still needs Claude Code signed in (romp's judges — the card
summaries and attention routing — run on Claude regardless of what kind of
agent a session runs). On top of that, Codex sessions need, once per machine:

1. **The Codex SDK for romp:**

        romp-codex-setup

    This provisions a dedicated, pinned venv under romp's state dir (it never
    touches your system Python) and bundles the Codex binary.

2. **A Codex login:**

        codex login          # or: codex login --device-auth on a headless box

    Codex sessions bill the ChatGPT plan (or API key) this login carries.

If either is missing, creating a Codex session tells you exactly what to run —
romp never silently substitutes a different backend.

## Sandboxing

Codex runs its commands inside its own Linux sandbox (bubblewrap), in
`workspace-write` mode: the session can edit its working directory and reach
the network, and nothing else. Two host notes:

- The sandbox needs **unprivileged user namespaces**. Some images (notably
  newer GCP Ubuntu) restrict them; every command then fails visibly with
  `bwrap: … Permission denied`. To enable:

        sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0

    (persist it in `/etc/sysctl.d/` to survive reboots).

- There is no permission-prompt flow yet (approvals are pinned off; the
  sandbox is the guardrail). Codex approval prompts surfacing as romp's
  needs-you cards is planned.

## What works, what's coming

Working today: lanes and status, task cards and judging, full chat (prompts,
replies, thinking, commands, file diffs, web searches), steering a running
turn, interrupts, model and reasoning-effort switches, resume after restarts,
and postal delivery into Codex sessions.

Not yet (tracked in `plans/codex-backend.md`): approval prompts as needs-you
cards, Codex plan items on the card checklist, subagent lanes, rate-limit
gating, and MCP server management from the dashboard (Codex sessions read MCP
servers from `~/.codex/config.toml`).

## Installing romp-codex

This repo (`romp-codex`) is the distribution: romp's installer takes any repo
and ref, so it installs with the standard one-liner plus two variables:

    curl -fsSL https://raw.githubusercontent.com/lczh/romp-codex/main/bootstrap.sh | \
      ROMP_REPO=https://github.com/lczh/romp-codex.git ROMP_REF=main bash

Then run the two setup steps above. (The clone lands at `~/romp` by default —
set `ROMP_DIR=$HOME/romp-codex` in the same command if you'd rather it match
the repo name.)
