"""Global test isolation (2026-07-07): point XDG_STATE_HOME at a fresh temp dir BEFORE any test module
loads bin/romp-judge or bin/romp-kernel — both resolve their state root at import time. Without this,
any test that skips its own rebind writes into the REAL ~/.local/state/romp (the diary guard's
judge-errors.jsonl lines from legacy-flag fixtures made that visible). conftest.py imports before every
test module, so this is a suite-wide floor; per-class _rebind_state/tempdir isolation still layers on
top exactly as before."""
import os
import sys
import tempfile

import pytest

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="romp-tests-state-")   # recorded by tests/__init__.py's hook
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel exports this to its sessions; it outranks the XDG floor


def pytest_sessionfinish(session, exitstatus):
    """Temp-directory hygiene (2026-09-06, see tests/__init__.py): remove every directory this
    process made through tempfile.mkdtemp, this state root included, when the session ends. Runs in
    the controller and in every xdist worker, since each is its own pytest session. The package's
    atexit hook does the same at interpreter exit; both are idempotent."""
    try:
        from tests import remove_made_dirs
    except Exception:
        return
    remove_made_dirs()


# No test may reach a REAL manager control port (2026-08-27): on a machine running a live romp,
# every shell the manager tree spawns inherits ROMP_MANAGER_PORT, and any test kernel that dials
# "the manager" through the inherited value restarts the ACTUAL deployment — the serve-layer
# restart test's pop-then-restore raced the /restart handler's post-ack env read and took a
# self-hosted instance down mid-suite, repeatedly. POISONED to a dead port, never popped: an
# absent var is the one unsafe state, because _restart_this_kernel treats absent as "no manager"
# but _run_main_update maps absent to the DEFAULT port — the live one — so only a dead value is
# safe against every consumer. Import-time, so collection-time code is floored too.
os.environ["ROMP_MANAGER_PORT"] = "1"

# No test may read the REAL service.env (2026-09-04): sdk_backend.work_api_key now reads the manager
# env file LIVE (kernel/keysource.py) instead of popping os.environ once, so on a machine running a
# live romp every auth test would otherwise resolve the developer's ACTUAL API key — quietly billing
# nothing, but making the key material a test input, putting it one assertion message away from a
# terminal, and making the pinned fixture-key tests pass or fail on whether this box happens to have
# a key configured. Pointed at a path inside the temp state root that is never created, so every read
# is the "no file" case and the startup-pop fallback governs, exactly as before the live source
# existed. Both spellings, because keysource accepts both. Import-time (collection is floored too)
# plus a per-test re-assert below, on the same reasoning as the manager port.
_NO_SERVICE_ENV = os.path.join(os.environ["XDG_STATE_HOME"], "no-such-service.env")
os.environ["ROMP_SERVICE_ENV_FILE"] = _NO_SERVICE_ENV
os.environ["ROMP_SERVICE_ENV"] = _NO_SERVICE_ENV
# A runtime provider can also be selected directly from the manager's environment. Remove its
# inherited reference before module loading, so an auth test cannot resolve a developer's vault
# merely because the isolated service.env is absent. Tests set synthetic references explicitly.
os.environ.pop("ROMP_API_KEY_REF", None)
# Every shell under a romp-managed session inherits ROMP_SUPERVISED=1 from the kernel (the service
# unit exports it), and keysource gives that variable authority: a supervised manager reads the env
# file only and ignores a startup key. Twenty-five tests that stage a startup key went red when the
# suite ran from inside a romp session while CI stayed green (review find, 2026-09-05). The floor is
# the unsupervised case; a test that wants supervision sets the variable itself.
os.environ.pop("ROMP_SUPERVISED", None)
# No test may read the REAL Claude Code settings (2026-09-07): keysource.cli_self_auth() reads
# $CLAUDE_CONFIG_DIR (else ~/.claude)/settings.json and the managed-settings files for an
# `apiKeyHelper`, and a keyed launch with no romp key source now rides that helper instead of
# refusing — so on a developer box that bills through a helper, every "refuses to launch" test would
# have launched. An empty private dir under the temp state root: the "no helper" case, exactly what
# CI has. Tests that want a helper write their own settings.json under their own CLAUDE_CONFIG_DIR.
_NO_CLAUDE_CONFIG = os.path.join(os.environ["XDG_STATE_HOME"], "no-claude-config")
os.makedirs(_NO_CLAUDE_CONFIG, exist_ok=True)
os.environ["CLAUDE_CONFIG_DIR"] = _NO_CLAUDE_CONFIG
# Every `op read` resolves afresh under test (2026-09-07): keysource reuses a key it resolved within
# ROMP_OP_REUSE_S seconds (default 60) so a busy board issues one read per window, and the suite's
# call-count assertions were written for one read per operation. The floor is 0; the reuse tests set
# their own window.
os.environ["ROMP_OP_REUSE_S"] = "0"


