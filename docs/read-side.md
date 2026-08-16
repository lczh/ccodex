# The read side: the kernel, the UI, and the three panes

!!! note "Optional reading"
    You don't need any of this to use Romp.
    Describes the system as of **2026-07-24**; the behaviour it documents moves,
    so treat anything here as a snapshot rather than a contract.

Architecture deep dive. Layer 3 turns the records written by the event model
(`event-model.md`) and the summarizer layer (`docs/judges.md`) into the three
web-UI panes you look at: the **feed**, the **chat**, and the **timeline**.

## The governing principle

**Layer 3 derives no meaning. All meaning is computed below it (almost all in
Layer 2); the read side only selects and displays.** The judges write durable
meaning once; the panes are thin projections of it.

A direct consequence: the completion **rollup + "settled" gate** lives in
Layer 2. The producer publishes each goal's rolled-up status (working / blocked /
completed); the feed just paints columns. (Reflected in `docs/judges.md`.)

## The kernel and its clients

- **The kernel is the core, supervised by `romp-manager`.** One process: Layer 1
  (parse) + Layer 2 (the judges) **and** an HTTP server, single writer. Its
  *lifecycle* is owned by **`romp-manager`** — a durable, jupyter-lab-style
  supervisor, started by the login service that `install.sh` sets up, that spawns
  and respawns the kernel (via `romp-serve` → `romp-kernel`) and stays up across
  kernel restarts. Front ends
  (browser, phone, VS Code) ATTACH to the kernel; they never spawn it.
  `romp-serve` points at the Python kernel, so `romp up` supervises it on the
  manager's port (29855), and the front ends and tailscale serve attach unchanged.
- **The UI is served by the kernel.** The front-end (the three panes) is `ui/`. A
  browser hits the kernel's port and gets it.
- **The login service starts the supervisor**; `romp up` instead runs
  `romp-manager` in the foreground (like `jupyter lab`), for machines without the
  service or for watching it work. `romp refresh` restarts the kernel(s),
  `romp status` reports them. The kernel binds loopback only; tailnet/phone reach is
  `tailscale serve` proxying to `127.0.0.1:29855` (there is no `0.0.0.0` opt-in
  door; the tailscale proxy carries the phone path). The UI itself is just a URL
  the kernel serves.
- **Session discovery keys on the rompUuid birth stamp, not a time window.** The
  kernel discovers only sessions carrying the rompUuid birth stamp the launcher
  writes — the same rompUuid registry `event-model.md` defers, which also powers
  session→files stitching. A 48h window is allowed only as a perf bound on how far
  back to parse, never as the eligibility test (a time window is a fragile proxy
  and a banned time-heuristic). The kernel does not read or migrate the legacy
  record stores (`summaries/`, `requests/`, `decision-log`, `corrections/`,
  `digest/`), so old data cannot pollute the new model. Pre-rebuild sessions
  simply never appear: no resume-picker entry, no scrollback over old history.
