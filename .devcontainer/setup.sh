#!/usr/bin/env bash
# Runs once, when the codespace is created (about 5 minutes): the locked dependencies, the real Manhattan trip
# replay (NYC TLC, September 2026: yellow taxis and Uber / Lyft), the home page and the control room.
set -euo pipefail
cd "$(dirname "$0")/.."

git lfs install --local && git lfs pull                     # street networks, trained models, tour media

python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r backend/requirements-dev.lock
python -m pip install --quiet -e backend --no-deps

cd backend
python scripts/download_tlc.py --zones
python scripts/download_tlc.py --year 2026 --month 9
python scripts/download_tlc.py --kind fhvhv --year 2026 --month 9
python scripts/prepare_trips.py --city manhattan
rm -f data/*_tripdata_*.parquet                             # the 580 MB of raw downloads are no longer needed
cd ..

npm ci --prefix web --no-audit --no-fund
npm run build --prefix web
python frontend/build.py
echo "ORCA is ready. The live server starts with the codespace (.devcontainer/start.sh), on port 8000."
