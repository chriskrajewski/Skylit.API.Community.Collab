/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';

const API = process.env.FSE_UI_API ?? 'http://127.0.0.1:8080';

/** Same-origin API/auth paths — keep inline in urlPattern so Workbox serializes it into the SW. */
const LIVE_NETWORK_ONLY = /\/(api|auth)(\/|$)/;

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['icons/icon-192.png', 'icons/icon-512.png'],
      manifest: {
        name: 'fse',
        short_name: 'fse',
        description: 'futures strategy engine console',
        start_url: '/live',
        scope: '/',
        display: 'standalone',
        // Web App Manifest requires absolute hex colors (not CSS var()); matches --surface-page.
        // eslint-disable-next-line no-restricted-syntax -- PWA manifest theme/background must be hex
        background_color: '#0a0a0a',
        // eslint-disable-next-line no-restricted-syntax -- PWA manifest theme/background must be hex
        theme_color: '#0a0a0a',
        icons: [
          { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
          { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
        ],
      },
      workbox: {
        // App shell + static assets only; no offline Live snapshot.
        globPatterns: ['**/*.{js,css,html,ico,png,svg,woff2,webp}'],
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api\//, /^\/auth\//],
        // Network-only for /api/* and /auth/* (all methods). WebSocket upgrades are not
        // handled by Workbox fetch handlers.
        runtimeCaching: [
          { urlPattern: LIVE_NETWORK_ONLY, handler: 'NetworkOnly', method: 'GET' },
          { urlPattern: LIVE_NETWORK_ONLY, handler: 'NetworkOnly', method: 'POST' },
          { urlPattern: LIVE_NETWORK_ONLY, handler: 'NetworkOnly', method: 'PUT' },
          { urlPattern: LIVE_NETWORK_ONLY, handler: 'NetworkOnly', method: 'DELETE' },
          { urlPattern: LIVE_NETWORK_ONLY, handler: 'NetworkOnly', method: 'PATCH' },
        ],
      },
      devOptions: { enabled: false },
    }),
  ],
  server: {
    port: 5173,
    strictPort: true,
    host: '127.0.0.1',
    proxy: {
      '/api': { target: API, ws: true },
      '/auth': { target: API },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // Fonts as files, never data: URIs: the CSP allows font-src 'self' only.
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 900,
  },
  test: {
    environment: 'jsdom',
    // Only unit tests: the Playwright specs in e2e/ are not vitest files.
    include: ['src/**/*.test.{ts,tsx}'],
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
