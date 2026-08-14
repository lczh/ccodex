# The Codex session backend

A third `SessionBackend` that drives **OpenAI Codex** sessions, so Codex agents
sit on the same board as Claude sessions — same lanes, cards, chat, judges, and
postal bus. Follows the SDK-backend playbook (`plans/sdk-backend.md`): an exact,
event-based control channel, no TUI scraping.

**Decision: the official `openai-codex` Python SDK** (PyPI, published from
`github.com/openai/codex`, versioned in lockstep with the CLI; it bundles its
own `codex` binary via the `openai-codex-cli-bin` dependency). Its sync
`CodexClient` speaks JSON-RPC to `codex app-server` over stdio and matches the
ABC's sync surface — no asyncio bridge needed. Rejected alternatives:

- **TS SDK** (`@openai/codex-sdk`): wrong language for the kernel.
- **Driving the Codex TUI in tmux**: re-creates the screen-scraping layer the
  SDK backend exists to retire, against a TUI we know even less well.
- **Tailing Codex's own rollout JSONL** (`~/.codex/sessions/...`): a lossy
  reconstruction of an internal format, when a designed API exists. The
  authoritative-sources rule says use the API.
- **The unofficial `codex-sdk` / `openai-codex-sdk` PyPI packages**: the first
  is an unrelated product, the second an OpenAI-mimicking wrapper with no
  provenance. Neither is OpenAI's.

Pin `openai-codex==0.144.4` in a dedicated venv (`$STATE/codexvenv`, a sibling
of `sdkvenv`, built by `bin/romp-codex-setup`), because `codex app-server`
self-describes as *experimental*: version drift is a real risk and the pin
makes upgrades deliberate.

## The load-bearing move: normalize at write time

romp's entire read side — the event model, the judges, all three panes — consumes
transcript JSONL. The SDK backend already converts live SDK messages into
**transcript-shaped dicts** so the live stream and the file agree
(`sdk_backend.py` `_msg_to_atom`). The Codex backend extends that one step:
it converts app-server notifications into the same transcript-shaped records
and **materializes them as the session's JSONL on disk**, at

    $STATE/codex/projects/<enc-cwd>/<thread-id>.jsonl

- **fsid := the Codex thread id** (UUIDv7). One file per thread, append-only,
  a linear `uuid → parentUuid` chain (no forks in phase 1). Resume appends to
  the same file — Codex thread ids are stable across resumes.
- The source is the **designed API** (app-server notifications), not a scrape;
  the materialized file is romp's own event log, exactly like the
  `states/<sid>.jsonl` idle atoms the parser already reads. Codex's rollout
  file remains the resume source of truth (`thread/resume` by id); we never
  parse it.
- The read side then needs ONE new fact — a second transcript root — learned in
  one helper (a root LIST), not a fourth copy of the cwd-encoding.

## Verified mechanics

Probed against **CLI 0.147.0** and the **generated protocol bindings** shipped
in `openai-codex` 0.144.4 (`openai_codex/generated/v2_all.py` +
`notification_registry.py`) — the bindings are generated from the server's own
schema, so field names below are wire-exact.

