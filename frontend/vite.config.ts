import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // react-draggable (inside react-grid-layout) reads process.env at runtime;
  // without this shim every drag start throws "process is not defined".
  define: { "process.env": {} },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/auth": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test-setup.ts"],
  },
});
