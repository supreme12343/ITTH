"""Shared Campus Suite visual tokens for native agent windows."""

BG = "#1e1e2f"
CARD = "#27273b"
CARD_LIGHT = "#34344d"
BORDER = "#4a4a68"
ACCENT = "#007aff"
ACCENT_HOVER = "#0060c4"
TEXT = "#f1f5f9"
TEXT_MUTED = "#b1b1c7"
TITLE = "#61dafb"
GREEN = "#34c759"
RED = "#ff3b30"
GOLD = "#ffd60a"
CORNER = 18
SECTION_CORNER = 14


# Shared Campus Suite theme preference. Electron writes this file whenever the
# user changes theme; native Tk windows poll it so all open tools stay in sync.
import json
from pathlib import Path
import tkinter as tk

_THEME_FILE = Path(__file__).resolve().with_name("theme-preference.json")
_THEME_PALETTES = {
    "light": {
        "BG": "#f4f5f1", "CARD": "#ffffff", "CARD_LIGHT": "#edf0e8",
        "BORDER": "#e7e9e3", "ACCENT": "#263c31", "ACCENT_HOVER": "#385344",
        "TEXT": "#182522", "TEXT_MUTED": "#83908a", "TITLE": "#648657",
        "GREEN": "#d9efc4", "RED": "#d85a4a",
    },
    "dark": {
        "BG": "#111915", "CARD": "#1c2821", "CARD_LIGHT": "#27372f",
        "BORDER": "#34443a", "ACCENT": "#d9efc4", "ACCENT_HOVER": "#c5e3ad",
        "TEXT": "#edf3eb", "TEXT_MUTED": "#a2b0a6", "TITLE": "#b8d5a8",
        "GREEN": "#d9efc4", "RED": "#df7566",
    },
}

def get_theme_mode():
    """Return the shared Campus Suite mode, defaulting safely to light."""
    try:
        mode = json.loads(_THEME_FILE.read_text(encoding="utf-8")).get("mode")
        return mode if mode in _THEME_PALETTES else "light"
    except (OSError, ValueError, AttributeError):
        return "light"

def get_theme_palette(mode=None):
    """Return the Campus brand palette for the selected or current mode."""
    return dict(_THEME_PALETTES.get(mode or get_theme_mode(), _THEME_PALETTES["light"]))


class RoundedTkButton(tk.Canvas):
    """A small antialiased Tk button that follows the shared Campus palette."""

    def __init__(self, parent, text, command, *, variant="primary", width=150, height=42, font=None):
        super().__init__(parent, width=width, height=height, bg=parent.cget("bg"),
                         highlightthickness=0, bd=0, takefocus=True, cursor="hand2")
        self.label = text
        self.command = command
        self.variant = variant
        self.font = font or ("Segoe UI", 10, "bold")
        self.mode = get_theme_mode()
        self.hovered = False
        self._image = None
        self._images = None
        self.apply_theme(self.mode)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Return>", self._on_click)
        self.bind("<space>", self._on_click)

    def _on_enter(self, _event=None):
        self.hovered = True
        self._draw()

    def _on_leave(self, _event=None):
        self.hovered = False
        self._draw()

    def _on_click(self, _event=None):
        if callable(self.command):
            self.command()
        return "break"

    def set_text(self, text):
        self.label = text
        self._draw()

    def set_variant(self, variant):
        self.variant = variant
        self.apply_theme(self.mode)

    def apply_theme(self, mode=None):
        self.mode = mode or get_theme_mode()
        self.palette = get_theme_palette(self.mode)
        styles = {
            "primary": (self.palette["ACCENT"], "#ffffff" if self.mode == "light" else "#233326", self.palette["ACCENT_HOVER"]),
            "secondary": (self.palette["CARD_LIGHT"], self.palette["TEXT"], self.palette["BORDER"]),
            "danger": (self.palette["RED"], "#ffffff", "#c66053"),
            "success": (self.palette["GREEN"], "#233326", "#c5e3ad"),
            "warm": ("#ee9b62", "#263c31", "#dc8754"),
        }
        self.base_color, self.text_color, self.hover_color = styles.get(self.variant, styles["primary"])
        try:
            self.configure(bg=self.master.cget("bg"))
        except tk.TclError:
            pass
        self._draw()

    def _draw(self):
        self.delete("all")
        width = max(1, int(float(self.cget("width"))))
        height = max(1, int(float(self.cget("height"))))
        color = self.hover_color if self.hovered else self.base_color
        try:
            from PIL import Image, ImageDraw, ImageTk
            scale = 4
            canvas = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
            ImageDraw.Draw(canvas).rounded_rectangle((0, 0, width * scale - 1, height * scale - 1),
                                                      radius=12 * scale, fill=color)
            resampling = getattr(Image, "Resampling", Image)
            self._image = ImageTk.PhotoImage(canvas.resize((width, height), resampling.LANCZOS))
            self.create_image(0, 0, image=self._image, anchor="nw")
        except (ImportError, AttributeError, tk.TclError):
            radius = 12
            self.create_rectangle(radius, 0, width - radius, height, fill=color, outline=color)
            self.create_rectangle(0, radius, width, height - radius, fill=color, outline=color)
            for x, y in ((0, 0), (width - 2 * radius, 0), (0, height - 2 * radius), (width - 2 * radius, height - 2 * radius)):
                self.create_oval(x, y, x + 2 * radius, y + 2 * radius, fill=color, outline=color)
        self.create_text(width / 2, height / 2, text=self.label, fill=self.text_color,
                         font=self.font, width=max(20, width - 24), justify="center")

