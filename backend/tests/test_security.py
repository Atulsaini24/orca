"""HTTP hardening: operator controls, rate limits, body limits, headers, validation, metrics."""
import pytest
from starlette.testclient import TestClient

from orca.security import RULES, RateLimiter
from orca.server import build_app

OPTS = dict(seed=3, fleet=200, demand="synthetic", data=None, zones=None, db=None, warm=30, speed=20.0)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("ORCA_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("ORCA_OPEN_CONTROLS", raising=False)
    with TestClient(build_app(dict(OPTS))) as c:
        yield c


def test_token_bucket_refills():
    rl = RateLimiter({"x": (1.0, 2)})
    assert rl.allow("a", "x", now=0)[0] and rl.allow("a", "x", now=0)[0]
    ok, retry = rl.allow("a", "x", now=0)
    assert not ok and retry == pytest.approx(1.0)
    assert rl.allow("b", "x", now=0)[0]                 # buckets are per client
    assert rl.allow("a", "x", now=1.01)[0]              # refilled after a second


def test_security_headers_and_request_id(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    for h in ("x-content-type-options", "content-security-policy", "referrer-policy", "x-request-id"):
        assert h in r.headers
    assert client.get("/api/health", headers={"x-request-id": "abc123"}).headers["x-request-id"] == "abc123"


def test_operator_controls_need_token(client, monkeypatch):
    monkeypatch.setenv("ORCA_ADMIN_TOKEN", "s3cret")
    assert client.post("/api/command", json={"cmd": "speed", "value": 60}).status_code == 403
    assert client.post("/api/command", json={"cmd": "pause", "value": True}).status_code == 403
    assert client.post("/api/reset", json={"fleet": 300}).status_code == 403
    bad = client.post("/api/command", json={"cmd": "speed", "value": 60}, headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 403
    ok = client.post("/api/command", json={"cmd": "speed", "value": 60}, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200 and ok.json()["ok"]
    assert client.post("/api/command", json={"cmd": "spike"}).json()["ok"]     # incidents stay public


def test_local_mode_allows_loopback(client):
    assert client.post("/api/command", json={"cmd": "speed", "value": 30}).status_code == 200


def test_command_validation(client):
    assert client.post("/api/command", json={"cmd": "speed", "value": "fast"}).status_code == 400
    assert client.post("/api/command", json={"cmd": "pause", "value": "yes"}).status_code == 400
    assert client.post("/api/command", json=["spike"]).status_code == 400
    assert client.post("/api/command", content=b"not json", headers={"content-type": "application/json"}).status_code == 400


def test_body_limit(client):
    r = client.post("/api/command", content=b"x" * 10_000, headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_rate_limit_and_metrics(client):
    burst = int(RULES["command"][1])
    codes = [client.post("/api/command", json={"cmd": "accident"}).status_code for _ in range(burst + 3)]
    assert codes[:burst] == [200] * burst and 429 in codes[burst:]
    r = client.post("/api/command", json={"cmd": "accident"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    m = client.get("/metrics").text
    assert 'orca_http_requests_total{method="POST",route="/api/command",status="4xx"}' in m
    assert "orca_requests_served_total" in m


def test_html_is_revalidated(client):
    """A cached page would reference hashed assets that a rebuild removed: HTML must say no-cache."""
    r = client.get("/app")
    if r.status_code == 200:
        assert r.headers["cache-control"] == "no-cache"


def test_health_fails_when_the_clock_stops(client):
    """A frozen simulation must not look healthy: Docker and uptime monitors read /api/health."""
    hub = client.app.state.hub
    assert client.get("/api/health").status_code == 200
    hub.task.cancel()                                    # the clock stops, as when the old loop died
    hub.last_ok -= 60
    r = client.get("/api/health")
    assert r.status_code == 503 and r.json()["ok"] is False and r.json()["stalled_seconds"] >= 60
    assert "orca_clock_stall_seconds 6" in client.get("/metrics").text
