// Dedicated local UI preview: no backend proxy, no real robot socket, no login.
import { createServer, loadConfigFromFile } from "vite";
import { fileURLToPath } from "node:url";
import { homedir } from "node:os";
import { join } from "node:path";

const root = fileURLToPath(new URL("..", import.meta.url));
const loaded = await loadConfigFromFile({ command: "serve", mode: "development" }, join(root, "vite.config.ts"));
const cacheDir = join(homedir(), ".cache", "patrolbot-dashboard", "advanced-preview-vite");
const server = await createServer({ ...loaded.config, configFile: false, root, cacheDir,
  server: { ...loaded.config.server, proxy: undefined, host: "127.0.0.1", port: 4180, strictPort: true,
    fs: { allow: [root, fileURLToPath(new URL("../../contracts", import.meta.url)), cacheDir] } } });
await server.listen();
console.log("LOCAL SIMULATION ONLY — http://127.0.0.1:4180/advanced-preview.html?advanced=1");
server.printUrls();
