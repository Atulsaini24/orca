// =============================================================================
// ORCA renderer: a small purpose-built WebGL2 engine.
// Instanced meshes, a soft shadow-mapped sun, hemisphere ambient with a cheap
// ground-contact occlusion term, screen-space fat lines, a water surface and
// a sky gradient. No 3D library: everything the city needs and nothing else.
// =============================================================================

// ---------------------------------------------------------------- math (column-major)
const M4 = {
  ident() { const m = new Float32Array(16); m[0] = m[5] = m[10] = m[15] = 1; return m; },
  mul(a, b) {
    const o = new Float32Array(16);
    for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
      o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    }
    return o;
  },
  persp(fovy, aspect, n, f) {
    const t = 1 / Math.tan(fovy / 2), m = new Float32Array(16);
    m[0] = t / aspect; m[5] = t; m[10] = (f + n) / (n - f); m[11] = -1; m[14] = (2 * f * n) / (n - f); return m;
  },
  ortho(l, r, b, t, n, f) {
    const m = M4.ident();
    m[0] = 2 / (r - l); m[5] = 2 / (t - b); m[10] = -2 / (f - n);
    m[12] = -(r + l) / (r - l); m[13] = -(t + b) / (t - b); m[14] = -(f + n) / (f - n); return m;
  },
  lookAt(e, c, u) {
    let zx = e[0] - c[0], zy = e[1] - c[1], zz = e[2] - c[2];
    let l = Math.hypot(zx, zy, zz); zx /= l; zy /= l; zz /= l;
    let xx = u[1] * zz - u[2] * zy, xy = u[2] * zx - u[0] * zz, xz = u[0] * zy - u[1] * zx;
    l = Math.hypot(xx, xy, xz); xx /= l; xy /= l; xz /= l;
    const yx = zy * xz - zz * xy, yy = zz * xx - zx * xz, yz = zx * xy - zy * xx;
    const m = M4.ident();
    m[0] = xx; m[4] = xy; m[8] = xz; m[1] = yx; m[5] = yy; m[9] = yz; m[2] = zx; m[6] = zy; m[10] = zz;
    m[12] = -(xx * e[0] + xy * e[1] + xz * e[2]); m[13] = -(yx * e[0] + yy * e[1] + yz * e[2]); m[14] = -(zx * e[0] + zy * e[1] + zz * e[2]);
    return m;
  },
  invert(m) {
    const inv = new Float32Array(16), a = m;
    inv[0] = a[5] * a[10] * a[15] - a[5] * a[11] * a[14] - a[9] * a[6] * a[15] + a[9] * a[7] * a[14] + a[13] * a[6] * a[11] - a[13] * a[7] * a[10];
    inv[4] = -a[4] * a[10] * a[15] + a[4] * a[11] * a[14] + a[8] * a[6] * a[15] - a[8] * a[7] * a[14] - a[12] * a[6] * a[11] + a[12] * a[7] * a[10];
    inv[8] = a[4] * a[9] * a[15] - a[4] * a[11] * a[13] - a[8] * a[5] * a[15] + a[8] * a[7] * a[13] + a[12] * a[5] * a[11] - a[12] * a[7] * a[9];
    inv[12] = -a[4] * a[9] * a[14] + a[4] * a[10] * a[13] + a[8] * a[5] * a[14] - a[8] * a[6] * a[13] - a[12] * a[5] * a[10] + a[12] * a[6] * a[9];
    inv[1] = -a[1] * a[10] * a[15] + a[1] * a[11] * a[14] + a[9] * a[2] * a[15] - a[9] * a[3] * a[14] - a[13] * a[2] * a[11] + a[13] * a[3] * a[10];
    inv[5] = a[0] * a[10] * a[15] - a[0] * a[11] * a[14] - a[8] * a[2] * a[15] + a[8] * a[3] * a[14] + a[12] * a[2] * a[11] - a[12] * a[3] * a[10];
    inv[9] = -a[0] * a[9] * a[15] + a[0] * a[11] * a[13] + a[8] * a[1] * a[15] - a[8] * a[3] * a[13] - a[12] * a[1] * a[11] + a[12] * a[3] * a[9];
    inv[13] = a[0] * a[9] * a[14] - a[0] * a[10] * a[13] - a[8] * a[1] * a[14] + a[8] * a[2] * a[13] + a[12] * a[1] * a[10] - a[12] * a[2] * a[9];
    inv[2] = a[1] * a[6] * a[15] - a[1] * a[7] * a[14] - a[5] * a[2] * a[15] + a[5] * a[3] * a[14] + a[13] * a[2] * a[7] - a[13] * a[3] * a[6];
    inv[6] = -a[0] * a[6] * a[15] + a[0] * a[7] * a[14] + a[4] * a[2] * a[15] - a[4] * a[3] * a[14] - a[12] * a[2] * a[7] + a[12] * a[3] * a[6];
    inv[10] = a[0] * a[5] * a[15] - a[0] * a[7] * a[13] - a[4] * a[1] * a[15] + a[4] * a[3] * a[13] + a[12] * a[1] * a[7] - a[12] * a[3] * a[5];
    inv[14] = -a[0] * a[5] * a[14] + a[0] * a[6] * a[13] + a[4] * a[1] * a[14] - a[4] * a[2] * a[13] - a[12] * a[1] * a[6] + a[12] * a[2] * a[5];
    inv[3] = -a[1] * a[6] * a[11] + a[1] * a[7] * a[10] + a[5] * a[2] * a[11] - a[5] * a[3] * a[10] - a[9] * a[2] * a[7] + a[9] * a[3] * a[6];
    inv[7] = a[0] * a[6] * a[11] - a[0] * a[7] * a[10] - a[4] * a[2] * a[11] + a[4] * a[3] * a[10] + a[8] * a[2] * a[7] - a[8] * a[3] * a[6];
    inv[11] = -a[0] * a[5] * a[11] + a[0] * a[7] * a[9] + a[4] * a[1] * a[11] - a[4] * a[3] * a[9] - a[8] * a[1] * a[7] + a[8] * a[3] * a[5];
    inv[15] = a[0] * a[5] * a[10] - a[0] * a[6] * a[9] - a[4] * a[1] * a[10] + a[4] * a[2] * a[9] + a[8] * a[1] * a[6] - a[8] * a[2] * a[5];
    const det = a[0] * inv[0] + a[1] * inv[4] + a[2] * inv[8] + a[3] * inv[12];
    for (let i = 0; i < 16; i++) inv[i] /= det || 1;
    return inv;
  },
  xform(m, x, y, z) {
    const w = m[3] * x + m[7] * y + m[11] * z + m[15];
    return [(m[0] * x + m[4] * y + m[8] * z + m[12]) / w, (m[1] * x + m[5] * y + m[9] * z + m[13]) / w, (m[2] * x + m[6] * y + m[10] * z + m[14]) / w, w];
  },
};
const srgb = (hex) => {
  const n = parseInt(hex.replace('#', ''), 16), f = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  return [f((n >> 16) & 255), f((n >> 8) & 255), f(n & 255)];
};

