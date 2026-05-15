from __future__ import annotations

import asyncio
import contextlib
import hashlib
import html
import hmac
import io
import json
import os
import re
import secrets
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from fastapi import Body, Cookie, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
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


def _env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    value = raw_value.strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_path(name: str, default: Path) -> Path:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    value = raw_value.strip()
    if not value or value == ".":
        return default
    return Path(value)


REPO_ROOT = _find_repo_root()
CALC_ROOT = REPO_ROOT / "10k-calc"
CONFIG_PATH = CALC_ROOT / "config.yaml"
TABLE_DIR = REPO_ROOT / "10key-table"
TABLE_HTML = PACKAGE_ROOT / "table" / "table.html"
LEVEL_VIEWER_HTML = PACKAGE_ROOT / "table" / "level-viewer.html"
ADMIN_HTML = PACKAGE_ROOT / "table" / "admin.html"
DUAL_TABLE_DIR = PACKAGE_ROOT / "dual-difficulty-table-upload"
TABLE_ADMIN_TOKEN = os.getenv("TABLE_ADMIN_TOKEN", "").strip()
ADMIN_AUTH_DB_PATH = _env_path("TABLE_ADMIN_AUTH_DB", PACKAGE_ROOT / ".admin_auth.json")
ADMIN_AUDIT_LOG_PATH = _env_path("TABLE_ADMIN_AUDIT_LOG", PACKAGE_ROOT / ".admin_audit.jsonl")
ADMIN_SESSION_COOKIE = "table_admin_session"
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
DISCORD_ADMIN_CHANNEL_ID = os.getenv("DISCORD_ADMIN_CHANNEL_ID", "").strip()
DISCORD_APPROVAL_WEBHOOK_URL = os.getenv("DISCORD_APPROVAL_WEBHOOK_URL", "").strip()
DISCORD_APPLICATION_ID = os.getenv("DISCORD_APPLICATION_ID", "").strip()
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "").strip()
DISCORD_ADMIN_USER_IDS = {
    item.strip()
    for item in os.getenv("DISCORD_ADMIN_USER_IDS", "").split(",")
    if item.strip()
}
DISCORD_UPLOAD_DB_PATH = _env_path("DISCORD_UPLOAD_DB", PACKAGE_ROOT / ".discord_uploads.json")
DISCORD_UPLOAD_MAX_BYTES = _env_int("DISCORD_UPLOAD_MAX_BYTES", 24 * 1024 * 1024)
DISCORD_GATEWAY_ENABLED = os.getenv("DISCORD_GATEWAY_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
DISCORD_REGISTER_COMMANDS = os.getenv("DISCORD_REGISTER_COMMANDS", "1").strip().lower() not in ("0", "false", "no", "off")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
PASSWORD_HASH_ITERATIONS = 210_000
SESSION_TTL_SECONDS = _env_int("TABLE_ADMIN_SESSION_TTL_SECONDS", 60 * 60 * 24 * 30)
APPROVAL_TTL_SECONDS = _env_int("TABLE_ADMIN_APPROVAL_TTL_SECONDS", 60 * 60 * 24 * 7)
DISCORD_UPLOAD_TTL_SECONDS = _env_int("DISCORD_UPLOAD_TTL_SECONDS", 60 * 60 * 24 * 7)
TABLE_ROW_FIELDS = ("md5", "sha256", "title", "artist", "level", "comment")
EDITABLE_TABLE_FIELDS = ("title", "artist", "level", "comment", "md5", "sha256")
OBJ_PATTERN = re.compile(r"\bobj(?:ecter)?\s*[:：]?\s*([^\s,\[\]()/]+)", re.IGNORECASE)
LOGIN_ID_PATTERN = re.compile(r"^[a-z0-9_.-]{3,32}$")
DISCORD_API_BASE = "https://discord.com/api/v10"

if str(CALC_ROOT) not in sys.path:
    sys.path.insert(0, str(CALC_ROOT))

import bms_parser  # type: ignore  # noqa: E402
import new_calc  # type: ignore  # noqa: E402
import osu_parser  # type: ignore  # noqa: E402

try:
    import websockets  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency guard
    websockets = None

app = FastAPI(title="10k-calc Web API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_EXTENSIONS = {".bms", ".bme", ".bml", ".pms", ".osu"}
DISCORD_UPLOAD_EXTENSIONS = {".bms", ".bme", ".bml", ".pms"}
BMS_10K_DIRECTIVE_PATTERN = re.compile(br"(?im)^\s*#10K(?:\s|$)")
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


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _empty_auth_state() -> dict[str, Any]:
    return {"users": {}, "pending": {}, "denied": {}, "secret": ""}


def _load_auth_state() -> dict[str, Any]:
    if not ADMIN_AUTH_DB_PATH.exists():
        return _empty_auth_state()
    try:
        with ADMIN_AUTH_DB_PATH.open("r", encoding="utf-8") as stream:
            state = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"admin auth database read failed: {exc}") from exc
    if not isinstance(state, dict):
        raise HTTPException(status_code=500, detail="admin auth database root must be an object")
    users = state.get("users")
    pending = state.get("pending")
    denied = state.get("denied")
    state["users"] = users if isinstance(users, dict) else {}
    state["pending"] = pending if isinstance(pending, dict) else {}
    state["denied"] = denied if isinstance(denied, dict) else {}
    state["secret"] = str(state.get("secret") or "")
    return state


def _save_auth_state(state: dict[str, Any]) -> None:
    ADMIN_AUTH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = ADMIN_AUTH_DB_PATH.with_name(f".{ADMIN_AUTH_DB_PATH.name}.tmp")
    text = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(ADMIN_AUTH_DB_PATH)


def _request_meta(request: Request | None) -> dict[str, str]:
    if request is None:
        return {}
    return {
        "method": request.method,
        "path": request.url.path,
        "client": request.client.host if request.client else "",
        "user_agent": request.headers.get("user-agent", ""),
    }


def _append_admin_audit(event: str, request: Request | None = None, user: dict[str, Any] | None = None, **details: Any) -> None:
    ADMIN_AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _utc_now_text(),
        "event": event,
        "request": _request_meta(request),
        "user": {
            "loginId": str((user or {}).get("loginId") or ""),
            "displayName": str((user or {}).get("displayName") or ""),
        },
        "details": details,
    }
    with ADMIN_AUDIT_LOG_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")


def _audit_session_user(session_token: str | None) -> dict[str, str] | None:
    try:
        return _session_user(session_token)
    except HTTPException:
        return None


def _ensure_auth_secret(state: dict[str, Any]) -> bool:
    if state.get("secret"):
        return False
    state["secret"] = secrets.token_urlsafe(48)
    return True


def _cleanup_expired_pending(state: dict[str, Any]) -> bool:
    now = time.time()
    pending = state.get("pending", {})
    expired: list[str] = []
    for request_id, item in pending.items():
        if not isinstance(item, dict):
            expired.append(request_id)
            continue
        try:
            expires_at = float(item.get("expires_at", 0) or 0)
        except (TypeError, ValueError):
            expires_at = 0
        if expires_at < now:
            expired.append(request_id)
    for request_id in expired:
        pending.pop(request_id, None)
    return bool(expired)


def _normalize_login_id(value: Any) -> str:
    login_id = str(value or "").strip().lower()
    if not LOGIN_ID_PATTERN.fullmatch(login_id):
        raise HTTPException(
            status_code=400,
            detail="loginId must be 3-32 chars: lowercase letters, numbers, dot, dash, underscore",
        )
    return login_id


def _normalize_display_name(value: Any, login_id: str) -> str:
    display_name = str(value or "").strip() or login_id
    display_name = re.sub(r"\s+", " ", display_name)
    if len(display_name) > 80:
        raise HTTPException(status_code=400, detail="displayName must be 80 chars or less")
    return display_name


def _password_from_payload(payload: dict[str, Any]) -> str:
    password = str(payload.get("password") or "")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 chars")
    if len(password) > 256:
        raise HTTPException(status_code=400, detail="password is too long")
    return password


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        PASSWORD_HASH_ITERATIONS,
    ).hex()
    return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt}${digest}"


def _verify_password(password: str, encoded: Any) -> bool:
    try:
        algorithm, iterations_text, salt, expected = str(encoded).split("$", 3)
        iterations = int(iterations_text)
        salt_bytes = bytes.fromhex(salt)
    except (TypeError, ValueError):
        return False
    if algorithm != "pbkdf2_sha256" or iterations < 100_000:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_bytes,
        iterations,
    ).hex()
    return hmac.compare_digest(digest, expected)


def _public_user(login_id: str, user: dict[str, Any]) -> dict[str, str]:
    return {
        "loginId": login_id,
        "displayName": str(user.get("display_name") or login_id),
        "approvedAt": str(user.get("approved_at") or ""),
    }


