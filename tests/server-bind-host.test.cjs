'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');

const { resolveBindHost } = require('../server-bind-host');

test('HTTP and HTTPS bind to loopback by default', () => {
  assert.equal(resolveBindHost(true), '127.0.0.1');
  assert.equal(resolveBindHost(false), '127.0.0.1');
});

test('an explicit bind host override is trimmed and honored in either mode', () => {
  assert.equal(resolveBindHost(true, ' 127.0.0.2 '), '127.0.0.2');
  assert.equal(resolveBindHost(false, '127.0.0.3'), '127.0.0.3');
  assert.equal(resolveBindHost(false, '0.0.0.0'), '0.0.0.0');
  assert.equal(resolveBindHost(true, '   '), '127.0.0.1');
});
