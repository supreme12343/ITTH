// AI Live Receiver Panel for After Effects — FIXED synchronous version
// Polls the Python HTTP server (video_editing_agent.py) for JSX commands
// Place this file in: C:\Program Files\Adobe\Adobe After Effects 2022\Support Files\Scripts\ScriptUI Panels\
// Then in AE: Window > Extensions > (restart) or open via the ScriptUI Panels menu.
(function(thisObj) {
    var win = (thisObj instanceof Panel) ? thisObj : new Window("palette", "AI Live Receiver", undefined, {resizeable: true});
    win.orientation = "column";
    win.alignChildren = "fill";

    var statusText = win.add("statictext", undefined, "AI Engine: Starting...");
    statusText.alignment = ["fill", "top"];

    var logArea = win.add("edittext", undefined, "", {multiline: true, readonly: true});
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
    var ports = [8080, 6767, 6969, 6768, 6769];
    var host = "127.0.0.1";
    var lastError = "";
    var stopped = false;

    function log(msg) {
        var timestamp = new Date().toLocaleTimeString();
        logArea.text += "\n[" + timestamp + "] " + msg;
        logArea.selection = logArea.text.length;
    }

    // Synchronous HTTP GET via Socket. Returns { status: "ok"|"idle"|"empty"|"fail", body }.
    function httpGet(port) {
        var socket = new Socket();
        var response = "";
        try {
            if (!socket.open(host + ":" + port, "TCP")) {
                return { status: "fail", body: "Socket open failed" };
            }
            var request = "GET / HTTP/1.0\r\n" +
                          "Host: " + host + ":" + port + "\r\n" +
                          "Connection: close\r\n\r\n";
            socket.write(request);
            var start = new Date().getTime();
            var timeout = 4000;
            var idle = 0;
            while (socket.connected && (new Date().getTime() - start) < timeout) {
                var chunk = socket.read(1024);
                if (chunk) {
                    response += chunk;
                    idle = 0;
                } else {
                    idle++;
                    if (idle > 200) break;
                    $.sleep(10);
                }
            }
            socket.close();
        } catch (e) {
            try { socket.close(); } catch (e2) {}
            return { status: "fail", body: e.toString() };
        }

        var split = response.indexOf("\r\n\r\n");
        var sepLen = 4;
        if (split < 0) {
            split = response.indexOf("\n\n");
            sepLen = 2;
        }
        if (split >= 0) {
            var body = response.substring(split + sepLen).trim();
            if (body == "NO_COMMAND") return { status: "idle", body: "" };
            if (body == "") return { status: "empty", body: "" };
            return { status: "ok", body: body };
        }
        return { status: "fail", body: "No HTTP headers (" + response.length + " bytes)" };
    }

    function pollServer() {
        if (stopped) return;
        var now = new Date().getTime();
        if (now - lastPoll < pollInterval) return;
        lastPoll = now;

        var attempts = [];
        if (connected) {
            attempts = [ports[currentPortIndex]];
        }
        for (var i = 0; i < ports.length; i++) {
            if (attempts.indexOf(ports[i]) < 0) attempts.push(ports[i]);
        }

        var found = -1;
        var result = null;
        for (var j = 0; j < attempts.length; j++) {
            var p = attempts[j];
            result = httpGet(p);
            if (result.status === "ok" || result.status === "idle") {
                found = p;
                break;
            }
        }

        if (found >= 0) {
            if (!connected) {
                connected = true;
                currentPortIndex = ports.indexOf(found);
                statusText.text = "AI Engine: LIVE on Port " + found;
                log(">>> Connected to Python server on port " + found);
            }
            if (result.status === "ok") {
                try {
                    app.beginUndoGroup("AI Agent Command");
                    var data = result.body;
                    if (data.length) eval(data);
                    app.endUndoGroup();
                    log(">>> EXECUTED: " + data.substring(0, 80) + (data.length > 80 ? "..." : ""));
                } catch (e) {
                    try { app.endUndoGroup(); } catch (e2) {}
                    log(">>> EXECUTION ERROR: " + e.toString());
                }
            }
        } else {
            if (connected) {
                connected = false;
                statusText.text = "AI Engine: Disconnected";
                log("Lost connection. Last error: " + (result ? result.body : lastError));
            }
        }
    }

    testBtn.onClick = function() {
        log("=== Manual Test ===");
        for (var i = 0; i < ports.length; i++) {
            var res = httpGet(ports[i]);
            if (res.status === "ok") {
                log("Port " + ports[i] + " OK: " + res.body.substring(0, 60));
            } else if (res.status === "idle") {
                log("Port " + ports[i] + ": connected, waiting for commands");
            } else {
                log("Port " + ports[i] + " FAILED: " + res.body);
            }
        }
    };

    clearBtn.onClick = function() {
        logArea.text = "";
    };

    stopBtn.onClick = function() {
        stopped = true;
        statusText.text = "AI Engine: Stopped";
        log("Polling stopped.");
    };

    win.onIdle = function() {
        pollServer();
    };

    win.layout.layout(true);
    log("=== AI Live Receiver Started ===");
    log("Target ports: " + ports.join(", "));
    log("Click 'Test Connection' to verify");
    log("Run: python video_editing_agent.py to host server");
    return win;
})(this);