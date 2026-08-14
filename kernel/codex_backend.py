#!/usr/bin/env python3
"""codex_backend — the Codex SessionBackend (plans/codex-backend.md).

Drives OpenAI Codex sessions through the official openai-codex Python SDK's sync CodexClient
(JSON-RPC to `codex app-server` over stdio) and materializes each thread as Claude-transcript-shaped
JSONL via kernel/codex_events.ThreadNormalizer, so romp's entire read side parses Codex sessions
unchanged. Duck-types the SessionBackend ABC exactly like SdkBackend does (the kernel loads backends
via SourceFileLoader; a conformance test asserts every abstract method exists).

Shape of the machine:
- ONE CodexClient per backend — the app-server hosts many threads, unlike Claude's one-CLI-per-
  session. One GLOBAL pump thread drains thread-level notifications (tokenUsage, rateLimits);
  each session gets a WORKER thread that drains its send-queue into turns and consumes that turn's
  own notification queue (the SDK routes a started turn's events there, not to the global queue).
- The worker and global pump serialize through one per-session normalizer/file lock:
  notification → normalizer → append → poke the kernel.
- Sends NEVER block on a running turn: mid-turn they steer (turn/steer with the active turn id as
  precondition), racing a just-ended turn falls back to the queue, and the queue drains into the
  next turn_start — that is forwards_sends() on this backend.
- Auth is machine-global (`codex login`): a missing login is surfaced PER SESSION via launch_error,
  loudly, the moment a session tries to run (the 2026-07-28 rule: never a silent non-start).

Everything Claude-only returns its documented empty value and the kernel stays loud about it:
set_fast/set_mode/set_auth/stop_task/rewind_files → False, on_ask → False, current_ask → None.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import traceback
import uuid as uuidlib
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = Path(os.path.dirname(os.path.realpath(__file__)))
_events = SourceFileLoader("romp_codex_events", str(HERE / "codex_events.py")).load_module()

SDK_PIN = "openai-codex==0.144.4"     # bin/romp-codex-setup installs exactly this into codexvenv
SETUP_HINT = ("Session not created: the Codex backend isn't installed. "
              "Run ccodex-setup, then try again.")
LOGIN_HINT = "Codex isn't logged in on this machine — run: codex login"

# The phase-1 posture: sandboxed full-auto (plans/codex-backend.md). In pinned 0.144.4, BOTH legacy
# workspaceWrite and the built-in :workspace profile include `:root = read`; runtimeWorkspaceRoots
# only adds roots and cannot subtract that host-wide read. Define a fail-closed custom profile:
# minimal runtime files are readable, the session workspace is writable, and network remains enabled
# for git/web. Pinned 0.144.4 cannot enforce narrower child access inside a custom writable root, so
# metadata directories remain writable (documented in docs/codex.md) rather than carrying misleading
# read-only entries that its arbitrary-process sandbox ignores.
WORKSPACE_PERMISSION = "ccodex_workspace"
_WORKSPACE_PROFILE_OVERRIDE = (
    'permissions.ccodex_workspace={ filesystem = { ":minimal" = "read", '
    '":workspace_roots" = { "." = "write" } }, network = { enabled = true } }')
# 0.144.4 rejects any custom [permissions] table unless default_permissions is also selected.
# Selecting it globally is a defense in depth; thread/resume/turn still name it explicitly.
CODEX_CONFIG_OVERRIDES = (_WORKSPACE_PROFILE_OVERRIDE,
                          'default_permissions="ccodex_workspace"')
TURN_SANDBOX = None   # explicit smoke-only legacy override (for hosts unable to run the sandbox)
APPROVAL_POLICY = "never"
# romp effort names → Codex ReasoningEffort. Identity for the shared four; max/ultracode are
# Claude-only knobs and set_effort refuses them (False → the kernel warns instead of pretending).
EFFORTS = ("low", "medium", "high", "xhigh")

SEED_TAIL = 200   # records whose uuids seed the normalizer's dedup on re-attach (replay guard)
CLIENT_RETRY_MIN = 0.25
CLIENT_RETRY_MAX = 5.0
WORKER_JOIN_TIMEOUT = 2.0

_PERMANENT_RPC_ERRORS = {"ParseError", "InvalidRequestError", "MethodNotFoundError",
                         "InvalidParamsError"}
_PERMANENT_RPC_TEXT = (
    re.compile(r"\b(?:unknown|unsupported|invalid) (?:model|permission|approval|sandbox)\b", re.I),
    re.compile(r"\bmodel\b.*\b(?:does not exist|is not supported|not allowed)\b", re.I),
    re.compile(r"\bpermission profile\b.*\b(?:not found|does not exist|is not supported)\b", re.I),
)


def _is_permanent_turn_rejection(error):
    """Whether turn/start reached app-server and was rejected non-retryably.

    The pinned SDK gives request-shape/method/parameter failures distinct types. InternalRpcError,
    ServerBusyError and unknown numeric app codes can recover without changing the request, so they
    deliberately remain on automatic backoff. A few app-specific model/policy rejections arrive as
    generic CodexRpcError; park only their unambiguous text. Keep this duck-typed so the backend can
    import before the optional SDK is installed.
    """
    name = error.__class__.__name__
    if name in _PERMANENT_RPC_ERRORS:
        return True
    if name not in {"JsonRpcError", "CodexRpcError"}:
        return False
    message = str(getattr(error, "message", "") or error)
    return any(pattern.search(message) for pattern in _PERMANENT_RPC_TEXT)


class _PermanentTurnStartRejection(RuntimeError):
    def __init__(self, cause, change_generation, client_generation):
        super().__init__(str(cause) or cause.__class__.__name__)
        self.change_generation = change_generation
        self.client_generation = client_generation


def _execution_permissions(cwd, thread_start=False):
    """Pinned-runtime execution policy. TURN_SANDBOX remains only for the live smoke's explicit
    dangerFullAccess escape hatch; normal sessions select the named profile and exactly one root."""
    if TURN_SANDBOX is not None:
        if thread_start:
            modes = {"dangerFullAccess": "danger-full-access", "readOnly": "read-only",
                     "workspaceWrite": "workspace-write"}
            return {"sandbox": modes.get(TURN_SANDBOX.get("type"), "workspace-write")}
        return {"sandboxPolicy": TURN_SANDBOX}
    root = str(Path(cwd or ".").resolve())
    return {"permissions": WORKSPACE_PERMISSION, "runtimeWorkspaceRoots": [root]}


def _codex_config(config_cls, codex_bin):
    """Build the pinned SDK launch config in one testable place.

    In 0.144.4, custom profiles are process config while each thread/turn supplies its runtime root.
    """
    return config_cls(codex_bin=codex_bin, client_name="romp",
                      config_overrides=CODEX_CONFIG_OVERRIDES)


def ensure_codex_sdk(state_dir):
    """Make openai_codex importable: an already-installed copy wins, else the dedicated venv built
    by bin/romp-codex-setup ($STATE/codexvenv — never system python). True when importable."""
    import importlib.util
    import glob
    if importlib.util.find_spec("openai_codex"):
        return True
    for sp in sorted(glob.glob(str(Path(state_dir) / "codexvenv" / "lib" / "python3.*" / "site-packages"))):
        if sp not in sys.path:
            sys.path.insert(0, sp)
    return importlib.util.find_spec("openai_codex") is not None


def _enc_cwd(cwd):
    """The transcript dir name for a cwd — the same encoding the Claude CLI uses for
    ~/.claude/projects (realpath, every non-alphanumeric → '-'), so tooling that already
    understands one layout understands the other."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(cwd or "/").resolve()))


