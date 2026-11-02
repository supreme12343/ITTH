// Live Background Receiver Panel for After Effects
(function(thisObj) {
    var win = (thisObj instanceof Panel) ? thisObj : new Window("palette", "AI Live Receiver", undefined, {resizeable: true});
    win.orientation = "column";
    
    var statusText = win.add("statictext", undefined, "AI Engine: Offline");
    statusText.alignment = ["fill", "top"];

    var server = new Socket();
    var listening = false;

    // Background listener loop using UI Idle task
    win.onIdle = function() {
        if (!listening) {
            if (server.listen(8080)) {
                statusText.text = "AI Engine: LIVE on Port 8080";
                listening = true;
            }
        } else {
            var client = server.poll();
            if (client !== null) {
                var request = client.read();
                if (request) {
                    try {
                        app.beginUndoGroup("AI Agent Command");
                        // Execute incoming raw ExtendScript payload directly in AE memory
                        eval(request);
                        app.endUndoGroup();
                        client.write("HTTP/1.1 200 OK\r\n\r\nSUCCESS");
                    } catch(err) {
                        client.write("HTTP/1.1 500 Error\r\n\r\n" + err.toString());
                    }
                    client.close();
                }
            }
        }
    };

    win.layout.layout(true);
    return win;
})(this);