def watch_theme(root, callback, interval_ms=450):
    """Call callback(mode) when the shared preference changes; return cancel."""
    last_mode = get_theme_mode()
    alive = True
    def poll():
        nonlocal last_mode
        if not alive:
            return
        mode = get_theme_mode()
        if mode != last_mode:
            last_mode = mode
            callback(mode)
        try:
            root.after(interval_ms, poll)
        except Exception:
            pass
    try:
        token = root.after(interval_ms, poll)
    except Exception:
        token = None
    def cancel():
        nonlocal alive
        alive = False
        if token is not None:
            try:
                root.after_cancel(token)
            except Exception:
                pass
    return cancel

def apply_tk_theme(root):
    """Refresh standard Tk widget colors while preserving widget geometry."""
    mode = get_theme_mode()
    palette = get_theme_palette(mode)
    old = _THEME_PALETTES["dark" if mode == "light" else "light"]
    replacements = {
        old["BG"]: palette["BG"], old["CARD"]: palette["CARD"],
        old["CARD_LIGHT"]: palette["CARD_LIGHT"], old["BORDER"]: palette["BORDER"],
        old["TEXT"]: palette["TEXT"], old["TEXT_MUTED"]: palette["TEXT_MUTED"],
        old["ACCENT"]: palette["ACCENT"], old["ACCENT_HOVER"]: palette["ACCENT_HOVER"],
        old["TITLE"]: palette["TITLE"], old["GREEN"]: palette["GREEN"], old["RED"]: palette["RED"],
        "#3a3a4c": palette["CARD_LIGHT"], "#44475a": palette["CARD_LIGHT"],
        "#27273b": palette["CARD"], "#34344d": palette["CARD_LIGHT"],
        "#4a4a68": palette["BORDER"], "#1e1e2f": palette["BG"],
        "#ffffff": palette["CARD"], "#fff": palette["CARD"],
        "#20302a": palette["TEXT"], "#182522": palette["TEXT"],
        "#718075": palette["TEXT_MUTED"], "#83908a": palette["TEXT_MUTED"],
        "#405047": palette["TEXT"], "#59695f": palette["TEXT_MUTED"],
        "#426d3d": palette["GREEN"], "#875b37": palette["TITLE"],
        "#a24538": palette["RED"],
    }
    dark_text = "#233326" if mode == "dark" else palette["TEXT"]
    try:
        import tkinter as tk
        widgets = [root]
        while widgets:
            widget = widgets.pop()
            try:
                widgets.extend(widget.winfo_children())
                # The hand-drawn Campus buttons have their own palette and do
                # not expose Tk's normal foreground/background options.
                if isinstance(widget, RoundedTkButton):
                    widget.apply_theme(mode)
                    continue
                kind = widget.winfo_class()
                options = widget.configure()
                updates = {}
                for option in ("bg", "background", "highlightbackground", "insertbackground", "selectbackground", "activebackground"):
                    if option in options:
                        current = str(widget.cget(option)).lower()
                        if current in replacements:
                            updates[option] = replacements[current]
                for option in ("fg", "foreground", "insertforeground", "selectforeground", "activeforeground"):
                    if option in options:
                        current = str(widget.cget(option)).lower()
                        if current in replacements:
                            updates[option] = replacements[current]
                if kind in ("Tk", "Toplevel", "Frame", "Labelframe"):
                    updates.setdefault("bg", palette["BG"])
                if kind in ("Button", "Menubutton") and mode == "dark":
                    button_bg = updates.get("bg", str(widget.cget("bg")).lower() if "bg" in options else "")
                    if button_bg in (palette["GREEN"], palette["ACCENT"], palette["ACCENT_HOVER"]):
                        if "fg" in options:
                            updates["fg"] = "#233326"
                        if "activeforeground" in options:
                            updates["activeforeground"] = "#233326"
                if updates:
                    widget.configure(**updates)
            except (tk.TclError, TypeError, KeyError):
                continue
    except Exception:
        return


