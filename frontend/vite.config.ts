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
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
        // Without this the dev server prints a bare "read ECONNRESET" stack
        // for the two cases that actually happen: the backend isn't running
        // (`make server`), or it is running and closed the socket before
        // accepting it — which is what /ws/ui does for an expired session
        // (ui_gateway.py closes with 4401 pre-accept, and Starlette turns
        // that into an HTTP 403 the proxy reports identically).
        configure: (proxy) => {
          proxy.on("error", (err) => {
            console.warn(
              `[ws proxy] ${err.message} — is the backend up (\`make server\`)? ` +
                "If it is, this is usually a signed-out /ws/ui session.",
            );
          });
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test-setup.ts"],
  },
});