def _dump(payload):
    """A notification payload → the camelCase wire dict the normalizer speaks. Known methods parse
    into pydantic models (model_dump by_alias restores the wire names); unknown ones keep raw params."""
    fn = getattr(payload, "model_dump", None)
    if fn:
        try:
            return fn(by_alias=True, mode="json")
        except Exception:
            return fn(by_alias=True)
    return getattr(payload, "params", None) or {}


def _tail_state(path):
    """(last_uuid, recent uuids) off a materialized file, to re-anchor the normalizer's chain and
    seed its replay dedup after a restart. Reads the whole file once; keeps only the tail's uuids —
    replay across a reconnect only ever re-delivers recent items."""
    last, tail = None, []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    u = json.loads(line).get("uuid")
                except Exception:
                    continue
                if u:
                    last = u
                    tail.append(u)
                    if len(tail) > SEED_TAIL:
                        tail.pop(0)
    except OSError:
        pass
    return last, set(tail)


class _Session:
    """One Codex session: registry row + runtime state. The worker thread owns the normalizer and
    the file; everything else only reads or enqueues."""

    def __init__(self, sid, tid, name, cwd, model="", effort="", color=""):
        self.sid = sid
        self.tid = tid                # Codex thread id == fsid == transcript filename stem
        self.name = name
        self.cwd = cwd
        self.model = model
        self.effort = effort
        self.color = color            # identity bg — also in names/<sid> (fields 3/4), the shared store
        self.dead = False
        self.state = "waiting"        # waiting | working (the two states this backend can know)
        self.since = time.time()
        self.queue = []               # pending sends (persisted); drained into the next turn
        self.echoes = []              # optimistic user-atom echoes ahead of the materialized file
        self.turn_id = None           # the active turn (interrupt/steer target), else None
        self.loaded = False           # thread/resume done in THIS process
        self.launch_error = None      # {text, at, limit} — why the session can't run, or None
        self.norm = None              # ThreadNormalizer, built by the worker on first need
        self.worker = None
        self.kick = threading.Event() # wake the worker (new send / resume / shutdown)
        self.change_generation = 0    # explicit send/model/effort/resume changes that may fix a rejection
        self.turn_rejection = None    # (change_generation, client_generation) parked until either changes
        self.note = ""                # postal working-note
        self.lock = threading.RLock()  # queue/turn/worker/state fields; lifecycle calls can nest
        self.norm_lock = threading.Lock()  # the turn worker and global pump share one normalizer


