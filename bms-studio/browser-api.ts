import type { ElectronAPI } from '../preload/index.d';
import type { SupportedLocale } from '../shared/ipc-contract';
import { basename, dirname, extname } from './lib/pathUtils';

const chartExtensions = ['.bms', '.bme', '.bml', '.pms', '.bmson'];
const audioExtensions = ['.ogg', '.wav', '.flac', '.mp3', '.m4a', '.aac', '.opus'];
const files = new Map<string, Blob>();
const caseInsensitivePaths = new Map<string, string>();
const linkedKeysounds = new Map<string, Map<string, Blob>>();
const autosaves = new Map<string, string>();
let nextSelection = 0;
let activeChart = '';

function chooseFiles(accept = '', folder = false, multiple = false): Promise<File[]> {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = accept;
    input.multiple = multiple;
    input.webkitdirectory = folder;
    input.hidden = true;
    const finish = (selected: File[]) => {
      input.remove();
      resolve(selected);
    };
    input.addEventListener('change', () => finish(Array.from(input.files ?? [])), { once: true });
    input.addEventListener('cancel', () => finish([]), { once: true });
    document.body.append(input);
    input.click();
  });
}

function foldPath(path: string) {
  return path.normalize('NFC').toLowerCase();
}

function addFile(path: string, blob: Blob) {
  files.set(path, blob);
  caseInsensitivePaths.set(foldPath(path), path);
}

function getFile(path: string) {
  return files.get(path) ?? files.get(caseInsensitivePaths.get(foldPath(path)) ?? '');
}

function requireFile(path: string) {
  const file = getFile(path);
  if (!file) throw new Error('이 탭에서 파일을 다시 열어 주세요: ' + basename(path));
  return file;
}

async function openFile(extensions: string[]) {
  const [file] = await chooseFiles(extensions.join(','));
  if (!file || !extensions.includes(extname(file.name).toLowerCase())) return null;
  const path = 'selection-' + ++nextSelection + '/' + file.name;
  addFile(path, file);
  return path;
}

function uniquePath(folder: string, filename: string) {
  const ext = extname(filename);
  const stem = ext ? filename.slice(0, -ext.length) : filename;
  let path = folder + '/' + filename;
  for (let i = 2; getFile(path); i++) path = folder + '/' + stem + '-' + i + ext;
  return path;
}

function chartBlob(content: string | Uint8Array) {
  return new Blob([typeof content === 'string' ? content : new Uint8Array(content)], {
    type: 'application/octet-stream',
  });
}

function exportChart(path: string, content: string | Uint8Array) {
  const blob = chartBlob(content);
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = basename(path).replace(/\.bmson$/i, '.bms');
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
  addFile(path, blob);
  activeChart = path;
}

function keysoundAt(path: string) {
  const extension = extname(path);
  const exact = audioExtensions.includes(extension.toLowerCase()) ? getFile(path) : undefined;
  if (exact) return exact;
  const stem = extension ? path.slice(0, -extension.length) : path;
  for (const alt of audioExtensions) {
    const candidate = getFile(stem + alt);
    if (candidate) return candidate;
  }
}

function keysoundReference(filename: string) {
  const trimmed = filename.trim();
  const unquoted = trimmed.startsWith('"') && trimmed.endsWith('"') ? trimmed.slice(1, -1) : trimmed;
  return unquoted.replaceAll('\\', '/').replace(/^(\.\/)+/, '');
}

function resolveSelectedPath(folder: string, relative: string) {
  if (!relative || relative.startsWith('/') || relative.includes(':')) return;
  const parts = folder.split('/');
  for (const part of relative.split('/')) {
    if (!part || part === '.') continue;
    if (part === '..') {
      // The first segment is the browser's selection ID, not a disk path.
      if (parts.length <= 1) return;
      parts.pop();
    } else {
      parts.push(part);
    }
  }
  return parts.length > 1 ? parts.join('/') : undefined;
}

function findKeysound(chartPath: string, filename: string) {
  const relative = keysoundReference(filename);
  const linked = linkedKeysounds.get(chartPath)?.get(foldPath(relative));
  if (linked) return linked;
  const folder = dirname(chartPath);
  const resolved = resolveSelectedPath(folder, relative);
  if (!resolved) return;
  return keysoundAt(resolved) ?? keysoundAt(folder + '/' + basename(relative));
}

function fileStem(name: string) {
  const ext = extname(name);
  return ext ? name.slice(0, -ext.length) : name;
}

export async function importChartKeysounds(chartPath: string, references: string[] = []) {
  const selected = await chooseFiles(audioExtensions.join(','), false, true);
  const folder = dirname(chartPath);
  const known = [...new Set(references)].map((filename) => {
    const relative = keysoundReference(filename);
    const name = foldPath(basename(relative));
    return { filename, relative, name, stem: fileStem(name) };
  });
  return selected.flatMap((file) => {
    if (!audioExtensions.includes(extname(file.name).toLowerCase())) return [];
    const name = foldPath(file.name);
    const stem = fileStem(name);
    const exact = known.filter((entry) => entry.name === name);
    const matches = exact.length ? exact : known.filter((entry) => entry.stem === stem);
    const targets = new Set(matches.map(({ relative }) => foldPath(resolveSelectedPath(folder, relative) ?? relative)));
    const canLink = targets.size === 1 && matches.every(({ filename }) => !findKeysound(chartPath, filename));
    const destPath = uniquePath(folder, file.name);
    addFile(destPath, file);
    if (canLink) {
      const linked = linkedKeysounds.get(chartPath) ?? new Map<string, Blob>();
      for (const { relative } of matches) linked.set(foldPath(relative), file);
      linkedKeysounds.set(chartPath, linked);
      // Returning the original names lets the editor keep their existing #WAV IDs.
      return matches.map(({ filename }) => ({ filename, destPath }));
    }
    return [{ filename: basename(destPath), destPath }];
  });
}

