const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { ensureUiBuild } = require('./start.cjs');

test('the Campus Suite renderer build is reused until its inputs change', () => {
  const projectRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'campus-ui-cache-'));
  const appRoot = path.join(projectRoot, 'ai-agent-overlay');
  const componentDir = path.join(projectRoot, 'campus ui components');
  const sourceDir = path.join(componentDir, 'src');
  fs.mkdirSync(sourceDir, { recursive: true });
  const publicDir = path.join(componentDir, 'public');
  fs.mkdirSync(publicDir, { recursive: true });
  fs.mkdirSync(appRoot, { recursive: true });
  fs.writeFileSync(path.join(sourceDir, 'App.tsx'), 'export default function App() {}');
  fs.writeFileSync(path.join(publicDir, 'logo.svg'), '<svg/>');
  fs.writeFileSync(path.join(componentDir, 'index.html'), '<div id="root"></div>');
  fs.writeFileSync(path.join(componentDir, 'vite.config.ts'), 'export default {}');
  fs.writeFileSync(path.join(appRoot, 'package.json'), '{"name":"campus"}');
  fs.writeFileSync(path.join(appRoot, 'package-lock.json'), '{"lockfileVersion":3}');
  let buildCount = 0;
  const runBuild = () => {
    buildCount += 1;
    const dist = path.join(appRoot, 'dist');
    fs.rmSync(dist, { recursive: true, force: true });
    fs.mkdirSync(dist, { recursive: true });
    const assets = path.join(dist, 'assets');
    fs.mkdirSync(assets);
    fs.writeFileSync(path.join(dist, 'assets', 'app.js'), '/* app */');
    fs.writeFileSync(path.join(dist, 'assets', 'app.css'), '/* styles */');
    fs.writeFileSync(path.join(dist, 'index.html'), [
      '<div id="root"></div>',
      '<script type="module" src="./assets/app.js"></script>',
      '<link rel="stylesheet" href="./assets/app.css">',
    ].join('\n'));
    return { status: 0 };
  };

  try {
    assert.equal(ensureUiBuild(appRoot, projectRoot, runBuild), true);
    assert.equal(ensureUiBuild(appRoot, projectRoot, runBuild), false);
    assert.equal(buildCount, 1);

    fs.writeFileSync(path.join(sourceDir, 'App.tsx'), 'export default function App() { return null; }');
    assert.equal(ensureUiBuild(appRoot, projectRoot, runBuild), true);
    assert.equal(buildCount, 2);

    fs.rmSync(path.join(appRoot, 'dist', 'assets', 'app.js'));
    assert.equal(ensureUiBuild(appRoot, projectRoot, runBuild), true);
    assert.equal(buildCount, 3);

    fs.rmSync(path.join(appRoot, 'dist', 'assets', 'app.css'));
    assert.equal(ensureUiBuild(appRoot, projectRoot, runBuild), true);
    assert.equal(buildCount, 4);

    fs.writeFileSync(path.join(publicDir, 'logo.svg'), '<svg><title>Changed</title></svg>');
    assert.equal(ensureUiBuild(appRoot, projectRoot, runBuild), true);
    assert.equal(buildCount, 5);

    fs.writeFileSync(path.join(sourceDir, 'App.tsx'), 'export default function App() { return <div/>; }');
    const incompleteBuild = () => {
      fs.rmSync(path.join(appRoot, 'dist'), { recursive: true, force: true });
      fs.mkdirSync(path.join(appRoot, 'dist'), { recursive: true });
      fs.writeFileSync(path.join(appRoot, 'dist', 'index.html'),
        '<div id="root"></div><script src="./assets/missing.js"></script>');
      return { status: 0 };
    };
    assert.throws(() => ensureUiBuild(appRoot, projectRoot, incompleteBuild), /referenced JavaScript/);
  } finally {
    fs.rmSync(projectRoot, { recursive: true, force: true });
  }
});
