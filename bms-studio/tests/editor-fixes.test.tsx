import React from 'react';
import { render, fireEvent, cleanup } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { measureGridBeats } from '../.source/vendor/bms-editor/src/chart/editor/gridRenderers';
import { snapBeatToGrid } from '../.source/vendor/bms-editor/src/chart/editor/editorUtils';
import { writeNoteChannels } from '../.source/vendor/bms-core/src/writer/channelWriter';
import { HeaderEditorPanel } from '../.source/vendor/bms-editor/src/chart/panels/HeaderEditorPanel';
import { useEditorStore } from '../.source/src/renderer/stores/editorStore';
import { useEditorOptions } from '../.source/src/renderer/stores/editorOptionsStore';
import { EditorOptionsDialog } from '../.source/src/renderer/components/EditorOptionsDialog';
import type { EditableBMSNote } from '@rhythm-archive/bms-core';
import { BMSParser, BMSWriter } from '@rhythm-archive/bms-core';

const note = (id: string, beat: number, ch: number, keysound = '01'): EditableBMSNote => ({
  id, beat, tick: Math.round(beat * 960), measure: Math.floor(beat / 4), fraction: (beat % 4) / 4,
  noteType: 'bgm', keysound, bgmChannel: ch, channel: '01',
});
beforeEach(() => {
  useEditorStore.setState(useEditorStore.getInitialState());
  useEditorOptions.getState().resetOptions();
});
afterEach(cleanup);

describe('triplet display and placement', () => {
  it.each([0, 3, 6, 429])('3/4 measure at beat %s contains nine triplets, not twelve', (start) => {
    const lines = [start, ...measureGridBeats(start, 3, 12)];
    expect(lines).toHaveLength(9);
    lines.forEach((beat, i) => {
      expect(beat).toBeCloseTo(start + i / 3, 8);
      expect(snapBeatToGrid(beat, 12)).toBeCloseTo(beat, 8);
    });
  });
  it('4/4 retains twelve triplets and a different snap changes density', () => {
    expect(measureGridBeats(0, 4, 12)).toHaveLength(11);
    expect(measureGridBeats(0, 3, 16)).toHaveLength(11);
  });
  it.each([120, 188, 240])('BPM %s uses quarter-beat sixteenths in a 3/4 measure', (bpm) => {
    const lines = [3, ...measureGridBeats(3, 3, 16)];
    expect(lines).toHaveLength(12);
    lines.forEach((beat, i) => {
      expect(beat).toBe(3 + i / 4);
      expect(snapBeatToGrid(beat, 16)).toBe(beat);
      if (i) expect((beat - lines[i - 1]) * 60 / bpm).toBeCloseTo(60 / bpm / 4, 8);
    });
  });
});

describe('BGM columns in downloaded BMS', () => {
  it('keeps silent columns and separates instruments even without simultaneous notes', () => {
    const lines = writeNoteChannels([note('a', 0, 2, '01'), note('b', 1, 0, '02'), note('c', 4, 2, '03')]);
    expect(lines.map(({ measure, data }) => [measure, data])).toEqual([
      [0, '00020000'], [0, '00'], [0, '01'], [1, '00'], [1, '00'], [1, '03'],
    ]);
  });
  it('keeps every sound on same-column collisions and extra unassigned layers', () => {
    const a = note('a', 0, 1, '01');
    const b = note('b', 0, 1, '02');
    const c = { ...note('c', 0, 0, '03'), bgmChannel: undefined };
    expect(writeNoteChannels([a, b, c]).map((line) => line.data)).toEqual(['00', '01', '02', '03']);
  });
});

describe('dragged BGM note is the conversion anchor', () => {
  it.each([0, 1, 2])('places grabbed note %s on the destination lane', (anchor) => {
    const notes = [note('left', 3, 0), note('middle', 3, 1), note('right', 3, 2)];
    useEditorStore.setState({ notes, keyMode: '9K' });
    const lanes = ['1', '2', '3', '4', '5', '6', '7', '8', '9'];
    useEditorStore.getState().convertNotes(notes.map((n) => n.id), { kind: 'playable', column: '5', anchorId: notes[anchor].id }, lanes);
    const moved = useEditorStore.getState().notes;
    expect(moved[anchor].column).toBe('5');
    expect(moved.map((n) => n.column)).toEqual(notes.map((_, i) => String(5 + i - anchor)));
    expect(moved.map((n) => n.beat)).toEqual([3, 3, 3]);
    useEditorStore.getState().undo();
    expect(useEditorStore.getState().notes).toEqual(notes);
  });
  it('constrains the whole group at a lane boundary', () => {
    const notes = [note('a', 0, 0), note('b', 0, 1), note('c', 0, 2)];
    useEditorStore.setState({ notes, keyMode: '9K' });
    useEditorStore.getState().convertNotes(['a', 'b', 'c'], { kind: 'playable', column: '1', anchorId: 'c' }, ['1', '2', '3', '4']);
    expect(useEditorStore.getState().notes.map((n) => n.column)).toEqual(['1', '2', '3']);
  });
});

