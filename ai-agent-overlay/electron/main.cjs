const { app, BrowserWindow, ipcMain, shell } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { startDevServer, stopDevServer, waitForDevServer } = require('./dev-server.cjs');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const APP_ROOT = path.resolve(__dirname, '..');
const DEV_URL = 'http://127.0.0.1:5173';
const pythonCommand = process.env.CAMPUS_PYTHON || 'python';
const GUI_APP_IDS = new Set(['attendance', 'agents', 'classroom']);

function findPythonwCommand() {
  if (process.env.CAMPUS_PYTHONW) return process.env.CAMPUS_PYTHONW;
  const match = pythonCommand.match(/^(.*[\\/])?python(?:\.exe)?$/i);
  if (match) {
    const sibling = `${match[1] || ''}pythonw.exe`;
    if (path.isAbsolute(sibling) && fs.existsSync(sibling)) return sibling;
    if (!path.isAbsolute(sibling)) {
      for (const directory of (process.env.PATH || '').split(path.delimiter)) {
        const candidate = path.join(directory, sibling);
        if (fs.existsSync(candidate)) return candidate;
      }
    }
  }
  return pythonCommand;
}
const pythonwCommand = findPythonwCommand();

const catalog = [
  { id: 'attendance', name: 'Attendance system', category: 'Teach', detail: 'Mark attendance with face recognition', file: 'Test attendence system/Test attendece system.py' },
  { id: 'scanner', name: 'GitHub secret scanner', category: 'Create', detail: 'Spot common credentials before publishing', file: 'python_tools/secret_scanner.py' },
  { id: 'agents', name: 'AI agents', category: 'Create', detail: 'Get help from your AI team', file: 'campus_agent_desktop.py' },
  { id: 'classroom', name: 'Smart classroom', category: 'Study', detail: 'Lessons, study materials and AI help', file: 'maic_desktop.py' },
  { id: 'calculator', name: 'Calculator', category: 'Study', detail: 'A quick everyday calculator', file: 'calculator.py' },
  { id: 'resume', name: 'Resume builder', category: 'Create', detail: 'Build a polished first resume', file: 'resume builder.py' },
  { id: 'grades', name: 'Grade calculator', category: 'Study', detail: 'Track scores and target grades', file: 'grade_calculator.py' },
  { id: 'timer', name: 'Study timer', category: 'Study', detail: 'Focus sessions and short breaks', file: 'Study timer.py' },
  { id: 'translator', name: 'OCR + translator', category: 'Study', detail: 'Read and translate text from images', file: 'image extracter.py' },
  { id: 'presenter', name: 'Smart PPT remote', category: 'Teach', detail: 'Control slides with voice and vision', file: 'pptcontroller.py' },
  { id: 'roblox', name: 'Roblox', category: 'Play', detail: 'Take a break and play', external: true },
  { id: 'resume-match', name: 'Resume matcher', category: 'Create', detail: 'Compare a resume with a job description', file: 'python_tools/resume_matcher.py' },
  { id: 'job-tracker', name: 'Job application tracker', category: 'Create', detail: 'Keep a local record of applications', file: 'python_tools/job_tracker.py' },
  { id: 'interview-practice', name: 'Interview practice', category: 'Create', detail: 'Practice questions for your next interview', file: 'python_tools/interview_practice.py' },
  { id: 'duplicate-finder', name: 'Duplicate finder', category: 'Create', detail: 'Find identical files without deleting them', file: 'python_tools/duplicate_finder.py' },
  { id: 'csv-cleaner', name: 'CSV cleaner', category: 'Create', detail: 'Clean and deduplicate CSV data', file: 'python_tools/csv_cleaner.py' },
  { id: 'json-helper', name: 'JSON helper', category: 'Create', detail: 'Validate and format JSON files', file: 'python_tools/json_helper.py' },
  { id: 'backup-snapshot', name: 'Folder backup', category: 'Create', detail: 'Create a dated backup of a folder', file: 'python_tools/backup_snapshot.py' },
  { id: 'api-tester', name: 'REST API tester', category: 'Create', detail: 'Send requests and inspect API responses', file: 'python_tools/api_tester.py' },
  { id: 'repo-readiness', name: 'Project readiness check', category: 'Create', detail: 'Review a project before sharing it', file: 'python_tools/repo_readiness.py' },
  { id: 'teacher-lesson-planner', name: 'Lesson plan builder', category: 'Teach', detail: 'Create a lesson draft from learning goals', file: 'teacher_tools/lesson_planner.py' },
  { id: 'teacher-worksheet', name: 'Worksheet & exit ticket', category: 'Teach', detail: 'Make class practice with an answer guide', file: 'teacher_tools/worksheet_builder.py' },
  { id: 'teacher-rubric', name: 'Rubric builder', category: 'Teach', detail: 'Draft a clear assignment scoring rubric', file: 'teacher_tools/rubric_builder.py' },
  { id: 'teacher-differentiation', name: 'Differentiation planner', category: 'Teach', detail: 'Plan practical supports and extensions', file: 'teacher_tools/differentiation_planner.py' },
  { id: 'teacher-seating', name: 'Seating chart maker', category: 'Teach', detail: 'Create a randomized or alphabetical layout', file: 'teacher_tools/seating_chart.py' },
  { id: 'teacher-groups', name: 'Balanced group maker', category: 'Teach', detail: 'Distribute students into mixed groups', file: 'teacher_tools/group_maker.py' },
  { id: 'teacher-rotations', name: 'Station rotation planner', category: 'Teach', detail: 'Schedule classroom centers and transitions', file: 'teacher_tools/rotation_planner.py' },
  { id: 'teacher-conferences', name: 'Family conference notes', category: 'Teach', detail: 'Prepare a strengths-based meeting agenda', file: 'teacher_tools/conference_planner.py' },
  { id: 'teacher-participation', name: 'Participation log', category: 'Teach', detail: 'Record and review local class participation', file: 'teacher_tools/participation_tracker.py' },
  { id: 'teacher-assignments', name: 'Assignment tracker', category: 'Teach', detail: 'Track class assignments and due dates', file: 'teacher_tools/assignment_tracker.py' },
  { id: 'learning-arcade', name: 'Learning Arcade', category: 'Play', detail: 'Five short games for math, science, history and logic', file: 'teacher_tools/learning_arcade.py' },
  { id: 'source-ledger', name: 'Research Source Ledger', category: 'Study', detail: 'Organize claims, evidence, sources and limitations', file: 'python_tools/source_ledger.py' },
  { id: 'dataset-guide', name: 'Dataset Field Guide', category: 'Study', detail: 'Inspect CSV columns, gaps, ranges and common values', file: 'python_tools/dataset_field_guide.py' },
  { id: 'regex-workbench', name: 'Regex Workbench', category: 'Create', detail: 'Build and test text patterns against sample text', file: 'python_tools/regex_workbench.py' },
  { id: 'exit-ticket-analyzer', name: 'Exit-Ticket Analyzer', category: 'Teach', detail: 'Find common misconceptions from response patterns', file: 'teacher_tools/exit_ticket_analyzer.py' },
];

