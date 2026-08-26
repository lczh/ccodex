// romp-manager's multi-kernel registry (plans/multi-kernel.md phase 3): kernels.json profiles are
// parsed FRESH at every consult and validated hard — a malformed entry is DROPPED with a loud error,
// never half-applied — and specEnv is the whole per-kernel isolation story (state root, Claude config
// dir, postal port, tmux socket ride the child env). fileStamp backs the --refresh stale-manager
// detection (the user 2026-07-24: a long-lived manager respawned kernels on start-time defaults the
// disk had moved past, with everything reporting success). Run: node --test tests/manager-registry.test.js
const { test } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { loadSpecs, specEnv, fileStamp, configuredPort, ensurePort, validControlToken } =
  require(path.join(__dirname, '..', 'bin', 'romp-manager'));

const MAIN = 29855, CTRL = 7432;

test('manager and ensure ports are strict bounded integers', () => {
  assert.equal(configuredPort('1024', 9999, 'TEST_PORT'), 1024);
  assert.equal(configuredPort('65535', 9999, 'TEST_PORT'), 65535);
  for (const bad of ['1', '65536', '7.5', 'nope']) {
    assert.throws(() => configuredPort(bad, 9999, 'TEST_PORT'), /integer in \[1024,65535\]/);
  }
  assert.equal(ensurePort(String(CTRL)).status, 409, 'the control listener can never become a worker');
  assert.equal(ensurePort('65536').status, 400);
  assert.equal(ensurePort('7432.0').status, 400);
});

test('manager bearer tokens require a transport-safe high-entropy shape', () => {
  assert.equal(validControlToken('a'.repeat(32)), true);
  assert.equal(validControlToken('Az_09-' + 'b'.repeat(26)), true);
  for (const bad of ['', 'x', 'a'.repeat(31), 'a'.repeat(513), 'a'.repeat(31) + '\n',
                     'a'.repeat(31) + ':']) {
    assert.equal(validControlToken(bad), false, JSON.stringify(bad));
  }
});

function withFile(content, fn) {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'romp-kernels-'));
  const f = path.join(d, 'kernels.json');
  if (content !== null) fs.writeFileSync(f, content);
  try { return fn(f); } finally { fs.rmSync(d, { recursive: true, force: true }); }
}

test('no kernels.json → just main, no errors (single-kernel default)', () => {
  withFile(null, (f) => {
    const { specs, errors } = loadSpecs(f, MAIN, CTRL);
    assert.deepEqual(specs, [{ id: 'main', port: MAIN }]);
    assert.deepEqual(errors, []);
  });
});

test('a full profile parses with every isolation field', () => {
  withFile(JSON.stringify({ kernels: [{ id: 'alice', port: 30001, postalPort: 30002,
    stateDir: '/tmp/romp-alice', claudeConfigDir: '/tmp/claude-alice', tmuxSocket: 'romp-alice' }] }), (f) => {
    const { specs, errors } = loadSpecs(f, MAIN, CTRL);
    assert.deepEqual(errors, []);
    assert.equal(specs.length, 2);
    assert.deepEqual(specs[1], { id: 'alice', port: 30001, postalPort: 30002,
      stateDir: '/tmp/romp-alice', claudeConfigDir: '/tmp/claude-alice', tmuxSocket: 'romp-alice' });
  });
});

test('unreadable JSON drops the whole file loudly and keeps main', () => {
  withFile('{not json', (f) => {
    const { specs, errors } = loadSpecs(f, MAIN, CTRL);
    assert.deepEqual(specs, [{ id: 'main', port: MAIN }]);
    assert.equal(errors.length, 1);
    assert.match(errors[0], /unreadable JSON/);
  });
});

test('duplicate ids, taken ports, and malformed fields drop per-entry with errors', () => {
  withFile(JSON.stringify({ kernels: [
    { id: 'a', port: 30001 },
    { id: 'a', port: 30002 },                       // dup id
    { id: 'b', port: 30001 },                       // dup port
    { id: 'c', port: MAIN },                        // collides with main
    { id: 'd', port: CTRL },                        // collides with the control port
    { id: 'BAD ID', port: 30003 },                  // malformed id
    { id: 'e', port: 30004, stateDir: 'relative/nope' },   // malformed stateDir
    { id: 'f', port: 30005, tmuxSocket: 'has space' },      // malformed socket
    { id: 'g', port: 30006 },                       // fine
  ] }), (f) => {
    const { specs, errors } = loadSpecs(f, MAIN, CTRL);
    assert.deepEqual(specs.map((s) => s.id), ['main', 'a', 'g']);
    assert.equal(errors.length, 7, errors.join('\n'));   // one per dropped entry above
  });
});

