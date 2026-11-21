// GLSL for the journey. Night city: dark facades with lit windows, glowing streets and cars, bloom on top.

export const BUILDING_VS = /* glsl */ `
attribute vec4 info;              // footprint centre x, z, full height, random
uniform float uRise, uRadius;
uniform vec4 uFlat;                // x, z, radius, amount: buildings near a point sink so the streets show
varying vec3 vW; varying vec3 vN; varying vec2 vUv; varying float vY, vH, vRnd, vRise;
void main(){
  float d = length(info.xy) / uRadius;
  float g = clamp((uRise * 1.7 - d * 0.75 - info.w * 0.2) / 0.5, 0.0, 1.0);
  float e = 1.0 + 2.2 * pow(g - 1.0, 3.0) + 1.2 * pow(g - 1.0, 2.0);       // a little overshoot as each tower lands
  float fd = distance(info.xy, uFlat.xy);
  float fl = 1.0 - uFlat.w * 0.92 * (1.0 - smoothstep(uFlat.z * 0.55, uFlat.z, fd));
  vec3 p = position; p.y *= e * fl;
  vY = p.y; vH = info.z * e * fl; vRnd = info.w; vRise = g; vUv = uv; vN = normal;
  vec4 w = modelMatrix * vec4(p, 1.0); vW = w.xyz; vN = mat3(modelMatrix) * normal;
  gl_Position = projectionMatrix * viewMatrix * w;
}`;

export const BUILDING_FS = /* glsl */ `
uniform vec3 uFog; uniform float uFogD, uWin, uTime;
varying vec3 vW; varying vec3 vN; varying vec2 vUv; varying float vY, vH, vRnd, vRise;
float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
void main(){
  if (vRise <= 0.001) discard;
  vec3 n = normalize(vN);
  vec3 base = mix(vec3(0.055, 0.068, 0.095), vec3(0.12, 0.135, 0.17), vRnd);
  float light = 0.45 + 0.55 * max(dot(n, normalize(vec3(0.35, 0.8, 0.45))), 0.0);
  vec3 col = base * light;
  if (vUv.y < 0.0) {
    col = base * 1.35 + vec3(0.01, 0.012, 0.02);                        // roofs
  } else {
    vec2 c = vec2(vUv.x / 0.13, vY / 0.12), f = fract(c), cell = floor(c);
    float win = step(0.2, f.x) * step(f.x, 0.8) * step(0.28, f.y) * step(f.y, 0.8) * step(0.15, vY) * step(vY, vH - 0.08);
    float lit = step(0.6 - 0.12 * vRnd, hash(cell + vRnd * 91.0));
    vec3 warm = mix(vec3(1.0, 0.7, 0.36), vec3(0.7, 0.82, 1.0), step(0.82, hash(cell * 1.7 + 3.0)));
    col += win * lit * warm * (0.9 + 0.6 * hash(cell * 3.1)) * uWin;
  }
  // a band of taxi-yellow light rides the top of each building while it rises
  col += vec3(1.0, 0.76, 0.04) * 2.6 * smoothstep(vH - 0.5, vH, vY) * (1.0 - vRise) * step(0.02, vRise);
  float fog = 1.0 - exp(-distance(cameraPosition, vW) * uFogD);
  gl_FragColor = vec4(mix(col, uFog, fog), 1.0);
}`;

export const STREET_VS = /* glsl */ `
attribute float aMajor; attribute float aD;
varying float vMajor, vD; varying vec3 vW;
void main(){ vMajor = aMajor; vD = aD; vec4 w = modelMatrix * vec4(position, 1.0); vW = w.xyz; gl_Position = projectionMatrix * viewMatrix * w; }`;

export const STREET_FS = /* glsl */ `
uniform float uReveal, uAlpha, uFogD; uniform vec3 uFog;
varying float vMajor, vD; varying vec3 vW;
void main(){
  if (vD > uReveal) discard;
  float front = smoothstep(uReveal - 18.0, uReveal, vD);
  vec3 col = mix(vec3(0.16, 0.24, 0.36), vec3(0.35, 0.52, 0.78), vMajor) + front * vec3(1.0, 0.82, 0.3) * 2.2;
  float fog = 1.0 - exp(-distance(cameraPosition, vW) * uFogD * 0.7);
  gl_FragColor = vec4(col * (1.0 - fog), uAlpha * (0.55 + 0.45 * vMajor));
}`;

export const CAR_VS = /* glsl */ `
attribute vec3 aCol; uniform float uSize; varying vec3 vCol;
void main(){ vCol = aCol; vec4 mv = modelViewMatrix * vec4(position, 1.0); gl_PointSize = clamp(uSize * 260.0 / -mv.z, 1.5, 14.0); gl_Position = projectionMatrix * mv; }`;

export const CAR_FS = /* glsl */ `
uniform float uAlpha; varying vec3 vCol;
void main(){ float r = length(gl_PointCoord - 0.5); float a = smoothstep(0.5, 0.0, r); gl_FragColor = vec4(vCol * (0.6 + 1.6 * a * a), a * uAlpha); }`;

export const RIBBON_VS = /* glsl */ `
attribute float aT; attribute float aDelay; varying float vT, vDelay;
void main(){ vT = aT; vDelay = aDelay; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;

export const RIBBON_FS = /* glsl */ `
uniform float uProg, uDur, uAlpha; uniform vec3 uColor; varying float vT, vDelay;
void main(){
  float k = clamp((uProg - vDelay) / uDur, 0.0, 1.0);
  if (vT > k || k <= 0.0) discard;
  float head = smoothstep(k - 0.12, k, vT) * step(k, 0.999);
  gl_FragColor = vec4(uColor * (1.2 + head * 2.5), uAlpha);
}`;

export const GROUND_FS = /* glsl */ `
uniform vec3 uFog; varying vec3 vW;
void main(){
  vec2 g = abs(fract(vW.xz / 20.0) - 0.5);
  float line = 1.0 - smoothstep(0.0, 0.025, min(g.x, g.y));
  float fade = exp(-length(vW.xz) * 0.0022);
  gl_FragColor = vec4(uFog + vec3(0.03, 0.05, 0.08) * line * fade, 1.0);
}`;
export const GROUND_VS = /* glsl */ `varying vec3 vW; void main(){ vec4 w = modelMatrix * vec4(position, 1.0); vW = w.xyz; gl_Position = projectionMatrix * viewMatrix * w; }`;
