import tkinter as tk
from tkinter import messagebox
import threading
from campus_theme import ACCENT, ACCENT_HOVER, BG, CARD, CARD_LIGHT, GREEN, RED, TEXT, apply_tk_theme, get_theme_mode, get_theme_palette, watch_theme
try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    Image = ImageDraw = ImageTk = None

# Colors
BG_COLOR = BG
ENTRY_BG = CARD_LIGHT
ENTRY_FG = ACCENT
TEXT_COLOR = TEXT

NUMBER_BTN_BG = CARD_LIGHT
OPERATOR_BTN_BG = "#ff7f00"
EQUAL_BTN_BG = GREEN
CLEAR_BTN_BG = RED

ACTIVE_BG_LIGHT = "#54576a"
ACTIVE_BG_DARK = "#e07200"
ACTIVE_BG_GREEN = "#45a049"
ACTIVE_BG_RED = "#cc3e3e"

# ---------- Calculator logic ----------
def press(key):
    txt = entry.get()
    if key in ['/', '*', '-', '+', '**']:
        if not txt and key == '-':
            _write(key); return
        if txt:
            last = txt[-1]
            last2 = txt[-2:] if len(txt) >= 2 else ''
            if last in '/-*+' or last2 == '**':
                entry.config(state='normal')
                if last2 == '**':
                    entry.delete(len(txt)-2, tk.END)
                else:
                    entry.delete(len(txt)-1, tk.END)
                entry.insert(tk.END, key)
                entry.config(state='readonly')
                return
        else:
            return
    _write(key)

def _write(text):
    entry.config(state='normal')
    entry.insert(tk.END, text)
    entry.config(state='readonly')

def clear():
    entry.config(state='normal')
    entry.delete(0, tk.END)
    entry.config(state='readonly')

def clear_entry():
    s = entry.get()
    if not s: return
    entry.config(state='normal')
    if s.endswith('**'):
        entry.delete(len(s)-2, tk.END)
    elif s[-1] in '/-*+':
        entry.delete(len(s)-1, tk.END)
    else:
        i = len(s)-1
        while i >= 0 and s[i] not in '/-*+':
            if i >= 1 and s[i-1:i+1] == '**':
                break
            i -= 1
        entry.delete(i+1, tk.END)
    entry.config(state='readonly')

def backspace():
    s = entry.get()
    if not s: return
    entry.config(state='normal')
    if s.endswith('**'):
        entry.delete(len(s)-2, tk.END)
    else:
        entry.delete(len(s)-1, tk.END)
    entry.config(state='readonly')

def calculate():
    global last_calculation
    try:
        expr = entry.get().replace('×', '*')
        if not expr or expr[-1] in '/-*+' or expr.endswith('**'):
            raise ValueError("Incomplete expression")
        result = eval(expr)
        shown = str(int(result)) if isinstance(result, float) and result.is_integer() else str(result)
        last_calculation = f"{expr} = {shown}"
        entry.config(state='normal')
        entry.delete(0, tk.END)
        entry.insert(tk.END, shown)
        entry.config(state='readonly')
    except Exception as e:
        messagebox.showerror("Error", f"Invalid Expression: {e}")
        clear()

# ---------- Optional, opt-in Campus AI help ----------
last_calculation = ""

def ask_campus_ai():
    """Ask the shared Campus Suite AI about the expression or result on the display."""
    current = entry.get().strip()
    if not current and not last_calculation:
        messagebox.showinfo("Nothing to ask about", "Type an expression or calculate a result first.")
        return

    context = f"Current display: {current or '(empty)'}"
    if last_calculation:
        context += f"\nLast calculation: {last_calculation}"

    # campus_ai pulls in the shared key manager, which is slow to import, so load
    # it on a background thread and keep the keypad responsive meanwhile.
    ask_ai_button.set_text("Preparing AI…")

    def loader():
        try:
            from campus_ai import ask_about_result
            error = None
        except Exception as e:
            ask_about_result, error = None, str(e)

        def finish():
            if not root.winfo_exists():
                return
            ask_ai_button.set_text("Ask Campus AI")
            if error:
                messagebox.showerror("Campus AI unavailable", f"Could not load Campus Suite AI:\n{error}")
                return
            ask_about_result(
                root,
                "Python Calculator",
                context,
                privacy_note="This sends only the expression and result shown on the display.",
            )

        try:
            root.after(0, finish)
        except Exception:
            pass

    threading.Thread(target=loader, daemon=True).start()

