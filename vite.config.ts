import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// Конфигурации svelte.config.js нет намеренно: для сборки достаточно плагина,
// а корень репозитория занят Python-версией, и JSON-конфиг в корне ломает
// загрузчик Node (EOL). Компилятор Svelte работает с настройками по умолчанию.
const host = process.env["TAURI_DEV_HOST"];
const debug = !!process.env["TAURI_DEBUG"];

export default defineConfig({
  plugins: [svelte()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host ? { protocol: "ws", host, port: 1421 } : undefined,
  },
  envPrefix: ["VITE_", "TAURI_"],
  build: {
    target: "chrome110",
    minify: debug ? false : "esbuild",
    sourcemap: debug,
    outDir: "dist",
    emptyOutDir: true,
  },
});
