const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { startDevServer, stopDevServer, waitForDevServer } = require('./dev-server.cjs');

function stubChild() {
  const child = new EventEmitter();
  child.exitCode = null;
  child.pid = 4321;
  child.kill = () => { child.killedByFallback = true; };
  return child;
}

test('Vite readiness requires the Campus Suite app document, not any HTTP response', async () => {
  const server = http.createServer((_request, response) => {
    response.writeHead(200, { 'content-type': 'text/html' });
    response.end('<title>Campus Suite</title><div id="root"></div><script type="module" src="/src/main.tsx"></script>');
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const child = stubChild();
  try {
    await waitForDevServer(child, {
      url: `http://127.0.0.1:${server.address().port}`,
      timeoutMs: 1000,
      retryDelayMs: 10,
    });
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test('an unrelated HTTP 200 does not satisfy Vite readiness', async () => {
  const server = http.createServer((_request, response) => {
    response.writeHead(200, { 'content-type': 'text/html' });
    response.end('<title>Another application</title><div id="root"></div>');
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const child = stubChild();
  try {
    await assert.rejects(waitForDevServer(child, {
      url: `http://127.0.0.1:${server.address().port}`,
      timeoutMs: 80,
      retryDelayMs: 10,
    }), /did not start in time/);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test('Vite starts through Node directly with loopback host and no shell wrapper', () => {
  const appRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'campus-vite-launch-'));
  const projectRoot = path.dirname(appRoot);
  let launch;
  try {
    startDevServer({
      appRoot,
      projectRoot,
      env: { npm_node_execpath: 'node-runtime' },
      platform: 'win32',
      spawnProcess: (...args) => { launch = args; return stubChild(); },
    });
    assert.equal(launch[0], 'node-runtime');
    assert.ok(launch[1][0].endsWith(path.join('node_modules', 'vite', 'bin', 'vite.js')));
    assert.equal(launch[1].at(-1), '127.0.0.1');
    assert.equal(launch[2].shell, false);
    assert.equal(launch[2].detached, false);
  } finally {
    fs.rmSync(appRoot, { recursive: true, force: true });
  }
});

test('Windows dev-server cleanup kills the full child tree and falls back if taskkill fails', () => {
  const child = stubChild();
  const killer = new EventEmitter();
  let command;
  stopDevServer(child, {
    platform: 'win32',
    spawnProcess: (...args) => { command = args; return killer; },
  });
  assert.equal(command[0], 'taskkill.exe');
  assert.deepEqual(command[1], ['/PID', '4321', '/T', '/F']);
  killer.emit('exit', 1);
  assert.equal(child.killedByFallback, true);
});
