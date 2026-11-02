import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import os
import threading
from campus_theme import ACCENT, ACCENT_HOVER, BG, CARD_LIGHT, TEXT, RoundedTkButton, apply_tk_theme, get_theme_mode, get_theme_palette, watch_theme
from PIL import Image
import numpy as np
import pytesseract

try:
    from deep_translator import GoogleTranslator
    translator_available = True
except ImportError:
    translator_available = False

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

LANGUAGE_OPTIONS = {
    "English": "en",
    "Hindi": "hi",
    "Arabic": "ar",
    "French": "fr",
    "Spanish": "es",
    "German": "de",
    "Urdu": "ur",
    "Japanese": "ja",
    "Chinese": "zh-CN"
}

BG_COLOR = BG
FG_COLOR = TEXT
ACCENT_COLOR = ACCENT
INPUT_BG = CARD_LIGHT
BUTTON_FG = TEXT

PRIMARY_BTN_BG = ACCENT
PRIMARY_BTN_ACTIVE_BG = ACCENT_HOVER


class OCRTranslatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("OCR with Translation")
        self.root.geometry("650x500")
        self.root.configure(bg=BG_COLOR)

        self.image_path = None
        self.extracted_text = ""
        self.action_buttons = []

        self.action_buttons.append(RoundedTkButton(root, "Select Image", self.select_image,
                                                   width=200, height=40, font=("Arial", 10, "bold")))
        self.action_buttons[-1].pack(pady=8)

        self.action_buttons.append(RoundedTkButton(root, "Read Image (OCR)", self.read_image,
                                                   width=200, height=40, font=("Arial", 10, "bold")))
        self.action_buttons[-1].pack(pady=8)

        tk.Label(root, text="Choose Translation Language:",
                 bg=BG_COLOR, fg=FG_COLOR, font=('Arial', 10)
                 ).pack()

        self.language_var = tk.StringVar(value="English")
        self.language_option_menu = tk.OptionMenu(root, self.language_var, *LANGUAGE_OPTIONS.keys())
        self.language_option_menu.config(
            bg=INPUT_BG, fg=FG_COLOR, font=('Arial', 10),
            relief=tk.FLAT, borderwidth=1, highlightthickness=1,
            highlightbackground=ACCENT_COLOR, highlightcolor=ACCENT_COLOR,
            activebackground=PRIMARY_BTN_ACTIVE_BG, activeforeground=BUTTON_FG,
            indicatoron=0
        )
        self.root.option_add('*TearOff', 'False')
        self.root.option_add('*Menu.background', INPUT_BG)
        self.root.option_add('*Menu.foreground', FG_COLOR)
        self.root.option_add('*Menu.activeBackground', PRIMARY_BTN_ACTIVE_BG)
        self.root.option_add('*Menu.activeForeground', BUTTON_FG)
        self.language_option_menu.pack(pady=5)

        self.action_buttons.append(RoundedTkButton(root, "Translate Text", self.translate_text,
                                                   width=200, height=40, font=("Arial", 10, "bold")))
        self.action_buttons[-1].pack(pady=8)

        self.action_buttons.append(RoundedTkButton(root, "Ask Campus AI", self.ask_campus_ai,
                                                   width=200, height=40, font=("Arial", 10, "bold")))
        self.ask_ai_button = self.action_buttons[-1]
        self.action_buttons[-1].pack(pady=8)

        self.output_text = scrolledtext.ScrolledText(root, wrap=tk.WORD, width=75, height=15,
                                                     bg=INPUT_BG, fg=FG_COLOR, font=('Arial', 10),
                                                     relief=tk.FLAT, borderwidth=1,
                                                     highlightthickness=1, highlightbackground=ACCENT_COLOR, highlightcolor=ACCENT_COLOR,
                                                     insertbackground=ACCENT_COLOR
                                                     )
        self.output_text.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)

        if not translator_available:
            messagebox.showwarning(
                "Missing Translator",
                "The 'deep_translator' module is not installed.\nInstall with:\n\npip install deep-translator"
            )

    def apply_theme(self, mode):
        global BG_COLOR, FG_COLOR, ACCENT_COLOR, INPUT_BG, BUTTON_FG
        palette = get_theme_palette(mode)
        BG_COLOR, FG_COLOR = palette["BG"], palette["TEXT"]
        ACCENT_COLOR, INPUT_BG, BUTTON_FG = palette["ACCENT"], palette["CARD_LIGHT"], palette["TEXT"]
        apply_tk_theme(self.root)
        for button in self.action_buttons:
            button.apply_theme(mode)
        self.root.configure(bg=BG_COLOR)
        self.language_option_menu.configure(bg=INPUT_BG, fg=FG_COLOR,
                                            highlightbackground=palette["BORDER"], highlightcolor=ACCENT_COLOR)
        self.root.option_add('*Menu.background', INPUT_BG)
        self.root.option_add('*Menu.foreground', FG_COLOR)
        self.root.option_add('*Menu.activeBackground', palette["ACCENT_HOVER"])
        self.root.option_add('*Menu.activeForeground', BUTTON_FG)
        self.output_text.configure(bg=INPUT_BG, fg=FG_COLOR, insertbackground=ACCENT_COLOR,
                                   highlightbackground=palette["BORDER"], highlightcolor=ACCENT_COLOR)

    def select_image(self):
        file_path = filedialog.askopenfilename(
            title="Choose an image",
            filetypes=[("Image Files", "*.png *.jpg *.jpeg *.bmp *.tiff")]
        )
        if file_path:
            self.image_path = file_path
            messagebox.showinfo("Image Selected", f"Selected:\n{file_path}")
            self.output_text.delete(1.0, tk.END)
            self.output_text.insert(tk.END, "Image selected. Press 'Read Image (OCR)' to extract text.")
            self.output_text.config(fg=FG_COLOR)

    def read_image(self):
        if not self.image_path:
            messagebox.showwarning("No Image", "Please select an image first.")
            return

        try:
            image = Image.open(self.image_path)
            np_image = np.array(image)

            self.extracted_text = pytesseract.image_to_string(np_image)

            self.output_text.delete(1.0, tk.END)
            if self.extracted_text.strip():
                self.output_text.insert(tk.END, "--- OCR Output ---\n\n")
                self.output_text.insert(tk.END, self.extracted_text.strip())
                self.output_text.config(fg=FG_COLOR)
            else:
                self.output_text.insert(tk.END, "[No text found in image]")
                self.output_text.config(fg='orange')

        except Exception as e:
            messagebox.showerror("OCR Error", f"Failed to process image:\n{e}\n\n"
                                             "Ensure Tesseract is installed and the path is correct.")
            self.output_text.delete(1.0, tk.END)
            self.output_text.insert(tk.END, f"OCR Error: {e}")
            self.output_text.config(fg='red')


    def ask_campus_ai(self):
        """Optional, opt-in AI help with the extracted or translated text on screen."""
        if not self.extracted_text.strip():
            messagebox.showwarning("No Text", "Please perform OCR before asking Campus AI.")
            return

        context = (f"Image: {os.path.basename(self.image_path) if self.image_path else 'unknown'}\n"
                   f"Chosen translation language: {self.language_var.get()}\n\n"
                   f"Text shown in the tool:\n{self.output_text.get(1.0, tk.END).strip()}")

        # campus_ai pulls in the shared key manager, which is slow to import, so load
        # it on a background thread and keep this window responsive meanwhile.
        self.ask_ai_button.set_text("Preparing AI…")

        def loader():
            try:
                from campus_ai import ask_about_result
                error = None
            except Exception as e:
                ask_about_result, error = None, str(e)

            def finish():
                if not self.root.winfo_exists():
                    return
                self.ask_ai_button.set_text("Ask Campus AI")
                if error:
                    messagebox.showerror("Campus AI unavailable", f"Could not load Campus Suite AI:\n{error}")
                    return
                ask_about_result(
                    self.root,
                    "OCR with Translation",
                    context,
                    privacy_note="Text read out of an image can contain personal data — names, ID numbers, "
                                 "addresses, marks or medical details. Read the output pane first and delete "
                                 "anything private before sending it.",
                )

            try:
                self.root.after(0, finish)
            except Exception:
                pass

        threading.Thread(target=loader, daemon=True).start()

    def translate_text(self):
        if not translator_available:
            messagebox.showerror("Translator Not Available", "The 'deep_translator' module is not installed.\n"
                                                             "Please install it using:\n\npip install deep-translator")
            return

        if not self.extracted_text.strip():
            messagebox.showwarning("No Text", "Please perform OCR before translating.")
            return

        target_lang_name = self.language_var.get()
        target_lang_code = LANGUAGE_OPTIONS.get(target_lang_name, "en")

        try:
            current_content = self.output_text.get(1.0, tk.END)
            if "\n\n--- Translated" in current_content:
                start_index = current_content.rfind("\n\n--- Translated")
                self.output_text.delete(f"{start_index + 1}c", tk.END)

            self.output_text.insert(tk.END, "\n\n--- Translating... Please wait ---\n")
            self.output_text.config(fg=ACCENT_COLOR)
            self.root.update_idletasks()

            translated = GoogleTranslator(source='auto', target=target_lang_code).translate(self.extracted_text.strip())

            self.output_text.delete("end-1l", tk.END)
            self.output_text.insert(tk.END, f"--- Translated to {target_lang_name} ---\n\n")
            self.output_text.insert(tk.END, translated.strip())
            self.output_text.config(fg=FG_COLOR)

        except Exception as e:
            messagebox.showerror("Translation Error", f"Translation failed:\n{e}\n\n"
                                                     "Check your internet connection or the selected language.")
            self.output_text.delete("end-1l", tk.END)
            self.output_text.insert(tk.END, f"\n\nTranslation Error: {e}")
            self.output_text.config(fg='red')


if __name__ == "__main__":
    root = tk.Tk()
    app = OCRTranslatorApp(root)
    app.apply_theme(get_theme_mode())
    watch_theme(root, app.apply_theme)
    root.mainloop()