def _reset_keysource_state():
    """keysource remembers which path selected which source for the PROCESS (that is the resurrection
    guard); under one pytest process that memory would leak between test modules. Every loaded copy of
    the module (each SourceFileLoader name is its own module object) is reset."""
    import sys
    for name, m in list(sys.modules.items()):
        if "keysource" in name and hasattr(m, "_AUTHORITATIVE_PATHS"):
            m._AUTHORITATIVE_PATHS.clear()
            getattr(m, "_ENV_PROVIDER_PATHS", set()).clear()
            m._CACHE = ((), "")
            # the 2026-09-07 additions: the resolved-key memo and in-flight table, the health record,
            # the once-per-process notices, the settings stat cache, and the managed-settings paths
            # (floored to none: a developer box's /etc file must not decide a test)
            getattr(m, "_RESOLVED", {}).clear()
            getattr(m, "_RESOLVING", {}).clear()
            if isinstance(getattr(m, "_HEALTH", None), dict):
                m._HEALTH.update({"kind": "", "sourceFp": "", "lastOkT": 0.0, "lastFailT": 0.0, "note": ""})
            for flag in ("_NO_OP_CRED_SAID", "_OP_CRED_SEEN"):
                if hasattr(m, flag):
                    setattr(m, flag, False)
            getattr(m, "_CLI_SETTINGS_CACHE", {}).clear()
            if hasattr(m, "CLI_MANAGED_SETTINGS"):
                m.CLI_MANAGED_SETTINGS = ()
        if "judge" in name and isinstance(getattr(m, "_CLI_SELF_AUTH_SAID", None), set):
            m._CLI_SELF_AUTH_SAID.clear()


@pytest.fixture(autouse=True)
def _no_real_service_env():
    """Re-asserted, not defaulted: a module-level write in one test file executes during collection
    and would otherwise hold for the whole run. A test that needs its own env file points the vars at
    a temp path in setUp, which runs AFTER this fixture (pytest fills fixtures in the item's setup
    phase, before TestCase.run calls setUp) — so per-test intent still wins."""
    for var in ("ROMP_SERVICE_ENV_FILE", "ROMP_SERVICE_ENV"):
        os.environ[var] = _NO_SERVICE_ENV
    os.environ.pop("ROMP_API_KEY_REF", None)
    os.environ.pop("ROMP_SUPERVISED", None)
    os.environ["CLAUDE_CONFIG_DIR"] = _NO_CLAUDE_CONFIG    # re-asserted per test, same reasoning as above
    os.environ["ROMP_OP_REUSE_S"] = "0"
    _reset_keysource_state()
    yield


@pytest.fixture(autouse=True)
def _dead_manager_port():
    """The import-time poison above covers collection, but a module-level env write in a test file
    ALSO executes during collection — so one module's write (or pop) would otherwise hold for the
    entire run phase, erasing the floor for every test after it. Re-assert per test: no
    module-level write can outlive collection against this."""
    os.environ["ROMP_MANAGER_PORT"] = "1"
    yield


# No test may reach the REAL `claude` CLI (2026-08-12): _judge_claude_bin honors ROMP_CLAUDE_BIN
# first, so this floors every judge call a test forgot to stub at /bin/false — empty stdout, the
# dead-CLI row, byte-for-byte what a claude-less CI runner produces. Found when an unstubbed
# _judge_run in the kernel suite exec'd the live CLI on a dev machine: run alone it made a real
# (billed!) model call and passed; in the full suite the process env's key had already been claimed
# by an sdk-backend construction, the live CLI refused "Not logged in", and the judge-auth latch
# that refusal now correctly feeds floored the synthetic session's cards — 25 stays-in-Working
# tests red locally, green on CI, purely machine-dependent. Tests that assert _judge_claude_bin's
# own resolution pop this var themselves (test_judge.py), as they always had to.
os.environ["ROMP_CLAUDE_BIN"] = "/bin/false"

