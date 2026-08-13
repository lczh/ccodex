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
- The worker is the file's single writer: notification → normalizer → append → poke the kernel.
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
SETUP_HINT = ("Session not created: romp's Codex backend isn't installed. "
              "Run bin/romp-codex-setup, then try again.")
LOGIN_HINT = "Codex isn't logged in on this machine — run: codex login"

# The phase-1 posture: sandboxed full-auto (plans/codex-backend.md). Approvals never fire; the
# sandbox is the guardrail. Network stays on — romp sessions routinely need git and the web.
TURN_SANDBOX = {"type": "workspaceWrite", "networkAccess": True}
APPROVAL_POLICY = "never"
# romp effort names → Codex ReasoningEffort. Identity for the shared four; max/ultracode are
# Claude-only knobs and set_effort refuses them (False → the kernel warns instead of pretending).
EFFORTS = ("low", "medium", "high", "xhigh")

SEED_TAIL = 200   # records whose uuids seed the normalizer's dedup on re-attach (replay guard)


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
        self.note = ""                # postal working-note
        self.lock = threading.Lock()


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
        self._client_lock = threading.Lock()
        self._sessions = {}           # sid → _Session
        self._reg_lock = threading.Lock()
        self._load_registry()

    # ── registry persistence ─────────────────────────────────────────────────────────────────────
    def _reg_path(self):
        return self.root / "registry.json"

    def _load_registry(self):
        try:
            rows = json.loads(self._reg_path().read_text())
        except Exception:
            rows = {}
        for sid, r in rows.items():
            s = _Session(sid, r["tid"], r.get("name", ""), r.get("cwd", ""),
                         r.get("model", ""), r.get("effort", ""), r.get("color", ""))
            s.dead = bool(r.get("dead"))
            s.queue = list(r.get("queue") or [])
            s.note = r.get("note", "")
            self._sessions[sid] = s

    def _save_registry(self):
        # serialized: a handler thread (send) and a worker (batch pop) save concurrently, and two
        # unserialized writers shared one tmp path — the loser's os.replace found it already gone
        with self._reg_lock:
            rows = {sid: {"tid": s.tid, "name": s.name, "cwd": s.cwd, "model": s.model,
                          "effort": s.effort, "dead": s.dead, "queue": s.queue, "note": s.note,
                          "color": s.color}
                    for sid, s in self._sessions.items()}
            tmp = self._reg_path().with_suffix(".tmp")
            tmp.write_text(json.dumps(rows, indent=1))
            os.replace(tmp, self._reg_path())

    # ── client lifecycle ─────────────────────────────────────────────────────────────────────────
    def available(self):
        """Can this backend actually RUN a session right now? (The creation gate — mirrors
        _sdk_ready's contract.) Building the client is the real probe; a failure is remembered
        and surfaced, never retried in a hot loop."""
        return self._get_client() is not None

    def _get_client(self):
        with self._client_lock:
            if self._client is not None:
                return self._client
            if self._client_err is not None:
                return None           # a remembered failure — launch_error carries it per session
            try:
                if self._client_factory:
                    self._client = self._client_factory()
                else:
                    if not ensure_codex_sdk(self.state):
                        raise RuntimeError(SETUP_HINT)
                    from openai_codex.client import CodexClient, CodexConfig
                    cfg = CodexConfig(codex_bin=self.codex_bin, client_name="romp")
                    self._client = CodexClient(config=cfg)
                    self._client.start()
                    self._client.initialize()
                self._check_auth()
                threading.Thread(target=self._global_pump, daemon=True,
                                 name="codex-pump").start()
                return self._client
            except Exception as e:
                self._client_err = str(e) or e.__class__.__name__
                self._client = None
                self.log("client unavailable: %s" % self._client_err)
                return None

    def _check_auth(self):
        """A missing `codex login` must surface as text on the session, not as a hung turn."""
        try:
            acct = self._client.account_read()
        except Exception as e:
            self.log("account_read failed: %s" % e)
            return
        needs = getattr(acct, "requires_openai_auth", None)
        if needs and not getattr(acct, "account", None):
            self._client_err = LOGIN_HINT
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
            raise RuntimeError(LOGIN_HINT)

    def _global_pump(self):
        """Drain notifications NOT routed to a registered turn (thread status, rate limits, token
        usage between turns). Feeds the owning session's normalizer so nothing is dropped."""
        while True:
            c = self._client
            if c is None:
                return
            try:
                n = c.next_notification()
            except Exception as e:
                self.log("global pump stopped: %s" % e)
                return
            try:
                p = _dump(getattr(n, "payload", None))
                tid = p.get("threadId")
                s = next((s for s in self._sessions.values() if s.tid == tid), None)
                if s and s.norm:
                    recs = s.norm.handle(getattr(n, "method", ""), p)
                    if recs:
                        self._append(s, recs)
            except Exception:
                self.log("global pump: %s" % traceback.format_exc())

    # ── the materialized transcript ──────────────────────────────────────────────────────────────
    def transcript_path(self, sid):
        s = self._sessions.get(sid)
        if not s:
            return None
        d = self.projects / _enc_cwd(s.cwd)
        d.mkdir(parents=True, exist_ok=True)
        return d / ("%s.jsonl" % s.tid)

    def _ensure_norm(self, s):
        if s.norm is None:
            path = self.transcript_path(s.sid)
            last, seen = _tail_state(path)
            s.norm = _events.ThreadNormalizer(s.tid, cwd=s.cwd, model=s.model,
                                              version="codex", last_uuid=last, seen_uuids=seen)
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
        s = self._sessions.get(sid)
        return bool(s and not s.dead)

    def live_sessions(self):
        out = {}
        for sid, s in self._sessions.items():
            if s.dead:
                continue
            ctx = None
            if s.norm and s.norm.context and s.norm.context[1]:
                used, window = s.norm.context
                ctx = max(0, min(100, round(100 * used / window)))
            out[sid] = {"state": s.state, "model": s.model, "effort": s.effort,
                        "mode": "sandboxed", "since": s.since, "context": ctx,
                        "compactPct": None, "backend": "codex", "name": s.name, "cwd": s.cwd,
                        "color": s.color or None}
        return out

    def busy(self, sid):
        s = self._sessions.get(sid)
        if not s or s.dead:
            return None
        return bool(s.turn_id or s.queue)

    # ── control ──────────────────────────────────────────────────────────────────────────────────
    def send(self, sid, text):
        s = self._sessions.get(sid)
        if not s or s.dead or not text:
            return False
        c = self._get_client()
        with s.lock:
            s.echoes.append({"text": text.strip(), "t": time.time(),
                             "uuid": "echo-%s" % uuidlib.uuid4().hex[:8]})
        if c is not None and s.turn_id:
            # mid-turn: steer, with the active turn as precondition; a race with the turn's end
            # falls through to the queue (the worker delivers it in the next turn)
            try:
                c.turn_steer(s.tid, {"expectedTurnId": s.turn_id,
                                     "input": [{"type": "text", "text": text}]})
                return True
            except Exception:
                pass
        with s.lock:
            s.queue.append(text)
        self._save_registry()
        self._ensure_worker(s)
        s.kick.set()
        return True

    def interrupt(self, sid):
        s = self._sessions.get(sid)
        c = self._client
        if not s or s.dead or not s.turn_id or c is None:
            return False
        try:
            c.turn_interrupt(s.tid, s.turn_id)
            return True
        except Exception as e:
            self.log("interrupt %s: %s" % (s.name, e))
            return False

    def set_model(self, sid, value):
        s = self._sessions.get(sid)
        if not s or not value:
            return False
        s.model = value                    # applied on the next turn_start; Codex persists it
        if s.norm:
            s.norm.model = value
        self._save_registry()
        return True

    def set_mode(self, sid, mode):
        return False   # phase 1 pins approval never + workspace-write sandbox (plan doc); no live knob

    def set_effort(self, sid, value):
        s = self._sessions.get(sid)
        if not s or value not in EFFORTS:
            return False                   # max/ultracode are Claude-only — refuse loudly
        s.effort = value
        self._save_registry()
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
            self._sessions[sid] = s
            self._save_registry()
            return sid
        try:
            resp = c.thread_start({"cwd": cwd, "approvalPolicy": APPROVAL_POLICY,
                                   "sandbox": "workspace-write"})
            tid = resp.thread.id
            model = getattr(resp, "model", "") or ""
        except Exception as e:
            s = _Session(sid, "failed-%s" % sid[:8], name, cwd)
            s.launch_error = {"text": "codex thread/start failed: %s" % e, "at": time.time(),
                              "limit": False}
            self._sessions[sid] = s
            self._save_registry()
            return sid
        s = _Session(sid, tid, name, cwd, model=model, color=bg)
        s.loaded = True
        self._sessions[sid] = s
        self._ensure_norm(s)
        # touch the materialized transcript NOW: discovery lists real files, and an empty jsonl
        # parses to an empty session — the tab opens immediately instead of waiting for turn one
        self.transcript_path(sid).touch()
        self._write_name(s, bg, fg)
        self._save_registry()
        self.push()
        return sid

    def resume(self, name, sid, cwd=None):
        s = self._sessions.get(sid)
        if not s:
            return False
        s.dead = False
        s.loaded = False                   # the worker thread/resumes before the next turn
        s.name = name or s.name
        if cwd:
            s.cwd = cwd
        s.since = time.time()
        self._save_registry()
        if s.queue:
            self._ensure_worker(s)
            s.kick.set()
        return True

    def kill(self, sid):
        s = self._sessions.get(sid)
        if not s:
            return False
        held = s.norm.drain() if s.norm else []
        if held:
            self._append(s, held)          # never eat a held final message
        s.dead = True
        s.kick.set()                       # the worker sees dead and exits
        self._save_registry()
        return True

    def rename(self, sid, new_name):
        s = self._sessions.get(sid)
        if not s:
            return False
        s.name = new_name
        self._write_name(s)               # keep the shared identity file in sync (colours preserved)
        self._save_registry()
        c = self._client
        if c is not None:
            try:
                c.thread_set_name(s.tid, new_name)
            except Exception:
                pass                       # cosmetic on the Codex side; romp's registry is the truth
        return True

    # ── the per-session worker: queue → turns → records ────────────────────────────────────────
    def _ensure_worker(self, s):
        if s.worker and s.worker.is_alive():
            return
        s.worker = threading.Thread(target=self._work, args=(s,), daemon=True,
                                    name="codex-%s" % s.name)
        s.worker.start()

    def _work(self, s):
        while True:
            s.kick.wait()
            s.kick.clear()
            if s.dead:
                return
            while s.queue and not s.dead:
                try:
                    self._run_turn(s)
                except Exception:
                    self.log("turn failed (%s): %s" % (s.name, traceback.format_exc()))
                    s.launch_error = {"text": "codex turn failed: %s" % sys.exc_info()[1],
                                      "at": time.time(), "limit": False}
                    s.state = "waiting"
                    s.turn_id = None
                    self.push_session(s.sid)
                    break

    def _run_turn(self, s):
        c = self._get_client()
        if c is None:
            s.launch_error = {"text": self._client_err or SETUP_HINT, "at": time.time(),
                              "limit": False}
            self.push_session(s.sid)
            with s.lock:
                pass                       # queue stays parked, not lost — retried on next kick
            return
        if not s.loaded:
            c.thread_resume(s.tid, {"cwd": s.cwd})
            s.loaded = True
        with s.lock:
            batch, s.queue = s.queue, []
        self._save_registry()
        if not batch:
            return
        norm = self._ensure_norm(s)
        params = {"approvalPolicy": APPROVAL_POLICY, "sandboxPolicy": TURN_SANDBOX, "cwd": s.cwd}
        if s.model:
            params["model"] = s.model
        if s.effort:
            params["effort"] = s.effort
        started = c.turn_start(s.tid, [{"type": "text", "text": t} for t in batch], params)
        turn_id = started.turn.id
        s.turn_id = turn_id
        s.state = "working"
        s.since = time.time()
        s.launch_error = None
        self.push_session(s.sid)
        try:
            while True:
                n = c.next_turn_notification(turn_id)
                method = getattr(n, "method", "")
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
            s.turn_id = None
            s.state = "waiting"
            s.since = time.time()
            self.push_session(s.sid)

    # ── chat tail ────────────────────────────────────────────────────────────────────────────────
    def pending_queued(self, sid):
        s = self._sessions.get(sid)
        return list(s.queue) if s else []

    def live_atoms(self, sid):
        s = self._sessions.get(sid)
        if not s:
            return []
        with s.lock:
            return [{"type": "user", "uuid": e["uuid"], "session_id": sid, "fsid": s.tid,
                     "t": e["t"], "parentUuid": None, "author": "human",
                     "message": {"role": "user",
                                 "content": [{"type": "text", "text": e["text"]}]}}
                    for e in s.echoes]

    def prune_live(self, sid, tx_uuids, tx_user_texts=()):
        s = self._sessions.get(sid)
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
        s = self._sessions.get(sid)
        return s.launch_error if s else None

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
        s = self._sessions.get(sid)
        return s.note if s else ""

    def set_working_note(self, sid, text):
        s = self._sessions.get(sid)
        if s:
            s.note = text or ""
            self._save_registry()

    def wake(self, sid):
        s = self._sessions.get(sid)
        if not s or s.dead or not s.queue:
            return False
        self._ensure_worker(s)
        s.kick.set()
        return True

    def deliver(self, sid, text):
        """Postal deliver-time wake: a busy session gets the banner steered into the running turn;
        an idle one gets a turn started with it — either way the mail is in front of the agent NOW."""
        return self.send(sid, text)