const localeKey = '10k-bms-studio-locale';
function locale(): SupportedLocale {
  let preferred: string | null = null;
  try {
    preferred = localStorage.getItem(localeKey);
  } catch {
    // Locale still works when a browser blocks persistent storage.
  }
  const value = preferred ?? navigator.language.split('-')[0];
  return value === 'ko' || value === 'ja' ? value : 'en';
}

const api: ElectronAPI = {
  file: {
    openBmsFile: () => openFile(chartExtensions),
    openAudioFile: () => openFile(audioExtensions),
    async openBmsFolder() {
      const selected = await chooseFiles('', true);
      if (!selected.length) return null;
      const prefix = 'selection-' + ++nextSelection + '/';
      const root = selected[0].webkitRelativePath.split('/')[0];
      if (!root) return null;
      for (const file of selected) {
        if (file.webkitRelativePath) addFile(prefix + file.webkitRelativePath, file);
      }
      return prefix + root;
    },
    async listBmsFolder(folder) {
      return [...files].flatMap(([path, file]) => {
        const ext = extname(path).toLowerCase();
        if (!path.startsWith(folder + '/') || !chartExtensions.includes(ext)) return [];
        return [{ path, name: basename(path), ext, size: file.size }];
      }).sort((a, b) => a.name.localeCompare(b.name));
    },
    async readBms(path) {
      const buffer = await requireFile(path).arrayBuffer();
      activeChart = path;
      return new Uint8Array(buffer);
    },
    async saveBms(path, content) {
      requireFile(path);
      exportChart(path, content);
      return true;
    },
    async saveAs(content, filename = 'chart.bms') {
      const folder = activeChart ? dirname(activeChart) : 'selection-' + ++nextSelection;
      const linked = linkedKeysounds.get(activeChart);
      const name = basename(filename).replace(/\.bmson$/i, '.bms');
      const ext = extname(name);
      const stem = ext ? name.slice(0, -ext.length) : name;
      const path = uniquePath(folder, stem + '-copy' + (ext || '.bms'));
      exportChart(path, content);
      if (linked) linkedKeysounds.set(path, new Map(linked));
      return path;
    },
    async readMeta(path) {
      return (await getFile(path + '.meta')?.text()) ?? null;
    },
    async saveMeta(path, content) {
      addFile(path + '.meta', new Blob([content]));
      return true;
    },
    async writeAutoSave(path, content) {
      autosaves.set(path, content);
      return true;
    },
    async checkAutoSave(path) {
      return autosaves.get(path) ?? null;
    },
    async deleteAutoSave(path) {
      autosaves.delete(path);
      return true;
    },
    importKeysounds: importChartKeysounds,
    async createNewBms({ title, artist, bpm, keyMode }) {
      const cleanLine = (text: string) => text.replace(/[\r\n]/g, ' ').trim();
      const chartTitle = cleanLine(title) || 'Untitled';
      const name = chartTitle.replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_') + '.bms';
      const folderPath = 'selection-' + ++nextSelection;
      const path = folderPath + '/' + name;
      const player = keyMode === '10K' || keyMode === '14K' ? 2 : 1;
      const content = [
        '#PLAYER ' + player,
        '#KEYMODE ' + keyMode,
        '#TITLE ' + chartTitle,
        '#ARTIST ' + cleanLine(artist),
        '#BPM ' + bpm,
        '#PLAYLEVEL 1',
        '#RANK 3',
        '',
      ].join('\n');
      addFile(path, chartBlob(content));
      return { path, name, folderPath };
    },
    async saveWavSlice() {
      throw new Error('WAV 슬라이스 내보내기는 데스크톱 앱에서 사용할 수 있습니다.');
    },
    async saveWavSlices() {
      throw new Error('WAV 슬라이스 내보내기는 데스크톱 앱에서 사용할 수 있습니다.');
    },
  },
  audio: {
    async readFile(path) {
      return requireFile(path).arrayBuffer();
    },
    async readBatch(chartPath, keysoundMap) {
      const results: Record<string, ArrayBuffer> = Object.create(null);
      const errors: Record<string, string> = Object.create(null);
      const reads = new Map<Blob, Promise<ArrayBuffer>>();
      const entries = Object.entries(keysoundMap);
      for (let i = 0; i < entries.length; i += 20) {
        await Promise.all(entries.slice(i, i + 20).map(async ([id, filename]) => {
          const blob = findKeysound(chartPath, filename);
          if (!blob) {
            errors[id] = 'Not found: ' + filename;
            return;
          }
          let read = reads.get(blob);
          if (!read) {
            read = blob.arrayBuffer();
            reads.set(blob, read);
          }
          try {
            // The audio decoder can detach its buffer, so each WAV ID gets its own copy.
            results[id] = (await read).slice(0);
          } catch (error) {
            errors[id] = error instanceof Error ? error.message : 'Read failed';
          }
        }));
      }
      return { results, errors };
    },
  },
  locale: {
    async getInitial() {
      return locale();
    },
    async set(value) {
      try {
        localStorage.setItem(localeKey, value);
      } catch {
        // The renderer applies the selected language for the current page.
      }
      return true;
    },
  },
  app: { async confirmClose() { return true; } },
  // Native menu and window events have no browser counterpart.
  on: () => () => {},
};

window.api = api;