- **The VS Code extension is a thin client.** It speaks the kernel's WebSocket
  protocol (`vscode-extension/src/extension.ts`: "a THIN CLIENT of the romp web
  kernel", `ws://HOST:kernelPort()/ws?app=...`). Browser and extension render the
  **same served UI** over the **same protocol**, so the two front ends stay
  consistent by construction. Keeping the WS protocol stable is the compatibility
  contract.
- **Both judge tiers run continuously for any live session — no connection gate.**
  The kernel runs the index tier (captioner + archiver) AND the triage tier
  (planner → closer → courier → grouper → consolidator → distiller) in parallel,
  on a short event-driven backstop, whether or not a browser is attached — so the
  goal tree, feed, and timeline are already current the instant a client connects.
  A pass is cheap when nothing changed (cached parses; each judge makes an LLM call
  only on real new work), so always-on costs filesystem stats, not model calls,
  when idle. The tiers are a cost/value GROUPING (see `docs/judges.md`), not a
  runtime gate. Single process means single writer for free, no matter how many
  tabs are open.
- **Views are URL-hash tag selections.** `localhost:PORT/#work,personal` is one
  view; `#work` is another. Each browser tab is an independent view over the same
  kernel, ephemeral, zero-config, many at once. A saved default can live behind the
  settings gear.
- **Port is a per-machine config.** One fixed port the kernel binds at startup.
- **Liveness collapses to three states** (+ the user's `cleared`): **working**
  (open, nothing for you to do — including delegated work and waiting on a
  non-user trigger), **blocked** (needs *you*), **completed**. They map 1:1 onto
  the three feed columns.
- **`blocked` has a deterministic floor the judge cannot override.** A live
  permission / decision prompt is a fact, not a judgment. `blocked = hard OR soft`,
  hard wins: the planner's output can never clear a hard block. This is a merge
  rule, not a prompt instruction — the judge is told nothing special; its verdict
  simply never removes a hard block. Hard and soft rarely collide in time (the
  planner runs on *ended* segments; a live prompt sits on an *open* turn).
- **The read side has two inputs: durable judge records + a thin real-time
  live-state read.** Live state (the chip, the timeline stripes, the hard-block
  floor, "is a session mid-turn right now") comes from `states/<sid>.jsonl` + the
  event tree's open turn. It is deterministic and mechanical, not meaning-logic, so
  it does not violate the principle; it is the one thing that cannot be precomputed
  into a record because it is about *right now*.
- **Comms scope is directory-based, group-wide, alive-gated** (a design sketch —
  see below). A separate axis from view tags.
- **Tags are directory-derived, overridable.** A `directory → tag` map auto-tags a
  session at launch; a per-session manual override handles "get this out of work."
- **Hard data isolation is a separate `ROMP_STATE_DIR` root, manual, rare.** Default is
  one shared root (so views can overlap). Point a kernel at another root only when
  you genuinely need segregated data (a dedicated machine). It is free, because the
  root is already a parameter.

## The runtime picture

```
THE KERNEL  (one always-on process, single writer)
  Layer 1   parse transcripts → event tree
  Layer 2   index tier  (captioner + archiver)                      ALWAYS
            triage tier (planner/closer/courier/grouper/distiller)  ALWAYS (no connection gate)
  HTTP/WS   serve the UI + push pane payloads
  writes →  ~/.local/state/romp/   (the interface)

ROMP POSTAL SERVICE  (always-on infra, below Layer 2)  delivery never waits on a judge

CLIENTS  (0..N, pure readers, render only)
  browser tab(s)      ── each a view (URL-hash tags)
  VS Code extension   ── same WS protocol
```

The records dir is the only interface. The kernel is the only writer. Clients only
read and render. This is the producer/consumer split the whole system rests on,
collapsed into a single process for the common (one-machine) case while preserving
single-writer.

### How mail reaches a session

The bus stores mail per recipient (a Maildir) and the kernel owns the wake
(`POST /deliver`). Three delivery legs, chosen by what the recipient is:

1. **SDK session** — the kernel enqueues the banner on the session's SDK input
   queue. First-class, nothing to scrape.
2. **tmux session on Claude Code ≥ 2.1.224** — one JSON user record written to
   the session's **inbox socket**, the per-session Unix socket the CLI binds and
   registers (with its session id) in `~/.claude/sessions/<pid>.json`. The
   kernel joins that registry on the session's current transcript id
   (`lastSid`), connects, writes one line, done: instant, wakes an idle session,
   delivers between tool calls mid-turn, and never touches the composer, so a
   half-typed draft survives with no stash dance. The CLI treats socket arrivals
   as another-session traffic; its inbound gate can HOLD mail from an
   unverifiable sender (the kernel is one) when the session runs a bypass-class
   permission mode, and a held message silently expires after ~5 minutes — and
   the socket acks nothing, so a hold would read as delivered. That is why
   `bin/romp` launches sessions with the CLI's inbound-accept setting
   (`crossSessionInbound: accept`) and tags them `@romp-inbound-accept`, and the
   kernel takes this leg ONLY for tagged sessions: the tag is written by the
   same launch that made holds impossible, so tag and setting can never
   disagree. Security shape: the socket is owner-only (0600 inside a 0700 dir),
   unreachable from off-machine and from other local users; a same-user process
   could already type into any pane via `tmux send-keys` with FULL user
   authority, while socket mail arrives explicitly labeled as peer traffic with
   approval power stripped — the lower-privilege injection path of the two.
3. **Everything else** (older CLI, untagged launch, socket gone) —
   draft-preserving pane injection at a live ❯ prompt, with the Stop-hook drain
   (`hooks/romp-postal-drain.sh`) as the turn-boundary backstop. Unchanged, and
   still the fallback whenever leg 2 fails for any reason: non-delivery is
   caught by the maildir claim/retry and stuck-mail warnings either way.

## The two inputs

1. **Durable judge records** (`docs/judges.md` writes these):
   - **captions** — per segment and per turn, keyed by id. The activity log.
   - **the goal tree** — nodes + edges + per-node and rolled-up status. The inbox.
   - **courier records** — handoff (propagating / FYI) + which sender goal, keyed by
     message/segment id. The cross-session edges.
   - **archive** — per session, keyed by rompUuid: a sub-sentence **headline** + a
     2-3 sentence **abstract**, summarized from the session's captions (cheap
     input), continuously refreshed as the session gains turns. The index + the
     TOC header.
2. **A thin real-time live-state read**: `states/<sid>.jsonl` (working / permission /
   compacting / idle / closed transitions) + the event tree's open turn. Drives the
   chip, the timeline stripes, the hard-block floor, and the mid-turn pulse.

## The three panes (each a thin projection)

Chat is a zoom into Layer 1; the feed is a zoom into Layer 2; the timeline is the
bridge that shows Layer 1 spatially with Layer 2 labels.

### Chat = the event tree, rendered directly

Per-session tabs. Renders the event tree at the Atom / ContentBlock level (one
widget per block), with **no second transcript parser** — the event model already
produced the tree. Plus the live chip from the state read, and the TOC ledger below
the tabs.

**The ledger is a table of contents** (pure projection of captions + archive):
- top: the archiver's one-sentence headline for the session,
- then **turn captions** as top-level bullets, the whole session (not just recent),
- a multi-segment turn expands to its **segment captions** indented beneath,
- click any line to jump to that point in the transcript.

The captioner emits both grains and the event model gives the turn→segment nesting,
so the TOC is free. (Caveat: a live permission prompt's *content* may exist only in
tmux, not the transcript; a live AskUserQuestion/ExitPlanMode is in the tree as an
unanswered tool_use. The chip state comes from `states/` regardless.)

### Feed = top-level-goal cards, nothing else

**The only cards are top-level goals.** One card per top-level goal, bucketed into
the three columns by the rolled-up status the producer already wrote (working /
blocked / completed). A sub-goal never gets its own card: a block anywhere in the
tree rolls UP, so the *top-level card* moves to BLOCKED and its modal shows which
leaf is blocking; likewise a completed step shows inside the modal, not as its own
Completed card. No read-time DAG rebuild, no status derivation, no handoff repair.

- A card's modal shows the goal's trail (its filed segments + sub-goal tree,
  interleaved).
- **No caption stream.** Turn/segment captions are NOT feed cards — they live in the
  card's trail, the ledger, and the timeline. The rule lives in `build_feed`.
- **Card detail**: a card shows its caption trail; a richer expand view is parked.
- **Clear-all + undo**: a button retires every currently-open top-level card at
  once (batch `cleared`); an **undo** restores that batch if invoked right after.
  For sweeping away a stale backlog you know you don't care about.

### Timeline = segments as bars, with connectors and overlays

- **Lanes**: one per session; each **segment** is a bar `[t, end]` (segments are
  exactly "what the timeline draws as a bar" in the event model), a dot at the
  trigger, idle atoms as the not-working gaps, caption on hover.
- **Stripes**: needs-input (a live permission/picker prompt) / compacting, from the state read.
- **Connectors**: postal messages between lanes, from courier records / the message
  log.
- **Overlays**: focus / hover from the feed and chat (UI ephemera, one WS channel).
- Reads **segments straight from the event model**.
- The timeline lives **in `ui/`** next to chat and feed, sharing one view-builder,
  one bundle, one set of types.

## One view-builder

A single read library (TS, in `ui/`) of pure functions `records → ChatView |
FeedView | TimelineView`. One implementation, because there is one front end.

## What stays in the read side

The read side does no meaning-work: no DAG rebuild, no status derivation, no
handoff classification or repair. Those live in Layer 1/2 — the planner un-blocks
via newest-wins, origin is `trigger.author`, the courier classifies handoffs at
write time, captions are keyed by id upstream. The completion rollup, column
derivation, and the settled gate live in Layer 2, leaving the feed to read status.
Recency fade is the one display-only heuristic the read side keeps.

## Comms scope (directory groups, alive-gated)

**Design sketch — not shipped.** What ships today is live-name addressing with
per-host trust tiers (see `SECURITY.md`); the directory-group gate below has not
been built.

At the postal/infra level, below Layer 2, keyed on the working directory. Separate
from view tags.

- Sessions in the **same directory talk freely** (one project). This fits the
  shared-worktree reality: sibling sessions in one checkout are one group.
- **Cross-directory is blocked by default.** The first attempt surfaces an approval
  to the user; approving opens a **group-wide** edge (every session in dir A ↔ every
  session in dir B), not just the two that triggered it.
- The edge is **alive-gated**: it lives while both directories have ≥1 live session
  and tears down when either empties, so it re-asks next time. Event-based, no
  timer. ("Allow personal and work to talk today; tomorrow they're separate again.")
- A **config allowlist** (directory-pairs or tag-pairs) permanently bypasses the
  gate for pairs you always want open.
- **Agent norm**: sessions should not attempt cross-directory messages unless the
  user directs it; an unsanctioned attempt surfaces the approval prompt rather than
  delivering silently or failing silently.

## Tags and views

Two grains, both directory-rooted, matching the "things in the same folder" model:

- **Tag assignment**: a `directory → tag` map in settings auto-tags a session at
  launch (e.g. `~/work/* → work`); a manual per-session override in the UI handles
  reclassification. Tags are mutable identity metadata (alongside name / dir /
  color).
- **View selection**: the URL hash (`#work,personal`), ad-hoc and per-tab; a saved
  default behind the settings gear; or a quick selector control at the timeline
  bottom. The hash is the fast lever.

The exact project directory defines a **comms group**; a directory→tag rule defines
the coarser **display label**. Both default from where you launched, both
adjustable.

## The UI progress surface

When the kernel is catching up on a backlog (an old session opened that needs its
goals judged, or a burst of new activity), it is judging segments it hasn't judged
yet. The UI shows a **progress indicator** ("re-judging…", N pending) so the inbox
filling in is legible rather than mysterious. The kernel exposes the
pending-judgment count; the UI renders it.

## Remote kernels + postal federation

- **Each machine runs its own kernel** (its own records, its own indexing). Records
  stay local to the machine that produced them.
- **Postal federates over SSH.** Local and remote sessions share one bus address; a
  remote session tunnels the bus port to the laptop with `ssh -R
  PORT:127.0.0.1:PORT` and heartbeats for presence (`postal/postal_service.py`), so
  messages cross machines.
- **Viewing remote sessions**: shipped as read-federation — link a remote kernel
  and its sessions appear as `host:name` tabs and timeline lanes in the one local
  dashboard, sharing the feed (the guide's "Linking kernels on other machines"
  covers setup).
- **Comms across machines**: gated per host by trust tier (trusted / directed /
  isolated — see `SECURITY.md`), not by the directory-group sketch above.

## Serve-layer security (auth / CSRF hardening)

Binding `127.0.0.1` is not an auth boundary: any webpage the user opens can reach
localhost, and WebSockets are not covered by CORS, so without origin checks a
malicious page can open `ws://127.0.0.1:PORT/ws` and drive the kernel (inject
prompts, spawn/interrupt sessions). This is the ClawJacked class (CVE-2026-25253).
The Python kernel (`kernel/kernel.py`) closes it.

- **Always-on Origin/Host validation (token-independent).** Validate `Origin` and
  `Host` on every HTTP request AND the `/ws` upgrade; allow only the kernel's own
  origin plus known local client origins (the browser at the kernel's host, the
  `vscode-webview://` extension, the timeline), reject everything cross-site. This
  kills ClawJacked for free; legit local clients send the right Origin/Host.
- **Token REQUIRED on every gated route, loopback included** (Jupyter's model:
  loopback is one network stack shared by every local UID, so the `0600` token
  file — not the socket — is the same-user trust boundary; the gate keeps a
  same-host co-tenant out of `/send` and the bus). Accepted forms: `?token=`
  (browser bootstrap, seeds a `SameSite=Strict` cookie so it never re-prompts),
  the cookie, and `X-Romp-Token` (CLI/hooks/daemons, read from the file). The
  token is baked into how the kernel launches (env/autostart), never a manual
  per-launch flag; a bare browser open of `/` gets a paste-the-token login page
  (bare `romp` prints the link + opens a browser). Only the no-side-effect liveness
  probes are exempt (`/healthz`, `/version`, `/busy`; bus `/ping`) so liveness
  never breaks token-less monitors. `tailscale serve` traffic needs the token
  once per device like any browser — and funnel (public internet through the
  same proxy) must still never be enabled for this port, since the token would
  then be the only gate with no device identity in front of it.
- Regression tests: a cross-site `/ws` upgrade with a foreign `Origin` must be
  rejected, and a token-less loopback request to any gated route must 403
  (tests/test_kernel_auth_hardening.py, tests/test_kernel_ws_auth.py,
  tests/test_postal_token.py).

## Naming

- **kernel** — the one always-on core (Layer 1 + Layer 2 + HTTP/WS serving).
- **ui/** — the front-end package (the three panes).
- **Romp Postal Service** — always-on messaging infra, below Layer 2.
