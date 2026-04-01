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
