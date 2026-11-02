import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(new URL('../ai-agent-overlay/package.json', import.meta.url));
const { defineConfig } = require('vite');
const react = require('@vitejs/plugin-react').default;

const uiRoot = fileURLToPath(new URL('.', import.meta.url));
const packageRoot = fileURLToPath(new URL('../ai-agent-overlay/node_modules/', import.meta.url));

export default defineConfig({
  root: uiRoot,
  base: './',
  resolve: { alias: { react: `${packageRoot}react`, 'react-dom': `${packageRoot}react-dom` } },
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5173, strictPort: true },
  build: { target: 'esnext', outDir: '../ai-agent-overlay/dist', emptyOutDir: true },
  clearScreen: false,
});
