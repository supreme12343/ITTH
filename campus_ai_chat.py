"""A simple CustomTkinter chat window for demonstrating Campus Suite AI.

Run with ``python campus_ai_chat.py``. The local mode calls the bundled
Campus Suite model directly; automatic mode uses the shared key-manager route.
"""
from __future__ import annotations

import queue
import threading

import customtkinter as ctk


SYSTEM_PROMPT = (
    "You are Campus Suite AI, developed by Venkat and Shaan for the Campus Suite. "
    "Help students and teachers learn with clear, concise, classroom-appropriate "
    "answers. Explain ideas simply and say when you are unsure."
)
LOCAL_MODE = "Campus AI · Local"
AUTO_MODE = "Automatic · Connected providers"


class CampusAIChat(ctk.CTk):
    def __init__(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("green")
        super().__init__()

        self.title("Campus AI")
        self.geometry("1080x780")
        self.minsize(780, 600)
        self.configure(fg_color="#111813")

        self._closing = False
        self._busy = False
        self._result_queue: queue.Queue = queue.Queue()
        self._warmup_queue: queue.Queue = queue.Queue()
        self._history: list[dict[str, str]] = []
        self._local_assets = self._local_assets_available()
        self._local_ready = self._is_local_ready()
        self._local_loaded = False
        self._local_loading = False
        self._modes = [AUTO_MODE, LOCAL_MODE] if self._local_assets else [AUTO_MODE]
        self._mode = LOCAL_MODE if self._local_assets else AUTO_MODE
        self._api = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.protocol("WM_DELETE_WINDOW", self._close)

        self._build_header()
        self._build_chat_area()
        self._build_composer()
        self._show_empty_state()
        self.after(120, self._poll_results)
        if self._local_ready:
            self._local_loading = True
            self.mode_badge.configure(
                text="LOCAL MODEL LOADING",
                text_color="#c7e9ae",
                fg_color="#263a2b",
            )
            self.status_label.configure(
                text="Loading Campus AI into memory in the background; the first startup can take a few minutes."
            )
            threading.Thread(target=self._warm_local_model, daemon=True).start()

    @staticmethod
    def _local_assets_available() -> bool:
        try:
            import campus_local_provider

            return campus_local_provider.assets_available()
        except Exception:
            return False

    @staticmethod
    def _is_local_ready() -> bool:
        try:
            import campus_local_provider

            return campus_local_provider.is_configured()
        except Exception:
            return False

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="#18231c", corner_radius=0, height=82)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        header.grid_propagate(False)

        mark = ctk.CTkLabel(
            header,
            text="✳",
            width=44,
            height=44,
            corner_radius=14,
            fg_color="#d5edc4",
            text_color="#203326",
            font=ctk.CTkFont(size=25, weight="bold"),
        )
        mark.grid(row=0, column=0, padx=(24, 13), pady=18)

        title_group = ctk.CTkFrame(header, fg_color="transparent")
        title_group.grid(row=0, column=1, sticky="w", pady=13)
        ctk.CTkLabel(
            title_group,
            text="Campus AI",
            text_color="#eff5ed",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_group,
            text="A classroom-ready assistant",
            text_color="#9bab9e",
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", pady=(1, 0))

        self.mode_menu = ctk.CTkOptionMenu(
            header,
            values=self._modes,
            command=self._select_mode,
            width=238,
            height=38,
            fg_color="#28392d",
            button_color="#3b5941",
            button_hover_color="#4b704f",
            text_color="#e9f1e7",
            dropdown_fg_color="#1e2b22",
        )
        self.mode_menu.set(self._mode)
        self.mode_menu.grid(row=0, column=2, padx=(10, 12), pady=21)

        self.mode_badge = ctk.CTkLabel(
            header,
            text=("LOCAL MODEL READY" if self._local_ready else "LOCAL SETUP ISSUE")
            if self._local_assets else "AUTO ROUTING",
            text_color="#c7e9ae" if self._local_ready else "#e2bb92",
            fg_color="#263a2b" if self._local_ready else "#3b3024",
            corner_radius=10,
            font=ctk.CTkFont(size=10, weight="bold"),
            padx=11,
            pady=8,
        )
        self.mode_badge.grid(row=0, column=3, padx=(0, 22), pady=21)

    def _build_chat_area(self) -> None:
        area = ctk.CTkFrame(self, fg_color="#111813", corner_radius=0)
        area.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        area.grid_rowconfigure(0, weight=1)
        area.grid_columnconfigure(0, weight=1)

        self.messages_frame = ctk.CTkScrollableFrame(
            area,
            fg_color="#111813",
            corner_radius=0,
            scrollbar_button_color="#38483b",
            scrollbar_button_hover_color="#506652",
        )
        self.messages_frame.grid(row=0, column=0, sticky="nsew", padx=20, pady=(16, 8))
        self.messages_frame.grid_columnconfigure(0, weight=1)

    def _build_composer(self) -> None:
        footer = ctk.CTkFrame(self, fg_color="#111813", corner_radius=0)
        footer.grid(row=2, column=0, sticky="ew", padx=34, pady=(3, 19))
        footer.grid_columnconfigure(0, weight=1)

        composer = ctk.CTkFrame(
            footer,
            fg_color="#1c2820",
            border_color="#344638",
            border_width=1,
            corner_radius=18,
        )
        composer.grid(row=0, column=0, sticky="ew")
        composer.grid_columnconfigure(0, weight=1)

        self.input_box = ctk.CTkTextbox(
            composer,
            height=72,
            wrap="word",
            fg_color="transparent",
            text_color="#edf3eb",
            font=ctk.CTkFont(size=14),
            border_width=0,
        )
        self.input_box.grid(row=0, column=0, sticky="nsew", padx=(15, 6), pady=9)
        self.input_box.bind("<Return>", self._on_enter)
        self.input_box.bind("<Control-Return>", self._on_enter)

        self.send_button = ctk.CTkButton(
            composer,
            text="Send  ↑",
            width=104,
            height=40,
            corner_radius=12,
            fg_color="#d5edc4",
            hover_color="#c1e2a8",
            text_color="#203326",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._send_message,
        )
        self.send_button.grid(row=0, column=1, padx=(4, 11), pady=12, sticky="s")

        info = ctk.CTkFrame(footer, fg_color="transparent")
        info.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        info.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(
            info,
            text=self._mode_note(),
            text_color="#829287",
            font=ctk.CTkFont(size=11),
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="w")
        self.clear_button = ctk.CTkButton(
            info,
            text="New chat",
            width=82,
            height=26,
            fg_color="transparent",
            hover_color="#253329",
            text_color="#a9b9ac",
            font=ctk.CTkFont(size=11),
            command=self._clear_chat,
        )
        self.clear_button.grid(row=0, column=1, sticky="e")

    def _mode_note(self) -> str:
        if self._mode == LOCAL_MODE:
            if self._local_loading:
                return "Campus AI is loading in the background · your first startup can take a few minutes"
            if self._local_ready:
                return "Runs on this device · Enter to send · Shift+Enter for a new line"
            return "Local model files found, but llama.cpp needs setup · Shift+Enter for a new line"
        return "Automatic mode may use connected AI providers · Enter to send · Shift+Enter for a new line"

    def _select_mode(self, value: str) -> None:
        if self._busy:
            self.mode_menu.set(self._mode)
            return
        self._mode = value
        if hasattr(self, "status_label"):
            self.status_label.configure(text=self._mode_note())
        if hasattr(self, "mode_badge"):
            self.mode_badge.configure(
                text=("LOCAL MODEL READY" if self._local_ready else "LOCAL SETUP ISSUE")
                if value == LOCAL_MODE else "AUTOMATIC ROUTING",
                text_color="#c7e9ae" if value == LOCAL_MODE and self._local_ready else "#e2bb92"
                if value == LOCAL_MODE else "#b6c7b7",
                fg_color="#263a2b" if value == LOCAL_MODE and self._local_ready else "#3b3024"
                if value == LOCAL_MODE else "#29332c",
            )

    def _show_empty_state(self) -> None:
        self.empty_state = ctk.CTkFrame(self.messages_frame, fg_color="transparent")
        self.empty_state.grid(row=0, column=0, sticky="nsew", pady=(145, 60))
        ctk.CTkLabel(
            self.empty_state,
            text="What would you like to learn today?",
            text_color="#edf3eb",
            font=ctk.CTkFont(size=27, weight="bold"),
        ).pack(pady=(0, 11))
        ctk.CTkLabel(
            self.empty_state,
            text="Ask a question, explore an idea, or get help understanding a topic.",
            text_color="#9bab9e",
            font=ctk.CTkFont(size=14),
        ).pack()
        if self._local_ready:
            ctk.CTkLabel(
                self.empty_state,
                text="Try: Explain photosynthesis in three simple steps.",
                text_color="#bad2b7",
                font=ctk.CTkFont(size=13),
            ).pack(pady=(20, 0))

    def _on_enter(self, event):
        if event.state & 0x0001 and event.keysym == "Return":
            return None
        self._send_message()
        return "break"

    def _send_message(self) -> None:
        if self._busy:
            return
        prompt = self.input_box.get("1.0", "end-1c").strip()
        if not prompt:
            return
        self.input_box.delete("1.0", "end")
        if self.empty_state.winfo_exists():
            self.empty_state.destroy()

        self._add_message("user", prompt)
        request_history = [*self._history, {"role": "user", "content": prompt}][-24:]
        self._set_busy(True)
        self.status_label.configure(
            text=(
                "Loading Campus AI for its first reply; this can take a few minutes…"
                if self._mode == LOCAL_MODE and not self._local_loaded
                else "Campus AI is thinking…"
                if self._mode == LOCAL_MODE
                else "Finding an available AI provider…"
            )
        )
        threading.Thread(
            target=self._generate_reply,
            args=(prompt, request_history, self._mode),
            daemon=True,
        ).start()

    def _generate_reply(
        self,
        prompt: str,
        conversation: list[dict[str, str]],
        mode: str,
    ) -> None:
        try:
            from campus_ai import redact_sensitive_text

            safe_conversation = [
                {"role": message["role"], "content": redact_sensitive_text(message["content"])}
                for message in conversation
            ]
            safe_system_prompt = redact_sensitive_text(SYSTEM_PROMPT)
            if mode == LOCAL_MODE:
                import campus_local_provider

                response = campus_local_provider.chat_completion(
                    safe_conversation,
                    max_tokens=128,
                    temperature=0.3,
                    top_p=0.9,
                    identity_prompt=safe_system_prompt,
                )
                answer = response["choices"][0]["message"]["content"]
                provider = "Campus Suite local model"
            else:
                if self._api is None:
                    from key_manager import AiApi

                    self._api = AiApi()
                response = self._api.chat_with_role(
                    "analyst",
                    safe_system_prompt,
                    safe_conversation[-1]["content"],
                    temperature=0.35,
                    max_retries=1,
                    max_tokens=192,
                    conversation=safe_conversation,
                )
                answer = response.choices[0].message.content
                provider = response.model or "connected provider"

            if not isinstance(answer, str) or not answer.strip():
                raise RuntimeError("The provider returned an empty response.")
            self._post_result(prompt, answer.strip(), provider, None)
        except Exception as error:
            if mode == LOCAL_MODE:
                from campus_ai import redact_sensitive_text

                lines = redact_sensitive_text(str(error)).splitlines()
                detail = lines[0][:180] if lines else type(error).__name__
                print(f"[Campus AI Chat] Local request failed ({type(error).__name__}): {detail}")
                message = f"Campus AI local error ({type(error).__name__}): {detail}"
            else:
                # Connected provider errors can contain request details or credentials.
                print(f"[Campus AI Chat] Request failed ({type(error).__name__})")
                message = "I couldn't get a response. Check the provider setup, then try again."
            self._post_result(prompt, None, None, message)

    def _post_result(
        self,
        prompt: str,
        answer: str | None,
        provider: str | None,
        error: str | None,
    ) -> None:
        if not self._closing:
            self._result_queue.put((prompt, answer, provider, error))

    def _poll_results(self) -> None:
        if self._closing:
            return
        try:
            while True:
                result = self._result_queue.get_nowait()
                self._finish_request(*result)
        except queue.Empty:
            pass
        try:
            while True:
                ready, detail = self._warmup_queue.get_nowait()
                self._finish_warmup(ready, detail)
        except queue.Empty:
            pass
        self.after(120, self._poll_results)

    def _warm_local_model(self) -> None:
        try:
            import campus_local_provider

            campus_local_provider.warm_up()
            self._warmup_queue.put((True, None))
        except Exception as error:
            from campus_ai import redact_sensitive_text

            lines = redact_sensitive_text(str(error)).splitlines()
            detail = lines[0][:180] if lines else type(error).__name__
            print(f"[Campus AI Chat] Local model warm-up failed ({type(error).__name__}): {detail}")
            self._warmup_queue.put((False, detail))

    def _finish_warmup(self, ready: bool, detail: str | None) -> None:
        self._local_loading = False
        self._local_ready = ready
        if ready:
            self._local_loaded = True
            self.mode_badge.configure(
                text="LOCAL MODEL READY",
                text_color="#c7e9ae",
                fg_color="#263a2b",
            )
            if not self._busy and self._mode == LOCAL_MODE:
                self.status_label.configure(text=self._mode_note(), text_color="#829287")
            return

        self.mode_badge.configure(
            text="LOCAL SETUP ISSUE",
            text_color="#e2bb92",
            fg_color="#3b3024",
        )
        if not self._busy and self._mode == LOCAL_MODE:
            self.status_label.configure(
                text=f"Local model could not load: {detail or 'check the model runtime setup'}",
                text_color="#e2a294",
            )

    def _finish_request(
        self,
        prompt: str,
        answer: str | None,
        provider: str | None,
        error: str | None,
    ) -> None:
        if self._closing:
            return
        self._set_busy(False)
        if error:
            self.status_label.configure(text=error, text_color="#e2a294")
            return

        if provider in {"Campus Suite local model", "campus-suite-ai-local"}:
            self._local_loaded = True

        self._history.extend(
            ({"role": "user", "content": prompt}, {"role": "assistant", "content": answer})
        )
        self._add_message("assistant", answer, provider=provider)
        self.status_label.configure(
            text=f"Answered by {provider} · Enter to send · Shift+Enter for a new line",
            text_color="#9bab9e",
        )

    def _add_message(self, role: str, text: str, *, provider: str | None = None) -> None:
        row = ctk.CTkFrame(self.messages_frame, fg_color="transparent")
        row.grid(row=len(self.messages_frame.winfo_children()), column=0, sticky="ew", pady=7)
        row.grid_columnconfigure(0, weight=1)

        is_user = role == "user"
        bubble = ctk.CTkFrame(
            row,
            fg_color="#283b2d" if is_user else "#1c2820",
            border_color="#3a533f" if is_user else "#303e33",
            border_width=1,
            corner_radius=17,
        )
        bubble.grid(row=0, column=0, sticky="e" if is_user else "w", padx=(120, 0) if is_user else (0, 120))
        name = "YOU" if is_user else f"CAMPUS AI  ·  {provider or 'ASSISTANT'}"
        ctk.CTkLabel(
            bubble,
            text=name,
            text_color="#c2d6bb" if is_user else "#d5edc4",
            font=ctk.CTkFont(size=10, weight="bold"),
            anchor="w",
        ).pack(anchor="w", padx=15, pady=(11, 3))
        ctk.CTkLabel(
            bubble,
            text=text,
            text_color="#edf3eb",
            font=ctk.CTkFont(size=14),
            justify="left",
            anchor="w",
            wraplength=690,
        ).pack(anchor="w", padx=15, pady=(0, 12))
        self.after(40, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        if self._closing:
            return
        try:
            self.messages_frame._parent_canvas.yview_moveto(1.0)
        except (AttributeError, RuntimeError):
            pass

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.send_button.configure(state="disabled" if busy else "normal")
        self.mode_menu.configure(state="disabled" if busy else "normal")
        self.clear_button.configure(state="disabled" if busy else "normal")
        self.input_box.configure(state="disabled" if busy else "normal")

    def _clear_chat(self) -> None:
        if self._busy:
            return
        self._history.clear()
        for child in self.messages_frame.winfo_children():
            child.destroy()
        self._show_empty_state()
        self.status_label.configure(text=self._mode_note(), text_color="#829287")

    def _close(self) -> None:
        self._closing = True
        self.destroy()


if __name__ == "__main__":
    CampusAIChat().mainloop()
