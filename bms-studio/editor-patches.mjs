// Browser-only fixes for the pinned upstream sources. Exact-match edits fail
// the build on upstream drift instead of silently shipping a partial fix.
const patches = new Map();
function patch(path, before, after, count = 1) {
  const edits = patches.get(path) ?? [];
  edits.push({ before, after, count });
  patches.set(path, edits);
}

const grid = 'vendor/bms-editor/src/chart/editor/gridRenderers.tsx';
patch(grid, '// Measure line color constants', `/** Absolute snap positions strictly inside one measure (boundaries render separately). */
export function measureGridBeats(start: number, length: number, snap: number): number[] {
  const step = Math.max(1, Math.round(3840 / snap)) / 960;
  const first = Math.floor((start + 1e-8) / step) + 1;
  const beats: number[] = [];
  for (let i = first; i * step < start + length - 1e-8; i++) beats.push(i * step);
  return beats;
}

// Measure line color constants`);
patch(grid, `      // Beat lines (1-beat interval, excluding measure lines)
      for (let b = 1; b < beatsInMeasure; b++) {
        const beatPos = currentBeat + b;
        if (beatPos > maxBeat) break;
        addLine(beatPos * beatScale, BEAT_LINE_COLOR);
      }

      // Grid lines (per-measure gridSnap override supported; index-based to avoid floating-point accumulation errors)
      const effectiveSnap = gridSnapOverrides?.get(measure) ?? gridSnap;
      for (let i = 1; i < effectiveSnap; i++) {
        const b = (i * beatsInMeasure) / effectiveSnap;
        const beatPos = currentBeat + b;
        if (beatPos > maxBeat) break;
        const isOnBeat = Math.abs(b - Math.round(b)) < 0.001;
        if (!isOnBeat) {
          addLine(beatPos * beatScale, GRID_LINE_COLOR);
        }
      }`, `      // The toolbar defines subdivisions per beat (snap / 4), not per
      // variable-length measure. A 3/4 measure must not compress triplets
      // into sixteenths. Use the same absolute tick grid as note placement.
      const effectiveSnap = gridSnapOverrides?.get(measure) ?? gridSnap;
      for (const beatPos of measureGridBeats(currentBeat, beatsInMeasure, effectiveSnap)) {
        if (beatPos > maxBeat) break;
        const isOnBeat = Math.abs(beatPos - Math.round(beatPos)) < 0.001;
        addLine(beatPos * beatScale, isOnBeat ? BEAT_LINE_COLOR : GRID_LINE_COLOR);
      }`);
patch(grid, '[totalBeats, beatScale, gridSnap, halfWidth, minBeat, maxBeat]);',
  '[totalBeats, beatScale, gridSnap, halfWidth, minBeat, maxBeat, timeSignatures, gridSnapOverrides]);');

const canvas = 'vendor/bms-editor/src/chart/NoteChartEditor.tsx';
patch(canvas, '  const dragNoteIdsRef = useRef<string[]>([]);',
  '  const dragNoteIdsRef = useRef<string[]>([]);\n  const dragAnchorIdRef = useRef<string | undefined>(undefined);');
patch(canvas, 'const startNoteDrag = (ids: string[]) => {',
  'const startNoteDrag = (ids: string[], anchorId: string) => {\n        dragAnchorIdRef.current = anchorId;');
patch(canvas, 'startNoteDrag(Array.from(selectedNotes));', 'startNoteDrag(Array.from(selectedNotes), clickedNote.id);', 2);
patch(canvas, 'startNoteDrag([clickedNote.id]);', 'startNoteDrag([clickedNote.id], clickedNote.id);', 2);
patch(canvas, "{ kind: 'bgm', channel: targetChannel }", "{ kind: 'bgm', channel: targetChannel, anchorId: dragAnchorIdRef.current }");
patch(canvas, "{ kind: 'playable', column: newColumn }", "{ kind: 'playable', column: newColumn, anchorId: dragAnchorIdRef.current }");
patch(canvas, 'const delta = e.deltaY > 0 ? -4 : 4;', `// Respect trackpad precision and wheel units instead of jumping a measure.
        const pixels = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? size.height : 1);
        const pixelsPerBeat = beatScaleRef.current * ((camera as THREE.OrthographicCamera).zoom || 1);
        const delta = -pixels / Math.max(1, pixelsPerBeat);`);
