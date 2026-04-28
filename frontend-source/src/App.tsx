import { useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, DragEvent, FormEvent } from "react";

type OptionItem = { token: string; label: string };
type Point = { x: number; y: number };

type ApiOptions = {
  presets: OptionItem[];
  lifeGauges: OptionItem[];
  graphDataOptions: string[];
  defaults: {
    preset: string;
    lifeGauge: string;
    speedRate: number;
    speedRateMin: number;
    speedRateMax: number;
    randomPlacement: boolean;
    zeroPoorMode: boolean;
  };
  acceptedExtensions: string[];
};

type CalculationResponse = {
  fileName: string;
  format: string;
  title: string;
  titleRaw: string;
  artist: string;
  nameDiff: string;
  keyCount: number | null;
  modeName: string | null;
  keyLabel: string | null;
  noteCount: number;
  duration: number;
  resolvedPreset: string;
  options: {
    judgmentPreset: string;
    lifeGauge: string;
    speedRate: number;
    randomPlacement: boolean;
    zeroPoorMode: boolean;
  };
  metrics: Record<string, unknown>;
  totalDiff: Record<string, unknown>;
  noteTimes: number[];
  log: string;
};

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.trim() ?? "";
const DISCORD_URL = "https://discord.gg/2vwNSa54X9";
const FEATURED_METRICS = [
  { key: "circus_rating", label: "Circus Rating", precision: 3 },
  { key: "revive_lv", label: "Revive LV", precision: 0 },
  { key: "global_nps", label: "Global NPS", precision: 3 },
  { key: "peak_nps", label: "Peak NPS", precision: 3 },
  { key: "jack_diff", label: "Jack Diff", precision: 3 },
  { key: "stream_diff", label: "Stream Diff", precision: 3 },
];

function classNames(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

function formatMetricLabel(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function toFiniteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatMetricValue(value: unknown, precision = 3): string {
  const numericValue = toFiniteNumber(value);
  if (numericValue !== null) {
    const maxDigits = Number.isInteger(numericValue) ? 0 : precision;
    return numericValue.toLocaleString(undefined, {
      maximumFractionDigits: maxDigits,
    });
  }
  if (typeof value === "number") {
    return value.toLocaleString(undefined, {
      maximumFractionDigits: Number.isInteger(value) ? 0 : precision,
    });
  }
  if (typeof value === "boolean") {
    return value ? "True" : "False";
  }
  if (value == null) {
    return "N/A";
  }
  return String(value);
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return "0:00.000";
  }
  const minutes = Math.floor(seconds / 60);
  const rest = seconds - minutes * 60;
  return `${minutes}:${rest.toFixed(3).padStart(6, "0")}`;
}

function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unknown error";
}

function downsamplePoints(points: Point[], maxPoints = 800): Point[] {
  if (points.length <= maxPoints) {
    return points;
  }
  const step = points.length / maxPoints;
  const sampled: Point[] = [];
  for (let index = 0; index < maxPoints; index += 1) {
    sampled.push(points[Math.floor(index * step)]);
  }
  sampled[sampled.length - 1] = points[points.length - 1];
  return sampled;
}

function getNestedRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function getArrayFromRecord(record: Record<string, unknown>, key: string): unknown[] | null {
  const value = record[key];
  return Array.isArray(value) ? value : null;
}

