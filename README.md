# for-calc-web

Packaged output for the local `10k-calc` web wrapper.

## Included

- `backend/app.py`
- `backend/requirements.txt`
- `frontend-dist/`
- `frontend-source/`
- `compare_parity.py`
- `parity-scan-10Key-Revive-pack.txt`

## Notes

- This package is meant to stay inside the TenRiff repo.
- `backend/app.py` resolves the original calculator from the repo-root `10k-calc/` folder.
- The frontend output in `frontend-dist/` is already built and verified.
- `frontend-source/` is the editable React + Tailwind source package for the web designer.

## Run Backend

```powershell
cd Baepoks\for-calc-web\backend
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

## Admin Login Approval

The table admin editor now uses signup approval instead of manual token entry:

1. A user opens `/table/admin.html` and sends a signup request.
2. The backend posts an approval message to the 10K Discord admin channel.
3. An admin clicks the approval link in Discord, reviews the request, then approves or rejects it.
4. If rejected, the admin can enter a reason or choose no reason.
5. If approved, the user logs in with the approved login ID and password.

Required `.env` values:

```text
DISCORD_BOT_TOKEN=
DISCORD_ADMIN_CHANNEL_ID=
PUBLIC_BASE_URL=
```

`PUBLIC_BASE_URL` must be the browser-reachable backend URL used inside Discord approval links, for example `https://example.com` or a tunnel URL during local testing.
If you prefer an incoming webhook instead of a bot token, set `DISCORD_APPROVAL_WEBHOOK_URL`.
`DISCORD_ADMIN_CHANNEL_ID` can be the numeric channel ID or a Discord channel URL; the backend uses the last long numeric ID from the value.

## Discord Upload Commands

When `DISCORD_BOT_TOKEN` is set, the backend also runs a Discord Gateway bot and registers slash commands:

- `/업로드 파일:<chart> 코멘트:<text> 난이도:<optional>` accepts `.bms`, `.bme`, `.bml`, and `.pms` only, analyzes the chart, then posts an approval request to the admin channel.
- `/랜덤 난이도:<optional> 개수:<optional>` recommends random patterns from the 10Key table. If `난이도` is set, it only picks from that Revive Lv; `개수` is capped at 5.
- Admins approve or reject the upload in `DISCORD_ADMIN_CHANNEL_ID`; only approved uploads are appended to the server BMSTable body.
- `/차분 목록` shows Discord-approved uploads only, with table number, edited Revive Lv, calculated CR, title, comment, and uploader.
- `/차분 표기수정 번호:<index> 난이도:<optional> 코멘트:<optional>` edits only the displayed Revive Lv/comment when the caller is a server admin; CR remains the calculated value.

Useful `.env` values:

```text
DISCORD_APPLICATION_ID=
DISCORD_GUILD_ID=
DISCORD_ADMIN_USER_IDS=
DISCORD_UPLOAD_DB=
DISCORD_UPLOAD_MAX_BYTES=
DISCORD_GATEWAY_ENABLED=1
DISCORD_REGISTER_COMMANDS=1
```

Set `DISCORD_GUILD_ID` during testing if you want to force a specific server. If it is omitted, the backend tries to infer the server from `DISCORD_ADMIN_CHANNEL_ID` and registers guild commands there; if that lookup fails, commands are registered globally and Discord may take longer to show them. `DISCORD_ADMIN_USER_IDS` is a comma-separated allowlist; users with Administrator, Manage Server, or Manage Messages permission are also treated as admins. Pending uploads, approval history, and uploader ownership are stored in `.discord_uploads.json` by default.

Docker Compose reads the values automatically:

```powershell
docker compose up -d --build --force-recreate backend
```

For direct local backend runs, load the values before starting Uvicorn:

```powershell
$env:DISCORD_BOT_TOKEN = (Get-Content .env | Where-Object { $_ -like 'DISCORD_BOT_TOKEN=*' }).Split('=', 2)[1]
$env:DISCORD_ADMIN_CHANNEL_ID = (Get-Content .env | Where-Object { $_ -like 'DISCORD_ADMIN_CHANNEL_ID=*' }).Split('=', 2)[1]
$env:PUBLIC_BASE_URL = (Get-Content .env | Where-Object { $_ -like 'PUBLIC_BASE_URL=*' }).Split('=', 2)[1]
cd backend
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Approved users and pending approvals are stored in `.admin_auth.json` by default. Admin page visits, login events, approval decisions, and table edits are appended to `.admin_audit.jsonl`. Set `TABLE_ADMIN_AUTH_DB` or `TABLE_ADMIN_AUDIT_LOG` if you want those files written somewhere else. `TABLE_ADMIN_TOKEN` is still accepted as a legacy API fallback, but the admin page no longer asks for it.

## Frontend Output

Built files are in:

```text
Baepoks\for-calc-web\frontend-dist
```

To preview them with a simple local server:

```powershell
cd Baepoks\for-calc-web\frontend-dist
python -m http.server 5173 --bind 127.0.0.1
```

Then open:

```text
http://127.0.0.1:5173
```

If you use the static preview route above, the frontend expects the backend at:

```text
http://127.0.0.1:8000
```

## Frontend Source

Editable source files for the web designer are in:

```text
Baepoks\for-calc-web\frontend-source
```

Inside that folder:

- `src/App.tsx`
  - main page layout
  - upload flow
  - result cards
  - graph selector / graph panel
- `src/index.css`
  - local Tailwind component classes and visual tuning
- `index.html`
  - page shell
- `package.json`
  - frontend dependencies and scripts
- `vite.config.ts`
  - local dev server and `/api` proxy
- `tailwind.config.cjs`
  - Tailwind theme tokens

Run the editable frontend like this:

```powershell
cd Baepoks\for-calc-web\frontend-source
npm install
npm run dev
```

Then open:

```text
http://127.0.0.1:5173
```

Keep the backend running on `127.0.0.1:8000`.

## Designer Notes

- The visual layer can be changed freely.
- The backend contract should stay stable unless the parity script is rerun after changes.
- The current parity scan log against `D:\10Key-Revive-pack` is included as `parity-scan-10Key-Revive-pack.txt`.

## Parity Check

From the repo root:

```powershell
python Baepoks\for-calc-web\compare_parity.py
```
