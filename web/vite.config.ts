import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The deployed policy is `default-src 'none'` with a per-request nonce for
// script and style. Two consequences drive every option below.
//
// 1. Nothing may be inlined. An inlined asset becomes a `data:` URI or an
//    inline <style>, and both are refused by that policy. assetsInlineLimit 0
//    forces real files for every asset regardless of size.
// 2. `connect-src` is absent from the policy and falls back to `default-src
//    'none'`, so a cross-origin fetch is refused. The app therefore talks to
//    same-origin relative paths only, and the proxy below makes that true in
//    development too.
const UPSTREAM = "https://mitos-reader-437828525303.europe-west1.run.app";

const api = [
  "/identity",
  "/config",
  "/catalog",
  "/metrics.json",
  "/standards.json",
  "/watch",
  "/thread",
  "/run",
  "/github",
];

export default defineConfig({
  plugins: [react()],
  build: {
    assetsInlineLimit: 0,
    cssCodeSplit: false,
    sourcemap: false,
  },
  server: {
    allowedHosts: true,
    proxy: Object.fromEntries(
      api.map((path) => [
        path,
        {
          target: UPSTREAM,
          changeOrigin: true,
          secure: true,
          // Streaming must not be buffered by the proxy, or the whole point of
          // /run/stream is lost in development.
          configure: (proxy: any) => {
            proxy.on("proxyRes", (proxyRes: any) => {
              delete proxyRes.headers["content-encoding"];
            });
          },
        },
      ])
    ),
  },
});
