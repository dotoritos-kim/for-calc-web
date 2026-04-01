FROM python:3.12-slim

# See app.py for expected repo layout: REPO_ROOT is set to parents[3] of backend/app.py.
# If file is /app/for-calc-web/backend/app.py, then REPO_ROOT=/app, and CALC_ROOT=/app/10k-calc.
WORKDIR /app/for-calc-web/backend

# install python dependencies
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# add full repo at expected relative path for app.py
COPY . /app/for-calc-web

WORKDIR /app/for-calc-web/backend

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