patch(canvas, '[gl.domElement, totalBeats, hScrollElRef]);', '[gl.domElement, totalBeats, hScrollElRef, size.height, camera]);');
// Keep a single scroll range so the seekbar, minimap and canvas agree near EOF.
patch(canvas, 'totalBeats - viewportBeatsRef.current + 4', 'totalBeats', 4);
patch(canvas, 'totalBeats - newViewportBeats + 4', 'totalBeats');
patch(canvas, `  const prevExternalScrollRef = useRef(externalScrollBeat);`, `  const prevExternalScrollRef = useRef<number | undefined>(undefined);`);
patch(canvas, `      isExternalUpdateRef.current = true;
      setScrollBeat(clamped);`, `      if (Math.abs(clamped - scrollBeatRef.current) > 1e-8) {
        isExternalUpdateRef.current = true;
        setScrollBeat(clamped);
      }`);

const types = 'vendor/bms-editor/src/chart/editor/types.ts';
patch(types, "| { kind: 'bgm'; channel: number }\n  | { kind: 'playable'; column: string };",
  "| { kind: 'bgm'; channel: number; anchorId?: string }\n  | { kind: 'playable'; column: string; anchorId?: string };");

const store = 'src/renderer/stores/editorStore.ts';
patch(store, "target: { kind: 'bgm'; channel: number } | { kind: 'playable'; column: string },",
  "target: { kind: 'bgm'; channel: number; anchorId?: string } | { kind: 'playable'; column: string; anchorId?: string },");
patch(store, `      const base = targets.reduce((m, n) => Math.min(m, rel(n)), Infinity);
      for (const n of targets) {
        const idx = Math.min(lanes.length - 1, startIdx + rel(n) - base);`, `      const anchor = targets.find((n) => n.id === target.anchorId);
      const base = anchor ? rel(anchor) : targets.reduce((m, n) => Math.min(m, rel(n)), Infinity);
      // Dragged note stays under the pointer; constrain the whole group at
      // lane edges so a middle/right anchor does not collapse its neighbours.
      const min = Math.min(...targets.map(rel));
      const max = Math.max(...targets.map(rel));
      const shift = Math.max(-min, Math.min(lanes.length - 1 - max, startIdx - base));
      for (const n of targets) {
        const idx = Math.max(0, Math.min(lanes.length - 1, rel(n) + shift));`);
// Reverse conversion also uses the grabbed lane when supplied. Keyboard/menu
// conversions retain their existing packing behaviour.
patch(store, `      const ordered = [...targets].sort((a, b) => a.tick - b.tick`, `      const anchor = targets.find((n) => n.id === target.anchorId);
      const rel = (n: EditableBMSNote) => n.noteType === 'bgm'
        ? (n.bgmChannel ?? 0) : Math.max(0, laneIds.indexOf(n.column ?? ''));
      const shift = anchor ? Math.max(-Math.min(...targets.map(rel)), target.channel - rel(anchor)) : 0;
      const ordered = [...targets].sort((a, b) => a.tick - b.tick`);
patch(store, 'let ch = Math.max(0, target.channel);', 'let ch = anchor ? Math.max(0, rel(n) + shift) : Math.max(0, target.channel);');

