FROM node:20-alpine AS frontend-builder
WORKDIR /app
COPY frontend-source/package*.json ./
RUN npm ci
COPY frontend-source ./
RUN npm run build

FROM python:3.12-slim

# app.py: parents[3] of /app/for-calc-web/backend/app.py = / → CALC_ROOT = /10k-calc
WORKDIR /app/for-calc-web/backend

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/for-calc-web
COPY 10k-calc /10k-calc
COPY --from=frontend-builder /app/dist /app/static

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
