# Competition runbook

## PPT Controller local browser workflow

Run from PowerShell at the repository root. This launches the Python controller UI; use its **Start Services** control to start the loopback Python API and the Node UI/WebSocket server. The child Node process inherits these settings.

```powershell
$env:USE_HTTP = 'true'
$env:NODE_BIND_HOST = '127.0.0.1'
python .\pptcontroller.py
```

Open `http://127.0.0.1:8000/`. HTTP and HTTPS both bind to loopback by default. Keep `NODE_BIND_HOST` at `127.0.0.1` for local competition use. Do not set it to `0.0.0.0` unless you intentionally need a trusted network service.

Check that the UI, Socket.IO client, and Python API respond:

```powershell
$front = 'http://127.0.0.1:8000'
(Invoke-WebRequest -UseBasicParsing "$front/health").StatusCode
(Invoke-WebRequest -UseBasicParsing "$front/").StatusCode
(Invoke-WebRequest -UseBasicParsing "$front/socket.io/socket.io.js").StatusCode
(Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:9005/openapi.json').StatusCode
```

Each check should return `200`; the page should change from **CONNECTING...** to **ONLINE**. If PowerPoint is not open or no deck is loaded, an empty deck (`No Deck`, slide `1 / 0`) is expected and is separate from connection health.

## Campus Suite desktop launcher

The Campus Suite React/Electron launcher is separate from the PPT Controller. Start it from the repository root with:

```powershell
python ".\campus suite.py"
```

It requires Node.js/npm and the Electron dependencies under `ai-agent-overlay\node_modules`. The root `npm start` command starts the PPT Controller Node server; it does not launch the Campus Suite desktop app.

## Recovery and browser safety

- Do not use port `8123` as the PPT Controller UI: it serves a static copy without the same-origin Socket.IO server, so live status and controls cannot connect.
- If the controller reports that a port is already in use, check the existing service before starting another controller window; the app can attach to a healthy existing instance.
- If the HTTPS page shows a certificate warning, do not bypass it. Use the loopback-only HTTP workflow above for local competition use, or install a browser-trusted certificate that matches the HTTPS hostname before using HTTPS.
- Provider integrations without their corresponding environment variables remain disabled and report an unconfigured state; the local slide controls do not require provider credentials.
