.PHONY: install data cities web frontend run train tune bench test test-fast e2e lint lock video perf all
PY ?= python
CITIES ?= manhattan chicago

install:   ; $(PY) -m pip install -r backend/requirements-dev.lock && $(PY) -m pip install -e backend --no-deps && cd web && npm ci
# real trips, September 2026: NYC TLC (yellow taxis + Uber / Lyft) and the City of Chicago (TNP)
data:      ; cd backend && $(PY) scripts/download_tlc.py --zones && $(PY) scripts/download_tlc.py --year 2026 --month 9 && $(PY) scripts/download_tlc.py --kind fhvhv --year 2026 --month 9 && $(PY) scripts/prepare_trips.py --city manhattan && $(PY) scripts/download_chicago.py --only trips && $(PY) scripts/prepare_trips.py --city chicago
# street networks (OpenStreetMap) and skylines (official footprints); the outputs are committed
cities:    ; cd backend && for c in $(CITIES); do $(PY) scripts/build_streets.py --city $$c && $(PY) scripts/build_buildings.py --city $$c || exit 1; done
web:       ; cd web && npm run build
frontend:  ; $(PY) frontend/build.py
run:       ; cd backend && $(PY) -m orca.server
train:     ; cd backend && for c in $(CITIES); do $(PY) -m orca.train demand --city $$c && $(PY) -m orca.train eta --city $$c && $(PY) -m orca.train eta-real --city $$c && $(PY) -m orca.calibrate --city $$c || exit 1; done
tune:      ; cd backend && for c in $(CITIES); do ORCA_CITY=$$c $(PY) -m orca.tune || exit 1; done
bench:     ; cd backend && for c in $(CITIES); do $(PY) -m orca.bench --city $$c || exit 1; done
test:      ; cd backend && $(PY) -m pytest -q
test-fast: ; cd backend && $(PY) -m pytest -q -m "not e2e"
e2e:       ; cd backend && $(PY) -m pytest -q -m e2e
lint:      ; ruff check backend tools && cd web && npm run lint && npx tsc -b --noEmit
lock:      ; $(PY) tools/lock_deps.py
video:     ; $(PY) tools/make_tour_video.py
# frame rate and GPU time of the control room on this machine (needs `make run` in another terminal)
perf:      ; $(PY) tools/perf_probe.py
all:       data train tune bench frontend web
