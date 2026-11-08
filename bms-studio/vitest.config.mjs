import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { dirname } from 'node:path';
import { patchEditorSource } from './editor-patches.mjs';

const fromSource = createRequire(new URL('./.source/package.json', import.meta.url));
const local = (path) => fileURLToPath(new URL(path, import.meta.url));
export default {
  root: local('.'),
  plugins: [{ name: 'browser-editor-fixes', enforce: 'pre', transform: patchEditorSource }],
  resolve: {
    alias: {
      '@rhythm-archive/bms-core': local('./.source/vendor/bms-core/src/index.ts'),
      '@rhythm-archive/bms-editor': local('./.source/vendor/bms-editor/src/index.ts'),
      '@rhythm-archive/bms-player': local('./.source/vendor/bms-player/src/index.ts'),
      '@testing-library/react': fromSource.resolve('@testing-library/react'),
      'react': dirname(fromSource.resolve('react/package.json')),
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: [local('./.source/tests/setup.ts')],
    include: [
      'tests/**/*.test.ts', 'tests/**/*.test.tsx',
      '.source/tests/unit/lib/bgmLanes.test.ts',
      '.source/tests/unit/stores/editorOptionsStore.test.ts',
      '.source/tests/unit/stores/editorStore-placement.test.ts',
      '.source/tests/integration/editor-workflow.test.ts',
      // writer.test.ts requires the upstream author's external S: music library.
      '.source/vendor/bms-core/tests/writer-fixes.test.ts',
      '.source/vendor/bms-core/tests/writer-lnobj.test.ts',
      '.source/vendor/bms-core/tests/writer-roundtrip-strict.test.ts',
      '.source/vendor/bms-core/tests/bgm-lines.test.ts',
    ],
    fileParallelism: false,
  },
};