# ---------- Rounded Button ----------
class RoundedButton(tk.Canvas):
    def __init__(self, parent, text, width=80, height=52, radius=12, bg="#44475a", fg=TEXT_COLOR, command=None, activebg=None, font=('Arial',12,'bold')):
        super().__init__(parent, width=width, height=height, bg=parent['bg'], highlightthickness=0)
        self.width, self.height, self.radius = width, height, radius
        self.bg, self.fg, self.text, self.command = bg, fg, text, command
        self.activebg = activebg or bg
        self.font = font
        self.theme_kind = "equals" if text == "=" else "clear" if text in {"C", "CE", "DEL"} else "operator" if text in {"/", "*", "-", "+", "x^y"} else "number"
        self._shape_image = None
        self._draw(bg)
        self.bind("<Button-1>", lambda e: self._on_click())
        self.bind("<Enter>", lambda e: (self._draw(self.activebg), self.config(cursor="hand2")))
        self.bind("<Leave>", lambda e: (self._draw(self.bg), self.config(cursor="")))

    def _draw(self, color):
        self.delete("all")
        r = self.radius
        w, h = self.width, self.height
        if Image is not None:
            scale = 4
            shape = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
            draw = ImageDraw.Draw(shape)
            draw.rounded_rectangle((0, 0, w * scale - 1, h * scale - 1), radius=r * scale, fill=color)
            resampling = getattr(Image, "Resampling", Image)
            self._shape_image = ImageTk.PhotoImage(shape.resize((w, h), resampling.LANCZOS))
            self.create_image(0, 0, image=self._shape_image, anchor="nw")
        else:
            self.create_rectangle(r, 0, w-r, h, fill=color, outline=color)
            self.create_rectangle(0, r, w, h-r, fill=color, outline=color)
            self.create_oval(0, 0, 2*r, 2*r, fill=color, outline=color)
            self.create_oval(w-2*r, 0, w, 2*r, fill=color, outline=color)
            self.create_oval(0, h-2*r, 2*r, h, fill=color, outline=color)
            self.create_oval(w-2*r, h-2*r, w, h, fill=color, outline=color)
        self.create_text(w/2, h/2, text=self.text, fill=self.fg, font=self.font)

    def set_theme_colors(self, bg, fg, active, canvas_bg):
        self.bg, self.fg, self.activebg = bg, fg, active
        self.configure(bg=canvas_bg)
        self._draw(bg)

    def set_text(self, text):
        self.text = text
        self._draw(self.bg)

    def _on_click(self):
        self.move("all", 0, 1)
        self.after(80, lambda: (self.move("all", 0, -1), self._call()))

    def _call(self):
        if callable(self.command):
            self.command()

# ---------- Build UI ----------
root = tk.Tk()
root.title("Python Calculator")
root.geometry("373x580")
root.resizable(False, False)
root.configure(bg=BG_COLOR)
calculator_buttons = []

entry = tk.Entry(root, font=('Arial',20), borderwidth=0, relief=tk.FLAT,
                 justify='right', bg=ENTRY_BG, fg=ENTRY_FG, insertbackground=ENTRY_FG,
                 readonlybackground=ENTRY_BG, state='readonly')
entry.grid(row=0, column=0, columnspan=4, padx=10, pady=15, ipady=12, sticky="nsew")

# ---------- Keyboard Binding ----------
def handle_key(event):
    key = event.char
    if key.isdigit() or key in ['+', '-', '/', '.', '(', ')']:
        press(key)
    elif key in ['*', 'x', 'X']:
        press('*')
        # show visually ×
        entry.config(state='normal')
        entry.delete(len(entry.get())-1, tk.END)
        entry.insert(tk.END, '×')
        entry.config(state='readonly')
    elif key == '\r':  # Enter
        calculate()
    elif event.keysym == 'BackSpace':
        backspace()
    elif event.keysym in ['Delete', 'Escape']:
        clear()

root.bind('<Key>', handle_key)

# ---------- Layout Helper ----------
def place_button(text, bg, cmd, active, row, col, colspan=1, width=80, height=55):
    btn = RoundedButton(root, text=text, width=width, height=height, radius=16, bg=bg, fg=TEXT_COLOR, command=cmd, activebg=active)
    btn.grid(row=row, column=col, columnspan=colspan, padx=6, pady=6, sticky="nsew")
    calculator_buttons.append(btn)
    return btn

