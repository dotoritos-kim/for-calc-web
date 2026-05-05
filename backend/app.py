from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BACKEND_ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = BACKEND_ROOT.parent


def _find_repo_root() -> Path:
    for candidate in (PACKAGE_ROOT, *PACKAGE_ROOT.parents):
        if (candidate / "10k-calc").exists():
            return candidate
    if Path("/10k-calc").exists():
        return Path("/")
    return PACKAGE_ROOT


REPO_ROOT = _find_repo_root()
CALC_ROOT = REPO_ROOT / "10k-calc"
CONFIG_PATH = CALC_ROOT / "config.yaml"
TABLE_DIR = REPO_ROOT / "10key-table"
TABLE_HTML = PACKAGE_ROOT / "table" / "table.html"
LEVEL_VIEWER_HTML = PACKAGE_ROOT / "table" / "level-viewer.html"
ADMIN_HTML = PACKAGE_ROOT / "table" / "admin.html"
TABLE_ADMIN_TOKEN = os.getenv("TABLE_ADMIN_TOKEN", "").strip()
TABLE_ROW_FIELDS = ("md5", "sha256", "title", "artist", "level", "comment")
EDITABLE_TABLE_FIELDS = ("title", "artist", "level", "comment", "md5", "sha256")
OBJ_PATTERN = re.compile(r"\bobj(?:ecter)?\s*[:：]?\s*([^\s,\[\]()/]+)", re.IGNORECASE)

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


def _table_body_paths() -> list[Path]:
    candidates = [
        PACKAGE_ROOT / "body.json",
        PACKAGE_ROOT / "10key-table" / "body.json",
        PACKAGE_ROOT / "table" / "body.json",
        TABLE_DIR / "body.json",
    ]
    paths: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            key = candidate.resolve()
        except OSError:
            key = candidate
        if key in seen:
            continue
        seen.add(key)
        paths.append(candidate)
    return paths


def _primary_body_path() -> Path:
    for path in _table_body_paths():
        if path.exists():
            return path
    raise HTTPException(status_code=404, detail="body.json not found")


def _load_table_rows() -> list[dict[str, Any]]:
    try:
        with _primary_body_path().open("r", encoding="utf-8") as stream:
            rows = json.load(stream)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"body.json parse failed: {exc}") from exc
    if not isinstance(rows, list):
        raise HTTPException(status_code=500, detail="body.json root must be an array")
    return [row if isinstance(row, dict) else {} for row in rows]


def _write_table_rows(rows: list[dict[str, Any]]) -> list[str]:
    text = json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    written: list[str] = []
    for path in _table_body_paths():
        if not path.exists() and not path.parent.exists():
            continue
        tmp_path = path.with_name(f".{path.name}.tmp")
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(path)
        written.append(str(path))
    if not written:
        raise HTTPException(status_code=500, detail="No body.json paths were writable")
    return written


def _extract_objecters_from_row(row: dict[str, Any]) -> list[str]:
    values = [str(row.get(key, "")) for key in ("title", "artist", "comment")]
    found: list[str] = []
    for match in OBJ_PATTERN.finditer(" ".join(values)):
        value = match.group(1).strip(" .;:")
        if value and value not in found:
            found.append(value)
    return found


def _normalize_objecter_values(value: Any) -> list[str]:
    if value is None:
        return []
    raw_values = value if isinstance(value, list) else [value]
    objecters: list[str] = []
    for raw_value in raw_values:
        text = str(raw_value or "").strip()
        if not text:
            continue
        matches = list(OBJ_PATTERN.finditer(text))
        candidates = [match.group(1) for match in matches] if matches else re.split(r"[,/;\s]+", text)
        for candidate in candidates:
            objecter = re.sub(r"^obj(?:ecter)?\s*[:：]?", "", str(candidate), flags=re.IGNORECASE).strip(" .;:")
            if objecter and objecter not in objecters:
                objecters.append(objecter)
    return objecters


def _sync_comment_objecters(comment: str, objecters: list[str]) -> str:
    comment_without_obj = OBJ_PATTERN.sub("", comment)
    comment_without_obj = re.sub(r"\s+", " ", comment_without_obj).strip(" /")
    markers = " ".join(f"obj:{objecter}" for objecter in objecters)
    return f"{comment_without_obj} {markers}".strip()


def _payload_objecters(payload: dict[str, Any]) -> list[str] | None:
    if "objecter" in payload:
        return _normalize_objecter_values(payload.get("objecter"))
    if "obj" in payload:
        return _normalize_objecter_values(payload.get("obj"))
    return None


