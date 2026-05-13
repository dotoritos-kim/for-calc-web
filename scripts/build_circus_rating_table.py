from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import io
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
CALC_ROOT = REPO_ROOT / "10k-calc"
DEFAULT_PACK_DIR = Path(r"D:\10Key-Revive-pack")
DEFAULT_OUT_DIR = REPO_ROOT / "circus-rating-table"
CHART_EXTENSIONS = {".bms", ".bme", ".bml", ".pms", ".osu"}
OBJ_PATTERN = re.compile(r"\bobj(?:ecter)?[.:：]?\s*([^\s,\[\]()/]+)", re.IGNORECASE)

if str(CALC_ROOT) not in sys.path:
    sys.path.insert(0, str(CALC_ROOT))

import bms_parser  # type: ignore  # noqa: E402
import main_gui  # type: ignore  # noqa: E402


def _chart_paths(pack_dir: Path) -> list[Path]:
    return sorted(path for path in pack_dir.rglob("*") if path.is_file() and path.suffix.lower() in CHART_EXTENSIONS)


def _file_hashes(path: Path) -> tuple[str, str]:
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            md5.update(chunk)
            sha256.update(chunk)
    return md5.hexdigest(), sha256.hexdigest()


def _normalize_objecters(values: list[Any]) -> list[str]:
    objecters: list[str] = []
    for value in values:
        for match in OBJ_PATTERN.finditer(str(value or "")):
            item = match.group(1).strip(" .;:")
            if item and item not in objecters:
                objecters.append(item)
    return objecters


def _comment(parts: list[str], objecters: list[str]) -> str:
    clean_parts = [part.strip() for part in parts if part and part.strip()]
    clean_parts.extend(f"obj:{objecter}" for objecter in objecters)
    return " ".join(clean_parts)


def _format_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _format_gauge_recovery_percent(value: Any, total_notes: Any, chart_format: str = "bms") -> str:
    try:
        total = float(value)
        notes = float(total_notes)
    except (TypeError, ValueError):
        return ""
    if total <= 0 or notes <= 0:
        return ""
    if chart_format == "bmson":
        percent = abs(0.07605 * total / (0.01 * notes + 6.5))
    else:
        percent = total / notes
    return f"{_format_number(percent)}%"