// ---------------------------------------------------------------- geometry builders
// A geometry is {pos, nor, col, mask, idx}. `mask` = 1 marks faces tinted by the instance colour.
class Geo {
  constructor() { this.pos = []; this.nor = []; this.col = []; this.mask = []; this.idx = []; }
  get count() { return this.pos.length / 3; }
  quad(a, b, c, d, color, mask = 0) {  // a,b,c,d counter-clockwise seen from outside
    const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2], vx = d[0] - a[0], vy = d[1] - a[1], vz = d[2] - a[2];
    let nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx; const l = Math.hypot(nx, ny, nz) || 1; nx /= l; ny /= l; nz /= l;
    const o = this.count;
    for (const p of [a, b, c, d]) { this.pos.push(...p); this.nor.push(nx, ny, nz); this.col.push(...color); this.mask.push(mask); }
    this.idx.push(o, o + 1, o + 2, o, o + 2, o + 3);
    return this;
  }
  tri(a, b, c, color, mask = 0) {
    const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2], vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2];
    let nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx; const l = Math.hypot(nx, ny, nz) || 1;
    const o = this.count;
    for (const p of [a, b, c]) { this.pos.push(...p); this.nor.push(nx / l, ny / l, nz / l); this.col.push(...color); this.mask.push(mask); }
    this.idx.push(o, o + 1, o + 2);
    return this;
  }
  // Frustum box: bottom rectangle at y0, top rectangle (possibly smaller / shifted) at y1.
  frustum(b, t, y0, y1, color, mask = 0, { bottom = false } = {}) {
    const [bx0, bx1, bz0, bz1] = b, [tx0, tx1, tz0, tz1] = t;
    const B = [[bx0, y0, bz0], [bx1, y0, bz0], [bx1, y0, bz1], [bx0, y0, bz1]];
    const T = [[tx0, y1, tz0], [tx1, y1, tz0], [tx1, y1, tz1], [tx0, y1, tz1]];
    this.quad(T[3], T[2], T[1], T[0], color, mask);                    // top (+y)
    if (bottom) this.quad(B[0], B[1], B[2], B[3], color, mask);
    this.quad(B[3], B[2], T[2], T[3], color, mask);                    // +z
    this.quad(B[1], B[0], T[0], T[1], color, mask);                    // -z
    this.quad(B[2], B[1], T[1], T[2], color, mask);                    // +x
    this.quad(B[0], B[3], T[3], T[0], color, mask);                    // -x
    return this;
  }
  box(x0, x1, y0, y1, z0, z1, color, mask = 0, opts) { return this.frustum([x0, x1, z0, z1], [x0, x1, z0, z1], y0, y1, color, mask, opts); }
  cylinder(cx, cz, r, y0, y1, color, seg = 10, mask = 0, axis = 'y') {
    for (let i = 0; i < seg; i++) {
      const a0 = (i / seg) * Math.PI * 2, a1 = ((i + 1) / seg) * Math.PI * 2;
      const c0 = Math.cos(a0) * r, s0 = Math.sin(a0) * r, c1 = Math.cos(a1) * r, s1 = Math.sin(a1) * r;
      const P = (u, v, h) => (axis === 'y' ? [cx + u, h, cz + v] : [h, cz + v, cx + u]);   // axis 'x': along x (wheels)
      this.quad(P(c0, s0, y0), P(c0, s0, y1), P(c1, s1, y1), P(c1, s1, y0), color, mask);
      if (axis === 'y') this.tri(P(0, 0, y1), P(c1, s1, y1), P(c0, s0, y1), color, mask);
      else { this.tri(P(0, 0, y1), P(c0, s0, y1), P(c1, s1, y1), color, mask); this.tri(P(0, 0, y0), P(c1, s1, y0), P(c0, s0, y0), color, mask); }
    }
    return this;
  }
  cone(cx, cz, r, y0, y1, color, seg = 10, mask = 0) {
    for (let i = 0; i < seg; i++) {
      const a0 = (i / seg) * Math.PI * 2, a1 = ((i + 1) / seg) * Math.PI * 2;
      this.tri([cx + Math.cos(a0) * r, y0, cz + Math.sin(a0) * r], [cx, y1, cz], [cx + Math.cos(a1) * r, y0, cz + Math.sin(a1) * r], color, mask);
    }
    return this;
  }
  sphere(cx, cy, cz, r, color, seg = 10, rings = 7, mask = 0, squash = 1) {
    const P = (i, j) => { const th = (i / seg) * Math.PI * 2, ph = (j / rings) * Math.PI; return [cx + Math.sin(ph) * Math.cos(th) * r, cy + Math.cos(ph) * r * squash, cz + Math.sin(ph) * Math.sin(th) * r]; };
    for (let j = 0; j < rings; j++) for (let i = 0; i < seg; i++) {
      const a = P(i, j), b = P(i + 1, j), c = P(i + 1, j + 1), d = P(i, j + 1);
      if (j === 0) this.tri(a, c, d, color, mask); else if (j === rings - 1) this.tri(a, b, c, color, mask); else this.quad(a, d, c, b, color, mask);
    }
    return this;
  }
  ring(r0, r1, seg, color) {
    for (let i = 0; i < seg; i++) {
      const a0 = (i / seg) * Math.PI * 2, a1 = ((i + 1) / seg) * Math.PI * 2;
      this.quad([Math.cos(a0) * r1, 0, Math.sin(a0) * r1], [Math.cos(a0) * r0, 0, Math.sin(a0) * r0], [Math.cos(a1) * r0, 0, Math.sin(a1) * r0], [Math.cos(a1) * r1, 0, Math.sin(a1) * r1], color, 1);
    }
    return this;
  }
}

