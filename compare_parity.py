from __future__ import annotations

import argparse
import contextlib
import io
import math
import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_APP_PATH = REPO_ROOT / "tools" / "10k-calc-web" / "backend" / "app.py"
CALC_ROOT = REPO_ROOT / "10k-calc"

if str(CALC_ROOT) not in sys.path:
    sys.path.insert(0, str(CALC_ROOT))

import bms_parser  # type: ignore  # noqa: E402
import new_calc  # type: ignore  # noqa: E402
import osu_parser  # type: ignore  # noqa: E402

CHART_EXTENSIONS = {".bms", ".bme", ".bml", ".pms", ".osu"}


def _load_backend_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("tenriff_10k_calc_web_backend_app", BACKEND_APP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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


def _scale_notes_like_main_gui(notes: list[Any], speed_rate: float) -> list[Any]:
    if speed_rate == 1.0:
        return notes

    scaled_notes: list[Any] = []
    for note in notes:
        if not isinstance(note, dict):
            scaled_notes.append(note)
            continue
        scaled = dict(note)
        scaled["time"] = round(_safe_float(note.get("time", 0.0), 0.0) / speed_rate, 9)
        scaled_notes.append(scaled)
    return scaled_notes


def _scale_sv_list_like_main_gui(sv_list: Any, speed_rate: float) -> Any:
    if speed_rate == 1.0 or not isinstance(sv_list, list):
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


def _parse_with_original_flow(path: Path) -> tuple[Any, bool]:
    if path.suffix.lower() == ".osu":
        parser = osu_parser.OsuParser(str(path))
        return parser, True
    parser = bms_parser.BMSParser(str(path))
    return parser, False


def _run_original_flow(
    path: Path,
    *,
    judgment_preset: str,
    life_gauge: str,
    speed_rate: float,
    random_placement: bool,
    zero_poor_mode: bool,
    backend_module: Any,
) -> dict[str, Any]:
    stdout_buffer = io.StringIO()
    with contextlib.redirect_stdout(stdout_buffer):
        parser, is_osu = _parse_with_original_flow(path)
        notes = parser.parse()
        duration = getattr(parser, "duration", 0.0)
        header = getattr(parser, "header", {}) if hasattr(parser, "header") else {}
        if not isinstance(header, dict):
            header = {}
        sv_list = getattr(parser, "sv_list", None)

        if speed_rate != 1.0:
            notes = _scale_notes_like_main_gui(notes, speed_rate)
            sv_list = _scale_sv_list_like_main_gui(sv_list, speed_rate)
            try:
                duration = float(duration) / speed_rate
            except Exception:
                pass

        preset_name = _resolve_preset_name_from_header(judgment_preset, header, is_osu=is_osu)
        total_diff = new_calc.calculate_total_difficulty(
            notes,
            duration,
            key_mode=getattr(parser, "key_count", None) or 7,
            preset_name=preset_name,
            mode_name=getattr(parser, "detected_mode", None),
            random_placement=bool(random_placement),
            life_gauge=life_gauge,
            sv_list=sv_list,
            zero_poor_mode=bool(zero_poor_mode),
            config=backend_module._load_config(),
            create_multiprocessing_workers=True,
        )

    return {
        "resolvedPreset": preset_name,
        "duration": round(float(duration), 6),
        "noteCount": len(notes),
        "keyCount": getattr(parser, "key_count", None),
        "modeName": getattr(parser, "detected_mode", None),
        "noteTimes": [
            _safe_float(note.get("time", 0.0), 0.0)
            for note in notes
            if isinstance(note, dict)
        ],
        "totalDiff": backend_module._jsonify_value(total_diff if isinstance(total_diff, dict) else {}),
    }


def _compare_values(expected: Any, actual: Any, path: str, diffs: list[str], limit: int) -> None:
    if len(diffs) >= limit:
        return

    if isinstance(expected, dict) and isinstance(actual, dict):
        expected_keys = set(expected.keys())
        actual_keys = set(actual.keys())
        for missing in sorted(expected_keys - actual_keys):
            diffs.append(f"{path}.{missing}: missing in actual")
            if len(diffs) >= limit:
                return
        for extra in sorted(actual_keys - expected_keys):
            diffs.append(f"{path}.{extra}: unexpected key in actual")
            if len(diffs) >= limit:
                return
        for key in sorted(expected_keys & actual_keys):
            _compare_values(expected[key], actual[key], f"{path}.{key}", diffs, limit)
        return

    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            diffs.append(f"{path}: length mismatch expected={len(expected)} actual={len(actual)}")
            if len(diffs) >= limit:
                return
        for index, (expected_item, actual_item) in enumerate(zip(expected, actual)):
            _compare_values(expected_item, actual_item, f"{path}[{index}]", diffs, limit)
            if len(diffs) >= limit:
                return
        return

    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if not math.isclose(float(expected), float(actual), rel_tol=1e-12, abs_tol=1e-12):
            diffs.append(f"{path}: expected={expected!r} actual={actual!r}")
        return

    if expected != actual:
        diffs.append(f"{path}: expected={expected!r} actual={actual!r}")


def _default_chart_paths() -> list[Path]:
    candidates = [
        REPO_ROOT / "P_DA.bme",
        REPO_ROOT
        / "krrcream-Toolkit-master"
        / "tests"
        / "TestOsuFile"
        / "Jumpstream - Happy Hardcore Synthesizer (SK_la) [10k-1].osu",
    ]
    return [path for path in candidates if path.exists()]


def _expand_chart_paths(inputs: list[str]) -> list[Path]:
    if not inputs:
        return _default_chart_paths()

    expanded: list[Path] = []
    seen: set[str] = set()
    for raw_input in inputs:
        path = Path(raw_input).resolve()
        if path.is_dir():
            iterator = sorted(child for child in path.rglob("*") if child.is_file() and child.suffix.lower() in CHART_EXTENSIONS)
        else:
            iterator = [path]
        for item in iterator:
            key = str(item).lower()
            if key in seen:
                continue
            seen.add(key)
            expanded.append(item)
    return expanded


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare 10k-calc web API output against the original calculation flow.")
    parser.add_argument("paths", nargs="*", help="Chart files or directories to compare. Directories are scanned recursively.")
    parser.add_argument("--limit-diffs", type=int, default=20, help="Maximum mismatch lines to print per case.")
    parser.add_argument("--stop-on-first-fail", action="store_true", help="Stop immediately when the first mismatch is found.")
    args = parser.parse_args()

    backend_module = _load_backend_module()
    client = TestClient(backend_module.app)
    chart_paths = _expand_chart_paths(args.paths)
    if not chart_paths:
        print("No chart files available for parity comparison.", file=sys.stderr)
        return 1

    cases = [
        {
            "judgment_preset": "auto_stable",
            "life_gauge": "Score % Acc %",
            "speed_rate": 1.00,
            "random_placement": False,
            "zero_poor_mode": False,
        },
        {
            "judgment_preset": "auto_lazer",
            "life_gauge": "Full Combo",
            "speed_rate": 1.25,
            "random_placement": True,
            "zero_poor_mode": False,
        },
        {
            "judgment_preset": "auto_stable",
            "life_gauge": "Perfect Play",
            "speed_rate": 0.85,
            "random_placement": False,
            "zero_poor_mode": True,
        },
    ]

    total_runs = 0
    failures: list[tuple[Path, dict[str, Any], list[str]]] = []
    for chart_path in chart_paths:
        for case in cases:
            total_runs += 1
            direct = _run_original_flow(chart_path, backend_module=backend_module, **case)
            with chart_path.open("rb") as stream:
                response = client.post(
                    "/api/calculate",
                    files={"file": (chart_path.name, stream, "text/plain")},
                    data={
                        "judgment_preset": case["judgment_preset"],
                        "life_gauge": case["life_gauge"],
                        "speed_rate": f"{case['speed_rate']:.2f}",
                        "random_placement": str(case["random_placement"]).lower(),
                        "zero_poor_mode": str(case["zero_poor_mode"]).lower(),
                    },
                )
            if response.status_code != 200:
                print(f"[FAIL] API returned {response.status_code} for {chart_path.name} / {case}", file=sys.stderr)
                try:
                    print(response.json(), file=sys.stderr)
                except Exception:
                    print(response.text, file=sys.stderr)
                return 1

            payload = response.json()
            actual = {
                "resolvedPreset": payload["resolvedPreset"],
                "duration": payload["duration"],
                "noteCount": payload["noteCount"],
                "keyCount": payload["keyCount"],
                "modeName": payload["modeName"],
                "noteTimes": payload["noteTimes"],
                "totalDiff": payload["totalDiff"],
            }

            diffs: list[str] = []
            _compare_values(direct, actual, "root", diffs, args.limit_diffs)
            if diffs:
                failures.append((chart_path, case, diffs))
                print(f"[FAIL] {chart_path.name} / {case}")
                for diff in diffs:
                    print(f"  - {diff}")
                if args.stop_on_first_fail:
                    return 1
                continue

            print(
                f"[PASS] {chart_path.name} / preset={case['judgment_preset']} / "
                f"gauge={case['life_gauge']} / speed={case['speed_rate']:.2f} / "
                f"random={case['random_placement']} / zeroPoor={case['zero_poor_mode']}"
            )

    pass_count = total_runs - len(failures)
    print("")
    print(f"Scanned charts : {len(chart_paths)}")
    print(f"Scanned cases  : {total_runs}")
    print(f"Passed cases   : {pass_count}")
    print(f"Failed cases   : {len(failures)}")
    if failures:
        print("Parity comparison found mismatches.", file=sys.stderr)
        return 1
    print("Parity comparison passed with no mismatches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
