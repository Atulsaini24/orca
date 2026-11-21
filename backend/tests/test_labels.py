"""The control room's map-label stacking (frontend/src/labels.js), run under Node.

Regression: the previous stacking loop could spin forever on floating-point rounding when label plates
overlapped, freezing the page while the map was zoomed (about 5% of random overlapping layouts).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

LABELS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "labels.js"
node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="Node.js is not installed")

CHECK = r"""
let seed = 12345; const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
const out = { layouts: 0, overlaps: 0, worst_ms: 0 };
for (let i = 0; i < 100000; i++) {
  // three plates near one point, as when zoomed out or when the cars stand close together
  const cx = 100 + rnd() * 600, cy = 100 + rnd() * 400;
  const n = 2 + Math.floor(rnd() * 3);
  const boxes = Array.from({ length: n }, () => ({ sx: cx + (rnd() - 0.5) * 120, sy: cy + (rnd() - 0.5) * 60, w: 90 + rnd() * 80, h: 24.5 + rnd() * 0.3 }));
  const t0 = performance.now(), lift = stackLabels(boxes), dt = performance.now() - t0;
  out.worst_ms = Math.max(out.worst_ms, dt); out.layouts++;
  const r = boxes.map((b, k) => ({ l: b.sx - b.w / 2, r: b.sx + b.w / 2, b: b.sy - 10 - lift[k], t: b.sy - 10 - lift[k] - b.h }));
  for (let a = 0; a < n; a++) for (let b = a + 1; b < n; b++) {
    const A = r[a], B = r[b];
    if (A.l < B.r + 4 && A.r > B.l - 4 && A.t < B.b + 4 && A.b > B.t - 4) out.overlaps++;
  }
}
// the layout that never finished with the old loop
out.regression = stackLabels([
  { sx: 660.4224855020759, sy: 437.51969843181183, w: 127.97965667991768, h: 24.783609149179227 },
  { sx: 640.1, sy: 431.2, w: 120.5, h: 24.6 }, { sx: 655.0, sy: 425.0, w: 110.0, h: 24.7 }]);
out.offscreen = stackLabels([{ sx: NaN, sy: 10, w: 50, h: 20 }, { sx: 5, sy: 5, w: 50, h: 20 }]);
console.log(JSON.stringify(out));
"""


def test_label_stacking_always_finishes_and_never_overlaps():
    src = LABELS.read_text(encoding="utf-8") + CHECK
    # a hang would hit the timeout instead of freezing the test run
    res = subprocess.run([node, "--input-type=module", "-e", src], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["layouts"] == 100000
    assert out["overlaps"] == 0
    assert out["worst_ms"] < 50
    assert len(out["regression"]) == 3 and all(v >= 0 for v in out["regression"])
    assert out["offscreen"][0] == 0