function listApps() {
  return catalog.map((item) => ({
    ...item,
    available: item.external || fs.existsSync(path.join(PROJECT_ROOT, item.file)),
  }));
}

ipcMain.handle('campus:list-apps', () => listApps());
const THEME_PREFERENCE = path.join(PROJECT_ROOT, 'theme-preference.json');
function readThemePreference() {
  try {
    const preference = JSON.parse(fs.readFileSync(THEME_PREFERENCE, 'utf8'));
    return preference.mode === 'dark' ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}
ipcMain.handle('campus:get-theme', () => readThemePreference());
ipcMain.handle('campus:set-theme', (_event, mode) => {
  if (mode !== 'light' && mode !== 'dark') return readThemePreference();
  try {
    const temporary = THEME_PREFERENCE + '.tmp';
    fs.writeFileSync(temporary, JSON.stringify({ mode }, null, 2) + '\n', 'utf8');
    fs.renameSync(temporary, THEME_PREFERENCE);
  }
  catch (error) { console.error('Could not save Campus Suite theme preference:', error); }
  return mode;
});

const LOG_DIR = path.join(PROJECT_ROOT, 'logs');

function openToolLog(item) {
  try {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    const file = path.join(LOG_DIR, `tool-${item.id}.log`);
    const fd = fs.openSync(file, 'a');
    fs.writeSync(fd, `\n=== ${new Date().toISOString()} launching ${item.file} ===\n`);
    return { fd, path: file };
  } catch {
    return null;
  }
}

function closeToolLog(logFile) {
  // Only the launcher's handle; the tool keeps its own and goes on writing.
  if (logFile === null) return;
  try { fs.closeSync(logFile.fd); } catch { /* already closed */ }
}

function describeToolFailure(logFile) {
  if (logFile === null) return '';
  let text = '';
  try { text = fs.readFileSync(logFile.path, 'utf8'); } catch { return ''; }
  const lines = text.split(/\r?\n/).filter((line) => line.trim() !== '');
  const blame = lines.reverse().find((line) => /Error|Exception|Traceback/i.test(line));
  const where = `logs/${path.basename(logFile.path)}`;
  return blame ? ` ${blame.trim()} (full output in ${where})` : ` See ${where}.`;
}

function reportToolCrash(item, code, logFile) {
  // The renderer was told the tool started, because it did; it died afterwards.
  const message = `${item.name} closed immediately (exit code ${code}).${describeToolFailure(logFile)}`;
  console.error(message);
  if (typeof BrowserWindow.getAllWindows !== 'function') return;
  for (const window of BrowserWindow.getAllWindows()) {
    window.webContents.send('campus:tool-failed', { id: item.id, name: item.name, error: message });
  }
}

function recordToolActivity(item) {
  const activityCode = `from unified_memory import record_activity; record_activity("campus-suite", "launched-tool", {"tool": ${JSON.stringify(item.file || item.id)}})`;
  const activity = spawn(pythonCommand, ['-c', activityCode], {
    cwd: PROJECT_ROOT,
    windowsHide: true,
    detached: true,
    stdio: 'ignore',
  });
  activity.once('error', () => {});
  activity.unref();
}

ipcMain.handle('campus:launch-app', async (_event, appId) => {
  const item = catalog.find((entry) => entry.id === appId);
  if (!item) return { ok: false, error: 'That tool is not available in Campus Suite.' };
  if (item.external) {
    await shell.openExternal('https://www.roblox.com');
    return { ok: true, external: true };
  }

  const filePath = path.resolve(PROJECT_ROOT, item.file);
  if (!filePath.startsWith(PROJECT_ROOT + path.sep) || !fs.existsSync(filePath)) {
    return { ok: false, error: `${item.name} is not installed yet.` };
  }

  // The agent desktop embeds Edge through pywebview. Launch it with the normal
  // Python executable so its native window is created; pythonw leaves this
  // process running in the background without showing the Agent Workspace.
  const command = item.id === 'agents'
    ? pythonCommand
    : GUI_APP_IDS.has(item.id) ? pythonwCommand : pythonCommand;
  const workingDirectory = item.id === 'attendance' ? path.dirname(filePath) : PROJECT_ROOT;

  const logFile = openToolLog(item);

  return new Promise((resolve) => {
    const child = spawn(command, [filePath], {
      cwd: workingDirectory,
      windowsHide: true,
      detached: true,
      // Keep the tool's own output. This used to be 'ignore', so a tool that died
      // on a missing import vanished without a trace and Campus Suite still
      // reported success — the window simply never appeared.
      stdio: logFile === null ? 'ignore' : ['ignore', logFile.fd, logFile.fd],
    });

    let settled = false;
    const settle = (value) => {
      if (settled) return;
      settled = true;
      closeToolLog(logFile);
      resolve(value);
    };

    child.once('error', (error) => settle({ ok: false, error: `Could not open ${item.name}: ${error.message}` }));
    // A tool that dies on a missing import exits just after it starts — long after
    // we have answered the renderer — so push the reason to the window instead.
    child.once('exit', (code) => {
      if (code === 0) return settle({ ok: true, pid: child.pid });
      if (settled) return reportToolCrash(item, code, logFile);
      settle({ ok: false, error: `${item.name} closed immediately (exit code ${code}).${describeToolFailure(logFile)}` });
    });
    child.once('spawn', () => {
      child.unref();
      recordToolActivity(item);
      settle({ ok: true, pid: child.pid });
    });
  });
});

ipcMain.handle('campus:window-action', (event, action) => {
  const window = BrowserWindow.fromWebContents(event.sender);
  if (!window) return;
  if (action === 'minimize') window.minimize();
  if (action === 'toggle-maximize') window.isMaximized() ? window.unmaximize() : window.maximize();
  if (action === 'close') window.close();
});

let devServer;
async function createWindow() {
  const window = new BrowserWindow({
    width: 1440,
    height: 940,
    minWidth: 880,
    minHeight: 640,
    backgroundColor: readThemePreference() === 'dark' ? '#111915' : '#f4f5f1',
    title: 'Campus Suite',
    frame: false,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  window.once('closed', () => stopDevServer(devServer));

  window.once('ready-to-show', () => {
    window.show();
    console.log('CAMPUS_SUITE_READY');
  });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  if (process.argv.includes('--dev')) {
    devServer = startDevServer({ appRoot: APP_ROOT, projectRoot: PROJECT_ROOT });
    devServer.once('error', (error) => console.error('Could not start Vite:', error));
    await waitForDevServer(devServer, { url: DEV_URL });
    await window.loadURL(DEV_URL);
  } else {
    await window.loadFile(path.join(APP_ROOT, 'dist', 'index.html'));
  }
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  console.log('CAMPUS_SUITE_ALREADY_RUNNING');
  app.quit();
} else {
  app.whenReady().then(createWindow).catch((error) => {
    console.error('Campus Suite could not start:', error);
    stopDevServer(devServer);
    app.quit();
  });
  app.on('second-instance', () => BrowserWindow.getAllWindows()[0]?.focus());
  app.on('window-all-closed', () => app.quit());
  app.on('before-quit', () => stopDevServer(devServer));
}
