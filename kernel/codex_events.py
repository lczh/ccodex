#!/usr/bin/env python3
"""codex_events — Codex app-server v2 notifications → transcript-shaped records (plans/codex-backend.md).

The Codex backend materializes each thread as transcript JSONL under $STATE/codex/projects/, in the
SAME record vocabulary the Claude CLI writes, so the entire read side — the event model, the judges,
all three panes — parses Codex sessions unchanged. This module is that mapping: one per-thread state
machine over plain wire dicts, no IO and no SDK import, so the goldens run anywhere.

Wire shapes come from the openai-codex SDK's generated bindings (openai_codex/generated/v2_all.py,
pinned 0.144.4) — dicts exactly as received over JSON-RPC, camelCase field names. The mapping table
and what phase 1 deliberately skips live in plans/codex-backend.md; skipped item types are COUNTED
(`skipped`), never silently dropped, so the backend can log the vocabulary it isn't rendering yet.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

# A runaway command's aggregatedOutput could balloon the materialized transcript; the Claude CLI
# caps tool results too, so a cap keeps parity. The marker says what was dropped, per house rule.
TOOL_OUTPUT_CAP = 30000


def _cap(text):
    if len(text) <= TOOL_OUTPUT_CAP:
        return text
    return text[:TOOL_OUTPUT_CAP] + "\n… [romp: output truncated at %d chars]" % TOOL_OUTPUT_CAP


def _iso(ms, clock):
    """Record timestamps, Claude transcript format (…T…S.mmmZ). Codex stamps item lifecycles with
    epoch millis (startedAtMs/completedAtMs); events with no wire time (thread/compacted) take the
    injected clock, so the goldens run on a fixed one."""
    if ms is None:
        ms = int(clock() * 1000)
    dt = datetime.fromtimestamp(ms / 1000, timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + ("%03dZ" % (ms % 1000))


def _user_input_text(content):
    """The prompt text of a userMessage item's UserInput list. Non-text inputs keep a readable
    placeholder (the Claude CLI does the same for pasted images)."""
    parts = []
    for c in content or []:
        t = (c or {}).get("type")
        if t == "text" and c.get("text"):
            parts.append(c["text"])
        elif t in ("image", "localImage"):
            parts.append("[Image: %s]" % (c.get("path") or c.get("url") or "pasted"))
        elif t in ("skill", "mention") and c.get("name"):
            parts.append(c["name"])
    return "\n".join(p for p in parts if p).strip()


def _mcp_text(item):
    """One text blob for an mcpToolCall's outcome: the error message when it failed, else the
    result's MCP content blocks (text kept, the rest summarized by type)."""
    err = item.get("error") or {}
    if err.get("message"):
        return err["message"]
    res = item.get("result") or {}
    parts = []
    for b in res.get("content") or []:
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
            parts.append(b["text"])
        elif isinstance(b, dict) and b.get("type"):
            parts.append("[%s]" % b["type"])
    if not parts and res.get("structuredContent") is not None:
        parts.append(json.dumps(res["structuredContent"], ensure_ascii=False, default=str))
    return "\n".join(parts)


def usage_from_token_usage(token_usage):
    """ThreadTokenUsage.last → the Anthropic usage keys the cost/token readers already understand
    (kernel _session_tokens). reasoningOutputTokens is folded into output (that is what it is)."""
    last = (token_usage or {}).get("last") or {}
    if not last:
        return None
    return {"input_tokens": last.get("inputTokens", 0),
            "output_tokens": last.get("outputTokens", 0) + last.get("reasoningOutputTokens", 0),
            "cache_read_input_tokens": last.get("cachedInputTokens", 0),
            "cache_creation_input_tokens": 0}


