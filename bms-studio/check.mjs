import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { editorPatchPaths, patchEditorSource } from './editor-patches.mjs';

const source = fileURLToPath(new URL('./.source/', import.meta.url));
const require = createRequire(new URL('./.source/package.json', import.meta.url));
const ts = require('typescript');
for (const path of editorPatchPaths) {
  patchEditorSource(readFileSync(source + path, 'utf8'), source + path);
}
const configFile = ts.readConfigFile(source + 'tsconfig.web.json', ts.sys.readFile);
const config = ts.parseJsonConfigFileContent(configFile.config, ts.sys, source);
const options = {
  ...config.options, noEmit: true, incremental: false, composite: false,
  baseUrl: source,
  types: [source + 'node_modules/vite/client'],
  paths: Object.fromEntries(['core', 'editor', 'player'].map((name) =>
    ['@rhythm-archive/bms-' + name, ['vendor/bms-' + name + '/src/index.ts']])),
};
const host = ts.createCompilerHost(options);
const read = host.readFile.bind(host);
const baseline = ts.getPreEmitDiagnostics(ts.createProgram(config.fileNames, options, host));
const signature = (error) => [error.file?.fileName, error.code, error.start,
  ts.flattenDiagnosticMessageText(error.messageText, '\n')].join(':');
const existing = new Set(baseline.map(signature));
host.readFile = (path) => {
  const code = read(path);
  return code === undefined ? code : patchEditorSource(code, path);
};
const program = ts.createProgram(config.fileNames, options, host);
const diagnostics = ts.getPreEmitDiagnostics(program);
const errors = diagnostics.filter((error) => !existing.has(signature(error)));
if (errors.length) {
  console.error(ts.formatDiagnosticsWithColorAndContext(errors, {
    getCurrentDirectory: () => source, getCanonicalFileName: (path) => path, getNewLine: () => '\n',
  }));
  process.exitCode = 1;
} else {
  console.log('Pinned-source patch checks passed; no new renderer TypeScript diagnostics.');
}
if (baseline.length) {
  console.log('Existing upstream/type-environment diagnostics (' + baseline.length + '):');
  for (const error of baseline) console.log((error.file?.fileName ?? '') + ': TS' + error.code + ' ' + ts.flattenDiagnosticMessageText(error.messageText, ' '));
}