function extractGraphPoints(result: CalculationResponse | null, graphKey: string): Point[] {
  if (!result) {
    return [];
  }

  const totalDiff = result.totalDiff;
  if (graphKey === "sv_list") {
    const svList = Array.isArray(totalDiff.sv_list) ? totalDiff.sv_list : [];
    return svList
      .map((entry) => {
        if (!Array.isArray(entry) || entry.length < 2) {
          return null;
        }
        const x = toFiniteNumber(entry[0]);
        const y = toFiniteNumber(entry[1]);
        if (x === null || y === null) {
          return null;
        }
        return { x: x / 1000.0, y };
      })
      .filter((point): point is Point => point !== null);
  }

  let values: unknown[] | null = null;
  if (graphKey === "nps_v2") {
    const npsV2 = getNestedRecord(totalDiff.nps_v2);
    values = npsV2 && Array.isArray(npsV2.nps_v2) ? (npsV2.nps_v2 as unknown[]) : null;
  }
  if (!values) {
    values = getArrayFromRecord(totalDiff, graphKey);
  }
  if (!values) {
    const jackDiff = getNestedRecord(totalDiff.jack_diff);
    values = jackDiff ? getArrayFromRecord(jackDiff, graphKey) : null;
  }
  if (!values) {
    const noteDiff = getNestedRecord(totalDiff.note_diff);
    values = noteDiff ? getArrayFromRecord(noteDiff, graphKey) : null;
  }
  if (!values) {
    return [];
  }

  return values
    .map((value, index) => {
      const y = toFiniteNumber(value);
      if (y === null) {
        return null;
      }
      const x = index < result.noteTimes.length ? result.noteTimes[index] : index;
      return { x, y };
    })
    .filter((point): point is Point => point !== null);
}