it('applies source layout to the open chart and persists the preference', () => {
  const notes = [{ ...note('a', 0, 0), sourceBgmLine: 3 }];
  useEditorStore.setState({ notes });
  const ui = render(<EditorOptionsDialog open onClose={() => {}} />);
  fireEvent.click(ui.getByTestId('editor-option-bgm-layout-source'));
  expect(useEditorStore.getState().notes[0].bgmChannel).toBe(3);
  expect(JSON.parse(localStorage.getItem('editor-options')!).bgmLaneLayout).toBe('source');
  fireEvent.click(ui.getByRole('button', { name: /기본값으로/ }));
  expect(useEditorStore.getState().notes[0].bgmChannel).toBe(0);
  expect(JSON.parse(localStorage.getItem('editor-options')!).bgmLaneLayout).toBe('packed');
});

it('edits and removes LNMODE from Basic through the existing custom header storage', () => {
  const onSet = vi.fn();
  const onDelete = vi.fn();
  const chart = { headers: { custom: new Map([['LNMODE', '2']]), wav: new Map(), bmp: new Map() } } as any;
  const ui = render(<HeaderEditorPanel chart={chart} onHeaderChange={vi.fn()} onCustomHeaderSet={onSet} onCustomHeaderDelete={onDelete} />);
  const select = ui.getByLabelText('LNMODE');
  expect(select).toHaveValue('2');
  fireEvent.change(select, { target: { value: '3' } });
  expect(onSet).toHaveBeenCalledWith('LNMODE', '3');
  fireEvent.change(select, { target: { value: '' } });
  expect(onDelete).toHaveBeenCalledWith('LNMODE');
});

// Private files stay outside the repository; this opt-in check runs only when
// a local reproduction file was explicitly supplied by the user.
it.skipIf(!process.env.BMS_REPRO_FILE)('checks triplet density in the supplied chart', () => {
  const text = readFileSync(process.env.BMS_REPRO_FILE!, 'utf8');
  const shortMeasures = [...text.matchAll(/^#(\d{3})02:0\.75\s*$/gm)];
  expect(shortMeasures.length).toBeGreaterThan(0);
  for (let m = 0; m < shortMeasures.length; m++) {
    expect(measureGridBeats(m * 3, 3, 12)).toHaveLength(8);
  }
});

it.skipIf(!process.env.BMS_REPRO_FILE)('round-trips every BGM column and long note in the supplied chart', async () => {
  const parser = new BMSParser();
  const input = await parser.readBuffer(readFileSync(process.env.BMS_REPRO_FILE!));
  const original = parser.compileString(input);
  const editable = BMSWriter.fromBMSChart(original);
  const bgmObjects = original.objects.allSorted().filter((o) => o.channel === '01');
  const key = (n: {measure: number; fraction: number; value?: string; keysound?: string}) =>
    `${n.measure}:${n.fraction}:${n.value ?? n.keysound}`;
  const buckets = new Map<string, number[]>();
  for (const object of bgmObjects) {
    const entries = buckets.get(key(object)) ?? [];
    entries.push(object.bgmLine ?? 0);
    buckets.set(key(object), entries);
  }
  for (const n of editable.notes) if (n.noteType === 'bgm') n.bgmChannel = buckets.get(key(n))!.shift();
  editable.headers.custom.set('LNMODE', '3');
  const writer = new BMSWriter({ lnMode: editable.headers.lnobj ? 'lnobj' : 'channel', lnObjValue: editable.headers.lnobj });
  const output = writer.write(editable);
  const reloaded = new BMSParser().compileString(output);
  const signatures = (objects: typeof bgmObjects) => objects.map((o) => `${key(o)}:${o.bgmLine}`).sort();
  expect(signatures(reloaded.objects.allSorted().filter((o) => o.channel === '01'))).toEqual(signatures(bgmObjects));
  const after = BMSWriter.fromBMSChart(reloaded);
  expect(after.notes.length).toBe(editable.notes.length);
  expect(after.notes.filter((n) => n.endBeat !== undefined).map((n) => [n.beat, n.endBeat, n.keysound]))
    .toEqual(editable.notes.filter((n) => n.endBeat !== undefined).map((n) => [n.beat, n.endBeat, n.keysound]));
  expect(after.headers.custom.get('LNMODE')).toBe('3');
  expect(after.headers.bpm).toBe(editable.headers.bpm);
  expect([...after.timeSignatures]).toEqual([...editable.timeSignatures]);
});

it.skipIf(!process.env.BMS_REPRO_FILE || !process.env.BMS_EXPORTED_FILE)('checks the actual browser download against the original', async () => {
  const parse = async (path: string) => {
    const parser = new BMSParser();
    return parser.compileString(await parser.readBuffer(readFileSync(path)));
  };
  const before = await parse(process.env.BMS_REPRO_FILE!);
  const after = await parse(process.env.BMS_EXPORTED_FILE!);
  const signature = (chart: typeof before) => chart.objects.allSorted()
    .map((o) => `${o.measure}:${o.fraction}:${o.channel}:${o.value}:${o.bgmLine ?? ''}`).sort();
  expect(signature(after)).toEqual(signature(before));
  expect(after.headers.get('lnmode')).toBe('3');
  expect(after.headers.get('bpm')).toBe(before.headers.get('bpm'));
  expect([...after.timeSignatures.entries()]).toEqual([...before.timeSignatures.entries()]);
});
