.PHONY: install data web frontend run train tune bench test test-fast e2e lint lock video all
PY ?= python

install:   ; $(PY) -m pip install -r backend/requirements-dev.lock && $(PY) -m pip install -e backend --no-deps && cd web && npm ci
data:      ; cd backend && $(PY) scripts/download_tlc.py --year 2015 --month 1 && $(PY) scripts/download_tlc.py --zones && $(PY) scripts/prepare_tlc.py
web:       ; cd web && npm run build
frontend:  ; $(PY) frontend/build.py
run:       ; cd backend && $(PY) -m orca.server
train:     ; cd backend && $(PY) -m orca.train demand && $(PY) -m orca.train eta && $(PY) -m orca.train eta-real
tune:      ; cd backend && $(PY) -m orca.tune
bench:     ; cd backend && $(PY) -m orca.bench
test:      ; cd backend && $(PY) -m pytest -q
test-fast: ; cd backend && $(PY) -m pytest -q -m "not e2e"
e2e:       ; cd backend && $(PY) -m pytest -q -m e2e
lint:      ; ruff check backend tools && cd web && npm run lint && npx tsc -b --noEmit
lock:      ; $(PY) tools/lock_deps.py
video:     ; $(PY) tools/make_tour_video.py
all:       data train tune bench frontend web
