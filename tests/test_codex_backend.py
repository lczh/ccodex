#!/usr/bin/env python3
"""CodexBackend contract tests — a scripted FAKE client (no SDK, no network, no login) drives the
backend through spawn/send/steer/interrupt/kill/resume and pins: the worker's turn loop writes the
materialized transcript, state transitions are event-based, the uuid chain survives a backend
restart (the _tail_state re-anchor), Claude-only knobs refuse loudly, and a missing `codex login`
surfaces as launch_error text instead of a silent non-start. All data synthetic per CLAUDE.md.

Run:    python3 tests/test_codex_backend.py
"""
import json
import os
import queue
import sys
import tempfile
import threading
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
cb = SourceFileLoader("romp_codex_backend", os.path.join(ROOT, "kernel", "codex_backend.py")).load_module()
sb = SourceFileLoader("romp_session_backend", os.path.join(ROOT, "kernel", "session_backend.py")).load_module()


def until(fn, timeout=5.0, step=0.01):
    dl = time.time() + timeout
    while time.time() < dl:
        if fn():
            return True
        time.sleep(step)
    return False


class _Payload:
    def __init__(self, d):
        self._d = d

    def model_dump(self, by_alias=True, mode=None):
        return self._d


def note(method, params):
    return SimpleNamespace(method=method, payload=_Payload(params))


class FakeClient:
    """Scripted app-server: turn_start opens a queue and streams either the injected script or a
    default echo turn (userMessage + agentMessage + tokenUsage + completed)."""

    def __init__(self):
        self.calls = []
        self.turn_queues = {}
        self.scripts = []           # each turn_start pops one, [] → default echo turn
        self.hold_open = False      # script the turn to stay open (steer/interrupt tests)
        self._n = 0
        self._global = queue.Queue()

    # bookkeeping helpers ------------------------------------------------------------------
    def _rec(self, name, *a):
        self.calls.append((name,) + a)

    def called(self, name):
        return [c for c in self.calls if c[0] == name]

    # client surface the backend uses ------------------------------------------------------
    def account_read(self, *a, **k):
        self._rec("account_read")
        return SimpleNamespace(requires_openai_auth=False, account={"ok": True})

    def initialize(self):
        self._rec("initialize")

    def close(self):
        self._rec("close")

    def thread_start(self, params=None):
        self._rec("thread_start", params)
        return SimpleNamespace(thread=SimpleNamespace(id="T-%d" % len(self.called("thread_start"))),
                               model="gpt-5-test")

    def thread_resume(self, tid, params=None):
        self._rec("thread_resume", tid, params)
        return SimpleNamespace(thread=SimpleNamespace(id=tid))

    def thread_set_name(self, tid, name):
        self._rec("thread_set_name", tid, name)

    def model_list(self, *a, **k):
        self._rec("model_list")
        return SimpleNamespace(data=[
            SimpleNamespace(id="gpt-5-test", display_name="GPT-5 Test", hidden=False),
            SimpleNamespace(id="gpt-5-hidden", display_name="Hidden", hidden=True)])

    def turn_start(self, tid, input_items, params=None):
        self._n += 1
        turn_id = "t-%d" % self._n
        self._rec("turn_start", tid, input_items, params, turn_id)
        q = queue.Queue()
        self.turn_queues[turn_id] = q
        script = self.scripts.pop(0) if self.scripts else None
        ms = 1781100000000 + self._n * 100000
        text = " ".join(i.get("text", "") for i in input_items)
        if script is None:
            script = [
                ("item/completed", {"threadId": tid, "turnId": turn_id, "completedAtMs": ms,
                                    "item": {"type": "userMessage", "id": "u-%d" % self._n,
                                             "content": [{"type": "text", "text": text}]}}),
                ("item/completed", {"threadId": tid, "turnId": turn_id, "completedAtMs": ms + 1000,
                                    "item": {"type": "agentMessage", "id": "a-%d" % self._n,
                                             "text": "ack: " + text}}),
                ("thread/tokenUsage/updated",
                 {"threadId": tid, "turnId": turn_id,
                  "tokenUsage": {"last": {"inputTokens": 900, "outputTokens": 40,
                                          "cachedInputTokens": 500, "reasoningOutputTokens": 10,
                                          "totalTokens": 54400},
                                 "total": {"inputTokens": 9000, "outputTokens": 400,
                                           "cachedInputTokens": 5000,
                                           "reasoningOutputTokens": 100, "totalTokens": 500000},
                                 "modelContextWindow": 272000}}),
                ("turn/completed", {"threadId": tid,
                                    "turn": {"id": turn_id, "items": [], "status": "completed"}}),
            ]
        for m, p in script:
            q.put(note(m, p))
        if not self.hold_open and not any(m == "turn/completed" for m, _ in script):
            q.put(note("turn/completed", {"threadId": tid,
                                          "turn": {"id": turn_id, "items": [],
                                                   "status": "completed"}}))
        return SimpleNamespace(turn=SimpleNamespace(id=turn_id))

    def next_turn_notification(self, turn_id):
        return self.turn_queues[turn_id].get(timeout=10)

    def unregister_turn_notifications(self, turn_id):
        self._rec("unregister", turn_id)

    def turn_steer(self, tid, expected_turn_id, input_items):
        self._rec("turn_steer", tid, expected_turn_id, input_items)

    def turn_interrupt(self, tid, turn_id):
        self._rec("turn_interrupt", tid, turn_id)
        self.turn_queues[turn_id].put(note("turn/completed",
                                           {"threadId": tid,
                                            "turn": {"id": turn_id, "items": [],
                                                     "status": "interrupted"}}))

    def next_notification(self):
        return self._global.get()   # blocks forever — the global pump just parks in tests