def _header_for_bms(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".osu":
        return {}
    parser = bms_parser.BMSParser(str(path))
    with contextlib.redirect_stdout(io.StringIO()):
        parser.parse()
    header = getattr(parser, "header", {}) if hasattr(parser, "header") else {}
    return header if isinstance(header, dict) else {}


def _build_row(path: Path, config: dict[str, Any], pack_dir: Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    md5_hash, sha256_hash = _file_hashes(path)
    result = main_gui.analyze_file_summary_mp(
        str(path),
        None,
        "auto_stable",
        "Score % Acc %",
        False,
        False,
        config,
    )
    result["relative_path"] = str(path.relative_to(pack_dir))

    if result.get("status") != "Success":
        return None, result

    header = _header_for_bms(path)
    circus_rating = float(result.get("level", 0.0))
    rating_level = f"{circus_rating:.2f}"
    title = str(result.get("title_raw") or result.get("title") or path.stem).strip()
    artist = str(result.get("artist") or "").strip()
    name_diff = str(result.get("name_diff") or "").strip()
    objecters = _normalize_objecters([artist, name_diff, header.get("SUBARTIST"), header.get("SUBTITLE")])
    artist = OBJ_PATTERN.sub("", artist).strip(" /")
    playlevel = str(header.get("PLAYLEVEL") or header.get("playlevel") or "").strip()
    key_label = str(result.get("key_label") or "").strip()
    revive_lv = result.get("revive_lv", "")
    total_notes = result.get("notes")
    gauge_total = _format_gauge_recovery_percent(header.get("TOTAL", header.get("total")), total_notes)
    comment_parts = [
        name_diff,
        f"CR:{rating_level}",
        f"TOTAL:{gauge_total}" if gauge_total else "",
        f"ReviveLv:{revive_lv}" if revive_lv not in ("", None) else "",
        key_label,
        f"BMSLv:{playlevel}" if playlevel else "",
    ]
    row = {
        "md5": md5_hash,
        "sha256": sha256_hash,
        "title": title,
        "artist": artist,
        "level": rating_level,
        "comment": _comment(comment_parts, objecters),
        "gauge_total": gauge_total,
        "notes": total_notes,
    }
    result["circus_rating_level"] = rating_level
    result["gauge_total"] = gauge_total
    return row, result


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "relative_path",
        "status",
        "title",
        "artist",
        "name_diff",
        "key_label",
        "level",
        "circus_rating_level",
        "revive_lv",
        "gauge_total",
        "avg_nps",
        "peak_nps",
        "notes",
        "duration",
        "md5",
        "sha256",
        "error",
    ]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a BMSTable-compatible table using Circus Rating as the level.")
    parser.add_argument("--pack-dir", type=Path, default=DEFAULT_PACK_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--name", default="10Key Revive Pack Circus Rating")
    parser.add_argument("--symbol", default="Ⓒ")
    parser.add_argument("--data-url", default="body.json")
    args = parser.parse_args()

    pack_dir = args.pack_dir.resolve()
    out_dir = args.out_dir.resolve()
    if not pack_dir.exists():
        raise SystemExit(f"Pack directory not found: {pack_dir}")

    config_path = CALC_ROOT / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    paths = _chart_paths(pack_dir)
    if not paths:
        raise SystemExit(f"No chart files found: {pack_dir}")

    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    body_rows: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, path in enumerate(paths, start=1):
        print(f"[{index}/{len(paths)}] {path.relative_to(pack_dir)}", flush=True)
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                row, result = _build_row(path, config, pack_dir)
            result["calculator_log"] = stdout.getvalue().strip()
            results.append(result)
            if row is None:
                errors.append(result)
                continue
            body_rows.append(row)
        except Exception as exc:
            md5_hash, sha256_hash = _file_hashes(path)
            error = {
                "relative_path": str(path.relative_to(pack_dir)),
                "status": "Error",
                "error": str(exc),
                "md5": md5_hash,
                "sha256": sha256_hash,
                "calculator_log": stdout.getvalue().strip(),
            }
            results.append(error)
            errors.append(error)

    body_rows.sort(key=lambda row: (float(row["level"]), row["title"], row["artist"], row["md5"]))
    levels = [f"{level:.2f}" for level in sorted({float(row["level"]) for row in body_rows})]
    header = {
        "name": args.name,
        "symbol": args.symbol,
        "data_url": args.data_url,
        "level_order": levels,
        "enum_level_order": levels,
    }
    md5_values = [row["md5"].lower() for row in body_rows if row.get("md5")]
    sha_values = [row["sha256"].lower() for row in body_rows if row.get("sha256")]
    summary = {
        "pack_dir": str(pack_dir),
        "out_dir": str(out_dir),
        "chart_files": len(paths),
        "success": len(body_rows),
        "errors": len(errors),
        "level_count": len(levels),
        "level_min": levels[0] if levels else None,
        "level_max": levels[-1] if levels else None,
        "duplicate_md5": len(md5_values) - len(set(md5_values)),
        "duplicate_sha256": len(sha_values) - len(set(sha_values)),
        "key_counts": dict(sorted(Counter(str(result.get("key_label") or "") for result in results if result.get("status") == "Success").items())),
        "elapsed_seconds": round(time.time() - started, 3),
    }

    _write_json(out_dir / "header.json", header)
    _write_json(out_dir / "body.json", body_rows)
    _write_json(out_dir / "batch_results_circus_rating.json", results)
    _write_json(out_dir / "validation_summary.json", summary)
    _write_csv(out_dir / "batch_results_circus_rating.csv", results)
    (out_dir / "README.md").write_text(
        "\n".join(
            [
                "# 10Key Revive Pack Circus Rating Table",
                "",
                "Upload `header.json` and `body.json` as a separate BMSTable difficulty table.",
                "",
                "- `level` is the calculator Circus Rating rounded to 2 decimals.",
                "- `gauge_total` keeps the Qwilight per-note gauge recovery percentage computed from BMS `#TOTAL` and note count when available.",
                "- `comment` keeps the original chart diff name, Circus Rating, gauge recovery percentage, key mode, Revive Lv, BMS PLAYLEVEL, and obj credit when available.",
                "- `batch_results_circus_rating.json` and `.csv` are audit files, not required for upload.",
                "",
                f"Generated from `{pack_dir}`.",
                f"Rows: {len(body_rows)} success / {len(errors)} errors / {len(paths)} scanned.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
