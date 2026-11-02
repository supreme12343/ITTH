// AI Live Receiver — file-based bridge for After Effects
// Reads commands written by the Python VideoEditingAgent from the bridge folder.
// Bridge folder: %TEMP%\ae_video_bridge  (or the path below if customized)
// HOW TO USE:
//   1. In the Python app: click "Start Server" (starts the bridge folder watcher).
//   2. In After Effects: Window > ... or File > Scripts > Run Script File > AI Live Receiver.jsx
//   3. Type prompts in the app; they appear here as commands and execute instantly.
(function(thisObj) {
    var BRIDGE = new Folder("C:/Users/user/AppData/Local/Temp/ae_video_bridge");
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

    var pollInterval = 1000; // ms
    var lastPoll = 0;
    var connected = false;
    var stopped = false;

    function log(msg) {
        var timestamp = new Date().toLocaleTimeString();
        logArea.text += "\n[" + timestamp + "] " + msg;
        logArea.selection = logArea.text.length;
    }

    function bridgeExists() {
        try {
            return BRIDGE.exists;
        } catch (e) {
            return false;
        }
    }

    function checkHeartbeat() {
        try {
            var hb = new File(BRIDGE.fsName + "/heartbeat.txt");
            if (hb.exists) {
                return true;
            }
        } catch (e) {}
        return false;
    }

    // Return list of .jsx command files in the bridge folder, sorted by name.
    function getCommands() {
        var out = [];
        if (!bridgeExists()) return out;
        var files = BRIDGE.getFiles("command_*.jsx");
        for (var i = 0; i < files.length; i++) {
            var f = files[i];
            if (f instanceof File && f.exists) out.push(f);
        }
        // Sort by name (command_0001.jsx ... )
        out.sort(function(a, b) {
            var na = parseInt(a.name.replace(/\\D/g, ""), 10) || 0;
            var nb = parseInt(b.name.replace(/\\D/g, ""), 10) || 0;
            return na - nb;
        });
        return out;
    }

    function pollServer() {
        if (stopped) return;
        var now = new Date().getTime();
        if (now - lastPoll < pollInterval) return;
        lastPoll = now;

        if (!bridgeExists()) {
            if (connected) {
                connected = false;
                statusText.text = "AI Engine: No bridge folder";
                log("Bridge folder missing: " + BRIDGE.fsName);
                log("Start the server in the Python app first.");
            }
            return;
        }

        if (!checkHeartbeat()) {
            if (connected) {
                connected = false;
                statusText.text = "AI Engine: Waiting for server...";
                log("Bridge folder exists but no heartbeat — server not running.");
            }
            return;
        }

        if (!connected) {
            connected = true;
            statusText.text = "AI Engine: LIVE (bridge connected)";
            log(">>> Connected to Python bridge at " + BRIDGE.fsName);
        }

        var cmds = getCommands();
        for (var i = 0; i < cmds.length; i++) {
            var f = cmds[i];
            try {
                var data = "";
                f.open("r");
                data = f.read();
                f.close();
                if (!data || data.length === 0) {
                    f.remove();
                    continue;
                }
                log(">>> EXECUTING " + f.name + " (" + data.length + " chars)");
                app.beginUndoGroup("AI Agent Command");
                eval(data);
                app.endUndoGroup();
                log(">>> EXECUTED: " + data.substring(0, 80) + (data.length > 80 ? "..." : ""));
                f.remove();
            } catch (e) {
                try { app.endUndoGroup(); } catch (e2) {}
                log(">>> EXECUTION ERROR on " + f.name + ": " + e.toString());
                // Don't remove the file so it isn't lost; rename to .err for inspection.
                try { f.rename(f.fullName.replace(".jsx", ".err")); } catch (e3) {}
            }
        }
    }

    testBtn.onClick = function() {
        log("=== Manual Test ===");
        log("Bridge folder: " + BRIDGE.fsName);
        if (!bridgeExists()) {
            log("FAILED: bridge folder does not exist.");
            log("Start the server in the Python app first.");
            return;
        }
        if (!checkHeartbeat()) {
            log("FAILED: no heartbeat file — server not running.");
            log("Start the server in the Python app first.");
            return;
        }
        var files = getCommands();
        log("OK: bridge folder exists and server is running.");
        log("Pending commands: " + files.length);
        statusText.text = "AI Engine: LIVE (bridge connected)";
        connected = true;
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
    log("Bridge folder: " + BRIDGE.fsName);
    log("Click 'Test Connection' to verify");
    log("Run the Python VideoEditingAgent and click Start Server to host the bridge.");
    return win;
})(this);