# No test kernel may fetch the Models API (2026-09-02): the kernel's lazy _sdk() build (_sdk_locked)
# fires the T222 catalog refresh, `_refresh_model_catalog("boot")` — an async GET to
# api.anthropic.com on any credential the process carries: the manager-env key
# sdk_backend.work_api_key claimed, else a bare ANTHROPIC_API_KEY, else an ANTHROPIC_AUTH_TOKEN
# bearer. A DEFENSIVE floor: no test reached the network before this line (checked, not assumed —
# the one in-process _sdk() driver, test_kernel_headless_ops' SdkSingleFlight, runs the refresh
# inside the test process with the module loader mocked, and it stopped only because the mocked
# module's work_api_key handed http.client a credential it rejects before a socket opens), but any
# in-process _sdk() call is one exported key away from a real request no test asserts on, on a key
# the test never chose. The kernel-SPAWNING tests floor it in their subprocess env
# (test_gear_select_matrix, test_ship_reship, test_awaiting_box_sync); this floors every test,
# whatever the developer's shell exports.
# Set, not setdefault: "off" is the only value the switch recognises, so no outer intent is being
# overridden. The catalog suite unsets the var inside its own tests — FetchAndFallback pops it in
# setUp to drive the fetch against a local fake server; StalenessEvent and ModelsRoute set it in
# setUp and pop it in tearDown — leaving it absent for every test after that module in a serial
# run; hence the per-test re-assert below, on the same reasoning as the manager-port one
# (tests/test_model_catalog_floor.py pins both). Those pops still win inside their own tests:
# pytest fills every fixture, autouse included, in the item's setup phase, before runtest hands
# the case to TestCase.run(), which is what calls setUp.
os.environ["ROMP_MODEL_CATALOG"] = "off"


@pytest.fixture(autouse=True)
def _no_model_catalog_fetch():
    os.environ["ROMP_MODEL_CATALOG"] = "off"
    yield


# No test may reach the REAL `systemd-run` (2026-09-05): constructing the SDK backend decides once
# whether to spawn CLIs inside per-session transient scopes (sdk_backend.cli_scope_supported), and
# that verdict defaults to ON under the supervised service — ROMP_SUPERVISED=1 is inherited by every
# tool shell of a session running on a self-hosted romp, so a suite run from one would probe the
# live user manager at every backend construction and route every _options() through the wrapper.
# Floored to the explicit off value; the truth-table tests pass their own environ and are unaffected.
# Per-test re-assert below, on the same reasoning as the manager-port floor.
os.environ["ROMP_CLI_SCOPE"] = "0"


@pytest.fixture(autouse=True)
def _no_cli_scope():
    os.environ["ROMP_CLI_SCOPE"] = "0"
    yield


# No test may reach the machine's REAL tmux server (2026-09-06): keysource.claim_op_env scrubs the tmux
# server's globals the moment romp becomes the op consumer, which any test that configures a reference
# and constructs the SDK backend (or resolves a key) does — and on a developer's box that `tmux
# set-environment -gu` would land on the live server every session runs in. The same private socket
# directory the bats suites use (tests/tmux-private.bash): tmux puts every socket, `-L` ones included,
# under $TMUX_TMPDIR/tmux-<uid>/, and the directory must exist or tmux 3.4 silently falls back to the
# default. No server ever exists there, so a scrub from a test exits with "no server running".
os.environ["TMUX_TMPDIR"] = tempfile.mkdtemp(prefix="romp-tests-tmux-")
os.environ.pop("TMUX", None)
os.environ.pop("ROMP_TMUX_SOCKET", None)


@pytest.fixture(autouse=True)
def _no_live_tmux_server():
    os.environ["TMUX_TMPDIR"] = _TMUX_PRIVATE
    yield


_TMUX_PRIVATE = os.environ["TMUX_TMPDIR"]


@pytest.fixture(autouse=True)
def _stub_place_llm(monkeypatch):
    """Card-first placer floor (2026-07-08): every loaded romp-judge instance gets a no-op place_llm so
    no test can reach a real `claude -p` subprocess through _card_route_subs (a plan test whose mocked
    sub lands on a card with open sub-goals would otherwise fire the real second call). Placer tests
    override jd.place_llm in-body; monkeypatch restores whatever was there after each test."""
    seen = set()
    for m in list(sys.modules.values()):
        for j in (m, getattr(m, "jd", None)):
            if j is not None and id(j) not in seen and getattr(j, "_card_route_subs", None) is not None:
                seen.add(id(j))
                monkeypatch.setattr(j, "place_llm", lambda *a, **k: "")
    yield
