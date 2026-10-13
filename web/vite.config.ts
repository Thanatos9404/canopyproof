import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// In development the Python API (python -m canopyproof serve) runs on :8080.
const api = "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { "/api": api, "/data": api, "/rpc": api, "/vendor": api } },
  build: { chunkSizeWarningLimit: 900 },
});
