"""Measure how smoothly the control room runs on this machine's GPU.

    python tools/perf_probe.py                                   # http://localhost:8000/app
    python tools/perf_probe.py "http://localhost:8000/app?city=chicago"

Opens the page in a real (headed) Chromium with GPU acceleration and reports load time, frame times while
idle, while orbiting the camera and zoomed in, the GPU time of one frame (synchronised with a 1-pixel read),
and the city build time from the page's own performance marks. Needs `pip install playwright` and
`playwright install chromium`.
"""
import json
import sys
import time

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/app"
ARGS = ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist"] if sys.platform == "win32" else ["--enable-gpu", "--ignore-gpu-blocklist"]

FRAMES = """
(sec) => new Promise((done) => {
  const ts = []; let last = performance.now(); const end = last + sec * 1000;
  const f = (now) => {
    ts.push(now - last); last = now;
    if (now < end) return requestAnimationFrame(f);
    ts.sort((a, b) => a - b); const m = ts.reduce((a, b) => a + b, 0) / ts.length;
    done({ fps: +(1000 / m).toFixed(1), mean_ms: +m.toFixed(1), p95_ms: +ts[Math.floor(ts.length * 0.95)].toFixed(1) });
  };
  requestAnimationFrame(f);
})
"""
GPU = """
() => {
  const { R, cam, dpr } = window.__orcaPerf, gl = R.gl, px = new Uint8Array(4);
  const sync = () => gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
  let s = 0; for (let i = 0; i < 20; i++) { sync(); const a = performance.now(); R.render(cam); sync(); s += performance.now() - a; }
  return { gpu_frame_ms: +(s / 20).toFixed(1), canvas: [R.canvas.width, R.canvas.height], pixel_ratio: dpr(), tiles_drawn: R.stats.tiles };
}
"""


def main():
    url = URL + ("&" if "?" in URL else "?") + "perf"
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False, args=ARGS)
        pg = b.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=1.5)
        t0 = time.time()
        pg.goto(url, wait_until="load")
        pg.wait_for_function("() => window.__orcaPerf", timeout=60_000)
        ready = time.time() - t0
        build = pg.evaluate("() => Object.fromEntries(performance.getEntriesByType('measure').filter((m) => m.name.startsWith('orca'))"
                            ".map((m) => [m.name.slice(5), Math.round(m.duration)]))")
        gpu_name = pg.evaluate("() => { const g = document.createElement('canvas').getContext('webgl2'), e = g.getExtension('WEBGL_debug_renderer_info');"
                               " return e ? g.getParameter(e.UNMASKED_RENDERER_WEBGL) : 'unknown'; }")
        pg.evaluate("() => { const w = document.getElementById('welcome'); if (w) w.hidden = true; }")
        pg.wait_for_timeout(4000)                                     # the entrance fly-in
        idle = pg.evaluate(FRAMES, 5)
        gpu = pg.evaluate(GPU)
        pg.mouse.move(800, 450)
        pg.mouse.down()
        probe = pg.evaluate_handle(f"({FRAMES})(5)")
        start, k = time.time(), 0
        while time.time() - start < 5.2:
            k += 1
            pg.mouse.move(800 + 300 * ((k % 40) / 40), 450 + 40 * ((k % 20) / 20 - 0.5))
        pg.mouse.up()
        orbit = probe.json_value()
        for _ in range(6):
            pg.mouse.wheel(0, -300)
            pg.wait_for_timeout(120)
        zoomed = pg.evaluate(FRAMES, 4)
        print(json.dumps(dict(url=URL, gpu=gpu_name, ready_s=round(ready, 2), city_build_ms=build, idle=idle, orbiting=orbit,
                              zoomed_in=zoomed, one_frame=gpu), indent=1))
        b.close()


if __name__ == "__main__":
    main()
