const assert = require('node:assert/strict');
const fs = require('node:fs');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
const { test } = require('node:test');

const projectRoot = path.resolve(__dirname, '..');
const appRoot = path.join(projectRoot, 'ai-agent-overlay');
const uiRoot = path.join(projectRoot, 'campus ui components');

test('Campus Suite TSX transforms and its category/search filters select the right tools', { timeout: 30000 }, async () => {
  const catalogSource = fs.readFileSync(path.join(appRoot, 'electron', 'main.cjs'), 'utf8');
  const ids = [...catalogSource.matchAll(/\bid: '([^']+)'/g)].map(match => match[1]);
  assert.equal(new Set(ids).size, ids.length, 'launcher tool IDs must stay unique');
  assert.match(catalogSource, /id: 'learning-arcade'.*category: 'Play'.*teacher_tools\/learning_arcade\.py/);
  assert.match(catalogSource, /id: 'source-ledger'.*category: 'Study'.*python_tools\/source_ledger\.py/);
  assert.match(catalogSource, /id: 'dataset-guide'.*category: 'Study'.*python_tools\/dataset_field_guide\.py/);
  assert.match(catalogSource, /id: 'regex-workbench'.*category: 'Create'.*python_tools\/regex_workbench\.py/);
  assert.match(catalogSource, /id: 'exit-ticket-analyzer'.*category: 'Teach'.*teacher_tools\/exit_ticket_analyzer\.py/);
  for (const relativePath of ['teacher_tools/learning_arcade.py', 'python_tools/source_ledger.py', 'python_tools/dataset_field_guide.py', 'python_tools/regex_workbench.py', 'teacher_tools/exit_ticket_analyzer.py']) {
    assert.equal(fs.existsSync(path.join(projectRoot, relativePath)), true, `${relativePath} should be launchable`);
  }

  const vitePath = require.resolve('vite', { paths: [appRoot] });
  const { createServer } = await import(pathToFileURL(vitePath));
  const vite = await createServer({
    configFile: path.join(uiRoot, 'vite.config.ts'),
    server: { middlewareMode: true },
    appType: 'custom',
    logLevel: 'error',
  });

  try {
    const appModule = await vite.transformRequest('/src/ui/App.tsx');
    assert.ok(appModule?.code.includes('What are we working on?'));
    assert.ok(appModule?.code.includes('Your campus,'));

    const { filterCampusApps } = await vite.ssrLoadModule('/src/ui/appLogic.ts');
    const apps = [
      { id: 'game', name: 'Learning Arcade', detail: 'Five study games', category: 'Play' },
      { id: 'ledger', name: 'Research Source Ledger', detail: 'Claims and evidence', category: 'Study' },
      { id: 'timer', name: 'Study timer', detail: 'Focus sessions', category: 'Study' },
    ];
    assert.deepEqual(filterCampusApps(apps, 'Play', '').map(app => app.id), ['game']);
    assert.deepEqual(filterCampusApps(apps, 'All tools', '  SOURCE ledger ').map(app => app.id), ['ledger']);
    assert.deepEqual(filterCampusApps(apps, 'Study', 'focus').map(app => app.id), ['timer']);
  } finally {
    await vite.close();
  }
});