const options = 'src/renderer/components/EditorOptionsDialog.tsx';
patch(options, "import type { BgmLaneLayout } from '../lib/bgmLanes';", "import type { BgmLaneLayout } from '../lib/bgmLanes';\nimport { useEditorStore } from '../stores/editorStore';");
patch(options, 'onChange={() => setOptions({ bgmLaneLayout: value })}', `onChange={() => {
                    setOptions({ bgmLaneLayout: value });
                    useEditorStore.getState().relayoutBgmLanes(value);
                  }}`);
patch(options, 'onClick={resetOptions}', `onClick={() => {
            resetOptions();
            useEditorStore.getState().relayoutBgmLanes(useEditorOptions.getState().bgmLaneLayout);
          }}`);
for (const [locale, oldLabel, label, oldDescription, description] of [
  ['ko', '차트를 열 때 BGM 레인 배치', 'BGM 레인 배치',
    '이 편집기로 저장한 차트는 저장할 때의 배치로 열립니다. 열려 있는 차트는 BGM 매니저의 "레인 배치"에서 바꿀 수 있습니다.',
    '현재 차트에 즉시 적용하고 다음에 열 차트의 기본값으로 저장합니다. 다운로드한 BGM 열을 유지하려면 원본 순서를 선택하세요.'],
  ['en', 'BGM lanes when opening a chart', 'BGM lane layout',
    'Charts saved by this editor open with their saved layout. For the open chart, use "Lanes" in the BGM manager.',
    'Applies to the open chart and saves the default for future charts. Choose As in file to keep the columns in downloaded BMS files.'],
  ['ja', '譜面を開くときのBGMレーン配置', 'BGMレーン配置',
    'このエディタで保存した譜面は保存時の配置で開きます。開いている譜面はBGMマネージャの「レーン配置」で変えられます。',
    '開いている譜面にすぐ適用し、次に開く譜面の初期設定として保存します。ダウンロードしたBGM列を維持するには「元の順番」を選んでください。'],
]) {
  const path = 'src/shared/i18n/locales/' + locale + '/app.json';
  patch(path, JSON.stringify(oldLabel), JSON.stringify(label));
  patch(path, JSON.stringify(oldDescription), JSON.stringify(description));
}

const writer = 'vendor/bms-core/src/writer/channelWriter.ts';
patch(writer, 'type ChannelEvent = {', 'type ChannelEvent = {\n  bgmChannel?: number;');
patch(writer, `    const startEvent: NoteEvent = {
      ...pos,`, `    const startEvent: NoteEvent = {
      ...pos,
      bgmChannel: note.noteType === 'bgm' ? note.bgmChannel : undefined,`);
patch(writer, `        const layers = splitOverlappingEvents(channelEvents, resolution);
        for (const layerEvents of layers) {
          const channelData = createChannelDataForMeasure(measure, channel, layerEvents, resolution);
          if (channelData) channelDataList.push(channelData);
        }`, `        // Column N is the Nth channel-01 line in each measure, including
        // empty columns. Sidecar metadata cannot preserve this in a download.
        const columns = new Map<number, ChannelEvent[]>();
        const unassigned: ChannelEvent[] = [];
        for (const event of channelEvents) {
          const ch = event.bgmChannel;
          if (ch === undefined || !Number.isSafeInteger(ch) || ch < 0) {
            unassigned.push(event);
          } else {
            const entries = columns.get(ch) ?? [];
            entries.push(event);
            columns.set(ch, entries);
          }
        }
        const overflow: ChannelEvent[][] = [];
        const lastColumn = columns.size ? Math.max(...columns.keys()) : -1;
        for (let ch = 0; ch <= lastColumn; ch++) {
          const layers = splitOverlappingEvents(columns.get(ch) ?? [], resolution);
          const data = createChannelDataForMeasure(measure, channel, layers[0] ?? [], resolution);
          channelDataList.push(data ?? { measure, channel, data: '00' });
          overflow.push(...layers.slice(1));
        }
        // Preserve simultaneous notes even if edits produced a same-column
        // collision. Extra layers follow the named columns, never replace one.
        for (const layer of [...overflow, ...splitOverlappingEvents(unassigned, resolution)]) {
          const data = createChannelDataForMeasure(measure, channel, layer, resolution);
          if (data) channelDataList.push(data);
        }`);

