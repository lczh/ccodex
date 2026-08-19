<p align="center">
  <img src="docs/assets/brand/romp-wordmark.png" alt="romp" width="440">
</p>

# ccodex

**ccodex** runs your AI coding agents — **OpenAI Codex** and **Claude Code**, side by
side or either one alone — on one board. It is a fork of
[romp](https://github.com/romp-on/romp) that adds first-class Codex support: the agent
kind is picked per session, and a machine can run entirely on a Codex login, with no
Claude account at all. The command is `ccodex` (`romp` works too — ccodex is built on
romp, and everything below is romp's design, unchanged).

AI agents like Codex and Claude Code can work autonomously for long stretches, allowing
several to be run in parallel. But this parallelism creates new management work: tracking
what the agents are doing, scrolling through transcripts to find the background a
decision needs, and coordinating handoffs of work and context.

ccodex simplifies and automates this management: it tracks every agent and its tasks,
surfaces what needs your attention, and keeps them moving and working together. *You*
stay focused on what you're trying to accomplish.

<!-- Feature sections. Real captures live in docs/assets/guide/; docs/index.md embeds them as
     <video> MP4s, and this README embeds the same captures as GIFs so GitHub autoplays them. -->

### Every session, one view

The timeline shows every session: what's running, what's idle, and what needs you.

<img src="docs/assets/guide/every-session-timeline.gif" alt="Every session with its status in one view" width="100%">

### Task management

ccodex reads the agents' transcripts and tracks the work, so you don't have to.

<img src="docs/assets/guide/task-cards.gif" alt="Work grouped into task cards" width="100%">

A card shows each task with the essential information: why the work is happening, what the agent did, and the sub-tasks completed along the way.

<img src="docs/assets/guide/context-tabs.gif" alt="A card's tabs: the background, the summary, and the sub-tasks" width="100%">

### Coordination you can see

The postal service lets agents ask each other questions and hand off work.

<img src="docs/assets/guide/coordination.gif" alt="A message crossing between two sessions, on the timeline and in the chat" width="100%">

### Navigate anywhere, detail on demand

Find any work by when it happened on the timeline or by the task it belonged to, then open it for the full detail.

<img src="docs/assets/guide/navigate.gif" alt="Clicking a message to jump to its place, then opening a card for the full detail" width="100%">

### Every machine, one place

Sessions on your server appear alongside your laptop's, agents hand off work across machines, and you can view everything from a laptop or a phone.

<img src="docs/assets/guide/every-machine.png" alt="Sessions on two machines, gathered into one dashboard and the same view on a phone" width="100%">

ccodex works with Claude Code and Codex. It adds all of this on top of the sessions you already run, without changing how you work — whichever vendor they run on.

## Self-hosted, reachable from anywhere

You run ccodex yourself, on your laptop or a server, with no hosted service in between.

- **On your phone.** Reach the full dashboard over Tailscale.
- **Across machines.** Connect several over SSH: agents on different machines message each other, and you steer them all from one place.
- **In your editor or a browser.** Open it as a VS Code / Cursor extension or a plain browser tab.

## Quick start

Needs Python 3.10+, Node.js 22+, git — and at least one agent vendor: an OpenAI account for
Codex sessions, and/or [Claude Code](https://claude.com/claude-code) (signed in) for
Claude sessions.

```bash
curl -fsSL https://raw.githubusercontent.com/lczh/ccodex/main/bootstrap.sh | \
  ROMP_REPO=https://github.com/lczh/ccodex.git ROMP_DIR=$HOME/ccodex bash
```

This clones ccodex to `~/ccodex`, checks out the newest stable release (signed from
v1.2.2 onward), installs it, and adds `bin/` to your shell rc. With a release trust
root configured, verification is a hard gate; without one, it is attempted and its
outcome noted — pinning the published key is one documented step, recommended:
[Release signature trust](docs/install.md#release-signature-trust). Installed
machines then learn about new releases on their own — the dashboard offers each
update as a one-click banner (or installs it automatically; the gear's **Updates**
setting decides). Then, in a new terminal:

```bash
ccodex-setup           # provision the Codex backend (a pinned venv; never your system Python)
codex login            # or: codex login --device-auth on a headless box
ccodex engine codex    # optional — run EVERYTHING on the Codex login (skip if you also use Claude Code)
ccodex                 # opens the dashboard; start a session from there
```

The gate deliberately does not fall back to unsigned `main`, a prerelease, or an older
release.

By default ccodex's own intelligence — the judges that caption work and route your
attention — runs on Claude, so a machine without Claude Code should run
`ccodex engine codex` as above. Codex specifics — sandboxing, the judge engine, what's
supported today — are in [docs/codex.md](docs/codex.md).

## Docs

[docs/codex.md](docs/codex.md) covers everything Codex-specific. For the rest — remote
hosts, a guide to each capability, and how it all works under the hood — upstream romp's
[documentation](https://romp-on.github.io/romp/) applies to ccodex unchanged (source in
[`docs/`](docs/)); read `romp` there as `ccodex` here.

## License

[Apache-2.0](LICENSE). ccodex is a fork of [romp](https://github.com/romp-on/romp); the
romp name, wordmark, and upstream documentation belong to that project.