def sync_native_theme(root):
    """Apply the shared palette to Tk windows and their rounded controls."""
    apply_tk_theme(root)
    mode = get_theme_mode()
    try:
        widgets = [root]
        while widgets:
            widget = widgets.pop()
            widgets.extend(widget.winfo_children())
            if isinstance(widget, RoundedTkButton):
                widget.apply_theme(mode)
    except Exception:
        return

def apply_ctk_theme(root, mode=None):
    """Refresh explicit CustomTkinter colors without changing widget layout."""
    palette = get_theme_palette(mode)
    replacements = {
        "#1e1e2f": palette["BG"], "#252538": palette["CARD"],
        "#151522": palette["CARD"], "#11111b": palette["CARD"],
        "#27273b": palette["CARD"], "#34344d": palette["CARD_LIGHT"],
        "#3c3c54": palette["BORDER"], "#2d2d44": palette["BORDER"],
        "#4a4a68": palette["BORDER"], "#324092": palette["ACCENT"],
        "#3f51b5": palette["ACCENT_HOVER"], "#007aff": palette["ACCENT"],
        "#0060c4": palette["ACCENT_HOVER"], "#61dafb": palette["TITLE"],
        "#00bcd4": palette["TITLE"], "#a78bfa": palette["TITLE"],
        "#a5b4fc": palette["TITLE"], "#f1f5f9": palette["TEXT"],
        "#b1b1c7": palette["TEXT_MUTED"],
        # Colors historically used by the native PowerPoint controller.
        "#ffc107": palette["TITLE"], "#a78bfa": palette["TITLE"],
        "#607d8b": palette["ACCENT"], "#455a64": palette["ACCENT_HOVER"],
        "#8888a0": palette["TEXT_MUTED"], "#a5b4fc": palette["TEXT"],
        "#50fa7b": palette["GREEN"], "#ff5555": palette["RED"],
        "#2e7d32": palette["GREEN"], "#388e3c": palette["GREEN"],
        "#b71c1c": palette["RED"], "#c62828": palette["RED"],
        "#1565c0": palette["ACCENT"], "#1976d2": palette["ACCENT_HOVER"],
        "#1b5e20": palette["GREEN"], "#8d1a1a": palette["RED"],
    }
    try:
        widgets = [root]
        while widgets:
            widget = widgets.pop()
            try:
                widgets.extend(widget.winfo_children())
                options = widget.configure()
                updates = {}
                for option in ("fg_color", "bg_color", "border_color", "text_color", "hover_color", "button_color", "button_hover_color", "progress_color"):
                    if option not in options:
                        continue
                    value = widget.cget(option)
                    if (option == "text_color" and mode == "light"
                            and isinstance(value, str) and value.lower() in {"white", "#fff", "#ffffff"}
                            and type(widget).__name__ in {"CTkLabel", "CTkTextbox", "CTkEntry"}):
                        updates[option] = palette["TEXT"]
                        continue
                    if isinstance(value, tuple):
                        changed = tuple(replacements.get(str(item).lower(), item) for item in value)
                        if changed != value:
                            updates[option] = changed
                    elif isinstance(value, str) and value.lower() in replacements:
                        updates[option] = replacements[value.lower()]
                if updates:
                    widget.configure(**updates)
            except Exception:
                continue
    except Exception:
        return


# Keep the legacy import names used by existing tools, but source their values
# from the currently selected shared palette.
_CURRENT_PALETTE = get_theme_palette()
BG = _CURRENT_PALETTE["BG"]
CARD = _CURRENT_PALETTE["CARD"]
CARD_LIGHT = _CURRENT_PALETTE["CARD_LIGHT"]
BORDER = _CURRENT_PALETTE["BORDER"]
ACCENT = _CURRENT_PALETTE["ACCENT"]
ACCENT_HOVER = _CURRENT_PALETTE["ACCENT_HOVER"]
TEXT = _CURRENT_PALETTE["TEXT"]
TEXT_MUTED = _CURRENT_PALETTE["TEXT_MUTED"]
TITLE = _CURRENT_PALETTE["TITLE"]
GREEN = _CURRENT_PALETTE["GREEN"]
RED = _CURRENT_PALETTE["RED"]
