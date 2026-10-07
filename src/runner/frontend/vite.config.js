import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      manifest: {
        name: 'Corporate Travel Dispatch Intelligence',
        short_name: 'Dispatch Intel',
        description: 'Real-time TFR, weather, airspace, and ground-route dispatch intelligence.',
        theme_color: '#040812',
        background_color: '#040812',
        display: 'standalone',
        orientation: 'any',
        start_url: '/',
        scope: '/',
        icons: [
          { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
          { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
          { src: '/icons/icon-512-maskable.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // 2026-08-12: without this, Workbox's SPA navigateFallback catches
        // EVERY document-mode navigation -- including the knowledge-graph
        // iframe's GET to /api/v1/knowledge-graph/html -- and serves the
        // cached index.html shell instead, which is why the graph tab
        // rendered the whole app recursively nested inside itself instead
        // of the actual graph viz.
        // 2026-10-05: /console is the web app's operator console on the same
        // tailnet origin (nginx routes it to :8000). The fallback served this
        // dashboard's cached shell for it instead, so the console never loaded
        // in any browser that had opened the dashboard.
        navigateFallbackDenylist: [/^\/api\//, /^\/console/],
        runtimeCaching: [
          {
            urlPattern: /^\/api\//,
            handler: 'NetworkFirst',
            options: { cacheName: 'api-cache', networkTimeoutSeconds: 5 },
          },
        ],
      },
    }),
  ],
  server: {
    proxy: {
      '/api': { target: 'http://localhost:8001', changeOrigin: true },
    },
  },
  build: { outDir: '../static', target: 'esnext' },
})
