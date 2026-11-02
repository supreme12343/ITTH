const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');

function collectFiles(entry, files) {
  if (!fs.existsSync(entry)) return;
  const stat = fs.statSync(entry);
  if (stat.isDirectory()) {
    for (const name of fs.readdirSync(entry).sort()) collectFiles(path.join(entry, name), files);
  } else {
    files.push(entry);
  }
}

function uiFingerprint(appRoot, projectRoot) {
  const sourceFiles = [];
  for (const entry of [
    path.join(projectRoot, 'campus ui components', 'src'),
    path.join(projectRoot, 'campus ui components', 'public'),
    path.join(projectRoot, 'campus ui components', 'index.html'),
    path.join(projectRoot, 'campus ui components', 'vite.config.ts'),
    path.join(projectRoot, 'campus ui components', 'tsconfig.json'),
    path.join(projectRoot, 'campus ui components', 'tsconfig.node.json'),
    path.join(appRoot, 'package.json'),
    path.join(appRoot, 'package-lock.json'),
  ]) collectFiles(entry, sourceFiles);

  const digest = crypto.createHash('sha256');
  for (const file of sourceFiles.sort()) {
    digest.update(path.relative(projectRoot, file).replaceAll(path.sep, '/'));
    digest.update(fs.readFileSync(file));
  }
  return digest.digest('hex');
}

function referencedUiAssets(appRoot) {
  const dist = path.join(appRoot, 'dist');
  const indexPath = path.join(dist, 'index.html');
  if (!fs.existsSync(indexPath)) return null;
  let html;
  try { html = fs.readFileSync(indexPath, 'utf8'); } catch { return null; }

  const assets = [];
  let scriptCount = 0;
  for (const match of html.matchAll(/<(script|link)\b([^>]*)>/gi)) {
    const tagName = match[1].toLowerCase();
    const attributes = new Map();
    for (const attribute of match[2].matchAll(/([\w:-]+)\s*=\s*(["'])(.*?)\2/gi)) {
      attributes.set(attribute[1].toLowerCase(), attribute[3]);
    }

    const reference = tagName === 'script' ? attributes.get('src') : attributes.get('href');
    if (!reference || /^(?:[a-z][a-z\d+.-]*:|\/\/|#)/i.test(reference)) continue;
    const relation = (attributes.get('rel') || '').toLowerCase().split(/\s+/);
    const isScript = tagName === 'script';
    const isStyleOrModule = tagName === 'link'
      && (relation.includes('stylesheet') || relation.includes('modulepreload')
        || attributes.get('as') === 'script' || attributes.get('as') === 'style');
    if (!isScript && !isStyleOrModule) continue;

    const cleanReference = decodeURIComponent(reference.split(/[?#]/, 1)[0]);
    const localPath = cleanReference.startsWith('/') ? cleanReference.slice(1) : cleanReference;
    const resolved = path.resolve(dist, localPath);
    if (resolved !== dist && !resolved.startsWith(dist + path.sep)) return null;
    if (isScript) scriptCount += 1;
    assets.push(resolved);
  }
  if (scriptCount === 0 || assets.length === 0) return null;
  return assets;
}

function hasCurrentUiBuild(appRoot, fingerprint) {
  const dist = path.join(appRoot, 'dist');
  const marker = path.join(dist, '.campus-ui-build.json');
  if (!fs.existsSync(path.join(dist, 'index.html')) || !fs.existsSync(marker)) return false;
  try {
    const assets = referencedUiAssets(appRoot);
    return assets !== null
      && assets.every((asset) => fs.existsSync(asset) && fs.statSync(asset).isFile())
      && JSON.parse(fs.readFileSync(marker, 'utf8')).fingerprint === fingerprint;
  } catch {
    return false;
  }
}

function saveUiBuildFingerprint(appRoot, fingerprint) {
  const marker = path.join(appRoot, 'dist', '.campus-ui-build.json');
  const temporary = `${marker}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify({ fingerprint }), 'utf8');
  fs.renameSync(temporary, marker);
}

function ensureUiBuild(appRoot, projectRoot, runBuild) {
  const fingerprint = uiFingerprint(appRoot, projectRoot);
  if (hasCurrentUiBuild(appRoot, fingerprint)) return false;
  const result = runBuild();
  if (result.status !== 0) {
    throw result.error || new Error(`Campus Suite UI build failed with exit code ${result.status}.`);
  }
  const assets = referencedUiAssets(appRoot);
  if (assets === null || !assets.every((asset) => fs.existsSync(asset) && fs.statSync(asset).isFile())) {
    throw new Error('Campus Suite UI build completed without its referenced JavaScript and stylesheet assets.');
  }
  saveUiBuildFingerprint(appRoot, fingerprint);
  return true;
}

function main() {
  const appRoot = path.resolve(__dirname, '..');
  const projectRoot = path.resolve(appRoot, '..');
  const npmExecPath = process.env.npm_execpath;

  const built = ensureUiBuild(appRoot, projectRoot, () => {
    console.log('CAMPUS_UI_BUILD_REQUIRED');
    const command = npmExecPath ? process.execPath : (process.platform === 'win32' ? 'npm.cmd' : 'npm');
    const args = npmExecPath ? [npmExecPath, 'run', 'build'] : ['run', 'build'];
    return spawnSync(command, args, {
      cwd: appRoot, env: process.env, stdio: 'inherit', windowsHide: true,
      shell: !npmExecPath && process.platform === 'win32',
    });
  });
  console.log(built ? 'CAMPUS_UI_BUILD_READY' : 'CAMPUS_UI_CACHE_HIT');
  if (process.argv.includes('--prepare-only')) return;
  console.log('CAMPUS_UI_LAUNCHING');

  const electronPath = require('electron');
  const child = spawn(electronPath, [path.join(__dirname, 'main.cjs')], {
    cwd: appRoot,
    env: process.env,
    stdio: 'inherit',
    windowsHide: false,
  });
  child.once('error', (error) => {
    console.error('Campus Suite could not start Electron:', error);
    process.exitCode = 1;
  });
  child.once('exit', (code, signal) => {
    process.exitCode = code ?? (signal ? 1 : 0);
  });
}

if (require.main === module) {
  try {
    main();
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}

module.exports = { ensureUiBuild, hasCurrentUiBuild, referencedUiAssets, uiFingerprint };
