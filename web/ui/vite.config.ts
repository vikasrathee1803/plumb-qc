import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base './' so the built assets load when FastAPI serves dist at the root.
// The dev server proxies /api to the backend so the SPA is same-origin.
export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: "dist",
  },
});
