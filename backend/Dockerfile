FROM node:20-alpine AS frontend-builder
WORKDIR /app
COPY frontend-source/package*.json ./
RUN npm ci
COPY frontend-source ./
RUN npm run build

FROM node:22-alpine AS bms-studio-builder
RUN apk add --no-cache git
WORKDIR /app
COPY bms-studio/ ./bms-studio/
RUN node bms-studio/build.mjs

FROM python:3.12-slim

# app.py: parents[3] of /app/for-calc-web/backend/app.py = / → CALC_ROOT = /10k-calc
WORKDIR /app/for-calc-web/backend

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/for-calc-web
COPY 10k-calc /10k-calc
COPY 10key-table /10key-table
COPY table /app/for-calc-web/table
COPY --from=frontend-builder /app/dist /app/static
COPY --from=bms-studio-builder /app/bms-studio/dist /app/for-calc-web/bms-studio/dist

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
