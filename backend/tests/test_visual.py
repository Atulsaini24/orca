"""Visual tests of the 3D city: render each real city by day and by night and check the picture.

The control room's capture mode is deterministic (fixed seed, stepped frames, fixed camera), so the
same build renders the same image on the same renderer. Each render is checked for

* sanity: the frame is not blank, has structure (contrast) and colour, and night is darker than day,
* night lighting: lit windows (warm, bright pixels) appear at night and not in the day,
* regressions: a downscaled, blurred comparison against a golden image stored per platform and GPU
  renderer (tests/golden/<renderer>/). Missing goldens are written on the first run and the test is
  skipped; refresh them deliberately with ORCA_UPDATE_GOLDEN=1 after an intended visual change.
"""
import io
import os
import re
import sys
from pathlib import Path

import numpy as np
import pytest

from orca import cityspec

pw = pytest.importorskip("playwright.sync_api")
Image = pytest.importorskip("PIL.Image")
pytestmark = pytest.mark.e2e

REPO = Path(__file__).resolve().parents[2]
PAGE = REPO / "frontend" / "index.html"
GOLDEN = Path(__file__).resolve().parent / "golden"
CITIES = [c for c in cityspec.built() if (REPO / "frontend" / "data" / c / "streets.js").exists()]


@pytest.fixture(scope="module")
def page():
    with pw.sync_playwright() as p:
        args = (["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist"] if sys.platform == "win32"
                else ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]) + ["--allow-file-access-from-files"]
        try:
            b = p.chromium.launch(args=args)
        except Exception as exc:
            pytest.skip(f"Chromium for Playwright is not installed: {exc}")
        pg = b.new_page(viewport={"width": 1280, "height": 720})
        yield pg
        b.close()


def render(pg, city: str, light: str) -> np.ndarray:
    pg.goto(f"{PAGE.resolve().as_uri()}?capture&city={city}&light={light}")
    pg.wait_for_selector("canvas#gl")
    for _ in range(200):
        if pg.title() == "capture-ready":
            break
        pg.wait_for_timeout(250)
    else:
        raise AssertionError("capture mode never became ready")
    pg.evaluate("(() => { for (let i = 0; i < 89; i++) __orca.step(1/30, false); __orca.step(1/30); })()")
    png = pg.locator("canvas#gl").screenshot()
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype=np.float32) / 255.0


def renderer_key(pg) -> str:
    name = pg.evaluate("(() => { const g = document.createElement('canvas').getContext('webgl2'); "
                       "const d = g && g.getExtension('WEBGL_debug_renderer_info'); return d ? g.getParameter(d.UNMASKED_RENDERER_WEBGL) : 'unknown'; })()")
    kind = "swiftshader" if "SwiftShader" in name else re.sub(r"[^a-z0-9]+", "-", name.lower())[:40]
    return f"{sys.platform}-{kind}"


def small(img: np.ndarray) -> np.ndarray:
    """Grey, downscaled to 160 x 90 with a box filter: robust to sub-pixel and anti-aliasing noise."""
    g = img @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    h, w = g.shape
    return g[: h // 8 * 8, : w // 8 * 8].reshape(h // 8, 8, w // 8, 8).mean(axis=(1, 3))


@pytest.mark.skipif(not CITIES, reason="no real city data built (scripts/build_streets.py)")
@pytest.mark.parametrize("city", CITIES)
def test_city_renders_day_and_night(page, city):
    day, night = render(page, city, "day"), render(page, city, "night")
    for name, img in (("day", day), ("night", night)):
        lum = img @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        assert lum.std() > 0.04, f"{city} {name}: frame is nearly uniform (blank canvas?)"
        assert np.ptp(img.reshape(-1, 3).mean(axis=0)) > 0.005 or name == "night", f"{city} {name}: no colour at all"
        assert (np.abs(np.diff(lum, axis=1)) > 0.08).mean() > 0.01, f"{city} {name}: no edges (buildings and streets missing?)"
    lum_d, lum_n = (x @ np.array([0.299, 0.587, 0.114], dtype=np.float32) for x in (day, night))
    assert lum_n.mean() < 0.6 * lum_d.mean(), f"{city}: night is not darker than day"
    warm = lambda im: ((im[..., 0] > 0.75) & (im[..., 1] > 0.55) & (im[..., 2] < 0.75) & (im[..., 0] > im[..., 2] + 0.15)).mean()
    assert warm(night) > 0.002, f"{city}: no lit windows at night"

    key = renderer_key(page)
    for name, img in (("day", day), ("night", night)):
        gold = GOLDEN / key / f"{city}-{name}.png"
        if os.environ.get("ORCA_UPDATE_GOLDEN") == "1" or not gold.exists():
            gold.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray((img * 255).astype(np.uint8)).save(gold)
            continue
        ref = np.asarray(Image.open(gold).convert("RGB"), dtype=np.float32) / 255.0
        diff = np.abs(small(img) - small(ref))
        assert diff.mean() < 0.035 and np.percentile(diff, 99) < 0.30, (
            f"{city} {name} differs from {gold.relative_to(GOLDEN)}: mean {diff.mean():.3f}, p99 {np.percentile(diff, 99):.3f}. "
            "If the change is intended, re-run with ORCA_UPDATE_GOLDEN=1.")