class CodexBackend:
    def __init__(self, state_dir, notify=None, poke=None, push=None, push_session=None,
                 codex_bin=None, log=None, client_factory=None):
        self.state = Path(state_dir)
        self.root = self.state / "codex"
        self.projects = self.root / "projects"
        self.projects.mkdir(parents=True, exist_ok=True)
        self.notify = notify or (lambda *a, **k: None)
        self.poke = poke or (lambda: None)
        self.push = push or (lambda: None)
        self.push_session = push_session or (lambda sid: None)
        self.codex_bin = codex_bin
        self.log = log or (lambda m: sys.stderr.write("codex-backend: %s\n" % m))
        self._client_factory = client_factory   # tests inject a fake; None → real CodexClient
        self._client = None
        self._client_err = None       # why the client can't be built/authed (str), or None
        self._client_retry_at = 0.0
        self._client_failures = 0
        self._client_generation = 0   # successful app-server client installations
        self._catalog = None          # model_catalog() cache — fetched once per process
        self._client_lock = threading.Lock()
        self._sessions = {}           # sid → _Session
        self._sessions_lock = threading.RLock()
        self._reg_lock = threading.Lock()
        self._load_registry()
        # A kernel restart must not strand a durable backend queue until the user happens to send
        # again. Re-arm every live queued session immediately; client retry backoff keeps failures cool.
        for _, s in self._session_items():
            with s.lock:
                recover = bool(s.queue) and not s.dead
            if recover:
                self._ensure_worker(s)
                s.kick.set()

    # ── registry persistence ─────────────────────────────────────────────────────────────────────
    def _reg_path(self):
        return self.root / "registry.json"

    def _load_registry(self):
        try:
            rows = json.loads(self._reg_path().read_text())
        except Exception:
            rows = {}
        with self._sessions_lock:
            for sid, r in rows.items():
                if not isinstance(r, dict) or not isinstance(r.get("tid"), str):
                    self.log("ignoring malformed Codex registry row: %s" % sid)
                    continue
                s = _Session(sid, r["tid"], r.get("name", ""), r.get("cwd", ""),
                             r.get("model", ""), r.get("effort", ""), r.get("color", ""))
                s.dead = bool(r.get("dead"))
                raw_queue = r.get("queue")
                s.queue = [t for t in raw_queue if isinstance(t, str) and t] \
                    if isinstance(raw_queue, list) else []
                s.note = r.get("note", "")
                s.launch_error = r.get("launchError") if isinstance(r.get("launchError"), dict) else None
                self._sessions[sid] = s

    def _session(self, sid):
        with self._sessions_lock:
            return self._sessions.get(sid)

    def _session_items(self):
        with self._sessions_lock:
            return list(self._sessions.items())

    def _put_session(self, s):
        with self._sessions_lock:
            self._sessions[s.sid] = s

    def _save_registry(self):
        # serialized: a handler thread (send) and a worker (batch pop) save concurrently, and two
        # unserialized writers shared one tmp path — the loser's os.replace found it already gone
        with self._reg_lock:
            rows = {}
            for sid, s in self._session_items():
                with s.lock:
                    rows[sid] = {"tid": s.tid, "name": s.name, "cwd": s.cwd,
                                 "model": s.model, "effort": s.effort, "dead": s.dead,
                                 "queue": list(s.queue), "note": s.note, "color": s.color,
                                 "launchError": s.launch_error}
            tmp = self._reg_path().with_name(
                "registry.tmp.%d.%s" % (os.getpid(), uuidlib.uuid4().hex[:8]))
            try:
                tmp.write_text(json.dumps(rows, indent=1))
                os.replace(tmp, self._reg_path())
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass

    # ── client lifecycle ─────────────────────────────────────────────────────────────────────────
    def available(self):
        """Can this backend actually RUN a session right now? (The creation gate — mirrors
        _sdk_ready's contract.) Building the client is the real probe; a failure is surfaced and
        retried after bounded exponential backoff, never in a hot loop."""
        return self._get_client() is not None

    def _get_client(self):
        with self._client_lock:
            if self._client is not None:
                return self._client
            if time.monotonic() < self._client_retry_at:
                return None           # remembered only until the retry deadline, not for the process
            candidate = None
            try:
                if self._client_factory:
                    candidate = self._client_factory()
                else:
                    if not ensure_codex_sdk(self.state):
                        raise RuntimeError(SETUP_HINT)
                    from openai_codex.client import CodexClient, CodexConfig
                    cfg = _codex_config(CodexConfig, self.codex_bin)
                    candidate = CodexClient(config=cfg)
                    candidate.start()
                    candidate.initialize()
                self._check_auth(candidate)
                self._client = candidate
                self._client_err = None
                self._client_retry_at = 0.0
                self._client_failures = 0
                self._client_generation += 1
                # A replacement app-server can make a previously deterministic rejection obsolete.
                # Wake parked queued workers; their generation gate decides whether to retry.
                for _, s in self._session_items():
                    s.kick.set()
                threading.Thread(target=self._global_pump, args=(candidate,), daemon=True,
                                 name="codex-pump").start()
                return candidate
            except Exception as e:
                self._record_client_failure_locked(e, candidate)
                return None

    def _record_client_failure_locked(self, error, candidate=None):
        """Record one failed client generation. Caller owns _client_lock."""
        self._client_err = str(error) or error.__class__.__name__
        self._client_failures += 1
        delay = min(CLIENT_RETRY_MAX,
                    CLIENT_RETRY_MIN * (2 ** min(self._client_failures - 1, 8)))
        self._client_retry_at = time.monotonic() + delay
        if candidate is None:
            candidate = self._client
        if candidate is self._client and self._client is not None:
            # Invalidation is itself a generation edge. The global pump wakes queued workers after
            # releasing this lock, so a permanently parked request can build the replacement client
            # automatically rather than waiting for an unrelated user action.
            self._client_generation += 1
            self._client = None
        if candidate is not None:
            try:
                candidate.close()
            except Exception:
                pass
        self.log("client unavailable: %s (retry in %.2fs)" % (self._client_err, delay))

    def _client_retry_remaining(self):
        with self._client_lock:
            return max(0.0, self._client_retry_at - time.monotonic())

    def _client_generation_now(self):
        with self._client_lock:
            return self._client_generation

    def _client_generation_for(self, client):
        with self._client_lock:
            # If it was invalidated between _get_client and turn/start, use a deliberately stale
            # sentinel. The worker then sees the generation mismatch and rebuilds automatically.
            return self._client_generation if self._client is client else -1

    def _check_auth(self, client):
        """A missing `codex login` must surface as text on the session, not as a hung turn."""
        try:
            acct = client.account_read()
        except Exception as e:
            self.log("account_read failed: %s" % e)
            return
        needs = getattr(acct, "requires_openai_auth", None)
        if needs and not getattr(acct, "account", None):
            raise RuntimeError(LOGIN_HINT)

    def _global_pump(self, client):
        """Drain notifications NOT routed to a registered turn (thread status, rate limits, token
        usage between turns). Feeds the owning session's normalizer so nothing is dropped."""
        while True:
            try:
                n = client.next_notification()
            except Exception as e:
                self.log("global pump stopped: %s" % e)
                with self._client_lock:
                    if self._client is client:
                        self._record_client_failure_locked(e, client)
                for _, s in self._session_items():
                    with s.lock:
                        queued = bool(s.queue) and not s.dead
                    if queued:
                        self._ensure_worker(s)
                        s.kick.set()
                return
            try:
                p = _dump(getattr(n, "payload", None))
                tid = p.get("threadId")
                s = next((s for _, s in self._session_items() if s.tid == tid), None)
                if s:
                    with s.norm_lock:
                        # Placeholder recovery replaces the normalizer under this same lock. Recheck
                        # after acquiring it: a pre-lock `s.norm` test could race to None here.
                        if s.norm:
                            recs = s.norm.handle(getattr(n, "method", ""), p)
                            if recs:
                                self._append(s, recs)
            except Exception:
                self.log("global pump: %s" % traceback.format_exc())

    # ── the materialized transcript ──────────────────────────────────────────────────────────────
    def transcript_path(self, sid):
        s = self._session(sid)
        if not s:
            return None
        with s.lock:
            cwd, tid = s.cwd, s.tid
        d = self.projects / _enc_cwd(cwd)
        d.mkdir(parents=True, exist_ok=True)
        return d / ("%s.jsonl" % tid)

    def _ensure_norm(self, s):
        with s.norm_lock:
            if s.norm is None:
                path = self.transcript_path(s.sid)
                last, seen = _tail_state(path)
                s.norm = _events.ThreadNormalizer(s.tid, cwd=s.cwd, model=s.model,
                                                  version="codex", last_uuid=last,
                                                  seen_uuids=seen)
            return s.norm

    def _append(self, s, recs):
        path = self.transcript_path(s.sid)
        with open(path, "a", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        # a landed user record replaces its optimistic echo (uuid-independent: match by text)
        landed = {self._rec_text(r) for r in recs if r.get("type") == "user"}
        if landed:
            with s.lock:
                s.echoes = [e for e in s.echoes if e["text"] not in landed]
        self.poke()
        self.push_session(s.sid)

    @staticmethod
    def _rec_text(rec):
        c = (rec.get("message") or {}).get("content")
        if isinstance(c, list):
            return " ".join(b.get("text", "") for b in c
                            if isinstance(b, dict) and b.get("type") == "text").strip()
        return (c or "").strip() if isinstance(c, str) else ""

    # ── liveness / identity ──────────────────────────────────────────────────────────────────────
    def owns(self, sid):
        s = self._session(sid)
        if not s:
            return False
        with s.lock:
            return not s.dead

    def live_sessions(self):
        out = {}
        for sid, s in self._session_items():
            with s.lock:
                if s.dead:
                    continue
                row = {"state": s.state, "model": s.model, "effort": s.effort,
                       "mode": "sandboxed", "since": s.since, "context": None,
                       "compactPct": None, "backend": "codex", "name": s.name,
                       "cwd": s.cwd, "color": s.color or None}
            with s.norm_lock:
                if s.norm and s.norm.context and s.norm.context[1]:
                    used, window = s.norm.context
                    row["context"] = max(0, min(100, round(100 * used / window)))
            out[sid] = row
        return out

    def busy(self, sid):
        s = self._session(sid)
        if not s:
            return None
        with s.lock:
            return None if s.dead else bool(s.turn_id or s.queue)

    # ── control ──────────────────────────────────────────────────────────────────────────────────
    def send(self, sid, text):
        s = self._session(sid)
        if not s or not text:
            return False
        c = self._get_client()
        with s.lock:
            if s.dead:
                return False
            s.echoes.append({"text": text.strip(), "t": time.time(),
                             "uuid": "echo-%s" % uuidlib.uuid4().hex[:8]})
            turn_id = s.turn_id
            tid = s.tid
        if c is not None and turn_id:
            # mid-turn: steer, with the active turn as precondition; a race with the turn's end
            # falls through to the queue (the worker delivers it in the next turn)
            try:
                c.turn_steer(tid, turn_id, [{"type": "text", "text": text}])
                return True
            except Exception:
                pass
        with s.lock:
            if s.dead:
                s.echoes = [e for e in s.echoes if e["text"] != text.strip()]
                return False
            s.queue.append(text)
            s.change_generation += 1
        self._save_registry()
        self._ensure_worker(s)
        s.kick.set()
        return True

    def interrupt(self, sid):
        s = self._session(sid)
        if not s:
            return False
        with s.lock:
            if s.dead or not s.turn_id:
                return False
            tid, turn_id = s.tid, s.turn_id
        c = self._client
        if c is None:
            return False
        try:
            c.turn_interrupt(tid, turn_id)
            return True
        except Exception as e:
            self.log("interrupt %s: %s" % (s.name, e))
            return False

    def set_model(self, sid, value):
        s = self._session(sid)
        if not s or not value or not str(value).startswith("gpt"):
            # a Claude alias (the other engine's vocabulary — a mis-aimed menu or script) would ride
            # the next turn_start straight into a 400 that breaks the session's next turn; refusing
            # here keeps the failure a loud kernel warn instead (2026-08-14 UI review)
            return False
        with s.lock:
            s.model = value                # applied on the next turn_start; Codex persists it
            s.change_generation += 1
            queued = bool(s.queue) and not s.dead
        with s.norm_lock:
            if s.norm:
                s.norm.model = value
        self._save_registry()
        if queued:
            self._ensure_worker(s)
            s.kick.set()
        return True

    def model_catalog(self):
        """[{value,label}] for the UI's model picker — the app-server's own model list (the ONE
        authoritative source), fetched once per process and cached. [] when the client is
        unavailable (the picker then shows nothing rather than another vendor's list). A plan
        account may still refuse some listed models per turn — that failure surfaces loudly as
        the turn's error card, and switching back is one click."""
        if self._catalog is not None:
            return self._catalog
        c = self._get_client()
        if c is None:
            return []
        try:
            ms = c.model_list()
            self._catalog = [{"value": m.id, "label": getattr(m, "display_name", None) or m.id}
                             for m in (getattr(ms, "data", None) or [])
                             if not getattr(m, "hidden", False)]
        except Exception as e:
            self.log("model_list failed: %s" % e)
            return []
        return self._catalog

    def set_mode(self, sid, mode):
        return False   # phase 1 pins approval never + the custom permission profile; no live knob

    def set_effort(self, sid, value):
        s = self._session(sid)
        if not s or value not in EFFORTS:
            return False                   # max/ultracode are Claude-only — refuse loudly
        with s.lock:
            s.effort = value
            s.change_generation += 1
            queued = bool(s.queue) and not s.dead
        self._save_registry()
        if queued:
            self._ensure_worker(s)
            s.kick.set()
        return True

    def set_fast(self, sid, value):
        return False   # no Codex equivalent

    # ── lifecycle ────────────────────────────────────────────────────────────────────────────────
    def _write_name(self, s, bg="", fg=""):
        """The shared identity/discovery file names/<sid> (name, cwd, bg, fg) — the same four-field
        format both other backends write, so name/identity surfaces read Codex sessions for free.
        Discovery itself finds Codex transcripts via the codex registry, not this file."""
        d = self.state / "names"
        d.mkdir(parents=True, exist_ok=True)
        try:
            old = (d / s.sid).read_text().rstrip("\n").split("\t")
        except OSError:
            old = []
        bg = bg or (old[2] if len(old) > 2 else "")
        fg = fg or (old[3] if len(old) > 3 else "")
        (d / s.sid).write_text("%s\t%s\t%s\t%s\n" % (s.name, s.cwd, bg, fg))

    def spawn(self, name, cwd, bg="", fg="", sid=None, auth=""):
        sid = sid or str(uuidlib.uuid4())
        c = self._get_client()
        if c is None:
            # the entry still exists so the failure is VISIBLE on the lane (launch_error),
            # never a silently-missing session
            s = _Session(sid, "pending-%s" % sid[:8], name, cwd)
            s.launch_error = {"text": self._client_err or SETUP_HINT, "at": time.time(),
                              "limit": False}
            self._put_session(s)
            self._save_registry()
            return sid
        try:
            resp = c.thread_start({"cwd": cwd, "approvalPolicy": APPROVAL_POLICY,
                                   **_execution_permissions(cwd, thread_start=True)})
            tid = resp.thread.id
            model = getattr(resp, "model", "") or ""
        except Exception as e:
            s = _Session(sid, "failed-%s" % sid[:8], name, cwd)
            s.launch_error = {"text": "codex thread/start failed: %s" % e, "at": time.time(),
                              "limit": False}
            self._put_session(s)
            self._save_registry()
            return sid
        s = _Session(sid, tid, name, cwd, model=model, color=bg)
        s.loaded = True
        self._put_session(s)
        self._ensure_norm(s)
        # touch the materialized transcript NOW: discovery lists real files, and an empty jsonl
        # parses to an empty session — the tab opens immediately instead of waiting for turn one
        self.transcript_path(sid).touch()
        self._write_name(s, bg, fg)
        self._save_registry()
        self.push()
        return sid

    def resume(self, name, sid, cwd=None):
        s = self._session(sid)
        if not s:
            return False
        with s.lock:
            s.dead = False
            s.loaded = False               # the worker thread/resumes before the next turn
            s.name = name or s.name
            if cwd:
                s.cwd = cwd
            s.state = "waiting"
            s.since = time.time()
            s.change_generation += 1
            queued = bool(s.queue)
        self._save_registry()
        if queued:
            self._ensure_worker(s)
            s.kick.set()
        return True

    def kill(self, sid):
        s = self._session(sid)
        if not s:
            return False
        with s.lock:
            s.dead = True
            turn_id, tid, worker = s.turn_id, s.tid, s.worker
        c = self._client
        if turn_id and c is not None:
            try:
                c.turn_interrupt(tid, turn_id)
            except Exception as e:
                self.log("kill interrupt %s: %s" % (s.name, e))
        s.kick.set()                       # wake a retry wait or idle worker so it can exit
        if worker and worker is not threading.current_thread():
            worker.join(WORKER_JOIN_TIMEOUT)
        worker_stopped = not worker or not worker.is_alive()
        if not worker_stopped:
            self.log("worker did not stop after kill: %s" % s.name)
        if worker_stopped:
            with s.norm_lock:
                held = s.norm.drain() if s.norm else []
                if held:
                    self._append(s, held)  # never eat a held final message; serialize file appends
        self._save_registry()
        return True

    def rename(self, sid, new_name):
        s = self._session(sid)
        if not s:
            return False
        with s.lock:
            s.name = new_name
            tid = s.tid
        self._write_name(s)               # keep the shared identity file in sync (colours preserved)
        self._save_registry()
        c = self._client
        if c is not None:
            try:
                c.thread_set_name(tid, new_name)
            except Exception:
                pass                       # cosmetic on the Codex side; romp's registry is the truth
        return True

    # ── the per-session worker: queue → turns → records ────────────────────────────────────────
    def _ensure_worker(self, s):
        with s.lock:
            if s.worker and s.worker.is_alive():
                return
            s.worker = threading.Thread(target=self._work, args=(s,), daemon=True,
                                        name="codex-%s" % s.name)
            s.worker.start()

    def _prepare_thread(self, s, c):
        """Resume a durable thread, or turn a visible pending/failed placeholder into a real one."""
        with s.lock:
            if s.dead:
                return False
            tid, cwd = s.tid, s.cwd
            create = tid.startswith("pending-") or tid.startswith("failed-")
        if create:
            resp = c.thread_start({"cwd": cwd, "approvalPolicy": APPROVAL_POLICY,
                                   **_execution_permissions(cwd, thread_start=True)})
            with s.lock:
                if s.dead:
                    return False
                s.tid = resp.thread.id
                s.model = getattr(resp, "model", "") or s.model
                s.loaded = True
            with s.norm_lock:
                s.norm = None
            self._ensure_norm(s)
            self.transcript_path(s.sid).touch()
            self._write_name(s)
            self._save_registry()
            self.push()
            return True
        c.thread_resume(tid, {"cwd": cwd, **_execution_permissions(cwd, thread_start=True)})
        with s.lock:
            if s.dead:
                return False
            s.loaded = True
        return True

    def _work(self, s):
        retry_delay = CLIENT_RETRY_MIN
        try:
            while True:
                s.kick.wait()
                s.kick.clear()
                while True:
                    with s.lock:
                        if s.dead:
                            return
                        queued = bool(s.queue)
                        rejection = s.turn_rejection
                        change_generation = s.change_generation
                    if not queued:
                        break
                    client_generation = self._client_generation_now()
                    if rejection == (change_generation, client_generation):
                        # A background wake/push is not evidence that the rejected request changed.
                        # Park without a timer; the four explicit session changes above or a newly
                        # installed client generation set kick and make this tuple differ.
                        break
                    if rejection is not None:
                        with s.lock:
                            if s.turn_rejection == rejection:
                                s.turn_rejection = None
                    try:
                        progressed = self._run_turn(s)
                    except _PermanentTurnStartRejection as e:
                        self.log("turn rejected (%s): %s" % (s.name, e))
                        with s.lock:
                            s.launch_error = {"text": "codex turn rejected: %s" % e,
                                              "at": time.time(), "limit": False}
                            s.state = "waiting"
                            s.turn_id = None
                            # Record the generations of the REJECTED request, not whatever is
                            # current after its RPC returned. A send/model change racing the RPC
                            # must remain a fresh kick and immediately retry the new request.
                            s.turn_rejection = (e.change_generation, e.client_generation)
                        try:
                            self._save_registry()      # durable queue + visible rejection; no timed retry
                        except Exception:
                            self.log("turn rejection registry save: %s" % traceback.format_exc())
                        self.push_session(s.sid)
                        break
                    except Exception as e:
                        self.log("turn failed (%s): %s" % (s.name, traceback.format_exc()))
                        with s.lock:
                            s.launch_error = {"text": "codex turn failed: %s" % e,
                                              "at": time.time(), "limit": False}
                            s.state = "waiting"
                            s.turn_id = None
                        try:
                            self._save_registry()      # queue + visible failure survive another restart
                        except Exception:
                            self.log("turn failure registry save: %s" % traceback.format_exc())
                        self.push_session(s.sid)
                        progressed = False
                    if progressed:
                        retry_delay = CLIENT_RETRY_MIN
                        continue
                    delay = max(retry_delay, self._client_retry_remaining())
                    retry_delay = min(CLIENT_RETRY_MAX, retry_delay * 2)
                    # Clear stale send kicks before the backoff. A concurrent kill sets dead and/or
                    # wakes this wait, so shutdown stays prompt while repeated sends cannot spin it.
                    s.kick.clear()
                    with s.lock:
                        if s.dead:
                            return
                    s.kick.wait(delay)
                    s.kick.clear()
        finally:
            with s.lock:
                if s.worker is threading.current_thread():
                    s.worker = None

    def _run_turn(self, s):
        c = self._get_client()
        if c is None:
            with s.lock:
                s.launch_error = {"text": self._client_err or SETUP_HINT, "at": time.time(),
                                  "limit": False}
            self.push_session(s.sid)
            return False                   # queue stays parked; worker retries after the deadline
        with s.lock:
            loaded = s.loaded
        if not loaded and not self._prepare_thread(s, c):
            return True                    # killed while the resume/create RPC was in flight
        norm = self._ensure_norm(s)
        with s.lock:
            if s.dead:
                return True
            batch = list(s.queue)           # retain the durable prefix until turn/start ACKs
            if not batch:
                return True
            params = {"approvalPolicy": APPROVAL_POLICY, "cwd": s.cwd,
                      **_execution_permissions(s.cwd)}
            if s.model:
                params["model"] = s.model
            if s.effort:
                params["effort"] = s.effort
            tid = s.tid
            change_generation = s.change_generation
        # Do not hold the lifecycle lock across an app-server RPC: kill must stay prompt even when
        # turn/start itself stalls. Sends may append meanwhile; the snapshotted prefix stays in place.
        client_generation = self._client_generation_for(c)
        try:
            started = c.turn_start(tid, [{"type": "text", "text": t} for t in batch], params)
        except Exception as e:
            if _is_permanent_turn_rejection(e):
                raise _PermanentTurnStartRejection(e, change_generation, client_generation) from e
            raise
        with s.lock:
            turn_id = started.turn.id
            if s.queue[:len(batch)] != batch:
                raise RuntimeError("Codex send queue prefix changed during turn/start")
            del s.queue[:len(batch)]
            s.turn_id = turn_id
            s.state = "working"
            s.since = time.time()
            s.launch_error = None
            s.turn_rejection = None
            killed_during_start = s.dead
        try:
            self._save_registry()           # ACK first, then commit dequeue (at-least-once on crash)
            self.push_session(s.sid)
            if killed_during_start:
                try:
                    c.turn_interrupt(tid, turn_id)
                except Exception as e:
                    self.log("kill interrupt %s: %s" % (s.name, e))
            while True:
                n = c.next_turn_notification(turn_id)
                method = getattr(n, "method", "")
                with s.norm_lock:
                    recs = norm.handle(method, _dump(getattr(n, "payload", None)))
                    if recs:
                        self._append(s, recs)
                if method == "turn/completed":
                    break
        finally:
            try:
                c.unregister_turn_notifications(turn_id)
            except Exception:
                pass
            with s.lock:
                s.turn_id = None
                s.state = "waiting"
                s.since = time.time()
            self.push_session(s.sid)
        return True

    # ── chat tail ────────────────────────────────────────────────────────────────────────────────
    def pending_queued(self, sid):
        s = self._session(sid)
        if not s:
            return []
        with s.lock:
            return list(s.queue)

    def live_atoms(self, sid):
        s = self._session(sid)
        if not s:
            return []
        with s.lock:
            return [{"type": "user", "uuid": e["uuid"], "session_id": sid, "fsid": s.tid,
                     "t": e["t"], "parentUuid": None, "author": "human",
                     "message": {"role": "user",
                                 "content": [{"type": "text", "text": e["text"]}]}}
                    for e in s.echoes]

    def prune_live(self, sid, tx_uuids, tx_user_texts=()):
        s = self._session(sid)
        if not s:
            return
        texts = {t.strip() for t in tx_user_texts or ()}
        with s.lock:
            s.echoes = [e for e in s.echoes
                        if e["uuid"] not in (tx_uuids or ()) and e["text"] not in texts]

    # ── ask picker (no Codex equivalent in phase 1) ─────────────────────────────────────────────
    def on_ask(self, sid, kind, payload=None):
        return False

    def current_ask(self, sid):
        return None

    # ── the loud degradations ────────────────────────────────────────────────────────────────────
    def launch_error(self, sid):
        s = self._session(sid)
        if not s:
            return None
        with s.lock:
            return s.launch_error

    def forwards_sends(self):
        return True    # sends steer mid-turn or queue; the kernel hands them over immediately

    def set_auth(self, sid, value):
        return False   # Codex auth is machine-global (codex login); no per-session pick

    def stop_task(self, sid, task_id):
        return False

    def mcp_status(self, sid):
        return [], ("Codex sessions load MCP servers from ~/.codex/config.toml; "
                    "per-session MCP controls aren't available here yet")

    def mcp_action(self, sid, name, action, enabled=True):
        return "Codex sessions load MCP servers from ~/.codex/config.toml; edit that file instead"

    def rewind_files(self, sid, uuid):
        return False

    # ── coordination ─────────────────────────────────────────────────────────────────────────────
    def working_note(self, sid):
        s = self._session(sid)
        if not s:
            return ""
        with s.lock:
            return s.note

    def set_working_note(self, sid, text):
        s = self._session(sid)
        if s:
            with s.lock:
                s.note = text or ""
            self._save_registry()

    def wake(self, sid):
        s = self._session(sid)
        if not s:
            return False
        with s.lock:
            if s.dead or not s.queue:
                return False
        self._ensure_worker(s)
        s.kick.set()
        return True

    def deliver(self, sid, text):
        """Postal deliver-time wake: a busy session gets the banner steered into the running turn;
        an idle one gets a turn started with it — either way the mail is in front of the agent NOW."""
        return self.send(sid, text)
