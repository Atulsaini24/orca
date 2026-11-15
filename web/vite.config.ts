import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The home page is served by the ORCA server at "/"; the control room lives at "/app".
// `npm run dev` proxies the API and the control room to a running `python -m orca.server`.
export default defineConfig({
  plugins: [react()],
  server: {
    fs: { allow: ['..'] },
    proxy: { '/api': 'http://127.0.0.1:8000', '/app': 'http://127.0.0.1:8000', '/media': 'http://127.0.0.1:8000' },
  },
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: true },
});