// ---------------------------------------------------------------- shaders
const VS_MESH = `#version 300 es
layout(location=0) in vec3 aPos; layout(location=1) in vec3 aNor; layout(location=2) in vec3 aCol; layout(location=3) in float aMask;
layout(location=4) in vec3 iOff; layout(location=5) in vec3 iScale; layout(location=6) in float iRot; layout(location=7) in vec4 iCol;
uniform mat4 uVP; uniform mat4 uLVP;
out vec3 vW; out vec3 vN; out vec3 vCol; out float vA; out vec4 vL; out vec3 vLoc; out vec3 vSc;
void main(){
  float c = cos(iRot), s = sin(iRot);
  vec3 p = aPos * iScale;
  vec3 w = vec3(c*p.x + s*p.z, p.y, -s*p.x + c*p.z) + iOff;
  vec3 n = normalize(aNor / iScale);
  vN = vec3(c*n.x + s*n.z, n.y, -s*n.x + c*n.z);
  vW = w; vCol = mix(aCol, iCol.rgb, aMask); vA = iCol.a; vL = uLVP * vec4(w, 1.0); vLoc = aPos; vSc = iScale;
  gl_Position = uVP * vec4(w, 1.0);
}`;
const FS_MESH = `#version 300 es
precision highp float; precision highp sampler2DShadow;
in vec3 vW; in vec3 vN; in vec3 vCol; in float vA; in vec4 vL; in vec3 vLoc; in vec3 vSc;
uniform vec3 uSun, uSunCol, uSky, uGround, uCam, uFog; uniform vec2 uFogR;
uniform float uUnlit, uFacade, uShadowOn, uExposure, uAO; uniform int uPcf;
uniform sampler2DShadow uShadow;
out vec4 outColor;
float shadowAt(vec4 l){
  vec3 p = l.xyz / l.w * 0.5 + 0.5;
  if (p.x < 0.0 || p.x > 1.0 || p.y < 0.0 || p.y > 1.0 || p.z > 1.0) return 1.0;
  vec2 t = 1.0 / vec2(textureSize(uShadow, 0));
  float s = 0.0;
  float k = float(uPcf), n = 0.0;
  for (int x = -uPcf; x <= uPcf; x++) for (int y = -uPcf; y <= uPcf; y++) { s += texture(uShadow, vec3(p.xy + vec2(x, y) * t * (1.7 / max(k, 1.0)), p.z - 0.0012)); n += 1.0; }
  return s / n;
}
vec3 aces(vec3 x){ return clamp((x*(2.51*x + 0.03)) / (x*(2.43*x + 0.59) + 0.14), 0.0, 1.0); }
void main(){
  vec3 col = vCol;
  if (uUnlit < 0.5) {
    vec3 n = normalize(vN);
    float ndl = max(dot(n, uSun), 0.0);
    float sh = uShadowOn > 0.5 ? shadowAt(vL) : 1.0;
    vec3 amb = mix(uGround, uSky, n.y * 0.5 + 0.5);
    float ao = mix(1.0 - uAO, 1.0, smoothstep(0.0, 3.5, vW.y + 0.2));
    if (uFacade > 0.5) {
      // Engraved floor lines and faint window bays on walls; brighter rims where edges catch light.
      vec3 m = vLoc * vSc;
      float wall = 1.0 - abs(n.y);
      float floorLine = smoothstep(0.06, 0.0, abs(fract(m.y / 1.15) - 0.5) - 0.44) * step(0.9, m.y);
      float along = abs(n.x) > 0.5 ? m.z : m.x;
      float bay = smoothstep(0.05, 0.0, abs(fract(along / 1.3) - 0.5) - 0.45);
      col *= 1.0 - wall * (0.045 * floorLine + 0.02 * bay);
      vec2 e2 = abs(n.y) > 0.5 ? vec2(0.5*vSc.x - abs(m.x), 0.5*vSc.z - abs(m.z)) : vec2((abs(n.x) > 0.5 ? 0.5*vSc.z - abs(m.z) : 0.5*vSc.x - abs(m.x)), vSc.y - m.y);
      float rim = 1.0 - smoothstep(0.0, 0.18, min(e2.x, e2.y));
      col *= 1.0 + 0.10 * rim;
    }
    col = col * (amb * ao + uSunCol * ndl * sh);
  }
  float d = distance(uCam, vW);
  vec3 c = aces(col * uExposure);
  c = pow(c, vec3(1.0 / 2.2));
  c = mix(c, uFog, smoothstep(uFogR.x, uFogR.y, d));
  outColor = vec4(c, vA);
}`;
const FS_DEPTH = `#version 300 es
precision mediump float; out vec4 o; void main(){ o = vec4(1.0); }`;

