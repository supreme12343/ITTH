const http = require('node:http');
const path = require('node:path');
const { spawn } = require('node:child_process');

const DEV_URL = 'http://127.0.0.1:5173';
const stopping = new WeakSet();

function isCampusSuiteDevResponse(statusCode, headers, body) {
  if (statusCode !== 200 || !/^text\/html\b/i.test(headers['content-type'] || '')) return false;
  return /<title>\s*Campus Suite\s*<\/title>/i.test(body)
    && /<div\s+id=["']root["']/i.test(body)
    && /<script\b[^>]*src=["'](?:\.\/)?\/?src\/main\.tsx["']/i.test(body);
}

function waitForDevServer(child, {
  url = DEV_URL,
  timeoutMs = 30000,
  requestTimeoutMs = 1500,
  retryDelayMs = 300,
  get = http.get,
} = {}) {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + timeoutMs;
    let settled = false;
    let retryTimer;
    let request;

    const finish = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(retryTimer);
      child.removeListener('exit', onExit);
      child.removeListener('error', onError);
      if (request && !request.destroyed) request.destroy();
      if (error) reject(error);
      else resolve();
    };
    const onExit = () => finish(new Error('The Campus Suite development server stopped.'));
    const onError = () => finish(new Error('Could not start the Campus Suite development server.'));
    child.once('exit', onExit);
    child.once('error', onError);

    const retry = () => {
      if (settled) return;
      if (child.exitCode !== null) return onExit();
      if (Date.now() >= deadline) return finish(new Error('Campus Suite did not start in time.'));
      retryTimer = setTimeout(check, retryDelayMs);
    };

    const check = () => {
      if (settled) return;
      if (child.exitCode !== null) return onExit();
      if (Date.now() >= deadline) return finish(new Error('Campus Suite did not start in time.'));
      try {
        request = get(url, (response) => {
          let body = '';
          response.setEncoding?.('utf8');
          response.on('data', (chunk) => {
            if (body.length < 128_000) body += chunk;
          });
          response.on('error', retry);
          response.on('end', () => {
            if (isCampusSuiteDevResponse(response.statusCode, response.headers || {}, body)) finish();
            else retry();
          });
          response.resume?.();
        });
        request.setTimeout?.(requestTimeoutMs, () => request.destroy(new Error('Dev server request timed out.')));
        request.on('error', retry);
      } catch {
        retry();
      }
    };
    check();
  });
}

function startDevServer({
  appRoot,
  projectRoot,
  env = process.env,
  platform = process.platform,
  spawnProcess = spawn,
}) {
  const node = env.npm_node_execpath || env.CAMPUS_NODE || 'node';
  const viteCli = path.join(appRoot, 'node_modules', 'vite', 'bin', 'vite.js');
  const viteConfig = path.join(projectRoot, 'campus ui components', 'vite.config.ts');
  return spawnProcess(node, [viteCli, '--config', viteConfig, '--host', '127.0.0.1'], {
    cwd: appRoot,
    env,
    shell: false,
    detached: platform !== 'win32',
    windowsHide: true,
    stdio: 'ignore',
  });
}

function stopDevServer(child, {
  platform = process.platform,
  spawnProcess = spawn,
  killProcess = process.kill,
} = {}) {
  if (!child || child.exitCode !== null || stopping.has(child)) return;
  stopping.add(child);

  if (platform === 'win32') {
    let treeKiller;
    try {
      treeKiller = spawnProcess('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], {
        windowsHide: true,
        stdio: 'ignore',
        shell: false,
      });
    } catch {
      try { child.kill(); } catch { /* process already exited */ }
      return;
    }
    treeKiller.once('error', () => {
      if (child.exitCode === null) child.kill();
    });
    treeKiller.once('exit', (code) => {
      if (code !== 0 && child.exitCode === null) child.kill();
    });
    return;
  }

  try {
    killProcess(-child.pid, 'SIGTERM');
  } catch {
    try { child.kill('SIGTERM'); } catch { /* process already exited */ }
  }
  const forceTimer = setTimeout(() => {
    if (child.exitCode !== null) return;
    try { killProcess(-child.pid, 'SIGKILL'); } catch {
      try { child.kill('SIGKILL'); } catch { /* process already exited */ }
    }
  }, 2000);
  child.once('exit', () => clearTimeout(forceTimer));
}

module.exports = { DEV_URL, isCampusSuiteDevResponse, startDevServer, stopDevServer, waitForDevServer };
