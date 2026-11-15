.PHONY: install data web frontend run train bench test video all
install:   ; cd backend && pip install -e ".[dev]" && cd ../web && npm ci
data:      ; cd backend && python scripts/download_tlc.py --year 2015 --month 1 && python scripts/download_tlc.py --zones && python scripts/prepare_tlc.py
web:       ; cd web && npm run build
frontend:  ; python frontend/build.py
run:       ; cd backend && python -m orca.server
train:     ; cd backend && python -m orca.train demand && python -m orca.train eta
bench:     ; cd backend && python -m orca.bench --minutes 45 --seeds 1 2
test:      ; cd backend && pytest -q
video:     ; python tools/make_tour_video.py
all:       data train bench frontend web