const VS_LINE = `#version 300 es
layout(location=0) in vec2 aCorner;
layout(location=1) in vec3 iA; layout(location=2) in vec3 iB; layout(location=3) in vec4 iCol; layout(location=4) in float iW;
uniform mat4 uVP; uniform vec2 uRes;
out vec4 vCol;
void main(){
  vec4 a = uVP * vec4(iA, 1.0), b = uVP * vec4(iB, 1.0);
  vec2 sa = a.xy / a.w, sb = b.xy / b.w;
  vec2 dir = (sb - sa) * uRes; float l = length(dir); dir = l > 1e-5 ? dir / l : vec2(1.0, 0.0);
  vec2 nrm = vec2(-dir.y, dir.x);
  vec4 p = mix(a, b, aCorner.x);
  p.xy += nrm * aCorner.y * iW / uRes * p.w;
  gl_Position = p; vCol = iCol;
}`;
const FS_LINE = `#version 300 es
precision mediump float; in vec4 vCol; out vec4 o; void main(){ o = vCol; }`;

const VS_PLANE = `#version 300 es
layout(location=0) in vec2 aXZ; uniform mat4 uVP; uniform vec4 uRect; uniform float uY;
out vec2 vUV; out vec3 vW;
void main(){ vec3 w = vec3(uRect.x + aXZ.x * uRect.z, uY, uRect.y + aXZ.y * uRect.w); vW = w; vUV = aXZ; gl_Position = uVP * vec4(w, 1.0); }`;
const FS_HEAT = `#version 300 es
precision mediump float; in vec2 vUV; uniform sampler2D uTex; uniform float uAlpha; out vec4 o;
void main(){ vec4 t = texture(uTex, vUV); o = vec4(t.rgb, t.a * uAlpha); }`;
const FS_WATER = `#version 300 es
precision highp float; in vec2 vUV; in vec3 vW; uniform float uTime; uniform vec3 uCam, uFog, uDeep, uShallow, uSun; uniform vec2 uFogR, uCity; out vec4 o;
float n2(vec2 p){ return sin(p.x) * cos(p.y); }
void main(){
  vec2 p = vW.xz * 0.07;
  float w = n2(p + uTime * 0.25) * 0.5 + n2(p * 2.1 - uTime * 0.2) * 0.3 + n2(p * 4.3 + uTime * 0.35) * 0.2;
  vec3 nrm = normalize(vec3(w * 0.12, 1.0, w * 0.1));
  vec3 v = normalize(uCam - vW);
  float spec = pow(max(dot(reflect(-uSun, nrm), v), 0.0), 60.0);
  float shore = smoothstep(26.0, 2.0, max(abs(vW.x - uCity.x) - uCity.y, abs(vW.z - uCity.x) - uCity.y));
  vec3 c = mix(uDeep, uShallow, shore * 0.8) + spec * 0.35 + w * 0.012;
  float d = distance(uCam, vW);
  o = vec4(mix(c, uFog, smoothstep(uFogR.x, uFogR.y, d)), 1.0);
}`;
const VS_SKY = `#version 300 es
layout(location=0) in vec2 aXZ; out vec2 vP; void main(){ vP = aXZ; gl_Position = vec4(aXZ * 2.0 - 1.0, 0.9999, 1.0); }`;
const FS_SKY = `#version 300 es
precision mediump float; in vec2 vP; uniform vec3 uTop, uBot; out vec4 o; void main(){ o = vec4(mix(uBot, uTop, smoothstep(0.2, 1.0, vP.y)), 1.0); }`;

// ---------------------------------------------------------------- renderer
const INST_FLOATS = 11;   // off(3) scale(3) rot(1) color(4)

