# ---- 1. real data: download each city's trip records and prepare the replays
FROM python:3.12-slim AS data
WORKDIR /app/backend
COPY backend/requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock matplotlib==3.10.1
COPY backend/orca orca
COPY backend/scripts scripts
# street networks (OpenStreetMap) and zone boundaries are committed, so trips are projected onto real streets
COPY backend/artifacts artifacts
COPY backend/data/chicago/region.json backend/data/chicago/community_areas.geojson data/chicago/
COPY backend/data/chicago/neighborhoods data/chicago/neighborhoods
COPY frontend/data /app/frontend/data
# Manhattan: NYC TLC, September 2026, yellow taxis + Uber / Lyft. Chicago: City of Chicago TNP trips, same month.
ARG CITIES="manhattan chicago"
RUN python scripts/download_tlc.py --zones \
 && python scripts/download_tlc.py --year 2026 --month 9 \
 && python scripts/download_tlc.py --kind fhvhv --year 2026 --month 9 \
 && python scripts/prepare_trips.py --city manhattan && rm -f data/*_tripdata_*.parquet \
 && if echo "$CITIES" | grep -q chicago; then python scripts/download_chicago.py --only trips && python scripts/prepare_trips.py --city chicago; fi

# ---- 2. home page: React + TypeScript + Vite + Tailwind
FROM node:22-alpine AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
COPY docs/benchmark_*.json /app/docs/
COPY backend/artifacts/*.json /app/backend/artifacts/
COPY --from=data /app/backend/artifacts/*.json /app/backend/artifacts/
RUN npm run build

# ---- 3. runtime
FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.lock backend/
RUN pip install --no-cache-dir -r backend/requirements.lock && useradd --create-home --uid 10001 orca
COPY backend/orca backend/orca
COPY backend/pyproject.toml backend/
COPY backend/artifacts backend/artifacts
COPY --from=data /app/backend/artifacts/*.json backend/artifacts/
COPY --from=data /app/backend/data/trips_*.parquet backend/data/
COPY frontend/index.html frontend/index.html
COPY frontend/data frontend/data
COPY --from=data /app/frontend/data frontend/data
COPY docs/orca-tour.mp4 docs/orca-tour.webm docs/orca-tour.vtt docs/orca-tour-poster.jpg docs/orca-tour-score.mp3 docs/
COPY docs/benchmark*.json docs/
COPY --from=web /app/web/dist web/dist
WORKDIR /app/backend
RUN mkdir -p /app/backend/ledger && chown -R orca /app/backend/ledger
USER orca
ENV ORCA_HOST=0.0.0.0 ORCA_PORT=8000 PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=90s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["python", "-m", "orca.server"]