def build(tmp=None, factory=None):
    tmp = tmp or tempfile.mkdtemp()
    fake = FakeClient()
    be = cb.CodexBackend(tmp, client_factory=(factory or (lambda: fake)))
    return be, fake, tmp


class Conformance(unittest.TestCase):
    def test_every_abstract_method_exists(self):
        missing = [m for m in sb.SessionBackend.__abstractmethods__
                   if not callable(getattr(cb.CodexBackend, m, None))]
        self.assertEqual(missing, [], "CodexBackend must duck-type the full ABC")

    def test_only_deterministic_rpc_rejections_park(self):
        def error_type(name, code, message):
            cls = type(name, (RuntimeError,), {})
            err = cls(message)
            err.code, err.message = code, message
            return err

        self.assertTrue(cb._is_permanent_turn_rejection(
            error_type("InvalidParamsError", -32602, "invalid model")))
        self.assertTrue(cb._is_permanent_turn_rejection(
            error_type("CodexRpcError", -32000, "model gpt-x is not supported for this account")))
        self.assertFalse(cb._is_permanent_turn_rejection(
            error_type("InternalRpcError", -32603, "internal error")))
        self.assertFalse(cb._is_permanent_turn_rejection(
            error_type("CodexRpcError", -32001, "temporary backend failure")))