def _make_session_token(login_id: str, state: dict[str, Any]) -> str:
    _ensure_auth_secret(state)
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    body = f"{login_id}|{expires_at}"
    signature = hmac.new(str(state["secret"]).encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{body}|{signature}"


def _session_user(session_token: str | None, state: dict[str, Any] | None = None) -> dict[str, str] | None:
    if not session_token:
        return None
    state = state or _load_auth_state()
    secret = str(state.get("secret") or "")
    if not secret:
        return None
    try:
        login_id, expires_text, signature = session_token.split("|", 2)
        expires_at = int(expires_text)
    except ValueError:
        return None
    if expires_at < int(time.time()):
        return None
    body = f"{login_id}|{expires_at}"
    expected = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    user = state.get("users", {}).get(login_id)
    if not isinstance(user, dict) or not user.get("approved_at"):
        return None
    return _public_user(login_id, user)


def _approval_configured() -> bool:
    return bool(DISCORD_APPROVAL_WEBHOOK_URL or (DISCORD_BOT_TOKEN and DISCORD_ADMIN_CHANNEL_ID))


def _discord_admin_channel_id() -> str:
    channel_id = DISCORD_ADMIN_CHANNEL_ID.strip()
    if channel_id.isdigit():
        return channel_id
    matches = re.findall(r"\d{15,25}", channel_id)
    return matches[-1] if matches else channel_id


def _public_base_url(request: Request) -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    return str(request.base_url).rstrip("/")


def _send_discord_approval(pending: dict[str, Any], approve_url: str, admin_url: str) -> None:
    content = (
        "10Key table admin signup approval request\n"
        f"Login ID: `{pending['login_id']}`\n"
        f"Name: {pending['display_name']}\n"
        f"Approve: {approve_url}\n"
        f"Admin page: {admin_url}"
    )
    payload = {"content": content, "allowed_mentions": {"parse": []}}
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "for-calc-web-admin-auth",
    }
    if DISCORD_BOT_TOKEN and DISCORD_ADMIN_CHANNEL_ID:
        endpoint = f"https://discord.com/api/v10/channels/{_discord_admin_channel_id()}/messages"
        headers["Authorization"] = f"Bot {DISCORD_BOT_TOKEN}"
    elif DISCORD_APPROVAL_WEBHOOK_URL:
        endpoint = DISCORD_APPROVAL_WEBHOOK_URL
    else:
        raise HTTPException(status_code=503, detail="Discord approval is not configured")

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status >= 300:
                raise HTTPException(status_code=502, detail=f"Discord returned HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise HTTPException(status_code=502, detail=f"Discord approval message failed: {detail}") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"Discord approval message failed: {exc.reason}") from exc


def _empty_discord_upload_state() -> dict[str, Any]:
    return {"pending": {}, "history": {}, "ownership": {}}


def _load_discord_upload_state() -> dict[str, Any]:
    if not DISCORD_UPLOAD_DB_PATH.exists():
        return _empty_discord_upload_state()
    try:
        with DISCORD_UPLOAD_DB_PATH.open("r", encoding="utf-8") as stream:
            state = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"discord upload database read failed: {exc}") from exc
    if not isinstance(state, dict):
        raise HTTPException(status_code=500, detail="discord upload database root must be an object")
    for key in ("pending", "history", "ownership"):
        if not isinstance(state.get(key), dict):
            state[key] = {}
    return state


def _save_discord_upload_state(state: dict[str, Any]) -> None:
    DISCORD_UPLOAD_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = DISCORD_UPLOAD_DB_PATH.with_name(f".{DISCORD_UPLOAD_DB_PATH.name}.tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(DISCORD_UPLOAD_DB_PATH)


def _cleanup_expired_discord_uploads(state: dict[str, Any]) -> bool:
    now = time.time()
    expired: list[str] = []
    for upload_id, item in state.get("pending", {}).items():
        if not isinstance(item, dict):
            expired.append(upload_id)
            continue
        try:
            expires_at = float(item.get("expires_at", 0) or 0)
        except (TypeError, ValueError):
            expires_at = 0
        if expires_at < now:
            expired.append(upload_id)
    for upload_id in expired:
        item = state["pending"].pop(upload_id, {})
        if isinstance(item, dict):
            item["status"] = "expired"
            item["expired_at"] = _utc_now_text()
            state["history"][upload_id] = item
    return bool(expired)


def _file_hashes(path: Path) -> tuple[str, str]:
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            md5.update(chunk)
            sha256.update(chunk)
    return md5.hexdigest(), sha256.hexdigest()


def _format_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _format_cr_level(value: float) -> str:
    return f"{float(value):.2f}"


def _format_gauge_recovery_percent(total_value: Any, total_notes: Any, chart_format: str = "bms") -> str:
    try:
        total = float(str(total_value).strip().rstrip("%"))
        notes = float(total_notes)
    except (TypeError, ValueError):
        return ""
    if total <= 0 or notes <= 0:
        return ""
    if str(chart_format).lower().find("bmson") >= 0:
        percent = abs(0.07605 * total / (0.01 * notes + 6.5))
    else:
        percent = total / notes
    return f"{_format_number(percent)}%"


def _strip_cr_markers(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"\bCR\s*[:：]\s*[0-9]+(?:\.[0-9]+)?", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" /")


def _truncate_text(value: Any, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _discord_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "for-calc-web-discord-bot",
    }


def _discord_api_request(method: str, path: str, payload: Any | None = None, retry: bool = True) -> Any:
    if not DISCORD_BOT_TOKEN:
        raise HTTPException(status_code=503, detail="DISCORD_BOT_TOKEN is not configured")
    endpoint = path if path.startswith("http") else f"{DISCORD_API_BASE}{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(endpoint, data=data, headers=_discord_headers(), method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
            if not body:
                return {}
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        if exc.code == 429 and retry:
            try:
                retry_after = float(json.loads(detail).get("retry_after", 1.0))
            except Exception:
                retry_after = 1.0
            time.sleep(min(max(retry_after, 0.25), 5.0))
            return _discord_api_request(method, path, payload, retry=False)
        raise HTTPException(status_code=502, detail=f"Discord API HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"Discord API failed: {exc.reason}") from exc


_discord_runtime_application_id = DISCORD_APPLICATION_ID
_discord_gateway_task: asyncio.Task[Any] | None = None


def _discord_application_id() -> str:
    global _discord_runtime_application_id
    if _discord_runtime_application_id:
        return _discord_runtime_application_id
    me = _discord_api_request("GET", "/users/@me")
    app_id = str(me.get("id") or "")
    if not app_id:
        raise HTTPException(status_code=502, detail="Discord application id could not be resolved")
    _discord_runtime_application_id = app_id
    return app_id


def _discord_command_payloads() -> list[dict[str, Any]]:
    return [
        {
            "name": "업로드",
            "description": "차분 파일을 분석하고 어드민 승인 후 추가합니다. #10K가 없으면 자동 추가합니다.",
            "dm_permission": False,
            "options": [
                {
                    "type": 11,
                    "name": "파일",
                    "description": "업로드할 .bms/.bme/.bml/.pms 파일. #10K가 있으면 변경하지 않습니다.",
                    "required": True,
                },
                {
                    "type": 3,
                    "name": "코멘트",
                    "description": "테이블 comment에 추가할 내용",
                    "required": False,
                    "max_length": 500,
                },
                {
                    "type": 4,
                    "name": "난이도",
                    "description": "자동 산출 대신 사용할 Revive Lv",
                    "required": False,
                    "min_value": 1,
                    "max_value": 99,
                },
            ],
        },
        {
            "name": "차분",
            "description": "내가 올린 차분을 조회하거나 표기를 수정합니다.",
            "dm_permission": False,
            "options": [
                {
                    "type": 1,
                    "name": "목록",
                    "description": "내가 수정할 수 있는 차분 목록을 봅니다.",
                },
                {
                    "type": 1,
                    "name": "표기수정",
                    "description": "업로드한 사람 또는 어드민이 난이도/코멘트를 수정합니다.",
                    "options": [
                        {
                            "type": 4,
                            "name": "번호",
                            "description": "목록에 표시된 테이블 번호",
                            "required": True,
                            "min_value": 0,
                        },
                        {
                            "type": 4,
                            "name": "난이도",
                            "description": "새 Revive Lv",
                            "required": False,
                            "min_value": 1,
                            "max_value": 99,
                        },
                        {
                            "type": 3,
                            "name": "코멘트",
                            "description": "새 comment",
                            "required": False,
                            "max_length": 500,
                        },
                    ],
                },
            ],
        },
        {
            "name": "랜덤",
            "description": "10키 테이블에서 할 패턴을 랜덤으로 추천합니다.",
            "dm_permission": False,
            "options": [
                {
                    "type": 4,
                    "name": "난이도",
                    "description": "특정 Revive Lv 안에서만 추천합니다.",
                    "required": False,
                    "min_value": 1,
                    "max_value": 99,
                },
                {
                    "type": 4,
                    "name": "개수",
                    "description": "추천할 패턴 수",
                    "required": False,
                    "min_value": 1,
                    "max_value": 5,
                },
            ],
        },
    ]


def _discord_registration_guild_id() -> str:
    if DISCORD_GUILD_ID:
        return DISCORD_GUILD_ID
    channel_id = _discord_admin_channel_id()
    if not channel_id:
        return ""
    try:
        channel = _discord_api_request("GET", f"/channels/{channel_id}")
    except HTTPException:
        return ""
    return str(channel.get("guild_id") or "")


def _discord_register_commands() -> None:
    if not DISCORD_REGISTER_COMMANDS or not DISCORD_BOT_TOKEN:
        return
    app_id = _discord_application_id()
    guild_id = _discord_registration_guild_id()
    if guild_id:
        path = f"/applications/{app_id}/guilds/{guild_id}/commands"
    else:
        path = f"/applications/{app_id}/commands"
    _discord_api_request("PUT", path, _discord_command_payloads())
    scope = f"guild {guild_id}" if guild_id else "global"
    print(f"[discord] slash commands registered ({scope})", flush=True)


def _discord_interaction_callback(interaction: dict[str, Any], payload: dict[str, Any]) -> Any:
    return _discord_api_request(
        "POST",
        f"/interactions/{interaction['id']}/{interaction['token']}/callback",
        payload,
    )


def _discord_interaction_app_id(interaction: dict[str, Any]) -> str:
    app_id = str(interaction.get("application_id") or "")
    return app_id or _discord_application_id()


def _discord_edit_original(interaction: dict[str, Any], content: str, components: list[Any] | None = None) -> Any:
    payload: dict[str, Any] = {
        "content": _truncate_text(content, 1900),
        "allowed_mentions": {"parse": []},
    }
    if components is not None:
        payload["components"] = components
    app_id = _discord_interaction_app_id(interaction)
    return _discord_api_request("PATCH", f"/webhooks/{app_id}/{interaction['token']}/messages/@original", payload)


def _discord_followup(interaction: dict[str, Any], content: str, ephemeral: bool = True) -> Any:
    payload: dict[str, Any] = {
        "content": _truncate_text(content, 1900),
        "allowed_mentions": {"parse": []},
    }
    if ephemeral:
        payload["flags"] = 64
    app_id = _discord_interaction_app_id(interaction)
    return _discord_api_request("POST", f"/webhooks/{app_id}/{interaction['token']}", payload)


def _discord_patch_message(channel_id: str, message_id: str, content: str, components: list[Any] | None = None) -> Any:
    payload: dict[str, Any] = {
        "content": _truncate_text(content, 1900),
        "allowed_mentions": {"parse": []},
    }
    if components is not None:
        payload["components"] = components
    return _discord_api_request("PATCH", f"/channels/{channel_id}/messages/{message_id}", payload)


def _discord_send_channel_message(channel_id: str, content: str, components: list[Any] | None = None) -> Any:
    payload: dict[str, Any] = {
        "content": _truncate_text(content, 1900),
        "allowed_mentions": {"parse": ["users"]},
    }
    if components is not None:
        payload["components"] = components
    return _discord_api_request("POST", f"/channels/{channel_id}/messages", payload)


def _discord_user(interaction: dict[str, Any]) -> dict[str, Any]:
    member = interaction.get("member")
    if isinstance(member, dict) and isinstance(member.get("user"), dict):
        return member["user"]
    user = interaction.get("user")
    return user if isinstance(user, dict) else {}


def _discord_user_id(interaction: dict[str, Any]) -> str:
    return str(_discord_user(interaction).get("id") or "")


def _discord_user_name(interaction: dict[str, Any]) -> str:
    user = _discord_user(interaction)
    return str(user.get("global_name") or user.get("username") or user.get("id") or "unknown")


def _discord_audit_user(interaction: dict[str, Any]) -> dict[str, str]:
    user_id = _discord_user_id(interaction)
    return {"loginId": f"discord:{user_id}", "displayName": _discord_user_name(interaction)}


def _discord_is_admin(interaction: dict[str, Any]) -> bool:
    user_id = _discord_user_id(interaction)
    if user_id and user_id in DISCORD_ADMIN_USER_IDS:
        return True
    member = interaction.get("member")
    permissions_text = str(member.get("permissions") if isinstance(member, dict) else "0")
    try:
        permissions = int(permissions_text)
    except ValueError:
        permissions = 0
    administrator = 1 << 3
    manage_guild = 1 << 5
    manage_messages = 1 << 13
    return bool(permissions & (administrator | manage_guild | manage_messages))


def _discord_option_map(options: list[dict[str, Any]] | None) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for option in options or []:
        if not isinstance(option, dict):
            continue
        mapped[str(option.get("name") or "")] = option.get("value")
    return mapped


def _discord_modal_value(interaction: dict[str, Any], custom_id: str) -> str:
    for row in interaction.get("data", {}).get("components", []) or []:
        for component in row.get("components", []) or []:
            if component.get("custom_id") == custom_id:
                return str(component.get("value") or "").strip()
    return ""


def _download_discord_attachment(attachment: dict[str, Any]) -> bytes:
    url = str(attachment.get("url") or "")
    filename = str(attachment.get("filename") or "chart")
    size = int(attachment.get("size") or 0)
    if not url:
        raise HTTPException(status_code=400, detail="attachment URL is missing")
    if size and size > DISCORD_UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=400, detail=f"{filename} is larger than the upload limit")
    request = urllib.request.Request(url, headers={"User-Agent": "for-calc-web-discord-bot"})
    data = bytearray()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > DISCORD_UPLOAD_MAX_BYTES:
                    raise HTTPException(status_code=400, detail=f"{filename} is larger than the upload limit")
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"attachment download failed: {exc.reason}") from exc
    if not data:
        raise HTTPException(status_code=400, detail="uploaded attachment is empty")
    return bytes(data)


