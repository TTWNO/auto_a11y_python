'use strict';

// Tests for ProcessManager's stale-mongod recovery. A previous launch (or a
// second app instance) can leave a mongod alive holding the lock on our
// dbpath; a fresh mongod then dies with DBPathInUse (exit 100) and the app
// "fails to start". cleanStaleLock must terminate that leftover so startup
// recovers — while NOT killing an unrelated process that merely reused the PID.
//
// Uses only Node's built-in test runner; the only "fixtures" are real
// throwaway child processes we spawn and a temp dbpath.

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const { ProcessManager } = require('../process-manager');

function isAlive(pid) {
  try { process.kill(pid, 0); return true; } catch { return false; }
}

function spawnSleeper() {
  // A long-lived child with its own PID we can signal.
  return spawn(process.execPath, ['-e', 'setInterval(() => {}, 1e9)'], { stdio: 'ignore' });
}

async function waitUntil(fn, timeoutMs = 5000, stepMs = 50) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (fn()) return true;
    await new Promise((r) => setTimeout(r, stepMs));
  }
  return fn();
}

function makePM(userDataDir) {
  // cleanStaleLock only reads this.settings.userDataDir for the lock paths.
  return new ProcessManager({ userDataDir });
}

function writeLock(userDataDir, content) {
  const dataDir = path.join(userDataDir, 'mongodb', 'data');
  fs.mkdirSync(dataDir, { recursive: true });
  const lockFile = path.join(dataDir, 'mongod.lock');
  fs.writeFileSync(lockFile, String(content), 'utf8');
  return lockFile;
}

test('terminatePid kills a running process', async () => {
  const child = spawnSleeper();
  const pm = makePM(os.tmpdir());
  try {
    assert.ok(isAlive(child.pid));
    await pm.terminatePid(child.pid);
    assert.strictEqual(await waitUntil(() => !isAlive(child.pid)), true, 'process should be gone');
  } finally {
    if (isAlive(child.pid)) child.kill('SIGKILL');
  }
});

test('terminatePid is a no-op for an already-dead process', async () => {
  const child = spawnSleeper();
  const pid = child.pid;
  child.kill('SIGKILL');
  await waitUntil(() => !isAlive(pid));
  const pm = makePM(os.tmpdir());
  await pm.terminatePid(pid); // must resolve without throwing
});

test('cleanStaleLock terminates a live mongod holding the dbpath and clears the lock', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'a11y-mongo-'));
  const child = spawnSleeper();
  const lockFile = writeLock(dir, child.pid);
  const pm = makePM(dir);
  pm.isMongodPid = () => true; // the lock holder is (pretend) a mongod
  try {
    await pm.cleanStaleLock();
    assert.strictEqual(
      await waitUntil(() => !isAlive(child.pid)), true,
      'stale mongod should be terminated',
    );
    assert.strictEqual(fs.readFileSync(lockFile, 'utf8').trim(), '', 'lock should be cleared');
  } finally {
    if (isAlive(child.pid)) child.kill('SIGKILL');
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('cleanStaleLock leaves a non-mongod lock holder untouched (PID reuse)', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'a11y-mongo-'));
  const child = spawnSleeper();
  writeLock(dir, child.pid);
  const pm = makePM(dir);
  pm.isMongodPid = () => false; // the PID was reused by something that isn't mongod
  try {
    await pm.cleanStaleLock();
    // Give it a beat — if it were going to kill, it would have by now.
    await new Promise((r) => setTimeout(r, 200));
    assert.ok(isAlive(child.pid), 'a non-mongod process must NOT be killed');
  } finally {
    child.kill('SIGKILL');
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('cleanStaleLock clears a lock whose owner is already dead', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'a11y-mongo-'));
  const child = spawnSleeper();
  const pid = child.pid;
  child.kill('SIGKILL');
  await waitUntil(() => !isAlive(pid));
  const lockFile = writeLock(dir, pid);
  const pm = makePM(dir);
  try {
    await pm.cleanStaleLock();
    assert.strictEqual(
      fs.readFileSync(lockFile, 'utf8').trim(), '',
      'a lock owned by a dead process should be cleared',
    );
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
