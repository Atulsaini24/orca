// ----------------------------------------------------------------------------- map labels: lift overlapping plates
// The explanation plates ("Request ...", "ORCA picks Car ...", "Nearest: Car ...") label points on the map. When those
// points project close together (zoomed out, or cars near each other) the plates would cover one another, so each
// is lifted above any plate it collides with, and its stem stretches down to the point it labels.
//
// boxes: [{ sx, sy, w, h }]: the labelled point on screen and the plate's size, in CSS pixels.
// Returns each plate's lift in pixels, in input order. Pure, so tests run it under Node (tests/test_labels.py).
//
// Each collision moves a plate strictly upward and half a pixel clear of the plate it hit, so it can never collide
// with that plate again: a plate is moved at most once per plate already placed, and the loop always ends. The
// earlier version set the plate's edge exactly onto the other plate's margin and re-tested the same overlap; with
// floating-point rounding the test could stay true forever and freeze the page while the map was zoomed.
function stackLabels(boxes, gap = 4, stem = 10) {
  const order = boxes.map((_, i) => i).sort((a, b) => boxes[b].sy - boxes[a].sy);   // lowest on screen first
  const placed = [], lift = new Array(boxes.length).fill(0);
  for (const i of order) {
    const { sx, sy, w, h } = boxes[i], l = sx - w / 2, r = sx + w / 2;
    if (![sx, sy, w, h].every(Number.isFinite)) continue;                             // not on screen: leave it unlifted
    let bottom = sy - stem;
    for (let moves = 0; moves <= placed.length; moves++) {
      const hit = placed.find((q) => l < q.r + gap && r > q.l - gap && bottom - h < q.b + gap && bottom > q.t - gap);
      if (!hit) break;
      bottom = hit.t - gap - 0.5;
    }
    lift[i] = sy - stem - bottom;
    placed.push({ l, r, t: bottom - h, b: bottom });
  }
  return lift;
}
