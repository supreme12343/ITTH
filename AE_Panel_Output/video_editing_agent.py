"""
Video Editing Agent — Live After Effects control via HTTP + ExtendScript.

Usage:
    from video_editing_agent import VideoEditingAgent

    agent = VideoEditingAgent()
    agent.start_server()  # starts HTTP server on port 8080 (with fallback ports)
    agent.send_prompt("add text Hello World")
    agent.stop_server()

    # Get AE panel script to run in After Effects
    script = agent.get_ae_panel_script()
"""

import socket
import threading
import queue
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class VideoEditingAgent:
    """Controls After Effects via HTTP server + ExtendScript (JSX) commands."""

    # Default fallback ports for AE panel to try
    DEFAULT_PORTS = [8080, 6767, 6969, 6768, 6769]

    def __init__(self, host="127.0.0.1", port=8080, fallback_ports=None):
        self.host = host
        self.port = port
        self.fallback_ports = sorted(set((fallback_ports or self.DEFAULT_PORTS) + [port]))
        self._server = None
        self._server_thread = None
        self._command_queue = queue.Queue()
        self._running = False
        self._bound_port = None
        self._log_callback = None

    def set_log_callback(self, callback):
        """Set a callback function to receive log messages: callback(message: str)."""
        self._log_callback = callback

    def _log(self, msg):
        if self._log_callback:
            self._log_callback(msg)
        else:
            print(f"[VideoEditingAgent] {msg}")

    def _port_available(self, port):
        """Return True if the given port can be bound to."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((self.host, port))
                return True
            except OSError:
                return False

    def start_server(self):
        """Start the HTTP server in a background thread, trying each port in turn."""
        if self._running:
            self._log("Server already running")
            return True

        class AECommandHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                try:
                    cmd = self.server.agent._command_queue.get_nowait()
                    body = cmd.encode("utf-8")
                except queue.Empty:
                    body = b"NO_COMMAND"
                self.send_response(200)
                self.send_header("Content-type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except Exception:
                    pass

            def log_message(self, *args):
                return

        # Try candidate ports in order until one binds.
        for port in self.fallback_ports:
            if not self._port_available(port):
                self._log(f"Port {port} busy, trying next...")
                continue
            try:
                self._server = HTTPServer((self.host, port), AECommandHandler)
                self._server.agent = self
                self._bound_port = port
                self._log(f"Server started on http://{self.host}:{self.port} (bound port {port})")
                break
            except OSError as e:
                self._log(f"Failed to bind port {port}: {e}")
                continue

        if self._server is None:
            self._log("ERROR: No available port to bind the server. ")
            return False

        def run_server():
            try:
                self._server.serve_forever()
            except Exception as e:
                self._log(f"Server error: {e}")

        self._server_thread = threading.Thread(target=run_server, daemon=True)
        self._server_thread.start()
        self._running = True
        return True

    def stop_server(self):
        """Stop the HTTP server cleanly."""
        if not self._running or self._server is None:
            return
        self._running = False
        try:
            self._server.shutdown()
        except Exception:
            pass
        try:
            self._server.server_close()
        except Exception:
            pass
        self._log(f"Server stopped (port {self._bound_port})")
        self._server = None
        self._bound_port = None

    @property
    def bound_port(self):
        return self._bound_port

    def send_prompt(self, prompt: str) -> str:
        """Parse natural language prompt to JSX and queue it for AE execution."""
        if not prompt.strip():
            return ""
        jsx = self._parse_prompt(prompt)
        self._command_queue.put(jsx)
        self._log(f"Queued: '{prompt}'")
        return jsx

    def _parse_prompt(self, prompt_text: str) -> str:
        """Convert natural language to ExtendScript (JSX) for After Effects."""
        prompt = prompt_text.lower().strip()

        if "text" in prompt or "title" in prompt:
            clean_text = prompt_text
            for kw in ["add text", "text", "title"]:
                clean_text = clean_text.replace(kw, "").strip()
            if not clean_text:
                clean_text = prompt_text
            safe_text = clean_text.replace("\\", "\\\\").replace('"', '\\"')
            return (
                'var comp = app.project.activeItem;\n'
                'if (comp && comp instanceof CompItem) {\n'
                'var txt = comp.layers.addText("' + safe_text + '");\n'
                'txt.property("Position").setValue([comp.width / 2, comp.height / 2]);\n'
                '}'
            )
        elif "glow" in prompt or "shine" in prompt:
            return (
                'var comp = app.project.activeItem;\n'
                'if (comp && comp instanceof CompItem) {\n'
                'if (comp.selectedLayers.length > 0) {\n'
                'for (var i = 0; i < comp.selectedLayers.length; i++) {\n'
                'comp.selectedLayers[i].property("Effects").addProperty("ADBE Glow");\n'
                '}\n'
                '} else {\n'
                'var adj = comp.layers.addSolid([1,1,1], "AI_Glow_Pass", comp.width, comp.height, comp.pixelAspect, comp.duration);\n'
                'adj.adjustmentLayer = true;\n'
                'adj.property("Effects").addProperty("ADBE Glow");\n'
                '}\n'
                '}'
            )
        elif "color" in prompt or "grade" in prompt:
            return (
                'var comp = app.project.activeItem;\n'
                'if (comp && comp instanceof CompItem) {\n'
                'var colorAdj = comp.layers.addSolid([1,1,1], "AI_Color_Grade", comp.width, comp.height, comp.pixelAspect, comp.duration);\n'
                'colorAdj.adjustmentLayer = true;\n'
                'colorAdj.property("Effects").addProperty("ADBE Curves");\n'
                '}'
            )
        elif "render" in prompt:
            return (
                'var comp = app.project.activeItem;\n'
                'if (comp && comp instanceof CompItem) {\n'
                'app.project.renderQueue.items.add(comp);\n'
                '}'
            )
        else:
            safe = prompt_text.strip().replace("\\", "\\\\").replace('"', '\\"')
            return (
                'var comp = app.project.activeItem;\n'
                'if (comp && comp instanceof CompItem) {\n'
                'comp.layers.addSolid([0.2, 0.4, 0.8], "' + safe + '", comp.width, comp.height, comp.pixelAspect, comp.duration);\n'
                '}'
            )

    def get_ae_panel_script(self) -> str:
        """Return the ExtendScript (JSX) for the After Effects panel."""
        ports_js = ", ".join(str(p) for p in self.fallback_ports)
        return AI_LIVE_RECEIVER_JSX.format(ports_js=ports_js, host=self.host)


AI_LIVE_RECEIVER_JSX = '''// AI Live Receiver — fixed synchronous version
(function(thisObj) {{
    var win = (thisObj instanceof Panel) ? thisObj : new Window("palette", "AI Live Receiver", undefined, {{resizeable: true}});
    win.orientation = "column";
    win.alignChildren = "fill";

    var statusText = win.add("statictext", undefined, "AI Engine: Starting...");
    statusText.alignment = ["fill", "top"];

    var logArea = win.add("edittext", undefined, "", {{multiline: true, readonly: true}});
    logArea.preferredSize = [400, 250];

    var btnRow = win.add("group");
    btnRow.orientation = "row";
    btnRow.alignChildren = "left";
    var testBtn = btnRow.add("button", undefined, "Test Connection");
    var clearBtn = btnRow.add("button", undefined, "Clear Log");
    var stopBtn = btnRow.add("button", undefined, "Stop Polling");

    var pollInterval = 2000;
    var lastPoll = 0;
    var connected = false;
    var currentPortIndex = 0;
    var ports = [{ports_js}];
    var host = "{host}";
    var lastError = "";
    var stopped = false;

    function log(msg) {{
        var timestamp = new Date().toLocaleTimeString();
        logArea.text += "\\n[" + timestamp + "] " + msg;
        logArea.selection = logArea.text.length;
    }}

    // Synchronous HTTP GET. Returns an object {{ status, body }}.
    function httpGet(port) {{
        var socket = new Socket();
        var response = "";
        try {{
            if (!socket.open(host + ":" + port, "TCP")) {{
                return {{ status: "fail", body: "Socket open failed" }};
            }}
            var request = "GET / HTTP/1.0\\r\\n" +
                          "Host: " + host + ":" + port + "\\r\\n" +
                          "Connection: close\\r\\n\\r\\n";
            socket.write(request);
            var start = new Date().getTime();
            var timeout = 4000;
            var idle = 0;
            while (socket.connected && (new Date().getTime() - start) < timeout) {{
                var chunk = socket.read(1024);
                if (chunk) {{
                    response += chunk;
                    idle = 0;
                }} else {{
                    idle++;
                    if (idle > 200) break;
                    $.sleep(10);
                }}
            }}
            socket.close();
        }} catch (e) {{
            try {{ socket.close(); }} catch (e2) {{}}
            return {{ status: "fail", body: e.toString() }};
        }}

        // Parse header/body
        var split = response.indexOf("\\r\\n\\r\\n");
        var sepLen = 4;
        if (split < 0) {{
            split = response.indexOf("\\n\\n");
            sepLen = 2;
        }}
        if (split >= 0) {{
            var body = response.substring(split + sepLen).trim();
            if (body == "NO_COMMAND") return {{ status: "idle", body: "" }};
            if (body == "") return {{ status: "empty", body: "" }};
            return {{ status: "ok", body: body }};
        }}
        return {{ status: "fail", body: "No HTTP headers (" + response.length + " bytes)" }};
    }}

    function tryPort(port) {{
        var res = httpGet(port);
        return res;
    }}

    function pollServer() {{
        if (stopped) return;
        var now = new Date().getTime();
        if (now - lastPoll < pollInterval) return;
        lastPoll = now;

        var attempts = [];
        if (connected) {{
            attempts = [ports[currentPortIndex]];
        }}
        for (var i = 0; i < ports.length; i++) {{
            if (attempts.indexOf(ports[i]) < 0) attempts.push(ports[i]);
        }}

        var found = -1;
        var result = null;
        for (var j = 0; j < attempts.length; j++) {{
            var p = attempts[j];
            result = httpGet(p);
            if (result.status === "ok") {{
                found = p;
                break;
            }} else if (result.status === "idle") {{
                found = p;
                break;
            }}
        }}

        if (found >= 0) {{
            if (!connected) {{
                connected = true;
                currentPortIndex = ports.indexOf(found);
                statusText.text = "AI Engine: LIVE on Port " + found;
                log(">>> Connected to Python server on port " + found);
            }}
            if (result.status === "ok") {{
                try {{
                    app.beginUndoGroup("AI Agent Command");
                    var data = result.body;
                    if (data.length) eval(data);
                    app.endUndoGroup();
                    log(">>> EXECUTED: " + data.substring(0, 80) + (data.length > 80 ? "..." : ""));
                }} catch (e) {{
                    app.endUndoGroup();
                    log(">>> EXECUTION ERROR: " + e.toString());
                }}
            }}
        }} else {{
            if (connected) {{
                connected = false;
                statusText.text = "AI Engine: Disconnected";
                log("Lost connection. Last error: " + (result ? result.body : lastError));
            }}
        }}
    }}

    testBtn.onClick = function() {{
        log("=== Manual Test ===");
        for (var i = 0; i < ports.length; i++) {{
            var res = httpGet(ports[i]);
            if (res.status === "ok") {{
                log("Port " + ports[i] + " OK: " + res.body.substring(0, 60));
            }} else if (res.status === "idle") {{
                log("Port " + ports[i] + ": connected, waiting for commands");
            }} else {{
                log("Port " + ports[i] + " FAILED: " + res.body);
            }}
        }}
    }};

    clearBtn.onClick = function() {{
        logArea.text = "";
    }};

    stopBtn.onClick = function() {{
        stopped = true;
        statusText.text = "AI Engine: Stopped";
        log("Polling stopped.");
    }};

    win.onIdle = function() {{
        pollServer();
    }};

    win.layout.layout(true);
    log("=== AI Live Receiver Started ===");
    log("Target ports: " + ports.join(", "));
    log("Click 'Test Connection' to verify");
    log("Run: python video_editing_agent.py to host server");
    return win;
}})(this);
'''


# Standalone test
if __name__ == "__main__":
    import time
    agent = VideoEditingAgent()
    agent.set_log_callback(lambda m: print(m))
    if not agent.start_server():
        print("Failed to start server.")
        raise SystemExit(1)
    time.sleep(1)

    agent.send_prompt("add text Hello AE")
    time.sleep(1)
    agent.send_prompt("glow effect")
    time.sleep(1)
    agent.send_prompt("color grade")
    time.sleep(1)
    agent.send_prompt("render")

    print("\n--- Polling test (simulate AE panel) ---")
    import urllib.request
    time.sleep(0.5)
    try:
        with urllib.request.urlopen("http://127.0.0.1:" + str(agent.bound_port) + "/") as r:
            print("GET ->", r.read().decode("utf-8"))
    except Exception as e:
        print("GET failed:", e)

    time.sleep(1)
    agent.stop_server()
    print("\nDone.")