import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// In dev and in e2e the console talks to P1 and P3 through same-origin proxies (/p1, /p3), so no CORS setup is needed locally.
// A production build uses VITE_P1_URL / VITE_P3_URL directly (those services then need the console origin allowed).
const P1 = process.env.P1_TARGET ?? 'http://127.0.0.1:8000'
const P3 = process.env.P3_TARGET ?? 'http://127.0.0.1:8002'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/p1': { target: P1, changeOrigin: true, rewrite: (p) => p.replace(/^\/p1/, '') },
      '/p3': { target: P3, changeOrigin: true, rewrite: (p) => p.replace(/^\/p3/, '') },
    },
  },
  preview: { port: 4173 },
  build: {
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks: {
          echarts: ['echarts', 'echarts-for-react'],
          react: ['react', 'react-dom', '@tanstack/react-router', '@tanstack/react-query'],
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