class Lifecycle(unittest.TestCase):
    def test_spawn_send_turn_materializes_transcript(self):
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.owns(sid))
        self.assertEqual(be.live_sessions()[sid]["state"], "waiting")
        self.assertTrue(be.send(sid, "hello codex"))
        self.assertTrue(until(lambda: be.live_sessions()[sid]["state"] == "waiting"
                              and not be.busy(sid)))
        path = be.transcript_path(sid)
        recs = [json.loads(l) for l in Path(path).read_text().splitlines()]
        self.assertEqual([r["type"] for r in recs], ["user", "assistant"])
        self.assertEqual(recs[0]["promptSource"], "sdk")
        self.assertEqual(recs[1]["message"]["stop_reason"], "end_turn")
        self.assertEqual(recs[1]["parentUuid"], recs[0]["uuid"])
        # the optimistic echo was pruned when its record landed
        self.assertTrue(until(lambda: be.live_atoms(sid) == []))
        # context % from tokenUsage: 54400/272000 = 20
        self.assertEqual(be.live_sessions()[sid]["context"], 20)
        # Both thread creation and each turn carry the pinned runtime's named workspace profile.
        # The stale legacy workspaceWrite shape has unrestricted reads and must never reappear.
        thread_params = fake.called("thread_start")[0][1]
        self.assertEqual(thread_params["permissions"], "ccodex_workspace")
        self.assertEqual(thread_params["runtimeWorkspaceRoots"], ["/TESTDIR"])
        self.assertNotIn("sandbox", thread_params)
        _, tid, items, params, _ = fake.called("turn_start")[0]
        self.assertEqual(params["approvalPolicy"], "never")
        self.assertEqual(params["permissions"], "ccodex_workspace")
        self.assertEqual(params["runtimeWorkspaceRoots"], ["/TESTDIR"])
        self.assertNotIn("sandboxPolicy", params)

    def test_send_during_open_turn_steers(self):
        be, fake, _ = build()
        fake.hold_open = True
        fake.scripts = [[("item/completed",
                          {"threadId": "T-1", "turnId": "t-1", "completedAtMs": 1781100000000,
                           "item": {"type": "userMessage", "id": "u-1",
                                    "content": [{"type": "text", "text": "long job"}]}})]]
        sid = be.spawn("web", "/TESTDIR")
        be.send(sid, "long job")
        # wait for the TURN to open (busy() is already true while merely queued — a steer needs
        # the active turn id)
        self.assertTrue(until(lambda: be.live_sessions()[sid]["state"] == "working"))
        self.assertTrue(be.send(sid, "also check the docs"))
        self.assertEqual(len(fake.called("turn_steer")), 1)
        _, tid, expected_turn_id, input_items = fake.called("turn_steer")[0]
        self.assertEqual(expected_turn_id, "t-1")
        self.assertEqual(input_items, [{"type": "text", "text": "also check the docs"}])
        fake.turn_queues["t-1"].put(note("turn/completed",
                                         {"threadId": tid,
                                          "turn": {"id": "t-1", "items": [],
                                                   "status": "completed"}}))
        self.assertTrue(until(lambda: not be.busy(sid)))

    def test_interrupt_targets_active_turn_and_settles(self):
        be, fake, _ = build()
        fake.hold_open = True
        fake.scripts = [[("item/completed",
                          {"threadId": "T-1", "turnId": "t-1", "completedAtMs": 1781100000000,
                           "item": {"type": "userMessage", "id": "u-1",
                                    "content": [{"type": "text", "text": "run forever"}]}})]]
        sid = be.spawn("web", "/TESTDIR")
        be.send(sid, "run forever")
        self.assertTrue(until(lambda: be.live_sessions()[sid]["state"] == "working"))
        self.assertTrue(be.interrupt(sid))
        self.assertEqual(fake.called("turn_interrupt")[0][2], "t-1")
        self.assertTrue(until(lambda: not be.busy(sid)))
        recs = [json.loads(l) for l in Path(be.transcript_path(sid)).read_text().splitlines()]
        self.assertTrue(any("[Request interrupted" in json.dumps(r) for r in recs))

    def test_kill_resume_roundtrip(self):
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        be.send(sid, "one")
        self.assertTrue(until(lambda: not be.busy(sid)))
        self.assertTrue(be.kill(sid))
        self.assertFalse(be.owns(sid))
        self.assertNotIn(sid, be.live_sessions())
        self.assertTrue(be.resume("web", sid))
        self.assertTrue(be.owns(sid))
        be.send(sid, "two")
        self.assertTrue(until(lambda: len(fake.called("thread_resume")) == 1))
        resume_params = fake.called("thread_resume")[0][2]
        self.assertEqual(resume_params["permissions"], "ccodex_workspace")
        self.assertEqual(resume_params["runtimeWorkspaceRoots"], ["/TESTDIR"])
        self.assertNotIn("sandbox", resume_params)
        self.assertTrue(until(lambda: not be.busy(sid) and not be.pending_queued(sid)))

    def test_turn_start_failure_keeps_the_unacknowledged_batch_for_retry(self):
        class FailOnceClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.attempts = []
                self.failed = threading.Event()
                self.retry_entered = threading.Event()
                self.allow_retry = threading.Event()

            def turn_start(self, tid, input_items, params=None):
                self.attempts.append([i["text"] for i in input_items])
                if len(self.attempts) == 1:
                    self.failed.set()
                    raise RuntimeError("synthetic pre-ack failure")
                self.retry_entered.set()
                self.allow_retry.wait(5)
                return super().turn_start(tid, input_items, params)

        fake = FailOnceClient()
        be, _, tmp = build(factory=lambda: fake)
        sid = be.spawn("web", "/TESTDIR")
        # Build one deterministic two-message batch before allowing the worker to start.
        ensure = be._ensure_worker
        be._ensure_worker = lambda s: None
        self.assertTrue(be.send(sid, "first"))
        self.assertTrue(be.send(sid, "second"))
        be._ensure_worker = ensure
        self.assertTrue(be.wake(sid))
        self.assertTrue(fake.failed.wait(2))
        self.assertEqual(be.pending_queued(sid), ["first", "second"])
        rows = json.loads((Path(tmp) / "codex" / "registry.json").read_text())
        self.assertEqual(rows[sid]["queue"], ["first", "second"])
        self.assertTrue(fake.retry_entered.wait(2))
        fake.allow_retry.set()
        self.assertTrue(until(lambda: not be.busy(sid) and not be.pending_queued(sid)))
        self.assertEqual(fake.attempts, [["first", "second"], ["first", "second"]])

    def test_permanent_turn_rejection_parks_without_spin_until_explicit_change(self):
        class InvalidParamsError(RuntimeError):
            def __init__(self, message):
                super().__init__(message)
                self.code = -32602

        class RejectingClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.attempts = []

            def turn_start(self, tid, input_items, params=None):
                self.attempts.append((list(input_items), dict(params or {})))
                raise InvalidParamsError("model is not available")

        fake = RejectingClient()
        be, _, _ = build(factory=lambda: fake)
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.send(sid, "keep this durable"))
        self.assertTrue(until(lambda: len(fake.attempts) == 1))
        time.sleep(0.65)  # exceeds the old 0.25s retry; a permanent rejection has no timer
        self.assertEqual(len(fake.attempts), 1)
        self.assertEqual(be.pending_queued(sid), ["keep this durable"])
        self.assertIn("model is not available", be.launch_error(sid)["text"])
        self.assertTrue(be.wake(sid))
        time.sleep(0.35)
        self.assertEqual(len(fake.attempts), 1, "an ordinary wake must not repeat a rejected RPC")
        self.assertTrue(be.set_model(sid, "gpt-5-fixed"))
        self.assertTrue(until(lambda: len(fake.attempts) == 2))
        self.assertEqual(fake.attempts[1][1]["model"], "gpt-5-fixed")
        time.sleep(0.65)
        self.assertEqual(len(fake.attempts), 2, "the replacement request parks if it is rejected too")
        self.assertTrue(be.kill(sid))

    def test_new_client_generation_retries_a_parked_permanent_rejection(self):
        class InvalidRequestError(RuntimeError):
            code = -32600

        class RejectOnceClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.rejected = threading.Event()

            def turn_start(self, tid, input_items, params=None):
                self.rejected.set()
                raise InvalidRequestError("old server rejected request")

        old, replacement = RejectOnceClient(), FakeClient()
        clients = [old, replacement]
        be, _, _ = build(factory=lambda: clients.pop(0))
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.send(sid, "retry on replacement"))
        self.assertTrue(old.rejected.wait(2))
        with be._client_lock:
            be._record_client_failure_locked(RuntimeError("replace generation"), old)
            be._client_retry_at = 0.0
        self.assertTrue(be.available())
        self.assertTrue(until(lambda: not be.busy(sid) and not be.pending_queued(sid)))
        self.assertEqual(len(replacement.called("turn_start")), 1)

    def test_client_factory_failure_backs_off_then_recovers_queued_session(self):
        fake = FakeClient()
        attempts = []

        def flaky_factory():
            attempts.append(time.monotonic())
            if len(attempts) == 1:
                raise RuntimeError("synthetic unavailable client")
            return fake

        be, _, _ = build(factory=flaky_factory)
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.send(sid, "retry me"))
        time.sleep(0.03)
        self.assertEqual(len(attempts), 1, "unavailable client must not hot-spin")
        self.assertTrue(until(lambda: len(attempts) == 2 and not be.busy(sid), timeout=3))
        self.assertEqual(len(fake.called("thread_start")), 1,
                         "the pending placeholder must become a real Codex thread")
        self.assertFalse(be.pending_queued(sid))

    def test_kill_interrupts_an_active_turn_and_worker_exits(self):
        be, fake, _ = build()
        fake.hold_open = True
        fake.scripts = [[("item/completed",
                          {"threadId": "T-1", "turnId": "t-1", "completedAtMs": 1781100000000,
                           "item": {"type": "userMessage", "id": "u-1",
                                    "content": [{"type": "text", "text": "keep running"}]}})]]
        sid = be.spawn("web", "/TESTDIR")
        be.send(sid, "keep running")
        self.assertTrue(until(lambda: be.live_sessions()[sid]["state"] == "working"))
        worker = be._sessions[sid].worker
        self.assertTrue(be.kill(sid))
        self.assertEqual(fake.called("turn_interrupt")[0][2], "t-1")
        self.assertTrue(until(lambda: not worker.is_alive()))
        self.assertFalse(be.owns(sid))

    def test_kill_can_mark_dead_while_turn_start_is_in_flight_then_interrupts_the_ack(self):
        class BlockingStartClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.start_entered = threading.Event()
                self.release_start = threading.Event()

            def turn_start(self, tid, input_items, params=None):
                self.start_entered.set()
                self.release_start.wait(2)
                return super().turn_start(tid, input_items, params)

        fake = BlockingStartClient()
        be, _, _ = build(factory=lambda: fake)
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.send(sid, "start slowly"))
        self.assertTrue(fake.start_entered.wait(1))
        result = []
        killer = threading.Thread(target=lambda: result.append(be.kill(sid)))
        killer.start()
        self.assertTrue(until(lambda: not be.owns(sid), timeout=0.5),
                        "turn/start must not hold the session lock needed by kill")
        fake.release_start.set()
        killer.join(2)
        self.assertFalse(killer.is_alive())
        self.assertEqual(result, [True])
        self.assertEqual(fake.called("turn_interrupt")[0][2], "t-1",
                         "an ACK that races kill must be interrupted as soon as its id exists")

    def test_concurrent_worker_ensure_starts_exactly_one_thread(self):
        be, _, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        s = be._sessions[sid]
        started = []
        release = threading.Event()

        def parked_worker(session):
            started.append(threading.get_ident())
            release.wait(5)

        be._work = parked_worker
        callers = [threading.Thread(target=be._ensure_worker, args=(s,)) for _ in range(20)]
        for t in callers:
            t.start()
        for t in callers:
            t.join(2)
        self.assertEqual(len(started), 1)
        release.set()
        self.assertTrue(until(lambda: not s.worker.is_alive()))

    def test_registry_and_chain_survive_backend_restart(self):
        be, fake, tmp = build()
        sid = be.spawn("web", "/TESTDIR")
        be.send(sid, "first")
        self.assertTrue(until(lambda: not be.busy(sid)))
        # a NEW backend over the same state dir (kernel restart): same session, resumed lazily,
        # and the file's uuid chain continues off the pre-restart tail (_tail_state re-anchor)
        be2 = cb.CodexBackend(tmp, client_factory=lambda: fake)
        self.assertTrue(be2.owns(sid))
        be2.send(sid, "second")
        self.assertTrue(until(lambda: not be2.busy(sid) and not be2.pending_queued(sid)))
        recs = [json.loads(l) for l in Path(be2.transcript_path(sid)).read_text().splitlines()]
        for prev, r in zip(recs, recs[1:]):
            self.assertEqual(r["parentUuid"], prev["uuid"],
                             "chain broke across the restart at %s" % r["uuid"])
        self.assertEqual(len({r["uuid"] for r in recs}), len(recs))

    def test_backend_restart_rearms_a_persisted_queue_without_another_send(self):
        be, fake, tmp = build()
        sid = be.spawn("web", "/TESTDIR")
        ensure = be._ensure_worker
        be._ensure_worker = lambda s: None
        self.assertTrue(be.send(sid, "survive restart"))
        be._ensure_worker = ensure
        self.assertEqual(be.pending_queued(sid), ["survive restart"])
        be2 = cb.CodexBackend(tmp, client_factory=lambda: fake)
        self.assertTrue(until(lambda: not be2.busy(sid) and not be2.pending_queued(sid)))
        self.assertIn("survive restart", Path(be2.transcript_path(sid)).read_text())

    def test_launch_error_survives_backend_restart(self):
        def bad_factory():
            raise RuntimeError(cb.LOGIN_HINT)
        be, _, tmp = build(factory=bad_factory)
        sid = be.spawn("web", "/TESTDIR")
        be2 = cb.CodexBackend(tmp, client_factory=bad_factory)
        self.assertIn("codex login", be2.launch_error(sid)["text"])

    def test_missing_login_is_loud_not_silent(self):
        def bad_factory():
            raise RuntimeError(cb.LOGIN_HINT)
        be, _, _ = build(factory=bad_factory)
        sid = be.spawn("web", "/TESTDIR")
        self.assertIsNotNone(sid)                       # the session EXISTS, visibly broken
        err = be.launch_error(sid)
        self.assertIn("codex login", err["text"])
        self.assertFalse(err["limit"])
        self.assertFalse(be.available())

    def test_claude_only_knobs_refuse(self):
        be, _, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.set_effort(sid, "xhigh"))    # Codex takes xhigh natively
        self.assertFalse(be.set_effort(sid, "max"))     # Claude-only → loud refusal
        self.assertFalse(be.set_fast(sid, "on"))
        self.assertFalse(be.set_mode(sid, "plan"))
        self.assertFalse(be.set_auth(sid, "key"))
        self.assertFalse(be.rewind_files(sid, "u1"))
        self.assertTrue(be.set_model(sid, "gpt-5-codex"))
        self.assertEqual(be.live_sessions()[sid]["model"], "gpt-5-codex")
        # a Claude alias would 400 the next turn — refused here so the kernel warns instead
        self.assertFalse(be.set_model(sid, "sonnet"))
        self.assertEqual(be.live_sessions()[sid]["model"], "gpt-5-codex")

    def test_client_launch_defines_the_fail_closed_workspace_profile(self):
        captured = []

        class CaptureConfig:
            def __init__(self, **kwargs):
                captured.append(kwargs)

        cb._codex_config(CaptureConfig, "/opt/codex")
        self.assertEqual(captured[0]["codex_bin"], "/opt/codex")
        self.assertEqual(captured[0]["client_name"], "romp")
        overrides = captured[0]["config_overrides"]
        self.assertEqual(overrides, cb.CODEX_CONFIG_OVERRIDES)
        profile = overrides[0]
        self.assertIn('":minimal" = "read"', profile)
        self.assertIn('"." = "write"', profile)
        for metadata in (".git", ".agents", ".codex"):
            self.assertNotIn('"%s"' % metadata, profile)
        self.assertIn("network = { enabled = true }", profile)
        self.assertEqual(overrides[1], 'default_permissions="ccodex_workspace"')

    def test_model_catalog_from_app_server(self):
        be, fake, _ = build()
        cat = be.model_catalog()
        self.assertEqual(cat, [{"value": "gpt-5-test", "label": "GPT-5 Test"}])
        be.model_catalog()
        self.assertEqual(len(fake.called("model_list")), 1, "catalog is fetched once, then cached")

    def test_deliver_and_wake_reach_the_agent(self):
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.deliver(sid, "you have mail from web"))
        self.assertTrue(until(lambda: not be.busy(sid) and not be.pending_queued(sid)))
        texts = Path(be.transcript_path(sid)).read_text().splitlines()
        self.assertTrue(any("you have mail" in t for t in texts))


if __name__ == "__main__":
    unittest.main(verbosity=2)
