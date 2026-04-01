FROM python:3.12-slim

# app.py: REPO_ROOT = Path(__file__).resolve().parents[3]
# /app/for-calc-web/backend/app.py → parents[3] = / → CALC_ROOT = /10k-calc
WORKDIR /app/for-calc-web/backend

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/for-calc-web

# directly place 10k-calc at the path app.py resolves (/10k-calc)
COPY 10k-calc /10k-calc

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