def _validate_table_row(row: dict[str, Any]) -> None:
    title = str(row.get("title", "")).strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    if "level" not in row or str(row.get("level", "")).strip() == "":
        raise HTTPException(status_code=400, detail="level is required")
    try:
        level_value = int(str(row["level"]))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="level must be an integer string") from exc
    if level_value < 1 or level_value > 99:
        raise HTTPException(status_code=400, detail="level must be between 1 and 99")
    row["level"] = str(level_value)


def _find_duplicate_hash(rows: list[dict[str, Any]], row: dict[str, Any]) -> str | None:
    for field in ("md5", "sha256"):
        value = str(row.get(field, "")).strip().lower()
        if not value:
            continue
        for existing in rows:
            if str(existing.get(field, "")).strip().lower() == value:
                return field
    return None


def _public_table_row(index: int, row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload["_index"] = index
    payload["_objecters"] = _extract_objecters_from_row(row)
    return payload


def _require_admin_token(x_admin_token: str | None) -> None:
    if TABLE_ADMIN_TOKEN and x_admin_token != TABLE_ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid admin token")


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


@app.get("/api/table/body")
def table_body() -> dict[str, Any]:
    rows = _load_table_rows()
    return {
        "rows": [_public_table_row(index, row) for index, row in enumerate(rows)],
        "count": len(rows),
        "adminTokenRequired": bool(TABLE_ADMIN_TOKEN),
        "source": str(_primary_body_path()),
    }


@app.post("/api/table/body")
def create_table_body_row(
    payload: dict[str, Any] = Body(...),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    _require_admin_token(x_admin_token)
    rows = _load_table_rows()
    row = {
        field: "" if payload.get(field) is None else str(payload.get(field, "")).strip()
        for field in TABLE_ROW_FIELDS
    }
    objecters = _payload_objecters(payload)
    if objecters is not None:
        row["comment"] = _sync_comment_objecters(row.get("comment", ""), objecters)
    _validate_table_row(row)
    duplicate_field = _find_duplicate_hash(rows, row)
    if duplicate_field:
        raise HTTPException(status_code=409, detail=f"{duplicate_field} already exists")

    rows.append(row)
    written = _write_table_rows(rows)
    row_index = len(rows) - 1
    return {
        "ok": True,
        "row": _public_table_row(row_index, row),
        "index": row_index,
        "written": written,
    }


@app.patch("/api/table/body/{row_index}")
def update_table_body_row(
    row_index: int,
    payload: dict[str, Any] = Body(...),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    _require_admin_token(x_admin_token)
    rows = _load_table_rows()
    if row_index < 0 or row_index >= len(rows):
        raise HTTPException(status_code=404, detail="Row not found")

    row = dict(rows[row_index])
    changed: dict[str, dict[str, str]] = {}
    for field in EDITABLE_TABLE_FIELDS:
        if field not in payload:
            continue
        next_value = "" if payload[field] is None else str(payload[field]).strip()
        previous_value = "" if row.get(field) is None else str(row.get(field))
        if next_value != previous_value:
            row[field] = next_value
            changed[field] = {"before": previous_value, "after": next_value}

    objecters = _payload_objecters(payload)
    if objecters is not None:
        previous_comment = "" if row.get("comment") is None else str(row.get("comment"))
        next_comment = _sync_comment_objecters(previous_comment, objecters)
        if next_comment != previous_comment:
            row["comment"] = next_comment
            changed["objecter"] = {"before": previous_comment, "after": next_comment}

    _validate_table_row(row)

    rows[row_index] = row
    written = _write_table_rows(rows) if changed else []
    return {
        "ok": True,
        "changed": changed,
        "row": _public_table_row(row_index, row),
        "written": written,
    }


@app.get("/table.html")
def serve_table_html() -> FileResponse:
    if not TABLE_HTML.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(TABLE_HTML, media_type="text/html")


@app.get("/table/{filename:path}")
def serve_table(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    if safe_name == "level-viewer.html":
        if not LEVEL_VIEWER_HTML.exists():
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(LEVEL_VIEWER_HTML, media_type="text/html")
    if safe_name == "admin.html":
        if not ADMIN_HTML.exists():
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(ADMIN_HTML, media_type="text/html")
    if safe_name not in ("header.json", "body.json"):
        raise HTTPException(status_code=404, detail="Not found")
    file_path = TABLE_DIR / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(file_path, media_type="application/json")


_STATIC_DIR = Path("/app/static")
if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