class Renderer {
  constructor(canvas, opts = {}) {
    const gl = canvas.getContext('webgl2', { antialias: true, alpha: false, preserveDrawingBuffer: true, powerPreference: 'high-performance' });
    if (!gl) throw new Error('WebGL2 is not available');
    this.gl = gl; this.canvas = canvas; this.layers = []; this.lineLayers = []; this.time = 0;
    this.pMesh = this.program(VS_MESH, FS_MESH);
    this.pDepth = this.program(VS_MESH, FS_DEPTH);
    this.pLine = this.program(VS_LINE, FS_LINE);
    this.pHeat = this.program(VS_PLANE, FS_HEAT);
    this.pWater = this.program(VS_PLANE, FS_WATER);
    this.pSky = this.program(VS_SKY, FS_SKY);
    // unit quad (for planes / sky) and line corner quad
    this.quadVao = this.vao([{ loc: 0, size: 2, data: new Float32Array([0, 0, 1, 0, 1, 1, 0, 0, 1, 1, 0, 1]) }]);
    this.shadowSize = opts.shadowSize || 2048; this.pcf = opts.pcf ?? 2;
    this.shadowTex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.shadowTex);
    gl.texStorage2D(gl.TEXTURE_2D, 1, gl.DEPTH_COMPONENT24, this.shadowSize, this.shadowSize);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_COMPARE_MODE, gl.COMPARE_REF_TO_TEXTURE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_COMPARE_FUNC, gl.LEQUAL);
    this.shadowFbo = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.shadowFbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.TEXTURE_2D, this.shadowTex, 0);
    gl.drawBuffers([gl.NONE]); gl.readBuffer(gl.NONE);
    // Static casters (buildings, trees) are drawn once into their own depth map; each frame that map is
    // copied and only moving casters (cars, pins) are drawn on top.
    this.staticRb = gl.createRenderbuffer();
    gl.bindRenderbuffer(gl.RENDERBUFFER, this.staticRb);
    gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT24, this.shadowSize, this.shadowSize);
    this.staticFbo = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.staticFbo);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, this.staticRb);
    gl.drawBuffers([gl.NONE]); gl.readBuffer(gl.NONE);
    this.staticDirty = true;
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    this.light = { dir: [0, 1, 0], color: [1, 1, 1], sky: [0.5, 0.5, 0.5], ground: [0.3, 0.3, 0.3] };
    this.fog = [0.9, 0.9, 0.9]; this.fogRange = [600, 1600]; this.exposure = 1; this.ao = 0.35;
    this.planes = [];
  }
  program(vs, fs) {
    const gl = this.gl, mk = (t, s) => { const sh = gl.createShader(t); gl.shaderSource(sh, s); gl.compileShader(sh); if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh) + '\n' + s); return sh; };
    const p = gl.createProgram(); gl.attachShader(p, mk(gl.VERTEX_SHADER, vs)); gl.attachShader(p, mk(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    const u = {}; const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
    for (let i = 0; i < n; i++) { const info = gl.getActiveUniform(p, i); u[info.name] = gl.getUniformLocation(p, info.name); }
    return { p, u };
  }
  vao(attrs, idx) {
    const gl = this.gl, v = gl.createVertexArray(); gl.bindVertexArray(v);
    for (const a of attrs) {
      const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, a.data, a.usage || gl.STATIC_DRAW);
      gl.enableVertexAttribArray(a.loc); gl.vertexAttribPointer(a.loc, a.size, gl.FLOAT, false, 0, 0);
      a.buffer = b;
    }
    if (idx) { const ib = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW); }
    gl.bindVertexArray(null);
    return v;
  }
  // An instanced mesh layer: geometry + up to `max` instances.
  mesh(geo, max, opts = {}) {
    const gl = this.gl, v = gl.createVertexArray(); gl.bindVertexArray(v);
    const put = (loc, size, arr) => { const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(arr), gl.STATIC_DRAW); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0); };
    put(0, 3, geo.pos); put(1, 3, geo.nor); put(2, 3, geo.col); put(3, 1, geo.mask);
    const big = geo.count > 65535, ib = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, big ? new Uint32Array(geo.idx) : new Uint16Array(geo.idx), gl.STATIC_DRAW);
    const data = new Float32Array(max * INST_FLOATS), ibuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, ibuf); gl.bufferData(gl.ARRAY_BUFFER, data.byteLength, opts.dynamic ? gl.DYNAMIC_DRAW : gl.STATIC_DRAW);
    const S = INST_FLOATS * 4;
    [[4, 3, 0], [5, 3, 3], [6, 1, 6], [7, 4, 7]].forEach(([loc, size, off]) => {
      gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, size, gl.FLOAT, false, S, off * 4); gl.vertexAttribDivisor(loc, 1);
    });
    gl.bindVertexArray(null);
    const layer = { vao: v, ibuf, data, max, n: 0, elems: geo.idx.length, itype: big ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT,
      cast: !!opts.cast, dynamic: !!opts.dynamic, unlit: !!opts.unlit, facade: !!opts.facade, alpha: !!opts.alpha, shadowed: opts.shadowed !== false,
      order: opts.order || 0, visible: true, dirty: true, depthWrite: opts.depthWrite !== false,
      set(i, x, y, z, sx, sy, sz, rot, r, g, b, a = 1) {
        const o = i * INST_FLOATS, d = this.data;
        d[o] = x; d[o + 1] = y; d[o + 2] = z; d[o + 3] = sx; d[o + 4] = sy; d[o + 5] = sz; d[o + 6] = rot; d[o + 7] = r; d[o + 8] = g; d[o + 9] = b; d[o + 10] = a;
      },
      commit(n) { this.n = Math.min(n, this.max); this.dirty = true; } };
    this.layers.push(layer); this.layers.sort((a, b) => a.order - b.order);
    return layer;
  }
  lines(max, opts = {}) {
    const gl = this.gl, v = gl.createVertexArray(); gl.bindVertexArray(v);
    const cb = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, cb);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, -1, 1, -1, 1, 1, 0, -1, 1, 1, 0, 1]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    const F = 11, data = new Float32Array(max * F), ib = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, ib); gl.bufferData(gl.ARRAY_BUFFER, data.byteLength, gl.DYNAMIC_DRAW);
    [[1, 3, 0], [2, 3, 3], [3, 4, 6], [4, 1, 10]].forEach(([loc, size, off]) => {
      gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, size, gl.FLOAT, false, F * 4, off * 4); gl.vertexAttribDivisor(loc, 1);
    });
    gl.bindVertexArray(null);
    const L = { vao: v, ib, data, max, n: 0, order: opts.order || 0, visible: true, dirty: true, depth: opts.depth !== false,
      reset() { this.n = 0; },
      seg(ax, ay, az, bx, by, bz, r, g, b, a, w) {
        if (this.n >= this.max) return; const o = this.n * F, d = this.data;
        d[o] = ax; d[o + 1] = ay; d[o + 2] = az; d[o + 3] = bx; d[o + 4] = by; d[o + 5] = bz; d[o + 6] = r; d[o + 7] = g; d[o + 8] = b; d[o + 9] = a; d[o + 10] = w; this.n++;
      },
      circle(x, y, z, rad, r, g, b, a, w, seg = 72, from = 0, to = 1) {
        for (let i = 0; i < seg; i++) {
          const f0 = from + (to - from) * (i / seg), f1 = from + (to - from) * ((i + 1) / seg);
          const a0 = f0 * Math.PI * 2, a1 = f1 * Math.PI * 2;
          this.seg(x + Math.cos(a0) * rad, y, z + Math.sin(a0) * rad, x + Math.cos(a1) * rad, y, z + Math.sin(a1) * rad, r, g, b, a, w);
        }
      },
      commit() { this.dirty = true; } };
    this.lineLayers.push(L); this.lineLayers.sort((a, b) => a.order - b.order);
    return L;
  }
  heatTexture(n) {
    const gl = this.gl, t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, n, n, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array(n * n * 4));
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return { tex: t, n, update: (data) => { gl.bindTexture(gl.TEXTURE_2D, t); gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, n, n, gl.RGBA, gl.UNSIGNED_BYTE, data); } };
  }
  resize(w, h, dpr) {
    this.canvas.width = Math.max(1, Math.round(w * dpr)); this.canvas.height = Math.max(1, Math.round(h * dpr));
    this.w = w; this.h = h; this.dpr = dpr;
  }
  upload(layer, F) {
    if (!layer.dirty) return;
    const gl = this.gl; gl.bindBuffer(gl.ARRAY_BUFFER, layer.ibuf || layer.ib);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, layer.data, 0, Math.max(1, layer.n) * F);
    layer.dirty = false;
  }
  meshUniforms(P, vp, lvp, cam) {
    const gl = this.gl, u = P.u, L = this.light;
    gl.uniformMatrix4fv(u.uVP, false, vp); gl.uniformMatrix4fv(u.uLVP, false, lvp);
    if (u.uSun) {
      gl.uniform3fv(u.uSun, L.dir); gl.uniform3fv(u.uSunCol, L.color); gl.uniform3fv(u.uSky, L.sky); gl.uniform3fv(u.uGround, L.ground);
      gl.uniform3fv(u.uCam, cam); gl.uniform3fv(u.uFog, this.fog); gl.uniform2fv(u.uFogR, this.fogRange);
      gl.uniform1f(u.uExposure, this.exposure); gl.uniform1f(u.uAO, this.ao); gl.uniform1i(u.uPcf, this.pcf);
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this.shadowTex); gl.uniform1i(u.uShadow, 0);
    }
  }
  render(cam) {
    const gl = this.gl, L = this.light;
    for (const l of this.layers) this.upload(l, INST_FLOATS);
    for (const l of this.lineLayers) this.upload(l, 11);
    // light camera: orthographic, looking along the sun direction at the city centre
    const c = cam.lightCenter, R = cam.lightRadius;
    const eye = [c[0] + L.dir[0] * 600, c[1] + L.dir[1] * 600, c[2] + L.dir[2] * 600];
    const lvp = M4.mul(M4.ortho(-R, R, -R, R, 200, 1100), M4.lookAt(eye, c, [0, 1, 0]));
    // shadow pass: cached static casters + per-frame dynamic casters
    const S = this.shadowSize, casters = (dyn) => {
      for (const l of this.layers) if (l.cast && l.visible && l.n && !!l.dynamic === dyn) { gl.bindVertexArray(l.vao); gl.drawElementsInstanced(gl.TRIANGLES, l.elems, l.itype, 0, l.n); }
    };
    gl.viewport(0, 0, S, S); gl.enable(gl.DEPTH_TEST); gl.depthMask(true); gl.disable(gl.BLEND);
    gl.enable(gl.POLYGON_OFFSET_FILL); gl.polygonOffset(2, 4);
    gl.useProgram(this.pDepth.p); gl.uniformMatrix4fv(this.pDepth.u.uVP, false, lvp);
    if (this.staticDirty) {
      gl.bindFramebuffer(gl.FRAMEBUFFER, this.staticFbo); gl.clear(gl.DEPTH_BUFFER_BIT); casters(false); this.staticDirty = false;
    }
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER, this.staticFbo); gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, this.shadowFbo);
    gl.blitFramebuffer(0, 0, S, S, 0, 0, S, S, gl.DEPTH_BUFFER_BIT, gl.NEAREST);
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.shadowFbo);
    casters(true);
    gl.disable(gl.POLYGON_OFFSET_FILL);
    // main pass
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.clearColor(this.fog[0], this.fog[1], this.fog[2], 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    // sky
    gl.depthMask(false); gl.useProgram(this.pSky.p); gl.uniform3fv(this.pSky.u.uTop, this.skyTop); gl.uniform3fv(this.pSky.u.uBot, this.fog);
    gl.bindVertexArray(this.quadVao); gl.drawArrays(gl.TRIANGLES, 0, 6); gl.depthMask(true);
    // water
    if (this.water) {
      const P = this.pWater, u = P.u; gl.useProgram(P.p);
      gl.uniformMatrix4fv(u.uVP, false, cam.vp); gl.uniform4fv(u.uRect, this.water.rect); gl.uniform1f(u.uY, this.water.y);
      gl.uniform1f(u.uTime, this.time); gl.uniform3fv(u.uCam, cam.eye); gl.uniform3fv(u.uFog, this.fog); gl.uniform2fv(u.uFogR, this.fogRange);
      gl.uniform3fv(u.uDeep, this.water.deep); gl.uniform3fv(u.uShallow, this.water.shallow); gl.uniform3fv(u.uSun, L.dir); gl.uniform2fv(u.uCity, this.water.city);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
    }
    // opaque meshes
    gl.useProgram(this.pMesh.p); this.meshUniforms(this.pMesh, cam.vp, lvp, cam.eye);
    const u = this.pMesh.u;
    const drawMesh = (l) => {
      gl.uniform1f(u.uUnlit, l.unlit ? 1 : 0); gl.uniform1f(u.uFacade, l.facade ? 1 : 0); gl.uniform1f(u.uShadowOn, l.shadowed ? 1 : 0);
      gl.bindVertexArray(l.vao); gl.drawElementsInstanced(gl.TRIANGLES, l.elems, l.itype, 0, l.n);
    };
    gl.disable(gl.BLEND);
    for (const l of this.layers) if (!l.alpha && l.visible && l.n) drawMesh(l);
    // translucent: heat wash, rings, then lines
    gl.enable(gl.BLEND); gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA); gl.depthMask(false);
    for (const pl of this.planes) if (pl.visible) {
      const P = this.pHeat; gl.useProgram(P.p); gl.uniformMatrix4fv(P.u.uVP, false, cam.vp);
      gl.uniform4fv(P.u.uRect, pl.rect); gl.uniform1f(P.u.uY, pl.y); gl.uniform1f(P.u.uAlpha, pl.alpha);
      gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, pl.tex.tex); gl.uniform1i(P.u.uTex, 1);
      gl.bindVertexArray(this.quadVao); gl.drawArrays(gl.TRIANGLES, 0, 6);
    }
    gl.useProgram(this.pMesh.p); this.meshUniforms(this.pMesh, cam.vp, lvp, cam.eye);
    for (const l of this.layers) if (l.alpha && l.visible && l.n) { if (l.depthWrite) gl.depthMask(true); drawMesh(l); gl.depthMask(false); }
    gl.useProgram(this.pLine.p); gl.uniformMatrix4fv(this.pLine.u.uVP, false, cam.vp); gl.uniform2f(this.pLine.u.uRes, this.canvas.width / this.dpr, this.canvas.height / this.dpr);
    for (const l of this.lineLayers) if (l.visible && l.n) {
      if (l.depth) gl.enable(gl.DEPTH_TEST); else gl.disable(gl.DEPTH_TEST);
      gl.bindVertexArray(l.vao); gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, l.n);
    }
    gl.enable(gl.DEPTH_TEST); gl.depthMask(true); gl.disable(gl.BLEND);
    gl.bindVertexArray(null);
  }
}