test('a main entry overrides only the port — the stale-env escape hatch', () => {
  withFile(JSON.stringify({ kernels: [{ id: 'main', port: 31000, stateDir: '/tmp/x' }] }), (f) => {
    const { specs } = loadSpecs(f, MAIN, CTRL);
    assert.deepEqual(specs[0], { id: 'main', port: 31000 }, 'port moves; main keeps the primary state root');
  });
});

test('specEnv carries the whole isolation story, and only what the spec sets', () => {
  const base = { PATH: '/usr/bin', HOME: '/home/u' };
  const ids = { managerPid: 42, controlPort: CTRL, managerToken: 'manager-test-token' };
  const full = specEnv({ id: 'alice', port: 30001, postalPort: 30002, stateDir: '/tmp/ra',
                         claudeConfigDir: '/tmp/ca', tmuxSocket: 'romp-alice' }, base, ids);
  assert.equal(full.ROMP_SERVE_PORT, '30001');
  assert.equal(full.ROMP_KERNEL_PORT, '30001', 'both spellings of the listen port move together');
  assert.equal(full.ROMP_POSTAL_PORT, '30002');
  assert.equal(full.ROMP_STATE_DIR, '/tmp/ra');
  assert.equal(full.CLAUDE_CONFIG_DIR, '/tmp/ca');
  assert.equal(full.ROMP_TMUX_SOCKET, 'romp-alice');
  assert.equal(full.ROMP_MANAGER_PID, '42');
  assert.equal(full.ROMP_MANAGER_TOKEN, 'manager-test-token');
  assert.equal(full.PATH, '/usr/bin', 'base env rides through');
  const bare = specEnv({ id: 'main', port: MAIN }, base, ids);
  for (const k of ['ROMP_POSTAL_PORT', 'ROMP_STATE_DIR', 'CLAUDE_CONFIG_DIR', 'ROMP_TMUX_SOCKET']) {
    assert.ok(!(k in bare), k + ' must not leak into an unscoped kernel (main keeps the process defaults)');
  }
  assert.ok(!('ROMP_STATE_DIR' in base), 'the base object is never mutated');
});

test('specEnv overwrites a stale inherited ROMP_KERNEL_PORT from the base env', () => {
  // The manager's own env may carry the PRIMARY kernel's port under the other spelling. An aux
  // kernel inheriting that copy is how one profile's sessions end up addressing another
  // profile's kernel, so the spec's port has to win under BOTH names.
  const base = { PATH: '/usr/bin', ROMP_KERNEL_PORT: '29855', ROMP_SERVE_PORT: '29855' };
  const env = specEnv({ id: 'alice', port: 30001 }, base, { managerPid: 42, controlPort: CTRL });
  assert.equal(env.ROMP_KERNEL_PORT, '30001');
  assert.equal(env.ROMP_SERVE_PORT, '30001');
});

test('fileStamp changes when the file changes — the staleness detector', () => {
  withFile('one', (f) => {
    const a = fileStamp(f);
    assert.notEqual(a, '', 'a real file stamps non-empty');
    fs.writeFileSync(f, 'two-longer');
    assert.notEqual(fileStamp(f), a, 'a rewrite moves the stamp');
    assert.equal(fileStamp(f + '.missing'), '', 'a missing file stamps empty');
  });
});

// ── rompServeBin: per-spawn runtime-generation resolution (the v1.3.18 audit's P1, redesigned
// per the r46 verification). Provenance for every test below: the r46 re-verify — reverting the
// resolution kept every suite green, so these EXECUTE it. A real tiny git repo stands in for the
// checkout; the generation lives at <gitdir>/romp-run-<sha8>/bin/romp-serve.
const { rompServeBin } = require(path.join(__dirname, '..', 'bin', 'romp-manager'));
const { execFileSync } = require('node:child_process');

