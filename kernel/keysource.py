"""Live API-key configuration, separate from credential retrieval.

The design, in four rules:

* A SOURCE is configuration, not a secret: a static key line, or a 1Password reference
  (``ROMP_API_KEY_REF=op://vault/item/field``) that names where the key lives. Everything that
  needs to KNOW about the key — the Billing picker, status displays, ``romp keyswap``'s listing and
  identity checks — reads the source. Only resolve() ever runs ``op read``, at the moment a Claude
  session launches, an API-key-billed judge call is made, or the model catalog refreshes; the value
  is handed to that operation and never written to disk. A SUCCESSFUL read is kept in memory for
  ``ROMP_OP_REUSE_S`` seconds (60 by default, 0 disables) so a wave of launches and judge calls
  shares one retrieval; a failure is never kept, and forget_resolved() drops the memory the moment
  the source or the file moves.
* A selected source is AUTHORITATIVE. A file that once carried a key line or a reference keeps
  governing this process: emptying it, removing the line, or making it unreadable is an error the
  operation reports, never permission to fall back to the key the manager started with or to a
  login. That is what makes ``romp keyswap`` a swap — nothing an operator removed can come back.
* Supervised managers (``ROMP_SUPERVISED=1``: the systemd/launchd service) read the FILE only. The
  manager process keeps the environment it started with across every kernel restart, so a key it
  inherited would otherwise resurrect after the operator removed it from the file. A startup key
  the kernel therefore ignores is said once on the log wire (sdk_backend.work_api_key_source).
* ``op``'s OWN credential (a service-account token, a session token) is the one secret that must
  reach the kernel's environment for headless use — and nothing else: claim_op_env() takes those
  names out of os.environ at startup and resolve() hands them back to the ``op read`` subprocess
  alone, so no Claude session, judge child or tmux launch inherits a vault-wide credential. The
  tmux SERVER is scrubbed of the same names — and of a stale ``ANTHROPIC_API_KEY`` — whenever romp
  becomes the op consumer, not only at kernel start (2026-09-06: a keyswap to a reference with no
  restart left both in the server's globals, and every new pane billed the old key).
* An op source selected from the FILE is remembered on disk (``service.env.source``, beside the file),
  so a supervised restart after the reference line vanished still refuses to fall to a login
  (2026-09-06: the memory was process-local and a kernel restart forgot it).

Legacy environment/file keys and Claude login remain supported without 1Password.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field

KEY_VAR = "ANTHROPIC_API_KEY"
REF_VAR = "ROMP_API_KEY_REF"
OP_TIMEOUT = 15
# One bounded retry of a failed or timed-out `op read` (2 attempts, ~1 s apart): a transient blip in op's
# own network leg used to fail a judge pass and kill every connecting session outright (2026-09-07). The
# worst case, 2 x OP_TIMEOUT + the delay, still fits under one judge call.
OP_ATTEMPTS = 2
OP_RETRY_DELAY_S = 1.0
# ROMP_OP_REUSE_S: how long (seconds) a value resolve() just retrieved is handed to the NEXT caller for the
# same source without another `op read`; 0 disables. In memory only, never on disk; a failure is never kept.
OP_REUSE_S_DEFAULT = 60
# The one sentence an `op read` that failed for a reason outside op's own vocabulary (an exception the
# subprocess machinery raised, a codec error) is reported with — never the exception's text.
OP_UNEXPECTED_ERROR = "1Password credential retrieval failed unexpectedly; check the manager's op installation and env file"
OP_ENV_INVALID_ERROR = "the op credential lines in the env file contain invalid characters"
# The environment names the 1Password CLI authenticates from. Claimed out of the kernel's environment
# once (claim_op_env) and given back to the `op read` subprocess only (resolve): a service-account
# token reads every field the account can see, and a child that inherits it — a Claude session's
# Bash, a judge call, a subagent — could print it with `env`. OP_SESSION_<account> is the name shape
# `op signin` exports; anything with that prefix is treated the same way.
OP_ENV_NAMES = ("OP_SERVICE_ACCOUNT_TOKEN", "OP_CONNECT_HOST", "OP_CONNECT_TOKEN", "OP_ACCOUNT")
OP_ENV_PREFIX = "OP_SESSION_"
_OP_ENV: dict[str, str] = {}
_OP_CLAIM_SAID = False
_TMUX_SCRUBBED: set = set()      # names already unset from the tmux server's globals by this process
# The sibling file that remembers, across kernel restarts, that the env file's source was a 1Password
# reference: `service.env.source` (sibling_path), containing the word `op` and never a value.
SOURCE_MARKER = "source"


def is_op_env_name(name: str) -> bool:
    return name in OP_ENV_NAMES or name.startswith(OP_ENV_PREFIX)


def is_tmux_scrub_name(name: str) -> bool:
    """The ONE list of names romp removes from a tmux server's globals and a tmux launch's client env
    while it is the op consumer: op's credential names, and ANTHROPIC_API_KEY — the key the manager
    started with, which a keyswap to a reference retired but the tmux server still carried, so every new
    pane billed it (review find, 2026-09-06). A pane without it falls to Claude Code's own auth (login or
    apiKeyHelper). Never applied on a box with no reference: static-key panes rely on the inheritance."""
    return is_op_env_name(name) or name == KEY_VAR


def op_consumer() -> bool:
    """Is romp itself the one running `op`? Only when a 1Password reference is selected (in the env file
    or the manager's environment). A box whose SESSIONS fetch their key through Claude Code's apiKeyHelper
    calling `op` needs op's credential in every session's environment, and romp then leaves it alone."""
    if REF_VAR in os.environ:
        return True
    return read_source().kind in ("op", "error") if os.path.exists(service_env_path()) else False


def claim_op_env() -> dict[str, str]:
    """Take op's credential names out of os.environ and return the running stash — but only while romp
    is the op consumer (see op_consumer): otherwise the environment is left exactly as found, since a
    session-side helper may need it. Idempotent and cheap; a value that appears later is claimed too. The
    claimed NAMES (never values) are said once on the log wire so a helper that stops working has a
    line to be found by."""
    global _OP_CLAIM_SAID
    if not op_consumer():
        return _OP_ENV
    names = [k for k in os.environ if is_op_env_name(k)]
    for k in names:
        _OP_ENV[k] = os.environ.pop(k)
    if names and not _OP_CLAIM_SAID:
        _OP_CLAIM_SAID = True
        import sys
        sys.stderr.write("op credentials claimed from the environment for `op read`: %s — sessions, judge "
                         "calls and tmux launches will not see them\n" % ", ".join(sorted(names)))
    # The tmux server the manager started carries the same environment the kernel did, and every pane
    # inherits the SERVER's globals: scrub it of what was just claimed, plus the manager's startup
    # ANTHROPIC_API_KEY. Here, not only in kernel main() — a `romp keyswap` to a reference on a box that
    # started without one makes romp the op consumer mid-run, and the first claim after it is the moment
    # the server still holds the token (review find, 2026-09-06). Once per name per process; best effort.
    pending = (set(_OP_ENV) | {KEY_VAR}) - _TMUX_SCRUBBED
    if pending:
        tmux_unset_global(pending, os.environ.get("ROMP_TMUX_SOCKET", ""))
        _TMUX_SCRUBBED.update(pending)
    return _OP_ENV


def strip_op_env(env: dict) -> dict:
    """The same names removed from a child environment built before the claim (a standalone judge, a
    kernel that spawned a tmux launch before its backend existed) — while romp is the op consumer; a
    helper box keeps its environment. Returns `env` for chaining."""
    if _OP_ENV or op_consumer():
        for k in [k for k in env if is_op_env_name(k)]:
            env.pop(k, None)
    return env


def strip_tmux_env(env: dict) -> dict:
    """The environment a tmux launch (`romp new -t`, `romp resume --detach`) is spawned with: op's
    credential (strip_op_env) and, while romp is the op consumer, ANTHROPIC_API_KEY as well — the pane
    must not bill the key the manager started with once a reference governs (is_tmux_scrub_name). A box
    with no reference keeps both, as before. Returns `env` for chaining."""
    strip_op_env(env)
    if _OP_ENV or op_consumer():
        env.pop(KEY_VAR, None)
    return env


def tmux_unset_global(names, socket: str = "") -> list:
    """Remove credential names from a tmux SERVER's global environment — the environment every new
    pane inherits, which the manager-started server carried from service.env (review find, 2026-09-05:
    a tmux session's `exec claude` saw the token although the launching client had been scrubbed). The
    names are filtered through is_tmux_scrub_name, the one list. Best effort: no tmux, no server, an old
    tmux → nothing happens. Returns the commands run, for tests."""
    ran = []
    for name in sorted(set(n for n in names if is_tmux_scrub_name(n))):
        cmd = ["tmux"] + (["-L", socket] if socket else []) + ["set-environment", "-gu", name]
        try:
            _TMUX_RUN(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      timeout=5, check=False)
            ran.append(cmd)
        except Exception:      # best effort by contract: a scrub that cannot run must never fail a launch
            pass
    return ran


# Bound at import, not looked up per call: every suite here fakes `op read` by patching subprocess.run,
# and the tmux scrub that claim_op_env now runs would otherwise register as a credential retrieval (or
# raise the fake's "unexpected retrieval"). Tests that want to see the scrub patch THIS name.
_TMUX_RUN = subprocess.run


def runtime_reserved_names(auth: str, source) -> tuple:
    """The credential names a per-session environment may NOT carry while runtime retrieval governs.
    A per-session ANTHROPIC_API_KEY always competes with the selected source. A KEYED launch (an explicit
    key pick, or no pick with a configured source) must carry no token beside the key it resolves either;
    a LOGIN session's own token override bills the account the user chose for it and never touches the
    key source, so it stays (review find, 2026-09-05). One rule for the doors, the launch and the fork."""
    if source is None or source.kind not in ("op", "error"):
        return ()
    keyed = auth == "key" or (auth != "login" and source.configured)
    return ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN") if keyed else ("ANTHROPIC_API_KEY",)

# Cache file configuration only. ctime/mode also invalidate permission changes; a formerly
# readable credential must not survive a chmod merely because its content did not change.
_CACHE: tuple = ((), "")
_AUTHORITATIVE_PATHS: dict[str, str] = {}
_ENV_PROVIDER_PATHS: set[str] = set()


class KeySourceError(RuntimeError):
    """A credential failure whose message is safe for user-visible logs."""


# The ONE remedy text for "API-key billing selected, no source configured": the launch card, the kernel
# log, the judge note, the boot preflight and the CLI all say this, so an operator reads the same line
# wherever the failure surfaces (the user 2026-09-07, whose board was down for twenty minutes while every
# surface described the outage differently). %s is service_env_path().
REMEDY = ("No API key source is configured for API-key billing. Run `romp keysource`, or add "
          "ROMP_API_KEY_REF=op://vault/item/field (and OP_SERVICE_ACCOUNT_TOKEN for a headless service) "
          "to %s; held sessions resume on their own once it is there.")


def remedy() -> str:
    return REMEDY % service_env_path()


# Claude Code's own settings files, in the order the CLI consults them: the user's ($CLAUDE_CONFIG_DIR or
# ~/.claude) and the managed (admin) files. Read for the PRESENCE of an apiKeyHelper only.
CLI_MANAGED_SETTINGS = ("/etc/claude-code/managed-settings.json",
                        "/Library/Application Support/ClaudeCode/managed-settings.json")
_CLI_SETTINGS_CACHE: dict[str, tuple] = {}     # path -> (file identity, names a helper) — never the command


def cli_settings_paths() -> tuple:
    d = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return (os.path.join(d, "settings.json"),) + CLI_MANAGED_SETTINGS


def _settings_name_a_helper(path: str) -> bool:
    """Whether one settings file carries a non-empty "apiKeyHelper". Stat-cached like read_source; the
    helper COMMAND is never read into anything that outlives this call, let alone returned or logged."""
    try:
        st = os.stat(path)
    except OSError:
        _CLI_SETTINGS_CACHE.pop(path, None)
        return False
    ident = (st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size)
    hit = _CLI_SETTINGS_CACHE.get(path)
    if hit is not None and hit[0] == ident:
        return hit[1]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            d = json.load(fh)
        has = isinstance(d, dict) and bool(str(d.get("apiKeyHelper") or "").strip())
    except (OSError, ValueError):
        has = False
    _CLI_SETTINGS_CACHE[path] = (ident, has)
    return has


def cli_self_auth() -> str:
    """"apiKeyHelper" when Claude Code's own settings (user or managed) name an API-key helper, else "".
    Presence only: this says whether a CLI child launched WITHOUT an injected key can still authenticate
    itself the way it did before runtime retrieval existed (the pre-2026-09-07 behaviour a hard error
    replaced, taking the board down); it never reads, returns or runs the helper command."""
    for p in cli_settings_paths():
        if _settings_name_a_helper(p):
            return "apiKeyHelper"
    return ""


# ---- in-process retrieval memory (K3): single flight per source, bounded reuse, never on disk ----
class _Flight:
    """One `op read` in progress for a source fingerprint; waiters take its verdict instead of spawning
    their own op. repr hides the value: module state is dumped into test assertions and debug output."""
    __slots__ = ("done", "value", "error")

    def __init__(self):
        self.done, self.value, self.error = False, "", ""

    def __repr__(self):
        return "<op read %s>" % ("failed" if self.error else "done" if self.done else "in flight")


class _Resolved:
    __slots__ = ("value", "t")

    def __init__(self, value: str, t: float):
        self.value, self.t = value, t

    def __repr__(self):
        return "<resolved t=%.0f>" % self.t


_RESOLVE_CV = threading.Condition()            # guards _RESOLVED, _RESOLVING and _HEALTH
_RESOLVED: dict[str, _Resolved] = {}           # source fingerprint -> value retrieved within the reuse window
_RESOLVING: dict[str, _Flight] = {}            # source fingerprint -> the `op read` in flight for it
_HEALTH = {"kind": "", "sourceFp": "", "lastOkT": 0.0, "lastFailT": 0.0, "note": ""}
_SLEEP = time.sleep                            # the retry delay; bound at import so tests can make it instant


def op_reuse_s() -> float:
    """The reuse window from ROMP_OP_REUSE_S (seconds; 0 disables), read at call time; the default when
    the variable is unset or not a number."""
    raw = (os.environ.get("ROMP_OP_REUSE_S") or "").strip()
    if not raw:
        return float(OP_REUSE_S_DEFAULT)
    try:
        return max(0.0, float(raw))
    except ValueError:
        return float(OP_REUSE_S_DEFAULT)


def forget_resolved() -> None:
    """Drop every value the reuse window still holds: the next resolve() runs `op read` again. Called when
    the env file changes identity (read_source), when write_source / write_assignment rewrite it, and by a
    caller that knows the world moved (a keyswap, a rotation)."""
    with _RESOLVE_CV:
        _RESOLVED.clear()


def health() -> dict:
    """The one health line for credential retrieval: the selected source's kind and fingerprint (never a
    value), when a retrieval last succeeded / failed (epoch seconds, 0.0 for never), and the last failure's
    fixed note. select_source() keeps kind/sourceFp current; resolve() stamps the times."""
    with _RESOLVE_CV:
        return dict(_HEALTH)


def _health_result(ok: bool, note: str = "") -> None:
    with _RESOLVE_CV:
        if ok:
            _HEALTH["lastOkT"], _HEALTH["note"] = time.time(), ""
        else:
            _HEALTH["lastFailT"], _HEALTH["note"] = time.time(), note


def _op_failure_class(stderr) -> str:
    """op's stderr reduced to a FIXED vocabulary — auth, not-found, network, other — from a few substring
    rules. The text itself is never stored, logged or returned: 1Password's messages can echo the
    reference, an account name, or a token fragment."""
    try:
        text = (stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else str(stderr or "")).lower()
    except Exception:
        return "other"
    if any(k in text for k in ("isn't an item", "is not an item", "not found", "no item", "isn't a vault",
                               "doesn't exist", "does not exist", "no such field")):
        return "not-found"
    if any(k in text for k in ("401", "unauthorized", "token", "not signed in", "session expired",
                               "authentication", "sign in", "signin")):
        return "auth"
    if any(k in text for k in ("dial", "timeout", "timed out", "network", "connection", "no such host",
                               "tls", "unreachable", "connect:")):
        return "network"
    return "other"


@dataclass(frozen=True)
class KeySource:
    kind: str
    value: str = field(default="", repr=False)
    error: str = ""

    @property
    def configured(self) -> bool:
        # An invalid provider remains an explicit choice, never permission to use a login.
        return self.kind in ("op", "error") or bool(self.value)

    def validate(self) -> None:
        if self.kind == "error":
            raise KeySourceError(self.error or "Cannot read the configured API key source")
        if self.kind == "op":
            parts = self.value[5:].split("/") if self.value.startswith("op://") else []
            if (len(parts) not in (3, 4) or not all(parts)
                    or any(c in self.value for c in ("\r", "\n", "\0"))):
                raise KeySourceError("ROMP_API_KEY_REF must be an op://vault/item/[section/]field reference")
        elif self.kind not in ("file", "environment", "none"):
            raise KeySourceError("Unknown API key source")
        elif any(c in self.value for c in ("\r", "\n", "\0")):
            raise KeySourceError("API keys must be a single line")

    def fingerprint(self) -> str:
        """Configuration identity; for op this hashes the reference, never retrieves its value."""
        if self.kind == "op":
            return fingerprint("op:" + self.value)
        if self.kind == "error":
            return ""
        return fingerprint(self.value)

    def resolve(self) -> str:
        """The key itself, for ONE operation. For an op source: one `op read` per source at a time — a wave
        of concurrent callers (six judge threads, several sessions connecting at once) shares the read in
        flight instead of each spawning op — and a value retrieved within the reuse window (op_reuse_s) is
        handed out again without a new read. A failure is shared with the callers that waited on that very
        read and remembered by nobody: the next caller retrieves afresh (judge.py's per-pass gate composes
        with this — the memory here shares success, the gate shares failure)."""
        try:
            self.validate()
        except KeySourceError as e:
            _health_result(False, str(e))
            raise
        if self.kind != "op":
            _health_result(True)
            return self.value
        fp, reuse, now = self.fingerprint(), op_reuse_s(), time.monotonic()
        with _RESOLVE_CV:
            hit = _RESOLVED.get(fp)
            if hit is not None and reuse > 0 and now - hit.t < reuse:
                return hit.value
            flight = _RESOLVING.get(fp)
            if flight is not None:
                deadline = now + OP_ATTEMPTS * OP_TIMEOUT + OP_RETRY_DELAY_S + 5
                while not flight.done:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break              # a reader that never reported: read for ourselves below
                    _RESOLVE_CV.wait(timeout=left)
                if flight.done:
                    if flight.error:
                        raise KeySourceError(flight.error)
                    return flight.value
            flight = _RESOLVING[fp] = _Flight()
        # The flight is retired on EVERY exit — a KeySourceError, any other exception, even a
        # KeyboardInterrupt — or the waiters above sit out their whole deadline on a reader that never
        # reported, and the next caller finds a flight nobody owns (review find, 2026-09-07). An
        # unexpected exception becomes a KeySourceError with a FIXED sentence: str(e) of a subprocess
        # or codec error can carry the env value or the reference it choked on.
        value, error = "", ""
        try:
            value = self._op_read()
        except KeySourceError as e:
            error = str(e)
            raise
        except Exception:
            error = OP_UNEXPECTED_ERROR
            raise KeySourceError(error) from None
        except BaseException:               # an interrupt stays an interrupt for this caller; the waiters
            error = OP_UNEXPECTED_ERROR     # still get a verdict instead of a deadline
            raise
        finally:
            with _RESOLVE_CV:
                flight.value, flight.error, flight.done = ("" if error else value), error, True
                if not error and reuse > 0:
                    _RESOLVED[fp] = _Resolved(value, time.monotonic())
                if _RESOLVING.get(fp) is flight:
                    del _RESOLVING[fp]
                _RESOLVE_CV.notify_all()
            _health_result(not error, error)
        return value

    def _op_read(self) -> str:
        """Run `op read` (with the one bounded retry) and validate what came back. Neither op's stderr nor a
        subprocess exception's repr is safe to log: stderr is reduced to _op_failure_class's vocabulary and
        the exit code, and every raise is `from None`."""
        # op authenticates from the credential names claimed at startup; they ride into THIS subprocess
        # and no other (see claim_op_env). The rest of the environment is a whitelist, not a copy
        # (review find, 2026-09-06: the copy carried ROMP_SERVE_TOKEN — full control of every session —
        # and the manager's startup ANTHROPIC_API_KEY into a third-party binary). op needs HOME and the
        # XDG_* names to find `~/.config/op` and the desktop app's socket, PATH to run, TMPDIR/LANG/LC_*/
        # TERM for ordinary CLI behaviour, USER/LOGNAME for its account defaults; nothing else of romp's.
        op_env = op_subprocess_env()
        attempt = 0
        while True:
            attempt += 1
            try:
                result = subprocess.run(
                    ["op", "read", "--no-newline", self.value], stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=OP_TIMEOUT,
                    check=False, env=op_env,
                )
            except FileNotFoundError:
                raise KeySourceError("1Password CLI (op) is not on the manager's PATH") from None
            except subprocess.TimeoutExpired:
                if attempt < OP_ATTEMPTS:
                    _SLEEP(OP_RETRY_DELAY_S)
                    continue
                raise KeySourceError("1Password credential retrieval timed out; check op authentication "
                                     "(op: network, timeout after %d attempts)" % attempt) from None
            except OSError:
                raise KeySourceError("Cannot run 1Password CLI; check the manager's op installation") from None
            except ValueError:
                # subprocess refuses an env value with an embedded NUL (file_op_env drops those, but the
                # claimed stash and os.environ are not filtered); the ValueError's text quotes the value
                raise KeySourceError(OP_ENV_INVALID_ERROR) from None
            if result.returncode:
                if attempt < OP_ATTEMPTS:
                    _SLEEP(OP_RETRY_DELAY_S)
                    continue
                raise KeySourceError("1Password credential retrieval failed; check op authentication and vault "
                                     "access (op: %s, exit %d)" % (_op_failure_class(result.stderr), result.returncode))
            break
        try:
            value = result.stdout.decode("utf-8")
        except UnicodeError:
            raise KeySourceError("1Password returned an invalid API key") from None
        if not value or len(value) > 16384 or any(c.isspace() or c == "\0" for c in value):
            raise KeySourceError("1Password returned an empty or invalid API key")
        return value


OP_ENV_PASSTHROUGH = ("PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "TERM")
OP_ENV_PASSTHROUGH_PREFIXES = ("LC_", "XDG_")


def op_subprocess_env() -> dict:
    """The environment the `op read` subprocess gets and nothing more: the passthrough names above, op's
    own credential names — the claimed stash, plus any still in os.environ (a caller resolving a
    reference on a box where romp never became the consumer), with the env FILE's own OP_* lines winning
    over both (file_op_env) — and no romp variable of any kind."""
    env = {k: v for k, v in os.environ.items()
           if k in OP_ENV_PASSTHROUGH or k.startswith(OP_ENV_PASSTHROUGH_PREFIXES) or is_op_env_name(k)}
    env.update(claim_op_env())
    env.update(file_op_env())
    return env


def file_op_env(path: str | None = None) -> dict:
    """op's credential lines (the OP_* names, is_op_env_name) read from the service env file at RESOLVE
    time: literal assignments, never `source`d, never exported into os.environ, handed to the `op read`
    subprocess alone. The file outranks the stash claim_op_env took from the manager's start-time
    environment because a supervised manager keeps that environment until the service restarts — so a
    token written after start (a keyswap to a reference, a token rotation) could not authenticate op
    until an operator restarted the service, and nothing said so (2026-09-07). Unreadable file → {} (the
    retrieval then fails loudly on its own)."""
    try:
        with open(path or service_env_path(), "r", encoding="utf-8", errors="replace") as fh:
            values = _assignments(fh.read(), is_op_env_name)
    except OSError:
        return {}
    # A value with a control character (a NUL, a stray escape) is dropped like a garbled one (_garbled):
    # subprocess would refuse the NUL with a ValueError that quotes the value, and no op credential
    # legitimately carries one. Dropping it leaves the retrieval to fail on its own, loudly and safely.
    return {k: v for k, v in values.items()
            if v and "\ufffd" not in v and not any(c < " " or c == "\x7f" for c in v)}


_NO_OP_CRED_SAID = False
_OP_CRED_SEEN = False


def _note_missing_op_credential(path: str) -> None:
    """Said ONCE per process when a 1Password reference is selected but op has nothing to authenticate
    with — no OP_* name in the claimed stash, the environment, or the env file. Before this the only
    evidence was a failed retrieval per session and per judge call."""
    global _NO_OP_CRED_SAID, _OP_CRED_SEEN
    if _NO_OP_CRED_SAID or _OP_CRED_SEEN:
        return
    if _OP_ENV or any(is_op_env_name(k) for k in os.environ) or file_op_env(path):
        _OP_CRED_SEEN = True
        return
    _NO_OP_CRED_SAID = True
    # Worded for both boxes it can land on: a headless service (the token line is missing, or was
    # added after the manager started) and a desktop-app sign-in (no OP_* name anywhere, and op is
    # fine) — the second must not be sent to write a token it does not need.
    sys.stderr.write("1Password reference selected but this kernel has no op credential (no OP_* name in "
                     "its environment or in %s): a headless service needs OP_SERVICE_ACCOUNT_TOKEN in that "
                     "file, then restart the service; a desktop-app sign-in needs nothing here\n" % path)


def service_env_path() -> str:
    """The path of the env file the manager is configured from.

    `ROMP_SERVICE_ENV_FILE` is the name the installer and the macOS launcher already use
    (`bin/romp-service`, `bin/romp-node-launch`), so it is the primary; `ROMP_SERVICE_ENV` is
    accepted as an alias. Default `${XDG_CONFIG_HOME:-~/.config}/romp/service.env` — the same
    expression those two scripts compute, so all three always name one file.
    """
    for var in ("ROMP_SERVICE_ENV_FILE", "ROMP_SERVICE_ENV"):
        p = (os.environ.get(var) or "").strip()
        if p:
            return os.path.expanduser(p)
    base = (os.environ.get("XDG_CONFIG_HOME") or "").strip() or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "romp", "service.env")


def sibling_path(name: str, path: str | None = None) -> str:
    """The candidate file a keyswap reads from. A bare name (`highprio`) means the sibling
    `service.env.<name>` beside the live file — the convention that keeps candidate files together
    with mode 0600 (a private parent directory needs 0700). Anything with a separator, or an explicit
    path, is taken as given."""
    name = str(name or "").strip()
    if not name:
        return ""
    if os.sep in name or name.startswith("~"):
        return os.path.expanduser(name)
    return (path or service_env_path()) + "." + name


def _assignments(text: str, keep=None) -> dict[str, str]:
    """Read literal env assignments; never execute or expand the file's contents. `keep` selects the
    names (a predicate); default: the key and reference lines."""
    out = {}
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if not sep or not (keep(name.strip()) if keep else name.strip() in (KEY_VAR, REF_VAR)):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]     # systemd strips one layer; without this the CLI gets the quotes
        out[name.strip()] = value
    return out


def _garbled(values: dict) -> str:
    """The name of a key/reference line that did not decode as UTF-8 (the read replaces bad bytes with
    U+FFFD, which no real key contains) — "" when both are clean. A garbled key must fail HERE, with a
    readable note, not at the API as an unexplained invalid-key error."""
    for name in (REF_VAR, KEY_VAR):
        if "\ufffd" in values.get(name, ""):
            return name
    return ""


def parse_key(text: str) -> str:
    return _assignments(text).get(KEY_VAR, "")


def parse_source(text: str) -> KeySource:
    values = _assignments(text)
    bad = _garbled(values)
    if bad:
        return KeySource("error", error="the %s line in the API key source configuration is not valid UTF-8" % bad)
    if REF_VAR in values:
        return KeySource("op", values[REF_VAR])
    if KEY_VAR in values:
        return KeySource("file", values[KEY_VAR])
    return KeySource("none")


def read_source(path: str | None = None) -> KeySource:
    """Read configuration only. Read errors remain errors, never an absent credential."""
    global _CACHE
    p = path or service_env_path()
    try:
        st = os.stat(p)
        ident = (p, st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_mode)
    except FileNotFoundError:
        if _CACHE[0] != (p, "absent"):
            forget_resolved()                 # a new file identity: nothing retrieved under the old one is reused
        _CACHE = ((p, "absent"), KeySource("none"))
        return _CACHE[1]
    except OSError:
        return KeySource("error", error="Cannot read the API key source configuration")
    if _CACHE[0] == ident:
        return _CACHE[1]
    forget_resolved()
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:   # a stray byte in a comment is not an outage
            source = parse_source(fh.read())
    except OSError:
        return KeySource("error", error="Cannot read the API key source configuration")
    _CACHE = (ident, source)
    return source


def select_source(startup_key: str = "") -> KeySource:
    """_select_source, plus the bookkeeping every selection carries: health()'s kind/sourceFp, and the
    one-shot notice when an op reference is selected with nothing for op to authenticate from."""
    source = _select_source(startup_key)
    with _RESOLVE_CV:
        _HEALTH["kind"], _HEALTH["sourceFp"] = source.kind, source.fingerprint()
    if source.kind == "op":
        _note_missing_op_credential(service_env_path())
    return source


def _select_source(startup_key: str = "") -> KeySource:
    """Choose a source without fetching it; only a never-configured file permits env fallback.

    Track the path, not one global flag, so isolated kernels/tests with different config roots
    cannot change each other's source policy. An unreadable file is always an error. Once an op
    reference has been selected from a file or the environment, deleting it is an error until a new
    source is configured. A previously selected file stays authoritative over the environment.
    Supervised services always use the file: their manager may still hold an old assignment across
    kernel restarts, so inherited credentials cannot establish a fallback for a fresh kernel.
    """
    path = service_env_path()
    source = read_source(path)
    if source.kind != "none":
        if source.kind != "error":
            if _AUTHORITATIVE_PATHS.get(path) != source.kind:      # a transition: mirror it to disk once
                remember_file_source(path, source.kind)
            _AUTHORITATIVE_PATHS[path] = source.kind
        return source
    # The op memory is DURABLE (2026-09-06): a supervised kernel restart forgot the process-local entry,
    # and with the reference line gone from the file and none in the manager's environment every
    # session without an explicit pick launched on the login — the silent fallback this module exists
    # to end. The marker beside the file says `op` until a non-op source is selected or written.
    previous = _AUTHORITATIVE_PATHS.get(path) or ("op" if read_marker(path) == "op" else None)
    if previous == "op":
        return KeySource("error", error="The 1Password reference was removed; configure an API key source explicitly")
    if previous:
        return KeySource("file")
    if os.environ.get("ROMP_SUPERVISED") == "1":
        if REF_VAR in os.environ:
            _AUTHORITATIVE_PATHS[path] = "op"
            return KeySource("error", error="The 1Password reference was removed; configure an API key source explicitly")
        _AUTHORITATIVE_PATHS[path] = "file"
        return KeySource("file")
    if REF_VAR in os.environ:
        _ENV_PROVIDER_PATHS.add(path)
        return KeySource("op", os.environ[REF_VAR].strip())
    if path in _ENV_PROVIDER_PATHS:
        return KeySource("error", error="The 1Password reference was removed from the environment; configure an API key source explicitly")
    return KeySource("environment", startup_key) if startup_key else source


def marker_path(path: str | None = None) -> str:
    """`service.env.source` beside the env file: the durable memory that the file's selected source was a
    1Password reference. Holds the word `op` (never a reference, never a value); absent otherwise."""
    return sibling_path(SOURCE_MARKER, path)


def read_marker(path: str | None = None) -> str:
    try:
        with open(marker_path(path), "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(16).strip()
    except OSError:
        return ""


def remember_file_source(path: str | None, kind: str) -> None:
    """Mirror a source selected from (or written to) the env file onto the marker: `op` writes it, any
    other kind removes it, so an operator's intentional switch to a static key is not an error at the
    next restart. Atomic, 0600, same directory, like write_source. Best effort — a read-only config
    directory must not fail the selection; the process-local memory still governs this process."""
    mp = marker_path(path)
    try:
        if kind != "op":
            if os.path.lexists(mp):
                os.unlink(mp)
            return
        if read_marker(path) == "op":
            return
        d = os.path.dirname(mp) or "."
        fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(mp) + ".")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("op\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, mp)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        pass


def read_key(path: str | None = None) -> str:
    """Legacy file reader. Provider references are never interpreted as raw keys."""
    source = read_source(path)
    return source.value if source.kind == "file" else ""


def fingerprint(key: str) -> str:
    """The ONLY renderable form of a key: first 12 hex of its sha256, "" for no key. Same value in
    the kernel log and in `romp keyswap`'s output, so an operator can check that the kernel reads
    the key they just wrote without either side ever printing it."""
    key = str(key or "")
    if not key:
        return ""
    return hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:12]


def write_source(source: KeySource, path: str | None = None) -> dict:
    """Atomically select a source, removing competing key/reference assignments.

    Every other line survives byte for byte, in place — the file also carries things like
    `ROMP_PERF=1` and `ROMP_EXPECTED_AUTH`, and a rewrite that dropped them would change the
    manager's behaviour on its next start for reasons nobody would connect to a key swap. The
    key line keeps its POSITION (last assignment replaced in place, any earlier duplicate
    removed so the file cannot disagree with itself); a file with no such line gets one appended.

    Written to a temp file in the same directory, created 0600 with O_EXCL, then `os.replace` —
    so no reader ever sees a half-written file and the key is never briefly world-readable. The
    mode is the original file's, narrowed to 0600 if it granted group or other any access at all;
    a new file is 0600.

    A SYMLINKED env file is written THROUGH (2026-09-04): a dotfiles-managed `service.env` is a link,
    and an `os.replace` onto the link's own name would swap the link for a plain file and leave its
    target — what the operator's repo tracks and what a re-link would restore — on the old key.

    Returns {"path", "old", "new", "mode", "tightened", "lines", "target"} — `old`/`new` are source
    descriptors, so the caller can report their identities without resolving provider credentials;
    `target` is the file actually rewritten (the link's target, else `path`). Raises OSError on a real
    failure (the caller reports it).
    """
    source.validate()
    if source.kind not in ("op", "file", "environment"):
        raise KeySourceError("Select an API key or a 1Password reference")
    given = path or service_env_path()
    new_line = "%s=%s" % (REF_VAR if source.kind == "op" else KEY_VAR, source.value)
    r = _rewrite_assignment(given, (KEY_VAR, REF_VAR), new_line)
    # The durable op memory follows the write: a swap to a reference arms it, a swap to a static key
    # clears it, so the marker never outlives the choice it records (select_source consults it).
    remember_file_source(given, "op" if source.kind == "op" else "file")
    return {"path": given, "old": parse_source(r["body"]), "new": source, "mode": r["mode"],
            "tightened": r["tightened"], "lines": r["lines"], "target": r["target"]}


def write_assignment(name: str, value: str, path: str | None = None) -> dict:
    """Atomically set ONE `NAME=VALUE` line in the env file, with write_source's guarantees (the last
    assignment replaced in place, earlier duplicates dropped, every other line byte-identical, 0600 temp
    file + os.replace, a symlink written through, mode narrowed to 0600). The CLI's way of adding
    OP_SERVICE_ACCOUNT_TOKEN beside a reference without an operator hand-editing a 0600 file. Returns
    {"path", "target", "mode", "tightened", "replaced"}; `replaced` says whether a line was already there."""
    name = str(name or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise KeySourceError("Environment variable names are letters, digits and underscores")
    value = str(value or "")
    if any(c in value for c in ("\r", "\n", "\0")):
        raise KeySourceError("Environment values must be a single line")
    given = path or service_env_path()
    r = _rewrite_assignment(given, (name,), "%s=%s" % (name, value))
    return {"path": given, "target": r["target"], "mode": r["mode"], "tightened": r["tightened"],
            "replaced": r["replaced"]}


def _rewrite_assignment(given: str, names: tuple, new_line: str) -> dict:
    """The shared rewrite under write_source and write_assignment (see write_source for the guarantees).
    Returns the file's previous body, the target actually written, the mode, whether it was tightened,
    the line count, and whether an existing assignment of one of `names` was replaced."""
    p = os.path.realpath(given) if os.path.islink(given) else given
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            body = fh.read()
        existed = True
    except FileNotFoundError:
        body, existed = "", False
    lines = body.splitlines()
    trailing_nl = (not body) or body.endswith("\n")
    # Which physical lines assign the name: the LAST one is rewritten in place, earlier ones drop.
    hits = [i for i, raw in enumerate(lines)
            if raw.strip() and not raw.strip().startswith("#")
            and raw.strip().partition("=")[1]
            and raw.strip().partition("=")[0].strip() in names]
    if hits:
        lines[hits[-1]] = new_line
        for i in reversed(hits[:-1]):
            del lines[i]
    else:
        lines.append(new_line)
        trailing_nl = True
    out = "\n".join(lines) + ("\n" if trailing_nl else "")
    mode, tightened = 0o600, False
    if existed:
        try:
            mode = os.stat(p).st_mode & 0o777
            if mode & 0o077:            # never leave a key group- or world-readable
                mode, tightened = 0o600, True
        except OSError:
            mode = 0o600
    d = os.path.dirname(p) or "."
    # mkstemp: same directory (so the rename is atomic — a cross-filesystem one is a copy), a name
    # nothing can collide with, and 0600 from the moment the file exists, so the key is never
    # briefly readable by anyone else.
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(p) + ".keyswap.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(out)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    forget_resolved()                          # the file moved under every value retrieved so far
    return {"body": body, "target": p, "mode": mode, "tightened": tightened, "lines": len(lines),
            "replaced": bool(hits)}


def write_key(key: str, path: str | None = None) -> dict:
    """Compatibility API for callers explicitly writing a legacy static key."""
    result = write_source(KeySource("file", key), path)
    return dict(result, old=result["old"].value if result["old"].kind == "file" else "", new=key)
