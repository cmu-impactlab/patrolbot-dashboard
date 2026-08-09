import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // react-draggable (inside react-grid-layout) reads process.env at runtime;
  // without this shim every drag start throws "process is not defined".
  define: { "process.env": {} },
  build: {
    // scripts/check-bundle.mjs walks the entry's import graph from this.
    manifest: true,
    rollupOptions: {
      output: {
        // Split by how often things change, not by size: application code is
        // rebuilt constantly while these dependencies move a few times a year,
        // so keeping them in their own files means a dashboard deploy does not
        // invalidate a returning operator's cached copy of React and the
        // charts. Anything not matched here stays with the application chunk.
        //
        // Matching on the resolved module path rather than naming the packages
        // is what makes this work: the name form left `react` empty, because
        // react-dom and jsx-runtime pull React in through their own specifiers
        // and Rollup put the lot in the application chunk instead.
        manualChunks(id) {
          if (!id.includes("node_modules")) return;
          // The *last* node_modules segment is the package actually being
          // bundled: a nested install puts the real one after its parent, and
          // splitting on the first occurrence would classify it as the parent.
          const after = id.split("node_modules/").at(-1) ?? "";
          const segments = after.replace(/^\.pnpm\/[^/]+\/node_modules\//, "")
            .split("/");
          const pkg = segments[0].startsWith("@")
            ? `${segments[0]}/${segments[1]}`
            : segments[0];

          if (["react", "react-dom", "scheduler"].includes(pkg)) return "react";
          if (["recharts", "recharts-scale", "victory-vendor", "internmap",
               "decimal.js-light"].includes(pkg)
            || pkg.startsWith("d3-")) return "charts";
          if (["react-grid-layout", "react-draggable",
               "react-resizable"].includes(pkg)) return "grid";
          if (pkg === "lucide-react" || pkg.startsWith("@radix-ui/")) return "ui";
          if (pkg.startsWith("@tanstack/")) return "query";
        },
      },
    },
  },
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
