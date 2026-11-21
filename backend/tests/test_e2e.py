"""End-to-end tests in a real browser (Playwright + Chromium) against a live server.

Run with the rest of the suite; skipped automatically when Playwright's Chromium is not
installed (`pip install playwright && playwright install chromium`).
"""
import contextlib
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
pw = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.e2e


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    import requests
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "orca.server", "--port", str(port), "--fleet", "300", "--demand", "synthetic"],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(240):
        try:
            if requests.get(base + "/api/health", timeout=1).ok:
                break
        except requests.RequestException:
            time.sleep(0.5)
    yield base
    proc.terminate()
    proc.wait(10)


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        software = sys.platform != "win32" or os.environ.get("ORCA_E2E_SOFTWARE_GL") == "1"   # CI renders WebGL on the CPU
        args = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if software else ["--use-angle=d3d11", "--enable-gpu"]
        try:
            b = p.chromium.launch(args=args)
        except Exception as exc:  # browser binaries not installed
            pytest.skip(f"Chromium for Playwright is not installed: {exc}")
        yield b
        b.close()


def _until(fn, timeout=20.0, what="condition"):
    """Poll from Python: the page's CSP (no 'unsafe-eval') blocks Playwright's in-page polling predicates."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return
        except Exception:  # element not attached yet
            pass
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}")


_open_pages: list = []
LOCAL_HOSTS = {"127.0.0.1", "localhost", ""}


@pytest.fixture(autouse=True)
def _close_pages():
    """Close each test's pages: a control room left open keeps rendering WebGL (on CI, on the CPU) and starves
    the server and the next test's page."""
    yield
    while _open_pages:
        with contextlib.suppress(Exception):
            _open_pages.pop().close()


def _offline(route):
    """The tests never touch the internet: web fonts and other external requests get an empty answer at once,
    so a slow or unreachable host cannot hold up the page's load event."""
    if urlparse(route.request.url).hostname in LOCAL_HOSTS:
        route.continue_()
    else:
        route.fulfill(status=200, body="", content_type="text/css" if route.request.resource_type == "stylesheet"
                      else "application/octet-stream")


def _page(browser, errors):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _open_pages.append(page)
    page.route(re.compile(r"^https?://"), _offline)            # file:// pages load untouched
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))
    return page


@pytest.mark.skipif(not (REPO / "web" / "dist" / "index.html").exists(), reason="home page not built (make web)")
def test_home_page(server, browser):
    errors: list[str] = []
    page = _page(browser, errors)
    page.goto(server + "/", wait_until="domcontentloaded")
    page.get_by_role("heading", level=1).wait_for()
    assert "every ride" in page.get_by_role("heading", level=1).inner_text()
    page.get_by_text("Engine live").wait_for(timeout=10_000)              # live status from /api/health
    assert page.locator("#journey canvas").count() == 1                   # the scroll-driven 3D journey through real cities
    for section in ("#how", "#results", "#data", "#tour"):
        assert page.locator(section).count() == 1
    assert page.locator("video source[src='/media/orca-tour.mp4']").count() == 1
    page.get_by_role("link", name="Launch the Manhattan control room").click()
    page.wait_for_url(lambda u: "/app" in u)
    _until(lambda: page.inner_text("#modeTxt") == "Live server", what="control room after navigation")
    assert not [e for e in errors if "favicon" not in e], errors


def test_control_room_live(server, browser):
    errors: list[str] = []
    page = _page(browser, errors)
    page.goto(server + "/app", wait_until="domcontentloaded")
    _until(lambda: page.inner_text("#modeTxt") == "Live server", what="live server status")
    _until(lambda: ":" in page.inner_text("#w-orca"), what="first wait figures")
    assert page.locator("canvas#gl").is_visible()
    # Clicks are dispatched as DOM events: a pointer click first waits for the button to sit still across
    # animation frames, and with WebGL rendered on the CPU (CI) those frames can stall for longer than the
    # timeout. What the buttons do is still checked: the tour caption below appears only if the tour started.
    page.locator("#w-ok").dispatch_event("click")                        # dismiss the welcome card
    page.get_by_role("button", name="Demand spike").dispatch_event("click")
    page.locator("#tourBtn").dispatch_event("click")
    _until(lambda: len(page.inner_text("#capT")) > 0, what="tour caption")
    assert "tour" in page.locator("#tourBtn").inner_text().lower()
    assert not errors, errors


def test_control_room_offline_runs_real_data(browser):
    """Opened as a file, the page runs the engine in the browser on the bundled real trips of the first city."""
    errors: list[str] = []
    page = _page(browser, errors)
    page.goto((REPO / "frontend" / "index.html").resolve().as_uri(), wait_until="domcontentloaded")
    _until(lambda: page.inner_text("#modeTxt") == "In-browser engine", what="in-browser engine")
    _until(lambda: "real Manhattan trips" in page.inner_text("#engine"), what="real-data engine label")
    real = [e for e in errors if "fonts.g" not in e and "ERR_FILE_NOT_FOUND" not in e]
    assert not real, real


@pytest.mark.skipif(not (REPO / "frontend" / "data" / "chicago" / "streets.js").exists(), reason="Chicago not built")
def test_control_room_other_city_runs_in_browser(server, browser):
    """Regression: asking for a city the server is not simulating must not draw the server's cars on it."""
    errors: list[str] = []
    page = _page(browser, errors)
    page.goto(server + "/app?city=chicago", wait_until="domcontentloaded")
    _until(lambda: page.inner_text("#modeTxt") == "In-browser engine", what="in-browser engine for the other city")
    _until(lambda: "real Chicago trips" in page.inner_text("#engine"), what="Chicago engine label")
    assert not [e for e in errors if "favicon" not in e], errors