function buildSvgPolyline(points: Point[], width: number, height: number): string {
  if (!points.length) {
    return "";
  }

  const xValues = points.map((point) => point.x);
  const yValues = points.map((point) => point.y);
  const minX = Math.min(...xValues);
  const maxX = Math.max(...xValues);
  const minY = Math.min(...yValues);
  const maxY = Math.max(...yValues);
  const xSpan = maxX - minX || 1;
  const ySpan = maxY - minY || 1;

  return points
    .map((point) => {
      const x = ((point.x - minX) / xSpan) * width;
      const y = height - ((point.y - minY) / ySpan) * height;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

export default function App() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [options, setOptions] = useState<ApiOptions | null>(null);
  const [optionsError, setOptionsError] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [preset, setPreset] = useState("auto_stable");
  const [lifeGauge, setLifeGauge] = useState("Score % Acc %");
  const [speedRate, setSpeedRate] = useState("1.00");
  const [randomPlacement, setRandomPlacement] = useState(false);
  const [zeroPoorMode, setZeroPoorMode] = useState(false);
  const [selectedGraphData, setSelectedGraphData] = useState("note_score_diff");
  const [dragActive, setDragActive] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [result, setResult] = useState<CalculationResponse | null>(null);
  const [showAllMetrics, setShowAllMetrics] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    async function loadOptions() {
      try {
        const response = await fetch(`${API_BASE}/api/options`, { signal: controller.signal });
        if (!response.ok) {
          throw new Error(`Options request failed with ${response.status}`);
        }
        const data = (await response.json()) as ApiOptions;
        setOptions(data);
        setPreset(data.defaults.preset);
        setLifeGauge(data.defaults.lifeGauge);
        setSpeedRate(data.defaults.speedRate.toFixed(2));
        setRandomPlacement(data.defaults.randomPlacement);
        setZeroPoorMode(data.defaults.zeroPoorMode);
        if (data.graphDataOptions.length > 0) {
          setSelectedGraphData(data.graphDataOptions[0]);
        }
      } catch (error) {
        if ((error as Error).name !== "AbortError") {
          setOptionsError(errorMessage(error));
        }
      }
    }
    void loadOptions();
    return () => controller.abort();
  }, []);

  const noteDiffMetrics =
    result && typeof result.metrics.note_diff === "object" && result.metrics.note_diff !== null
      ? (result.metrics.note_diff as Record<string, unknown>)
      : null;
  const featuredMetrics = result
    ? FEATURED_METRICS.filter((metric) => metric.key in result.metrics)
    : [];
  const otherMetrics = result
    ? Object.entries(result.metrics)
        .filter(([key]) => key !== "note_diff" && !FEATURED_METRICS.some((metric) => metric.key === key))
        .sort(([left], [right]) => left.localeCompare(right))
    : [];
  const graphPoints = useMemo(
    () => downsamplePoints(extractGraphPoints(result, selectedGraphData)),
    [result, selectedGraphData],
  );
  const graphPolyline = useMemo(() => buildSvgPolyline(graphPoints, 760, 220), [graphPoints]);
  const graphXValues = graphPoints.map((point) => point.x);
  const graphYValues = graphPoints.map((point) => point.y);

  function assignFile(file: File | null) {
    setSelectedFile(file);
    setSubmitError("");
  }

  function onFileInput(event: ChangeEvent<HTMLInputElement>) {
    assignFile(event.target.files?.[0] ?? null);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragActive(false);
    assignFile(event.dataTransfer.files?.[0] ?? null);
  }

  function onDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragActive(true);
  }

  function onDragLeave(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      setDragActive(false);
    }
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedFile) {
      setSubmitError("Upload a BMS, BME, BML, PMS, or osu!mania file first.");
      return;
    }

    setIsSubmitting(true);
    setSubmitError("");
    const formData = new FormData();
    formData.append("file", selectedFile);
    formData.append("judgment_preset", preset);
    formData.append("life_gauge", lifeGauge);
    formData.append("speed_rate", speedRate);
    formData.append("random_placement", String(randomPlacement));
    formData.append("zero_poor_mode", String(zeroPoorMode));

    try {
      const response = await fetch(`${API_BASE}/api/calculate`, { method: "POST", body: formData });
      if (!response.ok) {
        let detail = `Calculation failed with ${response.status}`;
        try {
          const payload = (await response.json()) as { detail?: string };
          if (payload.detail) {
            detail = payload.detail;
          }
        } catch {
          // ignore JSON parse failure
        }
        throw new Error(detail);
      }
      setResult((await response.json()) as CalculationResponse);
    } catch (error) {
      setSubmitError(errorMessage(error));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="relative overflow-hidden">
      <div className="pointer-events-none absolute inset-0 bg-paper-grid bg-[size:44px_44px] opacity-30" />
      <div className="mx-auto flex min-h-screen max-w-[1500px] flex-col px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <header className="panel relative mb-6 overflow-hidden p-6 sm:p-8">
          <div className="absolute inset-y-0 right-0 hidden w-2/5 bg-[radial-gradient(circle_at_center,rgba(195,99,55,0.22),transparent_62%)] lg:block" />
          <div className="relative max-w-4xl">
            <p className="mb-3 font-mono text-[0.72rem] uppercase tracking-[0.3em] text-emberDeep">
              TenRiff Laboratory / 10k-calc Web Harness
            </p>
            <h1 className="max-w-3xl font-display text-4xl leading-tight text-ink sm:text-5xl">
              Upload one chart, resolve the original preset logic, and inspect the calculator in a
              browser.
            </h1>
            <p className="mt-4 max-w-2xl text-sm leading-6 text-ink/72 sm:text-base">
              The Python parser and difficulty engine stay intact. This page only wraps upload,
              option selection, and result formatting around the original `10k-calc`.
            </p>
            <div className="mt-6 flex flex-wrap gap-2">
              <span className="option-chip">BMS / BME / BML / PMS / OSU</span>
              <span className="option-chip">Auto Stable / Auto Lazer</span>
              <span className="option-chip">Circus Rating + Revive LV</span>
              <span className="option-chip">Original Python Core</span>
            </div>
            <a
              className="mt-5 inline-flex min-h-11 items-center rounded-full border border-ink/12 bg-white/75 px-4 py-2 text-sm font-semibold text-ink transition hover:border-ember/40 hover:text-emberDeep"
              href={DISCORD_URL}
              rel="noreferrer"
              target="_blank"
            >
              10키 한국 디스코드
            </a>
          </div>
        </header>

        <main className="grid gap-6 lg:grid-cols-[minmax(360px,420px)_minmax(0,1fr)]">
          <section className="panel p-5 sm:p-6">
            <div className="mb-6 flex items-end justify-between gap-4">
              <div>
                <p className="field-label">Input Rig</p>
                <h2 className="mt-2 font-display text-3xl text-ink">Chart Setup</h2>
              </div>
              <span
                className={classNames(
                  "rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em]",
                  options
                    ? "border border-moss/20 bg-moss/10 text-moss"
                    : "border border-ember/20 bg-ember/10 text-emberDeep",
                )}
              >
                {options ? "API Ready" : "Loading"}
              </span>
            </div>

            <form className="space-y-5" onSubmit={onSubmit}>
              <div
                className={classNames(
                  "rounded-[26px] border border-dashed p-5 transition duration-150",
                  dragActive
                    ? "border-ember bg-ember/10 shadow-bevel"
                    : "border-ink/15 bg-white/55 hover:border-ember/45 hover:bg-white/70",
                )}
                onDrop={onDrop}
                onDragOver={onDragOver}
                onDragLeave={onDragLeave}
              >
                <p className="field-label">Upload</p>
                <h3 className="mt-2 font-display text-2xl text-ink">Drop a chart file here</h3>
                <p className="mt-2 text-sm leading-6 text-ink/70">
                  Accepted: {options?.acceptedExtensions.join(", ") ?? "loading..."}
                </p>
                <div className="mt-4 flex flex-wrap items-center gap-3">
                  <button
                    className="rounded-full bg-ink px-5 py-3 text-sm font-semibold text-shell transition hover:bg-emberDeep disabled:cursor-not-allowed disabled:opacity-50"
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={!options}
                  >
                    Choose File
                  </button>
                  <span className="text-sm text-ink/60">or drag from Explorer directly</span>
                </div>
                <input
                  ref={fileInputRef}
                  accept={options?.acceptedExtensions.join(",")}
                  className="hidden"
                  type="file"
                  onChange={onFileInput}
                />
                <div className="mt-4 rounded-[22px] border border-ink/10 bg-paper/70 px-4 py-4">
                  {selectedFile ? (
                    <>
                      <p className="font-semibold text-ink">{selectedFile.name}</p>
                      <p className="mt-1 font-mono text-xs uppercase tracking-[0.18em] text-ink/55">
                        {formatFileSize(selectedFile.size)}
                      </p>
                    </>
                  ) : (
                    <p className="text-sm text-ink/55">No chart selected yet.</p>
                  )}
                </div>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <label className="space-y-2">
                  <span className="field-label">Judgment Preset</span>
                  <select className="field-input" value={preset} onChange={(event) => setPreset(event.target.value)}>
                    {(options?.presets ?? []).map((item) => (
                      <option key={item.token} value={item.token}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="space-y-2">
                  <span className="field-label">Life Gauge</span>
                  <select
                    className="field-input"
                    value={lifeGauge}
                    onChange={(event) => setLifeGauge(event.target.value)}
                  >
                    {(options?.lifeGauges ?? []).map((item) => (
                      <option key={item.token} value={item.token}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <label className="block space-y-2">
                <span className="field-label">Speed Rate</span>
                <input
                  className="field-input"
                  max={options?.defaults.speedRateMax ?? 2}
                  min={options?.defaults.speedRateMin ?? 0.5}
                  step="0.01"
                  type="number"
                  value={speedRate}
                  onChange={(event) => setSpeedRate(event.target.value)}
                />
                <p className="text-xs leading-5 text-ink/55">
                  Backend clamp:{" "}
                  {options
                    ? `${options.defaults.speedRateMin.toFixed(2)} - ${options.defaults.speedRateMax.toFixed(2)}`
                    : "0.50 - 2.00"}
                </p>
              </label>

              <label className="toggle-card">
                <input
                  checked={randomPlacement}
                  className="mt-1 size-4 rounded border-ink/25 text-ember focus:ring-ember/40"
                  type="checkbox"
                  onChange={(event) => setRandomPlacement(event.target.checked)}
                />
                <span>
                  <span className="block text-sm font-semibold text-ink">Note Line Random</span>
                  <span className="mt-1 block text-sm leading-6 text-ink/62">
                    Use the random lane-placement branch from the original calculator.
                  </span>
                </span>
              </label>

              <label className="toggle-card">
                <input
                  checked={zeroPoorMode}
                  className="mt-1 size-4 rounded border-ink/25 text-ember focus:ring-ember/40"
                  type="checkbox"
                  onChange={(event) => setZeroPoorMode(event.target.checked)}
                />
                <span>
                  <span className="block text-sm font-semibold text-ink">0Poor Mode</span>
                  <span className="mt-1 block text-sm leading-6 text-ink/62">
                    Pass the zero-poor flag through without altering calculator logic.
                  </span>
                </span>
              </label>

              {(optionsError || submitError) && (
                <div className="rounded-2xl border border-ember/20 bg-ember/10 px-4 py-3 text-sm text-emberDeep">
                  {optionsError || submitError}
                </div>
              )}

              <button
                className="flex w-full items-center justify-center rounded-[22px] bg-ember px-5 py-4 text-sm font-semibold uppercase tracking-[0.18em] text-shell transition hover:bg-emberDeep disabled:cursor-not-allowed disabled:opacity-55"
                disabled={!options || !selectedFile || isSubmitting}
                type="submit"
              >
                {isSubmitting ? "Calculating..." : "Calculate Difficulty"}
              </button>
            </form>
          </section>

          <section className="panel p-5 sm:p-6">
            <div className="flex flex-col gap-4 border-b border-ink/10 pb-5 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <p className="field-label">Result Surface</p>
                <h2 className="mt-2 font-display text-3xl text-ink">Live Output</h2>
              </div>
              {result ? (
                <div className="flex flex-wrap gap-2">
                  <span className="option-chip">{result.format.toUpperCase()}</span>
                  {result.keyLabel ? <span className="option-chip">{result.keyLabel}</span> : null}
                  {result.modeName ? <span className="option-chip">{result.modeName}</span> : null}
                </div>
              ) : null}
            </div>

            {result ? (
              <div className="space-y-6 pt-6">
                <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
                  <div className="rounded-[26px] border border-ink/10 bg-paper/75 p-5">
                    <p className="font-mono text-[0.7rem] uppercase tracking-[0.24em] text-emberDeep">
                      Chart Identity
                    </p>
                    <h3 className="mt-3 font-display text-3xl text-ink">{result.title}</h3>
                    <div className="mt-4 grid gap-3 sm:grid-cols-2">
                      <div><p className="field-label">Artist</p><p className="mt-1 text-sm text-ink/75">{result.artist || "N/A"}</p></div>
                      <div><p className="field-label">Difficulty Label</p><p className="mt-1 text-sm text-ink/75">{result.nameDiff || "N/A"}</p></div>
                      <div><p className="field-label">File</p><p className="mt-1 break-all text-sm text-ink/75">{result.fileName}</p></div>
                      <div><p className="field-label">Resolved Preset</p><p className="mt-1 text-sm text-ink/75">{result.resolvedPreset}</p></div>
                    </div>
                  </div>
                  <div className="rounded-[26px] border border-ink/10 bg-white/70 p-5">
                    <p className="font-mono text-[0.7rem] uppercase tracking-[0.24em] text-ink/55">
                      Session Facts
                    </p>
                    <dl className="mt-4 grid grid-cols-2 gap-4">
                      <div><dt className="field-label">Notes</dt><dd className="mt-1 text-lg font-semibold text-ink">{result.noteCount}</dd></div>
                      <div><dt className="field-label">Duration</dt><dd className="mt-1 text-lg font-semibold text-ink">{formatDuration(result.duration)}</dd></div>
                      <div><dt className="field-label">Key Count</dt><dd className="mt-1 text-lg font-semibold text-ink">{result.keyCount ?? "N/A"}</dd></div>
                      <div><dt className="field-label">Speed Rate</dt><dd className="mt-1 text-lg font-semibold text-ink">x{Number(result.options.speedRate).toFixed(2)}</dd></div>
                    </dl>
                  </div>
                </div>

                {featuredMetrics.length > 0 ? (
                  <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                    {featuredMetrics.map((metric) => (
                      <article key={metric.key} className="metric-card">
                        <p className="field-label">{metric.label}</p>
                        <p className="mt-3 font-display text-4xl text-ink">
                          {formatMetricValue(result.metrics[metric.key], metric.precision)}
                        </p>
                      </article>
                    ))}
                  </div>
                ) : null}

                <div className="rounded-[26px] border border-ink/10 bg-white/72 p-5">
                  <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
                    <div>
                      <p className="field-label">Graph Lens</p>
                      <h3 className="mt-2 font-display text-2xl text-ink">Original graph datasets</h3>
                    </div>
                    <label className="block min-w-[240px] space-y-2">
                      <span className="field-label">Graph Data</span>
                      <select
                        className="field-input"
                        value={selectedGraphData}
                        onChange={(event) => setSelectedGraphData(event.target.value)}
                      >
                        {(options?.graphDataOptions ?? []).map((item) => (
                          <option key={item} value={item}>
                            {item}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>

                  <div className="mt-4 rounded-[22px] border border-ink/10 bg-paper/55 p-4">
                    {graphPoints.length > 0 ? (
                      <>
                        <svg
                          className="h-[220px] w-full overflow-visible"
                          viewBox="0 0 760 220"
                          preserveAspectRatio="none"
                        >
                          <rect x="0" y="0" width="760" height="220" rx="18" fill="rgba(255,255,255,0.55)" />
                          <polyline
                            fill="none"
                            points={graphPolyline}
                            stroke="#c36337"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth="2"
                          />
                        </svg>
                        <div className="mt-4 grid gap-3 sm:grid-cols-3">
                          <div className="rounded-2xl border border-ink/8 bg-white/60 px-4 py-3">
                            <p className="field-label">Points</p>
                            <p className="mt-2 text-sm font-semibold text-ink">{graphPoints.length}</p>
                          </div>
                          <div className="rounded-2xl border border-ink/8 bg-white/60 px-4 py-3">
                            <p className="field-label">X Range</p>
                            <p className="mt-2 text-sm font-semibold text-ink">
                              {formatMetricValue(Math.min(...graphXValues), 3)} to{" "}
                              {formatMetricValue(Math.max(...graphXValues), 3)}
                            </p>
                          </div>
                          <div className="rounded-2xl border border-ink/8 bg-white/60 px-4 py-3">
                            <p className="field-label">Y Range</p>
                            <p className="mt-2 text-sm font-semibold text-ink">
                              {formatMetricValue(Math.min(...graphYValues), 3)} to{" "}
                              {formatMetricValue(Math.max(...graphYValues), 3)}
                            </p>
                          </div>
                        </div>
                      </>
                    ) : (
                      <div className="rounded-2xl border border-dashed border-ink/10 bg-white/50 px-4 py-10 text-center text-sm text-ink/60">
                        No data was returned for `{selectedGraphData}` on this chart.
                      </div>
                    )}
                  </div>
                </div>

                <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
                  <div className="rounded-[26px] border border-ink/10 bg-white/72 p-5">
                    <div className="flex items-center justify-between gap-4">
                      <div><p className="field-label">Metrics Ledger</p><h3 className="mt-2 font-display text-2xl text-ink">Scalar metrics</h3></div>
                      <button
                        className="rounded-full border border-ink/10 bg-paper px-3 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-ink/65 transition hover:border-ember/35 hover:text-emberDeep"
                        type="button"
                        onClick={() => setShowAllMetrics((value) => !value)}
                      >
                        {showAllMetrics ? "Collapse" : "Show All"}
                      </button>
                    </div>
                    <div className="mt-4 grid gap-3 sm:grid-cols-2">
                      {(showAllMetrics ? otherMetrics : otherMetrics.slice(0, 10)).map(([key, value]) => (
                        <div key={key} className="rounded-2xl border border-ink/8 bg-paper/60 px-4 py-3">
                          <p className="field-label">{formatMetricLabel(key)}</p>
                          <p className="mt-2 text-sm font-semibold text-ink">{formatMetricValue(value)}</p>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className="rounded-[26px] border border-ink/10 bg-paper/78 p-5">
                    <p className="field-label">Applied Options</p>
                    <h3 className="mt-2 font-display text-2xl text-ink">Runtime flags</h3>
                    <dl className="mt-4 space-y-4">
                      <div><dt className="field-label">Judgment preset</dt><dd className="mt-1 text-sm text-ink/75">{result.options.judgmentPreset}</dd></div>
                      <div><dt className="field-label">Life gauge</dt><dd className="mt-1 text-sm text-ink/75">{result.options.lifeGauge}</dd></div>
                      <div><dt className="field-label">Note line random</dt><dd className="mt-1 text-sm text-ink/75">{result.options.randomPlacement ? "Enabled" : "Disabled"}</dd></div>
                      <div><dt className="field-label">0Poor mode</dt><dd className="mt-1 text-sm text-ink/75">{result.options.zeroPoorMode ? "Enabled" : "Disabled"}</dd></div>
                    </dl>
                  </div>
                </div>

                {noteDiffMetrics ? (
                  <div className="rounded-[26px] border border-ink/10 bg-white/72 p-5">
                    <p className="field-label">Nested Metrics</p>
                    <h3 className="mt-2 font-display text-2xl text-ink">note_diff</h3>
                    <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                      {Object.entries(noteDiffMetrics).sort(([left], [right]) => left.localeCompare(right)).map(([key, value]) => (
                        <div key={key} className="rounded-2xl border border-ink/8 bg-paper/60 px-4 py-3">
                          <p className="field-label">{formatMetricLabel(key)}</p>
                          <p className="mt-2 text-sm font-semibold text-ink">{formatMetricValue(value)}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}

                <div className="grid gap-4 xl:grid-cols-2">
                  <div className="rounded-[26px] border border-ink/10 bg-[#181412] p-5 text-shell">
                    <p className="font-mono text-[0.7rem] uppercase tracking-[0.24em] text-shell/55">Engine Log</p>
                    <pre className="mt-4 max-h-[360px] overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-6 text-shell/85">
                      {result.log || "No stdout was emitted by the parser or calculator."}
                    </pre>
                  </div>
                  <div className="rounded-[26px] border border-ink/10 bg-[#fffdf8] p-5">
                    <p className="field-label">Full totalDiff JSON</p>
                    <pre className="mt-4 max-h-[360px] overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-6 text-ink/80">
                      {JSON.stringify(result.totalDiff, null, 2)}
                    </pre>
                  </div>
                </div>
              </div>
            ) : (
              <div className="flex min-h-[420px] items-center justify-center rounded-[28px] border border-dashed border-ink/12 bg-white/45 p-8 text-center">
                <div className="max-w-lg">
                  <p className="font-mono text-[0.72rem] uppercase tracking-[0.28em] text-emberDeep">
                    Waiting For Input
                  </p>
                  <h3 className="mt-3 font-display text-3xl text-ink">No chart has been calculated yet.</h3>
                  <p className="mt-4 text-sm leading-6 text-ink/65">
                    Upload a chart on the left, run the API, and this panel will show metadata,
                    featured metrics, the remaining scalar outputs, nested `note_diff`, and the
                    original Python log.
                  </p>
                </div>
              </div>
            )}
          </section>
        </main>
      </div>
    </div>
  );
}
