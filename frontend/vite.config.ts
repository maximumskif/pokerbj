import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies the local API; both bind to 127.0.0.1 only.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/ws": { target: "ws://127.0.0.1:8765", ws: true },
    },
  },
});
