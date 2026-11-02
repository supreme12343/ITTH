import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
var uiRoot = fileURLToPath(new URL('.', import.meta.url));
export default defineConfig({
    root: uiRoot,
    base: './',
    plugins: [react()],
    server: { host: '127.0.0.1', port: 5173, strictPort: true },
    build: { target: 'esnext', outDir: '../ai-agent-overlay/dist', emptyOutDir: true },
    clearScreen: false,
});
