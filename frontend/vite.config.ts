/// <reference types="vitest" />
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// ROADMAP Stage 10.7z — the SPA is now mounted at the application
// root. The legacy `index.html` at the repo root was retired alongside
// this change, so `base` is '/' to match the FastAPI mount point and
// keep the emitted asset URLs (`<script src="/assets/...">`)
// reachable through `app.mount("/", StaticFiles(...))` in `main.py`.
export default defineConfig({
  base: '/',
  plugins: [vue()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    strictPort: false,
  },
  // ROADMAP Stage 10.2 — Vitest config lives alongside Vite so it
  // shares the same plugin pipeline (Vue SFC parsing, alias
  // resolution). `happy-dom` is faster than jsdom for the small DOM
  // surface we exercise (sessionStorage, Headers, fetch mock).
  test: {
    environment: 'happy-dom',
    globals: false,
    include: ['src/**/*.test.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
    },
  },
})
