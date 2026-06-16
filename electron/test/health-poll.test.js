'use strict';

// Tests for ProcessManager.pollUrl — the launcher's "is Flask up yet?" gate.
//
// Uses only Node's built-in test runner (`node --test`); no extra deps.
// pollUrl is self-contained (http + timers), so we exercise the real
// method against a throwaway local /health server.

const { test } = require('node:test');
const assert = require('node:assert');
const http = require('http');

const { ProcessManager } = require('../process-manager');

/**
 * Spin up a throwaway server that always answers with the given JSON body
 * and status code. Resolves to { url, close }.
 */
function startHealthServer(body, statusCode = 200) {
  const server = http.createServer((req, res) => {
    res.writeHead(statusCode, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(body));
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolve({
        url: `http://127.0.0.1:${port}/health`,
        close: () => new Promise((r) => server.close(r)),
      });
    });
  });
}

test('pollUrl resolves when the server reports recovery mode', async () => {
  // Settings Recovery mode serves /health as {status:'recovery'} with
  // HTTP 503. The launcher must treat that as "server is up" and open the
  // window (which lands on /recovery/), NOT poll until timeout and show
  // "failed to start" — otherwise the recovery screen can never appear.
  const srv = await startHealthServer({ status: 'recovery' }, 503);
  const pm = new ProcessManager(null);
  try {
    const data = await pm.pollUrl(srv.url, 3000, 50);
    assert.strictEqual(data.status, 'recovery');
  } finally {
    await srv.close();
  }
});

test('pollUrl still resolves on a healthy server (regression)', async () => {
  const srv = await startHealthServer({ status: 'healthy' }, 200);
  const pm = new ProcessManager(null);
  try {
    const data = await pm.pollUrl(srv.url, 3000, 50);
    assert.strictEqual(data.status, 'healthy');
  } finally {
    await srv.close();
  }
});

test('pollUrl waits through a slow start and resolves once ready', async () => {
  // Flask's cold start can take ~30s; until it is serving, /health refuses
  // the connection or isn't answering. The launcher must keep polling and
  // resolve once the server comes up — not give up on the first miss. Here
  // the server answers connection-refused-equivalent (404, no JSON) for the
  // first ~300ms, then flips to healthy.
  let ready = false;
  const server = http.createServer((req, res) => {
    if (!ready) {
      res.writeHead(503);
      res.end('not ready');
      return;
    }
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'healthy' }));
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const { port } = server.address();
  setTimeout(() => { ready = true; }, 300);

  const pm = new ProcessManager(null);
  try {
    const data = await pm.pollUrl(`http://127.0.0.1:${port}/health`, 3000, 50);
    assert.strictEqual(data.status, 'healthy');
  } finally {
    await new Promise((r) => server.close(r));
  }
});

test('pollUrl times out on an unrecognised status', async () => {
  // A state we don't recognise as "up" (e.g. a half-initialised server)
  // must still surface as a timeout rather than opening a dead window.
  const srv = await startHealthServer({ status: 'starting' }, 503);
  const pm = new ProcessManager(null);
  try {
    await assert.rejects(
      pm.pollUrl(srv.url, 600, 50),
      /Timeout waiting for/,
    );
  } finally {
    await srv.close();
  }
});
