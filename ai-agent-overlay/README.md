# Campus Suite Desktop

Campus Suite now uses a React interface hosted in a secure Electron window. Its existing Python tools continue to run as separate desktop applications.

The React components, styles, HTML entry, and TypeScript/Vite configuration live in the workspace-root `campus ui components/` folder, separate from this Electron shell.

## Start the desktop app

From this folder, run `npm install` once, then `npm start`. The existing `campus suite.py` launcher also starts the desktop app after dependencies are installed.

For interface development, run `npm run desktop:dev`. Electron starts the Vite server and opens the live React page from `../campus ui components/`.

The launcher checks each tool's file before enabling it. The code scanner remains visible as unavailable until `Vuln.py` is added.