class ThreadNormalizer:
    """One Codex thread's notification → record state machine (single writer, append-only file).

    The one stateful trick: a turn's FINAL agentMessage must land already stamped
    stop_reason:"end_turn" — records are append-only, there is no retro-stamp — so a completed
    agentMessage is HELD and flushed by whatever comes next: a further item flushes it
    stop_reason:null (mid-turn text, exactly how the Claude CLI writes it), turn/completed flushes
    it "end_turn" (+ usage). Everything else is a straight mapping; see plans/codex-backend.md.
    """

    def __init__(self, thread_id, cwd="", version="", model="", last_uuid=None, clock=time.time):
        self.thread_id = thread_id
        self.cwd = cwd
        self.version = version        # codex CLI version, stamped like the Claude CLI's `version`
        self.model = model            # kept current by the backend; assistant records carry it
        self.clock = clock
        self.last_uuid = last_uuid    # chain anchor; the backend re-anchors on the file tail at resume
        self.turn_id = None
        self.turn_open = False        # turn/started..turn/completed bracket (the backend's busy signal)
        self.skipped = {}             # phase-2 item vocabulary seen on the wire: type -> count
        self._usage = None            # newest thread/tokenUsage/updated, stamped onto the turn's settle
        self.context = None           # (total tokens, model context window) for live_sessions' context %
        self._pending = None          # the held agentMessage: (uuid, ts_ms, text)
        self._tool_open = set()       # item ids whose tool_use record is already on disk
        self._seen_user = set()       # userMessage ids already written (they arrive started AND completed)

    # ── record builders (every record advances the uuid chain) ────────────────────────────────
    def _base(self, rtype, uuid, ts_ms):
        r = {"type": rtype, "uuid": uuid, "parentUuid": self.last_uuid,
             "timestamp": _iso(ts_ms, self.clock), "sessionId": self.thread_id,
             "cwd": self.cwd, "version": self.version}
        self.last_uuid = uuid
        return r

    def _user(self, uuid, ts_ms, blocks, prompt_source="sdk"):
        r = self._base("user", uuid, ts_ms)
        r["message"] = {"role": "user", "content": blocks}
        if prompt_source:
            # "sdk" = the human on a programmatic session — author_of(sdk_human=True) renders it
            # as the human bubble, the same convention the SDK backend's sessions use.
            r["promptSource"] = prompt_source
        return r

    def _assistant(self, uuid, ts_ms, blocks, stop=None, usage=None):
        m = {"role": "assistant", "content": blocks, "stop_reason": stop}
        if self.model:
            m["model"] = self.model
        if usage:
            m["usage"] = usage
        r = self._base("assistant", uuid, ts_ms)
        r["message"] = m
        return r

    def _tool_use(self, iid, ts_ms, name, tool_input):
        self._tool_open.add(iid)
        return self._assistant(iid, ts_ms,
                               [{"type": "tool_use", "id": iid, "name": name, "input": tool_input}])

    def _tool_result(self, iid, ts_ms, text, is_error=False, raw=None):
        block = {"type": "tool_result", "tool_use_id": iid,
                 "content": [{"type": "text", "text": text}]}
        if is_error:
            block["is_error"] = True
        r = self._user(iid + "-r", ts_ms, [block], prompt_source=None)
        if raw is not None:
            r["toolUseResult"] = raw   # the raw Codex payload, where the kernel's readers look for it
        return r

    def _flush(self, stop=None, usage=None):
        """Write out the held agentMessage (see class docstring)."""
        if not self._pending:
            return []
        uuid, ts_ms, text = self._pending
        self._pending = None
        return [self._assistant(uuid, ts_ms, [{"type": "text", "text": text}],
                                stop=stop, usage=usage)]

    # ── the dispatcher ─────────────────────────────────────────────────────────────────────────
    def handle(self, method, params):
        """Map one notification to the records to APPEND (possibly []). The caller owns the file."""
        p = params or {}
        if method == "turn/started":
            self.turn_id = (p.get("turn") or {}).get("id")
            self.turn_open = True
            return []
        if method == "item/started":
            return self._item(p.get("item") or {}, p.get("startedAtMs"), started=True)
        if method == "item/completed":
            return self._item(p.get("item") or {}, p.get("completedAtMs"), started=False)
        if method == "turn/completed":
            self.turn_open = False
            return self._turn_completed(p.get("turn") or {})
        if method == "thread/compacted":
            return self._compacted(p)
        if method == "thread/tokenUsage/updated":
            tu = p.get("tokenUsage") or {}
            self._usage = usage_from_token_usage(tu)
            total = (tu.get("total") or {}).get("totalTokens")
            if total is not None:
                self.context = (total, tu.get("modelContextWindow"))
            return []
        if method == "error":
            return self._error(p)
        return []

    def _item(self, item, ts_ms, started):
        t = item.get("type")
        iid = item.get("id") or ("cx-%d" % int(ts_ms or 0))
        if t == "userMessage":
            if iid in self._seen_user:
                return []
            self._seen_user.add(iid)
            text = _user_input_text(item.get("content"))
            if not text:
                return []
            # a user item mid-turn is a steer — close any held text first, then the prompt record
            return self._flush() + [self._user(iid, ts_ms, [{"type": "text", "text": text}])]
        if started:
            # calls whose INVOCATION should show the moment it starts (long commands stay visible
            # while they run, like a Claude tool_use does); results land at completed.
            if t == "commandExecution":
                return self._flush() + [self._tool_use(iid, ts_ms, "Bash",
                                                       {"command": item.get("command") or ""})]
            if t == "webSearch":
                return self._flush() + [self._tool_use(iid, ts_ms, "WebSearch",
                                                       {"query": item.get("query") or ""})]
            if t == "mcpToolCall":
                name = "mcp__%s__%s" % (item.get("server") or "?", item.get("tool") or "?")
                args = item.get("arguments")
                return self._flush() + [self._tool_use(iid, ts_ms, name,
                                                       args if isinstance(args, dict) else
                                                       ({"arguments": args} if args is not None else {}))]
            return []   # everything else is written at completed, when its content is final
        # ── item/completed ──
        if t == "agentMessage":
            out = self._flush()
            if item.get("text"):
                self._pending = (iid, ts_ms, item["text"])
            return out
        if t == "reasoning":
            parts = [s for s in (item.get("content") or []) if s] or \
                    [s for s in (item.get("summary") or []) if s]
            if not parts:
                return []
            return self._flush() + [self._assistant(iid, ts_ms,
                                                    [{"type": "thinking",
                                                      "thinking": "\n\n".join(parts)}])]
        if t == "commandExecution":
            out = self._flush()
            if iid not in self._tool_open:   # attached mid-turn: write the invocation first
                out.append(self._tool_use(iid, ts_ms, "Bash", {"command": item.get("command") or ""}))
            exit_code = item.get("exitCode")
            out.append(self._tool_result(iid, ts_ms, _cap(item.get("aggregatedOutput") or ""),
                                         is_error=(exit_code not in (0, None)),
                                         raw={"exitCode": exit_code,
                                              "durationMs": item.get("durationMs")}))
            return out
        if t == "fileChange":
            out = self._flush()
            for i, ch in enumerate(item.get("changes") or []):
                cid = "%s-%d" % (iid, i)
                kind = ch.get("kind")
                kind = kind.get("type") if isinstance(kind, dict) else kind
                name = "Write" if kind == "add" else "Edit"
                out.append(self._tool_use(cid, ts_ms, name, {"file_path": ch.get("path") or ""}))
                out.append(self._tool_result(cid, ts_ms, _cap(ch.get("diff") or ""),
                                             raw={"kind": kind, "status": item.get("status")}))
            return out
        if t == "mcpToolCall":
            out = self._flush()
            if iid not in self._tool_open:
                name = "mcp__%s__%s" % (item.get("server") or "?", item.get("tool") or "?")
                args = item.get("arguments")
                out.append(self._tool_use(iid, ts_ms, name,
                                          args if isinstance(args, dict) else
                                          ({"arguments": args} if args is not None else {})))
            out.append(self._tool_result(iid, ts_ms, _cap(_mcp_text(item)),
                                         is_error=bool(item.get("error"))))
            return out
        if t == "webSearch":
            out = self._flush()
            if iid not in self._tool_open:
                out.append(self._tool_use(iid, ts_ms, "WebSearch", {"query": item.get("query") or ""}))
            out.append(self._tool_result(iid, ts_ms, "done"))
            return out
        if t == "contextCompaction":
            return []   # thread/compacted writes the boundary; two writers would double it
        # phase-2 vocabulary (plan, subAgentActivity, collabAgentToolCall, imageView, …) — counted
        if t:
            self.skipped[t] = self.skipped.get(t, 0) + 1
        return []

    def _turn_completed(self, turn):
        status = turn.get("status")
        err = turn.get("error") or {}
        if status == "failed" or err.get("message"):
            # the failure settle: an error-flagged assistant record, the SAME tag the file adapter
            # turns into the API-error card (isApiErrorMessage → isApiError atom)
            out = self._flush()
            rec = self._assistant("%s-err" % (turn.get("id") or self.turn_id or "turn"), None,
                                  [{"type": "text", "text": err.get("message") or "turn failed"}],
                                  stop="end_turn")
            rec["isApiErrorMessage"] = True
            out.append(rec)
            return out
        # completed or interrupted: the held text (partial, on an interrupt) is the settle.
        # A turn with NO held text (interrupt mid-tool) writes nothing — the file turn stays
        # unterminated by design; liveness comes from the notifications, not the file (plan doc).
        usage, self._usage = self._usage, None
        return self._flush(stop="end_turn", usage=usage)

    def _compacted(self, p):
        # the compaction stitch: parentUuid null + logicalParentUuid = pre-compaction leaf, the
        # exact shape the FileAdapter's walk follows (event_model parent_of)
        uuid = "cb-%s" % (p.get("turnId") or self.turn_id or "0")
        rec = {"type": "system", "subtype": "compact_boundary", "uuid": uuid, "parentUuid": None,
               "logicalParentUuid": self.last_uuid, "timestamp": _iso(None, self.clock),
               "sessionId": self.thread_id, "cwd": self.cwd, "version": self.version,
               "isMeta": False, "compactMetadata": {"trigger": "auto"}}
        # Codex exposes no compaction summary text — no isCompactSummary record follows, and the
        # card's "what compaction kept" stays absent for Codex sessions (absent, not faked).
        self.last_uuid = uuid
        return [rec]

    def _error(self, p):
        if p.get("willRetry"):
            return []   # transient — the backend may chip it; the transcript records outcomes
        err = (p.get("error") or {})
        out = self._flush()
        rec = self._assistant("err-%s" % (p.get("turnId") or self.turn_id or "0"), None,
                              [{"type": "text", "text": err.get("message") or "error"}],
                              stop="end_turn")
        rec["isApiErrorMessage"] = True
        out.append(rec)
        return out
