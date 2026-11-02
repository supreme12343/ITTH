const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const { resolveBindHost } = require('../server-bind-host');

const html = fs.readFileSync(path.resolve(__dirname, '..', 'public', 'index.html'), 'utf8');

test('phone clients can reach Node on the LAN unless a bind override is set', () => {
  assert.equal(resolveBindHost(false), '0.0.0.0');
  assert.equal(resolveBindHost(true), '0.0.0.0');
  assert.equal(resolveBindHost(false, '127.0.0.1'), '127.0.0.1');
});

test('PPT UI explains an unavailable Socket.IO client and reconnecting server', () => {
  assert.match(html, /id="networkStatus"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.match(html, /const socketClientAvailable = typeof io === 'function';/);
  assert.match(html, /setNetworkStatus\('LIVE CONNECTION UNAVAILABLE', 'error'\)/);
  assert.match(html, /socketClientAvailable \? io\(\)/);
  assert.doesNotMatch(html, /reconnection\s*:\s*false/);

  const start = html.indexOf("        socket.on('connect', () => {");
  const end = html.indexOf("        socket.on('system_sync',", start);
  assert.ok(start >= 0 && end > start, 'connectivity handlers should precede the sync handlers');

  const handlers = new Map();
  const socket = {
    active: true,
    on: (event, callback) => handlers.set(event, callback),
    emit: () => {},
  };
  const rendered = { message: 'CONNECTING...', state: 'connecting' };
  const setNetworkStatus = (message, state) => Object.assign(rendered, { message, state });
  vm.runInNewContext(html.slice(start, end), { socket, setNetworkStatus });

  handlers.get('connect_error')();
  assert.equal(rendered.message, 'SERVER UNAVAILABLE · RETRYING');
  assert.equal(rendered.state, 'retrying');

  handlers.get('disconnect')();
  assert.equal(rendered.message, 'RECONNECTING…');
  assert.equal(rendered.state, 'retrying');

  socket.active = false;
  handlers.get('disconnect')();
  assert.equal(rendered.message, 'DISCONNECTED');
  assert.equal(rendered.state, 'error');

  handlers.get('connect')();
  assert.equal(rendered.message, 'ONLINE');
  assert.equal(rendered.state, 'online');
});