def _ensure_discord_upload_10k_directive(data: bytes, extension: str) -> tuple[bytes, bool]:
    if extension.lower() not in DISCORD_UPLOAD_EXTENSIONS:
        return data, False
    if BMS_10K_DIRECTIVE_PATTERN.search(data):
        return data, False
    return b"#10K\r\n" + data, True


def _discord_upload_10k_notice(analysis: dict[str, Any]) -> str:
    if analysis.get("added10KDirective"):
        return "#10K 처리: 원본 파일에 #10K가 없어 업로드 분석용 파일에 추가했습니다."
    return "#10K 처리: 원본 파일에 #10K가 있으면 변경하지 않습니다."


def _row_owner_key(row: dict[str, Any]) -> str:
    md5 = str(row.get("md5") or "").strip().lower()
    if md5:
        return f"md5:{md5}"
    sha256 = str(row.get("sha256") or "").strip().lower()
    if sha256:
        return f"sha256:{sha256}"
    return ""


def _row_matches_owner_key(row: dict[str, Any], owner_key: str) -> bool:
    kind, _, value = owner_key.partition(":")
    if not value:
        return False
    if kind == "md5":
        return str(row.get("md5") or "").strip().lower() == value
    if kind == "sha256":
        return str(row.get("sha256") or "").strip().lower() == value
    return False


def _explicit_objecters_from_values(values: list[Any]) -> list[str]:
    objecters: list[str] = []
    for value in values:
        for match in OBJ_PATTERN.finditer(str(value or "")):
            objecter = match.group(1).strip(" .;:")
            if objecter and objecter not in objecters:
                objecters.append(objecter)
    return objecters