function withGenRepo(fn) {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'romp-genrepo-'));
  fs.mkdirSync(path.join(d, 'bin'));
  fs.writeFileSync(path.join(d, 'bin', 'romp-serve'), '#!/bin/sh\n', { mode: 0o755 });
  execFileSync('git', ['-C', d, 'init', '-q', '-b', 'main'], { stdio: 'ignore' });
  execFileSync('git', ['-C', d, 'add', '-A'], { stdio: 'ignore' });
  execFileSync('git', ['-C', d, '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'x'],
               { stdio: 'ignore' });
  const out = (args) => execFileSync('git', ['-C', d, ...args],
                                     { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim();
  const gd = out(['rev-parse', '--absolute-git-dir']);
  const h8 = out(['rev-parse', '--short=8', 'HEAD']);
  const gen = path.join(gd, 'romp-run-' + h8, 'bin', 'romp-serve');
  fs.mkdirSync(path.dirname(gen), { recursive: true });
  fs.writeFileSync(gen, '#!/bin/sh\n', { mode: 0o755 });
  try { return fn({ d, gd, gen }); } finally { fs.rmSync(d, { recursive: true, force: true }); }
}

// rompServeBin reads process.env at call time; scope every case's env and restore after.
function withEnv(vars, fn) {
  const keys = ['ROMP_SERVE_BIN', 'ROMP_SERVE_ROOT', 'ROMP_DIR'];
  const saved = {};
  for (const k of keys) { saved[k] = process.env[k]; delete process.env[k]; }
  Object.assign(process.env, vars);
  try { return fn(); } finally {
    for (const k of keys) {
      if (saved[k] === undefined) delete process.env[k]; else process.env[k] = saved[k];
    }
  }
}

test('rompServeBin resolves the generation serve for the checkout HEAD (per spawn, via ROMP_DIR)', () => {
  // the r46 re-verify: reverting the resolution kept every suite green
  withGenRepo(({ d, gen }) => {
    assert.equal(withEnv({ ROMP_DIR: d }, rompServeBin), gen);
  });
});

test('rompServeBin: ROMP_SERVE_ROOT outranks ROMP_DIR (the snapshot-updater seam)', () => {
  // the r46 re-verify: reverting the resolution kept every suite green
  withGenRepo(({ d, gen }) => {
    assert.equal(withEnv({ ROMP_SERVE_ROOT: d, ROMP_DIR: '/nonexistent-romp-dir' }, rompServeBin),
                 gen);
  });
});

test('rompServeBin: no generation for HEAD falls to the checkout\'s own serve, exactly as before', () => {
  // the r46 re-verify: reverting the resolution kept every suite green
  withGenRepo(({ d, gd, gen }) => {
    fs.rmSync(path.join(gd, path.relative(gd, gen).split(path.sep)[0]),
              { recursive: true, force: true });
    assert.equal(withEnv({ ROMP_DIR: d }, rompServeBin), path.join(d, 'bin', 'romp-serve'));
  });
});

test('rompServeBin: a STALE generation pin is ignored — resolution proceeds to HEAD\'s generation', () => {
  // the r46 re-verify (reverting kept every suite green): managers spawned by the v1.3.18
  // env-pin wrapper carry ROMP_SERVE_BIN for life; once that generation was pruned the pin
  // fell through WITH a stale ROMP_KERNEL_BIN riding the child env — a permanent crash loop
  withGenRepo(({ d, gd, gen }) => {
    const stale = path.join(gd, 'romp-run-deadbeef', 'bin', 'romp-serve');
    assert.equal(withEnv({ ROMP_SERVE_BIN: stale, ROMP_DIR: d }, rompServeBin), gen);
  });
});

test('rompServeBin: a stale pin with NO generation at all falls to the live serve — never the pin', () => {
  // the r46 re-verify: reverting the resolution kept every suite green
  withGenRepo(({ d, gd, gen }) => {
    fs.rmSync(path.dirname(path.dirname(gen)), { recursive: true, force: true });
    const stale = path.join(gd, 'romp-run-deadbeef', 'bin', 'romp-serve');
    assert.equal(withEnv({ ROMP_SERVE_BIN: stale, ROMP_DIR: d }, rompServeBin),
                 path.join(d, 'bin', 'romp-serve'));
  });
});

test('rompServeBin: a NON-generation pin stays authoritative even when a generation exists', () => {
  // the r46 re-verify (reverting kept every suite green): only pins INTO a romp-run-* dir are
  // second-guessed; the explicit test/dev seam keeps winning unconditionally
  withGenRepo(({ d }) => {
    const pin = path.join(os.tmpdir(), 'romp-explicit-serve-' + process.pid);
    fs.writeFileSync(pin, '#!/bin/sh\n', { mode: 0o755 });
    try {
      assert.equal(withEnv({ ROMP_SERVE_BIN: pin, ROMP_DIR: d }, rompServeBin), pin);
    } finally { fs.rmSync(pin, { force: true }); }
  });
});