// ---------------------------------------------------------------- camera: orbit / pan / zoom-to-cursor with damping
class OrbitCam {
  constructor(target, yaw, pitch, dist) {
    this.t = [...target]; this.yaw = yaw; this.pitch = pitch; this.dist = dist;
    this.g = { t: [...target], yaw, pitch, dist };   // goal values the camera eases toward
    this.fov = 0.52; this.aspect = 1; this.tween = null;
    this.limits = { pitchMin: 0.18, pitchMax: 1.52, distMin: 40, distMax: 900, bound: 420 };
    this.update(1);
  }
  eyeOf(t, yaw, pitch, dist) { return [t[0] + Math.sin(yaw) * Math.cos(pitch) * dist, t[1] + Math.sin(pitch) * dist, t[2] + Math.cos(yaw) * Math.cos(pitch) * dist]; }
  clampGoal() {
    const g = this.g, L = this.limits;
    g.pitch = Math.max(L.pitchMin, Math.min(L.pitchMax, g.pitch));
    g.dist = Math.max(L.distMin, Math.min(L.distMax, g.dist));
    g.t[0] = Math.max(-L.bound + L.cx, Math.min(L.bound + L.cx, g.t[0])); g.t[2] = Math.max(-L.bound + L.cz, Math.min(L.bound + L.cz, g.t[2]));
  }
  rotate(dx, dy) { this.tween = null; this.g.yaw -= dx * 0.0055; this.g.pitch += dy * 0.0045; this.clampGoal(); }
  pan(dx, dy) {   // screen pixels → ground units, scaled by distance
    this.tween = null;
    // screen-right on the ground is (cos yaw, 0, -sin yaw); screen-up (forward) is (-sin yaw, 0, -cos yaw)
    const k = this.dist * 0.0016, c = Math.cos(this.yaw), s = Math.sin(this.yaw), fy = 1 / Math.max(0.5, Math.sin(this.pitch));
    this.g.t[0] += (-dx * c - dy * s * fy) * k;
    this.g.t[2] += (dx * s - dy * c * fy) * k;
    this.clampGoal();
  }
  panWorld(dx, dz) { this.tween = null; this.g.t[0] += dx; this.g.t[2] += dz; this.clampGoal(); }
  zoom(factor, ground) {   // zoom toward a ground point (or the target)
    this.tween = null;
    const nd = Math.max(this.limits.distMin, Math.min(this.limits.distMax, this.g.dist * factor));
    if (ground) { const f = 1 - nd / this.g.dist; this.g.t[0] += (ground[0] - this.g.t[0]) * f; this.g.t[2] += (ground[2] - this.g.t[2]) * f; }
    this.g.dist = nd; this.clampGoal();
  }
  flyTo(goal, dur = 1.4) {
    const from = { t: [...this.t], yaw: this.yaw, pitch: this.pitch, dist: this.dist };
    let dy = goal.yaw - from.yaw; while (dy > Math.PI) dy -= Math.PI * 2; while (dy < -Math.PI) dy += Math.PI * 2;
    const to = { t: goal.t ? [...goal.t] : [...from.t], yaw: from.yaw + (goal.yaw != null ? dy : 0), pitch: goal.pitch ?? from.pitch, dist: goal.dist ?? from.dist };
    if (goal.yaw == null) to.yaw = from.yaw;
    this.tween = { from, to, t: 0, dur: Math.max(0.001, dur) };
  }
  update(dt) {
    if (this.tween) {
      const tw = this.tween; tw.t = Math.min(1, tw.t + dt / tw.dur);
      const e = tw.t < 0.5 ? 4 * tw.t ** 3 : 1 - (-2 * tw.t + 2) ** 3 / 2;
      const L = (a, b) => a + (b - a) * e;
      // arc: lift the camera a little mid-flight so long moves read as a glide, not a slide
      const lift = Math.sin(Math.PI * e) * Math.min(160, Math.hypot(tw.to.t[0] - tw.from.t[0], tw.to.t[2] - tw.from.t[2]) * 0.35);
      this.t = [L(tw.from.t[0], tw.to.t[0]), 0, L(tw.from.t[2], tw.to.t[2])];
      this.yaw = L(tw.from.yaw, tw.to.yaw); this.pitch = L(tw.from.pitch, tw.to.pitch); this.dist = L(tw.from.dist, tw.to.dist) + lift;
      this.g = { t: [...this.t], yaw: this.yaw, pitch: this.pitch, dist: L(tw.from.dist, tw.to.dist) };
      if (tw.t >= 1) { this.tween = null; this.g.dist = this.dist = tw.to.dist; }
    } else {
      const k = 1 - Math.exp(-dt * 9);
      this.t[0] += (this.g.t[0] - this.t[0]) * k; this.t[2] += (this.g.t[2] - this.t[2]) * k;
      this.yaw += (this.g.yaw - this.yaw) * k; this.pitch += (this.g.pitch - this.pitch) * k; this.dist += (this.g.dist - this.dist) * k;
    }
    this.eye = this.eyeOf(this.t, this.yaw, this.pitch, this.dist);
    this.view = M4.lookAt(this.eye, this.t, [0, 1, 0]);
    this.proj = M4.persp(this.fov, this.aspect, 2, 4000);
    this.vp = M4.mul(this.proj, this.view);
    this.ivp = M4.invert(this.vp);
  }
  project(x, y, z, w, h) {   // → [screenX, screenY, visible]
    const p = M4.xform(this.vp, x, y, z);
    return [((p[0] + 1) / 2) * w, ((1 - p[1]) / 2) * h, p[3] > 0 && p[2] < 1];
  }
  ground(sx, sy, w, h) {    // screen point → point on y=0 plane
    const nx = (sx / w) * 2 - 1, ny = 1 - (sy / h) * 2;
    const a = M4.xform(this.ivp, nx, ny, -1), b = M4.xform(this.ivp, nx, ny, 1);
    const dy = b[1] - a[1]; if (Math.abs(dy) < 1e-6) return null;
    const t = -a[1] / dy; if (t < 0) return null;
    return [a[0] + (b[0] - a[0]) * t, 0, a[2] + (b[2] - a[2]) * t];
  }
}
