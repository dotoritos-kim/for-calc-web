# for-calc-web

Packaged output for the local `10k-calc` web wrapper.

## Included

- `backend/app.py`
- `backend/requirements.txt`
- `frontend-dist/`
- `frontend-source/`
- `bms-studio/`
- `compare_parity.py`
- `parity-scan-10Key-Revive-pack.txt`

## Notes

- `backend/app.py` resolves the original calculator from the repo-root `10k-calc/` folder.
- The frontend output in `frontend-dist/` is already built and verified.
- `frontend-source/` is the editable React + Tailwind source package for the web designer.

## Run Backend

```powershell
python -m pip install -r backend/requirements.txt
$env:DISCORD_GATEWAY_ENABLED = "0"
python -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 8000
```

## Browser BMS Editor and Player

Open `/table.html` and follow the web editor and player link to `/Editor.html`.
The page uses the BMS Desktop 0.7.0 renderer (pinned at `1a96e2e`) and pinned library sources.
Choose a folder containing both the chart and its keysounds. When opening
only a chart file, use the editor's keysound upload button to select audio
files. Matching missing sounds keep their existing WAV IDs.
Chart saves download a BMS-family file. The open files, sidecar edits and
autosaves are held in the current page. For charts with keysounds, give the
downloaded chart a new name and place it in the folder with its source chart.
WAV slicing is available in BMS Desktop.

For a local build, run this from the repository root with Git and Node.js 22
available. The first build fetches the pinned sources and installs their npm
dependencies.

```powershell
node bms-studio/build.mjs
python -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/table.html` after starting the server.
The backend Dockerfile builds the same browser bundle and includes it in the image.
See [배포 인수 문서](docs/deploy-bms-studio.md) for an isolated Docker preview
and the setup and update procedures for a host.

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
- `/랜덤 단위:<optional> 난이도:<optional> 개수:<optional>` recommends random patterns from the 10Key table. If `난이도` is set, it only picks from that Revive Lv; otherwise `단위` uses the current dan and next dan range, for example `14단` recommends Revive Lv.13~16. If neither `단위` nor a mapped dan Role is found, it uses the full table. `개수` is capped at 5.
- Admins approve or reject the upload in `DISCORD_ADMIN_CHANNEL_ID`; only approved uploads are appended to the server BMSTable body.
- `/차분 목록` shows Discord-approved uploads only, with table number, edited Revive Lv, calculated CR, title, comment, and uploader.
- `/차분 표기수정 번호:<index> 난이도:<optional> 코멘트:<optional>` edits only the displayed Revive Lv/comment when the caller is a server admin; CR remains the calculated value.
- `/랭킹 곡명:<title> 레벨:<optional>` looks up the Qwilight online ranking for a 10Key chart, covering both the Revive table and 10K charts that only exist on Qwilight. See below.

## Discord Ranking Lookup

`/랭킹` answers "who has the best scores on this 10Key Revive table chart" using the Qwilight online ranking.

- `곡명` is autocompleted from the local table body. Each suggestion is labelled `Lv.<Revive Lv> | <title> | <chart tag>` and inserts `<title> [Lv.<n>]`, so picking one identifies a single row.
- Typing by hand also works: the title is matched case-insensitively against the table, ignoring spacing and symbols, and a partial title is enough.
- `레벨` is the Revive Lv, used when one title exists at several levels (`William Tell` is in the table six times). Without it the bot lists the matching rows and asks for a level instead of guessing.
- The reply shows the matched Qwilight chart, its record/play counts, then the top `QWILIGHT_RANKING_LIMIT` records as `place, grade, score, accuracy, player, date` in Qwilight's own ranking order.

Table rows carry `md5`/`sha256`, but Qwilight keys its ranking by a different chart hash, so rows are matched to Qwilight charts by title instead:

1. Qwilight's note search matches titles by prefix, so the bot searches for the table title (first narrowing with `<title> - <tag>` when the comment starts with a chart tag like `[10K SHD]`).
2. Candidates must start with the table title, and their remaining text must contain that chart tag, which is what separates `William Tell - 10K SHD` from `William Tell - 10K EX`. Artist agreement and record count break ties.
3. Charts nobody has submitted a record for are absent from the Qwilight ranking entirely. The bot says so and lists the other Qwilight charts for the same song (10K ones first) rather than reporting a wrong difficulty.

### Charts outside the Revive table (GCS 10K)

Most 10K charts are not in the Revive table — GCS 10K charts in particular live only on Qwilight, where they are marked by an objecter in the artist field (`DETRO / obj : GCS`) and a 10K tag in the title (`volcanic ~10K HD~ GCS 10K 1.7`).

When `곡명` matches no table row, the bot searches Qwilight for that song and keeps the charts carrying a 10K marker in the title or artist. Each result is labelled with its objecter, parsed with the same `OBJ_PATTERN` the upload flow already uses, so `obj:GCS` and `obj:u_e` charts are told apart. A single hit is answered with its ranking; several hits are listed so `레벨` can narrow them.

