const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');

const handlers = new Map();
let externalOpened = '';
const projectRoot = path.resolve(__dirname, '..', '..');
const sharedPython = path.join(projectRoot, 'shared-python', 'python.exe');
const sharedPythonw = path.join(projectRoot, 'shared-python', 'pythonw.exe');
const originalCampusPython = process.env.CAMPUS_PYTHON;
const originalCampusPythonw = process.env.CAMPUS_PYTHONW;
process.env.CAMPUS_PYTHON = sharedPython;
process.env.CAMPUS_PYTHONW = sharedPythonw;
const electronMock = {
  app: {
    requestSingleInstanceLock: () => false,
    quit: () => {},
    on: () => {},
  },
  BrowserWindow: class {},
  ipcMain: { handle: (name, callback) => handlers.set(name, callback) },
  shell: { openExternal: async (url) => { externalOpened = url; } },
};
const spawned = [];
const originalLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === 'electron') return electronMock;
  if (request === 'node:child_process') {
    return {
      spawn(command, args, options) {
        const child = new EventEmitter();
        child.pid = 1234;
        child.unref = () => {};
        spawned.push({ command, args, options, child });
        setImmediate(() => child.emit('spawn'));
        return child;
      },
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};
require('./main.cjs');
Module._load = originalLoad;
if (originalCampusPython === undefined) delete process.env.CAMPUS_PYTHON;
else process.env.CAMPUS_PYTHON = originalCampusPython;
if (originalCampusPythonw === undefined) delete process.env.CAMPUS_PYTHONW;
else process.env.CAMPUS_PYTHONW = originalCampusPythonw;

const listApps = handlers.get('campus:list-apps');
const launchApp = handlers.get('campus:launch-app');

test('every Campus Suite home card has an available launch target', () => {
  const apps = listApps();
  assert.ok(apps.length > 0);
  assert.equal(new Set(apps.map((app) => app.id)).size, apps.length, 'tool ids must be unique');
  const unavailable = apps.filter((app) => !app.available).map((app) => app.name);
  assert.deepEqual(unavailable, []);
  assert.ok(apps.some((app) => app.id === 'agents' && app.file === 'campus_agent_desktop.py'));
  assert.ok(apps.some((app) => app.id === 'scanner' && app.file === 'python_tools/secret_scanner.py'));
});

test('every Campus Suite home card invokes its configured launch route', async () => {
  for (const app of listApps()) {
    const result = await launchApp({}, app.id);
    assert.equal(result.ok, true, `${app.name} did not open`);
    if (app.external) {
      assert.equal(result.external, true);
      assert.equal(externalOpened, 'https://www.roblox.com');
    } else {
      const launch = spawned.find(({ args }) => args?.[0] === path.join(projectRoot, app.file));
      assert.ok(launch, `${app.name} did not spawn ${app.file}`);
    }
  }
  assert.equal(spawned.length, listApps().filter((app) => !app.external).length * 2,
    'each local tool should launch once and record activity once');
});

test('attendance launches from its own folder with the shared Python runtime', async () => {
  const attendance = listApps().find((app) => app.id === 'attendance');
  const result = await launchApp({}, attendance.id);
  assert.equal(result.ok, true);
  const launch = spawned.find(({ args }) => args?.[0] === path.join(projectRoot, attendance.file));
  assert.ok(launch, 'attendance script was not launched');
  assert.equal(launch.options.cwd, path.dirname(path.join(projectRoot, attendance.file)));
  assert.equal(launch.command, sharedPythonw);
});

test('MAIC uses windowed Python while the pywebview agent desktop uses normal Python', async () => {
  for (const [appId, expectedRuntime] of [['classroom', sharedPythonw], ['agents', sharedPython]]) {
    const result = await launchApp({}, appId);
    assert.equal(result.ok, true);
    const app = listApps().find((entry) => entry.id === appId);
    const launch = spawned.findLast(({ args }) => args?.[0] === path.join(projectRoot, app.file));
    assert.ok(launch, `${app.name} was not launched`);
    assert.equal(launch.command, expectedRuntime);
  }
});

test('unknown tool ids fail with a user-facing error', async () => {
  const result = await launchApp({}, 'not-a-campus-tool');
  assert.equal(result.ok, false);
  assert.match(result.error, /not available/i);
});
