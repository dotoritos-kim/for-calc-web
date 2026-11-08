# 웹 BMS 에디터 배포 인수

`/table.html`의 **웹 BMS 에디터 · 플레이어** 링크는 같은 도메인의 `/Editor.html`을 연다. FastAPI가 이 HTML과 `/bms-studio/assets/`의 번들을 제공한다. 사용자가 고른 BMS와 키음은 브라우저 안에서 읽고, 저장한 차트는 다운로드된다.

## 소스와 빌드

검토한 `for-calc-web` 커밋이나 그 커밋의 소스 ZIP을 사용한다. `backend/Dockerfile`은 계산기 프런트와 웹 에디터를 빌드한 뒤 같은 FastAPI 이미지에 넣는다. `bms-studio/build.mjs`는 BMS Desktop v0.6.0과 세 라이브러리의 고정 커밋을 확인하고 잠금 파일로 의존성을 설치한다. 처음 빌드할 때는 Docker 이미지 레지스트리, GitHub, npm, PyPI에 접속할 수 있어야 한다.

## 운영 전 로컬 확인

받은 소스의 루트에서 빌드하고 별도 컨테이너로 연다. 이 예시는 로컬 포트에만 열리며, 운영 `.env`와 상태 볼륨은 운영 프로젝트에 둔다.

```sh
docker build -f backend/Dockerfile -t for-calc-web:studio-preview .
docker run --rm --name for-calc-web-studio-preview \
  -p 127.0.0.1:8766:8000 \
  -e DISCORD_GATEWAY_ENABLED=0 \
  for-calc-web:studio-preview
```