def _discord_history_by_owner_key(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for item in state.get("history", {}).values():
        if not isinstance(item, dict) or item.get("status") != "approved":
            continue
        row = item.get("row")
        if not isinstance(row, dict):
            continue
        owner_key = _row_owner_key(row)
        if owner_key:
            mapped[owner_key] = item
    return mapped


def _discord_row_cr_level(row: dict[str, Any], history_item: dict[str, Any] | None = None) -> str:
    row_value = str(row.get("cr_level") or "").strip()
    if row_value:
        return row_value
    analysis = history_item.get("analysis", {}) if isinstance(history_item, dict) else {}
    if isinstance(analysis, dict):
        if analysis.get("crLevel") not in (None, ""):
            return str(analysis["crLevel"])
        if analysis.get("circusRating") not in (None, ""):
            try:
                return _format_cr_level(float(analysis["circusRating"]))
            except (TypeError, ValueError):
                pass
    return ""


def _build_discord_upload_row(
    path: Path,
    original_filename: str,
    upload_comment: str,
    level_override: int | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    parsed = _parse_chart(path)
    notes = parsed["notes"]
    if not notes:
        raise HTTPException(status_code=400, detail="No notes were found in the uploaded chart.")
    total_diff = new_calc.calculate_total_difficulty(
        notes,
        parsed["duration"] if parsed["duration"] > 0 else 1.0,
        key_mode=parsed["key_count"] or 7,
        preset_name=_resolve_preset_name_from_header("auto_stable", parsed["header"], is_osu=bool(parsed["is_osu"])),
        mode_name=parsed["mode_name"],
        random_placement=False,
        life_gauge="Score % Acc %",
        sv_list=parsed["sv_list"],
        zero_poor_mode=False,
        config=_load_config(),
        create_multiprocessing_workers=True,
    )
    revive_lv = int(total_diff.get("revive_lv") or 0) if isinstance(total_diff, dict) else 0
    circus_rating = float(total_diff.get("circus_rating") or 0.0) if isinstance(total_diff, dict) else 0.0
    cr_level = _format_cr_level(circus_rating)
    level = int(level_override) if level_override is not None else revive_lv
    if level <= 0:
        raise HTTPException(status_code=400, detail="Revive Lv could not be calculated. Use the 난이도 option.")

    md5_hash, sha256_hash = _file_hashes(path)
    header = parsed["header"]
    title = str(parsed["title_raw"] or parsed["title"] or Path(original_filename).stem).strip()
    artist = str(parsed["artist"] or "").strip()
    name_diff = str(parsed["name_diff"] or "").strip()
    key_label = str(parsed["key_label"] or "").strip()
    playlevel = str(header.get("PLAYLEVEL") or header.get("playlevel") or "").strip()
    objecters = _explicit_objecters_from_values([
        artist,
        name_diff,
        upload_comment,
        header.get("SUBARTIST"),
        header.get("SUBTITLE"),
    ])
    artist = OBJ_PATTERN.sub("", artist).strip(" /")
    comment_parts = [
        name_diff,
        _strip_cr_markers(upload_comment),
        key_label,
        f"BMSLv:{playlevel}" if playlevel else "",
    ]
    comment = _sync_comment_objecters(" ".join(part for part in comment_parts if part), objecters)
    note_count = len(notes)
    gauge_total = _format_gauge_recovery_percent(header.get("TOTAL", header.get("total")), note_count, parsed["format"])
    row = {
        "md5": md5_hash,
        "sha256": sha256_hash,
        "title": title,
        "artist": artist,
        "level": str(level),
        "comment": comment,
        "cr_level": cr_level,
        "gauge_total": gauge_total,
        "notes": note_count,
    }
    _validate_table_row(row)
    return row, {
        "reviveLv": revive_lv,
        "circusRating": circus_rating,
        "crLevel": cr_level,
        "avgNps": float(total_diff.get("global_nps") or 0.0) if isinstance(total_diff, dict) else 0.0,
        "peakNps": int(total_diff.get("peak_nps") or 0) if isinstance(total_diff, dict) else 0,
        "keyLabel": key_label,
        "fileName": original_filename,
    }


def _discord_upload_summary(row: dict[str, Any], analysis: dict[str, Any]) -> str:
    cr_level = _discord_row_cr_level(row, {"analysis": analysis})
    return (
        f"제목: {row.get('title') or '-'}\n"
        f"아티스트: {row.get('artist') or '-'}\n"
        f"난이도: Lv.{row.get('level') or '-'}\n"
        f"CR 레벨: {cr_level}\n"
        f"코멘트: {_truncate_text(_strip_cr_markers(row.get('comment')), 220) or '-'}\n"
        f"노트: {row.get('notes') or '-'} / 회복: {row.get('gauge_total') or '-'}\n"
        f"{_discord_upload_10k_notice(analysis)}\n"
        f"MD5: `{row.get('md5') or '-'}`"
    )


def _send_discord_upload_approval(upload_id: str, pending: dict[str, Any]) -> dict[str, Any]:
    if not DISCORD_ADMIN_CHANNEL_ID:
        raise HTTPException(status_code=503, detail="DISCORD_ADMIN_CHANNEL_ID is not configured")
    row = pending["row"]
    analysis = pending.get("analysis", {})
    uploader = pending.get("uploader", {})
    content = (
        "차분 업로드 승인 요청\n"
        f"요청자: <@{uploader.get('id')}> ({uploader.get('name')})\n"
        f"파일: `{pending.get('filename')}`\n"
        f"{_discord_upload_summary(row, analysis)}"
    )
    components = [
        {
            "type": 1,
            "components": [
                {"type": 2, "style": 3, "label": "승인", "custom_id": f"upload_approve:{upload_id}"},
                {"type": 2, "style": 4, "label": "거부", "custom_id": f"upload_deny:{upload_id}"},
            ],
        }
    ]
    return _discord_send_channel_message(_discord_admin_channel_id(), content, components)


def _discord_prepare_upload(interaction: dict[str, Any]) -> str:
    options = _discord_option_map(interaction.get("data", {}).get("options"))
    attachment_id = str(options.get("파일") or "")
    comment = str(options.get("코멘트") or "").strip()
    level_override = options.get("난이도")
    if level_override is not None:
        level_override = int(level_override)
    attachments = interaction.get("data", {}).get("resolved", {}).get("attachments", {})
    attachment = attachments.get(attachment_id)
    if not isinstance(attachment, dict):
        raise HTTPException(status_code=400, detail="uploaded attachment could not be resolved")
    filename = str(attachment.get("filename") or "chart")
    extension = Path(filename).suffix.lower()
    if extension not in DISCORD_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Discord upload only accepts BMS-family files: .bms, .bme, .bml, .pms")

    upload_data = _download_discord_attachment(attachment)
    upload_data, added_10k_directive = _ensure_discord_upload_10k_directive(upload_data, extension)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as temp_file:
            temp_file.write(upload_data)
            temp_path = Path(temp_file.name)
        stdout_buffer = io.StringIO()
        with contextlib.redirect_stdout(stdout_buffer):
            row, analysis = _build_discord_upload_row(temp_path, filename, comment, level_override)
            analysis["added10KDirective"] = added_10k_directive
    finally:
        if temp_path and temp_path.exists():
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    rows = _load_table_rows()
    duplicate_field = _find_duplicate_hash(rows, row)
    if duplicate_field:
        raise HTTPException(status_code=409, detail=f"{duplicate_field} already exists in the table")

    upload_id = secrets.token_urlsafe(9)
    state = _load_discord_upload_state()
    _cleanup_expired_discord_uploads(state)
    pending = {
        "id": upload_id,
        "status": "pending",
        "row": row,
        "analysis": analysis,
        "filename": filename,
        "uploader": {"id": _discord_user_id(interaction), "name": _discord_user_name(interaction)},
        "source_channel_id": str(interaction.get("channel_id") or ""),
        "created_at": _utc_now_text(),
        "expires_at": time.time() + DISCORD_UPLOAD_TTL_SECONDS,
    }
    state["pending"][upload_id] = pending
    _save_discord_upload_state(state)
    approval_message = _send_discord_upload_approval(upload_id, pending)
    state = _load_discord_upload_state()
    if isinstance(state.get("pending", {}).get(upload_id), dict):
        state["pending"][upload_id]["approval_message_id"] = str(approval_message.get("id") or "")
        _save_discord_upload_state(state)
    _append_admin_audit(
        "discord_upload_requested",
        None,
        _discord_audit_user(interaction),
        uploadId=upload_id,
        row=row,
    )
    return (
        "분석 완료. 어드민 채널 승인 대기 중입니다.\n"
        f"{_discord_upload_summary(row, analysis)}"
    )


def _discord_approve_pending_upload(upload_id: str, interaction: dict[str, Any]) -> dict[str, Any]:
    state = _load_discord_upload_state()
    _cleanup_expired_discord_uploads(state)
    pending = state.get("pending", {}).get(upload_id)
    if not isinstance(pending, dict):
        raise HTTPException(status_code=404, detail="upload request is missing or already handled")
    row = dict(pending.get("row") or {})
    _validate_table_row(row)
    rows = _load_table_rows()
    duplicate_field = _find_duplicate_hash(rows, row)
    if duplicate_field:
        raise HTTPException(status_code=409, detail=f"{duplicate_field} already exists in the table")
    rows.append(row)
    written = _write_table_rows(rows)
    written.extend(_write_circus_table_row(row))
    row_index = len(rows) - 1
    owner_key = _row_owner_key(row)
    if owner_key:
        state["ownership"][owner_key] = {
            "user_id": str(pending.get("uploader", {}).get("id") or ""),
            "user_name": str(pending.get("uploader", {}).get("name") or ""),
            "row_index": row_index,
            "uploaded_at": _utc_now_text(),
        }
    pending["status"] = "approved"
    pending["approved_at"] = _utc_now_text()
    pending["approved_by"] = {"id": _discord_user_id(interaction), "name": _discord_user_name(interaction)}
    pending["row_index"] = row_index
    pending["written"] = written
    state["history"][upload_id] = pending
    state["pending"].pop(upload_id, None)
    _save_discord_upload_state(state)
    _append_admin_audit(
        "discord_upload_approved",
        None,
        _discord_audit_user(interaction),
        uploadId=upload_id,
        index=row_index,
        row=_public_table_row(row_index, row),
        written=written,
    )
    return {"pending": pending, "row": row, "index": row_index}


def _discord_deny_pending_upload(upload_id: str, interaction: dict[str, Any], reason: str) -> dict[str, Any]:
    state = _load_discord_upload_state()
    _cleanup_expired_discord_uploads(state)
    pending = state.get("pending", {}).get(upload_id)
    if not isinstance(pending, dict):
        raise HTTPException(status_code=404, detail="upload request is missing or already handled")
    pending["status"] = "denied"
    pending["denied_at"] = _utc_now_text()
    pending["denied_by"] = {"id": _discord_user_id(interaction), "name": _discord_user_name(interaction)}
    pending["denial_reason"] = str(reason or "").strip()[:500]
    state["history"][upload_id] = pending
    state["pending"].pop(upload_id, None)
    _save_discord_upload_state(state)
    _append_admin_audit(
        "discord_upload_denied",
        None,
        _discord_audit_user(interaction),
        uploadId=upload_id,
        reason=pending["denial_reason"],
        row=pending.get("row"),
    )
    return {"pending": pending}


def _discord_admin_upload_message(result: dict[str, Any], status: str) -> str:
    pending = result["pending"]
    row = pending.get("row", result.get("row", {}))
    analysis = pending.get("analysis", {})
    uploader = pending.get("uploader", {})
    if status == "approved":
        return (
            "차분 업로드 승인 완료\n"
            f"요청자: <@{uploader.get('id')}> ({uploader.get('name')})\n"
            f"테이블 번호: {result.get('index')}\n"
            f"{_discord_upload_summary(row, analysis)}"
        )
    reason = pending.get("denial_reason") or "사유 없음"
    return (
        "차분 업로드 거부 완료\n"
        f"요청자: <@{uploader.get('id')}> ({uploader.get('name')})\n"
        f"사유: {_truncate_text(reason, 300)}\n"
        f"{_discord_upload_summary(row, analysis)}"
    )


def _discord_notify_upload_result(result: dict[str, Any], status: str) -> None:
    pending = result["pending"]
    channel_id = str(pending.get("source_channel_id") or "")
    uploader_id = str(pending.get("uploader", {}).get("id") or "")
    if not channel_id or channel_id == _discord_admin_channel_id():
        return
    if status == "approved":
        content = f"<@{uploader_id}> 업로드가 승인되어 테이블에 추가됐습니다. 번호: {result.get('index')}"
    else:
        reason = pending.get("denial_reason") or "사유 없음"
        content = f"<@{uploader_id}> 업로드가 거부됐습니다. 사유: {_truncate_text(reason, 300)}"
    _discord_send_channel_message(channel_id, content)


def _discord_rows_for_user(interaction: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _load_table_rows()
    state = _load_discord_upload_state()
    history_by_owner_key = _discord_history_by_owner_key(state)
    is_admin = _discord_is_admin(interaction)
    user_id = _discord_user_id(interaction)
    result: list[dict[str, Any]] = []
    for owner_key, owner in state.get("ownership", {}).items():
        if not isinstance(owner, dict):
            continue
        if not is_admin and str(owner.get("user_id") or "") != user_id:
            continue
        matched_index = -1
        matched_row: dict[str, Any] | None = None
        for index, row in enumerate(rows):
            if _row_matches_owner_key(row, str(owner_key)):
                matched_index = index
                matched_row = row
                break
        if matched_row is None:
            continue
        history_item = history_by_owner_key.get(str(owner_key), {})
        result.append(
            {
                "index": matched_index,
                "row": matched_row,
                "owner": owner,
                "history": history_item,
                "uploaded_at": str(owner.get("uploaded_at") or history_item.get("approved_at") or ""),
            }
        )
    result.sort(key=lambda item: str(item.get("uploaded_at") or ""), reverse=True)
    return result


def _discord_build_row_list(interaction: dict[str, Any]) -> str:
    records = _discord_rows_for_user(interaction)
    if not records:
        return "표시할 차분이 없습니다."
    is_admin = _discord_is_admin(interaction)
    lines = [f"{'등록 차분 목록' if is_admin else '내 업로드 차분 목록'} (총 {len(records)}개)"]
    for item in records[:10]:
        index = int(item["index"])
        row = item["row"]
        owner = item.get("owner", {})
        history_item = item.get("history", {})
        cr_level = _discord_row_cr_level(row, history_item)
        title = _truncate_text(row.get("title"), 42) or "-"
        artist = _truncate_text(row.get("artist"), 28) or "-"
        comment = _truncate_text(_strip_cr_markers(row.get("comment")), 84) or "-"
        line = f"#{index} | Revive Lv.{row.get('level') or '-'} | CR {cr_level or '-'} | {title} - {artist}"
        if is_admin:
            uploader = _truncate_text(owner.get("user_name"), 24) or str(owner.get("user_id") or "-")
            line += f" | 업로더 {uploader}"
        lines.append(line)
        lines.append(f"  코멘트: {comment}")
    if len(records) > 10:
        lines.append(f"...외 {len(records) - 10}개")
    if is_admin:
        lines.append("/차분 표기수정 번호:<번호> 난이도:<값> 코멘트:<내용>")
    else:
        lines.append("표기 수정은 서버 어드민만 가능합니다.")
    return "\n".join(lines)


def _table_row_level_int(row: dict[str, Any]) -> int | None:
    try:
        return int(str(row.get("level") or "").strip())
    except (TypeError, ValueError):
        return None


def _discord_random_row_line(index: int, row: dict[str, Any]) -> str:
    title = _truncate_text(row.get("title"), 48) or "-"
    artist = _truncate_text(row.get("artist"), 28) or "-"
    level = str(row.get("level") or "-").strip() or "-"
    cr_level = _discord_row_cr_level(row)
    comment = _truncate_text(_strip_cr_markers(row.get("comment")), 72) or "-"
    heading = f"#{index} | Revive Lv.{level}"
    details = []
    if cr_level:
        details.append(f"CR {cr_level}")
    if row.get("notes") not in (None, ""):
        details.append(f"{row.get('notes')} notes")
    if row.get("gauge_total") not in (None, ""):
        details.append(f"TOTAL {row.get('gauge_total')}")
    lines = [
        heading,
        f"{title} - {artist}",
    ]
    if details:
        lines.append(" / ".join(details))
    lines.append(f"코멘트: {comment}")
    return "\n".join(lines)


def _truncate_discord_message(value: Any, limit: int = 1900) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _discord_build_random_recommendation(interaction: dict[str, Any]) -> str:
    options = _discord_option_map(interaction.get("data", {}).get("options"))
    level_filter = int(options["난이도"]) if options.get("난이도") is not None else None
    count = int(options.get("개수") or 1)
    count = max(1, min(5, count))
    candidates: list[tuple[int, dict[str, Any]]] = []
    for index, row in enumerate(_load_table_rows()):
        if not str(row.get("title") or "").strip():
            continue
        if level_filter is not None and _table_row_level_int(row) != level_filter:
            continue
        candidates.append((index, row))
    if not candidates:
        if level_filter is not None:
            return f"Revive Lv.{level_filter}에서 추천할 10키 패턴이 없습니다."
        return "추천할 10키 패턴이 없습니다."
    selected = secrets.SystemRandom().sample(candidates, min(count, len(candidates)))
    title = "10키 랜덤 추천"
    if level_filter is not None:
        title += f" (Revive Lv.{level_filter})"
    lines = [title, f"후보 {len(candidates)}개 중 {len(selected)}개"]
    for position, (index, row) in enumerate(selected, 1):
        lines.append(f"{position}. {_discord_random_row_line(index, row)}")
    return "\n\n".join(lines)


def _discord_update_row_marking(interaction: dict[str, Any], options: dict[str, Any]) -> str:
    if not _discord_is_admin(interaction):
        raise HTTPException(status_code=403, detail="Only server admins can edit row labels")
    row_index = int(options.get("번호"))
    rows = _load_table_rows()
    if row_index < 0 or row_index >= len(rows):
        raise HTTPException(status_code=404, detail="Row not found")
    row = dict(rows[row_index])
    changed: dict[str, dict[str, str]] = {}
    if options.get("난이도") is not None:
        before = str(row.get("level") or "")
        after = str(int(options["난이도"]))
        if after != before:
            row["level"] = after
            changed["level"] = {"before": before, "after": after}
    if options.get("코멘트") is not None:
        before = str(row.get("comment") or "")
        after = _strip_cr_markers(options["코멘트"])
        if after != before:
            row["comment"] = after
            changed["comment"] = {"before": before, "after": after}
    if not changed:
        return "변경할 난이도나 코멘트를 입력해 주세요."
    _validate_table_row(row)
    rows[row_index] = row
    written = _write_table_rows(rows)
    written.extend(_write_circus_table_row(row))
    _append_admin_audit(
        "discord_table_row_updated",
        None,
        _discord_audit_user(interaction),
        index=row_index,
        title=str(row.get("title", "")),
        changed=changed,
        written=written,
    )
    return (
        "표기 수정 완료\n"
        f"번호: {row_index}\n"
        f"난이도: Lv.{row.get('level')}\n"
        f"코멘트: {_truncate_text(row.get('comment'), 300) or '-'}"
    )


async def _discord_handle_upload_command(interaction: dict[str, Any]) -> None:
    await asyncio.to_thread(
        _discord_interaction_callback,
        interaction,
        {"type": 5, "data": {"flags": 64}},
    )
    try:
        message = await asyncio.to_thread(_discord_prepare_upload, interaction)
        await asyncio.to_thread(_discord_edit_original, interaction, message)
    except HTTPException as exc:
        await asyncio.to_thread(_discord_edit_original, interaction, f"업로드 실패: {exc.detail}")
    except Exception as exc:
        await asyncio.to_thread(_discord_edit_original, interaction, f"업로드 실패: {exc}")


async def _discord_handle_list_command(interaction: dict[str, Any]) -> None:
    try:
        message = await asyncio.to_thread(_discord_build_row_list, interaction)
    except HTTPException as exc:
        message = f"목록 조회 실패: {exc.detail}"
    await asyncio.to_thread(
        _discord_interaction_callback,
        interaction,
        {"type": 4, "data": {"content": _truncate_text(message, 1900), "flags": 64}},
    )


async def _discord_handle_marking_command(interaction: dict[str, Any], options: dict[str, Any]) -> None:
    try:
        message = await asyncio.to_thread(_discord_update_row_marking, interaction, options)
    except HTTPException as exc:
        message = f"수정 실패: {exc.detail}"
    except Exception as exc:
        message = f"수정 실패: {exc}"
    await asyncio.to_thread(
        _discord_interaction_callback,
        interaction,
        {"type": 4, "data": {"content": _truncate_text(message, 1900), "flags": 64}},
    )


async def _discord_handle_random_command(interaction: dict[str, Any]) -> None:
    ephemeral = False
    try:
        message = await asyncio.to_thread(_discord_build_random_recommendation, interaction)
    except HTTPException as exc:
        message = f"랜덤 추천 실패: {exc.detail}"
        ephemeral = True
    except Exception as exc:
        message = f"랜덤 추천 실패: {exc}"
        ephemeral = True
    data: dict[str, Any] = {"content": _truncate_discord_message(message), "allowed_mentions": {"parse": []}}
    if ephemeral:
        data["flags"] = 64
    await asyncio.to_thread(
        _discord_interaction_callback,
        interaction,
        {"type": 4, "data": data},
    )


async def _discord_handle_component(interaction: dict[str, Any]) -> None:
    custom_id = str(interaction.get("data", {}).get("custom_id") or "")
    if not custom_id.startswith(("upload_approve:", "upload_deny:")):
        return
    if not _discord_is_admin(interaction):
        await asyncio.to_thread(
            _discord_interaction_callback,
            interaction,
            {"type": 4, "data": {"content": "어드민만 처리할 수 있습니다.", "flags": 64}},
        )
        return
    action, upload_id = custom_id.split(":", 1)
    if action == "upload_deny":
        modal = {
            "type": 9,
            "data": {
                "custom_id": f"upload_deny_modal:{upload_id}",
                "title": "업로드 거부 사유",
                "components": [
                    {
                        "type": 1,
                        "components": [
                            {
                                "type": 4,
                                "custom_id": "reason",
                                "style": 2,
                                "label": "사유",
                                "required": False,
                                "max_length": 500,
                            }
                        ],
                    }
                ],
            },
        }
        await asyncio.to_thread(_discord_interaction_callback, interaction, modal)
        return

    await asyncio.to_thread(_discord_interaction_callback, interaction, {"type": 6})
    try:
        result = await asyncio.to_thread(_discord_approve_pending_upload, upload_id, interaction)
        message_id = str(result["pending"].get("approval_message_id") or "")
        if message_id:
            await asyncio.to_thread(
                _discord_patch_message,
                _discord_admin_channel_id(),
                message_id,
                _discord_admin_upload_message(result, "approved"),
                [],
            )
        await asyncio.to_thread(_discord_notify_upload_result, result, "approved")
    except HTTPException as exc:
        await asyncio.to_thread(_discord_followup, interaction, f"승인 실패: {exc.detail}", True)
    except Exception as exc:
        await asyncio.to_thread(_discord_followup, interaction, f"승인 실패: {exc}", True)


async def _discord_handle_deny_modal(interaction: dict[str, Any]) -> None:
    custom_id = str(interaction.get("data", {}).get("custom_id") or "")
    if not custom_id.startswith("upload_deny_modal:"):
        return
    upload_id = custom_id.split(":", 1)[1]
    if not _discord_is_admin(interaction):
        await asyncio.to_thread(
            _discord_interaction_callback,
            interaction,
            {"type": 4, "data": {"content": "어드민만 처리할 수 있습니다.", "flags": 64}},
        )
        return
    await asyncio.to_thread(
        _discord_interaction_callback,
        interaction,
        {"type": 5, "data": {"flags": 64}},
    )
    reason = _discord_modal_value(interaction, "reason")
    try:
        result = await asyncio.to_thread(_discord_deny_pending_upload, upload_id, interaction, reason)
        message_id = str(result["pending"].get("approval_message_id") or "")
        if message_id:
            await asyncio.to_thread(
                _discord_patch_message,
                _discord_admin_channel_id(),
                message_id,
                _discord_admin_upload_message(result, "denied"),
                [],
            )
        await asyncio.to_thread(_discord_notify_upload_result, result, "denied")
        await asyncio.to_thread(_discord_edit_original, interaction, "거부 처리 완료")
    except HTTPException as exc:
        await asyncio.to_thread(_discord_edit_original, interaction, f"거부 실패: {exc.detail}")
    except Exception as exc:
        await asyncio.to_thread(_discord_edit_original, interaction, f"거부 실패: {exc}")


async def _discord_handle_interaction(interaction: dict[str, Any]) -> None:
    try:
        interaction_type = int(interaction.get("type") or 0)
        data = interaction.get("data") or {}
        if interaction_type == 2:
            command_name = str(data.get("name") or "")
            if command_name == "업로드":
                await _discord_handle_upload_command(interaction)
                return
            if command_name == "랜덤":
                await _discord_handle_random_command(interaction)
                return
            if command_name == "차분":
                subcommand = (data.get("options") or [{}])[0]
                sub_name = str(subcommand.get("name") or "")
                sub_options = _discord_option_map(subcommand.get("options"))
                if sub_name == "목록":
                    await _discord_handle_list_command(interaction)
                    return
                if sub_name == "표기수정":
                    await _discord_handle_marking_command(interaction, sub_options)
                    return
        elif interaction_type == 3:
            await _discord_handle_component(interaction)
            return
        elif interaction_type == 5:
            await _discord_handle_deny_modal(interaction)
            return
    except Exception as exc:
        print(f"[discord] interaction handler failed: {exc}", flush=True)


async def _discord_heartbeat(ws: Any, interval_ms: int, seq_holder: dict[str, Any]) -> None:
    while True:
        await asyncio.sleep(max(interval_ms / 1000.0 * 0.9, 1.0))
        await ws.send(json.dumps({"op": 1, "d": seq_holder.get("seq")}))


async def _discord_gateway_loop() -> None:
    if not DISCORD_GATEWAY_ENABLED or not DISCORD_BOT_TOKEN:
        return
    if websockets is None:
        print("[discord] websockets package is unavailable; gateway bot disabled", flush=True)
        return
    while True:
        heartbeat_task: asyncio.Task[Any] | None = None
        try:
            gateway = await asyncio.to_thread(_discord_api_request, "GET", "/gateway/bot")
            gateway_url = str(gateway.get("url") or "wss://gateway.discord.gg")
            seq_holder: dict[str, Any] = {"seq": None}
            async with websockets.connect(f"{gateway_url}/?v=10&encoding=json", max_size=2**24) as ws:
                async for raw_message in ws:
                    payload = json.loads(raw_message)
                    op = payload.get("op")
                    if payload.get("s") is not None:
                        seq_holder["seq"] = payload.get("s")
                    if op == 10:
                        heartbeat_task = asyncio.create_task(
                            _discord_heartbeat(ws, int(payload["d"]["heartbeat_interval"]), seq_holder)
                        )
                        await ws.send(
                            json.dumps(
                                {
                                    "op": 2,
                                    "d": {
                                        "token": DISCORD_BOT_TOKEN,
                                        "intents": 0,
                                        "properties": {
                                            "os": "windows",
                                            "browser": "for-calc-web",
                                            "device": "for-calc-web",
                                        },
                                    },
                                }
                            )
                        )
                        continue
                    if op == 0:
                        event_name = payload.get("t")
                        data = payload.get("d") or {}
                        if event_name == "READY":
                            global _discord_runtime_application_id
                            app_id = str(data.get("application", {}).get("id") or data.get("user", {}).get("id") or "")
                            if app_id:
                                _discord_runtime_application_id = app_id
                            await asyncio.to_thread(_discord_register_commands)
                            print("[discord] gateway bot online", flush=True)
                        elif event_name == "INTERACTION_CREATE":
                            asyncio.create_task(_discord_handle_interaction(data))
                    elif op in (7, 9):
                        break
        except asyncio.CancelledError:
            if heartbeat_task:
                heartbeat_task.cancel()
            raise
        except Exception as exc:
            print(f"[discord] gateway error: {exc}", flush=True)
        finally:
            if heartbeat_task:
                heartbeat_task.cancel()
        await asyncio.sleep(10)


def _approval_html(title: str, message: str, status_code: int = 200) -> HTMLResponse:
    safe_title = html.escape(title)
    safe_message = html.escape(message)
    body = f"""<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{safe_title}</title>
    <style>
      body {{
        margin: 0;
        font-family: "Segoe UI", "Noto Sans KR", system-ui, sans-serif;
        background: #f6f7f9;
        color: #20242a;
      }}
      main {{
        max-width: 640px;
        margin: 12vh auto;
        border: 1px solid #d7dde5;
        border-radius: 8px;
        background: #fff;
        padding: 24px;
      }}
      h1 {{
        margin: 0 0 12px;
        font-size: 24px;
      }}
      p {{
        margin: 0;
        color: #5f6875;
        line-height: 1.6;
      }}
    </style>
  </head>
  <body>
    <main>
      <h1>{safe_title}</h1>
      <p>{safe_message}</p>
    </main>
  </body>
</html>"""
    return HTMLResponse(body, status_code=status_code)


def _approval_decision_html(pending: dict[str, Any], request_id: str, approval_token: str) -> HTMLResponse:
    login_id = html.escape(str(pending.get("login_id") or ""))
    display_name = html.escape(str(pending.get("display_name") or ""))
    created_at = html.escape(str(pending.get("created_at") or ""))
    safe_request_id = html.escape(request_id, quote=True)
    safe_token = html.escape(approval_token, quote=True)
    body = f"""<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>관리자 가입 승인</title>
    <style>
      body {{
        margin: 0;
        font-family: "Segoe UI", "Noto Sans KR", system-ui, sans-serif;
        background: #f6f7f9;
        color: #20242a;
      }}
      main {{
        max-width: 680px;
        margin: 10vh auto;
        border: 1px solid #d7dde5;
        border-radius: 8px;
        background: #fff;
        padding: 24px;
      }}
      h1 {{
        margin: 0 0 16px;
        font-size: 24px;
      }}
      dl {{
        display: grid;
        grid-template-columns: 120px 1fr;
        gap: 10px 16px;
        margin: 0 0 20px;
      }}
      dt {{
        color: #667181;
        font-weight: 700;
      }}
      dd {{
        margin: 0;
      }}
      .question {{
        margin: 0 0 14px;
        font-size: 18px;
        font-weight: 800;
      }}
      .actions {{
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }}
      button {{
        min-height: 38px;
        border: 1px solid #cbd2dc;
        border-radius: 6px;
        background: #fff;
        color: #20242a;
        padding: 8px 14px;
        font: inherit;
        cursor: pointer;
      }}
      button.primary {{
        border-color: #0a4dcc;
        background: #1b6bff;
        color: #fff;
        font-weight: 800;
      }}
      button.danger {{
        border-color: #b53a2c;
        background: #d64d3f;
        color: #fff;
        font-weight: 800;
      }}
      #denyBox {{
        display: none;
        margin-top: 16px;
        border-top: 1px solid #e5e9ef;
        padding-top: 16px;
      }}
      label {{
        display: block;
        margin-bottom: 10px;
        color: #667181;
        font-size: 13px;
        font-weight: 700;
      }}
      textarea {{
        width: 100%;
        min-height: 120px;
        border: 1px solid #cbd2dc;
        border-radius: 6px;
        padding: 10px;
        font: inherit;
        resize: vertical;
      }}
    </style>
  </head>
  <body>
    <main>
      <h1>관리자 가입 승인</h1>
      <dl>
        <dt>Login ID</dt>
        <dd>{login_id}</dd>
        <dt>Name</dt>
        <dd>{display_name}</dd>
        <dt>Requested</dt>
        <dd>{created_at}</dd>
      </dl>
      <p class="question">이 가입 요청을 승인할까요?</p>
      <div class="actions">
        <form method="post" action="/api/admin/auth/approval-decision">
          <input type="hidden" name="request_id" value="{safe_request_id}" />
          <input type="hidden" name="approval_token" value="{safe_token}" />
          <input type="hidden" name="decision" value="approve" />
          <button class="primary" type="submit">예, 승인</button>
        </form>
        <button type="button" onclick="document.getElementById('denyBox').style.display = 'block'; document.getElementById('reason').focus();">아니오</button>
      </div>
      <section id="denyBox">
        <form method="post" action="/api/admin/auth/approval-decision">
          <input type="hidden" name="request_id" value="{safe_request_id}" />
          <input type="hidden" name="approval_token" value="{safe_token}" />
          <input type="hidden" name="decision" value="deny" />
          <label for="reason">거부 사유</label>
          <textarea id="reason" name="reason" maxlength="500" placeholder="거부 사유를 적어 주세요."></textarea>
          <div class="actions">
            <button class="danger" type="submit">거부</button>
            <button type="submit" name="no_reason" value="true">사유 없음</button>
          </div>
        </form>
      </section>
    </main>
  </body>
</html>"""
    return HTMLResponse(body)


def _require_admin_access(session_token: str | None, x_admin_token: str | None = None) -> dict[str, str]:
    if TABLE_ADMIN_TOKEN and x_admin_token == TABLE_ADMIN_TOKEN:
        return {"loginId": "legacy-token", "displayName": "Legacy admin token", "approvedAt": ""}
    user = _session_user(session_token)
    if not user:
        raise HTTPException(status_code=401, detail="Admin login required")
    return user


def _table_body_paths() -> list[Path]:
    candidates = [
        PACKAGE_ROOT / "body.json",
        PACKAGE_ROOT / "10key-table" / "body.json",
        PACKAGE_ROOT / "table" / "body.json",
        DUAL_TABLE_DIR / "revive" / "body.json",
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
    written: list[str] = []
    for path in _table_body_paths():
        if not path.exists() and not path.parent.exists():
            continue
        _write_json_atomic(path, rows)
        written.append(str(path))
    if not written:
        raise HTTPException(status_code=500, detail="No body.json paths were writable")
    return written


def _write_json_atomic(path: Path, payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def _load_json_rows_from_path(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            rows = json.load(stream)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"{path} parse failed: {exc}") from exc
    if not isinstance(rows, list):
        raise HTTPException(status_code=500, detail=f"{path} root must be an array")
    return [row if isinstance(row, dict) else {} for row in rows]


def _circus_body_paths() -> list[Path]:
    candidates = [
        DUAL_TABLE_DIR / "circus" / "body.json",
        PACKAGE_ROOT / "circus-rating-table-10k" / "body.json",
        PACKAGE_ROOT / "circus-rating-table" / "body.json",
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


def _circus_sort_key(row: dict[str, Any]) -> tuple[float, str, str, str]:
    try:
        level = float(row.get("level"))
    except (TypeError, ValueError):
        level = 9999.0
    return (
        level,
        str(row.get("title") or ""),
        str(row.get("artist") or ""),
        str(row.get("md5") or row.get("sha256") or ""),
    )


def _sync_circus_header_levels(body_path: Path, rows: list[dict[str, Any]]) -> str | None:
    header_path = body_path.with_name("header.json")
    if not header_path.exists():
        return None
    try:
        with header_path.open("r", encoding="utf-8") as stream:
            header = json.load(stream)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"{header_path} parse failed: {exc}") from exc
    if not isinstance(header, dict):
        raise HTTPException(status_code=500, detail=f"{header_path} root must be an object")
    levels = sorted(
        {str(row.get("level") or "").strip() for row in rows if str(row.get("level") or "").strip()},
        key=lambda value: (float(value) if _is_float_text(value) else 9999.0, value),
    )
    if header.get("level_order") == levels and header.get("enum_level_order") == levels:
        return None
    header["level_order"] = levels
    header["enum_level_order"] = levels
    _write_json_atomic(header_path, header)
    return str(header_path)


def _is_float_text(value: str) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _circus_row_from_table_row(row: dict[str, Any]) -> dict[str, Any] | None:
    cr_level = str(row.get("cr_level") or "").strip()
    if not cr_level:
        return None
    objecters = _extract_objecters_from_row(row)
    revive_level = str(row.get("level") or "").strip()
    comment = re.sub(r"\bReviveLv\s*:\s*\S+", "", _strip_cr_markers(row.get("comment"))).strip()
    comment_parts = [comment, f"ReviveLv:{revive_level}" if revive_level else ""]
    circus_row = {
        "md5": str(row.get("md5") or ""),
        "sha256": str(row.get("sha256") or ""),
        "title": str(row.get("title") or ""),
        "artist": str(row.get("artist") or ""),
        "level": cr_level,
        "comment": _sync_comment_objecters(" ".join(part for part in comment_parts if part), objecters),
    }
    for optional_key in ("gauge_total", "notes"):
        if row.get(optional_key) not in (None, ""):
            circus_row[optional_key] = row[optional_key]
    return circus_row


def _write_circus_table_row(row: dict[str, Any]) -> list[str]:
    circus_row = _circus_row_from_table_row(row)
    if not circus_row:
        return []
    owner_key = _row_owner_key(row)
    if not owner_key:
        return []
    written: list[str] = []
    for path in _circus_body_paths():
        if not path.exists() and not path.parent.exists():
            continue
        rows = _load_json_rows_from_path(path) if path.exists() else []
        match_index = next(
            (index for index, existing_row in enumerate(rows) if _row_matches_owner_key(existing_row, owner_key)),
            -1,
        )
        if match_index >= 0:
            rows[match_index] = circus_row
        else:
            rows.append(circus_row)
        rows.sort(key=_circus_sort_key)
        _write_json_atomic(path, rows)
        written.append(str(path))
        header_written = _sync_circus_header_levels(path, rows)
        if header_written:
            written.append(header_written)
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


@app.get("/api/admin/auth/status")
def admin_auth_status(table_admin_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    state = _load_auth_state()
    changed = _cleanup_expired_pending(state)
    if changed:
        _save_auth_state(state)
    user = _session_user(table_admin_session, state)
    return {
        "authenticated": bool(user),
        "user": user,
        "approvalConfigured": _approval_configured(),
        "legacyTokenEnabled": bool(TABLE_ADMIN_TOKEN),
    }


@app.post("/api/admin/auth/signup")
def admin_auth_signup(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if not _approval_configured():
        raise HTTPException(
            status_code=503,
            detail="Discord approval is not configured. Set DISCORD_BOT_TOKEN and DISCORD_ADMIN_CHANNEL_ID.",
        )
    login_id = _normalize_login_id(payload.get("loginId"))
    display_name = _normalize_display_name(payload.get("displayName"), login_id)
    password_hash = _hash_password(_password_from_payload(payload))

    state = _load_auth_state()
    _cleanup_expired_pending(state)
    if login_id in state["users"]:
        raise HTTPException(status_code=409, detail="loginId already exists")
    if any(item.get("login_id") == login_id for item in state["pending"].values() if isinstance(item, dict)):
        raise HTTPException(status_code=409, detail="loginId is already waiting for approval")

    request_id = secrets.token_urlsafe(12)
    approval_token = secrets.token_urlsafe(32)
    pending = {
        "login_id": login_id,
        "display_name": display_name,
        "password_hash": password_hash,
        "request_id": request_id,
        "approval_token": approval_token,
        "created_at": _utc_now_text(),
        "expires_at": time.time() + APPROVAL_TTL_SECONDS,
    }
    state["pending"][request_id] = pending
    _ensure_auth_secret(state)
    _save_auth_state(state)

    base_url = _public_base_url(request)
    approve_url = f"{base_url}/api/admin/auth/approve?request_id={request_id}&approval_token={approval_token}"
    admin_url = f"{base_url}/table/admin.html"
    try:
        _send_discord_approval(pending, approve_url, admin_url)
    except HTTPException:
        fresh_state = _load_auth_state()
        fresh_state.get("pending", {}).pop(request_id, None)
        _save_auth_state(fresh_state)
        raise

    _append_admin_audit(
        "auth_signup_requested",
        request,
        loginId=login_id,
        displayName=display_name,
        requestId=request_id,
    )
    return {"ok": True, "status": "pending", "loginId": login_id}


@app.post("/api/admin/auth/login")
def admin_auth_login(
    request: Request,
    response: Response,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    login_id = _normalize_login_id(payload.get("loginId"))
    password = str(payload.get("password") or "")
    state = _load_auth_state()
    user = state["users"].get(login_id)
    if not isinstance(user, dict) or not _verify_password(password, user.get("password_hash")):
        _append_admin_audit("auth_login_failed", request, loginId=login_id)
        raise HTTPException(status_code=401, detail="Invalid loginId or password")
    changed = _ensure_auth_secret(state)
    session_token = _make_session_token(login_id, state)
    if changed:
        _save_auth_state(state)
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        session_token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )
    _append_admin_audit("auth_login_succeeded", request, _public_user(login_id, user))
    return {"ok": True, "user": _public_user(login_id, user)}


@app.post("/api/admin/auth/logout")
def admin_auth_logout(
    request: Request,
    response: Response,
    table_admin_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _append_admin_audit("auth_logout", request, _audit_session_user(table_admin_session))
    response.delete_cookie(ADMIN_SESSION_COOKIE, path="/", samesite="lax")
    return {"ok": True}


@app.get("/api/admin/auth/approve")
def admin_auth_approve(request_id: str, approval_token: str) -> HTMLResponse:
    state = _load_auth_state()
    changed = _cleanup_expired_pending(state)
    pending = state["pending"].get(request_id)
    if not isinstance(pending, dict):
        if changed:
            _save_auth_state(state)
        return _approval_html("승인 요청 없음", "승인 요청이 만료되었거나 이미 처리되었습니다.", 404)
    if not hmac.compare_digest(str(pending.get("approval_token") or ""), approval_token):
        return _approval_html("승인 실패", "승인 링크가 올바르지 않습니다.", 403)
    if float(pending.get("expires_at", 0) or 0) < time.time():
        state["pending"].pop(request_id, None)
        _save_auth_state(state)
        return _approval_html("승인 만료", "승인 요청이 만료되었습니다. 다시 가입 요청을 보내 주세요.", 410)

    return _approval_decision_html(pending, request_id, approval_token)


@app.post("/api/admin/auth/approval-decision")
def admin_auth_approval_decision(
    request: Request,
    request_id: str = Form(...),
    approval_token: str = Form(...),
    decision: str = Form(...),
    reason: str = Form(""),
    no_reason: str | None = Form(None),
) -> HTMLResponse:
    state = _load_auth_state()
    changed = _cleanup_expired_pending(state)
    pending = state["pending"].get(request_id)
    if not isinstance(pending, dict):
        if changed:
            _save_auth_state(state)
        return _approval_html("승인 요청 없음", "승인 요청이 만료되었거나 이미 처리되었습니다.", 404)
    if not hmac.compare_digest(str(pending.get("approval_token") or ""), approval_token):
        return _approval_html("승인 실패", "승인 링크가 올바르지 않습니다.", 403)
    if float(pending.get("expires_at", 0) or 0) < time.time():
        state["pending"].pop(request_id, None)
        _save_auth_state(state)
        return _approval_html("승인 만료", "승인 요청이 만료되었습니다. 다시 가입 요청을 보내 주세요.", 410)

    login_id = str(pending["login_id"])
    if login_id in state["users"]:
        state["pending"].pop(request_id, None)
        _save_auth_state(state)
        return _approval_html("이미 승인됨", "이미 승인된 계정입니다. 어드민 페이지에서 로그인하면 됩니다.")

    if decision == "deny":
        denial_reason = "" if no_reason else str(reason or "").strip()
        if len(denial_reason) > 500:
            denial_reason = denial_reason[:500]
        state.setdefault("denied", {})[request_id] = {
            "login_id": login_id,
            "display_name": str(pending.get("display_name") or login_id),
            "reason": denial_reason,
            "created_at": str(pending.get("created_at") or ""),
            "denied_at": _utc_now_text(),
        }
        state["pending"].pop(request_id, None)
        _save_auth_state(state)
        _append_admin_audit(
            "auth_signup_denied",
            request,
            loginId=login_id,
            displayName=str(pending.get("display_name") or login_id),
            requestId=request_id,
            reason=denial_reason,
            noReason=not bool(denial_reason),
        )
        message = f"{login_id} 가입 요청을 거부했습니다."
        if denial_reason:
            message = f"{message} 사유: {denial_reason}"
        else:
            message = f"{message} 사유 없음."
        return _approval_html("거부 완료", message)

    if decision != "approve":
        return _approval_html("처리 실패", "알 수 없는 승인 결정입니다.", 400)

    state["users"][login_id] = {
        "display_name": str(pending["display_name"]),
        "password_hash": str(pending["password_hash"]),
        "created_at": str(pending.get("created_at") or _utc_now_text()),
        "approved_at": _utc_now_text(),
    }
    state["pending"].pop(request_id, None)
    _save_auth_state(state)
    _append_admin_audit(
        "auth_signup_approved",
        request,
        loginId=login_id,
        displayName=str(pending["display_name"]),
        requestId=request_id,
    )
    return _approval_html("승인 완료", f"{login_id} 계정을 승인했습니다. 이제 어드민 페이지에서 로그인할 수 있습니다.")


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
        "discordUploadExtensions": sorted(DISCORD_UPLOAD_EXTENSIONS),
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
def table_body(table_admin_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    rows = _load_table_rows()
    admin_user = _session_user(table_admin_session)
    return {
        "rows": [_public_table_row(index, row) for index, row in enumerate(rows)],
        "count": len(rows),
        "adminTokenRequired": bool(TABLE_ADMIN_TOKEN),
        "adminAuthRequired": True,
        "adminAuthenticated": bool(admin_user),
        "adminUser": admin_user,
        "source": str(_primary_body_path()),
    }


@app.post("/api/table/body")
def create_table_body_row(
    request: Request,
    payload: dict[str, Any] = Body(...),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    table_admin_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    admin_user = _require_admin_access(table_admin_session, x_admin_token)
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
    _append_admin_audit(
        "table_row_created",
        request,
        admin_user,
        index=row_index,
        row=_public_table_row(row_index, row),
        written=written,
    )
    return {
        "ok": True,
        "row": _public_table_row(row_index, row),
        "index": row_index,
        "written": written,
    }


@app.patch("/api/table/body/{row_index}")
def update_table_body_row(
    request: Request,
    row_index: int,
    payload: dict[str, Any] = Body(...),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    table_admin_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    admin_user = _require_admin_access(table_admin_session, x_admin_token)
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
    written = []
    if changed:
        written = _write_table_rows(rows)
        written.extend(_write_circus_table_row(row))
    if changed:
        _append_admin_audit(
            "table_row_updated",
            request,
            admin_user,
            index=row_index,
            title=str(row.get("title", "")),
            changed=changed,
            written=written,
        )
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
def serve_table(
    filename: str,
    request: Request,
    table_admin_session: str | None = Cookie(default=None),
) -> FileResponse:
    safe_name = Path(filename).name
    if safe_name == "level-viewer.html":
        if not LEVEL_VIEWER_HTML.exists():
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(LEVEL_VIEWER_HTML, media_type="text/html")
    if safe_name == "admin.html":
        if not ADMIN_HTML.exists():
            raise HTTPException(status_code=404, detail="Not found")
        _append_admin_audit("admin_page_viewed", request, _audit_session_user(table_admin_session))
        return FileResponse(ADMIN_HTML, media_type="text/html")
    if safe_name not in ("header.json", "body.json"):
        raise HTTPException(status_code=404, detail="Not found")
    file_path = TABLE_DIR / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(file_path, media_type="application/json")


@app.get("/dual-difficulty-table-upload/{filename:path}")
def serve_dual_difficulty_table(filename: str) -> FileResponse:
    if not DUAL_TABLE_DIR.exists():
        raise HTTPException(status_code=404, detail="Not found")
    requested_path = (DUAL_TABLE_DIR / filename).resolve()
    root_path = DUAL_TABLE_DIR.resolve()
    if requested_path != root_path and root_path not in requested_path.parents:
        raise HTTPException(status_code=404, detail="Not found")
    if not requested_path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    media_type = "application/json" if requested_path.suffix == ".json" else "text/html"
    return FileResponse(requested_path, media_type=media_type)


@app.on_event("startup")
async def start_discord_bot() -> None:
    global _discord_gateway_task
    if DISCORD_GATEWAY_ENABLED and DISCORD_BOT_TOKEN and _discord_gateway_task is None:
        _discord_gateway_task = asyncio.create_task(_discord_gateway_loop())


@app.on_event("shutdown")
async def stop_discord_bot() -> None:
    global _discord_gateway_task
    if _discord_gateway_task is not None:
        _discord_gateway_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _discord_gateway_task
        _discord_gateway_task = None


_STATIC_DIR = Path("/app/static")
if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
