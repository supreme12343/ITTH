import tkinter as tk
import time
import threading
from tkinter import messagebox
from campus_theme import BG, CARD_LIGHT, TEXT, GREEN, RED, TITLE, RoundedTkButton, apply_tk_theme, get_theme_mode, get_theme_palette, watch_theme

# Constants
FOCUS_TIME = 1 * 60
SHORT_BREAK = 1 * 60
LONG_BREAK = 1 * 60

bg_color = BG
button_color = CARD_LIGHT
text_color = TEXT
focus_color = TITLE
break_color = GREEN

class PomodoroApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Pomodoro Timer")
        self.root.geometry("400x350")
        self.root.configure(bg=bg_color)

        self.session_count = 0
        self.running = False
        self.timer_id = None
        self.remaining = FOCUS_TIME

        self.label = tk.Label(root, text="Focus Time", font=("Helvetica", 24, "bold"), fg=focus_color, bg=bg_color)
        self.label.pack(pady=20)

        self.time_display = tk.Label(root, text="25:00", font=("Helvetica", 48, "bold"), fg=focus_color, bg=bg_color)
        self.time_display.pack(pady=10)

        self.controls = tk.Frame(root, bg=bg_color)
        self.controls.pack(pady=20)

        self.start_btn = RoundedTkButton(self.controls, "Start", self.toggle_timer,
                                         variant="primary", width=112, height=44,
                                         font=("Helvetica", 12, "bold"))
        self.start_btn.grid(row=0, column=0, padx=10)

        self.reset_btn = RoundedTkButton(self.controls, "Reset", self.reset_timer,
                                         variant="secondary", width=112, height=44,
                                         font=("Helvetica", 12, "bold"))
        self.reset_btn.grid(row=0, column=1, padx=10)

        self.ask_ai_btn = RoundedTkButton(self.controls, "Ask Campus AI", self.ask_campus_ai,
                                          variant="secondary", width=130, height=44,
                                          font=("Helvetica", 12, "bold"))
        self.ask_ai_btn.grid(row=0, column=2, padx=10)

        self.session_label = tk.Label(root, text="Pomodoros: 0", font=("Helvetica", 12), fg=text_color, bg=bg_color)
        self.session_label.pack()

    def ask_campus_ai(self):
        """Optional, opt-in AI help with the session shown on the timer."""
        context = (f"Current phase: {self.label.cget('text')}\n"
                   f"Time left in this phase: {self.time_display.cget('text')}\n"
                   f"Timer running: {'yes' if self.running else 'no'}\n"
                   f"Completed pomodoros: {self.session_count // 2}\n"
                   f"Phases finished this run: {self.session_count}\n\n"
                   f"Configured lengths (seconds) — focus: {FOCUS_TIME}, "
                   f"short break: {SHORT_BREAK}, long break: {LONG_BREAK}")

        # campus_ai pulls in the shared key manager, which is slow to import, so load
        # it on a background thread and keep the timer ticking meanwhile.
        self.ask_ai_btn.set_text("Preparing AI…")

        def loader():
            try:
                from campus_ai import ask_about_result
                error = None
            except Exception as e:
                ask_about_result, error = None, str(e)

            def finish():
                if not self.root.winfo_exists():
                    return
                self.ask_ai_btn.set_text("Ask Campus AI")
                if error:
                    messagebox.showerror("Campus AI unavailable", f"Could not load Campus Suite AI:\n{error}")
                    return
                ask_about_result(
                    self.root,
                    "Pomodoro Timer",
                    context,
                    privacy_note="This sends only your timer settings and session counts.",
                )

            try:
                self.root.after(0, finish)
            except Exception:
                pass

        threading.Thread(target=loader, daemon=True).start()

    def toggle_timer(self):
        if self.running:
            self.stop_timer()
        else:
            self.start_timer()

    def start_timer(self):
        if not self.running:
            self.running = True
            self.start_btn.set_text("Stop")
            self.start_btn.set_variant("danger")
            self.countdown()

    def stop_timer(self):
        if self.timer_id:
            self.root.after_cancel(self.timer_id)
            self.timer_id = None
        self.running = False
        self.start_btn.set_text("Start")
        self.start_btn.set_variant("primary")

    def reset_timer(self):
        if self.timer_id:
            self.root.after_cancel(self.timer_id)
            self.timer_id = None
        self.running = False
        self.session_count = 0
        self.remaining = FOCUS_TIME
        self.label.config(text="Focus Time", fg=focus_color)
        self.time_display.config(text="25:00", fg=focus_color)
        self.session_label.config(text="Pomodoros: 0")
        self.start_btn.set_text("Start")
        self.start_btn.set_variant("primary")

    def countdown(self):
        mins, secs = divmod(self.remaining, 60)
        time_str = f"{mins:02d}:{secs:02d}"
        self.time_display.config(text=time_str)

        if self.remaining > 0:
            self.remaining -= 1
            self.timer_id = self.root.after(1000, self.countdown)
        else:
            self.session_count += 1
            self.session_label.config(text=f"Pomodoros: {self.session_count // 2}")
            self.set_time()

    def set_time(self):
        if self.session_count % 8 == 0 and self.session_count != 0:
            self.remaining = LONG_BREAK
            self.label.config(text="Long Break", fg=break_color)
            self.time_display.config(fg=break_color)
        elif self.session_count % 2 == 0:
            self.remaining = FOCUS_TIME
            self.label.config(text="Focus Time", fg=focus_color)
            self.time_display.config(fg=focus_color)
        else:
            self.remaining = SHORT_BREAK
            self.label.config(text="Short Break", fg=break_color)
            self.time_display.config(fg=break_color)

        if self.running:
            self.countdown()

if __name__ == "__main__":
    root = tk.Tk()
    app = PomodoroApp(root)
    def refresh_theme(mode):
        global bg_color, button_color, text_color, focus_color, break_color
        palette = get_theme_palette(mode)
        bg_color, button_color = palette["BG"], palette["CARD_LIGHT"]
        text_color, focus_color, break_color = palette["TEXT"], palette["TITLE"], palette["GREEN"]
        apply_tk_theme(root)
        root.configure(bg=bg_color)
        app.controls.configure(bg=bg_color)
        is_break = "Break" in app.label.cget("text")
        current_color = break_color if is_break else focus_color
        app.label.configure(bg=bg_color, fg=current_color)
        app.time_display.configure(bg=bg_color, fg=current_color)
        app.session_label.configure(bg=bg_color, fg=text_color)
        app.start_btn.apply_theme(mode)
        app.reset_btn.apply_theme(mode)
        app.ask_ai_btn.apply_theme(mode)
        if app.running:
            app.start_btn.set_variant("danger")

    refresh_theme(get_theme_mode())
    watch_theme(root, refresh_theme)
    root.mainloop()