[로컬 난이도표](http://127.0.0.1:8766/table.html)를 열고 다음을 확인한다.

1. 난이도표의 링크로 `/Editor.html`이 열리고 기존 표 뷰어와 계산기도 열린다.
2. 홈에서 **폴더**를 눌러 BMS와 WAV·OGG가 들어 있는 곡 폴더를 고른다. 차분을 편집 화면에서 열고 키음 미리듣기와 플레이를 확인한다.
3. 노트나 제목을 바꿔 저장한다. 다운로드한 BMS에 다른 이름을 붙여 원래 음원 폴더에 둔 뒤 다시 열어 수정과 키음 연결을 확인한다.

BMS만 연 경우 편집기의 **키음 파일 업로드**에서 음원을 고르면 이름이 맞는 누락 키음을 기존 `#WAV` ID에 연결할 수 있다. 자동 저장과 파일 연결은 현재 페이지에 보관되고, 편집 옵션은 같은 브라우저·사이트의 로컬 저장소에 유지된다. **설정 → 편집 옵션 → BGM 레인 배치 → 원본 순서**는 현재 차트에 즉시 적용되며, 다운로드한 BMS를 다시 열 때도 파일의 열 순서를 사용한다. 로컬 확인을 마치면 컨테이너를 실행한 터미널에서 Ctrl+C로 종료한다.

2026-11-09의 그리드·BGM 이동/저장·스크롤·LNMODE 수정과 검증 방법은 [웹 에디터 수정 기록](web-editor-fixes-2026-11-09.md)을 참고한다.

## 새 호스트에 설치

Docker Compose v2가 설치된 서버에서 소스를 풀고, `.env.example`을 참고해 서버에 `.env`를 만든다. 난이도표와 에디터만 제공하는 경우 Discord 토큰을 비워 두고 `DISCORD_GATEWAY_ENABLED=0`으로 둔다.

호스트에서 HTTPS 리버스 프록시를 실행하는 경우 소스 루트에 다음 `docker-compose.override.yml`을 두고 `docker compose up -d --build backend`를 실행한다.

```yaml
services:
  backend:
    ports:
      - "127.0.0.1:8000:8000"
```

HTTPS 프록시는 같은 도메인의 전체 경로를 `http://127.0.0.1:8000`으로 전달한다. 프록시가 같은 Docker 네트워크에 있으면 호스트 포트 매핑 없이 `http://backend:8000`으로 연결할 수 있다. 공개 도메인의 `/table.html`을 열어 위 브라우저 절차를 확인한다.

## 기존 호스트 업데이트

아래 예시는 저장소의 기본 Compose 경로를 사용한다. 기존 운영 프로젝트 이름, `.env`, Compose 오버라이드, 프록시 설정, `table-state` 볼륨을 이어서 사용한다. 운영 서비스가 표나 상태를 별도 경로에서 마운트한다면 소스를 적용하기 전에 해당 경로를 대조하고 백업한다.

운영 도메인은 HTTPS에서 기존 경로와 함께 `/Editor.html`, `/bms-studio/`를 백엔드의 8000번 포트로 전달한다. 기존 터널이나 리버스 프록시가 백엔드 전체를 전달하는 경우 같은 연결을 사용한다. Compose 기본 파일에는 호스트 포트 공개 설정이 없다.

`table-state` 볼륨에는 관리자 계정, 감사 로그, Discord 업로드 상태가 있다. 표 본문은 별도 파일이므로 운영 중 추가·수정한 행을 보존하려면 아래 순서로 기존 컨테이너에서 복사해 새 컨테이너에 넣은 뒤 시작한다. 상태 볼륨과 환경 변수도 호스트의 비공개 백업 방식으로 보관한다.

검토한 소스를 기존 배포 작업 디렉터리에 준비한다. 기존 Compose 프로젝트와 같은 옵션으로 다음을 실행한다. `BACKUP_ROOT`는 저장소와 Docker 빌드 컨텍스트 밖의 비공개 백업 디렉터리로 지정한다.

```sh
set -eu
umask 077
backup_dir="$(mktemp -d "${BACKUP_ROOT:?Set a private backup directory}/bms-studio.XXXXXX")"
old_container="$(docker compose ps -q backend)"
test -n "$old_container"
old_image="$(docker inspect --format '{{.Image}}' "$old_container")"
image_ref="$(docker inspect --format '{{.Config.Image}}' "$old_container")"
rollback_tag="for-calc-web:before-bms-studio-$(date +%Y%m%d-%H%M%S)"
docker image tag "$old_image" "$rollback_tag"
printf '%s\n' "$image_ref" > "$backup_dir/image-ref.txt"
printf '%s\n' "$rollback_tag" > "$backup_dir/rollback-tag.txt"
docker compose build backend
```

빌드 성공 후 서비스 전환 시간에 같은 셸에서 이어서 실행한다. 기존 컨테이너를 중지한 다음 백업하므로 표에 쓰는 작업은 이 사이에 멈춘다.

```sh
docker compose stop backend
table_files='app/for-calc-web/body.json
app/for-calc-web/10key-table/body.json
app/for-calc-web/table/body.json
app/for-calc-web/dual-difficulty-table-upload/revive/body.json
app/for-calc-web/dual-difficulty-table-upload/circus/body.json
app/for-calc-web/dual-difficulty-table-upload/circus/header.json
10key-table/body.json'
printf '%s\n' "$table_files" > "$backup_dir/table-files.txt"
for path in $table_files; do
  mkdir -p "$backup_dir/tables/$(dirname "$path")"
  docker cp "$old_container:/$path" "$backup_dir/tables/$path"
  test -s "$backup_dir/tables/$path"
done

docker compose up --no-deps --no-build --no-start --force-recreate backend
new_container="$(docker compose ps -a -q backend)"
test -n "$new_container"
for path in $table_files; do
  docker cp "$backup_dir/tables/$path" "$new_container:/$path"
done
docker compose start backend
docker compose ps backend
```

백업 복사 중 실패하면 `docker compose start backend`로 기존 컨테이너를 다시 시작하고 파일 경로를 확인한다.

호스트에서 `/api/health`, `/table.html`, `/table/body.json`, `/Editor.html`을 확인하고 위 브라우저 절차를 반복한다. 표의 행 수와 최근 운영 변경도 백업과 대조한다. `/Editor.html`이 503을 반환하면 새 이미지에 웹 번들이 포함됐는지 확인한다.

## 배포 직후 되돌리기

표에 새 변경이 들어오기 전에 문제가 확인되면, 기존 Compose 프로젝트에서 아래 순서로 이전 이미지를 복구한다. 새 셸에서는 `backup_dir`를 이번 배포의 백업 디렉터리로 설정한다. 새 변경이 이미 들어왔다면 현재 표를 먼저 별도로 백업한 후 복원할 버전을 결정한다.

```sh
set -eu
docker compose stop backend
docker image tag "$(cat "$backup_dir/rollback-tag.txt")" "$(cat "$backup_dir/image-ref.txt")"
docker compose up --no-deps --no-build --no-start --force-recreate backend
restored_container="$(docker compose ps -a -q backend)"
test -n "$restored_container"
for path in $(cat "$backup_dir/table-files.txt"); do
  docker cp "$backup_dir/tables/$path" "$restored_container:/$path"
done
docker compose start backend
```
