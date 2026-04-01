# frontend-source

Editable React + Tailwind source for the `10k-calc` web wrapper.

## Main Files

- `src/App.tsx`
  - page composition
  - upload and option form
  - result panels
  - graph selector and graph preview
- `src/index.css`
  - Tailwind component classes and local visual styling
- `src/main.tsx`
  - React entry point
- `index.html`
  - app mount shell
- `vite.config.ts`
  - dev server and `/api` proxy to `127.0.0.1:8000`
- `tailwind.config.cjs`
  - theme colors, fonts, shadows

## Run

```powershell
cd Baepoks\for-calc-web\frontend-source
npm install
npm run dev
```

Backend must be running separately:

```powershell
cd Baepoks\for-calc-web\backend
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

## Important

- Visual edits are safe.
- If the request/response contract changes, rerun:

```powershell
python Baepoks\for-calc-web\compare_parity.py
```

- The current package already passed parity scanning against `D:\10Key-Revive-pack`.
