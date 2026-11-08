import { fileURLToPath } from 'node:url';
import desktop from './.source/electron.vite.config';
import { patchEditorSource } from './editor-patches.mjs';

const renderer = desktop.renderer!;

function patchKeysoundImport(code: string) {
  const edits = [
    ['  // --- Keysound Import ---', [
      '  const [keysoundReloadQueued, setKeysoundReloadQueued] = useState(false);',
      '  // --- Keysound Import ---',
    ].join('\n')],
    [
      'await window.api.file.importKeysounds(file.path)',
      'await importChartKeysounds(file.path, Object.values(keysoundRecord))',
    ],
    [
      "      showToast(t('editor:routes.editor.toast.keysoundsImported', { count: added }), 'success');",
      "      if (added > 0) showToast(t('editor:routes.editor.toast.keysoundsImported', { count: added }), 'success');",
    ],
    ['if (added > 0) reloadAudioRef.current?.();', 'if (added > 0 || reused > 0) setKeysoundReloadQueued(true);'],
    ['  // Auto-load audio effect (must be after loadAudio definition)', [
      '  useEffect(() => {',
      "    if (!keysoundReloadQueued || !chart || audioPhase === 'loading') return;",
      '    setKeysoundReloadQueued(false);',
      '    reloadAudioRef.current?.();',
      '  }, [keysoundReloadQueued, chart, audioPhase]);',
      '',
      '  // Auto-load audio effect (must be after loadAudio definition)',
    ].join('\n')],
  ];
  let patched = code.replaceAll('\r\n', '\n');
  for (const [before, after] of edits) {
    if (!patched.includes(before)) throw new Error('BMS Desktop keysound import has moved: ' + before);
    patched = patched.replace(before, after);
  }
  return "import { importChartKeysounds } from '../bms-studio-browser-api';\n" + patched;
}

export default {
  ...renderer,
  base: '/bms-studio/',
  build: {
    ...renderer.build,
    outDir: fileURLToPath(new URL('./dist', import.meta.url)),
    emptyOutDir: true,
  },
  plugins: [
    ...(renderer.plugins ?? []),
    {
      name: 'bms-studio-browser',
      enforce: 'pre',
      transformIndexHtml: {
        order: 'pre',
        handler(html: string) {
          const entry = './main.tsx';
          if (!html.includes(entry)) throw new Error('BMS Desktop renderer entry has moved.');
          return html
            .replace(entry, './bms-studio-browser-entry.ts')
            .replace('<title>BMS Desktop</title>', '<title>10Key BMS Studio</title>')
            .replace('<body class="', '<body class="bms-studio ')
            .replace('<div id="root"></div>', [
              '<header class="bms-studio-guide">',
              '<a href="/table.html">← 난이도표</a>',
              '<strong>BMS 에디터 · 플레이어</strong>',
              '<span>차트와 키음은 폴더로 함께 열 수 있어요. BMS만 열었다면 편집기의 키음 파일 업로드에서 연결하세요. 저장한 차트는 다운로드됩니다.</span>',
              '</header>',
              '<div id="root"></div>',
            ].join('\n'));
        },
      },
      transform(code: string, id: string) {
        const path = id.replaceAll('\\', '/');
        code = patchEditorSource(code, id);
        if (path.endsWith('/src/renderer/routes/Editor.tsx')) return patchKeysoundImport(code);
        if (!path.endsWith('/src/renderer/lib/sessionStorage.ts')) return code;
        // File picker handles last for this page. Avoid restoring paths after reload.
        return [
          'const sessionValues = new Map();',
          'const localStorage = {',
          '  getItem(key) { return sessionValues.get(key) ?? null; },',
          '  setItem(key, value) { sessionValues.set(key, value); },',
          '};',
          code,
        ].join('\n');
      },
    },
  ],
};
