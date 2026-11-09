import { spawnSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdirSync, readFileSync, realpathSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('.', import.meta.url));
const source = join(root, '.source');
const dist = join(root, 'dist');
if (existsSync(dist) && realpathSync(dist) !== join(realpathSync(root), 'dist')) {
  throw new Error('BMS Studio build output must be a local dist directory.');
}
// Upstream 0.7.0, including its documentation follow-up on main.
const revision = '1a96e2e3107ad29a2449d74023813b469e652ec5';
const vendors = {
  'bms-core': 'c5aea5d336432f330aa26968ac3f72ccada77eb6',
  'bms-editor': '096c821e169c1874d8d6fbf3a12fed19f0b60704',
  'bms-player': '6b5e5b1003ced36735ef4d14fd778a988ddf5c68',
};

function run(command, args, cwd, capture = false) {
  const result = spawnSync(command, args, {
    cwd,
    encoding: 'utf8',
    stdio: capture ? ['ignore', 'pipe', 'inherit'] : 'inherit',
    shell: command === 'npm' && process.platform === 'win32',
  });
  if (result.error || result.status !== 0) {
    throw new Error(command + ' ' + args[0] + ' failed: ' + (result.error ?? result.status));
  }
  return capture ? result.stdout.trim() : '';
}

function checkRevision(directory, expected) {
  const actual = run('git', ['rev-parse', 'HEAD'], directory, true);
  if (actual !== expected) {
    throw new Error('Unexpected BMS source revision in ' + directory + ': ' + actual);
  }
  run('git', ['diff', '--quiet', 'HEAD', '--'], directory);
}

if (!existsSync(source)) {
  // Fetch the reviewed commit directly so a moving branch/tag cannot change
  // a fresh build. Existing source directories are checked, never reset.
  run('git', ['init', source], root);
  run('git', ['remote', 'add', 'origin', 'https://github.com/dotoritos-kim/bms-electron-app.git'], source);
  run('git', ['fetch', '--depth', '1', 'origin', revision], source);
  run('git', ['checkout', '--detach', revision], source);
  run('git', ['submodule', 'update', '--init', '--recursive', '--depth', '1'], source);
}
checkRevision(source, revision);
for (const [name, expected] of Object.entries(vendors)) {
  checkRevision(join(source, 'vendor', name), expected);
}

const installedLock = join(source, 'node_modules', '.package-lock.json');
if (!existsSync(installedLock)) {
  // The web build uses Vite and the workspaces. Electron and install hooks are unnecessary.
  run('npm', ['ci', '--legacy-peer-deps', '--ignore-scripts', '--no-audit', '--no-fund', '--cache', '.npm-cache'], source);
}

const renderer = join(source, 'src', 'renderer');
for (const name of ['browser-api.ts', 'browser-entry.ts', 'browser.css']) {
  copyFileSync(join(root, name), join(renderer, 'bms-studio-' + name));
}

const vite = join(source, 'node_modules', 'vite', 'bin', 'vite.js');
run(process.execPath, [vite, 'build', '--config', join(root, 'vite.config.ts')], source);

// Ship the notices for the four pinned MIT projects alongside the web bundle.
const notices = join(dist, 'licenses');
mkdirSync(notices, { recursive: true });
for (const [name, path] of [
  ['bms-electron-app', source],
  ...Object.keys(vendors).map((name) => [name, join(source, 'vendor', name)]),
]) {
  const license = join(path, 'LICENSE');
  if (!readFileSync(license, 'utf8').includes('MIT License')) {
    throw new Error('Unexpected license in ' + path);
  }
  copyFileSync(license, join(notices, name + '.txt'));
}
