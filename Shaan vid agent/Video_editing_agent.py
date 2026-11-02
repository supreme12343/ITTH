from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import queue
import tkinter as tk
from tkinter import scrolledtext

# Global queue for holding commands waiting for AE
command_queue = queue.Queue()


class AECommandHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Responds to After Effects polling requests."""
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()

        # If a command is waiting, send it to AE
        if not command_queue.empty():
            cmd = command_queue.get()
            self.wfile.write(cmd.encode("utf-8"))
        else:
            self.wfile.write(b"NO_COMMAND")

    def log_message(self, format, *args):
        # Silence default HTTP server terminal logging
        return


def start_server():
    server = HTTPServer(("127.0.0.1", 8080), AECommandHandler)
    server.serve_forever()


class AgentGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("AI Video Editing Agent (Live Mode)")
        self.root.geometry("650x450")
        self.root.configure(bg="#181825")

        self.setup_ui()

        # Start background server thread
        threading.Thread(target=start_server, daemon=True).start()

    def setup_ui(self):
        header = tk.Label(
            self.root,
            text="AI Video Editor Agent",
            font=("Helvetica", 14, "bold"),
            fg="#cdd6f4",
            bg="#181825"
        )
        header.pack(anchor="w", padx=20, pady=(20, 5))

        sub_header = tk.Label(
            self.root,
            text="Type a prompt (e.g. 'add text Motion Graphics', 'glow effect', 'color grade') & press Enter:",
            font=("Helvetica", 9),
            fg="#a6adc8",
            bg="#181825"
        )
        sub_header.pack(anchor="w", padx=20, pady=(0, 10))

        input_frame = tk.Frame(self.root, bg="#181825")
        input_frame.pack(fill=tk.X, padx=20, pady=5)

        self.entry = tk.Entry(
            input_frame,
            font=("Helvetica", 11),
            bg="#313244",
            fg="#cdd6f4",
            insertbackground="#cdd6f4",
            relief="flat"
        )
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8, padx=(0, 10))
        self.entry.bind("<Return>", lambda e: self.process_command())

        btn = tk.Button(
            input_frame,
            text="Execute Live",
            command=self.process_command,
            bg="#a6e3a1",
            fg="#11111b",
            font=("Helvetica", 10, "bold"),
            relief="flat",
            padx=15,
            pady=4
        )
        btn.pack(side=tk.RIGHT)

        self.console = scrolledtext.ScrolledText(
            self.root,
            bg="#11111b",
            fg="#a6e3a1",
            font=("Consolas", 10),
            relief="flat"
        )
        self.console.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        self.log("Server Live on Port 8080. Waiting for AE connection...")

    def log(self, text):
        self.console.insert(tk.END, f"> {text}\n")
        self.console.see(tk.END)

    def parse_prompt(self, prompt_text):
        prompt = prompt_text.lower().strip()

        if "text" in prompt or "title" in prompt:
            clean_text = prompt_text.replace("add text", "").replace("text", "").replace("title", "").strip()
            if not clean_text:
                clean_text = prompt_text
            return f'''
            var comp = app.project.activeItem;
            if (comp && comp instanceof CompItem) {{
                var txt = comp.layers.addText("{clean_text}");
                txt.property("Position").setValue([comp.width / 2, comp.height / 2]);
            }}
            '''
        elif "glow" in prompt or "shine" in prompt:
            return '''
            var comp = app.project.activeItem;
            if (comp && comp instanceof CompItem) {
                if (comp.selectedLayers.length > 0) {
                    for (var i = 0; i < comp.selectedLayers.length; i++) {
                        comp.selectedLayers[i].property("Effects").addProperty("ADBE Glow");
                    }
                } else {
                    var adj = comp.layers.addSolid([1,1,1], "AI_Glow_Pass", comp.width, comp.height, comp.pixelAspect, comp.duration);
                    adj.adjustmentLayer = true;
                    adj.property("Effects").addProperty("ADBE Glow");
                }
            }
            '''
        elif "color" in prompt or "grade" in prompt:
            return '''
            var comp = app.project.activeItem;
            if (comp && comp instanceof CompItem) {
                var colorAdj = comp.layers.addSolid([1,1,1], "AI_Color_Grade", comp.width, comp.height, comp.pixelAspect, comp.duration);
                colorAdj.adjustmentLayer = true;
                colorAdj.property("Effects").addProperty("ADBE Curves");
            }
            '''
        else:
            return f'''
            var comp = app.project.activeItem;
            if (comp && comp instanceof CompItem) {{
                comp.layers.addSolid([0.2, 0.4, 0.8], "{prompt_text}", comp.width, comp.height, comp.pixelAspect, comp.duration);
            }}
            '''

    def process_command(self):
        prompt = self.entry.get().strip()
        if not prompt:
            return

        self.entry.delete(0, tk.END)
        jsx_code = self.parse_prompt(prompt)

        # Add to server queue
        command_queue.put(jsx_code)
        self.log(f"Sent prompt: '{prompt}' -> Executing live in AE!")


if __name__ == "__main__":
    root = tk.Tk()
    app = AgentGUI(root)
    root.mainloop()