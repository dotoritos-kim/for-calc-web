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
const revision = '886b0f935959c120f5555736989c56f05d8cb0a0';
const vendors = {
  'bms-core': '1398d587ac233bce9ca5d4c9f4de634808360ad0',
  'bms-editor': '65a7fce2137a646a2283c237ef05c9a40fc8576f',
  'bms-player': '94152230824e2c8dda0e31f2300b2550b9d7fbc5',
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
  run('git', [
    'clone', '--depth', '1', '--branch', 'v0.6.0', '--recurse-submodules',
    '--shallow-submodules', '--', 'https://github.com/dotoritos-kim/bms-electron-app.git', '.source',
  ], root);
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