const header = 'vendor/bms-editor/src/chart/panels/HeaderEditorPanel.tsx';
patch(header, "  { key: 'lnobj',      label: 'LNOBJ',      type: 'text',   placeholder: 'ZZ' },", `  { key: 'lnobj',      label: 'LNOBJ',      type: 'text',   placeholder: 'ZZ' },
  { key: 'lnmode', label: 'LN Mode', type: 'select', options: [
    { value: 1, label: '1 - LN' }, { value: 2, label: '2 - CN' }, { value: 3, label: '3 - HCN' },
  ] },`);
patch(header, '    // BMSHeaderData has named fields only;', "    if (key === 'lnmode') return chart.headers.custom.get('LNMODE') ?? '';\n    // BMSHeaderData has named fields only;");
patch(header, `    if (readOnly) return;
    if (field.type`, `    if (readOnly) return;
    if (field.key === 'lnmode') {
      if (value === '') onCustomHeaderDelete?.('LNMODE');
      else onCustomHeaderSet?.('LNMODE', value);
      return;
    }
    if (field.type`);
patch(header, '[onHeaderChange, readOnly]);', '[onHeaderChange, onCustomHeaderSet, onCustomHeaderDelete, readOnly]);');
patch(header, '<select\n                      value={getHeaderValue(field.key)}', '<select\n                      aria-label={field.key.toUpperCase()}\n                      value={getHeaderValue(field.key)}');

const route = 'src/renderer/routes/Editor.tsx';
patch(route, `  const handleBack = useCallback(() => {`, `  // Canvas navigation and the song seekbar share a position. During playback
  // automatic canvas reports must not seek the audio scheduler back on itself.
  const handleChartNavigate = useCallback((beat: number) => {
    const clamped = Math.max(0, Math.min(totalBeats, beat));
    store.setCurrentBeat(clamped);
    if (isPlayingRef.current) return;
    const timing = editedTimingRef.current;
    if (!timing) return;
    const seconds = Math.max(0, Math.min(useEditorStore.getState().playbackDuration, timing.beatToSeconds(clamped)));
    playbackBeatRef.current = clamped;
    playbackOffsetRef.current = seconds;
    store.setPlaybackTime(seconds);
    audioSchedulerRef.current?.seek(seconds, speedRef.current);
  }, [totalBeats]);

  const handleMinimapNavigate = useCallback((beat: number) => {
    const timing = editedTimingRef.current;
    if (timing) handleSeek(timing.beatToSeconds(beat));
    else store.setCurrentBeat(beat);
  }, [handleSeek]);

  const handleBack = useCallback(() => {`);
patch(route, 'onScrollChange={store.setCurrentBeat}', 'onScrollChange={handleChartNavigate}');
patch(route, 'onNavigate={store.setCurrentBeat}', 'onNavigate={handleMinimapNavigate}', 3);
const minimap = 'vendor/bms-editor/src/chart/panels/Minimap.tsx';
patch(minimap, 'totalBeats - viewportBeats + 4', 'totalBeats');

export const editorPatchPaths = [...patches.keys()];
export function patchEditorSource(code, id) {
  const path = id.replaceAll('\\', '/').split('?')[0];
  const key = editorPatchPaths.find((entry) => path.endsWith('/' + entry));
  if (!key) return code;
  let result = code.replaceAll('\r\n', '\n');
  for (const { before, after, count } of patches.get(key)) {
    const matches = result.split(before).length - 1;
    if (matches !== count) throw new Error('BMS Studio patch drift: ' + key + ' (expected ' + count + ', found ' + matches + '): ' + before.slice(0, 100));
    result = result.replaceAll(before, after);
  }
  return result;
}
