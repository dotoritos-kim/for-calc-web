from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

REPO_ROOT = Path(__file__).resolve().parents[3]
CALC_ROOT = REPO_ROOT / "10k-calc"
CONFIG_PATH = CALC_ROOT / "config.yaml"

if str(CALC_ROOT) not in sys.path:
    sys.path.insert(0, str(CALC_ROOT))

import bms_parser  # type: ignore  # noqa: E402
import new_calc  # type: ignore  # noqa: E402
import osu_parser  # type: ignore  # noqa: E402

app = FastAPI(title="10k-calc Web API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_EXTENSIONS = {".bms", ".bme", ".bml", ".pms", ".osu"}
LIFE_GAUGES = [
    {"token": "Score % Acc %", "label": "Score % Acc %"},
    {"token": "Full Combo", "label": "Full Combo"},
    {"token": "Perfect Play", "label": "Perfect Play"},
]
AUTO_PRESETS = [
    {"token": "auto_stable", "label": "Auto Stable"},
    {"token": "auto_lazer", "label": "Auto Lazer"},
]
GRAPH_DATA_OPTIONS = [
    "note_score_diff",
    "note_acc_diff",
    "note_jack_diff_score",
    "note_jack_diff_acc",
    "j75",
    "j100",
    "j125",
    "j150",
    "jack_nps_v2",
    "jack_interval",
    "jack_score_uniformity",
    "jack_acc_uniformity",
    "fds",
    "fda",
    "rds",
    "rda",
    "lfds",
    "lfda",
    "lrds",
    "lrda",
    "distance_difficulty",
    "minimum_distance_sum",
    "vrs",
    "vra",
    "ldb",
    "ldbd",
    "nps",
    "nps_v2",
    "sv_list",
]


def _batch_key_label(key_count: int | None, mode_name: str | None) -> str | None:
    if mode_name == "10+2K":
        return "10K2S"
    if mode_name == "DP16":
        return "14K2S"
    if mode_name == "5+1":
        return "5K1S"
    if mode_name == "7+1":
        return "7K1S"
    if isinstance(key_count, int):
        if key_count == 12:
            return "10K2S"
        if key_count == 16:
            return "14K2S"
        return f"{key_count}K"
    return None


def _extract_title_from_header(header: dict[str, Any], fallback: str = "") -> str:
    for key in ("Title", "TITLE", "title", "TitleUnicode"):
        value = header.get(key)
        if value:
            return str(value).strip()
    return fallback


def _extract_artist_from_header(header: dict[str, Any], fallback: str = "") -> str:
    for key in ("ArtistUnicode", "Artist", "ARTIST", "artist"):
        value = header.get(key)
        if value:
            return str(value).strip()
    return fallback


def _extract_version_from_header(header: dict[str, Any]) -> str:
    for key in ("Version", "VERSION", "version"):
        value = header.get(key)
        if value:
            return str(value).strip()
    return ""


def _extract_name_diff_from_header(header: dict[str, Any], is_osu: bool) -> str:
    if is_osu:
        return _extract_version_from_header(header)
    for key in ("SUBTITLE", "SUB_TITLE", "PLAYLEVEL", "DIFFICULTY"):
        value = header.get(key)
        if not value:
            continue
        if key == "PLAYLEVEL":
            return f"[LV.{str(value).strip()}]"
        return str(value).strip()
    return ""


def _build_display_title(header: dict[str, Any], fallback: str, is_osu: bool) -> str:
    title = _extract_title_from_header(header, fallback)
    if is_osu:
        version = _extract_version_from_header(header)
        if version:
            return f"{title} [{version}]"
    return title


def _resolve_preset_name_from_header(judgment_preset_value: str, header: dict[str, Any], is_osu: bool) -> str:
    preset_name = str(judgment_preset_value or "").strip()
    if not preset_name.startswith("auto_"):
        return preset_name

    if is_osu:
        try:
            od = float(header.get("OverallDifficulty", 8.0))
        except Exception:
            od = 8.0
        if preset_name == "auto_stable":
            return f"osu_od_interpolate_{od}"
        return f"osu_lazer_od_interpolate_{od}"

    try:
        rank_value = int(str(header.get("RANK", header.get("rank"))).strip())
    except Exception:
        rank_value = None

    if rank_value == 0:
        return "qwilight_bms_vh"
    if rank_value == 1:
        return "qwilight_bms_hd"
    if rank_value == 2:
        return "qwilight_bms_nm"
    return "qwilight_bms_ez"


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _scale_notes_like_main_gui(notes: list[dict[str, Any]], speed_rate: float) -> list[dict[str, Any]]:
    if speed_rate == 1.0:
        return notes

    scaled_notes: list[dict[str, Any]] = []
    for note in notes:
        if not isinstance(note, dict):
            scaled_notes.append(note)
            continue
        scaled = dict(note)
        scaled["time"] = round(_safe_float(note.get("time", 0.0), 0.0) / speed_rate, 9)
        scaled_notes.append(scaled)
    return scaled_notes


def _scale_sv_list(sv_list: list[list[float]] | None, speed_rate: float) -> list[list[float]] | None:
    if not isinstance(sv_list, list) or speed_rate == 1.0:
        return sv_list

    scaled: list[list[float]] = []
    for entry in sv_list:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        try:
            scaled.append([float(entry[0]) / speed_rate, float(entry[1])])
        except Exception:
            continue
    return scaled


def _scalarize_metrics(total_diff: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for key, value in total_diff.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            metrics[key] = value
            continue
        if key == "note_diff" and isinstance(value, dict):
            note_diff_scalars = {
                child_key: child_value
                for child_key, child_value in value.items()
                if isinstance(child_value, (str, int, float, bool)) or child_value is None
            }
            if note_diff_scalars:
                metrics[key] = note_diff_scalars
    return metrics


def _jsonify_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonify_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify_value(child) for child in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return float(value)
    if isinstance(value, str):
        return value
    if hasattr(value, "item"):
        try:
            return _jsonify_value(value.item())
        except Exception:
            pass
    return str(value)


@lru_cache(maxsize=1)
def _load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    if not isinstance(data, dict):
        return {}
    return data


def _list_preset_options() -> list[dict[str, str]]:
    config = _load_config()
    preset_items = list(AUTO_PRESETS)
    judgment_presets = config.get("judgment_presets", {})
    if isinstance(judgment_presets, dict):
        for token in sorted(judgment_presets.keys()):
            value = judgment_presets.get(token)
            label = token
            if isinstance(value, dict) and value.get("name"):
                label = str(value["name"])
            preset_items.append({"token": str(token), "label": label})
    return preset_items


def _parse_chart(path: Path) -> dict[str, Any]:
    extension = path.suffix.lower()
    if extension == ".osu":
        parser = osu_parser.OsuParser(str(path))
        is_osu = True
    else:
        parser = bms_parser.BMSParser(str(path))
        is_osu = False

    notes = parser.parse()
    duration = getattr(parser, "duration", 0.0)
    if duration is None:
        duration = 0.0
    key_count = getattr(parser, "key_count", None)
    mode_name = getattr(parser, "detected_mode", None)
    header = getattr(parser, "header", {}) if hasattr(parser, "header") else {}
    if not isinstance(header, dict):
        header = {}

    title = _build_display_title(header, path.name, is_osu=is_osu)
    title_raw = _extract_title_from_header(header, path.name)
    artist = _extract_artist_from_header(header, "")
    name_diff = _extract_name_diff_from_header(header, is_osu=is_osu)
    sv_list = getattr(parser, "sv_list", None)

    return {
        "notes": notes,
        "duration": float(duration),
        "key_count": key_count,
        "mode_name": mode_name,
        "key_label": _batch_key_label(key_count, mode_name),
        "header": header,
        "title": title,
        "title_raw": title_raw,
        "artist": artist,
        "name_diff": name_diff,
        "format": "osu" if is_osu else "bms",
        "is_osu": is_osu,
        "sv_list": sv_list,
        "note_times": [
            _safe_float(note.get("time", 0.0), 0.0)
            for note in notes
            if isinstance(note, dict)
        ],
    }


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True}


@app.get("/api/options")
def options() -> dict[str, Any]:
    return {
        "presets": _list_preset_options(),
        "lifeGauges": LIFE_GAUGES,
        "graphDataOptions": GRAPH_DATA_OPTIONS,
        "defaults": {
            "preset": "auto_stable",
            "lifeGauge": "Score % Acc %",
            "speedRate": 1.0,
            "speedRateMin": 0.5,
            "speedRateMax": 2.0,
            "randomPlacement": False,
            "zeroPoorMode": False,
        },
        "acceptedExtensions": sorted(ALLOWED_EXTENSIONS),
    }


@app.post("/api/calculate")
async def calculate(
    file: UploadFile = File(...),
    judgment_preset: str = Form("auto_stable"),
    life_gauge: str = Form("Score % Acc %"),
    speed_rate: float = Form(1.0),
    random_placement: bool = Form(False),
    zero_poor_mode: bool = Form(False),
) -> dict[str, Any]:
    filename = file.filename or "chart"
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {extension or '(none)'}")

    speed_rate = max(0.5, min(2.0, float(speed_rate)))

    gauge_value = life_gauge if any(item["token"] == life_gauge for item in LIFE_GAUGES) else "Score % Acc %"
    upload_data = await file.read()
    if not upload_data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    temp_path: Path | None = None
    log_output = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as temp_file:
            temp_file.write(upload_data)
            temp_path = Path(temp_file.name)

        stdout_buffer = io.StringIO()
        with contextlib.redirect_stdout(stdout_buffer):
            parsed = _parse_chart(temp_path)
            notes = parsed["notes"]
            if not notes:
                raise HTTPException(status_code=400, detail="No notes were found in the uploaded chart.")

            scaled_notes = _scale_notes_like_main_gui(notes, speed_rate)
            scaled_duration = parsed["duration"] / speed_rate if speed_rate != 1.0 else parsed["duration"]
            scaled_sv_list = _scale_sv_list(parsed["sv_list"], speed_rate)
            resolved_preset = _resolve_preset_name_from_header(
                judgment_preset,
                parsed["header"],
                is_osu=bool(parsed["is_osu"]),
            )
            total_diff = new_calc.calculate_total_difficulty(
                scaled_notes,
                scaled_duration,
                key_mode=parsed["key_count"] or 7,
                preset_name=resolved_preset,
                mode_name=parsed["mode_name"],
                random_placement=bool(random_placement),
                life_gauge=gauge_value,
                sv_list=scaled_sv_list,
                zero_poor_mode=bool(zero_poor_mode),
                config=_load_config(),
                create_multiprocessing_workers=True,
            )
        log_output = stdout_buffer.getvalue().strip()
        total_diff_json = _jsonify_value(total_diff if isinstance(total_diff, dict) else {})
        note_times = [
            _safe_float(note.get("time", 0.0), 0.0)
            for note in scaled_notes
            if isinstance(note, dict)
        ]

        return {
            "fileName": filename,
            "format": parsed["format"],
            "title": parsed["title"],
            "titleRaw": parsed["title_raw"],
            "artist": parsed["artist"],
            "nameDiff": parsed["name_diff"],
            "keyCount": parsed["key_count"],
            "modeName": parsed["mode_name"],
            "keyLabel": parsed["key_label"],
            "noteCount": len(scaled_notes),
            "duration": round(float(scaled_duration), 6),
            "resolvedPreset": resolved_preset,
            "options": {
                "judgmentPreset": judgment_preset,
                "lifeGauge": gauge_value,
                "speedRate": speed_rate,
                "randomPlacement": bool(random_placement),
                "zeroPoorMode": bool(zero_poor_mode),
            },
            "metrics": _scalarize_metrics(total_diff_json if isinstance(total_diff_json, dict) else {}),
            "totalDiff": total_diff_json,
            "noteTimes": note_times,
            "log": log_output,
        }
    finally:
        if temp_path and temp_path.exists():
            try:
                os.unlink(temp_path)
            except OSError:
                pass


_STATIC_DIR = Path("/app/static")
if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
