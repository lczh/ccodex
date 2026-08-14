<p align="center">
  <img src="docs/assets/brand/romp-wordmark.png" alt="romp" width="440">
</p>

> **This is ccodex** — a fork of [romp](https://github.com/romp-on/romp) that runs
> **OpenAI Codex** agents, alongside or instead of Claude Code: same board, cards,
> chat, and coordination, with the agent kind picked per session — including running
> entirely on a Codex login, no Claude required. Install and setup live in
> [docs/codex.md](docs/codex.md). The command is `ccodex` (`romp` works too: ccodex
> is built on romp, and everything below is the upstream project's documentation).

AI agents like Claude Code can work autonomously for long stretches, allowing several to be run in parallel. But this parallelism creates new management work: tracking what the agents are doing, scrolling through transcripts to find the background a decision needs, and coordinating handoffs of work and context.

Romp simplifies and automates this management: it tracks every agent and its tasks, surfaces what needs your attention, and keeps them moving and working together. *You* stay focused on what you're trying to accomplish.

<!-- Feature sections. Real captures live in docs/assets/guide/; docs/index.md embeds them as
     <video> MP4s, and this README embeds the same captures as GIFs so GitHub autoplays them. -->

### Every session, one view

The timeline shows every session: what's running, what's idle, and what needs you.

<img src="docs/assets/guide/every-session-timeline.gif" alt="Every session with its status in one view" width="100%">

### Task management

Romp reads the agents' transcripts and tracks the work, so you don't have to.

<img src="docs/assets/guide/task-cards.gif" alt="Work grouped into task cards" width="100%">

A card shows each task with the essential information: why the work is happening, what the agent did, and the sub-tasks completed along the way.

<img src="docs/assets/guide/context-tabs.gif" alt="A card's tabs: the background, the summary, and the sub-tasks" width="100%">

### Coordination you can see

The Romp Postal Service lets agents ask each other questions and hand off work.

<img src="docs/assets/guide/coordination.gif" alt="A message crossing between two sessions, on the timeline and in the chat" width="100%">

### Navigate anywhere, detail on demand

Find any work by when it happened on the timeline or by the task it belonged to, then open it for the full detail.

<img src="docs/assets/guide/navigate.gif" alt="Clicking a message to jump to its place, then opening a card for the full detail" width="100%">

Romp works with Claude Code today. It adds all of this on top of the sessions you already run, without changing how you work.

## Self-hosted, reachable from anywhere

You run Romp yourself, on your laptop or a server, with no hosted service in between.

- **On your phone.** Reach the full dashboard over Tailscale.
- **Across machines.** Connect several over SSH: agents on different machines message each other, and you steer them all from one place.
- **In your editor or a browser.** Open it as a VS Code / Cursor extension or a plain browser tab.

## Quick start

Needs [Claude Code](https://claude.com/claude-code) (signed in), Python 3.10+, and Node.js.

```bash
curl -fsSL https://raw.githubusercontent.com/romp-on/romp/main/bootstrap.sh | bash
```

This clones Romp to `~/romp`, checks out the newest release, installs it, and adds `bin/` to your shell rc. Open a new terminal afterwards. [Installing by hand](https://romp-on.github.io/romp/install/#manual-and-custom-installs) works too.

In that terminal, run `romp`. It opens the dashboard in your browser and prints
the link as well, so a machine with no browser of its own still gives you a way
in. Start a session from there.

## Docs

Requirements, remote-host setup, a guide to each capability, and how Romp works under the hood are in the [documentation](https://romp-on.github.io/romp/) (source in [`docs/`](docs/)).

## License

[Apache-2.0](LICENSE).