# Button configuration
buttons = [
    ('C', CLEAR_BTN_BG, clear, ACTIVE_BG_RED),
    ('CE', CLEAR_BTN_BG, clear_entry, ACTIVE_BG_RED),
    ('DEL', CLEAR_BTN_BG, backspace, ACTIVE_BG_RED),
    ('/', OPERATOR_BTN_BG, lambda: press('/'), ACTIVE_BG_DARK),
    ('7', NUMBER_BTN_BG, lambda: press('7'), ACTIVE_BG_LIGHT),
    ('8', NUMBER_BTN_BG, lambda: press('8'), ACTIVE_BG_LIGHT),
    ('9', NUMBER_BTN_BG, lambda: press('9'), ACTIVE_BG_LIGHT),
    ('*', OPERATOR_BTN_BG, lambda: press('×'), ACTIVE_BG_DARK),
    ('4', NUMBER_BTN_BG, lambda: press('4'), ACTIVE_BG_LIGHT),
    ('5', NUMBER_BTN_BG, lambda: press('5'), ACTIVE_BG_LIGHT),
    ('6', NUMBER_BTN_BG, lambda: press('6'), ACTIVE_BG_LIGHT),
    ('-', OPERATOR_BTN_BG, lambda: press('-'), ACTIVE_BG_DARK),
    ('1', NUMBER_BTN_BG, lambda: press('1'), ACTIVE_BG_LIGHT),
    ('2', NUMBER_BTN_BG, lambda: press('2'), ACTIVE_BG_LIGHT),
    ('3', NUMBER_BTN_BG, lambda: press('3'), ACTIVE_BG_LIGHT),
    ('+', OPERATOR_BTN_BG, lambda: press('+'), ACTIVE_BG_DARK),
    ('0', NUMBER_BTN_BG, lambda: press('0'), ACTIVE_BG_LIGHT),
    ('.', NUMBER_BTN_BG, lambda: press('.'), ACTIVE_BG_LIGHT),
    ('x^y', OPERATOR_BTN_BG, lambda: press('**'), ACTIVE_BG_DARK),
    ('=', EQUAL_BTN_BG, calculate, ACTIVE_BG_GREEN)
]

# Proper grid arrangement
row, col = 1, 0
for text, bg, cmd, active in buttons:
    colspan = 1
    if text == '0':
        colspan = 2
        width = 80*2 + 12
        place_button(text, bg, cmd, active, row, col, colspan, width)
        col += 2
    elif text == '=':
        colspan = 4 - col
        width = 80*colspan + 12*(colspan-1)
        place_button(text, bg, cmd, active, row, col, colspan, width)
        col += colspan
    else:
        place_button(text, bg, cmd, active, row, col)
        col += 1
    if col >= 4:
        col = 0
        row += 1

# ---------- Ask Campus AI row (optional, opt-in) ----------
ask_ai_button = RoundedButton(root, text="Ask Campus AI", width=80*4 + 12*3, height=44, radius=16,
                              bg=NUMBER_BTN_BG, fg=TEXT_COLOR, command=ask_campus_ai,
                              activebg=ACTIVE_BG_LIGHT, font=('Arial', 11, 'bold'))
ask_ai_button.grid(row=row, column=0, columnspan=4, padx=6, pady=(0, 10), sticky="nsew")
calculator_buttons.append(ask_ai_button)

for i in range(row+1):
    root.grid_rowconfigure(i, weight=1)
for i in range(4):
    root.grid_columnconfigure(i, weight=1)

def apply_calculator_theme(mode=None):
    palette = get_theme_palette(mode)
    root.configure(bg=palette["BG"])
    entry.configure(bg=palette["CARD"], readonlybackground=palette["CARD"],
                    fg=palette["TEXT"], insertbackground=palette["ACCENT"])
    for button in calculator_buttons:
        if button.theme_kind == "equals":
            bg, fg, hover = palette["GREEN"], "#263c31", "#c5e3ad"
        elif button.theme_kind == "operator":
            bg, fg, hover = "#ee9b62", "#263c31", "#dc8754"
        elif button.theme_kind == "clear":
            bg, fg, hover = ("#d87565" if mode != "dark" else "#df7566"), "#ffffff", "#c66053"
        else:
            bg, fg, hover = palette["CARD"], palette["TEXT"], palette["CARD_LIGHT"]
        button.set_theme_colors(bg, fg, hover, palette["BG"])

apply_calculator_theme(get_theme_mode())
watch_theme(root, lambda mode: (apply_tk_theme(root), apply_calculator_theme(mode)))
root.mainloop()
