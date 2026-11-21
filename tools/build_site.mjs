// Assemble ORCA as a static site (site/) for free static hosting (Vercel; see docs/DEPLOY.md).
//
//   npm ci --prefix web && npm run build --prefix web && node tools/build_site.mjs
//
// Same paths as the Python server, so every link keeps working:
//   /              home page (web/dist)
//   /app/          control room (frontend/index.html), city data under /app/data/
//   /media/...     tour video, captions, poster, music
// There is no /api: the control room's health check gets a 404 and it runs the dispatch simulation in the
// visitor's browser on the bundled real trips (the same decisions as the server, per frontend/build.py).
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SITE = join(ROOT, 'site');
const MEDIA = ['orca-tour.mp4', 'orca-tour.webm', 'orca-tour.vtt', 'orca-tour-poster.jpg', 'orca-tour-score.mp3', 'benchmark.png'];
const fail = (msg) => { console.error(`build_site: ${msg}`); process.exit(1); };
const mb = (bytes) => `${(bytes / 1e6).toFixed(1)} MB`;

for (const [path, how] of [['web/dist/index.html', 'npm run build --prefix web'], ['frontend/index.html', 'python frontend/build.py'],
  ['frontend/data/manhattan/streets.js', 'python backend/scripts/build_streets.py']]) {
  if (!existsSync(join(ROOT, path))) fail(`${path} is missing (${how})`);
}

rmSync(SITE, { recursive: true, force: true });
cpSync(join(ROOT, 'web', 'dist'), SITE, { recursive: true });
mkdirSync(join(SITE, 'app'), { recursive: true });
cpSync(join(ROOT, 'frontend', 'index.html'), join(SITE, 'app', 'index.html'));
cpSync(join(ROOT, 'frontend', 'data'), join(SITE, 'app', 'data'), { recursive: true });

// Git LFS stores the media; without LFS a checkout holds 130-byte pointer files instead (Vercel: enable Git LFS in
// the project's Git settings). The site still works without them, only the tour video and poster are missing.
mkdirSync(join(SITE, 'media'), { recursive: true });
let pointers = 0;
for (const name of MEDIA) {
  const src = join(ROOT, 'docs', name);
  if (!existsSync(src)) continue;
  const head = readFileSync(src).subarray(0, 40).toString('latin1');
  if (head.startsWith('version https://git-lfs')) { pointers++; continue; }
  cpSync(src, join(SITE, 'media', name));
}
if (pointers) console.warn(`build_site: ${pointers} media file(s) are Git LFS pointers and were left out: enable Git LFS for this checkout`);

const size = (p) => (statSync(p).isDirectory() ? readdirSync(p).reduce((a, e) => a + size(join(p, e)), 0) : statSync(p).size);
const cities = readdirSync(join(SITE, 'app', 'data')).filter((c) => existsSync(join(SITE, 'app', 'data', c, 'streets.js')));
console.log(`built site/: ${mb(size(SITE))}, cities ${cities.join(', ')}, media files ${readdirSync(join(SITE, 'media')).length}`);
