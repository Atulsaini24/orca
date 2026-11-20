import { createReadStream, existsSync, statSync } from 'node:fs';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

const frontend = fileURLToPath(new URL('../frontend/', import.meta.url));
const TYPES: Record<string, string> = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8' };

// `npm run dev` serves the control room (/app) and its city data (/app/data/...) straight from frontend/, so the home
// page and the control room both work without the Python server; /api and /media still go to it when it runs.
const controlRoom = (): Plugin => ({
  name: 'orca-control-room',
  configureServer(server) {
    server.middlewares.use((req, res, next) => {
      const path = decodeURIComponent((req.url ?? '').split('?')[0]);
      const file = path === '/app' || path === '/app/' ? join(frontend, 'index.html')
        : path.startsWith('/app/data/') ? normalize(join(frontend, 'data', path.slice('/app/data/'.length))) : null;
      if (!file || !file.startsWith(normalize(frontend)) || !existsSync(file) || !statSync(file).isFile()) return next();
      res.setHeader('Content-Type', TYPES[extname(file)] ?? 'application/octet-stream');
      createReadStream(file).pipe(res);
    });
  },
});

// The home page is served by the ORCA server at "/"; the control room lives at "/app".
export default defineConfig({
  plugins: [react(), controlRoom()],
  server: {
    fs: { allow: ['..'] },
    proxy: { '/api': 'http://127.0.0.1:8000', '/media': 'http://127.0.0.1:8000' },
  },
  worker: { format: 'es' },
  build: {
    outDir: 'dist', emptyOutDir: true, sourcemap: true, chunkSizeWarningLimit: 900,
    rollupOptions: { output: { manualChunks: { three: ['three'], motion: ['framer-motion', 'lenis'] } } },
  },
});