**Live-verified against the running app-server (2026-08-13, no login):**
`CodexClient.start()` + `initialize` handshake (SDK 0.144.4 ↔ CLI 0.147.0),
`account_read` on a login-less box returns exactly the auth-gate signature the
backend keys on (`requiresOpenaiAuth: true`, `account: null`), `model_list`
works unauthenticated (gpt-5.6-sol/terra/luna, 5.5, 5.2), and `thread_start`
accepts this plan's param spelling verbatim (`{"cwd", "approvalPolicy":
"never", "sandbox": "workspace-write"}`) and mints a UUIDv7 thread id with the
default model.

**Live smoke, logged in (2026-08-13, `tests/smoke_codex_live.py`):** two real
turns end-to-end — a file-writing task whose Write items materialized as
tool_use/tool_result pairs and whose settle carried usage + context %, and a
genuinely-running `sleep 300` interrupted live (`turn/interrupt` → interrupted
settle → the `[Request interrupted by user]` record → the NEXT prompt opens
its own parsed turn). Both turns parse ended through the real event model;
the uuid chain stays linear; the normalizer's skipped-vocabulary counter came
back empty. Failed patches on a sandbox-less box rendered as `is_error`
results with the failure text — visible, never a fake success.

**Host requirement (found live):** Codex's Linux sandbox is bubblewrap, which
needs unprivileged user namespaces. Newer GCP/Ubuntu images restrict them
(`kernel.apparmor_restrict_unprivileged_userns=1`) and then EVERY command and
patch under `workspaceWrite` fails with `bwrap: setting up uid map: Permission
denied` — loudly, as error-flagged results. Sandboxed operation needs the
sysctl flipped (persist via /etc/sysctl.d); the smoke's
`ROMP_SMOKE_SANDBOX=danger-full-access` override exercises the same protocol
machinery on such boxes without it.

- **Client**: `CodexClient` — `thread_start/resume/fork/list/read/set_name/
  compact`, `turn_start(thread_id, input, params)`, `turn_interrupt(thread_id,
  turn_id)`, `turn_steer`, `model_list`, `next_notification()` queues, login
  APIs (`account_login_start`, API-key and ChatGPT device-code flows).
- **Per-turn params**: model, reasoning effort, cwd, `approvalPolicy`
  (never/onRequest/unlessTrusted), `sandboxPolicy` (readOnly/workspaceWrite/
  dangerFullAccess), output schema.
- **Notifications** (exact methods): `turn/started`, `turn/completed`,
  `item/started`, `item/completed` (+ delta streams), `thread/compacted`,
  `thread/tokenUsage/updated`, `account/rateLimits/updated`, `error`
  (with `willRetry`), `thread/status/changed`, approval server-requests.
- **Item vocabulary** (`ThreadItem` variants): `userMessage`, `agentMessage`,
  `reasoning`, `commandExecution` (command, cwd, aggregatedOutput, exitCode,
  durationMs, status), `fileChange` (changes: [{path, kind, diff}], status),
  `mcpToolCall` (server, tool, arguments, result, error), `webSearch`, `plan`,
  `subAgentActivity`, `collabAgentToolCall` (Codex's own multi-agent channel),
  `contextCompaction`, `imageView`, `imageGeneration`, review-mode markers.
- **Timestamps**: items carry `startedAtMs`/`completedAtMs`; turns carry
  `completedAt` (s) — records get real times, no clock guessing.

## The mapping (codex_events.py, pure + golden-tested)

| Codex event | Transcript record |
|---|---|
| `item/*` userMessage | `user` record, `promptSource:"sdk"` (human on a programmatic session, same as the SDK backend) |
| `item/completed` agentMessage | assistant text — HELD, flushed with `stop_reason:null` when more items follow, `"end_turn"` at `turn/completed` (records are append-only; the turn's last message must land already-stamped) |
| `item/completed` reasoning | assistant `thinking` block |
| `item/started` commandExecution | assistant `tool_use` (name `Bash`, input {command}) |
| `item/completed` commandExecution | user `tool_result` (aggregatedOutput, exit code) + raw item under `toolUseResult` |
| `item/*` fileChange | per change: `tool_use` (add→`Write`, else `Edit`, input {file_path}) + `tool_result` carrying the diff |
| `item/*` mcpToolCall | `tool_use` named `mcp__<server>__<tool>` + `tool_result` (result or error) |
| `item/*` webSearch | `tool_use` `WebSearch` {query} + result |
| `thread/compacted` | `system`/`compact_boundary` with `logicalParentUuid` = pre-compaction leaf (the stitch the FileAdapter follows) |
| turn failed / terminal `error` | assistant record flagged `isApiErrorMessage` (the error-card tag) |
| `thread/tokenUsage/updated` | not a record — feeds `live_sessions().context` |
| `plan`, `subAgentActivity`, `collabAgentToolCall` | **phase 2** (skipped, logged once) |

`uuid` = the Codex item id; `parentUuid` = previous record in the file (the
writer re-anchors on the file's last uuid at resume/restart).

## ABC coverage (phase 1)

Real: `owns`, `live_sessions` (state from turn/thread notifications — never the
file), `send` (turn_start; mid-turn → `turn/steer`), `interrupt`, `busy`,
`spawn`, `resume`, `kill` (close + archive), `rename` (`thread/setName`),
`set_model`, `set_effort` (low/medium/high pass through per-turn),
`pending_queued`, `live_atoms`, `prune_live`, `launch_error` (missing
login/binary surfaces as text, incl. `limit:true` on out-of-usage),
`working_note`/`set_working_note`/`wake`/`deliver` (kernel-side store +
enqueue, as the SDK backend).

Documented-empty (loud, not faked): `set_fast` False (no Codex equivalent),
`set_mode` False in phase 1 — spawn pins `approvalPolicy:never` +
`sandboxPolicy:workspaceWrite` (sandboxed full-auto; the approval→ask-picker
bridge is phase 2), `set_auth` False (Codex auth is machine-global via
`codex login`), `stop_task`/`rewind_files` False, `mcp_status` explains Codex
MCP servers live in `~/.codex/config.toml`, `on_ask`/`current_ask` None.

## Phase 2 (explicitly out)

Approval server-requests → the `permission` chip + ask picker; plan items →
the card checklist; `subAgentActivity`/collab → subagent pills; rate-limit
gating from `account/rateLimits/updated`; `usage` stamping for the cost view;
unified-diff → `structuredPatch` rows; per-backend model catalogs in `/models`;
postal MCP auto-registration into `~/.codex/config.toml`; the new-session
picker's agent choice UI polish.

## Risks / open questions

- `codex app-server` is labeled experimental; the venv pin keeps upgrades
  deliberate. (A boot version check against a tested floor was considered and
  is NOT implemented — the pin alone bounds drift while the SDK and its
  bundled binary move together.)
- A turn that ends with no final agentMessage (interrupt mid-tool) leaves the
  file turn unterminated; state stays correct (it comes from notifications).
  Revisit if it confuses the judges.
- Codex compaction exposes no summary text — the card's "what compaction kept"
  tab stays empty for Codex sessions (absent, not faked).
