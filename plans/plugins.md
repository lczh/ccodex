# Plugins — a sandboxed pane, a broker, and a capability manifest

**Status: PROPOSED, not yet built.** Design record for a romp plugin system,
written to ship Hive (PR #341) as romp's *first* plugin instead of merging a
game-feel 3D view into core. No landing commits yet; unlike the rest of
`plans/`, this predates the work rather than recording it.

## Why

Hive is a 3D "command view" of your sessions. The engineering under it is sound
(it reads the real feed, mutates only on diff events so it doesn't thrash, and
stops rendering when hidden), but a 3D diorama is one person's taste, not
romp's spine, and core should stay lean and glanceable. It is, however, the
ideal forcing function for a plugin system: a rich surface that needs only to
(a) read session state and (b) issue a handful of drive ops. If the plugin API
can host Hive at arm's length, it can host almost anything.

One rule shapes the whole design: **a plugin must not be able to bypass romp's
serve token or reach a session it wasn't granted.** Same-origin trust is not
enough — a same-origin page carrying the dashboard cookie could just open
`/ws?app=chat` and drive everything. So plugins are caged, and a small trusted
broker is the only code that touches the kernel.

## The model

A plugin is a directory of static assets (`plugin.js`, `plugin.css`, images)
plus one `manifest.json`. romp serves it at `/plugin/<id>` into an iframe with
`sandbox="allow-scripts"` (opaque origin) and a CSP of `connect-src 'none'`, so
the plugin code holds no token and cannot open a socket at all. Read-only is
structural, not a promise.

A first-party **broker** frame (`/plugin-host/<id>`) is the only trusted code.
It holds the cookie, rides the existing feed WebSocket as `app=plugin:<id>`,
projects a read-only feed frame *down* to the caged plugin over `postMessage`,
and relays a manifest-scoped subset of drive ops *up* to the unchanged
`_drive`. The plugin talks only to the broker; the broker enforces the manifest.

## Manifest

```json
{
  "id": "hive",
  "name": "Hive",
  "version": "0.1.0",
  "entry": "plugin.js",
  "css": "plugin.css",
  "pane": { "label": "Hive", "placement": "column", "defaultOn": false, "order": 50 },
  "scopes": {
    "read": ["feed"],
    "drive": ["sendMessage", "endSession", "renameSession", "openSession"],
    "ui": ["reveal", "focusChat", "openPicker"],
    "sessions": "all"
  }
}
```

- **`pane`** drives the rail button and pane registration — what today is four
  hardcoded slots in the landing shell.
- **`scopes.read`** names the read riders it subscribes to (`feed` = the whole
  session-state payload; the entire data-in surface a consumer like Hive needs).
- **`scopes.drive`** is the allowlist of drive-op types the broker will forward.
- **`scopes.ui`** is the cross-pane relay vocabulary (reveal a pane, focus a
  chat tab, open the picker) that never touches the kernel.
- **`scopes.sessions`** is `"all"` or a sid/label filter, enforced on every op.

The manifest is the single source of truth and the consent surface: enabling a
community plugin prints these scopes for one-time approval.

## Data in (read-only)

The broker connects as a feed rider and forwards each feed frame verbatim to the
caged plugin as `{romp:"feed", payload}`. The payload is romp's existing feed
contract — `ledgers[]` (sid, name, color, `status.state`, tree nodes) joined
with `asks[]` (`blockSummary`, `awaiting.why`, `working`) — the same authority
the fleet and feed panes render, so no new push machinery is added, only one
generalized feed-membership test. Read-only is enforced by the cage:
`connect-src 'none'` means the plugin cannot fetch `/sessions`, `/file`, or
reopen the socket, and it never scrapes the DOM or a transcript (the
authoritative-source rule). Any future read route is added as an explicit
broker-proxied scope, never ambient access.

## Actions out (broker-mediated)

Two channels, both capability-checked:

**Drive.** The plugin posts `{romp:"drive", reqId, op, args}`; the broker
verifies the message came from its own caged frame, checks `op ∈ scopes.drive`
and `args.id ∈ scopes.sessions`, then sends the existing WS drive message. The
kernel's `_drive` is unchanged and already refuses a foreign sid loudly; the
refusal flows back to the plugin as `{romp:"ack", reqId, ok:false, error}`
(fail loudly, don't degrade). Hive's op set needs zero `_drive` changes.

**UI relay.** The plugin posts `{romp:"ui", action, args}` for cross-pane
gestures (reveal a pane, focus a chat tab, open the new-session picker); the
broker checks `action ∈ scopes.ui` and forwards to the shell. These never reach
the kernel. New-session creation is deliberately not a drive op: it delegates to
the chat pane's existing picker via `openPicker`, so a plugin never mints
sessions directly.

## Security model

- The plugin holds no token and no socket. The cookie lives only in the
  first-party broker; the caged frame is opaque-origin with `connect-src 'none'`,
  so it cannot authenticate anything even if it tried.
- The broker is the sole reference monitor: every op is checked against the
  manifest's drive and session scopes before it leaves. A plugin cannot reach a
  session outside its `sessions` scope or issue an op it didn't declare.
- Isolation is structural (sandbox + CSP), not trust-based. That is why a caged
  iframe beats a same-origin pane: a same-origin plugin carrying the dashboard
  cookie could open `/ws?app=chat` directly and bypass the whole manifest.
- Core and community plugins are treated identically by the cage; provenance
  only affects the consent prompt, not the runtime trust.

## Minimal core hooks

Kept as small as possible:

1. Generalize the four hardcoded pane slots (rail button, iframe/pane div, flex
   var, `po-*` default, focus/label maps) into a registry loop over enabled
   manifests.
2. One generalized feed-membership clause so `app=plugin:*` rides the existing
   feed push.
3. Two routes: `/plugin/<id>` (caged assets) and `/plugin-host/<id>` (broker).
4. The broker relay itself (postMessage <-> WS, enforcing scopes).

No per-plugin kernel code. Adding a plugin is a data drop the shell discovers.

## Access, tiers, and defaults

- **Access** is a Plugins section in the settings gear, one row per installed
  plugin with an on/off toggle. Enabling a plugin is what makes its rail button
  and pane appear; nothing new to build beyond the gear that already toggles
  autoNudge and the like.
- **Two tiers, Obsidian-style.** *Core plugins* ship bundled in the repo/dist
  and are listed in settings (Hive is the first). *Community plugins* drop into
  `~/.local/state/romp/plugins/<id>/` — the user-state root the token already
  trusts — installed via `romp plugin install`, which prints the manifest scopes
  for one-time consent.
- **Off by default, every plugin, both tiers.** romp doesn't add a surface or
  attention load unless asked; community plugins additionally gate on scope
  consent before they can read or drive.

## Hive as the first plugin

Under this model Hive ships as a directory drop with zero core patches. What
changes from PR #341:

- The kernel `_hive_page` route, the hardcoded rail button, and the
  landing-shell edits (what reddens #341's CI on the pinned landing tests) all
  go away, replaced by `manifest.json` + the generic `/plugin/hive` route.
- Hive's direct WS drive calls (sendMessage/endSession/rename/openSession) route
  through the broker instead; its cross-pane gestures (openPicker, reveal, focus
  chat) go through the UI relay.
- Its feed read is unchanged — it already consumes the feed payload.
- The bundled, unrelated auto-start change should be split into its own PR
  regardless.

## Existing features as plugins

Some, deliberately, later — not the spine. The optional panes (the timeline,
the usage/analytics graphs) are natural plugin candidates: they are already
panes-that-read-the-feed-and-issue-drive-ops, exactly the plugin contract. The
feed / needs-you board must stay privileged and non-toggleable — it is romp's
spine, the equivalent of Obsidian's editor, which Obsidian also keeps
non-optional. Converting the timeline to a core plugin is the best forcing
function to prove the API is complete (if it can host the timeline, it can host
anything), but the timeline needs a richer action vocabulary than Hive (model
and effort pickers, lane gears), so either the capability set grows to cover it
or internal panes stay privileged and only new/third-party surfaces take the
caged path. Recommended order: prove the API on Hive (external, minimal read +
drive), then attempt the timeline as a core plugin, and keep the feed privileged
throughout.

## Open questions

- **The feed payload becomes a public contract** once plugins depend on it. It
  needs a version field and a don't-break-it discipline, or a schema change
  silently breaks every plugin.
- **A richer capability tier** will be wanted later: plugins with their own
  persistent storage, a slash command, or a new card type. Hive needs none of
  these, which is why it validates the minimal read + drive tier first.