Two things to keep in mind for this path: `레벨` here filters on the Qwilight LV rather than a Revive Lv, because these charts have no Revive Lv, and Qwilight's title search matches by prefix, so the front of the song title has to be typed as-is. Autocomplete stays table-only, since it answers per keystroke and must not call out to `taehui.net`.

Resolved chart lookups are cached for `QWILIGHT_NOTE_CACHE_TTL_SECONDS` and ranking pages for `QWILIGHT_RANKING_CACHE_TTL_SECONDS` to keep repeat queries off `taehui.net`.

```text
QWILIGHT_API_BASE=https://taehui.net/qwilight/www
QWILIGHT_RANKING_LIMIT=10
QWILIGHT_SEARCH_MAX_PAGES=5
QWILIGHT_HTTP_TIMEOUT_SECONDS=15
QWILIGHT_NOTE_CACHE_TTL_SECONDS=43200
QWILIGHT_RANKING_CACHE_TTL_SECONDS=300
```

Useful `.env` values:

```text
DISCORD_APPLICATION_ID=
DISCORD_GUILD_ID=
DISCORD_ADMIN_USER_IDS=
DISCORD_DAN_ROLE_IDS=
DISCORD_UPLOAD_DB=
DISCORD_UPLOAD_MAX_BYTES=
DISCORD_GATEWAY_ENABLED=1
DISCORD_REGISTER_COMMANDS=1
DISCORD_WELCOME_ENABLED=1
DISCORD_WELCOME_CHANNEL_ID=
DISCORD_WELCOME_CHANNEL_NAME=일반
DISCORD_WELCOME_MESSAGE=환영합니다 {mention}님
```

## Discord Welcome Message

When a member joins the guild, the bot posts `환영합니다 XXX님` in the `#일반` channel.

- The channel is taken from `DISCORD_WELCOME_CHANNEL_ID` (numeric ID or channel URL). If unset, the bot resolves the channel named `DISCORD_WELCOME_CHANNEL_NAME` (default `일반`) once and caches it.
- `DISCORD_WELCOME_MESSAGE` supports `{mention}` (pings the member) and `{name}` (display name only).
- Bots that join are ignored, and `DISCORD_WELCOME_ENABLED=0` turns the feature off.
- This requires the privileged **Server Members Intent**: enable it in the Discord Developer Portal under Bot → Privileged Gateway Intents. If it is disabled, the gateway logs the warning and reconnects without welcome messages so slash commands keep working.

Set `DISCORD_GUILD_ID` during testing if you want to force a specific server. If it is omitted, the backend tries to infer the server from `DISCORD_ADMIN_CHANNEL_ID` and registers guild commands there; if that lookup fails, commands are registered globally and Discord may take longer to show them. `DISCORD_ADMIN_USER_IDS` is a comma-separated allowlist; users with Administrator, Manage Server, or Manage Messages permission are also treated as admins. Pending uploads, approval history, and uploader ownership are stored in `.discord_uploads.json` by default.

For automatic dan-based random recommendations, set Discord Role IDs with `DISCORD_DAN_ROLE_IDS`, for example `1=ROLE_ID,2=ROLE_ID,...,16=ROLE_ID`. Direct local runs also accept individual process env vars like `DISCORD_DAN_ROLE_14_ID=ROLE_ID`. If a member has multiple mapped dan roles, the highest dan is used.

Docker Compose reads the values automatically. For a new project, start it
with the following command.

```powershell
docker compose up -d --build backend
```

For an existing project, follow the [host update steps](docs/deploy-bms-studio.md#기존-호스트-업데이트)
to carry over the current table files.

For direct local backend runs, load the values before starting Uvicorn:

```powershell
$env:DISCORD_BOT_TOKEN = (Get-Content .env | Where-Object { $_ -like 'DISCORD_BOT_TOKEN=*' }).Split('=', 2)[1]
$env:DISCORD_ADMIN_CHANNEL_ID = (Get-Content .env | Where-Object { $_ -like 'DISCORD_ADMIN_CHANNEL_ID=*' }).Split('=', 2)[1]
$env:PUBLIC_BASE_URL = (Get-Content .env | Where-Object { $_ -like 'PUBLIC_BASE_URL=*' }).Split('=', 2)[1]
cd backend
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Approved users and pending approvals are stored in `.admin_auth.json` by default. Admin page visits, login events, approval decisions, and table edits are appended to `.admin_audit.jsonl`. Set `TABLE_ADMIN_AUTH_DB` or `TABLE_ADMIN_AUDIT_LOG` if you want those files written somewhere else. `TABLE_STATE_DIR` moves all three state files (`.admin_auth.json`, `.admin_audit.jsonl`, `.discord_uploads.json`) at once; `docker-compose.yml` points it at the `table-state` volume so redeploys keep admin accounts and upload records. `TABLE_ADMIN_TOKEN` is still accepted as a legacy API fallback, but the admin page no longer asks for it.

Logged-in admins can remove a chart with the 삭제 button in the editor. The row is removed from every Revive `body.json` copy and from the circus table, and the deletion is written to the audit log.

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
