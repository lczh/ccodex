# Reference

This page lists every command and knob. It is here for driving Romp from the
terminal, for scripting against it, and for debugging: you do not need any of it
for ordinary use, where the user interface covers everything. Everything here
runs on the machine that hosts the kernel.

## The `romp` command

Run `romp` on its own and it opens the dashboard, which is all most days need.
Every other command is a bare word after it, and a session's name is always an
argument rather than the command itself, so the two can never collide: `romp new
update` starts a session called "update".

| Command | What it does |
|---|---|
| `romp` | Open the dashboard in your browser, printing the tokened link too |
| `romp new <name>` | Start a session, run by the kernel and watched from the dashboard |
| `romp new -d <dir> <name>` | Start it in `<dir>` instead of the current folder |
| `romp new -t <name>` | Start it as a terminal (tmux) session and attach; add `--detach` to leave it running |
| `romp resume` | Resume a past conversation, chosen from a full-screen picker |
| `romp status` | Manager and kernel status |
| `romp refresh [--quiet] [--force]` | Restart the postal bus and every kernel immediately, picking up new code (cut turns resume with their history). Runs `romp keysource --check` first and refuses to restart into a state where API-key-billed sessions would hold for want of a key source; `--force` restarts anyway |
| `romp update [host…]` | Push this machine's committed Romp to attached remotes and restart them |
| `romp up` | Run the kernel manager in the foreground; rare, since the login service runs it |
| `romp version` | Version report across the moving parts |
| `romp keyswap [<name>] [--cycle <session,…>\|--cycle-all]` | Switch the API key source without restarting the manager: selects a 1Password reference or a legacy key from `service.env.<name>`, and `--cycle` reconnects running sessions onto it. Bare, it reports the configured source and candidates without fetching secrets. See [Switching which API key the sessions bill](#switching-which-api-key-the-sessions-bill-romp-keyswap) |
| `romp keysource [--check\|--probe] [--ref <op://…>] [--token-stdin\|--no-token] [--yes] [--restart] [--path <file>]` | Set up the 1Password route, guided: the reference (validated), how `op` authenticates (a service-account token pasted without echo and written as `OP_SERVICE_ACCOUNT_TOKEN`, or the desktop app / `op signin`), then what is live at once and what needs the login service restarted, with the exact command and a `[y/N]`; with a kernel running it ends by probing the new route on it so held sessions resume without a restart. Bare, it first reports the configured source and whether an `op` credential line is present, by name. `--check` is the preflight `romp refresh` runs: it asks the running kernel first, and exits 1 with the remedy only when sessions would actually hold (an explicit API-key pick, or a remembered `key` Billing pick, and no source). `--probe` asks the running kernel to resolve the source once and release the sessions it holds. Never prints a key, token or reference value. See [Updating onto the 1Password route](#updating-onto-the-1password-route) |
| `romp help` | The same list, from the terminal |

These are for scripting and for agents rather than daily use:

| Command | What it does |
|---|---|
| `romp url` | Print only the tokened dashboard URL, for piping |
| `romp sessions [--json]` | The fleet with each session's state, identity colours, directory and backend |
| `romp mail …` | The postal service from the shell (below) |
| `romp send <session> [--tag <label>] <text>` | Hand a session a message, on either backend. Anything a script, cron job, or launcher composes SHOULD carry a tag (one word, letters/digits/dashes, up to 24 chars): the chat then renders it as machine-sent under that label instead of as the user's typed words. Raw POST /send callers pass it as the JSON `tag` field (`{name, text, tag}` — a malformed tag fails the whole send, loudly); `--tag` is the CLI's equivalent. Both resolve to the `<!-- romp-tag: <label> -->` marker in the delivered text |
| `romp new --env NAME=VALUE <name>` | A per-session env var for the SDK session, repeatable; a re-run against a running `<name>` replaces the whole set — vars not re-named are dropped |
| `romp new --no-env <name>` | Clear a running SDK session's per-session env (declares the empty set) |
| `romp interrupt <session>` | Interrupt whatever turn a session is taking |
| `romp compact <session> [--wait] [--timeout <s>]` | Compact a session's context in place (Claude's `/compact`: summarize the history, keep the session's name, id, mailbox, and watches) — the alternative to ending and recreating a long-lived session, and the external hand a session needs since it cannot `/compact` itself mid-turn. Quiet session → compacts now; open turn → queued, fires alone the moment the turn ends (the same safe path the chat's compact button uses). `--wait` blocks until the compaction has started and cleared, polling the kernel's own `compacting` signal on the `/sessions` rows (also the field to point a `romp watch` predicate at for scripted recycling); exits 1 honestly on timeout. A remote session's compaction is requested on its own kernel — `--wait` can't follow it from here and says so |
| `romp end <session>` | End a session |
| `romp move <session> <dir>` | Move an SDK session's working directory to `<dir>` (the folder must already exist); the conversation, name, mail and history stay with the session. Quiet session → moves now; open turn → queued, fires when the turn ends. See [Moving a session to another folder](#moving-a-session-to-another-folder) |
| `romp checkin <host>` / `romp checkout <host>` | Publish this machine to an attached hub, or withdraw it |
| `romp default-dir [PATH]` | The default working directory for new sessions; no argument prints it, `""` clears it |
| `romp debug [on\|off\|status]` | Judge debug mode, where rejection rows carry the full input and reply |
| `romp resume <id> [--name <n>] [--detach]` | Resume one exact conversation by UUID |
| `romp refresh --quiet` | Refresh at the next quiet window instead — waits for sessions to finish their turns (15-min backstop) |

`--env` gives one session its own environment, so two sessions in the same
directory can run with different toggles (a `FEATURE_FLAG=1`, a `CLAUDE_CODE_*`
switch) without editing the directory's `.claude/settings*.json`, which reaches
every session there and outlives them all. Re-running `romp new --env` against
a running session declares its full per-session env: any var you don't name
again is dropped, and `romp new --no-env <name>` declares the empty set — it
clears them all. Keep real secrets out of it: each value is copied into
per-session files and the session registry under `~/.local/state/romp/`. For
runtime API keys, configure a 1Password reference in the service environment
instead; see [Service environment and credentials](#service-environment-and-credentials).

Two things to know before building on `romp sessions --json`. **`waiting` means
at rest**, the ordinary state of a session that has finished its turn, so
matching it as an alert badges the whole idle fleet as needing you; the states
that want a person are `permission` and `picker` (a live prompt) and `blocked`.
(`romp sessions` emits the RAW backend states — the dashboard's chip states,
`needsInput`/`awaitingBg`, never appear here.) And
**`id` is the durable key**, not `lastSid`: everything Romp files per session is
keyed by `id`, while `lastSid` is the live transcript's id and forks on
`/clear`.

That key opens the per-session records under `~/.local/state/romp/`. The
per-turn one-liners live in `captions/<id>.jsonl`, one JSON record a line, with
the text under `caption`. A record's own `id` field is not the session's: it
identifies the turn within it. There is no `summaries/` directory; an older
layout had one, and reading it fails silently, since a missing directory just
yields nothing rather than an error.

### Moving a session to another folder

A session's working directory can change after it starts, so when a subproject
moves to its own repository, the session working on it can follow. Right-click
the session's tab and choose **Move to folder…**, or run `romp move <session>
<dir>`. The folder must already exist. Terminal (tmux) sessions cannot be
moved; start a new one in the folder instead.

What moves with the session:

- The conversation. Claude Code moves the transcript, with the tool-results,
  subagent and workflow files beside it, into the new folder's project
  directory under `~/.claude/projects/`; Romp moves the session's earlier
  transcripts (its `/clear` episodes and resume forks) the same way, so history
  and search keep working.
- The session's name, colour, mailbox, goals, cards and captions, all keyed by
  the session id rather than the folder.
- What the agent sees. Claude Code tells the model where it now is and loads
  the new folder's `CLAUDE.md`; from the next turn on, permission rules, hooks,
  skills and project MCP servers come from the new folder.

What does not move, because Claude Code keys it by folder rather than by
session: the old project's auto-memory (`~/.claude/projects/<old
folder>/memory/`), the old folder's entry in `~/.claude.json` (its allowed
tools, MCP approvals and trust), and the old repository's
`.claude/settings.local.json`. A comment thread opened on the session also
keeps its own folder. What Claude Code keys by session id (its debug log, task
store and file-history checkpoints) needs no move.

A move never interrupts a turn: on a session mid-turn it is queued as a chip in
the chat and fires the moment the turn ends, like a queued `/compact`. If Claude
Code reports a turn Romp could not see (one it started itself), the chip waits
for that turn to end too; the chip can be cancelled like any queued item. A
closed session is revived first, in its old folder, then moved. Only one move
per session is in flight at a time; a second request while one is pending is
refused. Every refusal (a folder that does not exist, a path that is a file, a
move already pending) is reported where you asked. If Claude Code's reply to
the move is lost, Romp settles the outcome by where the transcript is, the same
check it runs after a restart that interrupted a move; a move it cannot settle
is reported and left for the next kernel start, with nothing changed.

The move is Claude Code's own relocation (the `set_cwd` control behind the
interactive `/cd`), with Romp moving its own records alongside. It fires Claude
Code's `CwdChanged` hook, not `SessionStart`; Romp registers no `CwdChanged`
hook, so nothing on Romp's side re-runs.

## The Romp Postal Service

How sessions message each other, from either side. Inside a session it is an MCP
server, so an agent calls the tools below directly; from a terminal the same
mailbox is behind `romp mail`. See
[Inter-agent communication](guide.md#inter-agent-communication-the-romp-postal-service)
for what it is for.

### Mail from the terminal

```bash
romp mail send [--kind delegate|coordinate|question] <name> "<text>"
romp mail inbox                  # read your messages, and clear them
romp mail peek                   # read them without clearing
romp mail agents                 # who is live, their branch and working-note
romp mail working "<note>"       # publish what this session is working on
romp mail sent                   # your sent messages, and whether each was read
romp mail recall <to> [id]       # unsend a message the recipient has not read
romp mail remote                 # connect this remote machine to your laptop's bus
```

### Mail inside a session (MCP tools)

| Tool | What it does |
|---|---|
| `send_message(to, body, kind)` | Message a live session by name; `kind` declares delegate / coordinate / question |
| `check_inbox()` | Read messages sent to you (also delivered at the end of each turn) |
| `list_agents()` | The live sessions, each with its branch and working-note |
| `set_working(text)` | Publish what you hold so peers steer clear |
| `check_sent()` | Whether your sent messages were read yet |
| `recall_message(to, id?)` | Unsend a message the recipient hasn't read |

### Claude Code 2.1.224 or newer

Mail to a terminal (tmux) session delivers through Claude Code's per-session
inbox socket, which the CLI added in 2.1.224: delivery is instant and never
touches a half-typed draft. An older Claude Code still works — delivery falls
back to typing the mail into the pane, which is slower and waits for a free
prompt — and `romp` says so at launch, with the upgrade being one
`claude update` away.

## Configuration

### Folder click, in your terminal or editor

The chat statusline shows the session's working directory; clicking it opens
that folder. The default is the OS opener (`open` / `xdg-open`). To open it
elsewhere, set a command via the env var `ROMP_OPEN_FOLDER` or the first
non-comment line of `~/.config/romp/open-folder`; `{dir}` is replaced with
the clicked path (omitted, the path is appended). The command runs on the
kernel's machine.

```bash
# ~/.config/romp/open-folder: pick one line
open -a Ghostty {dir}               # macOS: a new Ghostty window there
ghostty --working-directory={dir}   # Linux: Ghostty
code {dir}                          # VS Code instead
```

### Model and effort, from the statusline or a typed command

Typing `/model X` or `/effort X` into the chat composer, or sending one with
`romp send`, is the same setting change as a pick from the statusline's model
and effort dropdowns: the kernel takes it through its own setters, so what it
remembers (the value a reconnect relaunches with, the defaults new sessions
start from) follows the switch. A typed `/fast on|off` goes through the same
setters and matches a pick from the fast badge the next section describes, a
separate toggle rather than one of the two dropdowns. A bare `/model` (the
CLI's own picker), a value the kernel cannot vouch for (a typo), or a longer
message that merely opens with the command goes to the CLI verbatim, and the
chat shows the CLI's own reply.

The two backends apply the change differently. An SDK session switches model
live but reloads to apply a new effort: the chat shows "Reloading session…"
and the effort badge shows switching-dots until the reload completes, and a
session that is mid-turn reloads when the turn ends. A terminal (tmux) session
gets the CLI's own command typed into its pane. `/model` there asks for a
confirmation, which the kernel accepts on your behalf so the pane is never
left waiting on a keystroke the dashboard cannot send; `/effort` and `/fast`
apply in place.

### Fast mode, from the chat statusline

The statusline's badges — permission mode, model, effort — are each a small
dropdown. A fourth appears when the session reports Claude Code's fast-mode
state (an Opus-only research preview, billed at a premium): it reads **Fast**
in orange while fast mode is on, **Slow** while it's off, and **Cooldown**
while fast requests are rate-limited. Picking On or Off sends the CLI's own
`/fast` command; the badge never appears on a session that cannot run fast
mode. Turning it on while the session is on a non-Opus model makes the CLI
switch to a fast-capable one, which the chat shows as the command's own
confirmation. If the CLI refuses the toggle (for example, the account has
extra usage turned off), a toast says why and the pick reverts to off —
the control never silently disappears.

### Per-session billing (login vs API key)

An SDK session can bill either the machine's Claude login (subscription usage)
or the configured API key source — per session. That source can be a 1Password
reference resolved at runtime or a legacy `ANTHROPIC_API_KEY`.

The new-session picker's **Billing** row states the case whenever the backend
toggle says SDK: segmented buttons when the selected host offers both choices,
and with only one real choice, the same spot simply writes out which applies —
`Login (name@example.com)` or `API key` — so what a session will bill is never
a mystery. A live session additionally wears a statusline badge for
*switching*, beside mode/model/effort, and that control keeps the stricter
rule: it exists only when both choices are real (a one-option selector is
noise). Switching reconnects the session to apply (the key rides the launch
environment), with the same switching-dots the effort badge wears.

The login is named by its account (the email the credential store records);
the key option is labelled plainly `API key` — no fragment of the key, not
even a last-4 tail, ever reaches a browser or a screen. A new session
defaults to the last pick made anywhere, and before any pick to the key when
one is configured — exactly what an ambient key did before the selector
existed. tmux sessions are not covered by the picker: their CLI lives in the
tmux server's environment, which the kernel does not control. What Romp does
do there, when a 1Password reference is configured, is keep the manager's
startup `ANTHROPIC_API_KEY` out of the server's globals, so a terminal
session falls to Claude Code's own auth (login or `apiKeyHelper`) rather than
billing a key nobody chose; see [API keys from 1Password at
runtime](#api-keys-from-1password-at-runtime).

Each chat tab's hover tooltip carries the same fact as a `Billing` row —
`API key`, or `Login (name@example.com)` — whenever the session's backend
reports it, one-auth machines included; only tmux sessions, whose billing romp
cannot know, show no row. When the CLI's own report disagrees with what the
session was launched for — a key found through `apiKeyHelper`, say — the row
carries both: `Login (CLI reports API key)`.

Failures are loud rather than silent: a session that lands on the other auth
than it was launched for (say, a key found through `apiKeyHelper`) is flagged
in the Log panel, and a dead credential — "Not logged in", an invalid or
expired key — blocks the session's card with the fix named, and is never
auto-retried.

One side of that check can be the box's *design*: on a machine whose sessions
are all meant to bill a key that arrives through `apiKeyHelper` — so it never
appears in `service.env` — the landed-on-the-other-auth warning would fire on
every init, permanently. Declaring the intent fixes it: set
`ROMP_EXPECTED_AUTH=key` (or `login`) in `service.env`, and a session landing
on the declared side is quiet while one landing on the other side is flagged,
naming the declaration. The check inverts rather than disappearing; unset (or
any other value), it compares against what the session was launched with, as
before. One explicit gear **Billing** pick supersedes the declaration from then
on: the remembered pick becomes the box's expectation and the env var goes
inert (it described the unpicked design), so re-seeded spawns are judged
against your pick, never against stale doctrine.

The usage rail reflects a mixed machine: the window bars (5 hours / 7 days /
Fable 5) are drawn once, aggregated across every connected host's login as the
worst reading per window, and an `API` cell beside them carries the
key-billed dollars (5-hour burn and month-to-date, numbers only). Hovering
breaks both down per host — one column per host, side by side — and a host
can show its login's windows and its key's spend together. Only turns whose
session billed the key count toward the API numbers — a login turn's computed
cost is dollars nobody pays.

The token count beside the dollars is every kind together: fresh input,
output, cache writes, and cache reads. Cache reads are most of it — every API
call within a turn (one per tool step) re-reads the whole context from the
cache, so a long session's single turn can read tens of millions of tokens at
a tenth of the input price. The hover splits each window's count by kind, so
the size of the number carries its explanation.

### Self-scheduled work wakes an idle session

A session's own scheduled work (a recurring Monitor, a cron firing, a
background task's completion notice) arrives as a queued notification even
while the session is idle. The Claude Code CLI usually delivers it on its
own, starting the turn within a fraction of a second; but a session can fall
into a stuck state where the CLI only queues, nothing ever starts the turn
that reads the queue, and the backlog waits silently until your next message.
Romp watches for that: once a queued notification has sat undelivered for a
minute (well past the CLI's own delivery window) with no turn running, one
driven turn delivers every text that has waited out that minute, verbatim and
with no words of Romp's own, and logs one kernel-log line per wake; a newer
arrival waits out its own minute rather than delaying the rest. A
notification that arrives mid-turn is delivered once the turn settles, and
one whose delivery a kernel restart interrupted is re-driven on the next
boot rather than dropped. Notifications
the CLI delivers itself in either state (a background agent finishing) are
left to it, and sessions that are mid-turn, compacting, blocked on an API
error, retry-paused, or that you interrupted or ended are left alone. On the
first run after an upgrade, a session holding a genuinely old queued backlog
may get one catch-up turn delivering it; that is this feature doing its job
once.

### Install-time switches

For `./install.sh`:

- `ROMP_NO_SERVICE=1` skips the login service.
- `ROMP_NO_EXT=1` skips the VS Code / Cursor extension.
- `ROMP_NO_SDK=1` skips the SDK backend's venv (tmux sessions still work).

For the one-line installer (`bootstrap.sh`), which passes all of the above
through to `install.sh`:

- `ROMP_DIR=<path>` where to clone; default `~/romp`.
- `ROMP_REF=<tag|branch>` install a specific ref; default is the newest `v*`
  release tag, falling back to `main` when none is published.
- `ROMP_NO_PATH=1` leaves your shell rc alone.

### Ports

- `ROMP_KERNEL_PORT=<port>` moves the kernel and its dashboard off the default
  `29855`. `ROMP_SERVE_PORT` is a second name for the same port, the one the
  manager and the supervised service use. Set either and the other follows; set
  both to different values and the kernel refuses to start rather than picking
  one for you.
- `ROMP_POSTAL_PORT=<port>` moves the postal bus off the default `25302`.

Set these if something else on the machine already holds the default. Both have
to agree across everything that talks to the kernel, so export them where the
whole environment sees them rather than for one command.

Run `romp-service install` again after changing one. The service unit bakes in
whatever is set at install time, so a renumbered port that only lives in your
shell leaves the supervised manager on the old one, and the two collide.

### Service environment and credentials

The manager runs as a login service (launchd on macOS, systemd --user on
Linux), so it does not receive variables exported by your shell rc. Configure
the service in `~/.config/romp/service.env` using plain `KEY=VALUE` lines and
owner-only permissions (`chmod 600`). The service reads this file at manager
startup; API key source settings are also read live before use. Other changes
need a manager restart. `ROMP_SERVICE_ENV_FILE` overrides the file's path.
Supervised services use this file as their API key source. An empty or missing
source cannot fall back to credentials inherited from an earlier manager
start, including after a kernel refresh or crash restart. Foreground managers
can use an environment source when no service-file source governs them.

#### API keys from 1Password at runtime

To keep API key values out of Romp's configuration files, set a
[1Password secret reference](https://www.1password.dev/cli/secret-references):

    ROMP_API_KEY_REF=op://vault/item/field

`ROMP_API_KEY_REF` takes priority over a legacy `ANTHROPIC_API_KEY` in the
selected configuration. An empty or invalid reference is an error, not a
request to use the legacy key. Remove competing plaintext assignments when
migrating; `romp keyswap` does this automatically when selecting a profile.

Romp resolves the reference with
[`op read --no-newline`](https://www.1password.dev/cli/reference/commands/read)
for a Claude SDK session launch or reconnect, an API-key-billed judge call,
and a direct model-catalog refresh — at most once per `ROMP_OP_REUSE_S`
window (60 s by default; `0` restores one read per operation). A paginated
catalog refresh uses that credential for all its pages. An explicit `romp
keyswap --cycle` resolves the key once per request to check which quiet
sessions need a reconnect. When a retrieval fails, the judges do not retry it
on every call: the failure holds for the rest of that judging pass and is
retried when the next pass begins or the source changes, so an unreachable
`op` costs one timeout per pass. Romp captures the value in memory and passes
it to that operation. It never writes the resolved key to disk. Within the
reuse window it keeps the last successful `op read` in memory, so the N judge
calls of one pass and a burst of session launches share one `op` process
instead of running N, and a transient `op` blip inside that window cannot
fail them; a failed read is retried once before it is reported, and a
failure is never kept — the next call runs `op` again. The window is per
process and per reference: a `romp keyswap` to another reference reads
afresh, and a kernel restart starts empty. A key rotated behind the SAME
reference is picked up within the window on its own, or at once by `romp
keyswap --cycle`, which reads afresh. A running Claude process retains the
key it received at launch until it reconnects; this is not retrieval before
every message in an existing session.

The `op` executable must be on the **service's PATH**, and 1Password access
must work for the OS user running the service. The service installer records
PATH at install time; run `romp-service install` again after changing it.
An interactive terminal sign-in does not by itself establish that a headless
login service can read the same secret: a service has no desktop app to
unlock. The supported unattended route is a
[1Password service account](https://developer.1password.com/docs/service-accounts/):
put its token in `service.env` beside the reference,

    OP_SERVICE_ACCOUNT_TOKEN=ops_...
    ROMP_API_KEY_REF=op://vault/item/field

and give the account read access to that one vault, and nothing else. When a
reference is configured, Romp takes `op`'s own credential names
(`OP_SERVICE_ACCOUNT_TOKEN`, `OP_SESSION_*`, `OP_CONNECT_*`, `OP_ACCOUNT`) out
of its environment as its first act at startup, says which names it claimed in
the kernel log, and hands them to the `op read` subprocess alone: no Claude
session, judge call, or tmux launch inherits them, so an agent running `env`
sees neither the API key nor the credential. The `op read` subprocess itself
gets a minimal environment, not a copy of the kernel's: `PATH`, `HOME`, `USER`,
`LOGNAME`, `TMPDIR`, `LANG`, `LC_*`, `TERM`, the `XDG_*` names (op finds
`~/.config/op` and the desktop app's socket through `HOME` and `XDG_*`), and
the claimed `OP_*` names. Nothing of Romp's (the serve token, a startup
`ANTHROPIC_API_KEY`, the reference variable) reaches it.

The tmux server needs the same care, because every pane inherits the
**server's** globals, not the launching client's. Whenever Romp becomes the
op consumer — at kernel start, or later when `romp keyswap` selects a
reference on a box that started without one, with no manager restart — it
unsets the `OP_*` names **and** the manager's startup `ANTHROPIC_API_KEY`
from the tmux server's global environment; the manager starts the server
without them, and `romp new -t` scrubs them again before the pane exists
(reading the reference from its own environment or from `service.env`'s
line). A terminal session on a reference-governed box therefore never bills
the key the manager started with: with no `ANTHROPIC_API_KEY` in its
environment it falls to Claude Code's own auth (login or `apiKeyHelper`).
Panes that already existed when the reference was selected keep the
environment they launched with; end or relaunch them. A box with no reference
configured is untouched: static-key panes rely on inheriting the key, and an
`apiKeyHelper` box's sessions need `op`'s environment.

This keeps the token out of every agent's shell by default; it is
inheritance hygiene, not isolation. The file stays readable to the same OS
user (keep it `chmod 600`), and a same-user process can read the manager's
original environment, which is why the account must see only the one vault.
The `OP_*` lines of `service.env` now reach the kernel's `op read` live: a
token added or rotated in the file works for the KERNEL's reads (session
launches, judge calls, the catalog) without a manager restart. The manager's
OWN environment is still fixed at its start, so a service restart is what
makes the new token the service's too — `systemctl --user restart
romp-manager.service` on Linux, `launchctl kickstart -k gui/$(id -u)/com.romp.manager`
on macOS; `romp keysource` prints the right one and offers to run it. The
reference itself has always been read live. Do not put the token in a
per-session environment (`romp new --env`), which is copied into per-session
files.

A supervised manager (the systemd or launchd service) reads its key source
from `service.env` **only**. A key that reaches the manager some other way, a
systemd drop-in `Environment=` or a launchd plist entry, is ignored and said
so once in the kernel log with its fingerprint; sessions without an explicit
Billing pick then launch on the login. Move such a key into `service.env`, or
replace it with a reference.

For a foreground manager, the same reference can be supplied in its
environment:

    ROMP_API_KEY_REF=op://vault/item/field romp up

The Billing picker, status displays, and `romp keyswap` listing and selection
inspect the configured source without running `op`. A configured reference
therefore means "API key available to try", not "1Password access verified".
If a selected provider cannot resolve the key, the operation fails with a
credential error. It does not use an ambient key, a previous resolved key,
or a Claude login as a fallback. Choosing **Login** explicitly still uses
Claude Code's supported login flow and does not resolve the API key source.

To migrate an existing service:

1. Put the API key in 1Password and obtain its field's secret reference.
2. Replace `ANTHROPIC_API_KEY=...` in `service.env` with
   `ROMP_API_KEY_REF=op://vault/item/field`. Replace any sibling profiles used
   by `romp keyswap` in the same way, and remove obsolete plaintext copies.
3. Refresh the kernel once to load this version of Romp. Future source edits
   take effect without restarting the manager: the next session launch or
   judge call reads the reference, and that first read also scrubs the tmux
   server of `op`'s names and the retired `ANTHROPIC_API_KEY`.
4. Reconnect existing key-billed SDK sessions with `romp keyswap --cycle-all`
   when they are quiet. Newly launched sessions and subsequent judge/model
   requests use the configured source immediately. Terminal (tmux) sessions
   launched before the migration still carry the plaintext key they started
   with; end and relaunch them.

Once a reference has been selected from `service.env`, Romp remembers it on
disk in the sibling file `service.env.source` (the word `op`, mode 600, never
a reference or a key), so the memory survives kernel restarts: deleting the
reference line and restarting does not make sessions fall to the login.
Selecting a static key — `romp keyswap <static profile>`, or writing an
`ANTHROPIC_API_KEY=` line — removes the marker, so an intentional switch is
not an error. `romp keyswap` does not list the marker as a profile.

#### Updating onto the 1Password route

What happens when a box that predates the runtime key source takes this
version depends on how its sessions were getting their key:

- **A box that billed a key through Claude Code's `apiKeyHelper`** (the key
  never rode `service.env`) keeps working. Launches still ride the helper,
  and the kernel says so once in its log; nothing to do unless you want Romp
  to hold the reference itself.
- **A box on API-key billing with no key source** — sessions with an explicit
  API-key Billing pick (or seeded from a remembered `key` pick), and neither a
  `ROMP_API_KEY_REF=` nor an `ANTHROPIC_API_KEY=` line in `service.env` — no
  longer crashes its sessions at launch. Each such session is HELD, with the remedy on its card and one
  line in the kernel log, and resumes on its own the moment a source appears
  in the file: no restart, no re-send.
- **`romp refresh` refuses to restart into that state.** It runs
  `romp keysource --check` first; on exit 1 it prints the remedy and stops
  before the audit row and the restart. `romp refresh --force` restarts
  anyway. A preflight that cannot run at all (no `python3`) is a one-line
  warning, never a block. The check asks the RUNNING kernel first (`GET
  /keysource`, with the serve token): the kernel is what launches sessions,
  so a source it reports as configured passes at once, whatever the file
  says — a foreground `romp up` on an environment key has no file line to
  read. Only when no kernel answers does the file decide, and then it refuses
  only for what the kernel would actually hold: an alive session with an
  explicit API-key pick, or a remembered `key` Billing pick (which seeds
  every new session), with no source and no `apiKeyHelper`.
  `ROMP_EXPECTED_AUTH=key` alone holds nobody — an unpicked session launches
  login-side, and the declaration goes inert once a Billing pick exists — so
  it is mentioned as context, never a reason to refuse. A box that no login
  service supervises gets a warning and exit 0 instead of a refusal, because
  its manager may hold a key the file cannot show. `romp update` and the
  dashboard's Update button restart WITHOUT this preflight; on a box without
  a key source the result is the held state, reported on that kernel's cards
  and in its log.
- **After fixing `op` or the token, `romp keysource --probe`** asks the
  running kernel to resolve the source once (`POST /keysource/probe`; one
  `op read` for the whole board) and release every session it holds whose
  reason is gone, printing the outcome and the released sessions by name. A
  guided `romp keysource` run does this itself at the end when a kernel is
  reachable. Without the probe, the next judge pass, session launch or `romp
  refresh` picks the fix up.
- **`romp keysource`** walks through the setup: it reports the configured
  source and whether an `op` credential is present in the file (by name),
  asks for the reference and validates it, asks how `op` authenticates (a
  service-account token, pasted without echo and written as
  `OP_SERVICE_ACCOUNT_TOKEN`; or the desktop app / `op signin`, which a
  headless service cannot use), writes both lines atomically at mode 600,
  and says what is live at once (the reference) and what the login service
  only sees after a restart (a NEW token line, for the manager's own
  environment), with the exact command and a `[y/N]`. Non-interactive:
  `romp keysource --ref op://vault/item/field --token-stdin --yes` (token on
  stdin) or `--no-token --yes`; `--restart` runs the restart without asking;
  `--path <file>` names another env file — when it is not the one the kernel
  reads, the write is reported as NOT live (select it with `romp keyswap
  <name>` if it is a `service.env.<name>` sibling, or point
  `ROMP_SERVICE_ENV_FILE` at it and restart the service), and `--check --path`
  is a dry run of the file logic alone.

Removing or emptying an explicit service-file key source cannot revive the
key inherited when the kernel started. After removing a selected provider,
API-key operations fail until a valid source is selected; choose **Login**
explicitly to use that mode. Removing a static `ANTHROPIC_API_KEY=` line
while the kernel runs is different: the file stays authoritative and
sessions without an explicit Billing pick launch on the login, and the
kernel log says so once, with the removed key's fingerprint and the file's
path. Removing a source does not revoke a credential already held by a
running Claude process; reconnect or end those sessions too. Rotate a
previously exposed key with its issuer as appropriate.

#### Existing API keys and Claude login

1Password is optional for general use. Without a runtime provider selected,
Romp continues to support legacy `ANTHROPIC_API_KEY` configuration, including
in `service.env`, and the existing Claude Code login. A foreground manager
can also use its inherited API key when no service-file source governs it.
For login, authenticate through Claude Code's supported CLI login flow and
select **Login** in Billing; no extracted OAuth token is needed.

Plaintext keys remain supported for compatibility, but do not meet policies
that require secrets to be fetched from 1Password at runtime. File permissions
do not change that distinction.

### Switching which API key the sessions bill (`romp keyswap`)

The key a session bills rides its launch environment, and Romp checks the
API key source in `service.env` **at every session launch**.
So changing keys — moving to another organisation's key, rotating a leaked
one, switching between a high-priority and a batch key — costs no manager
restart, and no session loses an open turn.

Keep one file per profile beside `service.env`, each with a single
`ROMP_API_KEY_REF=op://vault/item/field` assignment, `chmod 600`:

    ~/.config/romp/service.env.highprio
    ~/.config/romp/service.env.lowprio

Then:

    romp keyswap                       # configured source and candidate profiles
    romp keyswap lowprio               # select the source from service.env.lowprio
    romp keyswap lowprio --cycle-all   # …and move the running sessions onto it too
    romp keyswap lowprio --cycle web,api

Legacy profiles containing a single `ANTHROPIC_API_KEY=` assignment also
work. `romp keyswap <name>` writes the selected assignment and removes the
competing key-source assignment, keeping all unrelated lines as they were
(line endings come out as LF). A temp file and rename make the update atomic,
and the mode stays `600` (a looser one is tightened). A symlinked `service.env`
is written through: the target changes, the link stays. A profile without a
usable source is rejected. Listing or selecting a reference never retrieves
its key; an explicit `--cycle` check asks the kernel to resolve it.

After the rewrite:

* **new sessions, and any session you revive, bill the new key immediately** —
  nothing else to do;
* **already-running sessions keep the key their process started with**, because
  the key is handed over at launch. `--cycle-all` (or `--cycle <session,…>`)
  reconnects them so they re-present the new one. A reconnect resumes the same
  conversation with its history intact — the same mechanism a reasoning-effort
  or billing switch uses — and only for a session that is quiet right now. Sessions billing the machine login are
  skipped, dormant ones are reported as needing nothing, a session already
  launched on the current resolved key reads `current`, and a session with a turn,
  subagents or background tasks in flight is skipped and named — a reconnect
  would kill that work — so re-run `--cycle` for those sessions once they are
  quiet;
* **the judges and direct model-catalog refreshes** pick the new source up on
  their next call, with no cycling at all;
* **terminal (tmux) sessions** are not cycled. A swap from a static key to a
  reference removes the old `ANTHROPIC_API_KEY` from the tmux server's
  globals at the kernel's next key read and at the next `romp new -t`, so new
  panes fall to Claude Code's own auth; panes already open keep what they
  launched with — end and relaunch them.

No key value is printed or sent in Romp's status/control responses. Legacy
keys are identified by the first 12 hex of their sha256, such as
`sha256:1a2b3c4d5e6f`. Provider status uses a fingerprint of the reference,
without resolving it; this cannot verify the secret value or detect a rotation
behind an unchanged reference. An explicit `--cycle` resolves the current key
for each quiet session and reconnects it if that key differs from its launch
key, including when the reference itself is unchanged. A session already
using that key reads `current` and stays connected. A reconnect inside the
reuse window (`ROMP_OP_REUSE_S`, 60 s by default) launches on the key the
cycle check just resolved; outside it, or with the window set to `0`, the
launch resolves the source again.

**One restart, once:** a running kernel needs to load this version to support
runtime providers. Take the update with `romp refresh` — or `romp refresh
--quiet`, which waits for sessions to finish their turns first — and later
source swaps need no manager restart. `romp keyswap --cycle-all` reports when
it encounters a kernel too old to support cycling.

Remote kernels each have their own `service.env` and their own key: run
`romp keyswap` on that machine.

`ROMP_SERVICE_ENV_FILE` overrides the path of the file. The installer bakes
the path it resolved into the unit and, when that is not the default, exports
it to the service as well, so the kernel's live read and the installer name
one file; a service installed before that carries only the default. `romp
keyswap` compares the running kernel's source identity with the file's and
says `MISMATCH` when they differ — the check to make after a swap.

### What survives a restart

A kernel restart ends every session's CLI. On `romp refresh`, the manager's
restart-all or a service stop, the kernel receives SIGTERM and drains: it
closes each CLI, and a CLI still running when the drain's bound expires gets
SIGTERM, then SIGKILL. A crash respawn has no drain: the kernel died without
running one, its CLIs are orphaned, and the next kernel's boot reaper
terminates them (see below). The CLI's harness background tasks do not all end
with it. Its timers and monitors live inside the CLI process and end when it
does. A background shell is a separate process the CLI started, and a CLI
killed by SIGKILL runs no cleanup, so its shells are re-parented and may keep
running. The session resumes with its history and is told what was cut: its
in-flight turn, if it had one, and each background task, with a request to
check whether each is still running before relaunching it. A kernel restart has
never touched work a session deliberately detached: tmux servers, `setsid`
children and other processes that outlive their shell.

A service restart (`systemctl --user restart romp-manager`, or the machine's
own service management) kills everything in the service's cgroup, so on Linux
under systemd Romp runs each session's CLI, and the default tmux server the
manager starts, in a transient systemd scope of its own, outside that cgroup
(`systemctl --user list-units 'romp-session-*' 'romp-tmux-*'` lists them). A
session's tmux servers, `setsid` children and other detached work live in the
session's scope, and a service restart leaves them alive as a kernel restart
does; before 2026-09-05 they were in the service's cgroup and died with it. The
CLI itself still ends: the kernel receives the service's SIGTERM and runs the
same drain. A scoped CLI outlives a service restart only when the drain does not
reach it: a kernel killed before its drain finishes (SIGKILL at the service's
stop timeout), or a CLI the drain could not find. The reaper handles that case:
at the next kernel boot, an SDK-driven CLI holding one of the kernel's sessions
whose parent is not a live romp kernel is treated as orphaned and terminated.
Under `systemd --user` an orphan re-parents to the user manager, not to pid 1,
so a ppid check alone would miss it and did, before 2026-09-05.

One-time caveat when this lands: the first service restart after it still
empties the current cgroup, tmux servers included, because the running manager
and its tmux server predate the change and are still inside the service's
cgroup. The guarantee holds from the following restart on.

`ROMP_CLI_SCOPE=0` in the service environment turns the scopes off, for
session CLIs and the tmux server alike. A manager run outside the service
(`romp up`) scopes nothing unless `ROMP_CLI_SCOPE=1` is set, which turns both
on. The kernel logs which it chose at start (`cli scope: on` or `off`, with the
reason); when the scopes were wanted on Linux and the box cannot provide them
(no `systemd-run`, or a user manager that refuses to start one), that verdict
also appears in the dashboard's error center, since every session then runs
inside the service cgroup. The macOS launchd path is unchanged: there is no cgroup kill there,
and the tmux server keeps its launchd lineage.

## Where things live

State is written under `${XDG_STATE_HOME:-~/.local/state}/romp/`. Transcripts
are read in place from where Claude Code writes them (`~/.claude/projects/`)
and never copied.

## Switches

Effective immediately, no restart.

`touch` to **disable**, `rm` to re-enable:

- `~/.claude/romp-postal-off`: the postal service

`touch` to **enable**, `rm` to turn back off:

- `~/.claude/romp-summarize-on`: the live tmux activity phrase. Off by default,
  because it spends tokens on every turn and the SDK backend reports what a
  session is doing without it.
