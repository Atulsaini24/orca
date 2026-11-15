# ---- 1. real data: download the NYC TLC trip file and zone shapefile, prepare the replay
FROM python:3.12-slim AS data
WORKDIR /app/backend
COPY backend/requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY backend/orca orca
COPY backend/scripts scripts
# the street network (OpenStreetMap) is committed in artifacts/, so the trip replay is projected onto real streets
COPY backend/artifacts artifacts
RUN mkdir -p data && python scripts/download_tlc.py --year 2015 --month 1 && python scripts/download_tlc.py --zones \
 && python scripts/prepare_tlc.py && rm data/yellow_tripdata_2015-01.parquet

# ---- 2. home page: React + TypeScript + Vite + Tailwind
FROM node:22-alpine AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
COPY docs/benchmark.json /app/docs/benchmark.json
COPY backend/artifacts/*.json /app/backend/artifacts/
COPY --from=data /app/backend/artifacts/nyc_traffic_profile.json /app/backend/artifacts/nyc_traffic_profile.json
RUN npm run build

# ---- 3. runtime
FROM python:3.12-slim
WORKDIR /app
COPY backend/pyproject.toml backend/
RUN pip install --no-cache-dir "starlette>=0.37" "uvicorn>=0.29" "numpy>=1.26" "pandas>=2.1" "scipy>=1.11" \
    "scikit-learn>=1.4" "joblib>=1.3" "threadpoolctl>=3.2" "pyarrow>=14"
COPY backend backend
COPY --from=data /app/backend/data/nyc_tlc_2015-01_manhattan.parquet backend/data/
COPY --from=data /app/backend/artifacts/nyc_traffic_profile.json backend/artifacts/
COPY frontend/index.html frontend/index.html
COPY docs/orca-tour.mp4 docs/orca-tour.webm docs/orca-tour.vtt docs/orca-tour-poster.jpg docs/orca-tour-score.mp3 docs/benchmark.json docs/
COPY --from=web /app/web/dist web/dist
WORKDIR /app/backend
RUN mkdir -p /app/backend/ledger && chown -R orca /app/backend/ledger
USER orca
ENV ORCA_HOST=0.0.0.0 ORCA_PORT=8000 PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=60s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["python", "-m", "orca.server"]
