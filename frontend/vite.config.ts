import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tsconfigPaths from "vite-tsconfig-paths";

const API_TARGET = process.env.VITE_API_URL || "http://localhost:8000";

// Client SPA. Dev-server proxy replaces the old next.config rewrites; in prod
// the built `dist/` is served behind the same reverse proxy as the API.
export default defineConfig({
  plugins: [react(), tsconfigPaths()],
  server: {
    port: 3000,
    proxy: {
      "/api/v1": {
        target: API_TARGET,
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
  },
});
