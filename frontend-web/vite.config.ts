/// <reference types="vitest/config" />
import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

// The repo-root .env holds VITE_API_URL / VITE_WS_URL next to the backend settings.
// `npm run dev:mock` (mode "mock") serves the UI on the MSW mocks (src/shared/lib/demo.ts).
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    // Spec 07 §1: installable customer app. The service worker exists only in production builds
    // (never next to the MSW mock worker) and never caches /api: a payment is always live.
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.svg", "apple-touch-icon.png"],
      manifest: {
        name: "FraudShield Pay",
        short_name: "FraudShield",
        description: "Safe payments: every payment is checked for scams before your money moves.",
        start_url: "/app",
        scope: "/",
        display: "standalone",
        orientation: "portrait",
        theme_color: "#0f766e",
        background_color: "#ffffff",
        icons: [
          { src: "pwa-192x192.png", sizes: "192x192", type: "image/png" },
          { src: "pwa-512x512.png", sizes: "512x512", type: "image/png" },
          { src: "maskable-512x512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,png,woff2}"],
        globIgnores: ["mockServiceWorker.js"],
        navigateFallback: "index.html",
        navigateFallbackDenylist: [/^\/api\//],
      },
      devOptions: { enabled: false },
    }),
  ],
  envDir: path.resolve(import.meta.dirname, ".."),
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  // /api (incl. WebSockets) goes to the backend: one origin for laptop and phone, no CORS (T4.5)
  server: { port: 5173, proxy: { "/api": { target: "http://127.0.0.1:8000", ws: true } } },
  preview: { port: 4173, proxy: { "/api": { target: "http://127.0.0.1:8000", ws: true } } },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"], // e2e/ is Playwright's
    css: false,
  },
});
