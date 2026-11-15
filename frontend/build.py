"""Bundle src/ into one self-contained index.html: no external code, only the web font.

    python frontend/build.py           # writes frontend/index.html
"""
import json
import pathlib

here = pathlib.Path(__file__).parent
root = here.parent
order = ["tlc_data.js", "sim.js", "gl.js", "city.js", "app.js"]
js = "\n".join((here / "src" / f).read_text() for f in order)

def load(p):
    p = root / p
    return json.loads(p.read_text()) if p.exists() else None

report = {
    "eta": load("backend/artifacts/eta_metrics.json"),
    "demand": load("backend/artifacts/demand_sim_metrics.json"),
    "demand_tlc": load("backend/artifacts/demand_tlc_metrics.json"),
    "benchmark": load("docs/benchmark.json"),
}
html = (here / "src/index.html").read_text()
html = html.replace("/*__REPORT__*/null", json.dumps(report, separators=(",", ":")))
html = html.replace("/*__APP__*/", js)
(here / "index.html").write_text(html)
(root / "build-test").mkdir(exist_ok=True)
(root / "build-test" / "bundle.mjs").write_text(js)
print(f"built frontend/index.html ({len(html) / 1024:.0f} KB)")
