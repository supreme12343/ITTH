from pathlib import Path

path = Path('blender_agent.py')
text = path.read_text(encoding='utf-8')
marker = '\n# MAIN APPLICATION\n'
if marker not in text:
    marker = '\n    # MAIN APPLICATION\n'
prefix = text.split(marker, 1)[0]

suffix = r'''

# MAIN APPLICATION
class BlenderAgentApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Blender 3D Agent — Campus Suite Pro")
        self.geometry("1220x820")
        self.minsize(1000, 680)
        self.configure(fg_color=BG)
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        self.client = BlenderSocket()
        self.connected = False
        self.busy = False
        self._executing = False
        self.warned = False
        self.diff_open = False
        self.uploaded_image = None
        self.chat_row = 0
        self._typing_wdg = None
        self.history = []
        self.km = RoleKeyManager() if RoleKeyManager else None
        self._build_ui()
        self.after(SCAN_MS, self._scan)
        log_event("app_started")

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        sidebar = ctk.CTkFrame(self, fg_color=SIDEBAR, width=270, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        ctk.CTkLabel(sidebar, text="Blender Agent", font=ctk.CTkFont(size=20, weight="bold"), text_color=ACCENT).pack(padx=20, pady=(24, 4), anchor="w")
        self.status_lbl = ctk.CTkLabel(sidebar, text="Scanning…", text_color=MUTED, anchor="w")
        self.status_lbl.pack(fill="x", padx=20, pady=4)
        self.engine_lbl = ctk.CTkLabel(sidebar, text="OmniRoute → Gemini", text_color=MUTED, anchor="w")
        self.engine_lbl.pack(fill="x", padx=20, pady=(0, 12))
        ctk.CTkLabel(sidebar, text="SCENE PRESETS", text_color=MUTED).pack(padx=20, pady=(8, 4), anchor="w")
        presets = ctk.CTkScrollableFrame(sidebar, fg_color="transparent", height=300)
        presets.pack(fill="x", padx=8)
        for name, prompt in PRESETS:
            ctk.CTkButton(presets, text=name, anchor="w", fg_color=CARD, hover_color=CARD_HOVER, command=lambda p=prompt: self._set_preset(p)).pack(fill="x", padx=4, pady=2)
        ctk.CTkButton(sidebar, text="Screenshot Viewport", command=self._manual_screenshot).pack(fill="x", padx=16, pady=(18, 4))
        ctk.CTkButton(sidebar, text="Clear Scene Log", command=self._clear_scene_log).pack(fill="x", padx=16, pady=4)
        ctk.CTkButton(sidebar, text="Reconnect Blender", command=self._reconnect).pack(fill="x", padx=16, pady=4)

        main = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(main, text="Natural Language → Blender 3D", font=ctk.CTkFont(size=24, weight="bold"), text_color=TEXT).grid(row=0, column=0, padx=28, pady=(22, 10), sticky="w")
        self.chat_f = ctk.CTkScrollableFrame(main, fg_color=PANEL, corner_radius=16)
        self.chat_f.grid(row=1, column=0, padx=28, pady=(0, 10), sticky="nsew")
        self.chat_f.grid_columnconfigure(0, weight=1)
        self.diff_box = ctk.CTkTextbox(main, height=150, fg_color="#080c14", text_color=TEXT)
        self.diff_box.grid(row=2, column=0, padx=28, pady=(0, 10), sticky="ew")
        self.diff_box.configure(state="disabled")
        input_bar = ctk.CTkFrame(main, fg_color=BG)
        input_bar.grid(row=3, column=0, padx=28, pady=(0, 22), sticky="ew")
        input_bar.grid_columnconfigure(0, weight=1)
        self.entry = ctk.CTkEntry(input_bar, placeholder_text="Describe what to build in Blender…", height=52)
        self.entry.grid(row=0, column=0, padx=(0, 8), sticky="ew")
        self.entry.bind("<Return>", self._send)
        ctk.CTkButton(input_bar, text="Image", width=90, command=self._pick_image).grid(row=0, column=1, padx=(0, 8))
        self.send_btn = ctk.CTkButton(input_bar, text="Generate", width=130, command=self._send)
        self.send_btn.grid(row=0, column=2)
        self._sys("Open Blender, enable the BlenderMCP add-on, start its server, then describe a scene.")

    def _scan(self):
        if self._executing:
            self.after(SCAN_MS, self._scan)
            return
        live = socket_alive()
        if live:
            self.connected = True
            self._set_status(f"Connected {HOST}:{PORT}", GREEN)
            if not self.warned:
                self.warned = True
                self._sys("BlenderMCP execute_code runs Python inside Blender. Use it only on your own machine.")
        elif blender_running():
            self.connected = False
            self._set_status("Blender open — start the MCP server", YELLOW)
        else:
            self.connected = False
            self._set_status("Blender not detected", RED)
        self.after(SCAN_MS, self._scan)

    def _send(self, _event=None):
        if self.busy:
            return
        prompt = self.entry.get().strip()
        image_path = self.uploaded_image
        if not prompt and image_path:
            prompt = "Recreate this reference image as a 3D scene in Blender."
        if not prompt:
            return
        if not self.connected:
            self._bubble("error", "Not connected to BlenderMCP. Start the add-on server first.")
            return
        self.entry.delete(0, "end")
        self._bubble("user", prompt)
        self.uploaded_image = None
        self.busy = True
        self.send_btn.configure(text="Working…", state="disabled")
        self._show_typing("Gathering scene information…")
        threading.Thread(target=self._run_agent, args=(prompt, image_path), daemon=True).start()

    def _run_agent(self, prompt, image_path=None):
        self._executing = True
        error_context = None
        try:
            for attempt in range(MAX_FIX_ATTEMPTS + 1):
                try:
                    self._update_typing(f"Analyzing scene (attempt {attempt + 1})…")
                    scene_info = self.client.get_scene_info()
                    self._update_typing("Generating Blender Python code…")
                    result = call_ai(prompt, image_path=image_path, error_context=error_context, scene_info=scene_info, current_script=read_scene_script())
                    code, explanation = extract_code_and_explanation(result.text)
                    if not code:
                        raise RuntimeError("AI returned no executable Python code block")
                    self._update_typing("Executing Python inside Blender…")
                    exec_result = self.client.execute(code)
                    if exec_result.success:
                        before, after = append_scene_script(code)
                        self._render_diff(make_diff(before, after))
                        self._update_typing("Capturing and auditing the viewport…")
                        screenshot = self.client.get_viewport_screenshot()
                        passed, feedback = audit_visual_quality(screenshot, prompt)
                        message = explanation or "The Blender scene was created and executed successfully."
                        message += f"\n\nAI route: {' → '.join(result.trail)}"
                        if not passed:
                            message += f"\n\nVisual audit: {feedback}"
                        self.after(0, lambda m=message, s=screenshot: self._safe_hide_typing_and_bubble("assistant", m, s))
                        log_event("execute_success", audit=passed)
                        return
                    error_context = exec_result.message
                    self.after(0, lambda e=error_context: self._bubble("error", f"Blender execution error: {e}"))
                except Exception as exc:
                    error_context = str(exc)
                    log_event("attempt_exception", attempt=attempt, error=error_context)
                if attempt >= MAX_FIX_ATTEMPTS:
                    self.after(0, lambda e=error_context: self._bubble("error", f"Agent stopped after {MAX_FIX_ATTEMPTS + 1} attempts.\n{e}"))
        finally:
            self._executing = False
            self._finish()

    def _pick_image(self):
        path = filedialog.askopenfilename(filetypes=[("Image files", "*.png *.jpg *.jpeg *.webp *.bmp")])
        if path:
            self.uploaded_image = Path(path)
            self._sys(f"Attached reference image: {self.uploaded_image.name}")

    def _manual_screenshot(self):
        if not self.connected:
            self._bubble("error", "Not connected to Blender.")
            return
        def capture():
            path = self.client.get_viewport_screenshot()
            if path:
                self.after(0, lambda: self._bubble("screenshot", "Viewport captured.", path))
            else:
                self.after(0, lambda: self._bubble("warn", "Screenshot failed."))
        threading.Thread(target=capture, daemon=True).start()

    def _clear_scene_log(self):
        SCENE_SCRIPT.write_text("", encoding="utf-8")
        self._render_diff("# scene log cleared")

    def _reconnect(self):
        self.client.close()
        self.connected = False
        self._set_status("Reconnecting…", YELLOW)

    def _set_preset(self, prompt):
        self.entry.delete(0, "end")
        self.entry.insert(0, prompt)
        self.entry.focus()

    def _set_status(self, text, color):
        self.status_lbl.configure(text=text, text_color=color)

    def _sys(self, text):
        self._bubble("system", text)

    def _bubble(self, role, text, screenshot=None):
        bubble = ChatBubble(self.chat_f, role, text, timestamp=datetime.now().strftime("%H:%M"), screenshot=screenshot)
        bubble.grid(row=self.chat_row, column=0, sticky="ew", pady=2, padx=6)
        self.chat_row += 1

    def _show_typing(self, text="Agent is thinking"):
        if self._typing_wdg:
            self._typing_wdg.set_text(text)
            return
        self._typing_wdg = TypingIndicator(self.chat_f, text)
        self._typing_wdg.grid(row=self.chat_row, column=0, sticky="w", padx=14, pady=2)
        self.chat_row += 1

    def _update_typing(self, text):
        if self._typing_wdg:
            self.after(0, lambda: self._typing_wdg.set_text(text))

    def _hide_typing(self):
        if self._typing_wdg:
            self._typing_wdg.destroy()
            self._typing_wdg = None

    def _render_diff(self, text):
        self.diff_box.configure(state="normal")
        self.diff_box.delete("1.0", "end")
        self.diff_box.insert("1.0", text)
        self.diff_box.configure(state="disabled")

    def _safe_hide_typing_and_bubble(self, role, text, screenshot=None):
        self._hide_typing()
        self._bubble(role, text, screenshot)

    def _finish(self):
        self.busy = False
        self.after(0, self._hide_typing)
        self.after(0, lambda: self.send_btn.configure(text="Generate", state="normal"))

if __name__ == "__main__":
    app = BlenderAgentApp()
    app.mainloop()
'''
path.write_text(prefix + suffix, encoding='utf-8')
print('repaired')
